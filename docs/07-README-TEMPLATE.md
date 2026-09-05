# 07 — README Template (this becomes the repo's README.md)

> Fill the bracketed parts. Keep the order — reviewers skim top-down and decide
> in the first 30 seconds whether to keep reading.

---

# Sector Analyst Agent

One configurable agent. Three financial personas. Three sectors. A real database
behind an MCP boundary, reachable from a web UI and a REST API.

[**Live demo**](…) · [**4-min walkthrough**](…) · [`POST /v1/query` docs](…/docs)

![compare view screenshot](docs/compare.png)
*The same question, the same database rows, three different conclusions.*

---

## What this is

A single agent that switches **persona** (Mutual Fund / Equity / Private Equity
analyst) and **sector** (Tech / Retail / Manufacturing) by parameter — nine valid
combinations. It answers only from a SQLite database of public-company
financials, which it reaches **exclusively through MCP tools**, never by direct
database access. The same `run_agent()` function serves the web UI, a Streamlit
UI, and the REST API.

## Run it in 60 seconds

```bash
git clone <repo> && cd sector-analyst-agent
cp .env.example .env        # add your GOOGLE_API_KEY (free: aistudio.google.com)
docker compose up
```
→ Web UI `http://localhost:3000` · API docs `http://localhost:8000/docs` · MCP `http://localhost:8765/mcp`

The database is committed, so nothing needs scraping. To rebuild it from source:
`python scripts/build_db.py`.

<details><summary>Run without Docker (PowerShell)</summary>

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.mcp_server.server                    # terminal 1
uvicorn app.api.main:app --port 8000               # terminal 2
cd web; npm install; npm run dev                   # terminal 3
streamlit run app/ui_streamlit/app.py              # optional fallback UI
```
</details>

**LLM provider:** Google Gemini 2.5 Flash. Only `GOOGLE_API_KEY` is required —
the free tier is sufficient to run everything here. Langfuse keys are optional;
without them the app runs with tracing disabled.

## Try these

| Persona | Sector | Question |
|---|---|---|
| all three | tech | Is this sector a good place to put money to work right now? |
| mf_analyst | retail | Which of these would fit a long-term core holding versus a name to avoid? |
| equity_analyst | manufacturing | Walk me through the margin profile — who's improving and who's under pressure? |
| pe_analyst | tech | If I had to take one company private, which and what's the operational thesis? |
| any | any | What's the most recent headcount signal you have for NVDA? |
| any | any | What do you think about SpaceX? *(not in the dataset — it should say so)* |

```bash
curl -X POST http://localhost:8000/v1/query -H "Content-Type: application/json" \
  -d '{"query":"Which companies look like attractive buyout targets?",
       "persona":"pe_analyst","sector":"manufacturing"}'
```
<details><summary>Sample response</summary>

```json
{
  "answer": "...",
  "persona": "pe_analyst",
  "persona_lens": "Deal/ops lens — cash flow, leverage capacity, exit potential",
  "sector": "manufacturing",
  "key_points": ["...", "..."],
  "companies_referenced": ["EMR", "ROK", "ITW"],
  "citations": [
    {"ticker":"EMR","company_name":"Emerson Electric",
     "fields_used":["free_cash_flow","debt_to_equity","ev_to_ebitda"],
     "source":"yfinance/yahoo","as_of":"2026-08-12"}
  ],
  "confidence": "high",
  "confidence_reason": "3 companies retrieved, all required fields present, snapshot 23 days old",
  "data_as_of": "2026-08-12",
  "out_of_scope": false,
  "tools_called": ["query_companies","compare_companies"],
  "trace_id": "…",
  "latency_ms": 4180
}
```
</details>

---

## Architecture

```
Next.js UI ─┐                                    ┌─ Langfuse (traces, cost, scores)
            ├─► FastAPI /v1/query ─► run_agent() ─┤
Streamlit ──┘                          │          └─ guardrails: in / grounding / out
                                       │ MCP (streamable-HTTP)
                                       ▼
                            FastMCP server ─► SQLite
