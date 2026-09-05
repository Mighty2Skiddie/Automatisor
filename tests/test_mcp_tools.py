"""Tests for the MCP tool surface and the protocol boundary it exists to enforce.

Two jobs:

1. Prove the agent package cannot reach the database except over MCP.
2. Prove each tool's *registered description* still carries the semantics the model
   depends on. FastMCP truncates a docstring at its ``Args:`` block, so guidance
   written after that block is silently dropped — a failure mode with no error
   message, which is exactly why it is asserted here.
"""

from __future__ import annotations

import ast
import asyncio
import re
import sqlite3
from pathlib import Path

import pytest

from app.mcp_server import server as mcp_server

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_PACKAGE = PROJECT_ROOT / "app" / "agent"

# The boundary is not an agent-only rule. If the API imported the data layer to
# answer /healthz or /v1/sectors, the protocol boundary this project is judged on
# would be bypassed in the one place a reviewer is most likely to look.
BOUNDED_PACKAGES = (
    PROJECT_ROOT / "app" / "agent",
    PROJECT_ROOT / "app" / "api",
    PROJECT_ROOT / "app" / "ui_streamlit",
)

EXPECTED_TOOLS = {
    "list_sectors",
    "dataset_overview",
    "query_companies",
    "search_companies",
    "get_company_detail",
    "get_company_signals",
    "compare_companies",
}

# The five the architecture doc specifies; the other two are additions, and this set
# is asserted separately so a rename cannot quietly drop one of the originals.
REQUIRED_BY_SPEC = {
    "list_sectors",
    "query_companies",
    "get_company_detail",
    "get_company_signals",
    "compare_companies",
}


def _registered_tools() -> dict[str, object]:
    async def collect() -> dict[str, object]:
        return {name: await mcp_server.mcp.get_tool(name) for name in EXPECTED_TOOLS}

    return asyncio.run(collect())


def _description(tool_name: str) -> str:
    """The description string the model is actually shown for one tool."""
    return getattr(_registered_tools()[tool_name], "description", "") or ""


# --------------------------------------------------------------------------
# The protocol boundary
# --------------------------------------------------------------------------


def _imported_modules(source: str) -> set[str]:
    """Every module name reachable from an import statement, via AST not substrings.

    A substring scan for "from app.data" misses ``import app.data.db as d``, relative
    imports, and ``importlib``, while false-positiving on the string appearing in a
    docstring. Parsing is barely more code and actually holds.
    """
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, e.g. `from ..data import db`
                modules.add(("." * node.level) + (node.module or ""))
            elif node.module:
                modules.add(node.module)
    return modules


FORBIDDEN_PREFIXES = ("app.data", "sqlite3", "sqlalchemy", "pandas.io.sql")


@pytest.mark.parametrize("package", BOUNDED_PACKAGES, ids=lambda p: p.name)
def test_client_packages_never_import_the_data_layer(package: Path) -> None:
    """The MCP protocol boundary must not be bypassed.

    Every package on the client side of the boundary is scanned, not just the agent.
    The API answers /healthz and /v1/sectors from real data, so it is the most likely
    place for someone to reach for a direct query — and the first file a reviewer
    opens.
    """
    if not package.exists():
        pytest.skip(f"{package.name} does not exist yet")

    offenders: list[str] = []
    for path in package.rglob("*.py"):
        for module in _imported_modules(path.read_text(encoding="utf-8")):
            if module.startswith(FORBIDDEN_PREFIXES) or module.endswith(".data.db"):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} imports {module}")

    assert not offenders, "MCP boundary bypassed:\n  " + "\n  ".join(offenders)


