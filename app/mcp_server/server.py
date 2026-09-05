"""FastMCP server exposing the financial database as typed tools.

This process is the *only* thing that touches SQLite. The agent is an MCP client
and reaches every fact through this surface, which is what makes the protocol
boundary real rather than decorative.

Two design rules govern what is here:

**Capability-shaped, not table-shaped.** There is deliberately no ``run_sql`` tool.
Handing an LLM raw SQL is an injection and correctness hazard; each tool below is a
typed, parameterised capability with a fixed shape.

**The docstrings are the contract.** An MCP client shows these to the model, so each
one states what an empty or error result means and when to prefer that tool over its
neighbours. Prompt engineering for tool selection lives here, not in the system
prompt.

  ⚠ Docstring ordering is load-bearing. FastMCP builds a tool's description from the
  docstring *up to the ``Args:`` block* and drops everything after it — per-argument
  text is preserved separately, in the input schema. So every instruction about what
  the result means must appear BEFORE ``Args:``, or the model never sees it. This is
  silent when you get it wrong, so ``tests/test_mcp_tools.py`` asserts each tool's
  registered description still carries its absence semantics.

Run it:

    python -m app.mcp_server.server
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app.config import settings
from app.data import db

mcp: FastMCP = FastMCP(
    name="financial-data",
    instructions=(
        "Read-only access to a database of public-company financials across four "
        "sectors. Every figure you state about a company must come from a call to one "
        "of these tools in this same request. If a company is not returned by these "
        "tools, the dataset has no data on it and you must say so plainly."
    ),
)


@mcp.tool
def list_sectors() -> list[str]:
    """List the sectors held in the database.

    Returns the exact sector identifiers accepted by `query_companies`. Call this
    first if you are unsure whether a sector exists, rather than guessing a name.

    An empty list means the database has not been built yet.
    """
    return db.list_sectors(settings.db_file)


@mcp.tool
def dataset_overview() -> dict[str, Any]:
    """Summarise dataset coverage: sectors, company counts and snapshot dates.

    Use this to answer "what data do you have?" and to state how current the data is
    before making claims about it. Prefer this over `query_companies` when the
    question is about the dataset itself rather than about companies.

    Returns `total_companies`, `sectors` (a list of `{sector, company_count,
    latest_snapshot}`) and `latest_snapshot` across the whole dataset. A
    `total_companies` of 0 means the database has not been built yet.
    """
    summary = db.sector_summary(settings.db_file)
    snapshots = [row["latest_snapshot"] for row in summary if row["latest_snapshot"]]
    return {
        "total_companies": sum(int(row["company_count"]) for row in summary),
        "sectors": summary,
        "latest_snapshot": max(snapshots) if snapshots else None,
    }


@mcp.tool
def query_companies(sector: str, limit: int = 25) -> list[dict[str, Any]]:
    """Return every company in one sector with its most recent financial snapshot.

    The workhorse for sector-wide screening, ranking and comparison: use it whenever
    the question is about "the companies in this sector" rather than about one named
    company. Companies are ordered by market cap, largest first, with companies
    lacking a market cap last.

    An empty list means that sector holds no companies. Report that plainly instead
    of answering from general knowledge.

    Units: market_cap, revenue and free_cash_flow are absolute USD. revenue_growth,
    gross_margin, operating_margin, profit_margin, return_on_equity and
    dividend_yield are decimal fractions, so 0.462 means 46.2%. debt_to_equity is a
    ratio, so 1.54 means 1.54x. pe_ratio, ev_to_ebitda and beta are dimensionless.
    A null means the value is not in the dataset — report it as unavailable, never
    as zero.

    Args:
        sector: One of the identifiers from `list_sectors` — currently "tech",
            "retail", "manufacturing" or "logistics". Case-sensitive.
        limit: Maximum companies to return. Defaults to 25 and is capped at 50.
    """
    return db.query_companies(settings.db_file, sector, limit)


@mcp.tool
def search_companies(query: str) -> list[dict[str, Any]]:
    """Find companies by name or ticker across every sector.

    Call this before making, or declining to make, any claim about a company the user
    named that you have not already retrieved. It is the authoritative membership
    check for the dataset.

    **An empty list is authoritative: the company is not in the dataset.** Say you
    have no data on it. Do not fall back to general knowledge, do not estimate, and
    do not substitute a similarly named company that did match.

    Args:
        query: A company name or ticker, or any fragment of one. Case-insensitive
            substring match, so "emerson", "EMR" and "emer" all find Emerson Electric.
    """
    return db.search_companies(settings.db_file, query)


@mcp.tool
def get_company_detail(ticker: str) -> dict[str, Any]:
    """Return the full profile, latest financials and all signals for one company.

    Prefer this over `query_companies` when the question is about a single named
    company, and prefer `compare_companies` when you need a few fields across several
    companies rather than everything about one.

    On success the result includes identity fields, every financial column, a
    `snapshot_date`, and a `signals` list. If the ticker is not in the dataset the
    result is `{"error": "No data for ticker 'X'"}` — that is a definitive answer,
    not a transient failure, so report the absence rather than retrying or
    improvising.

    Units: market_cap, revenue and free_cash_flow are absolute USD. revenue_growth,
    gross_margin, operating_margin, profit_margin, return_on_equity and
    dividend_yield are decimal fractions, so 0.462 means 46.2%. debt_to_equity is a
    ratio, so 1.54 means 1.54x. pe_ratio, ev_to_ebitda and beta are dimensionless.
    A null means the value is not in the dataset — report it as unavailable, never
    as zero.

    Args:
        ticker: An exchange ticker such as "NVDA" or "EMR". Case-insensitive. If you
            only know the company's name, resolve it with `search_companies` first
            rather than guessing the symbol.
    """
    return db.get_company_detail(settings.db_file, ticker)


@mcp.tool
def get_company_signals(ticker: str, signal_type: str = "") -> list[dict[str, Any]]:
    """Return this company's dated headcount signals, newest first.

    **Headcount is the only kind of signal in this dataset.** It holds no hiring
    signals and no news signals. If you are asked for a hiring signal, a news signal,
    or any other kind, say plainly that the dataset carries headcount only — do not
    hand back a headcount figure as though it answered the question, and do not read
    a hiring trend out of a single headcount number.

    Each row carries `signal_value` (human-readable), `numeric_value`, `as_of_date`,
    `as_of_basis` and `source`. Each headcount is dated to the period end of that
    company's most recent annual report (Form 10-K), so it is an annual figure that is
    usually months old, not a live one.

    **Report `as_of_date` and `source` whenever you quote one of these figures.**
    `as_of_date` may be null, which means the source does not date the figure —
    `as_of_basis` then explains why. When it is null, say the dataset does not date
    the figure. Never substitute the retrieval date for the as-of date.

    An empty list means the dataset holds no signal of that kind for that company.

    Args:
        ticker: Exchange ticker, case-insensitive.
        signal_type: Narrow to one kind. "headcount" is the only value present, so
            any other value returns an empty list. Leave empty for all.
    """
    return db.get_company_signals(settings.db_file, ticker, signal_type)


@mcp.tool
def compare_companies(tickers: list[str], fields: list[str]) -> list[dict[str, Any]]:
    """Pull a narrow set of fields across several named companies.

    Prefer this over repeated `get_company_detail` calls when comparing a handful of
    companies on a handful of metrics: it keeps the comparison focused on the fields
    your analysis actually weights.

    A ticker that is not in the dataset is simply absent from the result; check the
    returned tickers against the ones you asked for before describing a company. An
    unknown field name is rejected with an error listing the valid ones — correct the
    call rather than retrying it.

    Units: market_cap, revenue and free_cash_flow are absolute USD. revenue_growth,
    gross_margin, operating_margin, profit_margin, return_on_equity and
    dividend_yield are decimal fractions, so 0.462 means 46.2%. debt_to_equity is a
    ratio, so 1.54 means 1.54x. pe_ratio, ev_to_ebitda and beta are dimensionless.
    A null means the value is not in the dataset — report it as unavailable, never
    as zero.

    Args:
        tickers: Exchange tickers, case-insensitive.
        fields: Column names to return. Valid values are exactly: market_cap,
            revenue, revenue_growth, gross_margin, operating_margin, profit_margin,
            pe_ratio, ev_to_ebitda, debt_to_equity, free_cash_flow, return_on_equity,
            beta, dividend_yield, and the identity fields name, sector, industry,
            country. `ticker` is always returned.
    """
    try:
        return db.compare_companies(settings.db_file, tickers, fields)
    except ValueError as exc:
        # Surface the allowlist rejection to the model as a correctable tool error
        # rather than a stack trace it cannot act on.
        raise ToolError(str(exc)) from exc


def main() -> None:
    """Run the server over streamable HTTP.

    Host and port come from settings so the container in docker compose can bind
    0.0.0.0 while local development stays on loopback — a hardcoded 127.0.0.1 would
    make the server unreachable from the api container.
    """
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path="/mcp",
    )


if __name__ == "__main__":
    main()
