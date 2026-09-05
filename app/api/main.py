"""FastAPI application — the machine-facing door onto the agent.

The UI and this API both call ``run_agent``; there is one implementation of the
agent and two ways in. Nothing in this module talks to the database: registry and
health data come over MCP like everything else, and ``tests/test_mcp_tools.py``
enforces that for ``app/api`` as well as ``app/agent``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.llm import NoProviderConfiguredError, active_providers, describe_chain
from app.agent.personas import (
    PERSONA_KEYS,
    PERSONAS,
    UnknownPersonaError,
    get_persona,
)
from app.agent.runner import (
    MCPUnavailableError,
    call_tool,
    get_tools,
    reset_client,
    run_agent,
    run_agent_stream,
)
from app.agent.schemas import AgentResponse
from app.agent.sectors import (
    SECTOR_KEYS,
    SECTORS,
    UnknownSectorError,
    get_sector,
)
from app.config import settings
from app.logging_conf import configure_logging, request_id_var

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """Body of ``POST /v1/query``.

    ``persona`` and ``sector`` are plain strings rather than ``Literal`` types on
    purpose. A Literal makes FastAPI reject the body before the handler runs, and the
    generated error names the field but not the valid values — so the reviewer's
    "what sectors do you have?" question goes unanswered. Validating in the handler
    lets the 422 carry the actual list.
    """

    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, Field(min_length=1, max_length=2_000)]
    persona: str
    sector: str
    session_id: str | None = None


class PersonaInfo(BaseModel):
    key: str
    name: str
    lens: str
    priority_fields: list[str]


class SectorInfo(BaseModel):
    key: str
    label: str
    description: str
    company_count: int = 0
    latest_snapshot: str | None = None


class HealthResponse(BaseModel):
    status: str
    mcp: str
    db: str
    llm: str
    llm_chain: str


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


class SlidingWindowLimiter:
    """Per-IP request cap over a rolling minute.

    In-process and therefore per-worker: running four uvicorn workers gives four
    times the configured limit. That is an accepted limit of a single-node
    assessment build, documented rather than hidden — a real deployment puts this in
    a shared store or at the edge.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)


