# 04 — Frontend Specification

The brief says "Streamlit **or equivalent**". We ship a real web app as the
primary interface and keep a small Streamlit app as a fallback. Both call the
same agent.

---

## 1. The design brief

**Subject:** an analyst's desk instrument. The user is a professional investor
switching between three mental models over the same data.

**Primary job of the screen:** make it *obvious and provable* that (a) the persona
changed the reasoning, and (b) every number came from the database.

**Design consequence:** the memorable element is **not** the chat bubble. It is
the **evidence panel** — the live table of exact database rows the agent pulled,
sitting beside the answer. That panel is the visual answer to the reviewer's
deepest question ("is it actually retrieving, or bluffing?"). Spend the boldness
there; keep everything else quiet.

**Second consequence:** the persona is not a dropdown value, it is the
*identity of the session*. Switching persona re-tints the interface. The user
should feel they changed desks, not settings.

---

## 2. Design tokens

### Colour

A cool, instrument-grey base — deliberately not the cream/serif/terracotta look,
and not the near-black-plus-acid-accent look. The neutrals do the work; colour is
reserved for meaning (persona identity, data health).

```css
--ink:        #16191D;   /* primary text, near-graphite */
--slate:      #5C6670;   /* secondary text, labels */
--field:      #EDF0F2;   /* app background, cool grey */
--surface:    #FFFFFF;   /* cards, panels */
--rule:       #D5DBDF;   /* hairlines, table borders */

/* Persona identity — one accent owns the session */
--mf:         #1F6F5C;   /* fund green   — Mutual Fund Analyst */
--equity:     #A84B12;   /* burnt amber  — Equity Analyst */
--pe:         #45369B;   /* deep indigo  — PE Analyst */

/* Data-health semantics (used only in the evidence panel) */
--ok:         #1F6F5C;
--partial:    #B07A0B;
--missing:    #8A929A;   /* NULL is grey, never red — absence isn't an error */
```

`--accent` is set on `<html data-persona="pe">` and every persona-aware element
reads `var(--accent)`. One CSS variable swap re-themes the app.

### Type

Two families, clearly distinct, both free on Google Fonts:

- **Newsreader** (serif) — the agent's answer prose. An analyst note reads like
  editorial writing; a serif at 17–18px with generous leading makes a
  multi-paragraph answer genuinely readable instead of chat-bubble skimmable.
- **Public Sans** — all interface chrome: controls, labels, headers, buttons.
- **IBM Plex Mono** — *numbers only*, in the evidence table, with
  `font-variant-numeric: tabular-nums` so columns of figures align. Mono is used
  here because financial figures must be scannable in a column, not for decoration.

Scale (1.25 ratio): 12 / 14 / 16 / 18 / 22 / 28 / 36.
Answer body: 18px Newsreader, `line-height: 1.65`, max width **66ch**.
Left-aligned throughout. No centred body text, no all-caps labels.

### Layout

Three zones on a desk, not a chat app.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Sector Analyst   ·   Mutual Fund Analyst          data as of 12 Aug 2026│  header, 56px
├────────────────┬─────────────────────────────────┬───────────────────────┤
│                │                                 │                       │
│  DESK RAIL     │   ANSWER COLUMN                 │   EVIDENCE PANEL      │
│  260px         │   flexible, max 66ch            │   380px               │
│                │                                 │                       │
│ ┌────────────┐ │  ┌───────────────────────────┐  │  Rows the agent read  │
│ │ MF ANALYST │ │  │ Q: Is tech a good place   │  │  ┌─────────────────┐  │
│ │ Equity     │ │  │    to put money now?      │  │  │ NVDA  ●         │  │
│ │ PE         │ │  └───────────────────────────┘  │  │ rev growth 114% │  │
│ └────────────┘ │                                 │  │ op margin 62.1% │  │
│  the lens:     │  Answer, in Newsreader, framed  │  │ P/E       51.2  │  │
│  "long-only,   │  by the active persona…         │  │ src yfinance    │  │
│   benchmark-   │                                 │  └─────────────────┘  │
│   relative"    │  ── Key points ──               │  ┌─────────────────┐  │
│                │  · benchmark-relative view      │  │ INTC  ◐         │  │
│ ┌────────────┐ │  · growth durability            │  │ op margin  —    │  │
│ │ Tech       │ │  · portfolio fit                │  │ …               │  │
│ │ Retail     │ │                                 │  └─────────────────┘  │
│ │ Manufact.  │ │  [confidence: high ▸ why]       │                       │
│ └────────────┘ │  [tools called: 2]  [trace ↗]   │  5 rows · 1 tool call │
│                │                                 │                       │
│ 30 companies   │  ┌───────────────────────────┐  │                       │
│ 3 sectors      │  │ Ask something…      [Ask] │  │                       │
└────────────────┴─────────────────────────────────┴───────────────────────┘
```

Below 1100px the evidence panel becomes a slide-over drawer with a persistent
"Evidence (5)" button. Below 720px the desk rail collapses into two selects.

### Principles

1. The evidence panel is populated *before the answer arrives* — the user watches
   retrieval happen. Retrieval-then-reasoning is the story, and it is the ordering
   the SSE stream actually guarantees (`progress` -> `evidence` -> `response`).
2. Persona colour appears in exactly three places: the active rail item, the
   header persona name, and a 3px left border on the answer block. Nowhere else.
3. NULLs render as an em dash in `--missing` grey with a tooltip "not in dataset".
   Never 0, never blank.
4. One orchestrated motion moment: on persona change, the answer column
   cross-fades and the accent transitions over 240ms. No per-card hover lifts, no
   scroll-triggered entrances.
5. Every claim on screen is traceable: hovering a company name in the answer
   highlights its row in the evidence panel.

---

## 3. The persona-comparison view (the moment that wins the demo)

Add a second route, `/compare`, that runs **one question through all three
personas simultaneously** and shows them in three columns.

```
┌─────────────────────────────────────────────────────────────────┐
│ "Is this sector a good place to put money to work right now?"   │
│  Sector: Tech                                        [Run ▸]    │
├───────────────────┬───────────────────┬─────────────────────────┤
│ MUTUAL FUND       │ EQUITY            │ PRIVATE EQUITY          │
│ ▏green border     │ ▏amber border     │ ▏indigo border          │
│                   │                   │                         │
│ Benchmark-relative│ Margin & multiple │ Entry multiple, leverage│
│ framing…          │ framing…          │ headroom, exit path…    │
│                   │                   │                         │
│ Same rows read:   │ Same rows read:   │ Same rows read:         │
│ NVDA MSFT INTC    │ NVDA MSFT INTC    │ NVDA MSFT INTC          │
│ Weighted on:      │ Weighted on:      │ Weighted on:            │
│ growth · beta     │ margins · P/E     │ FCF · D/E · EV/EBITDA   │
└───────────────────┴───────────────────┴─────────────────────────┘
    identical evidence, three conclusions ──────────────────────►
