# 02 — Architecture

Read this fully before writing code. Every design decision below has a reason
attached; the reasons go into the README write-up.

---

## 1. System overview

```
   ┌──────────────────┐        ┌──────────────────┐
   │  Next.js web UI  │        │  Streamlit UI    │      ← two humans doors
   └────────┬─────────┘        └────────┬─────────┘
            │ HTTP/JSON                 │ direct python import
            ▼                           ▼
   ┌─────────────────────────────────────────────────┐
   │            FastAPI  ·  POST /v1/query           │   ← machine door
   └────────────────────────┬────────────────────────┘
                            ▼
   ┌─────────────────────────────────────────────────┐
   │      run_agent(query, persona, sector)          │   ← THE single entry point
   │  ┌───────────────────────────────────────────┐  │
   │  │           LangGraph state machine          │  │
   │  │   validate → guard_in → plan → tools ⟳ →   │  │
   │  │   verify_grounding → compose → guard_out   │  │
   │  └───────────────────────────────────────────┘  │
   └───────────┬──────────────────────────┬──────────┘
               │ MCP (streamable-HTTP)     │ traces
               ▼                           ▼
   ┌───────────────────────────┐   ┌────────────────┐
   │  FastMCP server :8765     │   │   Langfuse     │
   │  list_sectors             │   │  spans, cost,  │
   │  query_companies          │   │  eval scores   │
   │  get_company_detail       │   └────────────────┘
   │  get_company_signals      │
   │  compare_companies        │
   └────────────┬──────────────┘
                ▼
        SQLite  financials.db
```

**The one-sentence version:** two interfaces → one agent function → LangGraph
graph → MCP tool calls → SQLite, with Langfuse watching everything.

---

## 2. Why these choices (put this in the README)

| Decision | Alternative rejected | Reason |
|---|---|---|
| LangGraph over a plain ReAct loop | `create_react_agent`, raw SDK loop | We need *nodes* to hang guardrails and a grounding-verification gate on. A graph makes the control flow inspectable and testable; a loop hides it. |
| MCP over streamable-HTTP | stdio transport | The MCP server runs as its own process/container and is reachable by the API, the UI dev server, and MCP Inspector at the same time. stdio would bind it to one parent process. |
| SQLite over Postgres | Postgres | The brief permits it, the dataset is ~30 companies, and a committed `.db` file means the reviewer needs zero infrastructure and nothing is scraped on first run. (Measured: a *cold* `docker compose build` takes ~15 minutes, almost all of it `pip install`; start-up from a warm image cache is seconds.) |
| Structured SQL over a vector store | Chroma/pgvector + embeddings | The data is numeric and relational. Semantic search over numbers is strictly worse than `WHERE sector = ?`. Choosing the boring correct tool is the signal. |
| Gemini 2.5 Flash | GPT-4o, Claude | Free tier removes any key-sharing friction for the reviewer, supports native structured output, and is fast enough for a live demo. Provider is swappable in one line — documented. |
| Persona as *field-priority + framework*, not tone | Tone-only prompt | The brief explicitly warns against cosmetic tone change. Different weightings over identical rows produce genuinely different conclusions. |

---

## 3. Data model

Three tables. Normalised, provenance-aware, time-aware.

```sql
companies    (ticker PK, name, sector, industry, country, source, last_updated)
financials   (id PK, ticker FK, market_cap, revenue, revenue_growth,
              gross_margin, operating_margin, profit_margin, pe_ratio,
              ev_to_ebitda, debt_to_equity, free_cash_flow, return_on_equity,
              beta, dividend_yield, snapshot_date, source)
signals      (id PK, ticker FK, signal_type, signal_value, numeric_value,
              as_of_date, source)
```

**Schema decisions to defend in the README:**

1. **`financials` is separate from `companies` and keyed by `snapshot_date`.**
   Financials are a time series; identity is not. This lets us re-run the
   scraper without destroying history, and lets every answer state *as of when*
   it is true. A single wide table would make the agent quietly cite stale data.
