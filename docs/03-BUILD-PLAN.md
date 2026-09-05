# 03 — Build Plan

Eight phases. Each has: goal, files to create, exact commands (PowerShell), and a
**verification gate** that must pass before moving on.

Realistic total time with Claude Code: **12–16 focused hours**, spread over 2–3 days.

---

## Phase 0 — Environment & skeleton  ·  ~30 min

**Goal:** a running virtualenv, config loading, and the folder tree from `CLAUDE.md`.

1. Create the project and virtualenv:
   ```powershell
   mkdir sector-analyst-agent; cd sector-analyst-agent
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
   *If activation is blocked:* `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then retry.

2. Create `requirements.txt` (versions in `CLAUDE.md`), then:
   ```powershell
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. Create `.env.example`, copy it to `.env`, paste your real keys into `.env` only.
   - Gemini key: `aistudio.google.com` → Get API key (free, no card).
   - Langfuse keys: `cloud.langfuse.com` → new project → API keys (free tier).

4. Create `.gitignore` containing at minimum: `.env`, `venv/`, `__pycache__/`,
   `*.pyc`, `node_modules/`, `.next/`, `evals/results/*.json`.

5. Create `app/config.py` with `pydantic-settings`.

**Verify:**
```powershell
python -c "from app.config import settings; print(settings.llm_model, bool(settings.google_api_key))"
# expect: gemini-2.5-flash True
```

---

## Phase 1 — Data layer  ·  ~2 h

**Goal:** a real database of ~30 companies across three sectors, from public sources.

Files: `app/data/schema.sql`, `app/data/db.py`, `scripts/build_db.py`, `tests/test_db.py`.

Tickers (10 per sector, all large public companies with good Yahoo coverage):
- **tech:** AAPL, MSFT, NVDA, GOOGL, META, CRM, ADBE, ORCL, AMD, INTC
- **retail:** WMT, COST, TGT, HD, LOW, TJX, DG, KR, BBY, ROST
- **manufacturing:** CAT, GE, HON, DE, MMM, EMR, ITW, PH, ETN, ROK

`db.py` must expose: `init_db`, `list_sectors`, `query_companies`,
`get_company_detail`, `get_company_signals`, `compare_companies`. All SQL is
parameterised. All functions return plain dicts.

`build_db.py` must: create tables, pull each ticker via `yfinance`, store NULL for
missing fields (never 0), write a `headcount` signal row from `fullTimeEmployees`,
record `source` and timestamps, print a per-ticker ok/skip line, and be safely
re-runnable (`INSERT OR REPLACE` on companies).

**Normalise `debt_to_equity`:** yfinance returns it as a percentage. Divide by 100
on ingest and note it in the README caveats.

**Verify:**
```powershell
python scripts/build_db.py
python -c "from app.data.db import query_companies; r=query_companies('app/data/financials.db','tech',5); print(len(r), r[0]['name'], r[0]['ev_to_ebitda'])"
pytest tests/test_db.py -q
```
Gate: 28+ companies inserted, three sectors present, no exceptions.

---

## Phase 2 — MCP server  ·  ~1.5 h

**Goal:** the five tools from `02-ARCHITECTURE.md` §4 running as a separate process.

File: `app/mcp_server/server.py` using FastMCP, `transport="streamable-http"`,
host `127.0.0.1`, port `8765`.

Write the docstrings carefully — they are the model's instructions. Each must
state valid values, what an empty/error result means, and when to prefer the tool.

**Verify:**
```powershell
# terminal 1
python -m app.mcp_server.server
# terminal 2 — official MCP Inspector, no install needed
npx @modelcontextprotocol/inspector
# connect to http://127.0.0.1:8765/mcp, list tools, call query_companies(sector="tech")
```
Gate: all five tools listed in Inspector and returning real rows.
**Screenshot this** — it goes in the README as proof the MCP layer is real.

---

## Phase 3 — Personas & prompts  ·  ~1.5 h

**Goal:** three personas that genuinely reason differently.

Files: `app/agent/personas.py`, `app/agent/sectors.py`, `app/agent/prompts.py`.

Each `Persona` carries: `key`, `name`, `lens`, `priority_fields` (which columns it
reads first), `decision_rules` (what makes a company good *for this lens*),
`output_shape` (what it must conclude with), and `system_prompt`.

The critical detail — each persona's rules invert the others on purpose:

| Field | MF Analyst | Equity Analyst | PE Analyst |
|---|---|---|---|
| High `debt_to_equity` | risk → negative | balance-sheet quality concern | **less leverage headroom → negative for a *different* reason** |
| Low `debt_to_equity` | mildly positive | neutral | **strongly positive** (room to lever the deal) |
| High `beta` | **negative** (portfolio volatility) | mostly ignored | mostly ignored (private, not marked to market) |
| `dividend_yield` | **matters** (total return) | modest signal | **irrelevant** (dividends get recapped away) |
| `ev_to_ebitda` | secondary | valuation cross-check | **primary** — the entry multiple |
| Weak `operating_margin` | avoid | "under pressure" | **opportunity** — an operational lever |

That last row is the whole exercise: the same weak margin is a *reason to avoid*
for MF and a *reason to buy* for PE. Bake it into `decision_rules` explicitly.

`prompts.py` assembles: persona system prompt + sector context + hard rules
(only DB facts, cite tickers, state NULLs as unavailable, never give personalised
investment advice, refuse unknown companies plainly) + the output JSON schema.

**Verify:** `pytest tests/test_personas.py -q` — asserts three distinct
`priority_fields` sets and that each prompt contains its lens keywords.

---

## Phase 4 — LangGraph agent  ·  ~3 h

**Goal:** the graph from `02-ARCHITECTURE.md` §5, with `run_agent()` as the single
entry point.

Files: `app/agent/schemas.py`, `guardrails.py`, `graph.py`, `runner.py`.

1. `schemas.py` — the `AgentResponse` and `Citation` models exactly as specified.
2. `guardrails.py` — see `05-GUARDRAILS-EVAL-OBSERVABILITY.md` for the full rules.
3. `graph.py` — build the state machine. Load MCP tools with:
   ```python
   from langchain_mcp_adapters.client import MultiServerMCPClient
   client = MultiServerMCPClient({
       "financial-data": {"url": settings.mcp_server_url, "transport": "streamable_http"}
   })
   tools = await client.get_tools()
   ```
   Cache the client across requests — do not reconnect per call.
4. `runner.py` — `async def run_agent(query, persona, sector, session_id=None) -> AgentResponse`.
   This is the only function the API and both UIs may call.

**Verify:**
```powershell
python scripts/smoke_test.py
```
The smoke test must run all **nine** persona × sector combinations plus the two
adversarial cases (unknown company, prompt injection) and print a pass/fail table.

Gate: 9/9 grounded answers, out-of-scope correctly refused, injection blocked.

---

## Phase 5 — API  ·  ~1.5 h

**Goal:** `POST /v1/query` plus registry and health endpoints.

File: `app/api/main.py`.

Include: Pydantic request validation (422 with valid values on a bad sector/persona),
CORS for the Next.js dev origin, a simple in-memory rate limit (30 req/min/IP),
request-ID middleware, structured JSON logging, and a lifespan handler that opens
the MCP client on startup and closes it on shutdown.

**Verify:**
```powershell
uvicorn app.api.main:app --reload --port 8000
```
```powershell
curl -X POST http://localhost:8000/v1/query `
  -H "Content-Type: application/json" `
  -d '{\"query\":\"Which companies look like buyout targets?\",\"persona\":\"pe_analyst\",\"sector\":\"manufacturing\"}'

curl -X POST http://localhost:8000/v1/query `
  -H "Content-Type: application/json" `
  -d '{\"query\":\"test\",\"persona\":\"pe_analyst\",\"sector\":\"energy\"}'   # expect 422
# NOTE: `logistics` is a shipped sector and returns 200. Use an unshipped sector
# such as `energy` to exercise the 422 path.
```
Gate: valid JSON with citations and a trace_id; 422 (not 500) for an unknown sector;
`/docs` renders.

---

## Phase 6 — Frontend  ·  ~4 h

Build to `04-FRONTEND-SPEC.md`. Primary: Next.js 15 + Tailwind v4. Then add the
~80-line Streamlit fallback, which calls `run_agent` directly.

**Verify:** all nine combinations answerable from the UI; evidence panel shows the
actual rows; the same question under three personas visibly diverges on screen.

---

## Phase 7 — Guardrails, evals, observability  ·  ~3 h

Build to `05-GUARDRAILS-EVAL-OBSERVABILITY.md`.

**Verify:**
```powershell
python evals/run_eval.py
```
Gate (do not submit below these):
- Out-of-scope refusal: **100%**
- Grounding (numbers traceable to evidence): **≥ 95%**
- Persona divergence: **≥ 0.55**
- Zero-tool-call answers: **0**

Commit `evals/results/report.md` — a repo with its own eval report is rare and
lands hard.

---

## Phase 8 — Packaging & submission  ·  ~2 h

1. `docker-compose.yml` — three services: `mcp`, `api`, `web`, shared volume for the DB.
2. `.github/workflows/ci.yml` — ruff + pytest on push.
3. `README.md` from `07-README-TEMPLATE.md`, including the MCP Inspector screenshot
   and the eval scores table.
4. Commit `app/data/financials.db` so the repo runs with no scraping.
5. **Secret sweep before pushing:**
   ```powershell
   git log -p | Select-String -Pattern "AIza|sk-lf-|pk-lf-"
   ```
   Must return nothing.
6. Record the demo video from `08-DEMO-SCRIPT.md`.

**Final gate — the reviewer simulation.** On a clean clone:
```powershell
git clone <repo>; cd sector-analyst-agent
copy .env.example .env    # add keys
docker compose up
```
A clean clone must reach a working app with no manual steps beyond adding a key.
Note that "5 minutes" is wall-clock optimism on a *cold* Docker build: measured on
the build machine, `pip install` alone took 526s and the full image build ~15
minutes. Quote the warm-cache number and state the cold one honestly rather than
promising a figure the reviewer will watch fail. This is the most common reason good take-homes fail.