# --------------------------------------------------------------------------
# Lifespan
# --------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Warm the MCP connection at startup and drop it at shutdown.

    Connecting here rather than on first request means a misconfigured MCP URL is
    visible in the startup log instead of surfacing as a slow first query.
    """
    configure_logging(settings.log_level)
    logger.info(
        "api_starting",
        extra={
            "mcp_url": settings.mcp_server_url,
            "llm_chain": describe_chain(),
            "sectors": list(SECTOR_KEYS),
            "personas": list(PERSONA_KEYS),
        },
    )
    try:
        tools = await get_tools()
        logger.info("mcp_connected", extra={"tool_count": len(tools)})
    except MCPUnavailableError as exc:
        # Not fatal: the app should still serve /healthz so an operator can see what
        # is wrong, rather than crash-looping with the reason only in the logs.
        logger.error("mcp_unavailable_at_startup", extra={"error": str(exc)})
    yield
    await reset_client()
    logger.info("api_stopped")


app = FastAPI(
    title="Sector Analyst Agent",
    version="1.0.0",
    description=(
        "One configurable agent, three analyst personas, four sectors. All company "
        "data is reached over MCP; every figure in an answer comes from a live tool "
        "call. This is analysis, not investment advice."
    ),
    lifespan=lifespan,
)

# Middleware order matters and is the reverse of registration: the LAST registered
# wraps everything. Registering rate_limit first and CORS last gives the stack
# CORS -> request_context -> rate_limit, so a 429 still carries CORS headers (or the
# browser reports a CORS failure instead of a rate limit) and still produces a
# request-scoped log line with its X-Request-ID.


@app.middleware("http")
async def rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.url.path.startswith("/v1/query"):
        client_ip = request.client.host if request.client else "unknown"
        if not await limiter.allow(client_ip):
            logger.warning("rate_limited", extra={"client": client_ip})
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": (
                        f"Rate limit exceeded: {settings.rate_limit_per_minute} "
                        "requests per minute."
                    )
                },
            )
    return await call_next(request)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach a request id, time the call, and emit one structured log line."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        # Logged inside the context, not after it: resetting the ContextVar first
        # strips request_id from the very line it is meant to correlate.
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    finally:
        request_id_var.reset(token)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


@app.exception_handler(UnknownPersonaError)
async def _unknown_persona(_request: Request, exc: UnknownPersonaError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": str(exc), "valid_personas": list(PERSONA_KEYS)},
    )


@app.exception_handler(UnknownSectorError)
async def _unknown_sector(_request: Request, exc: UnknownSectorError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": str(exc), "valid_sectors": list(SECTOR_KEYS)},
    )


@app.exception_handler(MCPUnavailableError)
async def _mcp_down(_request: Request, exc: MCPUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )


@app.exception_handler(NoProviderConfiguredError)
async def _no_provider(_request: Request, exc: NoProviderConfiguredError) -> JSONResponse:
    """A missing LLM key is a service-availability problem, not a client error."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc), "llm_chain": describe_chain()},
    )


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    """Anything unforeseen still leaves the client a JSON body it can act on.

    The default handler returns ``Internal Server Error`` as text/plain, which a JSON
    client cannot parse and which carries no way to find the matching log line. The
    message stays generic — an unknown provider error can contain raw response text —
    and the request id is the handle for support.
    """
    logger.exception("unhandled_error", extra={"error_type": type(exc).__name__})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected error occurred while answering.",
            "request_id": request_id_var.get() or None,
        },
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@app.post(
    "/v1/query",
    response_model=AgentResponse,
    summary="Ask one question as one persona over one sector",
    responses={
        422: {"description": "Unknown persona or sector; the body lists valid values."},
        429: {"description": "Rate limited."},
        503: {"description": "MCP data service unreachable."},
    },
)
async def post_query(request: QueryRequest) -> AgentResponse:
    """Run the agent and return the full structured response.

    Consumable by another system: the answer arrives alongside the companies it
    referenced, the exact field values behind each claim, a confidence with the
    reason it is that confidence, and the tools that were actually called.
    """
    return await run_agent(
        query=request.query,
        persona=request.persona,
        sector=request.sector,
        session_id=request.session_id,
        interface="api",
    )


@app.post(
    "/v1/query/stream",
    summary="The same query, streamed as server-sent events",
    responses={422: {"description": "Unknown persona or sector."}},
)
async def post_query_stream(request: QueryRequest) -> StreamingResponse:
    """Stream graph progress, then evidence, then the final response.

    Exists because the UI promises the evidence panel fills *before* the answer —
    the visible proof that the agent retrieves rather than recalls. A single
    blocking POST cannot express that ordering.

    Events: ``progress`` (node name), ``evidence`` (retrieved rows and tool calls),
    ``response`` (the complete AgentResponse), ``error``, then always ``done``.
    """
    # Validated before the StreamingResponse is constructed. Once streaming begins the
    # status is committed to 200, so a documented 422 would be unreachable — an
    # unknown persona or sector has to fail here or not at all.
    get_persona(request.persona)
    get_sector(request.sector)

    async def events() -> AsyncIterator[str]:
        # Once the first byte ships, the status code is committed as 200 and every
        # failure has to arrive as an event. An enumerated except list is the wrong
        # shape here: anything it misses ends the stream mid-flight at 200 with no
        # terminal frame, which a client cannot distinguish from a slow answer — the
        # "spinner forever" state the architecture's failure table forbids. The
        # realistic trigger is both providers failing, which re-raises the last error.
        try:
            async for name, payload in run_agent_stream(
                query=request.query,
                persona=request.persona,
                sector=request.sector,
                session_id=request.session_id,
                interface="web",
            ):
                yield f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"
        except (UnknownPersonaError, UnknownSectorError, MCPUnavailableError) as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
        except Exception as exc:
            # Broad on purpose, and deliberately not echoing str(exc): an unknown
            # provider error can carry raw response text. The detail goes to the log
            # under this request id; the client gets a safe, actionable message.
            logger.exception("stream_failed", extra={"error_type": type(exc).__name__})
            detail = json.dumps(
                {"detail": "The agent failed while answering this question. Please retry."}
            )
            yield f"event: error\ndata: {detail}\n\n"
        finally:
            # A terminal frame on every path, so a consumer can tell a finished stream
            # from a dropped connection.
            yield 'event: done\ndata: {}\n\n'

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/personas", response_model=list[PersonaInfo], summary="Persona registry")
async def get_personas() -> list[PersonaInfo]:
    """Drives the UI's persona selector, including each lens's priority fields."""
    return [
        PersonaInfo(
            key=persona.key,
            name=persona.name,
            lens=persona.lens,
            priority_fields=list(persona.priority_fields),
        )
        for persona in PERSONAS.values()
    ]


