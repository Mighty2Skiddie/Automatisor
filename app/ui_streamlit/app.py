"""Streamlit interface — the human-facing door named in the brief.

This app calls ``run_agent`` **directly, in-process**. No HTTP, no second
implementation: it is the most literal possible demonstration that the UI and the
REST API are two doors onto one agent.

    streamlit run app/ui_streamlit/app.py

Like every other client-side package, this one never imports the data layer —
``tests/test_mcp_tools.py`` fails the build if it does.

The one genuinely tricky part is the event loop, and it is worth explaining because
the obvious implementation breaks on the *second* question rather than the first:

Streamlit re-executes this script top to bottom on every interaction. Calling
``asyncio.run(run_agent(...))`` would therefore create and tear down a new event loop
per interaction. The MCP client is cached across requests (a deliberate decision in
``runner.py`` — reconnecting per query is wasteful), and its underlying httpx
connection is bound to the loop that created it. The second question would then run
on a fresh loop while holding a connection owned by a closed one, and fail with a
confusing "attached to a different loop" error.

So the agent gets one long-lived loop on a daemon thread, cached by
``st.cache_resource`` for the life of the process, and coroutines are submitted to it
with ``run_coroutine_threadsafe``. One loop, one MCP connection, any number of reruns.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import streamlit as st

# `streamlit run` executes this file as a top-level script, which puts *its own*
# directory on sys.path ahead of everything else. Since this file is itself named
# app.py, a bare `import app.agent...` then resolves `app` to this module rather than
# to the project's app package, and fails with "'app' is not a package". Putting the
# project root first fixes the resolution. pytest never hits this, because it imports
# the module by its dotted path with the rootdir already on sys.path — so it is only
# reproducible by actually running the app.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
# Move to the front rather than merely ensure presence: Streamlit already has the
# working directory on sys.path, but *behind* the script's own directory, so a
# presence check would pass while this file still shadowed the package.
while _PROJECT_ROOT in sys.path:
    sys.path.remove(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
# And drop any binding of the name "app" that already resolved to this script, or the
# import below reuses the cached non-package module.
if "app" in sys.modules and not hasattr(sys.modules["app"], "__path__"):
    del sys.modules["app"]

from app.agent.personas import PERSONAS  # noqa: E402
from app.agent.runner import MCPUnavailableError, run_agent  # noqa: E402
from app.agent.schemas import AgentResponse  # noqa: E402
from app.agent.sectors import SECTORS  # noqa: E402
from app.config import settings  # noqa: E402

QUERY_TIMEOUT_SECONDS = 300

PERSONA_COLOURS = {
    "mf_analyst": "#1F6F5C",
    "equity_analyst": "#A84B12",
    "pe_analyst": "#45369B",
}

CONFIDENCE_ICON = {"high": "🟢", "medium": "🟡", "low": "🔴"}

EXAMPLE_QUESTIONS = [
    "Is this sector a good place to put money to work right now?",
    "Which would fit a long-term core holding versus a name to avoid?",
    "Walk me through the margin profile — who's improving and who's under pressure?",
    "If I had to take one company private, which and what's the operational thesis?",
]


@st.cache_resource
def get_agent_loop() -> asyncio.AbstractEventLoop:
    """One event loop for the life of the process, on a daemon thread.

    Cached so Streamlit's per-interaction reruns reuse it instead of creating a loop
    the cached MCP connection does not belong to.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="agent-loop", daemon=True)
    thread.start()
    return loop


def run_sync(coro: Any) -> Any:
    """Run a coroutine on the persistent loop and block until it finishes."""
    future: Future[Any] = asyncio.run_coroutine_threadsafe(coro, get_agent_loop())
    return future.result(timeout=QUERY_TIMEOUT_SECONDS)


def format_value(field: str, value: float | str | None) -> str:
    """Render a field for display, honouring the schema's unit conventions.

    NULL is an em dash, never 0 and never blank: "we do not have this" and "this is
    zero" are different facts, and the whole grounding story depends on not
    conflating them.
    """
    if value is None:
        return "—"
    if isinstance(value, str):
        return value

    fractions = {
        "revenue_growth",
        "gross_margin",
        "operating_margin",
        "profit_margin",
        "return_on_equity",
        "dividend_yield",
    }
    if field in fractions:
        return f"{value * 100:.2f}%"
    if field == "debt_to_equity":
        return f"{value:.2f}x"
    if field in {"market_cap", "revenue", "free_cash_flow"}:
        magnitude = abs(value)
        if magnitude >= 1e12:
            return f"${value / 1e12:.2f}T"
        if magnitude >= 1e9:
            return f"${value / 1e9:.2f}B"
        if magnitude >= 1e6:
            return f"${value / 1e6:.2f}M"
        return f"${value:,.0f}"
    return f"{value:,.2f}"


def render_sidebar() -> tuple[str, str]:
    """Persona and sector selectors. Returns the chosen keys."""
    with st.sidebar:
        st.title("Sector Analyst")

        persona_key = st.radio(
            "Persona",
            options=list(PERSONAS),
            format_func=lambda key: PERSONAS[key].name,
            help="The persona changes which fields the analyst weights, not just its tone.",
        )
        st.caption(PERSONAS[persona_key].lens)
        st.markdown(
            "**Reads first:** " + ", ".join(f"`{f}`" for f in PERSONAS[persona_key].priority_fields)
        )

        st.divider()

        sector_key = st.radio(
            "Sector",
            options=list(SECTORS),
            format_func=lambda key: SECTORS[key].label,
        )
        st.caption(SECTORS[sector_key].description)

        st.divider()
        st.caption(
            f"{len(SECTORS)} sectors · {len(PERSONAS)} personas · "
            f"{len(SECTORS) * len(PERSONAS)} combinations"
        )
        st.caption(f"Data reached over MCP at `{settings.mcp_server_url}`")

    return persona_key, sector_key