2. **`signals` is a separate soft-facts table.** Headcount, hiring notes and news
   are qualitative and irregularly dated — they do not belong in a numeric
   snapshot row. This table is what makes the brief's grounding stress test
   answerable with a real date and source.
3. **Every table carries `source`.** Provenance is a first-class column, not a
   README footnote. The agent returns it, so the reviewer can audit any claim.
4. **Missing data is `NULL`, never `0`.** A zero margin and an unknown margin are
   different facts. The agent is instructed to say "not available" for NULLs —
   this is a major hallucination vector if handled lazily.
5. **Fields chosen to serve the three lenses deliberately:**
   - MF lens needs `revenue_growth`, `beta`, `dividend_yield`, `pe_ratio`
   - Equity lens needs `gross/operating/profit_margin`, `pe_ratio`, `roe`
   - PE lens needs `free_cash_flow`, `debt_to_equity`, `ev_to_ebitda`, `market_cap`
   The schema exists to make persona divergence *possible*. That is the point.

**Data-quality caveats to document honestly (reviewers reward this):**
- Yahoo Finance fundamentals are trailing-twelve-month and can lag filings by a quarter.
- `debtToEquity` is reported as a percentage by Yahoo (e.g. `154.0` = 1.54×) — normalise or label it.
- `dividendYield` units changed across yfinance versions; verify and label.
- Some tickers return partial `info` dicts; those fields are stored NULL.
- Headcount is annual-report derived, so it can be up to 12 months old — always shown with `as_of_date`.

---

## 4. MCP design (the part the reviewer is really testing)

### Tool surface

| Tool | Signature | Purpose |
|---|---|---|
| `list_sectors` | `() -> list[str]` | Discovery; lets the agent verify a sector exists |
| `query_companies` | `(sector: str, limit: int = 25) -> list[dict]` | Sector-wide screening/comparison — the workhorse |
| `get_company_detail` | `(ticker: str) -> dict` | One company: profile + latest financials + signals. Returns `{"error": ...}` when absent |
| `get_company_signals` | `(ticker: str, signal_type: str = "") -> list[dict]` | Headcount/hiring/news lookups |
| `compare_companies` | `(tickers: list[str], fields: list[str]) -> list[dict]` | Narrow multi-company pulls without dragging the whole sector into context |

### Design principles

1. **Tools are capability-shaped, not table-shaped.** No `run_sql(query)` tool.
   Exposing raw SQL to an LLM is an injection and correctness disaster; typed,
   parameterised capabilities are how this is done in production.
2. **Docstrings are the contract.** The MCP client shows the docstring to the
   model, so each docstring states valid values, what an empty result means, and
   when to prefer this tool. Prompt engineering lives in the tool description.
3. **Absence is an explicit, typed result.** `get_company_detail` returns
   `{"error": "No data for ticker 'X'"}` rather than raising or returning `{}` —
   the model needs an unambiguous signal to produce an honest refusal.
4. **The server is stateless.** No session, no cursor. Any client can call it.
5. **Read-only.** No write tools exist, so no tool call can corrupt the dataset.

### The boundary is enforced, not just promised

Add this test so the reviewer can see the boundary is real:

```python
# tests/test_mcp_tools.py
def test_agent_package_never_imports_db_directly():
    """The MCP protocol boundary must not be bypassed."""
    for path in Path("app/agent").rglob("*.py"):
        src = path.read_text()
        assert "from app.data" not in src, f"{path} bypasses MCP"
        assert "import sqlite3" not in src, f"{path} bypasses MCP"
```

---

## 5. Agent graph (LangGraph)

### State

```python
class AgentState(TypedDict):
    query: str
    persona: str
    sector: str
    messages: Annotated[list, add_messages]
    tool_calls_made: list[dict]      # audit trail for grounding checks
    retrieved_rows: list[dict]       # everything MCP returned
    guard_flags: list[str]
    retries: int
    final: AgentResponse | None
```

### Nodes and edges