def test_the_boundary_check_can_actually_fail() -> None:
    """Guard the guard.

    A boundary test that cannot fail is worse than none, because the docstring in
    app/api/main.py cites it as proof. This asserts the detector fires on the exact
    imports it exists to catch.
    """
    bypasses = [
        "import sqlite3",
        "from app.data.db import query_companies",
        "import app.data.db as d",
        "from app.data import db",
    ]
    for source in bypasses:
        modules = _imported_modules(source)
        caught = any(
            m.startswith(FORBIDDEN_PREFIXES) or m.endswith(".data.db") for m in modules
        )
        assert caught, f"boundary check would not catch: {source!r}"


@pytest.mark.parametrize("package", BOUNDED_PACKAGES, ids=lambda p: p.name)
def test_client_packages_never_use_importlib_to_dodge_the_check(package: Path) -> None:
    """A dynamic import would satisfy the AST check above while still bypassing MCP."""
    if not package.exists():
        pytest.skip(f"{package.name} does not exist yet")

    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in package.rglob("*.py")
        if "import_module" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"Dynamic import in agent package: {offenders}"


# --------------------------------------------------------------------------
# The tool surface
# --------------------------------------------------------------------------


def test_all_expected_tools_are_registered() -> None:
    tools = _registered_tools()
    assert set(tools) == EXPECTED_TOOLS
    assert set(tools) >= REQUIRED_BY_SPEC


def test_no_raw_sql_tool_is_exposed() -> None:
    """Capability-shaped, not table-shaped: there must be no SQL passthrough."""
    forbidden = {"run_sql", "execute_sql", "query", "sql", "raw_query"}
    assert not (EXPECTED_TOOLS & forbidden)


def test_no_write_tools_are_exposed() -> None:
    """The surface is read-only, so no tool call can corrupt the dataset."""
    write_verbs = ("insert", "update", "delete", "drop", "write", "create", "set_")
    assert not [name for name in EXPECTED_TOOLS if name.startswith(write_verbs)]


@pytest.mark.parametrize(
    ("tool_name", "required_text"),
    [
        ("list_sectors", "empty list"),
        ("dataset_overview", "has not been built"),
        ("query_companies", "empty list"),
        ("search_companies", "authoritative"),
        ("get_company_detail", "No data for ticker"),
        ("get_company_signals", "empty list"),
        ("compare_companies", "absent from the result"),
    ],
)
def test_registered_description_states_what_absence_means(
    tool_name: str, required_text: str
) -> None:
    """Absence semantics must survive into the description the model actually reads.

    Regression guard: FastMCP drops everything after ``Args:``, so moving one of these
    sentences below the Args block would silently strip the model's only instruction
    about what an empty result means.
    """
    tool = _registered_tools()[tool_name]
    description = getattr(tool, "description", "") or ""
    assert required_text.lower() in description.lower(), (
        f"{tool_name}'s registered description is missing {required_text!r}. "
        "Is that sentence below the Args: block?"
    )


@pytest.mark.parametrize(
    "tool_name", ["query_companies", "get_company_detail", "compare_companies"]
)
def test_registered_description_states_units(tool_name: str) -> None:
    """Unit conventions must reach the model, or it will misreport every percentage."""
    tool = _registered_tools()[tool_name]
    description = getattr(tool, "description", "") or ""
    assert "decimal fraction" in description.lower()
    assert "never" in description.lower() and "zero" in description.lower()


# --------------------------------------------------------------------------
# Signals: the description may not promise more than the dataset holds
# --------------------------------------------------------------------------

# Every signal kind this project has discussed carrying. The schema is typed so any of
# them *could* be stored; which ones actually are is read from the database below,
# never assumed here.
CANDIDATE_SIGNAL_TYPES = ("headcount", "hiring", "news")

