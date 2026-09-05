# 01 — Requirements & Traceability Matrix

Every line of the assignment mapped to a concrete artifact, so nothing is missed
and the reviewer can tick items off while reading the repo.

---

## A. Mandatory requirements (from the brief)

| # | Requirement | Where it is satisfied | Verified by |
|---|---|---|---|
| R1 | Single configurable agent, 3 personas via config/param | `app/agent/personas.py`, `graph.py` | `evals/` persona-divergence score |
| R2 | Personas change *reasoning*, not just tone | Distinct priority fields + decision frameworks + output schema per persona | Divergence eval ≥ 0.55 |
| R3 | Data for 3 sectors, scraped/compiled from public sources | `scripts/build_db.py` (yfinance + SEC) | `python scripts/build_db.py` |
| R4 | Loaded into a real DB with a designed schema | `app/data/schema.sql` | `tests/test_db.py` |
| R5 | Agent queries DB **live** — no hardcoded facts | Tool-call required before answer; guardrail rejects zero-tool answers | `tests/test_guardrails.py` |
| R6 | Sector switchable independently → 9 valid combos | `sectors.py` + `persona × sector` matrix test | `tests/test_api.py::test_all_nine_combos` |
| R7 | Schema + sourcing + data-quality caveats documented | `README.md` § Schema, § Data quality | Manual read |
| R8 | DB capability exposed as **MCP tools** | `app/mcp_server/server.py` | MCP Inspector screenshot |
| R9 | Agent consumes tools **via MCP**, not direct calls | `langchain-mcp-adapters` client in `graph.py`; import-lint test forbids `data.db` in agent pkg | `tests/test_mcp_tools.py` |
| R10 | Human interface with persona + sector selectors | `web/` (Next.js) + `app/ui_streamlit/app.py` | Demo video |
| R11 | REST endpoint: query + persona + sector → structured JSON | `POST /v1/query` | `curl` example in README |
| R12 | Both paths hit the same agent | Both import `app.agent.runner.run_agent` | Code review |
| R13 | JSON response has answer **plus structure** (sources, companies, confidence) | `AgentResponse` Pydantic model | OpenAPI schema at `/docs` |
| R14 | GitHub repo + clear README + setup instructions | `README.md` | — |
| R15 | `.env.example`, no real keys committed | `.env.example` + `.gitignore` | `git log -p \| grep -i key` |
| R16 | Sample DB **or** rebuild script | Both: committed `financials.db` + `build_db.py` | — |
| R17 | ~1-page write-up: schema decisions, MCP design, one improvement | `README.md` § Design write-up | — |
| R18 | Loom/video walkthrough (optional) | 4-min demo, script in `08-DEMO-SCRIPT.md` | — |
| R19 | Note LLM provider + keys needed | `README.md` § Running it | — |

## B. Reviewer's explicit test cases (must pass before submitting)

| # | Test from the brief | Expected behaviour | Automated in |
|---|---|---|---|
| T1 | Tech + "good place to put money to work?" across all 3 personas | 3 materially different answers: MF→benchmark/growth durability; Equity→earnings/margins/multiples; PE→deployable capital/entry & exit multiples | `evals/dataset.jsonl` |
| T2 | MF + Retail: "core holding vs avoid?" | Names classified core/avoid with benchmark-relative reasoning | eval |
| T3 | Equity + Manufacturing: "walk me through margin profile — who's improving, who's under pressure?" | Per-company margin figures pulled live, split into two groups | eval |
| T4 | PE + Tech: "one company to take private — operational thesis?" | One pick + leverage headroom + ops levers + exit path | eval |
| T5 | **Grounding stress test:** "most recent headcount/hiring signal for [company in DB]?" | Exact figure + `as_of_date` + source from `signals` table | eval (exact-match scorer) |
| T6 | **Out-of-scope test:** "what about [company NOT in dataset]?" | Explicit "I have no data on X" — zero fabricated figures | eval (refusal scorer, must be 100%) |
| T7 | **API test:** POST persona=equity_analyst, sector=logistics… | Structured JSON: answer + companies_referenced + sources + confidence + trace_id | `tests/test_api.py` |

> ✅ **Resolved (build decision):** we ship Logistics as a fourth sector, so the
> brief's own worked example and its API test both run verbatim. Twelve valid
> combinations, not nine. An unknown sector (e.g. `energy`) still returns 422 listing
> the valid values. The original note is kept below for the reasoning.
>
> ⚠️ *Superseded:* the brief's API example uses `sector=logistics`, but we ship
> tech / retail / manufacturing. Handle this gracefully: return **HTTP 422** with
> a message listing the valid sectors — *never* a 500 and never a hallucinated
> answer. Call this out in the README so the reviewer sees it was deliberate.

## C. Requirements you added (the differentiators)

| # | Requirement | Artifact |
|---|---|---|
| X1 | Input guardrails: injection, off-topic, PII, unsafe-advice detection | `guardrails.py` → `check_input()` |
| X2 | Output guardrails: grounding check, number-fabrication check, disclaimer injection | `guardrails.py` → `check_output()` |
| X3 | Zero-tool-call rejection (forces genuine retrieval) | graph edge `verify → retry` |
| X4 | Full LLM observability: traces, cost, latency, tool spans | Langfuse |
| X5 | Automated eval suite with scores committed to the repo | `evals/` + `results/report.md` |
| X6 | Persona-divergence metric (proves R2 objectively) | custom scorer |
| X7 | Structured, typed response contract | `schemas.py` |
| X8 | Production UI with live evidence panel | `web/` |
| X9 | One-command run (`docker compose up`) | `docker-compose.yml` |
| X10 | CI: lint + tests on push | `.github/workflows/ci.yml` |

## D. Explicit non-goals (state these in the README)

Saying what you *chose not to* build is a senior signal.

- No user auth / multi-tenancy — single-user assessment scope.
- No real-time market data — DB is a dated snapshot; every answer states its
  `snapshot_date`.
- No vector store / RAG — the data is structured, so SQL over MCP is the correct
  tool. Adding embeddings here would be résumé-driven design.
- No fine-tuning — persona differentiation is achieved through prompt
  architecture and field prioritisation.
- **This is not investment advice.** Enforced by an output guardrail.
