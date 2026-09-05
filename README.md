# Sector Analyst Agent

One configurable agent. Three financial analyst personas. Four sectors. A real
database behind an **MCP protocol boundary**, reachable from a web UI and a REST API.

The same `run_agent()` function serves both interfaces. The agent never touches the
database — it is an MCP *client*, and a test fails the build if that stops being true.

```
Streamlit UI ─┐                                   ┌─ guardrails: input / grounding / output
              ├─► run_agent() ─► LangGraph ───────┤
REST API ─────┘                      │            └─ Langfuse (optional)
                                     │ MCP (streamable-HTTP)
                                     ▼
                            FastMCP server ─► SQLite
```

---

## Run it

```powershell
copy .env.example .env    # add GOOGLE_API_KEY (free: aistudio.google.com)
docker compose up
```

| | |
|---|---|
| Streamlit UI | http://localhost:8501 |
| API docs | http://localhost:8000/docs |
| MCP endpoint | http://localhost:8765/mcp |
| Health | http://localhost:8000/healthz |

The database is committed, so nothing is scraped on first run. Only `GOOGLE_API_KEY`
is required. Langfuse keys are optional — without them the app runs with tracing
silently disabled, which is a supported configuration and is tested.

<details><summary>Without Docker (PowerShell)</summary>

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m app.mcp_server.server                      # terminal 1
uvicorn app.api.main:app --port 8000                 # terminal 2
streamlit run app/ui_streamlit/app.py                # terminal 3
```

Rebuild the database from source: `python scripts/build_db.py`
</details>

---

## Try these

| Persona | Sector | Question |
|---|---|---|
| all three | tech | Is this sector a good place to put money to work right now? |
| mf_analyst | retail | Which would fit a long-term core holding versus a name to avoid? |
| equity_analyst | manufacturing | Walk me through the margin profile — who's improving and who's under pressure? |
| pe_analyst | tech | If I had to take one company private, which and what's the operational thesis? |
| any | any | What's the most recent headcount signal you have for NVDA? |
| any | any | What do you think about SpaceX? *(not in the dataset — it says so)* |

```powershell
curl.exe -X POST http://localhost:8000/v1/query `
  -H "Content-Type: application/json" `
  -d '{\"query\":\"Which companies look like attractive buyout targets?\",\"persona\":\"pe_analyst\",\"sector\":\"logistics\"}'
```

**On the brief's `sector=logistics` example:** Logistics is a *shipped sector here*, so
that request returns 200. The brief's worked example and its API test both use it, so
shipping four sectors rather than three means every question in the brief runs
verbatim. An unknown sector returns 422 naming the valid values:

```json
{"detail": "Unknown sector 'energy'. Valid: tech, retail, manufacturing, logistics",
 "valid_sectors": ["tech", "retail", "manufacturing", "logistics"]}
```

---

## Design write-up

### Schema decisions

Three tables: `companies` (identity), `financials` (dated numeric snapshots),
`signals` (dated soft facts).

- **Financials are split from identity and dated.** Fundamentals are a time series;
  a company's identity is not. Every answer can therefore state *as of when* it is
  true, and re-running the scraper appends history rather than destroying it.
  `UNIQUE(ticker, snapshot_date)` makes the ingest idempotent per day.
- **`signals` is separate** because headcount and hiring notes are qualitative and
  irregularly dated. A `COALESCE(as_of_date,'')` unique index is used rather than a
  plain `UNIQUE`, because SQLite treats NULLs as distinct and an undated signal would
  otherwise duplicate on every re-run.
- **Provenance is a column, not a footnote.** Every table carries `source`.
- **Missing data is NULL, never 0.** A zero margin and an unknown margin are different
  facts. The agent is required to say "not available in the dataset", and the UI
  renders NULL as an em dash.
- **Fields were chosen to make persona divergence possible** — growth/beta/yield for
  the fund lens, margins/returns/multiples for the equity lens, FCF/leverage/EV-EBITDA
  for the deal lens.

### MCP design

Seven tools: `list_sectors`, `dataset_overview`, `query_companies`,
`search_companies`, `get_company_detail`, `get_company_signals`, `compare_companies`.

- **Capability-shaped, not table-shaped.** There is deliberately no `run_sql` tool —
  handing an LLM raw SQL is an injection and correctness hazard.
- **`compare_companies(fields=...)` is allowlisted.** Those names become SQL column
  identifiers, and identifiers cannot be bound as parameters, so every requested field
  is checked against a frozen set. Four injection payloads are tested against it.
