"""Tests for the REST contract.

``run_agent`` and the MCP tool accessor are stubbed: the agent itself is covered by
its own tests and by the smoke test, while what matters here is the HTTP contract —
status codes, error bodies, headers, limits and the response shape a consuming
system depends on. No network, no LLM, no API key required.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.personas import UnknownPersonaError
from app.agent.runner import MCPUnavailableError
from app.agent.schemas import AgentResponse, Citation
from app.agent.sectors import UnknownSectorError
from app.api import main as api_main

SAMPLE = AgentResponse(
    answer="Emerson converts to cash well and carries little debt, which is the case.",
    key_points=["Strong free cash flow", "Low leverage leaves headroom"],
    companies_referenced=["EMR"],
    citations=[
        Citation(
            ticker="EMR",
            company_name="Emerson Electric Co.",
            fields_used=["free_cash_flow", "debt_to_equity"],
            values={"free_cash_flow": 3_710_000_000.0, "debt_to_equity": 0.68},
            source="yfinance/yahoo",
            as_of="2026-09-04",
        )
    ],
    caveats=["This is analysis of the dataset, not personalised investment advice."],
    persona="pe_analyst",
    persona_lens="Deal and operations.",
    sector="manufacturing",
    confidence="high",
    confidence_reason="3 companies retrieved; all requested fields present.",
    data_as_of="2026-09-04",
    tools_called=["query_companies", "compare_companies"],
    llm_provider="google",
    latency_ms=4180,
)

OVERVIEW = {
    "total_companies": 40,
    "latest_snapshot": "2026-09-04",
    "sectors": [
        {"sector": "tech", "company_count": 10, "latest_snapshot": "2026-09-04"},
        {"sector": "retail", "company_count": 10, "latest_snapshot": "2026-09-04"},
        {"sector": "manufacturing", "company_count": 10, "latest_snapshot": "2026-09-04"},
        {"sector": "logistics", "company_count": 10, "latest_snapshot": "2026-09-04"},
    ],
}


@pytest.fixture(autouse=True)
def stub_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the agent and MCP calls; keep the HTTP layer real."""

    async def fake_run_agent(**kwargs: Any) -> AgentResponse:
        from app.agent.personas import get_persona
        from app.agent.sectors import get_sector

        # Validation must still happen, since the 422 contract depends on it.
        get_persona(kwargs["persona"])
        get_sector(kwargs["sector"])
        return SAMPLE

    async def fake_call_tool(name: str, arguments: dict[str, Any]) -> Any:
        if name == "dataset_overview":
            return [{"type": "text", "text": json.dumps(OVERVIEW)}]
        return []

    async def fake_get_tools() -> list[Any]:
        return []

    monkeypatch.setattr(api_main, "run_agent", fake_run_agent)
    monkeypatch.setattr(api_main, "call_tool", fake_call_tool)
    monkeypatch.setattr(api_main, "get_tools", fake_get_tools)
    # Each test gets a fresh window so ordering cannot cause a spurious 429.
    api_main.limiter._hits.clear()


@pytest.fixture
def client() -> AsyncIterator[TestClient]:
    with TestClient(api_main.app) as test_client:
        yield test_client


VALID_BODY = {
    "query": "Which companies look like attractive buyout targets?",
    "persona": "pe_analyst",
    "sector": "manufacturing",
}


# --------------------------------------------------------------------------
# The happy path and the shape a consuming system depends on
# --------------------------------------------------------------------------


def test_query_returns_structured_json_not_a_text_blob(client: TestClient) -> None:
    response = client.post("/v1/query", json=VALID_BODY)
    assert response.status_code == 200
    body = response.json()
    for field in (
        "answer",
        "companies_referenced",
        "citations",
        "confidence",
        "confidence_reason",
        "data_as_of",
        "tools_called",
        "trace_id",
        "latency_ms",
    ):
        assert field in body, f"{field} missing from the response contract"


def test_citations_carry_the_actual_values(client: TestClient) -> None:
    """The brief asks for enough structure to be consumed programmatically."""
    citation = client.post("/v1/query", json=VALID_BODY).json()["citations"][0]
    assert citation["ticker"] == "EMR"
    assert citation["values"]["free_cash_flow"] == 3_710_000_000.0
    assert citation["as_of"] == "2026-09-04"
    assert citation["source"]


def test_confidence_comes_with_its_reason(client: TestClient) -> None:
    body = client.post("/v1/query", json=VALID_BODY).json()
    assert body["confidence"] in {"high", "medium", "low"}
    assert len(body["confidence_reason"]) > 10


def test_request_id_is_returned_for_traceability(client: TestClient) -> None:
    response = client.post("/v1/query", json=VALID_BODY)
    assert response.headers["X-Request-ID"]


def test_supplied_request_id_is_echoed(client: TestClient) -> None:
    response = client.post(
        "/v1/query", json=VALID_BODY, headers={"X-Request-ID": "abc123"}
    )
    assert response.headers["X-Request-ID"] == "abc123"


# --------------------------------------------------------------------------
# Validation — 422 must name the valid values
# --------------------------------------------------------------------------


