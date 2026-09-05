"""The LangGraph state machine.

    START -> validate -> guard_input -> plan -> tools -> verify_grounding
                             |                   ^            |
                             v                   |____________|
                          refuse                 v
                                              compose -> guard_output -> END

``verify_grounding`` is the most important node here. It is the mechanical
enforcement of "no hardcoded facts in prompts": an answer produced with zero tool
calls cannot be grounded, so the graph refuses to ship it. That converts a policy
into an invariant.

This module is an MCP *client*. It never imports ``app.data`` — ``tests/test_mcp_tools.py``
fails the build if it does.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.agent.guardrails import (
    backfill_citations,
    check_input,
    check_output,
    derive_referenced_companies,
)
from app.agent.llm import build_chat_model, build_structured_model
from app.agent.personas import Persona
from app.agent.prompts import build_retrieval_nudge, build_system_prompt
from app.agent.schemas import AnalystDraft, Citation, ToolCallRecord
from app.agent.sectors import Sector

logger = logging.getLogger(__name__)

MAX_TOOL_LOOPS = 5
MAX_GROUNDING_RETRIES = 2


class AgentState(TypedDict, total=False):
    """Everything the graph carries between nodes."""

    query: str
    persona: Persona
    sector: Sector
    messages: Annotated[list, add_messages]
    tool_calls_made: list[ToolCallRecord]
    retrieved_rows: list[dict[str, Any]]
    guard_flags: list[str]
    retries: int
    tool_loops: int
    requires_advice_caveat: bool
    refusal: str | None
    draft: AnalystDraft | None


# --------------------------------------------------------------------------
# Evidence extraction
# --------------------------------------------------------------------------


def _rows_from_tool_output(payload: Any) -> list[dict[str, Any]]:
    """Normalise whatever a tool returned into a list of row dicts.

    Four shapes have to be handled, and getting this wrong is silent:

    1. **MCP content blocks** — ``[{"type": "text", "text": "<json>"}]``. This is what
       ``langchain-mcp-adapters`` actually hands back, and it is the important case.
       Treating the wrapper as a row makes every answer look ungrounded while the
       tool call plainly succeeded, because the evidence contains ``type``/``text``
       keys and no tickers.
    2. a JSON string,
    3. a list of row dicts,
    4. a single dict, including the typed ``{"error": ...}`` absence result and
       ``dataset_overview``'s nested shape.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return []

    if isinstance(payload, list):
        # Unwrap MCP content blocks before treating list items as rows.
        if payload and all(
            isinstance(item, dict) and item.get("type") == "text" and "text" in item
            for item in payload
        ):
            rows: list[dict[str, Any]] = []
            for block in payload:
                rows.extend(_rows_from_tool_output(block["text"]))
            return rows
        return [row for row in payload if isinstance(row, dict)]

    if isinstance(payload, dict):
        if "error" in payload:
            return []
        # dataset_overview nests its rows.
        if isinstance(payload.get("sectors"), list):
            return [row for row in payload["sectors"] if isinstance(row, dict)]
        return [payload]

    return []


