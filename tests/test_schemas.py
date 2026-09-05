"""Tests that the prompt contract and the parsed contract cannot drift apart."""

from __future__ import annotations

from app.agent.prompts import OUTPUT_CONTRACT_FIELDS, render_output_contract
from app.agent.schemas import AgentResponse, AnalystDraft, Citation

MODEL_OWNED = {name for name, _ in OUTPUT_CONTRACT_FIELDS}
RUNNER_OWNED = {
    "persona",
    "persona_lens",
    "sector",
    "confidence",
    "data_as_of",
    "tools_called",
    "tool_calls",
    "guard_flags",
    "llm_provider",
    "trace_id",
    "latency_ms",
}


def test_draft_matches_the_prompt_contract_exactly() -> None:
    """What we ask the model for and what we parse back must be the same field set."""
    assert set(AnalystDraft.model_fields) == MODEL_OWNED


def test_response_is_the_draft_plus_execution_facts() -> None:
    response_fields = set(AgentResponse.model_fields)
    # confidence_reason is recomputed by the guard, so it appears on both sides.
    assert MODEL_OWNED - response_fields == set()
    assert response_fields >= RUNNER_OWNED


def test_model_cannot_supply_execution_metadata() -> None:
    """Fields the model could only guess at must not be askable."""
    for field in ("trace_id", "latency_ms", "tools_called", "confidence", "llm_provider"):
        assert field not in AnalystDraft.model_fields
        assert field not in MODEL_OWNED


def test_rendered_contract_lists_every_field() -> None:
    rendered = render_output_contract()
    for name in MODEL_OWNED:
        assert name in rendered


def test_trace_id_is_optional_because_langfuse_is_optional() -> None:
    """The app must run with no Langfuse keys, so trace_id has to be nullable."""
    assert AgentResponse.model_fields["trace_id"].default is None
    response = AgentResponse(
        answer="x", persona="pe_analyst", persona_lens="deal", sector="tech",
        confidence="low", confidence_reason="none",
    )
    assert response.trace_id is None
    assert response.data_as_of is None


def test_citation_carries_values_not_just_field_names() -> None:
    """Without values the evidence panel and the groundedness scorer have no input."""
    citation = Citation(
        ticker="EMR",
        company_name="Emerson Electric Co.",
        fields_used=["ev_to_ebitda", "operating_margin"],
        values={"ev_to_ebitda": 15.75, "operating_margin": None},
    )
    assert citation.values["ev_to_ebitda"] == 15.75
    # A null in the data must survive as null, never as zero.
    assert citation.values["operating_margin"] is None


def test_response_rejects_unknown_fields() -> None:
    """The wire contract is closed, so a typo cannot silently ship."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentResponse(
            answer="x", persona="pe_analyst", persona_lens="deal", sector="tech",
            confidence="low", confidence_reason="none", made_up_field=1,
        )
