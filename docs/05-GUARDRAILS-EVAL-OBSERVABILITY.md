# 05 — Guardrails, Evaluation & Observability

This is the layer that separates a demo from a system. Most take-home submissions
have none of it.

---

## 1. Guardrails

Three tiers. Cheap deterministic checks first, LLM checks only when needed.

### Tier 1 — Input guards (`check_input`, runs before any LLM call)

| Guard | Rule | On trigger |
|---|---|---|
| Schema | persona ∈ 3, sector ∈ 3 | 422, zero LLM cost |
| Length | 3 ≤ len(query) ≤ 2000 chars | 422 |
| Prompt injection | Regex + keyword set: "ignore previous", "system prompt", "you are now", "reveal your instructions", "disregard", role-marker strings | Refuse with a neutral message; log flag `injection` |
| Off-topic | Query has no financial intent and no ticker/company token | Polite redirect naming what the agent covers |
| PII | Email / phone / card-number patterns in the query | Strip before logging; never store raw |
| Personalised-advice seeking | "should I buy", "how much should I invest", "is this a good stock for my portfolio" | Answer analytically **and** attach the not-advice caveat — do not refuse outright, the brief expects analysis |

Injection defence is layered, not regex-only: the system prompt states that
message content is untrusted data, and the MCP tools take typed parameters, so
there is no raw-SQL surface to inject into. Say this in the README.

### Tier 2 — Grounding gate (`verify_grounding`, a graph node)

```python
if len(state["tool_calls_made"]) == 0 and state["retries"] < 2:
    return "plan"           # nudge: "You must query the database first."
if len(state["tool_calls_made"]) == 0:
    return "refuse_ungrounded"
return "compose"
```

This is the mechanical enforcement of "no hardcoded facts". Point at it in the
write-up.

### Tier 3 — Output guards (`check_output`)

| Guard | Implementation | On failure |
|---|---|---|
| Ticker fabrication | Every ticker mentioned must exist in `retrieved_rows` | Regenerate once; then strip the claim, add caveat, drop confidence to `low` |
| Number fabrication | Extract numerics from the answer; each must match an evidence value within 1% (or be a derived ratio the answer labels as computed) | Add caveat naming the unverifiable figure |
| Out-of-scope honesty | If the query names an entity absent from the DB, the answer must contain an explicit no-data statement and `out_of_scope=true` | Force the refusal template |
| NULL discipline | Answer must not state a value for a field that is NULL in evidence | Replace with "not available in the dataset" |
| Advice disclaimer | Always appended | — |
| Confidence calibration | Recompute from evidence completeness; overwrite whatever the model claimed | — |

**Design note worth stating:** confidence is computed from the evidence, not
self-reported by the LLM. Models are poorly calibrated at self-assessment; the
data knows how complete it is.

---

## 2. Evaluation suite

`evals/dataset.jsonl` — ~25 cases. Each: `id`, `query`, `persona`, `sector`,
`category`, `expected_behaviour`, optional `expected_facts`.

Categories and counts:

| Category | n | What it proves |
|---|---|---|
| `cross_persona` | 3 (same question, 3 personas) | The brief's headline requirement |
| `persona_specific` | 6 | The four named sample queries + 2 more |
| `grounding` | 5 | Exact-figure retrieval (headcount, margins, multiples) |
| `out_of_scope` | 4 | Companies absent from the DB — must refuse |
| `adversarial` | 4 | Injection, advice-seeking, off-topic, empty-sector |
| `api_contract` | 3 | Response shape, 422 on bad sector, all nine combos |

### Scorers

1. **Groundedness** (Ragas `faithfulness`) — is every claim supported by the
   retrieved rows? Target ≥ 0.95.
2. **Refusal accuracy** (custom, deterministic) — for `out_of_scope`, the answer
   must set `out_of_scope=true` and contain no figures. **Target 100%. Non-negotiable.**
3. **Fact exact-match** (custom) — for `grounding` cases, the expected figure must
   appear. Target ≥ 0.9.
4. **Persona divergence** (custom, the interesting one):
   ```
   For a fixed (query, sector), run all 3 personas.
   a) lexical divergence  = 1 − mean pairwise token overlap (stopwords removed)
   b) lens-keyword recall = fraction of each persona's expected lens terms present
      MF:     benchmark, index, durable, portfolio, volatility, core holding
      Equity: margin, earnings, multiple, valuation, pricing power
      PE:     leverage, EBITDA, entry multiple, exit, cash flow, operational
   c) conclusion divergence = do the personas pick different top companies?
   score = 0.4·a + 0.4·b + 0.2·c        target ≥ 0.55
   ```
   This turns "the personas are meaningfully different" from a claim into a
   number. Print the matrix in the README.
5. **Zero-tool-call rate** — must be 0.
6. **Latency p50/p95** and **cost per query** — pulled from Langfuse.

`evals/run_eval.py` runs the set, pushes scores to Langfuse as `scores` attached
to traces, and writes `evals/results/report.md` with a results table.

**Commit the report.** A take-home with its own eval report is memorable.

---

## 3. Observability (Langfuse)

### What to instrument

- One **trace per request**, named `sector-analyst-query`.
- Trace metadata: `persona`, `sector`, `session_id`, `interface` (`web`|`api`|`streamlit`),
  `request_id`.
- **Spans:** `guard_input`, `plan` (LLM), each MCP tool call (with args and row
  count), `verify_grounding`, `compose` (LLM), `guard_output`.
- **Generations:** model, prompt, completion, input/output tokens, cost — Langfuse
  computes cost automatically once the model name is set.
- **Scores** attached to traces: eval scores from the suite, plus a thumbs
  up/down from the UI written back via the Langfuse API.
- `trace_id` returned in `AgentResponse` and rendered as a clickable link in the
  UI. A reviewer clicking from an answer straight into its trace is a strong moment.

### Wiring

Use the Langfuse callback handler on the LangGraph invocation:

```python
from langfuse.callback import CallbackHandler
handler = CallbackHandler(
    session_id=session_id,
    metadata={"persona": persona, "sector": sector, "interface": interface},
)
result = await graph.ainvoke(state, config={"callbacks": [handler]})
```

If Langfuse keys are absent, the handler must be skipped silently — the app still
runs for a reviewer who does not create a Langfuse account. **Test this path.**

### Structured logging

Alongside tracing, `logging_conf.py` emits JSON lines: `timestamp`, `level`,
`request_id`, `persona`, `sector`, `event`, `duration_ms`, `guard_flags`. Never
log the raw query at INFO if PII was detected.

### Self-hosting option

`docker-compose.langfuse.yml` for a fully local Langfuse (Postgres + ClickHouse).
Mention it in the README for reviewers who cannot use a cloud service — a small
detail that reads as production awareness.