def test_unknown_sector_is_422_and_lists_valid_sectors(client: TestClient) -> None:
    """The assessment's own API example uses a sector; an unknown one must not 500."""
    response = client.post("/v1/query", json={**VALID_BODY, "sector": "energy"})
    assert response.status_code == 422
    body = response.json()
    assert "energy" in body["detail"]
    assert set(body["valid_sectors"]) == {"tech", "retail", "manufacturing", "logistics"}


def test_logistics_is_a_real_sector_not_a_422(client: TestClient) -> None:
    """We ship the sector the brief's worked example and API test both use."""
    response = client.post("/v1/query", json={**VALID_BODY, "sector": "logistics"})
    assert response.status_code == 200


def test_unknown_persona_is_422_and_lists_valid_personas(client: TestClient) -> None:
    response = client.post("/v1/query", json={**VALID_BODY, "persona": "hedge_fund"})
    assert response.status_code == 422
    assert set(response.json()["valid_personas"]) == {
        "mf_analyst",
        "equity_analyst",
        "pe_analyst",
    }


def test_empty_query_is_rejected(client: TestClient) -> None:
    assert client.post("/v1/query", json={**VALID_BODY, "query": ""}).status_code == 422


def test_unknown_body_field_is_rejected(client: TestClient) -> None:
    """A closed body stops a typo shipping as a silently ignored parameter."""
    response = client.post("/v1/query", json={**VALID_BODY, "persoan": "pe_analyst"})
    assert response.status_code == 422


def test_all_twelve_combinations_are_accepted(client: TestClient) -> None:
    """Persona and sector are independent: every pair is valid."""
    from app.agent.personas import PERSONA_KEYS
    from app.agent.sectors import SECTOR_KEYS

    api_main.limiter.limit = 1_000
    seen = 0
    for persona in PERSONA_KEYS:
        for sector in SECTOR_KEYS:
            response = client.post(
                "/v1/query", json={**VALID_BODY, "persona": persona, "sector": sector}
            )
            assert response.status_code == 200, f"{persona} x {sector}"
            seen += 1
    assert seen == 12
    api_main.limiter.limit = 30


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


def test_mcp_down_is_503_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**_kwargs: Any) -> AgentResponse:
        raise MCPUnavailableError("Could not reach the MCP server at http://x/mcp.")

    monkeypatch.setattr(api_main, "run_agent", boom)
    response = client.post("/v1/query", json=VALID_BODY)
    assert response.status_code == 503
    assert "MCP server" in response.json()["detail"]


def test_rate_limit_returns_429(client: TestClient) -> None:
    api_main.limiter.limit = 3
    api_main.limiter._hits.clear()
    codes = [client.post("/v1/query", json=VALID_BODY).status_code for _ in range(5)]
    api_main.limiter.limit = 30
    assert codes.count(200) == 3
    assert codes.count(429) == 2


def test_rate_limit_does_not_apply_to_registry_endpoints(client: TestClient) -> None:
    """Throttling /healthz would break the very check used to diagnose throttling."""
    api_main.limiter.limit = 1
    api_main.limiter._hits.clear()
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
    api_main.limiter.limit = 30


# --------------------------------------------------------------------------
# Registry and health
# --------------------------------------------------------------------------


def test_personas_registry_drives_the_ui_selector(client: TestClient) -> None:
    body = client.get("/v1/personas").json()
    assert len(body) == 3
    assert {item["key"] for item in body} == {"mf_analyst", "equity_analyst", "pe_analyst"}
    assert all(item["priority_fields"] for item in body)


def test_sectors_registry_reports_counts_from_mcp(client: TestClient) -> None:
    body = client.get("/v1/sectors").json()
    assert len(body) == 4
    counts = {item["key"]: item["company_count"] for item in body}
    assert counts == {"tech": 10, "retail": 10, "manufacturing": 10, "logistics": 10}
    assert all(item["latest_snapshot"] == "2026-09-04" for item in body)


def test_healthz_reports_each_dependency(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["mcp"] == "up"
    assert body["db"] == "up"
    assert body["llm"] in {"configured", "unconfigured"}
    assert "llm_chain" in body


def test_healthz_is_degraded_not_broken_when_mcp_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator must be able to see what is wrong, so /healthz still answers."""

    async def boom(name: str, arguments: dict[str, Any]) -> Any:
        raise MCPUnavailableError("down")

    monkeypatch.setattr(api_main, "call_tool", boom)
    body = client.get("/healthz").json()
    assert body["status"] == "degraded"
    assert body["mcp"] == "down"


def test_openapi_documents_the_contract(client: TestClient) -> None:
    """Reviewers click /docs; the schema must actually describe the response."""
    schema = client.get("/openapi.json").json()
    assert "/v1/query" in schema["paths"]
    assert "AgentResponse" in schema["components"]["schemas"]
    assert "Citation" in schema["components"]["schemas"]


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------


def test_stream_emits_evidence_before_the_final_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI's central claim: retrieval is visible before the answer arrives."""

    async def fake_stream(**_kwargs: Any) -> AsyncIterator[tuple[str, Any]]:
        yield "progress", {"node": "guard_input"}
        yield "evidence", {"rows": [{"ticker": "EMR"}], "tool_calls": []}
        yield "response", SAMPLE.model_dump()

    monkeypatch.setattr(api_main, "run_agent_stream", fake_stream)
    with client.stream("POST", "/v1/query/stream", json=VALID_BODY) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())

    assert text.index("event: evidence") < text.index("event: response")
    assert "EMR" in text