# A mention of an absent kind is fine — required, even — as long as the sentence
# carrying it says the dataset lacks it. These are the words that make a sentence
# disclaim rather than offer. Matched on word boundaries, because a substring test
# would accept "hiring notes" as a disclaimer on the strength of "not" inside "notes"
# — and "hiring notes" is exactly how the architecture doc phrases it, so that is a
# sentence someone could plausibly paste in.
DISCLAIMING_WORDS = ("no", "not", "none", "never", "only", "empty", "lacks", "without")
DISCLAIMER = re.compile(r"\b(?:" + "|".join(DISCLAIMING_WORDS) + r")\b", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", text) if chunk.strip()]


def _signal_types_in_database() -> set[str]:
    """Distinct `signal_type` values the configured database actually contains.

    Read live rather than hardcoded so that loading real hiring or news signals
    relaxes these assertions on its own, instead of leaving a stale test forbidding
    the tool from advertising data it has since gained.
    """
    path = mcp_server.settings.db_file
    if not path.exists():
        pytest.skip("database has not been built")
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT DISTINCT signal_type FROM signals").fetchall()
    except sqlite3.OperationalError as exc:
        pytest.skip(f"signals table unavailable: {exc}")
    finally:
        connection.close()
    return {str(row[0]).strip().lower() for row in rows}


def test_signals_description_names_the_signal_types_the_database_holds() -> None:
    """Whatever the dataset does carry must be named where the model can see it."""
    description = _description("get_company_signals").lower()
    unnamed = sorted(kind for kind in _signal_types_in_database() if kind not in description)
    assert not unnamed, (
        f"get_company_signals never mentions signal types it holds: {unnamed}"
    )


@pytest.mark.parametrize("kind", CANDIDATE_SIGNAL_TYPES)
def test_signals_description_never_promises_an_absent_signal_type(kind: str) -> None:
    """The tool must disclaim signal kinds the dataset cannot deliver, not offer them.

    The brief asks for "the most recent headcount or hiring signal", so a reviewer will
    ask for a hiring signal by name. A description that opens by offering hiring and
    news signals invites the model to answer with a headcount figure as though it were
    the thing asked for — the exact substitution this system treats as a lie. And
    because FastMCP truncates the description at ``Args:``, a correction written into
    the argument docs would never reach the model at all.
    """
    if kind in _signal_types_in_database():
        pytest.skip(f"the database holds {kind!r} signals, so advertising them is honest")

    description = _description("get_company_signals")
    mentions = [line for line in _sentences(description) if kind in line.lower()]
    assert mentions, (
        f"get_company_signals never tells the model the dataset holds no {kind!r} "
        "signals, so it cannot say so when asked for one."
    )
    for sentence in mentions:
        assert DISCLAIMER.search(sentence), (
            f"get_company_signals offers {kind!r} signals the database does not "
            f"contain: {sentence!r}"
        )


def test_argument_descriptions_reach_the_input_schema() -> None:
    """Per-argument guidance survives separately, in the JSON Schema."""
    tools = _registered_tools()
    fields_schema = tools["compare_companies"].parameters["properties"]["fields"]  # type: ignore[attr-defined]
    assert "ev_to_ebitda" in fields_schema["description"]

    sector_schema = tools["query_companies"].parameters["properties"]["sector"]  # type: ignore[attr-defined]
    assert "logistics" in sector_schema["description"]


def test_compare_companies_rejects_unknown_field_as_tool_error() -> None:
    """The allowlist rejection must reach the model as a correctable message."""
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="Unknown field"):
        mcp_server.compare_companies(["NVDA"], ["market_cap; DROP TABLE companies"])


def test_tools_read_the_configured_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tools resolve the DB through settings rather than a hardcoded path."""
    from app.data.db import init_db

    path = tmp_path / "isolated.db"
    init_db(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO companies (ticker, name, sector, industry, country, source, last_updated)
            VALUES ('ZZZ', 'Zeta Test Corp', 'testsector', NULL, NULL, 'fixture', '2026-09-01')
            """
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(type(mcp_server.settings), "db_file", property(lambda _self: path))

    assert mcp_server.list_sectors() == ["testsector"]
    assert mcp_server.search_companies("zeta")[0]["ticker"] == "ZZZ"
    assert mcp_server.get_company_detail("NOPE") == {"error": "No data for ticker 'NOPE'"}
