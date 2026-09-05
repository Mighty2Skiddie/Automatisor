"""``run_agent()`` — the single entry point.

The FastAPI route, the Streamlit app and the eval suite all call this function and
nothing else. There is exactly one implementation of the agent; the interfaces are
doors onto it.

Also owns the two things that must live outside the graph:

* the cached MCP client, so a connection is not rebuilt per request, and
* Langfuse wiring, which stays silent when no keys are configured — running without a
  Langfuse account is a supported configuration, not a degraded one.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agent.graph import build_graph
from app.agent.guardrails import compute_confidence
from app.agent.llm import active_providers
from app.agent.personas import get_persona
from app.agent.schemas import AgentResponse, AnalystDraft
from app.agent.sectors import get_sector
from app.config import settings

logger = logging.getLogger(__name__)

_client: MultiServerMCPClient | None = None
_tools: list[BaseTool] | None = None
_graph: Any = None


class MCPUnavailableError(RuntimeError):
    """The MCP server could not be reached. Surfaces as a 503, never a 500."""


async def get_tools() -> list[BaseTool]:
    """Connect to the MCP server once and cache the tool list."""
    global _client, _tools
    if _tools is not None:
        return _tools
    _client = MultiServerMCPClient(
        {
            "financial-data": {
                "url": settings.mcp_server_url,
                "transport": "streamable_http",
            }
        }
    )
    try:
        _tools = await _client.get_tools()
    except Exception as exc:
        raise MCPUnavailableError(
            f"Could not reach the MCP server at {settings.mcp_server_url}. "
            "Start it with `python -m app.mcp_server.server`."
        ) from exc
    logger.info("mcp_tools_loaded", extra={"count": len(_tools)})
    return _tools


async def get_graph() -> Any:
    """Compile the graph once per process."""
    global _graph
    if _graph is None:
        _graph = build_graph(await get_tools())
    return _graph


async def reset_client() -> None:
    """Drop the cached client. Used by the API's shutdown handler and by tests."""
    global _client, _tools, _graph
    _client = None
    _tools = None
    _graph = None


def _build_callbacks() -> tuple[list[Any], Any]:
    """Langfuse callback handler, or nothing at all when tracing is not configured.

    Trace dimensions (session, persona, sector, interface) are attached on the
    invocation config rather than the constructor: the v4 handler is keyword-only and
    takes no metadata, so per-run attributes travel with the run.
    """
    if not settings.langfuse_enabled:
        return [], None
    try:
        from langfuse.langchain import CallbackHandler

        handler = CallbackHandler()
    except Exception as exc:  # noqa: BLE001 - tracing must never break a request
        logger.warning("langfuse_unavailable", extra={"error": str(exc)})
        return [], None
    return [handler], handler