```

LangGraph nodes: `validate → guard_input → plan → tools ⟳ → verify_grounding → compose → guard_output`.

### Schema decisions

Three tables: `companies` (identity), `financials` (time-stamped numeric
snapshots), `signals` (dated soft facts like headcount).

- **Financials are split from identity and dated.** Fundamentals are a time
  series; a company's identity is not. Every answer can therefore state *as of
  when* it is true, and re-running the scraper adds history instead of destroying it.
- **`signals` is separate** because headcount and hiring notes are qualitative and
  irregularly dated. This is the table that makes the grounding stress test
  answerable with a real date and source.
- **`source` is a column on every table.** Provenance is data, not documentation.
- **Missing values are NULL, never 0** — and the agent is required to say "not
  available" rather than treat absence as zero. This closes a real hallucination path.
- **The field list was chosen to make persona divergence possible:** growth/beta/yield
  for the fund lens, margins/multiples for the equity lens, FCF/leverage/EV-EBITDA
  for the deal lens.

### MCP design

Five tools: `list_sectors`, `query_companies`, `get_company_detail`,
`get_company_signals`, `compare_companies`.

- **Capability-shaped, not table-shaped.** There is deliberately no `run_sql`
  tool — handing an LLM raw SQL is an injection and correctness hazard. Typed,
  parameterised capabilities are the production pattern.
- **Docstrings are the contract.** The MCP client surfaces them to the model, so
  each states valid values, what an empty result means, and when to prefer that tool.
- **Absence is a typed result**: `{"error": "No data for ticker 'X'"}` rather than
  an exception or an empty dict, so the model has an unambiguous signal to refuse honestly.
- **The boundary is tested, not just claimed** — `tests/test_mcp_tools.py` fails
  the build if anything under `app/agent/` imports the data layer or `sqlite3`.
- Read-only surface, stateless server, runs as its own process/container.

### Persona design

Personas are not tone presets. Each carries a **priority order over fields** and a
**decision framework**, so the same row yields different conclusions:

| Signal | MF Analyst | Equity Analyst | PE Analyst |
|---|---|---|---|
| Low debt/equity | mildly positive | neutral | **strongly positive** — leverage headroom |
| High beta | **negative** — portfolio volatility | secondary | largely irrelevant |
| Weak operating margin | avoid | "under pressure" | **attractive** — an operational lever |
| Dividend yield | matters | modest | irrelevant |

A weak margin is a reason to avoid for one persona and a reason to buy for
another. That divergence is measured, not asserted — see below.

---

## Evaluation

`python evals/run_eval.py` · full report: [`evals/results/report.md`](evals/results/report.md)

| Metric | Result | Target |
|---|---|---|
| Out-of-scope refusal accuracy | [ ]% | 100% |
| Groundedness (Ragas faithfulness) | [ ] | ≥ 0.95 |
| Fact exact-match (retrieval cases) | [ ]% | ≥ 90% |
| Persona divergence score | [ ] | ≥ 0.55 |
| Answers with zero tool calls | 0 | 0 |
| p50 / p95 latency | [ ] / [ ] ms | — |
| Cost per query | ~$[ ] | — |

25 graded cases across cross-persona, persona-specific, grounding, out-of-scope,
adversarial and API-contract categories.

## Guardrails

- **Input:** schema validation before any LLM call, prompt-injection detection,
  off-topic redirect, PII stripping, personalised-advice handling.
- **Grounding gate:** a graph node refuses to compose an answer that made zero
  tool calls. This is how "no hardcoded facts" is enforced mechanically rather
  than by prompt instruction.
- **Output:** ticker and figure cross-checks against retrieved evidence, NULL
  discipline, forced out-of-scope honesty, and confidence recomputed from
  evidence completeness rather than self-reported by the model.

## Observability

Every request is a Langfuse trace: guard spans, each MCP call with arguments and
row counts, both LLM generations with token cost, and eval scores attached. The
`trace_id` is returned in every API response and is clickable in the UI.

## Data quality caveats

- Yahoo fundamentals are trailing-twelve-month and can lag filings by a quarter.
- `debtToEquity` arrives as a percentage and is normalised to a ratio on ingest.
- Some tickers return partial `info`; those fields are stored NULL and reported as unavailable.
- Headcount is annual-report derived and may be up to 12 months old — always shown with `as_of_date`.
- ~30 companies, 10 per sector. Sufficient for reasoning; not a market census.

## Deliberate non-goals

No auth, no real-time pricing, no vector store (the data is structured — SQL over
MCP is the correct tool, embeddings here would be résumé-driven design), no
fine-tuning. **Nothing here is investment advice.**

> **Note on the brief's `sector=logistics` API example:** shipped as a real sector,
> so that request returns 200. An unknown sector returns 422 listing valid values.
>
> *Superseded note:* this build ships tech,
> retail and manufacturing. That request returns **422** with the valid values
> rather than a 500 or an invented answer — deliberate, and covered by a test.

## What I'd improve with more time

[Pick one and go deep — depth beats a list. Suggested:]

**Move from snapshot data to a time-series ingest with scheduled refreshes.**
Today every answer is anchored to one `snapshot_date`, which limits every persona:
the fund lens cannot assess growth *durability* without multi-quarter history, the
equity lens cannot show margin *trajectory*, and the PE lens cannot judge cash-flow
*stability*. The schema already supports this — `financials` is keyed by
`snapshot_date` — so the work is a scheduled ingest job, a `get_company_history`
MCP tool, and trend-aware persona rules. That single change would upgrade the
answers from "here is the current state" to "here is the direction of travel",
which is what all three of these analysts actually get paid to judge.

## Tech

Python 3.12 · LangGraph · FastMCP · Gemini 2.5 Flash · FastAPI · SQLite ·
Next.js 15 + Tailwind · Streamlit · Langfuse · Ragas · Docker
