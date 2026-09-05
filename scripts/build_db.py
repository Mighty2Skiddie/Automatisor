"""Rebuild `financials.db` from public sources.

Two sources, deliberately:

* **Yahoo Finance via yfinance** — the numeric snapshot. Trailing-twelve-month
  fundamentals for each ticker.
* **SEC EDGAR submissions API** — the *date* on the headcount signal. Yahoo's
  ``fullTimeEmployees`` is a bare integer with no as-of date attached, so dating it
  with the scrape date would invent provenance in the exact field the assessment
  uses to catch invention. Instead the figure is dated to the period end of the
  company's most recent Form 10-K and cited to that filing. When EDGAR cannot be
  reached the date is stored as NULL with a basis line saying so — an undated fact
  is reported as undated, never as fresh.

Safe to re-run: companies upsert, and financials are unique per
``(ticker, snapshot_date)`` so a second run on the same day replaces rather than
duplicates, while a run on a later day appends history.

    python scripts/build_db.py
    python scripts/build_db.py --skip-sec      # numeric snapshot only, undated signals
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance as yf
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.data.db import init_db
from app.data.universe import SECTOR_TICKERS

# The universe lives in app/data/universe.py so the ingest and the tests share one
# definition — the "no hardcoded companies in prompts" test is only meaningful if it
# checks against the list the database is actually built from.
SECTORS: Final[dict[str, tuple[str, ...]]] = SECTOR_TICKERS

YFINANCE_SOURCE: Final[str] = "yfinance/yahoo"
SEC_TICKER_MAP_URL: Final[str] = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL: Final[str] = "https://data.sec.gov/submissions/CIK{cik}.json"

# EDGAR requires a declared identity and asks for <10 requests/second.
SEC_USER_AGENT: Final[str] = "sector-analyst-agent/1.0 (take-home assessment; contact via repo)"
SEC_DELAY_SECONDS: Final[float] = 0.15
YFINANCE_DELAY_SECONDS: Final[float] = 0.4


class SourceUnavailable(RuntimeError):
    """A public data source could not be reached or returned nothing usable."""


def _num(value: Any) -> float | None:
    """Coerce a source value to a float, mapping every flavour of missing to None.

    Missing must never become 0.0 — an unknown margin and a zero margin are
    different facts, and conflating them is a direct hallucination path. A genuine
    zero from the source is preserved.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            value = float(stripped)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _ratio_from_percent(value: Any) -> float | None:
    """Yahoo reports debtToEquity as a percentage: 154.0 means 1.54x."""
    number = _num(value)
    return None if number is None else number / 100.0


def _dividend_yield_fraction(info: dict[str, Any]) -> float | None:
    """Return dividend yield as a decimal fraction, from whichever field Yahoo filled.

    The two fields carry *different units*, verified against yfinance 1.7.0:

    * ``dividendYield`` is a **percentage** — UNP returns ``1.96`` for 1.96%, AAPL
      returns ``0.33`` for 0.33%.
    * ``trailingAnnualDividendYield`` is already a **fraction** — UNP returns
      ``0.0191``.

    Do not be tempted to disambiguate by magnitude. A "divide by 100 only if the
    value exceeds 1" rule looks safe and is wrong for every company yielding under
    one percent, which silently stores AAPL at 33%.

    ``dividendYield`` is missing for several large payers (KR, WMT, CAT all return
    None while paying a dividend), so the trailing field is the fallback rather than
    recording a NULL we can avoid.
    """
    percent = _num(info.get("dividendYield"))
    if percent is not None:
        return percent / 100.0
    return _num(info.get("trailingAnnualDividendYield"))


