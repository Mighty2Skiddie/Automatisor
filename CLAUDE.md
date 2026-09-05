# CLAUDE.md — Project Context for Claude Code

> This file is read automatically by Claude Code at the start of every session.
> It is the single source of truth for how this repository is built.

---

## Project

**Name:** `sector-analyst-agent`
**What it is:** One configurable AI agent that acts as three different financial
analyst personas, answers questions grounded in a real database of company
financials across three sectors, and is reachable through both a web UI and a
REST API. The database is exposed to the agent over **MCP (Model Context
Protocol)** — the agent never touches the database directly.

**Why it exists:** Take-home assessment for an AI Engineer role. The reviewer is
judging *system design and engineering judgement*, not data volume. Everything
must look like production code written by a senior engineer, not a tutorial.

---

## Non-negotiable rules

1. **MCP is a hard protocol boundary.** The agent is an MCP *client*. The
   database is behind an MCP *server* running as a separate process. Never
   `import app.data.db` from agent code. If you are tempted to, stop — that
   defeats the entire point of the exercise.
2. **No hardcoded facts in prompts.** Every company name, number, or claim in an
   answer must come from a live MCP tool call in that same request.
3. **No secrets in code.** Everything through `.env` + `pydantic-settings`.
   `.env.example` is committed, `.env` is gitignored.
4. **Honest scope-awareness.** If asked about a company not in the database, the
   agent says so plainly. Fabrication is the single worst failure mode here.
5. **One agent, two doors.** Streamlit/Next UI and REST API both call the exact
   same `run_agent()` function. No duplicated logic.
6. **Personas change reasoning, not vocabulary.** Three personas reading the same
   database row must reach *different conclusions* because they weight fields
   differently — not just use different adjectives.
7. **Every LLM call is traced** to Langfuse with persona, sector, tools called,
   latency, and token cost.

---

## Stack (locked — do not substitute)

| Layer | Choice | Pin | Reason |
|---|---|---|---|
| Agent framework | LangGraph | `1.2.11` | Explicit state machine, easy to add guardrail nodes |
| LLM | Google Gemini 2.5 Flash | `langchain-google-genai==4.4.0` | Free tier, fast, native structured output |
| MCP server | FastMCP | `3.4.7` | Least ceremony, streamable-HTTP transport |
| MCP client | `langchain-mcp-adapters` | `0.3.2` | Turns MCP tools into LangGraph tools |
| DB | SQLite | stdlib | Zero setup, file-committable, assessment explicitly allows |
| API | FastAPI + Pydantic v2 | `0.141.1` / `2.13.5` | Typed contracts, auto OpenAPI docs |
| Primary UI | Next.js 15 + Tailwind v4 | — | Assessment says "Streamlit **or equivalent**" |
| Fallback UI | Streamlit (~80 lines) | `1.63.0` | Named in the brief; cheap insurance for the demo |
| Observability | Langfuse (cloud free tier) | `4.15.1` | Traces, cost, eval scores in one place |
| Evaluation | Ragas + custom persona-divergence eval | `0.4.3` | Proves the agent actually works |
| Data source | `yfinance` (Yahoo public data) | `1.7.0` | Free, legal, real numbers |

`requirements.txt` is the authoritative pin list; the column above exists so a stack
change is visible in a diff of this file. **Two cross-package constraints govern it:**

1. **Stay on an mcp-1.x FastMCP.** `langchain-mcp-adapters` 0.3.2 caps `mcp<2.0.0`;
   `fastmcp>=4` requires `mcp>=2.0.0`. Unpinned, pip does not error — it resolves to
   `fastmcp 4.0.2 + mcp 2.1.1 + langchain-mcp-adapters 0.3.1`, silently selecting the one
   adapter release with an unbounded `mcp>=1.24.0` pin. Release 0.3.2 added the `<2.0.0`
   cap specifically to disavow that pairing.
2. **One `langchain-core` 1.x across the tree.** langgraph needs `>=1.4.7`,
   langchain-google-genai `>=1.6.1`, langchain-mcp-adapters `>=1.3.3`.

**OS:** Windows. All shell commands in docs must be PowerShell-compatible.

---

## Repository layout (build exactly this)

```
sector-analyst-agent/
├── CLAUDE.md
├── docs/                         # 00-START-HERE .. 08-DEMO-AND-SUBMISSION + the brief
├── README.md                     # the submission write-up
├── requirements.txt
├── .env.example
├── .gitignore
├── docker-compose.yml            # one-command local run
├── Makefile                      # or tasks.ps1 for Windows
│
├── app/
│   ├── __init__.py
│   ├── config.py                 # pydantic-settings
│   ├── logging_conf.py           # structured JSON logging
│   │
│   ├── data/
│   │   ├── schema.sql
│   │   ├── db.py                 # ALL SQL lives here
│   │   └── financials.db         # committed sample DB
│   │
│   ├── mcp_server/
│   │   ├── __init__.py
│   │   └── server.py             # FastMCP tools
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── personas.py           # 3 persona definitions
│   │   ├── sectors.py            # sector registry + validation
│   │   ├── prompts.py            # system prompt assembly
│   │   ├── guardrails.py         # input + output guards
│   │   ├── schemas.py            # Pydantic response contract
│   │   ├── graph.py              # LangGraph state machine
│   │   └── runner.py             # run_agent() — THE single entry point
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py               # FastAPI app
│   │
│   └── ui_streamlit/
│       └── app.py                # fallback UI
│
├── web/                          # Next.js primary UI
│   └── (see docs/04-FRONTEND-SPEC.md)
│
├── scripts/
│   ├── build_db.py               # rebuild DB from public sources
│   └── smoke_test.py             # end-to-end sanity check
│
├── evals/
│   ├── dataset.jsonl             # ~25 graded test questions
│   ├── run_eval.py               # Ragas + custom scorers
│   └── results/                  # committed eval report
│
└── tests/
    ├── test_db.py
    ├── test_guardrails.py
    ├── test_mcp_tools.py
    └── test_api.py
```

---

## Coding conventions

- Python 3.12, full type hints, `ruff` clean.
- Docstrings explain *why*, not *what*.
- No bare `except:` — always catch specific exceptions and log them.
- All external calls (LLM, MCP) wrapped in `tenacity` retry with exponential backoff.
- Pydantic models for every boundary (API in/out, MCP tool returns, agent output).
- Tests use `pytest`; no network calls in tests (mock the LLM).

## Definition of done for any phase

A phase is done when: the command in its "Verify" block runs clean, the tests for
it pass, and the feature is visible in a Langfuse trace.

---

## Build order

Follow `docs/03-BUILD-PLAN.md` phase by phase. Do not jump ahead — each phase has a
verification gate. Read `docs/02-ARCHITECTURE.md` before writing any code.
