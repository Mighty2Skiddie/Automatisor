"""Tests for the data layer.

These run against a temporary database built from fixtures, never against the
committed ``financials.db`` and never over the network — the suite must stay green
on a machine with no internet and no API keys.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.data.db import (
    COMPARABLE_FIELDS,
    MAX_LIMIT,
    DatabaseError,
    compare_companies,
    get_company_detail,
    get_company_signals,
    init_db,
    list_sectors,
    query_companies,
    search_companies,
    sector_summary,
)

# Two sectors, one company with a deliberate NULL, and two snapshot dates for the
# same ticker so "latest snapshot" behaviour is actually exercised.
COMPANIES = [
    ("AAA", "Alpha Industries", "tech", "Software", "United States"),
    ("BBB", "Beta Corporation", "tech", "Semiconductors", "United States"),
    ("CCC", "Gamma Logistics", "logistics", "Freight", "United States"),
]

FINANCIALS = [
    # ticker, market_cap, operating_margin, ev_to_ebitda, debt_to_equity, snapshot
    ("AAA", 3_000.0, 0.31, 22.5, 0.42, "2026-08-01"),
    ("AAA", 3_500.0, 0.34, 24.0, 0.40, "2026-09-01"),
    ("BBB", 1_200.0, None, 15.0, 1.10, "2026-09-01"),
    ("CCC", 900.0, 0.12, 9.5, 2.05, "2026-09-01"),
]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A populated temporary database."""
    path = tmp_path / "test.db"
    init_db(path)
    connection = sqlite3.connect(path)
    try:
        connection.executemany(
            """
            INSERT INTO companies (ticker, name, sector, industry, country, source, last_updated)
            VALUES (?, ?, ?, ?, ?, 'test-fixture', '2026-09-01')
            """,
            COMPANIES,
        )
        connection.executemany(
            """
            INSERT INTO financials
                (ticker, market_cap, operating_margin, ev_to_ebitda,
                 debt_to_equity, snapshot_date, source)
            VALUES (?, ?, ?, ?, ?, ?, 'test-fixture')
            """,
            FINANCIALS,
        )
        connection.execute(
            """
            INSERT INTO signals
                (ticker, signal_type, signal_value, numeric_value,
                 as_of_date, as_of_basis, retrieved_on, source)
            VALUES ('AAA', 'headcount', '12,000 full-time employees', 12000,
                    '2025-12-31', 'Period end of most recent Form 10-K',
                    '2026-09-01', 'https://sec.gov/example')
            """
        )
        connection.execute(
            """
            INSERT INTO signals
                (ticker, signal_type, signal_value, numeric_value,
                 as_of_date, as_of_basis, retrieved_on, source)
            VALUES ('BBB', 'headcount', '4,000 full-time employees', 4000,
                    NULL, 'Undated Yahoo profile snapshot', '2026-09-01', 'yfinance/yahoo')
            """
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "twice.db"
    init_db(path)
    init_db(path)
    assert path.exists()


def test_missing_database_raises_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match=r"build_db\.py"):
        list_sectors(tmp_path / "absent.db")


def test_list_sectors(db: Path) -> None:
    assert list_sectors(db) == ["logistics", "tech"]


def test_sector_summary_counts_and_dates(db: Path) -> None:
    summary = {row["sector"]: row for row in sector_summary(db)}
    assert summary["tech"]["company_count"] == 2
    assert summary["tech"]["latest_snapshot"] == "2026-09-01"


def test_query_companies_returns_only_latest_snapshot(db: Path) -> None:
    rows = query_companies(db, "tech")
    assert len(rows) == 2
    alpha = next(row for row in rows if row["ticker"] == "AAA")
    # The 2026-08-01 snapshot must not win over the 2026-09-01 one.
    assert alpha["snapshot_date"] == "2026-09-01"
    assert alpha["market_cap"] == 3_500.0


def test_query_companies_orders_nulls_last(db: Path) -> None:
    """A company with no market cap must not head the screen."""
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            """
            INSERT INTO companies (ticker, name, sector, industry, country, source, last_updated)
            VALUES ('DDD', 'Delta Unknown', 'tech', NULL, NULL, 'test-fixture', '2026-09-01')
            """
        )
        connection.execute(
            """
            INSERT INTO financials (ticker, market_cap, snapshot_date, source)
            VALUES ('DDD', NULL, '2026-09-01', 'test-fixture')
            """
        )
        connection.commit()
    finally:
        connection.close()

    rows = query_companies(db, "tech")
    assert rows[0]["ticker"] == "AAA"
    assert rows[-1]["ticker"] == "DDD"


def test_query_companies_limit_is_capped(db: Path) -> None:
    assert len(query_companies(db, "tech", limit=1)) == 1
    # A caller asking for more than the cap gets the cap, not an error.
    assert len(query_companies(db, "tech", limit=10_000)) <= MAX_LIMIT


