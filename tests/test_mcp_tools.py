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
