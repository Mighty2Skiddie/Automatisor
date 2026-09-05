# 08 — Demo Video Script & Submission Checklist

---

## 1. The 4-minute video

Record with Loom (free). Screen + small webcam bubble. Do not read a script
aloud — know the beats and talk.

**The rule: lead with proof, not with a tour.** Most candidates open with "here's
my folder structure". Open with the thing they most doubt is real.

### 0:00–0:35 — The hook: same data, three answers

Open on `/compare`. Sector: Tech. Question already typed: *"Is this sector a good
place to put money to work right now?"* Hit Run.

Say, while three columns fill:
> "One agent, one database query, three personas. The fund analyst is arguing
> benchmark-relative growth durability. The equity analyst is on margins and
> multiples. The PE analyst is talking entry multiples and leverage headroom.
> Identical rows underneath — you can see the tickers are the same. Different
> weightings, different conclusions."

Point at the identical ticker lists. That is the whole assignment, demonstrated
in thirty seconds.

### 0:35–1:20 — Grounding is real, not claimed

Single-persona view, PE + Manufacturing. Ask about buyout targets.

> "Watch the right panel — it fills *before* the answer starts. That's the agent
> pulling rows over MCP. Every figure in the answer is in that panel, with its
> source and its as-of date."

Hover a company name → its evidence row highlights.

Then the headcount question: *"most recent headcount signal for NVDA?"* → exact
figure, dated, sourced.

### 1:20–1:50 — Honesty test

Ask about a company that isn't in the dataset (SpaceX, or Zomato).

> "This is the failure mode that matters. It says it has no data instead of
> writing something fluent and wrong. That's enforced by an output guardrail, not
> a hope."

### 1:50–2:30 — The MCP boundary

Split screen: MCP Inspector connected to `:8765` listing the five tools, and the
agent code.

> "The database sits behind an MCP server as its own process. The agent is an MCP
> client — it never imports the data layer. There's a test that fails the build if
> any file under `app/agent/` imports sqlite or the db module, so the boundary is
> enforced rather than promised. Tools are capability-shaped, not table-shaped —
> there's deliberately no `run_sql` tool to inject into."

### 2:30–3:05 — The API door

`/docs`, then a real curl. Show the JSON: citations, confidence with a reason,
tools_called, trace_id.

> "Same agent function. The UI and the API are two doors into one implementation.
> The response is built to be consumed by a system — companies referenced,
> fields used, confidence with the reason it's that confidence."

Then an unknown-sector request (`energy`) → 422 with valid values.
> "Your example used logistics — that's a shipped sector here, so it just works.
> Ask for one I don't have and you get a 422 naming what's available, rather than a
> 500 or an invented answer. There's a test for it."

### 3:05–3:45 — Guardrails, evals, observability

Click the trace link from an answer → Langfuse trace opens: spans, tool calls,
tokens, cost.

Then `evals/results/report.md`:
> "25 graded cases. 100% out-of-scope refusal, groundedness above 0.95, and a
> persona-divergence score — I measured that the personas actually diverge instead
> of just claiming it."

### 3:45–4:00 — Close

> "Runs on a clean clone with `docker compose up` and one free API key. If I had
> more time I'd move to time-series ingest so the personas could reason about
> trajectory, not just current state. Schema already supports it."

**Do not** narrate the folder structure, apologise for anything, or say "as you
can see". Cut the recording if you stumble; three takes is normal.

---

## 2. Pre-submission checklist

### Correctness
- [ ] All 9 persona × sector combinations return grounded answers
- [ ] All 6 sample queries from the brief produce good answers
- [ ] Out-of-scope test refuses cleanly, 100% of the time
- [ ] Headcount stress test returns a real, dated figure
- [ ] `sector=logistics` returns 200 (it is a shipped sector)
- [ ] an unknown sector such as `energy` returns 422 listing valid values, not 500
- [ ] Same question, 3 personas → visibly different conclusions

### Repo hygiene
- [ ] `.env` is gitignored; `.env.example` is committed
- [ ] `git log -p | Select-String "AIza|sk-lf-|pk-lf-"` returns nothing
- [ ] `financials.db` committed so the repo runs without scraping
- [ ] `scripts/build_db.py` works from scratch
- [ ] `ruff check .` clean, `pytest` green
- [ ] CI badge passing on the README
- [ ] No `TODO`, no commented-out blocks, no `print()` debugging left in

### The reviewer simulation (do this on a different folder)
- [ ] Clean clone → `.env` → `docker compose up` → working app, no manual steps
- [ ] Cold build time stated honestly in the README (~15 min build; seconds once cached)
- [ ] Works with **no** Langfuse keys set (tracing silently disabled)
- [ ] `/healthz` reports mcp/db/llm status
- [ ] README's first screen answers: what is it, how do I run it, what's impressive

### Deliverables
- [ ] Public GitHub repo (or shared with the reviewer's account)
- [ ] README with setup, schema decisions, MCP design, one improvement
- [ ] `.env.example`
- [ ] Sample DB + rebuild script
- [ ] Eval report committed
- [ ] MCP Inspector screenshot in README
- [ ] 4-minute Loom linked at the top of the README

---

## 3. The three things that decide this

1. **It runs on their machine in five minutes.** More strong take-homes die here
   than anywhere else.
2. **The personas visibly diverge.** The brief says this twice. The `/compare`
   view and the divergence score are your proof.
3. **It refuses honestly.** They wrote "we're checking for honest scope-awareness,
   not fluent bluffing." That sentence is the real test in the whole assignment.

Everything else — the guardrails, the evals, the tracing, the UI — is what moves
you from "passes" to "we should hire this person".