def render_evidence(response: AgentResponse) -> None:
    """The evidence panel: the exact rows behind every claim.

    This is the answer to "is it retrieving or bluffing?", so it shows the field
    values themselves rather than a count of citations.
    """
    if not response.citations:
        st.info("No company rows were cited for this answer.")
        return

    for citation in response.citations:
        with st.expander(
            f"**{citation.ticker}** — {citation.company_name}", expanded=False
        ):
            if citation.values:
                st.table(
                    {
                        "field": list(citation.values),
                        "value": [
                            format_value(name, value) for name, value in citation.values.items()
                        ],
                    }
                )
            else:
                st.caption("Fields used: " + ", ".join(citation.fields_used))
            st.caption(f"source: {citation.source or 'n/a'} · as of {citation.as_of or 'n/a'}")


def render_answer(response: AgentResponse) -> None:
    """The answer, its key points, and how it was produced."""
    accent = PERSONA_COLOURS.get(response.persona, "#5C6670")
    st.markdown(
        f"<div style='border-left:3px solid {accent};padding-left:14px'>"
        f"<strong>{PERSONAS[response.persona].name}</strong> · "
        f"{SECTORS[response.sector].label}</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    if response.out_of_scope:
        st.warning("Outside this dataset — see the answer below.", icon="🔍")

    st.markdown(response.answer)

    if response.key_points:
        st.markdown("**Key points**")
        for point in response.key_points:
            st.markdown(f"- {point}")

    icon = CONFIDENCE_ICON.get(response.confidence, "⚪")
    # st.metric renders its value in a large font and truncates to the column width,
    # so "Medium" clipped to "Medi…" at an even 1:1:1:1 split. The icon also moved out
    # of the value and into the label for the same reason — a confidence chip you
    # cannot read is worse than none.
    columns = st.columns([1.7, 1, 1, 1.1])
    columns[0].metric(f"{icon} Confidence", response.confidence.title())
    columns[1].metric("Companies", len(response.companies_referenced))
    columns[2].metric("Tool calls", len(response.tool_calls))
    columns[3].metric("Latency", f"{response.latency_ms / 1000:.1f}s")
    st.caption(f"Why this confidence: {response.confidence_reason}")

    if response.caveats:
        for caveat in response.caveats:
            st.caption(f"⚠️ {caveat}")

    with st.expander(f"MCP tool calls ({len(response.tool_calls)})"):
        for record in response.tool_calls:
            st.markdown(
                f"`{record.name}({record.arguments})` → **{record.row_count}** rows"
                + (f" · error: {record.error}" if record.error else "")
            )
        st.caption(f"Answered by `{response.llm_provider}` · data as of {response.data_as_of}")
        if response.trace_id:
            st.caption(f"Langfuse trace: `{response.trace_id}`")
        if response.guard_flags:
            st.caption("Guardrail flags: " + ", ".join(response.guard_flags))


def main() -> None:
    st.set_page_config(page_title="Sector Analyst Agent", page_icon="📊", layout="wide")
    persona_key, sector_key = render_sidebar()

    st.markdown("#### Ask about the companies in this dataset")
    with st.form("question", clear_on_submit=False):
        question = st.text_area(
            "Question",
            value=st.session_state.get("question", EXAMPLE_QUESTIONS[0]),
            height=80,
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Ask", type="primary")

    example_columns = st.columns(len(EXAMPLE_QUESTIONS))
    for column, example in zip(example_columns, EXAMPLE_QUESTIONS, strict=True):
        if column.button(example[:38] + "…", key=example, help=example):
            st.session_state["question"] = example
            st.rerun()

    if submitted and question.strip():
        with st.spinner("Querying the database over MCP, then reasoning…"):
            try:
                st.session_state["response"] = run_sync(
                    run_agent(
                        query=question,
                        persona=persona_key,
                        sector=sector_key,
                        interface="streamlit",
                    )
                )
                st.session_state.pop("error", None)
            except MCPUnavailableError as exc:
                st.session_state["error"] = ("🔌", f"Data service unavailable. {exc}")
                st.session_state.pop("response", None)
            except TimeoutError:
                st.session_state["error"] = (
                    "⏱️",
                    (
                        f"The agent did not answer within {QUERY_TIMEOUT_SECONDS}s. "
                        "The LLM provider may be rate limited — try again."
                    ),
                )
                st.session_state.pop("response", None)

    # Rendered from session state, not from `submitted`. Streamlit re-runs this script
    # on every interaction, and a form-submit flag is only true on the run that
    # submitted it — so rendering off `submitted` makes the answer vanish the moment
    # the user expands an evidence row, which is the one thing they are meant to do.
    if error := st.session_state.get("error"):
        icon, message = error
        st.error(message, icon=icon)

    response: AgentResponse | None = st.session_state.get("response")
    if response is not None:
        answer_column, evidence_column = st.columns([3, 2], gap="large")
        with answer_column:
            render_answer(response)
        with evidence_column:
            st.markdown("**Evidence** — rows the agent actually read")
            render_evidence(response)


if __name__ == "__main__":
    main()