- **Absence is a typed result.** `get_company_detail` returns
  `{"error": "No data for ticker 'X'"}`, and `search_companies` returns `[]` — an
  authoritative "not in the dataset" that the agent can *retrieve* rather than infer.
  That makes an out-of-scope refusal grounded in a real tool call.
- **Docstring ordering is load-bearing.** FastMCP builds a tool's description from the
  docstring up to its `Args:` block and silently drops the rest, so every statement
  about what a result *means* sits above that block. Tests assert each tool's
  registered description still carries its absence semantics, because this fails
  silently.
- **The boundary is enforced, not promised.** `tests/test_mcp_tools.py` walks the AST
  of every file under `app/agent`, `app/api` and `app/ui_streamlit` and fails if any
  imports the data layer or a SQL driver. A companion test proves the check itself can
  fail, so it cannot rot into a no-op.

### Persona design

Personas are not tone presets. The difference lives in structured data — a
signal-by-persona table of *directional verdicts* — and the system prompt is a
rendering of that table, so the prompt cannot drift from what the eval measures.

| Signal | MF Analyst | Equity Analyst | PE Analyst |
|---|---|---|---|
| Weak operating margin | negative | negative | **positive** — an operational lever |
| High revenue growth | positive | positive | **negative** — priced in, raises entry multiple |
| High dividend yield | positive | neutral | **negative** — cash that should service debt |
| High beta | negative | neutral | ignored — not marked to market |

A test asserts at least three signals are POSITIVE for one lens and NEGATIVE for
another, so "the personas diverge" fails the build if it stops being true.

The fund lens is defined as benchmark-relative, but the dataset holds no index data.
Rather than let it invent one, its rules require it to construct the comparison from
the retrieved peer set and *say* that the peer group is its benchmark proxy.

---

## Guardrails

| Tier | Check |
|---|---|
| Input | injection patterns, length, off-topic redirect, PII redaction, advice-seeking |
| Graph | `verify_grounding` — an answer with zero tool calls is refused, not shipped |
| Output | ticker fabrication, number fabrication, NULL discipline, not-advice caveat |

Two decisions worth naming:

**Confidence is computed, never self-reported.** It is derived from evidence
completeness — company count, NULL fields, snapshot age — and overwrites whatever the
model claimed. Models are poorly calibrated about their own certainty; the data knows
exactly how complete it is. The response model the LLM fills has no `confidence` field
at all, so it cannot talk itself into a high score.

**PII is redacted before it travels, not just before it is logged.** The redacted
query is what reaches the LLM provider and the tracing backend.

The number-fabrication check took four rounds of fixes to stop punishing correct
answers. It now understands that a value can be restated as a percentage, scaled to
billions, or **derived** as a difference between two fields of the same company; that
`15-16x` is a range and not the number `-16`; and that SEC accession numbers, ISO
dates and URLs are dense with digits but contain no claims. A test asserts the
loosened check still catches a genuine invention.

---

## Evaluation

`python evals/run_eval.py` runs 27 graded cases and writes
[`evals/results/report.md`](evals/results/report.md).

| Metric | Target | Actual |
|---|---|---|
| Out-of-scope refusal accuracy | 100% | **100%** |
| Groundedness (figures traceable to evidence) | ≥ 0.95 | **1.000** |
| Persona divergence | ≥ 0.55 | **0.816** |
| Zero-tool-call answers | 0 | **0** |

**Groundedness is deterministic, not LLM-judged.** Every figure in an answer must
trace to a retrieved value, checked with the same code the output guardrail runs in
production. Ragas `faithfulness` was considered and rejected as the gate: it needs a
judge model (Ragas defaults to OpenAI, which would break the promise that only
`GOOGLE_API_KEY` is required), and an LLM-judged score is not reproducible enough to
gate a build.

**Divergence is weighted toward conclusions, not vocabulary.** The obvious formulation
— mostly lexical overlap plus lens-keyword recall — is circular, because the lens
keywords are the same words the persona prompts inject. It scores highest on exactly
the cosmetic tone change the brief says does not count. Here it is
`0.6·conclusions + 0.2·lexical + 0.2·keywords`, and a test asserts that three answers
stuffed with distinct lens vocabulary but picking the *same* companies score **below**
the gate.

Conclusions are measured on the companies each lens *leads with*, not on everything it
mentions. Observed: asked whether tech is a good place to deploy capital, all three
lenses named all ten companies (membership divergence 0.27) while the buyout lens led
with ADBE/META/CRM and the others led with NVDA/GOOGL/MSFT (lead divergence 0.83).
Both numbers are in the report so the choice is auditable.

