# 00 — Start Here

This is a complete build specification. Hand it to Claude Code and it has
everything it needs — architecture, contracts, design system, phase gates — without
you re-explaining anything.

---

## The files

| File | What it's for | Who reads it |
|---|---|---|
| `CLAUDE.md` | Persistent project rules, stack, folder layout, conventions | Claude Code, every session |
| `01-REQUIREMENTS.md` | Every line of the brief → a concrete artifact + how it's verified | You, before submitting |
| `02-ARCHITECTURE.md` | System design, schema, MCP design, graph, contracts, failure modes | Claude Code, before any code |
| `03-BUILD-PLAN.md` | 8 phases with commands and verification gates | Claude Code, one phase at a time |
| `04-FRONTEND-SPEC.md` | Design tokens, layout, components, the `/compare` view | Claude Code, phase 6 |
| `05-GUARDRAILS-EVAL-OBSERVABILITY.md` | Guardrail rules, eval dataset + scorers, Langfuse wiring | Claude Code, phase 7 |
| `06-COSTS.md` | What it costs to build and to run, plus cost controls | You |
| `07-README-TEMPLATE.md` | The submission README, pre-written | You, phase 8 |
| `08-DEMO-AND-SUBMISSION.md` | Video script + final checklist | You, at the end |

---

## How to run this with Claude Code

**Step 1 — Set up the repo and drop in the docs.**
```powershell
mkdir sector-analyst-agent; cd sector-analyst-agent
git init
mkdir docs
# copy CLAUDE.md to the repo ROOT (Claude Code auto-reads it)
# copy 01- through 08- into docs/
```

**Step 2 — Start Claude Code and prime it.**
```powershell
claude
```
First message:
> Read CLAUDE.md, docs/02-ARCHITECTURE.md and docs/03-BUILD-PLAN.md in full.
> Then summarise back to me: the MCP boundary rule, the response contract, and
> what Phase 0 and Phase 1 require. Do not write code yet.

Making it summarise first catches misreadings before they become 40 files.

**Step 3 — Build one phase per session.**
> Execute Phase 1 from docs/03-BUILD-PLAN.md. Create every file it names, then
> run the Verify block and show me the output. Stop at the gate.

Do not let it run phases 1–4 in one go. Each gate exists so a mistake costs you
one phase, not the whole build.

**Step 4 — At each gate, actually run the verify commands yourself.** Claude Code
reporting success is not the same as it working.

**Step 5 — When a phase drifts,** point at the doc rather than re-explaining:
> That bypasses the MCP boundary — see CLAUDE.md rule 1 and 02-ARCHITECTURE §4.
> Fix it and re-run the import-lint test.

---

## The four things this build is really judged on

1. **The MCP boundary is genuine** — the agent is a client, and a test enforces it.
2. **The personas actually diverge** — the same weak margin is a reason to avoid
   for the fund analyst and a reason to buy for the PE analyst. Measured, not claimed.
3. **It refuses honestly** — the brief's own words: "honest scope-awareness, not
   fluent bluffing."
4. **It runs on their machine in five minutes.**

Guardrails, evals, observability and the UI are what take it from "passes" to
"hire this person" — but they never come at the cost of those four.

---

## Order of work if you run short on time

Cut from the bottom, never the top:

```
1. Data + MCP server + agent + API          ← the assignment
2. Streamlit UI                             ← brief-compliant human door
3. Guardrails + out-of-scope honesty        ← the real test
4. Eval suite + committed report            ← the differentiator
5. Langfuse observability                   ← the differentiator
6. Next.js UI                               ← the polish
7. /compare view                            ← the wow
8. Docker + CI                              ← the professionalism
```

The brief says it directly: a working MCP + dual-interface skeleton beats
exhaustive persona polish. If you cut anything, say so in the README under "what
I'd do next" — naming your own tradeoffs reads as senior.