```
  START
    │
    ▼
 [validate]          persona ∈ {3}, sector ∈ {3}? → else 422, no LLM call
    │
    ▼
 [guard_input]       injection / off-topic / PII / advice-seeking
    │  blocked ──────────────────────────────► [refuse] → END
    ▼
 [plan]              LLM + persona prompt + MCP tools bound
    │
    ▼
 [tools] ⟲           execute MCP calls, append to retrieved_rows (max 5 loops)
    │
    ▼
 [verify_grounding]  ── zero tool calls?  ─► back to [plan] with a nudge (max 2 retries)
    │                └─ still zero? ──────► [refuse_ungrounded] → END
    ▼
 [compose]           LLM emits AgentResponse as structured JSON
    │
    ▼
 [guard_output]      numbers-not-in-evidence check, disclaimer, confidence calibration
    │
    ▼
   END
```

**Why the `verify_grounding` node is the most important node in the repo:** it is
the mechanical answer to "no hardcoding facts into prompts". An answer produced
with zero tool calls cannot be grounded, so the graph refuses to ship it. This
converts a policy into an invariant.

---

## 6. Response contract (`app/agent/schemas.py`)

This is what the API returns and what the UI renders. Machine-consumable by design.

```python
class Citation(BaseModel):
    ticker: str
    company_name: str
    fields_used: list[str]          # e.g. ["ev_to_ebitda", "free_cash_flow"]
    source: str                     # e.g. "yfinance/yahoo"
    as_of: str                      # snapshot_date — every claim is dated

class AgentResponse(BaseModel):
    answer: str                     # the persona-framed narrative
    persona: str
    persona_lens: str               # human-readable lens, for the UI header
    sector: str
    key_points: list[str]           # 3-5 scannable takeaways
    companies_referenced: list[str]
    citations: list[Citation]
    confidence: Literal["high", "medium", "low"]
    confidence_reason: str          # WHY this confidence — not a bare number
    data_as_of: str
    out_of_scope: bool = False      # true for the "company not in DB" test
    caveats: list[str] = []
    tools_called: list[str]         # transparency: what it actually queried
    trace_id: str                   # Langfuse trace — clickable in the UI
    latency_ms: int
```

**Confidence is rule-derived, not vibes:**
- `high` — ≥3 companies retrieved, all requested fields non-NULL, snapshot < 90 days old
- `medium` — partial NULLs, or 1–2 companies, or a stale snapshot
- `low` — sparse data, or the question needs data the schema does not hold
- `out_of_scope=true` forces `confidence="high"` on the *refusal* (we are certain we lack the data)

---

## 7. API contract

```
POST /v1/query
{
  "query": "Which companies look like attractive buyout targets?",
  "persona": "pe_analyst",          // mf_analyst | equity_analyst | pe_analyst
  "sector": "manufacturing"          // tech | retail | manufacturing
}
→ 200 AgentResponse
→ 422 {"detail": "Unknown sector 'energy'. Valid: tech, retail, manufacturing, logistics"}
→ 429 rate limited
→ 503 {"detail": "MCP server unreachable"}

GET  /v1/personas   → persona registry with lenses (drives the UI selector)
GET  /v1/sectors    → sectors + company counts + snapshot dates
GET  /healthz       → {"status":"ok","mcp":"up","db":"up","llm":"configured"}
```

Also expose `GET /docs` (auto OpenAPI) and mention it in the README — reviewers
click it.

---

## 8. Failure modes and how each is handled

| Failure | Handling |
|---|---|
| MCP server down | API returns 503 with a clear message; UI shows a "data service unavailable" state, not a spinner forever |
| LLM rate-limited / 5xx | `tenacity` retry, 3 attempts, exponential backoff; then a typed error |
| Model invents a company | `guard_output` cross-checks every ticker in the answer against `retrieved_rows`; unknown ticker → regenerate once, then downgrade to `low` confidence + caveat |
| Model invents a number | Extract numerics from the answer, verify each appears in evidence (within rounding tolerance); flag mismatches as caveats |
| Empty sector result | Agent states no data rather than generalising from memory |
| Prompt injection in the query | Input guard blocks; refusal is logged to Langfuse with the flag |
| Reviewer asks for an unshipped sector | 422 listing valid values. `logistics` IS shipped, so the brief's own example returns 200 |
| NULL field | Rendered as "not available", never as 0 |
