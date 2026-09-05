"""Every SQL statement in the project lives here.

This module is the *only* code allowed to speak SQLite. It is imported by the MCP
server and by the build script — never by ``app.agent``, which reaches the data
exclusively over the MCP protocol boundary (see ``tests/test_mcp_tools.py``, which
fails the build if that rule is broken).

Every function takes an explicit ``db_path`` rather than reading configuration.
The MCP server, the build script and the tests each point at a different database
file, and a module-level connection would make that impossible to express.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Final

SCHEMA_PATH: Final[Path] = Path(__file__).resolve().parent / "schema.sql"

# Numeric columns on `financials`. This doubles as the allowlist for
# `compare_companies(fields=...)`: those names are interpolated into the SELECT
# clause as identifiers, and SQL parameters cannot bind an identifier. Validating
# against this frozen set is what keeps that tool from being an injection hole.
FINANCIAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "market_cap",
        "revenue",
        "revenue_growth",
        "gross_margin",
        "operating_margin",
        "profit_margin",
        "pe_ratio",
        "ev_to_ebitda",
        "debt_to_equity",
        "free_cash_flow",
        "return_on_equity",
        "beta",
        "dividend_yield",
    }
)

IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {"ticker", "name", "sector", "industry", "country"}
)

COMPARABLE_FIELDS: Final[frozenset[str]] = FINANCIAL_FIELDS | IDENTITY_FIELDS

# Cost control: an LLM asking for "all of them" should not be able to drag an
# unbounded result set into the context window.
DEFAULT_LIMIT: Final[int] = 25
MAX_LIMIT: Final[int] = 50

_FINANCIAL_SELECT: Final[str] = ", ".join(f"f.{name}" for name in sorted(FINANCIAL_FIELDS))


class DatabaseError(RuntimeError):
    """Raised when the database file is missing or structurally unusable."""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a read-friendly connection with dict-like rows."""
    path = Path(db_path)
    if not path.exists():
        raise DatabaseError(
            f"Database not found at {path}. Run `python scripts/build_db.py` to create it."
        )
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert rows to plain dicts so callers never leak a sqlite3 type outward."""
    return [dict(row) for row in rows]


def init_db(db_path: str | Path) -> None:
    """Create the schema, creating the database file and its parent if needed."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    try:
        connection.executescript(schema)
        connection.commit()
    finally:
        connection.close()


def list_sectors(db_path: str | Path) -> list[str]:
    """Return the sectors that actually have companies in the database."""
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT DISTINCT sector FROM companies ORDER BY sector"
        ).fetchall()
    finally:
        connection.close()
    return [str(row["sector"]) for row in rows]


def sector_summary(db_path: str | Path) -> list[dict[str, Any]]:
    """Per-sector company counts and latest snapshot date.

    Backs the `GET /v1/sectors` registry endpoint and the UI's dataset footer, so
    both can be answered over MCP rather than by importing this module directly.
    """
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT c.sector                  AS sector,
                   COUNT(DISTINCT c.ticker)  AS company_count,
                   MAX(f.snapshot_date)      AS latest_snapshot
            FROM companies c
            LEFT JOIN financials f ON f.ticker = c.ticker
            GROUP BY c.sector
            ORDER BY c.sector
            """
        ).fetchall()
    finally:
        connection.close()
    return _rows_to_dicts(rows)


def query_companies(
    db_path: str | Path,
    sector: str,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Return every company in a sector with its most recent financial snapshot.

    The workhorse for sector-wide screening. Ordering puts the companies with data
    first: SQLite sorts NULL below everything, so a plain ``ORDER BY market_cap DESC``
    would surface the least-known companies at the top of every screen.
    """
    bounded_limit = max(1, min(int(limit), MAX_LIMIT))
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            f"""
            SELECT c.ticker, c.name, c.sector, c.industry, c.country,
                   {_FINANCIAL_SELECT},
                   f.snapshot_date, f.source
            FROM companies c
            JOIN financials f ON f.ticker = c.ticker
            -- Case- and whitespace-insensitive: SQLite compares TEXT with BINARY
            -- collation by default, so an LLM passing "Retail" or " retail" would get
            -- a silent empty result that is indistinguishable from "no such data".
            WHERE LOWER(TRIM(c.sector)) = LOWER(TRIM(?))
              AND f.snapshot_date = (
                    SELECT MAX(f2.snapshot_date)
                    FROM financials f2
                    WHERE f2.ticker = c.ticker
              )
            ORDER BY (f.market_cap IS NULL), f.market_cap DESC
            LIMIT ?
            """,
            (sector, bounded_limit),
        ).fetchall()
    finally:
        connection.close()
    return _rows_to_dicts(rows)