async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Invoke one MCP tool by name.

    Exists so the API can answer registry and health questions (how many companies,
    which sectors, how current) without importing the data layer. The MCP boundary
    applies to every process in this repo, not only to the agent package.
    """
    tools = {tool.name: tool for tool in await get_tools()}
    if name not in tools:
        raise MCPUnavailableError(f"MCP server does not expose a tool named '{name}'")
    return await tools[name].ainvoke(arguments)


def _assemble_response(
    final_state: dict[str, Any],
    resolved_persona: Any,
    resolved_sector: Any,
    started: float,
    handler: Any,
) -> AgentResponse:
    """Build the wire contract from graph state plus observed execution facts.

    Shared by the blocking and streaming paths so the two can never disagree about
    what a response contains.
    """
    draft: AnalystDraft = final_state.get("draft") or AnalystDraft(
        answer="No answer was produced.", out_of_scope=False
    )
    evidence = final_state.get("retrieved_rows", [])
    tool_records = final_state.get("tool_calls_made", [])

    snapshot_dates = [
        str(row["snapshot_date"])
        for row in evidence
        if isinstance(row, dict) and row.get("snapshot_date")
    ]
    latest = max(snapshot_dates) if snapshot_dates else None

    # Recomputed here from the same helper the output guard uses, so the response can
    # never carry a confidence the model chose for itself.
    confidence, confidence_reason = compute_confidence(
        evidence=evidence,
        requested_fields=list(resolved_persona.priority_fields),
        snapshot_date=latest,
        out_of_scope=draft.out_of_scope,
    )

    return AgentResponse(
        answer=draft.answer,
        key_points=draft.key_points,
        companies_referenced=draft.companies_referenced,
        citations=draft.citations,
        caveats=draft.caveats,
        out_of_scope=draft.out_of_scope,
        persona=resolved_persona.key,
        persona_lens=resolved_persona.lens,
        sector=resolved_sector.key,
        confidence=confidence,
        confidence_reason=confidence_reason,
        data_as_of=latest,
        tools_called=[record.name for record in tool_records],
        tool_calls=tool_records,
        guard_flags=final_state.get("guard_flags", []),
        llm_provider=(active_providers() or ["none"])[0],
        trace_id=getattr(handler, "last_trace_id", None) if handler else None,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _invocation_config(
    callbacks: list[Any],
    session_id: str | None,
    persona_key: str,
    sector_key: str,
    interface: str,
) -> dict[str, Any]:
    config: dict[str, Any] = {"recursion_limit": 25}
    if callbacks:
        config["callbacks"] = callbacks
        config["metadata"] = {
            "langfuse_session_id": session_id or "",
            "langfuse_tags": [persona_key, sector_key, interface],
            "persona": persona_key,
            "sector": sector_key,
            "interface": interface,
        }
    return config


async def run_agent(
    query: str,
    persona: str,
    sector: str,
    session_id: str | None = None,
    interface: str = "api",
) -> AgentResponse:
    """Answer one question as one persona over one sector.

    Raises ``UnknownPersonaError`` / ``UnknownSectorError`` for bad parameters — the
    API turns those into a 422 listing valid values, before any token is spent — and
    ``MCPUnavailableError`` when the data service is down, which becomes a 503.
    """
    started = time.perf_counter()
    resolved_persona = get_persona(persona)
    resolved_sector = get_sector(sector)

    graph = await get_graph()
    callbacks, handler = _build_callbacks()

    final_state = await graph.ainvoke(
        {"query": query, "persona": resolved_persona, "sector": resolved_sector},
        config=_invocation_config(
            callbacks, session_id, resolved_persona.key, resolved_sector.key, interface
        ),
    )
    return _assemble_response(
        final_state, resolved_persona, resolved_sector, started, handler
    )


async def run_agent_stream(
    query: str,
    persona: str,
    sector: str,
    session_id: str | None = None,
    interface: str = "web",
) -> AsyncIterator[tuple[str, Any]]:
    """Same agent, streamed as ``(event, payload)`` pairs.

    Yields ``progress`` events named after the graph node that just completed, then
    ``evidence`` as soon as retrieval lands, then a final ``response``. The evidence
    arriving before the answer is the point: the UI shows retrieval happening rather
    than a spinner, which is the visual claim that the agent is querying rather than
    recalling.

    This is a second *transport*, not a second implementation — it drives the same
    graph and ends in the same ``_assemble_response``.
    """
    started = time.perf_counter()
    resolved_persona = get_persona(persona)
    resolved_sector = get_sector(sector)

    graph = await get_graph()
    callbacks, handler = _build_callbacks()

    accumulated: dict[str, Any] = {}
    evidence_sent = False

    async for update in graph.astream(
        {"query": query, "persona": resolved_persona, "sector": resolved_sector},
        config=_invocation_config(
            callbacks, session_id, resolved_persona.key, resolved_sector.key, interface
        ),
        stream_mode="updates",
    ):
        for node_name, node_state in update.items():
            if not isinstance(node_state, dict):
                continue
            accumulated.update(node_state)
            yield "progress", {"node": node_name}

            rows = accumulated.get("retrieved_rows") or []
            if rows and not evidence_sent:
                evidence_sent = True
                yield "evidence", {
                    "rows": rows,
                    "tool_calls": [
                        record.model_dump()
                        for record in accumulated.get("tool_calls_made", [])
                    ],
                }

    response = _assemble_response(
        accumulated, resolved_persona, resolved_sector, started, handler
    )
    yield "response", response.model_dump()