**The verdict probe** is the un-gameable version of the headline claim: the same
weak-margin company put to the fund lens and the buyout lens. They must reach opposite
conclusions from an identical row. They do:

> **MF:** "its weak operating margin is a clear reason to **avoid** it"
> **PE:** "this is a significant **opportunity** rather than a defect"

---

## Data and its limits

40 companies across four sectors, from two public sources.

- **Yahoo Finance (`yfinance`)** — the numeric snapshot, trailing-twelve-month, which
  can lag filings by a quarter.
- **SEC EDGAR** — the *date* on every headcount signal. Yahoo's `fullTimeEmployees` is
  a bare integer with no as-of date, so dating it with the scrape date would invent
  provenance in the exact field the brief uses to catch invention. Each figure is
  dated to the period end of the company's most recent Form 10-K and cited to that
  filing. When EDGAR cannot be reached the date is stored NULL with a basis line
  saying so — an undated fact is reported as undated.

Known caveats:

- `debtToEquity` is reported by Yahoo as a percentage and is divided by 100 on ingest.
- `dividendYield` is reported as a **percentage** (`1.96` = 1.96%), while
  `trailingAnnualDividendYield` is a **fraction**. Disambiguating by magnitude is
  wrong: a "divide by 100 only if the value exceeds 1" rule silently stores every
  sub-1% yield 100× too high. Both fields' units are handled explicitly.
- Some tickers return partial `info` dicts; those fields are stored NULL.
- The committed snapshot is dated. Confidence degrades with snapshot age by design, so
  answers become less confident over time rather than silently stale.
- Eval `expected_facts` are pinned to the committed database. Re-running
  `scripts/build_db.py` pulls fresh market data and will invalidate them.

---

## LLM provider

**Google Gemini 2.5 Flash**, with **Groq (`openai/gpt-oss-120b`) as a transparent
fallback**. The free Gemini tier is per-minute rate limited, and rate limits and
resolver blips were the single largest source of failure while building this.

Failover is at the **model layer**, not the graph layer: `with_fallbacks` wraps the
chat model, so a mid-conversation failure does not discard completed tool calls or
re-run MCP queries. Structured output is configured **per provider** — Groq's default
method selection fails on `gpt-oss-120b` with "Tool choice is required" while
`json_schema` works, so wrapping the whole chain once would have applied one provider's
strategy to both and the fallback would have broken at the exact moment it was needed.
`GET /healthz` reports the active chain.

Set only `GOOGLE_API_KEY` and the fallback is simply absent; nothing breaks.

---

## What I'd do next

**Time-series ingest.** The schema already supports it — `financials` is keyed by
`snapshot_date` and re-running the scraper appends rather than overwrites. Today every
persona reasons about a single point in time, which is the sharpest limitation here:
the equity lens is asked "who's improving and who's under pressure?" and can only
infer trajectory from a static margin. With two or three snapshots it could answer
that question directly, and the PE lens could underwrite a trend rather than a level.

Also next: cut prompt cost by projecting only the active persona's `priority_fields`
into the compose step (the tool for it, `compare_companies`, already exists), and add
a `/compare` view that runs one retrieval and fans out to three personas — which would
both perform the headline claim on screen and remove the worst rate-limit exposure.

## Non-goals

- No auth or multi-tenancy — single-user assessment scope.
- No real-time market data; every answer states its `snapshot_date`.
- No vector store. The data is numeric and relational, so SQL over MCP is the correct
  tool; embeddings here would be résumé-driven design.
- No fine-tuning — persona differentiation is prompt architecture and field
  prioritisation.
- **This is not investment advice**, and an output guardrail enforces that on every
  answer.

## Repository

```
app/
  config.py            pydantic-settings; one place a key is named
  logging_conf.py      structured JSON logs with a request-id ContextVar
  data/                schema.sql, db.py (all SQL), financials.db (committed)
  mcp_server/          FastMCP server — the only thing that touches SQLite
  agent/               personas, sectors, prompts, guardrails, graph, runner, llm
  api/                 FastAPI: /v1/query, /v1/query/stream (SSE), registries, health
  ui_streamlit/        the human interface, calling run_agent in-process
scripts/               build_db.py, smoke_test.py
evals/                 dataset.jsonl, run_eval.py, results/report.md
tests/                 263 tests, no network, no API key required
```

Run the checks: `pytest -q` and `ruff check .`
