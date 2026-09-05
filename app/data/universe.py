"""The ticker universe the database is built from.

Separated from ``scripts/build_db.py`` so that the ingest script and the tests share
one definition. The tests use it to assert that no prompt hardcodes a company from
the dataset — a check that is only meaningful if it runs against the same list the
database is actually populated with.

Four sectors: the assessment's worked example and its API test both use Logistics,
while its other sample questions name Tech, Retail and Manufacturing.
"""

from __future__ import annotations

from typing import Final

SECTOR_TICKERS: Final[dict[str, tuple[str, ...]]] = {
    "tech": ("AAPL", "MSFT", "NVDA", "GOOGL", "META", "CRM", "ADBE", "ORCL", "AMD", "INTC"),
    "retail": ("WMT", "COST", "TGT", "HD", "LOW", "TJX", "DG", "KR", "BBY", "ROST"),
    "manufacturing": ("CAT", "GE", "HON", "DE", "MMM", "EMR", "ITW", "PH", "ETN", "ROK"),
    "logistics": ("UPS", "FDX", "ODFL", "JBHT", "EXPD", "CHRW", "XPO", "UNP", "CSX", "NSC"),
}

ALL_TICKERS: Final[frozenset[str]] = frozenset(
    ticker for tickers in SECTOR_TICKERS.values() for ticker in tickers
)
