"""The response contract.

Split deliberately in two:

``AnalystDraft`` is what the **model** emits. It contains only things the model can
actually know from its own reasoning over retrieved rows.

``AgentResponse`` is what the **API** returns. It is the draft plus execution facts —
which tools ran, how long it took, which provider served it, the trace id, and a
confidence recomputed from the evidence rather than self-reported.

The architecture doc describes a single ``AgentResponse`` emitted by the compose
step, but a model cannot know its own trace id or latency, and asking it to fill
those fields invites it to invent them. Worse, ``confidence`` is overwritten by the
output guard anyway, so a model-supplied value is dead weight that costs tokens and
implies a self-assessment we then discard. Keeping the two shapes separate makes the
boundary between "reasoned" and "observed" explicit.

``tests/test_schemas.py`` asserts ``AnalystDraft`` matches ``OUTPUT_CONTRACT_FIELDS``
in ``prompts.py``, so the prompt and the parser cannot drift apart.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]


class Citation(BaseModel):
    """Evidence for the claims made about one company.

    ``values`` carries the retrieved figures themselves, not just the field names.
    Without them the UI's evidence panel has nothing to render and a groundedness
    scorer has no context to score an answer against — the citation would assert that
    evidence exists without ever showing it.
    """

    model_config = ConfigDict(extra="forbid")

    ticker: str
    company_name: str
    fields_used: list[str] = Field(
        default_factory=list,
        description="Columns that drove the conclusion for this company.",
    )
    values: dict[str, float | str | None] = Field(
        default_factory=dict,
        description="The exact retrieved value per field. None where the data is null.",
    )
    source: str = ""
    as_of: str = ""


class AnalystDraft(BaseModel):
    """What the LLM returns from the compose step.

    Mirrors ``prompts.OUTPUT_CONTRACT_FIELDS`` exactly.
    """

    model_config = ConfigDict(extra="ignore")

    answer: str
    key_points: list[str] = Field(default_factory=list)
    companies_referenced: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confidence_reason: str = ""
    caveats: list[str] = Field(default_factory=list)
    out_of_scope: bool = False


class ToolCallRecord(BaseModel):
    """One MCP call, recorded for the audit trail and the UI's tool trace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    row_count: int = 0
    error: str | None = None
    tool_call_id: str = Field(
        default="",
        description="Correlates the record to its ToolMessage so repeated tool loops "
        "record each call exactly once.",
    )


class AgentResponse(BaseModel):
    """The full contract returned by the API and both UIs."""

    model_config = ConfigDict(extra="forbid")

    # --- from the model ---
    answer: str
    key_points: list[str] = Field(default_factory=list)
    companies_referenced: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    out_of_scope: bool = False

    # --- assembled by the runner ---
    persona: str
    persona_lens: str
    sector: str
    confidence: Confidence
    confidence_reason: str
    data_as_of: str | None = Field(
        default=None,
        description="Snapshot date of the evidence. Null when nothing was retrieved.",
    )
    tools_called: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    guard_flags: list[str] = Field(default_factory=list)
    llm_provider: str = Field(
        default="",
        description="Provider that actually served this request, after any failover.",
    )
    trace_id: str | None = Field(
        default=None,
        description="Langfuse trace id. Null when tracing is disabled, which is a "
        "supported configuration — the app must run with no Langfuse keys.",
    )
    latency_ms: int = 0