```

Underneath, a single line stating: *"Identical database rows. Different
weightings. Different conclusions."* — and a small divergence score from the eval
suite.

This directly performs the brief's headline requirement ("same underlying data,
three different framings") instead of asking the reviewer to take it on faith.
**Open the demo video with this screen.**

---

## 4. Components to build

`web/` — Next.js 15 App Router, TypeScript, Tailwind v4, no component library
(Tailwind + a few hand-rolled primitives; shadcn's default look is recognisable).

| Component | Responsibility |
|---|---|
| `DeskRail` | Persona list (name + one-line lens), sector list, dataset stats footer |
| `PersonaProvider` | Sets `data-persona` on `<html>`; single source of accent truth |
| `QuestionBar` | Textarea + submit; Cmd/Ctrl+Enter sends; 4 example questions as chips |
| `AnswerBlock` | Streams the narrative, renders `key_points`, accent left border |
| `ConfidenceChip` | `high/medium/low` + expandable `confidence_reason` |
| `EvidencePanel` | One card per retrieved company: ticker, health dot, the exact fields used, source, `as_of` |
| `ToolTrace` | Collapsible list of MCP calls made, with arguments and row counts |
| `TraceLink` | Opens the Langfuse trace for this exact answer |
| `OutOfScopeNotice` | Distinct, calm treatment when `out_of_scope: true` — not an error style |
| `CompareView` | The three-column route above |
| `EmptyState` | "Pick a persona and a sector, then ask about the companies in the dataset." |
| `ErrorState` | Names what failed and the fix: "Data service unreachable. Start the MCP server on port 8765." |

### Interaction rules

- **The answer does not stream token by token, and the API does not pretend to.**
  The final answer is a *structured* object (`AnalystDraft`), and a JSON schema cannot
  be emitted incrementally as readable prose — a half-parsed object is not a partial
  answer. `POST /v1/query/stream` therefore streams what is genuinely available in
  order: `progress` events named after the graph node that just completed, then
  `evidence` as soon as the MCP rows land, then the complete `response`, then a
  terminal `done`. The evidence panel filling before the answer is the honest version
  of this promise, and it is the one that actually demonstrates retrieval.
- Persona/sector changes never clear history — each answer card keeps a small
  badge showing which persona produced it, so the transcript is a comparison.
- Keyboard: `1/2/3` switch persona, `Cmd/Ctrl+K` focuses the question bar.
- Loading is honest: "Querying database…" → "Reasoning as PE Analyst…" — mirroring
  the actual graph nodes, not a generic spinner.

### Quality floor (non-negotiable)

Responsive to 375px · visible keyboard focus rings · `prefers-reduced-motion`
respected · AA contrast on all text · `aria-live="polite"` on the answer region ·
no layout shift when the evidence panel fills.

---

## 5. Streamlit fallback (`app/ui_streamlit/app.py`)

~80 lines. Sidebar with persona and sector radios, chat input, answer, and an
expander showing the citation table. It calls `run_agent` directly (no HTTP), which
proves the "same agent, two doors" claim in the most literal way. Keep it plain —
its job is insurance and brief-compliance, not beauty.
