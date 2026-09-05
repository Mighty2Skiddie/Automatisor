"""Tests for the Streamlit interface's non-UI logic.

Streamlit widgets are not exercised here — what matters is the part that is easy to
get wrong and invisible until the second question: the event loop that owns the
cached MCP connection, and the unit formatting that decides whether a reviewer sees
"18.75%" or "0.1875".
"""

from __future__ import annotations

import asyncio

import pytest

from app.ui_streamlit.app import format_value, get_agent_loop, run_sync

# --------------------------------------------------------------------------
# The event loop — the bug this design exists to prevent
# --------------------------------------------------------------------------


def test_the_agent_loop_is_reused_across_reruns() -> None:
    """Streamlit re-executes the script per interaction; the loop must survive it.

    A fresh loop per rerun is what breaks the *second* question: the MCP client is
    cached across requests and its connection belongs to the loop that opened it.
    """
    assert get_agent_loop() is get_agent_loop()


def test_the_agent_loop_is_running_on_a_background_thread() -> None:
    loop = get_agent_loop()
    assert loop.is_running()
    assert not loop.is_closed()


def test_consecutive_questions_run_on_the_same_loop() -> None:
    """The regression: question two must not land on a different loop from question one.

    Each call records the loop it actually executed on. If ``run_sync`` created a new
    loop per call — the naive ``asyncio.run`` implementation — these would differ, and
    a real MCP connection opened during the first call would be unusable in the second.
    """

    async def which_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    first = run_sync(which_loop())
    second = run_sync(which_loop())
    third = run_sync(which_loop())

    assert first is second is third is get_agent_loop()


def test_run_sync_returns_the_coroutine_result() -> None:
    async def add() -> int:
        await asyncio.sleep(0)
        return 40 + 2

    assert run_sync(add()) == 42


def test_run_sync_propagates_exceptions_rather_than_hanging() -> None:
    """A failed agent run must surface to the UI, not silently produce nothing."""

    async def boom() -> None:
        raise ValueError("agent failed")

    with pytest.raises(ValueError, match="agent failed"):
        run_sync(boom())


def test_awaited_state_persists_across_calls_on_the_loop() -> None:
    """Something opened on the loop in one call is still usable in the next.

    This is the property the cached MCP client actually depends on: an asyncio
    primitive bound to the loop keeps working across Streamlit reruns.
    """
    holder: dict[str, asyncio.Event] = {}

    async def create() -> None:
        holder["event"] = asyncio.Event()

    async def use() -> bool:
        holder["event"].set()
        return holder["event"].is_set()

    run_sync(create())
    assert run_sync(use()) is True


# --------------------------------------------------------------------------
# Unit formatting — the schema's conventions, rendered
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("operating_margin", 0.1875, "18.75%"),
        ("gross_margin", 0.74674, "74.67%"),
        ("revenue_growth", -0.111, "-11.10%"),
        ("dividend_yield", 0.0637, "6.37%"),
        ("return_on_equity", 1.488, "148.80%"),
        ("debt_to_equity", 1.54, "1.54x"),
        ("free_cash_flow", 3_710_000_000.0, "$3.71B"),
        ("market_cap", 5_570_000_000_000.0, "$5.57T"),
        ("revenue", 5_603_000_000.0, "$5.60B"),
        ("free_cash_flow", -24_540_000_000.0, "$-24.54B"),
        ("pe_ratio", 29.202532, "29.20"),
        ("ev_to_ebitda", 27.238, "27.24"),
        ("beta", 2.489, "2.49"),
    ],
)
def test_values_render_in_their_documented_units(
    field: str, value: float, expected: str
) -> None:
    assert format_value(field, value) == expected


@pytest.mark.parametrize(
    "field",
    ["operating_margin", "debt_to_equity", "free_cash_flow", "pe_ratio", "market_cap"],
)
def test_null_renders_as_an_em_dash_never_zero(field: str) -> None:
    """A missing value and a zero value are different facts.

    Rendering NULL as 0 would put a number on screen that the database does not
    contain — the exact hallucination the schema's NULL discipline exists to prevent.
    """
    rendered = format_value(field, None)
    assert rendered == "—"
    assert "0" not in rendered


def test_a_genuine_zero_still_renders_as_zero() -> None:
    """AMD pays no dividend: that is a real 0.00%, not missing data."""
    assert format_value("dividend_yield", 0.0) == "0.00%"


def test_string_values_pass_through_unchanged() -> None:
    assert format_value("source", "yfinance/yahoo") == "yfinance/yahoo"
