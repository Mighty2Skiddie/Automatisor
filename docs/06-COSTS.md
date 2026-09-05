# 06 — What This Costs

Plain answer first: **you can build, run, demo and submit this for ₹0 / $0.**

---

## 1. Build-and-submit cost (what you'll actually pay)

| Item | Cost | Note |
|---|---|---|
| Gemini 2.5 Flash API | **$0** | Free tier in Google AI Studio. Rate-limited per minute, which is plenty for building and a demo. No card required. |
| Langfuse Cloud | **$0** | Hobby tier: 50,000 events/month. This build will use a few thousand. |
| SQLite | **$0** | A file. |
| FastMCP, LangGraph, FastAPI, Streamlit, Next.js, Ragas | **$0** | All open source. |
| yfinance data | **$0** | Public Yahoo data. |
| GitHub public repo + Actions CI | **$0** | Public repos get free Actions minutes. |
| Vercel (host the Next.js UI) | **$0** | Hobby tier. |
| Render / Railway (host the API + MCP) | **$0** | Free tier; it sleeps when idle — fine for a demo, note it in the README. |
| Loom video | **$0** | Free tier covers 5-minute videos. |
| **Total to submit** | **$0** | |

**The one thing to watch:** free-tier Gemini has a per-minute request limit. If
you run the eval suite in a tight loop you will hit it. Add a 1–2 second sleep
between eval cases — that is all.

---

## 2. If it were a real product (so you can answer "how would this scale?")

Rough token maths per query: ~2,500 input tokens (system prompt + persona +
retrieved rows) and ~700 output tokens.

| Model | Cost per query | 10k queries/month |
|---|---|---|
| Gemini 2.5 Flash | ~$0.0005 | **~$5** |
| GPT-4o mini class | ~$0.0007 | ~$7 |
| Frontier model (Claude Opus / GPT-4 class) | ~$0.05 | ~$500 |

Infrastructure at that scale:

| Item | Monthly |
|---|---|
| API + MCP on a small always-on instance (Render/Fly, 1GB) | $7–14 |
| Managed Postgres (once SQLite is outgrown) | $0–20 |
| Langfuse Cloud Pro (if past 50k events) | $59 |
| Vercel Pro (only if you need team features) | $20 |
| **Realistic total, 10k queries/month** | **~$25–100** |

**The expert point to make in your write-up:** the LLM is not the expensive part
at this scale — the always-on infrastructure is. And the biggest cost lever is
*context size*: sending 25 full company rows into every prompt is wasteful.
Retrieving only the fields the active persona actually weights (which the
`priority_fields` design already makes possible) cuts input tokens roughly in
half. Saying this shows you think about unit economics, which very few candidates do.

---

## 3. Cost controls to actually implement (they're cheap and they impress)

1. **Field projection by persona** — `compare_companies(tickers, fields)` exists so
   the agent can pull five fields instead of eighteen.
2. **Row limits** — `query_companies` defaults to 25 and caps at 50.
3. **Tool-loop cap** — maximum 5 tool iterations per query; hard stop.
4. **Response caching** — hash `(query, persona, sector, snapshot_date)`; serve
   repeats from cache. Demo questions get asked repeatedly during review, so this
   also makes your demo feel instant.
5. **Cost logged per request** — Langfuse gives you cost per trace; surface the
   session total in the UI footer. A UI that shows its own running cost is a
   detail reviewers remember.

---

## 4. Time cost (be realistic)

| Phase | Hours |
|---|---|
| 0–1 Setup + data | 2.5 |
| 2 MCP server | 1.5 |
| 3 Personas | 1.5 |
| 4 Agent graph | 3 |
| 5 API | 1.5 |
| 6 Frontend | 4 |
| 7 Guardrails + evals + observability | 3 |
| 8 Packaging, README, video | 2 |
| **Total** | **~19 h** |

With Claude Code doing the typing, call it **2–3 focused days**. If you are short
on time, the brief itself tells you the priority: a working MCP + dual-interface
skeleton beats persona polish. Cut the `/compare` route and the Next.js UI first
(Streamlit alone is compliant) — never cut the guardrails or the evals, because
those are what make the submission stand out.