def test_stream_reports_a_bad_sector_as_an_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the stream has begun there is no status code left to use."""

    async def fake_stream(**_kwargs: Any) -> AsyncIterator[tuple[str, Any]]:
        raise UnknownSectorError("Unknown sector 'energy'. Valid: tech")
        yield  # pragma: no cover - unreachable, defines this as a generator

    monkeypatch.setattr(api_main, "run_agent_stream", fake_stream)
    with client.stream("POST", "/v1/query/stream", json=VALID_BODY) as response:
        text = "".join(response.iter_text())
    assert "event: error" in text
    assert "energy" in text


def test_unknown_persona_error_type_maps_to_422(client: TestClient) -> None:
    """Guard against the handler being registered for the wrong exception class."""
    assert UnknownPersonaError in api_main.app.exception_handlers
    assert UnknownSectorError in api_main.app.exception_handlers
    assert MCPUnavailableError in api_main.app.exception_handlers


def test_log_lines_carry_the_request_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression: resetting the ContextVar before logging stripped the id.

    request_id is the whole point of the middleware — a user-reported problem has to
    be findable in the logs — so a line without one is a silent loss of traceability.
    """
    import logging

    from app.logging_conf import request_id_var

    seen: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage() == "http_request":
                seen.append(request_id_var.get())

    handler = Capture()
    logging.getLogger("app.api.main").addHandler(handler)
    try:
        client.post("/v1/query", json=VALID_BODY, headers={"X-Request-ID": "trace-me"})
    finally:
        logging.getLogger("app.api.main").removeHandler(handler)

    assert seen and seen[0] == "trace-me"


# --------------------------------------------------------------------------
# Regressions found by the Phase 5 adversarial review
# --------------------------------------------------------------------------


def test_stream_emits_an_error_event_for_any_exception(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enumerated except list left unknown failures as a 200 with no terminal frame.

    Both providers failing re-raises the last provider's error, which is not one of
    the three typed exceptions — the stream ended mid-flight and the UI span forever.
    """

    async def exploding_stream(**_kwargs: Any) -> AsyncIterator[tuple[str, Any]]:
        yield "progress", {"node": "guard_input"}
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(api_main, "run_agent_stream", exploding_stream)
    with client.stream("POST", "/v1/query/stream", json=VALID_BODY) as response:
        text = "".join(response.iter_text())

    assert "event: error" in text
    # The raw provider message must not be echoed to the client.
    assert "RESOURCE_EXHAUSTED" not in text
    assert "event: done" in text


def test_stream_always_ends_with_a_terminal_frame(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fine(**_kwargs: Any) -> AsyncIterator[tuple[str, Any]]:
        yield "response", SAMPLE.model_dump()

    monkeypatch.setattr(api_main, "run_agent_stream", fine)
    with client.stream("POST", "/v1/query/stream", json=VALID_BODY) as response:
        text = "".join(response.iter_text())
    assert text.rstrip().endswith("event: done\ndata: {}")


def test_stream_rejects_an_unknown_sector_with_422_before_streaming(
    client: TestClient,
) -> None:
    """The documented 422 has to happen before the status is committed to 200."""
    response = client.post("/v1/query/stream", json={**VALID_BODY, "sector": "energy"})
    assert response.status_code == 422
    assert "energy" in response.json()["detail"]


def test_rate_limited_response_still_carries_the_request_id(client: TestClient) -> None:
    """Middleware order: a 429 must not skip the request-context middleware."""
    api_main.limiter.limit = 1
    api_main.limiter._hits.clear()
    client.post("/v1/query", json=VALID_BODY)
    limited = client.post("/v1/query", json=VALID_BODY, headers={"X-Request-ID": "rl-1"})
    api_main.limiter.limit = 30
    assert limited.status_code == 429
    assert limited.headers["X-Request-ID"] == "rl-1"


def test_healthz_survives_mcp_dying_after_startup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once tools are cached, a dead server raises a transport error, not our type."""

    async def transport_died(name: str, arguments: dict[str, Any]) -> Any:
        raise ConnectionError("peer closed connection")

    monkeypatch.setattr(api_main, "call_tool", transport_died)
    body = client.get("/healthz").json()
    assert body["status"] == "degraded"
    assert body["mcp"] == "down"


def test_sectors_registry_survives_mcp_dying_after_startup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def transport_died(name: str, arguments: dict[str, Any]) -> Any:
        raise ConnectionError("peer closed connection")

    monkeypatch.setattr(api_main, "call_tool", transport_died)
    response = client.get("/v1/sectors")
    assert response.status_code == 200
    assert len(response.json()) == 4