@retry(
    retry=retry_if_exception_type((urllib.error.URLError, TimeoutError, SourceUnavailable)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _http_json(url: str) -> Any:
    """Fetch and decode JSON, retrying transient network failures."""
    request = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_sec_cik_map() -> dict[str, str]:
    """Map ticker -> zero-padded CIK using EDGAR's published directory."""
    payload = _http_json(SEC_TICKER_MAP_URL)
    if not isinstance(payload, dict):
        raise SourceUnavailable("Unexpected shape from EDGAR company_tickers.json")
    mapping: dict[str, str] = {}
    for entry in payload.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            mapping[ticker] = str(cik).zfill(10)
    return mapping


def fetch_latest_10k(cik: str) -> dict[str, str] | None:
    """Return period end, filing date and URL of the most recent Form 10-K."""
    payload = _http_json(SEC_SUBMISSIONS_URL.format(cik=cik))
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    report_dates = recent.get("reportDate", [])
    filing_dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    for index, form in enumerate(forms):
        if form != "10-K":
            continue
        accession = accessions[index] if index < len(accessions) else ""
        report_date = report_dates[index] if index < len(report_dates) else ""
        filing_date = filing_dates[index] if index < len(filing_dates) else ""
        if not report_date:
            continue
        stripped = accession.replace("-", "")
        return {
            "report_date": report_date,
            "filing_date": filing_date,
            "accession": accession,
            "url": (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{stripped}/{accession}-index.htm"
            ),
        }
    return None


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_yfinance_info(ticker: str) -> dict[str, Any]:
    """Pull one ticker's profile and fundamentals, retrying Yahoo's frequent 429s."""
    info = yf.Ticker(ticker).info
    if not isinstance(info, dict) or not info:
        raise SourceUnavailable(f"Empty info payload for {ticker}")
    return info


def upsert_company(
    connection: sqlite3.Connection, ticker: str, sector: str, info: dict[str, Any], today: str
) -> None:
    """Write company identity. `sector` is our label, not Yahoo's classification."""
    connection.execute(
        """
        INSERT OR REPLACE INTO companies
            (ticker, name, sector, industry, country, source, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            info.get("longName") or info.get("shortName") or ticker,
            sector,
            info.get("industry"),
            info.get("country"),
            YFINANCE_SOURCE,
            today,
        ),
    )


def upsert_financials(
    connection: sqlite3.Connection, ticker: str, info: dict[str, Any], today: str
) -> None:
    """Write one dated numeric snapshot, replacing any snapshot from the same day."""
    connection.execute(
        """
        INSERT INTO financials (
            ticker, market_cap, revenue, revenue_growth, gross_margin,
            operating_margin, profit_margin, pe_ratio, ev_to_ebitda,
            debt_to_equity, free_cash_flow, return_on_equity, beta,
            dividend_yield, snapshot_date, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (ticker, snapshot_date) DO UPDATE SET
            market_cap       = excluded.market_cap,
            revenue          = excluded.revenue,
            revenue_growth   = excluded.revenue_growth,
            gross_margin     = excluded.gross_margin,
            operating_margin = excluded.operating_margin,
            profit_margin    = excluded.profit_margin,
            pe_ratio         = excluded.pe_ratio,
            ev_to_ebitda     = excluded.ev_to_ebitda,
            debt_to_equity   = excluded.debt_to_equity,
            free_cash_flow   = excluded.free_cash_flow,
            return_on_equity = excluded.return_on_equity,
            beta             = excluded.beta,
            dividend_yield   = excluded.dividend_yield,
            source           = excluded.source
        """,
        (
            ticker,
            _num(info.get("marketCap")),
            _num(info.get("totalRevenue")),
            _num(info.get("revenueGrowth")),
            _num(info.get("grossMargins")),
            _num(info.get("operatingMargins")),
            _num(info.get("profitMargins")),
            _num(info.get("trailingPE")),
            _num(info.get("enterpriseToEbitda")),
            _ratio_from_percent(info.get("debtToEquity")),
            _num(info.get("freeCashflow")),
            _num(info.get("returnOnEquity")),
            _num(info.get("beta")),
            _dividend_yield_fraction(info),
            today,
            YFINANCE_SOURCE,
        ),
    )


def upsert_headcount_signal(
    connection: sqlite3.Connection,
    ticker: str,
    info: dict[str, Any],
    filing: dict[str, str] | None,
    today: str,
) -> bool:
    """Write the headcount signal, dated to a real filing where one was found."""
    headcount = _num(info.get("fullTimeEmployees"))
    if headcount is None:
        return False

    if filing is not None:
        as_of_date: str | None = filing["report_date"]
        as_of_basis = (
            f"Period end of most recent Form 10-K (accession {filing['accession']}, "
            f"filed {filing['filing_date']}). Headcount figure itself is from the "
            f"Yahoo Finance profile."
        )
        source = filing["url"]
    else:
        as_of_date = None
        as_of_basis = (
            "Yahoo Finance profile snapshot. The source does not date this figure and "
            "no matching Form 10-K was found, so it is recorded as undated."
        )
        source = YFINANCE_SOURCE

    connection.execute(
        """
        INSERT INTO signals
            (ticker, signal_type, signal_value, numeric_value,
             as_of_date, as_of_basis, retrieved_on, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (ticker, signal_type, COALESCE(as_of_date, '')) DO UPDATE SET
            signal_value  = excluded.signal_value,
            numeric_value = excluded.numeric_value,
            as_of_basis   = excluded.as_of_basis,
            retrieved_on  = excluded.retrieved_on,
            source        = excluded.source
        """,
        (
            ticker,
            "headcount",
            f"{int(headcount):,} full-time employees",
            headcount,
            as_of_date,
            as_of_basis,
            today,
            source,
        ),
    )
    return True


def build(db_path: Path, skip_sec: bool) -> int:
    """Run the full ingest. Returns a process exit code."""
    today = datetime.now(UTC).date().isoformat()
    init_db(db_path)

    cik_map: dict[str, str] = {}
    if skip_sec:
        print("SEC lookup disabled (--skip-sec): headcount signals will be undated.")
    else:
        try:
            cik_map = fetch_sec_cik_map()
            print(f"EDGAR ticker map loaded ({len(cik_map):,} tickers).")
        except (urllib.error.URLError, TimeoutError, SourceUnavailable, ValueError) as exc:
            print(f"EDGAR ticker map unavailable ({exc}); headcount signals will be undated.")

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    inserted = 0
    skipped: list[str] = []

    try:
        for sector, tickers in SECTORS.items():
            print(f"\n{sector}")
            for ticker in tickers:
                try:
                    info = fetch_yfinance_info(ticker)
                except Exception as exc:  # noqa: BLE001 - yfinance raises bare Exception
                    print(f"  skip {ticker:6} yfinance unavailable: {exc}")
                    skipped.append(ticker)
                    continue

                filing: dict[str, str] | None = None
                cik = cik_map.get(ticker)
                if cik:
                    try:
                        filing = fetch_latest_10k(cik)
                        time.sleep(SEC_DELAY_SECONDS)
                    except (urllib.error.URLError, TimeoutError, SourceUnavailable, ValueError):
                        filing = None

                upsert_company(connection, ticker, sector, info, today)
                upsert_financials(connection, ticker, info, today)
                has_headcount = upsert_headcount_signal(connection, ticker, info, filing, today)
                connection.commit()
                inserted += 1

                dated = filing["report_date"] if filing else "undated"
                headcount_note = f"headcount {dated}" if has_headcount else "no headcount"
                print(f"  ok   {ticker:6} {info.get('longName', ticker)[:38]:38} {headcount_note}")
                time.sleep(YFINANCE_DELAY_SECONDS)
    finally:
        connection.commit()
        connection.close()

    print(f"\n{inserted} companies written to {db_path}")
    if skipped:
        print(f"{len(skipped)} skipped: {', '.join(skipped)}")
    return 0 if inserted else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "app" / "data" / "financials.db",
        help="Destination SQLite file.",
    )
    parser.add_argument(
        "--skip-sec",
        action="store_true",
        help="Skip EDGAR; headcount signals are stored explicitly undated.",
    )
    args = parser.parse_args()
    return build(args.db_path, args.skip_sec)


if __name__ == "__main__":
    raise SystemExit(main())