@app.get("/v1/sectors", response_model=list[SectorInfo], summary="Sector registry")
async def get_sectors() -> list[SectorInfo]:
    """Sectors with company counts and snapshot dates, read over MCP.

    Counts come from the ``dataset_overview`` tool rather than a direct query, so the
    API honours the same protocol boundary as the agent.
    """
    coverage: dict[str, dict[str, Any]] = {}
    try:
        overview = await call_tool("dataset_overview", {})
        parsed = _unwrap(overview)
        for row in parsed.get("sectors", []) if isinstance(parsed, dict) else []:
            if isinstance(row, dict) and row.get("sector"):
                coverage[str(row["sector"])] = row
    except Exception as exc:  # noqa: BLE001 - registry degrades, never 500s
        # Not just MCPUnavailableError: once the tool list is cached, a server that
        # dies afterwards fails inside the transport instead, and /v1/sectors must
        # still answer with the sectors it knows.
        logger.warning("sector_counts_unavailable", extra={"error": str(exc)})

    return [
        SectorInfo(
            key=sector.key,
            label=sector.label,
            description=sector.description,
            company_count=int(coverage.get(sector.key, {}).get("company_count", 0) or 0),
            latest_snapshot=coverage.get(sector.key, {}).get("latest_snapshot"),
        )
        for sector in SECTORS.values()
    ]


# Two paths for one handler. Google's frontend intercepts /healthz on Cloud Run and
# answers 404 before the request reaches the container — verified in production: /docs
# and /v1/sectors arrive and are logged, /healthz never appears in the request log at
# all. /health is the deployed path; /healthz stays for local runs, docker compose and
# anything already pointing at it.
@app.get("/health", response_model=HealthResponse, summary="Liveness and dependencies")
@app.get("/healthz", response_model=HealthResponse, include_in_schema=False)
async def healthz() -> HealthResponse:
    """Report the state of each dependency.

    The database is reported *through* MCP: if the tool answers with a company count
    the data layer is demonstrably reachable, and the API never opens the file
    itself. A green ``db`` here therefore also proves the MCP path works.
    """
    mcp_state = "down"
    db_state = "down"
    try:
        parsed = _unwrap(await call_tool("dataset_overview", {}))
        mcp_state = "up"
        if isinstance(parsed, dict) and int(parsed.get("total_companies", 0)) > 0:
            db_state = "up"
        else:
            db_state = "empty"
    except Exception as exc:  # noqa: BLE001 - health must report, never raise
        # A health endpoint that 500s when a dependency is down tells the operator
        # nothing. Any transport failure means "mcp: down", which is the answer.
        logger.warning("healthz_mcp_down", extra={"error": str(exc)})

    llm_state = "configured" if active_providers() else "unconfigured"
    overall = "ok" if mcp_state == "up" and db_state == "up" and llm_state == "configured" else "degraded"

    return HealthResponse(
        status=overall,
        mcp=mcp_state,
        db=db_state,
        llm=llm_state,
        llm_chain=describe_chain(),
    )


def _unwrap(payload: Any) -> Any:
    """Decode an MCP tool result into plain Python.

    Results arrive as content blocks (``[{"type": "text", "text": "<json>"}]``) or as
    a JSON string, depending on the tool and adapter version.
    """
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and first.get("type") == "text":
            return _unwrap(first.get("text", ""))
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return {}
    return payload