def _latest_snapshot(rows: list[dict[str, Any]]) -> str | None:
    dates = [
        str(row["snapshot_date"])
        for row in rows
        if isinstance(row, dict) and row.get("snapshot_date")
    ]
    return max(dates) if dates else None


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def build_graph(tools: list[BaseTool]) -> Any:
    """Compile the state machine around a set of MCP tools."""
    planner = build_chat_model(tools)
    # Structured output is built per provider so each fallback uses the method that
    # actually works for it; see build_structured_model.
    composer = build_structured_model(AnalystDraft)
    tool_node = ToolNode(tools, handle_tool_errors=True)

    async def validate(_state: AgentState) -> dict[str, Any]:
        """Persona and sector are already resolved by the runner, so this seeds state."""
        return {
            "tool_calls_made": [],
            "retrieved_rows": [],
            "guard_flags": [],
            "retries": 0,
            "tool_loops": 0,
            "refusal": None,
            "draft": None,
        }

    async def guard_input(state: AgentState) -> dict[str, Any]:
        result = check_input(state["query"])
        update: dict[str, Any] = {
            "guard_flags": result.flags,
            "requires_advice_caveat": result.requires_advice_caveat,
        }
        if not result.allowed:
            update["refusal"] = result.refusal
            return update

        system = build_system_prompt(state["persona"], state["sector"])
        # The redacted form is what travels onward, not just what gets logged. An
        # email address or card number is never needed to answer a question about
        # company financials, and the message list is exactly what reaches the LLM
        # provider and the tracing backend — so redacting only at the log boundary
        # would leave the raw value in both.
        update["messages"] = [
            SystemMessage(content=system),
            HumanMessage(content=result.sanitised_query or state["query"]),
        ]
        return update

    async def plan(state: AgentState) -> dict[str, Any]:
        """Let the model choose and issue MCP tool calls."""
        response = await planner.ainvoke(state["messages"])
        return {"messages": [response], "tool_loops": state.get("tool_loops", 0) + 1}

    async def record_tools(state: AgentState) -> dict[str, Any]:
        """Harvest rows and an audit trail from ToolMessages not yet recorded.

        Deduplicated on tool_call_id: this node runs after every tool loop and sees
        the whole accumulated message list each time, so without it the same rows
        would be counted once per remaining iteration.
        """
        records: list[ToolCallRecord] = list(state.get("tool_calls_made", []))
        rows: list[dict[str, Any]] = list(state.get("retrieved_rows", []))
        already = {record.tool_call_id for record in records}

        requested: dict[str, tuple[str, dict[str, Any]]] = {}
        for message in state["messages"]:
            if isinstance(message, AIMessage):
                for call in message.tool_calls or []:
                    requested[call["id"]] = (call["name"], call.get("args", {}))

        for message in state["messages"]:
            if not isinstance(message, ToolMessage):
                continue
            call_id = message.tool_call_id or ""
            if call_id in already:
                continue
            already.add(call_id)

            extracted = _rows_from_tool_output(message.content)
            name, arguments = requested.get(call_id, (message.name or "", {}))
            is_error = getattr(message, "status", None) == "error"
            records.append(
                ToolCallRecord(
                    name=name,
                    arguments=arguments,
                    row_count=len(extracted),
                    error=str(message.content)[:300] if is_error else None,
                    tool_call_id=call_id,
                )
            )
            rows.extend(extracted)

        return {"tool_calls_made": records, "retrieved_rows": rows}

    async def verify_grounding(state: AgentState) -> dict[str, Any]:
        """Nudge the model to retrieve if it tried to answer from memory."""
        if state.get("tool_calls_made"):
            return {}
        return {
            "retries": state.get("retries", 0) + 1,
            "messages": [HumanMessage(content=build_retrieval_nudge(state["persona"]))],
        }

    async def compose(state: AgentState) -> dict[str, Any]:
        """Turn the evidence into the structured draft.

        Structured output is a separate call with no tools bound. Combining a response
        schema with function calling is not generally supported on this model tier, and
        the architecture already separates planning from composition for exactly this
        reason.
        """
        evidence = state.get("retrieved_rows", [])
        system = build_system_prompt(state["persona"], state["sector"])
        transcript = json.dumps(evidence, indent=2, default=str)[:60_000]

        draft = await composer.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(
                    content=(
                        f"Question: {state['query']}\n\n"
                        f"Evidence retrieved from the database ({len(evidence)} rows). "
                        "Every figure in your answer must come from these rows:\n\n"
                        f"{transcript}\n\n"
                        "Write your analysis now, as the analyst you are."
                    )
                ),
            ]
        )
        return {"draft": draft}

    async def refuse_ungrounded(state: AgentState) -> dict[str, Any]:
        """Emit an honest refusal when retrieval never happened."""
        return {
            "draft": AnalystDraft(
                answer=(
                    "I wasn't able to query the database for this question, so I have "
                    "no evidence to work from. Rather than answer from memory, I'd "
                    "rather tell you plainly that I have nothing grounded to offer. "
                    "Please try again, or rephrase the question."
                ),
                out_of_scope=False,
            ),
            "guard_flags": [*state.get("guard_flags", []), "ungrounded"],
        }

    async def refuse(state: AgentState) -> dict[str, Any]:
        return {
            "draft": AnalystDraft(
                answer=state.get("refusal") or "I can't help with that request.",
                out_of_scope=False,
            )
        }

    async def guard_output(state: AgentState) -> dict[str, Any]:
        """Verify the draft against evidence and recompute confidence."""
        draft = state.get("draft")
        if draft is None:
            return {}

        evidence = state.get("retrieved_rows", [])
        priority = list(state["persona"].priority_fields)
        result = check_output(
            draft=draft,
            evidence=evidence,
            requested_fields=priority,
            snapshot_date=_latest_snapshot(evidence),
            requires_advice_caveat=state.get("requires_advice_caveat", False),
        )
        # Drop citations for companies that were never retrieved rather than
        # presenting fabricated provenance.
        known = {
            str(row["ticker"]).upper()
            for row in evidence
            if isinstance(row, dict) and row.get("ticker")
        }
        citations: list[Citation] = [
            citation for citation in draft.citations if citation.ticker.upper() in known
        ]

        # Recover what the model discussed but failed to declare. Structured-output
        # compliance is imperfect; the evidence is authoritative, so the answer is
        # read against it rather than re-prompting for a formatting slip.
        referenced = [t.upper() for t in draft.companies_referenced if t.upper() in known]
        if not referenced:
            referenced = derive_referenced_companies(draft.answer, evidence)
        citations = backfill_citations(referenced, citations, evidence, priority)

        cleaned = draft.model_copy(
            update={
                "caveats": result.caveats,
                "citations": citations,
                "companies_referenced": referenced,
                # Evidence exists and companies were discussed, so this cannot be an
                # out-of-scope refusal whatever the model set.
                "out_of_scope": draft.out_of_scope and not referenced,
            }
        )
        return {
            "draft": cleaned,
            "guard_flags": [*state.get("guard_flags", []), *result.flags],
        }

    # ---- edges ----

    def after_guard_input(state: AgentState) -> str:
        return "refuse" if state.get("refusal") else "plan"

    def after_plan(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            if state.get("tool_loops", 0) >= MAX_TOOL_LOOPS:
                return "verify_grounding"
            return "tools"
        return "verify_grounding"

    def after_verify(state: AgentState) -> str:
        if state.get("tool_calls_made"):
            return "compose"
        if state.get("retries", 0) < MAX_GROUNDING_RETRIES:
            return "plan"
        return "refuse_ungrounded"

    graph = StateGraph(AgentState)
    graph.add_node("validate", validate)
    graph.add_node("guard_input", guard_input)
    graph.add_node("plan", plan)
    graph.add_node("tools", tool_node)
    graph.add_node("record_tools", record_tools)
    graph.add_node("verify_grounding", verify_grounding)
    graph.add_node("compose", compose)
    graph.add_node("guard_output", guard_output)
    graph.add_node("refuse", refuse)
    graph.add_node("refuse_ungrounded", refuse_ungrounded)

    graph.add_edge(START, "validate")
    graph.add_edge("validate", "guard_input")
    graph.add_conditional_edges(
        "guard_input", after_guard_input, {"refuse": "refuse", "plan": "plan"}
    )
    graph.add_conditional_edges(
        "plan", after_plan, {"tools": "tools", "verify_grounding": "verify_grounding"}
    )
    graph.add_edge("tools", "record_tools")
    graph.add_edge("record_tools", "plan")
    graph.add_conditional_edges(
        "verify_grounding",
        after_verify,
        {
            "compose": "compose",
            "plan": "plan",
            "refuse_ungrounded": "refuse_ungrounded",
        },
    )
    graph.add_edge("compose", "guard_output")
    # Refusals pass through the output guard too, so the not-advice caveat and the
    # confidence calculation apply on every path out of the graph, not just the
    # happy one.
    graph.add_edge("refuse", "guard_output")
    graph.add_edge("refuse_ungrounded", "guard_output")
    graph.add_edge("guard_output", END)

    return graph.compile()