def test_query_companies_unknown_sector_is_empty_not_error(db: Path) -> None:
    assert query_companies(db, "energy") == []


def test_null_is_preserved_not_zeroed(db: Path) -> None:
    rows = query_companies(db, "tech")
    beta = next(row for row in rows if row["ticker"] == "BBB")
    assert beta["operating_margin"] is None


def test_get_company_detail_includes_signals(db: Path) -> None:
    detail = get_company_detail(db, "aaa")
    assert detail["name"] == "Alpha Industries"
    assert detail["snapshot_date"] == "2026-09-01"
    assert len(detail["signals"]) == 1
    assert detail["signals"][0]["as_of_date"] == "2025-12-31"


def test_get_company_detail_absence_is_typed(db: Path) -> None:
    result = get_company_detail(db, "ZZZ")
    assert result == {"error": "No data for ticker 'ZZZ'"}


def test_get_company_signals_filters_by_type(db: Path) -> None:
    assert len(get_company_signals(db, "AAA", "headcount")) == 1
    assert get_company_signals(db, "AAA", "hiring") == []


def test_get_company_signals_reports_undated_honestly(db: Path) -> None:
    signal = get_company_signals(db, "BBB", "headcount")[0]
    assert signal["as_of_date"] is None
    assert "Undated" in signal["as_of_basis"]


def test_search_companies_matches_name_and_ticker(db: Path) -> None:
    assert [row["ticker"] for row in search_companies(db, "gamma")] == ["CCC"]
    assert [row["ticker"] for row in search_companies(db, "bb")] == ["BBB"]


def test_search_companies_absence_is_empty(db: Path) -> None:
    """Absence must be retrievable, so an out-of-scope refusal can be grounded."""
    assert search_companies(db, "SpaceX") == []


def test_compare_companies_projects_requested_fields(db: Path) -> None:
    rows = compare_companies(db, ["AAA", "CCC"], ["ev_to_ebitda", "debt_to_equity"])
    assert len(rows) == 2
    assert set(rows[0]) == {"ticker", "ev_to_ebitda", "debt_to_equity", "snapshot_date", "source"}


def test_compare_companies_rejects_unknown_field(db: Path) -> None:
    with pytest.raises(ValueError, match="Unknown field"):
        compare_companies(db, ["AAA"], ["market_cap", "not_a_column"])


@pytest.mark.parametrize(
    "payload",
    [
        "market_cap; DROP TABLE companies",
        "market_cap FROM companies WHERE 1=1 --",
        "(SELECT secret FROM companies)",
        "*",
    ],
)
def test_compare_companies_rejects_sql_injection_in_fields(db: Path, payload: str) -> None:
    """`fields` becomes a SQL identifier, which no parameter can bind — so it is allowlisted."""
    with pytest.raises(ValueError, match="Unknown field"):
        compare_companies(db, ["AAA"], [payload])
    # The table is still there.
    assert list_sectors(db) == ["logistics", "tech"]


def test_compare_companies_empty_inputs(db: Path) -> None:
    assert compare_companies(db, [], ["market_cap"]) == []


def test_comparable_fields_match_schema(db: Path) -> None:
    """The allowlist must not drift from the actual table definitions."""
    connection = sqlite3.connect(db)
    try:
        financial_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(financials)")
        }
        company_columns = {row[1] for row in connection.execute("PRAGMA table_info(companies)")}
    finally:
        connection.close()
    assert (financial_columns | company_columns) >= COMPARABLE_FIELDS


def test_rerunning_the_same_snapshot_does_not_duplicate(db: Path) -> None:
    """Re-ingesting the same day must replace, not append."""
    connection = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO financials (ticker, market_cap, snapshot_date, source)
                VALUES ('AAA', 9999.0, '2026-09-01', 'test-fixture')
                """
            )
    finally:
        connection.close()


def test_undated_signal_cannot_duplicate_on_rerun(db: Path) -> None:
    """NULL as_of_date must still collide, or every re-run adds another undated row."""
    connection = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO signals
                    (ticker, signal_type, signal_value, numeric_value,
                     as_of_date, as_of_basis, retrieved_on, source)
                VALUES ('BBB', 'headcount', '4,100 full-time employees', 4100,
                        NULL, 'Undated', '2026-09-02', 'yfinance/yahoo')
                """
            )
    finally:
        connection.close()


@pytest.mark.parametrize("variant", ["tech", "Tech", "TECH", "  tech  "])
def test_sector_lookup_tolerates_case_and_whitespace(db: Path, variant: str) -> None:
    """An LLM passing "Tech" must not get a silent empty result.

    SQLite compares TEXT with BINARY collation, so a capitalised sector used to
    return [] — indistinguishable from "this sector holds nothing" — and the agent
    would honestly but wrongly report that it had no data.
    """
    assert len(query_companies(db, variant)) == 2