def get_company_detail(db_path: str | Path, ticker: str) -> dict[str, Any]:
    """Return profile + latest financials + all signals for one company.

    Absence is returned as a typed ``{"error": ...}`` result rather than raised or
    returned as ``{}``: the model needs an unambiguous, machine-readable signal that
    a company is missing so it can refuse honestly instead of improvising.
    """
    normalised = ticker.strip().upper()
    connection = _connect(db_path)
    try:
        row = connection.execute(
            f"""
            SELECT c.ticker, c.name, c.sector, c.industry, c.country,
                   c.source AS profile_source, c.last_updated,
                   {_FINANCIAL_SELECT},
                   f.snapshot_date, f.source AS financials_source
            FROM companies c
            LEFT JOIN financials f ON f.ticker = c.ticker
                 AND f.snapshot_date = (
                       SELECT MAX(f2.snapshot_date)
                       FROM financials f2
                       WHERE f2.ticker = c.ticker
                 )
            WHERE c.ticker = ?
            """,
            (normalised,),
        ).fetchone()
        if row is None:
            return {"error": f"No data for ticker '{normalised}'"}
        detail = dict(row)
        signal_rows = connection.execute(
            """
            SELECT signal_type, signal_value, numeric_value,
                   as_of_date, as_of_basis, retrieved_on, source
            FROM signals
            WHERE ticker = ?
            ORDER BY (as_of_date IS NULL), as_of_date DESC
            """,
            (normalised,),
        ).fetchall()
    finally:
        connection.close()
    detail["signals"] = _rows_to_dicts(signal_rows)
    return detail


def get_company_signals(
    db_path: str | Path,
    ticker: str,
    signal_type: str = "",
) -> list[dict[str, Any]]:
    """Return dated soft facts for a company, newest first.

    An empty list means the dataset holds no such signal — it is not an error, and
    the caller must report it as absence rather than inferring a value.
    """
    normalised = ticker.strip().upper()
    connection = _connect(db_path)
    try:
        if signal_type:
            rows = connection.execute(
                """
                SELECT ticker, signal_type, signal_value, numeric_value,
                       as_of_date, as_of_basis, retrieved_on, source
                FROM signals
                WHERE ticker = ? AND signal_type = ?
                ORDER BY (as_of_date IS NULL), as_of_date DESC
                """,
                (normalised, signal_type.strip().lower()),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT ticker, signal_type, signal_value, numeric_value,
                       as_of_date, as_of_basis, retrieved_on, source
                FROM signals
                WHERE ticker = ?
                ORDER BY (as_of_date IS NULL), as_of_date DESC
                """,
                (normalised,),
            ).fetchall()
    finally:
        connection.close()
    return _rows_to_dicts(rows)


def search_companies(db_path: str | Path, term: str) -> list[dict[str, Any]]:
    """Case-insensitive name/ticker lookup across every sector.

    Exists so that "is this company in the dataset?" is a *retrieved fact* rather
    than an inference. An empty list is authoritative absence, which lets an
    out-of-scope refusal be grounded in a real tool call.
    """
    needle = term.strip()
    if not needle:
        return []
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT ticker, name, sector, industry, country
            FROM companies
            WHERE name LIKE ? COLLATE NOCASE OR ticker LIKE ? COLLATE NOCASE
            ORDER BY name
            """,
            (f"%{needle}%", f"%{needle}%"),
        ).fetchall()
    finally:
        connection.close()
    return _rows_to_dicts(rows)


def compare_companies(
    db_path: str | Path,
    tickers: list[str],
    fields: list[str],
) -> list[dict[str, Any]]:
    """Pull a narrow set of fields for a named set of companies.

    Field projection is the main context-size lever in the system: a persona that
    weights three fields should not drag eighteen into the prompt.

    ``fields`` names SQL columns, and identifiers cannot be bound as parameters, so
    every requested name is checked against ``COMPARABLE_FIELDS`` and an unknown one
    is rejected outright rather than interpolated.
    """
    if not tickers:
        return []

    requested = [field.strip().lower() for field in fields if field.strip()]
    unknown = sorted(set(requested) - COMPARABLE_FIELDS)
    if unknown:
        raise ValueError(
            f"Unknown field(s): {', '.join(unknown)}. "
            f"Valid fields: {', '.join(sorted(COMPARABLE_FIELDS))}"
        )

    # ticker always comes back, otherwise the rows are unidentifiable.
    selected = ["ticker"] + [f for f in requested if f != "ticker"]
    projection = ", ".join(
        f"c.{name}" if name in IDENTITY_FIELDS else f"f.{name}" for name in selected
    )

    normalised = [t.strip().upper() for t in tickers if t.strip()]
    if not normalised:
        return []
    placeholders = ", ".join("?" for _ in normalised)

    connection = _connect(db_path)
    try:
        rows = connection.execute(
            f"""
            SELECT {projection}, f.snapshot_date, f.source
            FROM companies c
            LEFT JOIN financials f ON f.ticker = c.ticker
                 AND f.snapshot_date = (
                       SELECT MAX(f2.snapshot_date)
                       FROM financials f2
                       WHERE f2.ticker = c.ticker
                 )
            WHERE c.ticker IN ({placeholders})
            ORDER BY c.ticker
            """,
            normalised,
        ).fetchall()
    finally:
        connection.close()
    return _rows_to_dicts(rows)
