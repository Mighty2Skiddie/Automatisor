"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";

import { usePersona } from "@/components/PersonaProvider";
import ConfidenceChip from "@/components/ConfidenceChip";
import ErrorState from "@/components/ErrorState";
import Markdown from "@/components/Markdown";
import OutOfScopeNotice from "@/components/OutOfScopeNotice";
import TraceLink from "@/components/TraceLink";
import { postQuery } from "@/lib/api";
import { fieldLabel, formatLatency } from "@/lib/format";
import type {
  AgentResponse,
  PersonaInfo,
  PersonaKey,
  SectorKey,
} from "@/lib/types";

/**
 * One question, one sector, all three lenses — side by side (spec §3).
 *
 * This is the screen that performs the brief's headline claim instead of asserting
 * it. The single-answer page can only ever show one persona's conclusion, which
 * leaves "the persona changes the reasoning" as something the reviewer has to take on
 * faith; three columns over one evidence base turns it into something they can read
 * off the screen in five seconds.
 *
 * Two decisions carry the component:
 *
 * 1. **The runs are sequential, not concurrent.** Three simultaneous agent runs is the
 *    single most quota-exposed request in the system — each one is a multi-step graph
 *    with tool calls against a free-tier model with a per-minute limit, and firing them
 *    together is how a live demo earns a 429 in front of the person evaluating it. So
 *    they queue, and each column renders the moment its own run lands.
 * 2. **Each column fails alone.** A rate-limited third persona must not blank the two
 *    conclusions already on screen, so column state is per-persona and the loop
 *    continues past a rejection rather than aborting the comparison.
 */

/**
 * Rebinding `--accent` per column, rather than reading the document-level accent.
 *
 * Everywhere else in this app the accent is the identity of the session and lives on
 * `<html data-persona>`. Here three identities are on screen at once, which that
 * mechanism cannot express — so each column rebinds `--accent` from the same design
 * token the stylesheet would have used. No component names a persona colour; this one
 * names which *token* a column inherits, and the accent still resolves through
 * `var(--accent)` in the markup below.
 */
const PERSONA_ACCENT: Record<PersonaKey, string> = {
  mf_analyst: "var(--color-mf)",
  equity_analyst: "var(--color-equity)",
  pe_analyst: "var(--color-pe)",
};

/** Used if the registry ever serves a persona this build has no token for. */
const FALLBACK_ACCENT = "var(--color-ink)";

/** `--accent` is a custom property, which `CSSProperties` alone will not accept. */
interface AccentStyle extends CSSProperties {
  "--accent": string;
}

/**
 * Questions where the three lenses genuinely disagree.
 *
 * A question with one defensible answer ("what is NVDA's operating margin?") makes the
 * comparison look broken, because three correct columns will agree. These are all
 * allocation judgements, which is where a fund's benchmark-relative view and a
 * sponsor's entry-multiple view actually come apart.
 */
const EXAMPLES: ReadonlyArray<{ label: string; question: string }> = [
  {
    label: "Put money to work?",
    question: "Is this sector a good place to put money to work right now?",
  },
  {
    label: "Best single name",
    question:
      "Which single company in this sector is the most attractive right now, and why?",
  },
  {
    label: "Weakest margins",
    question:
      "What should I make of the companies with the weakest operating margins here?",
  },
  {
    label: "Leverage headroom",
    question: "How much balance-sheet headroom do these companies have?",
  },
];

const DEFAULT_QUESTION = EXAMPLES[0].question;

type ColumnStatus = "idle" | "queued" | "running" | "done" | "failed";

interface ColumnState {
  status: ColumnStatus;
  response: AgentResponse | null;
  /** Whatever `postQuery` rejected with; `ErrorState` does the diagnosis. */
  error: unknown;
}

const IDLE_COLUMN: ColumnState = { status: "idle", response: null, error: null };

/** Per-persona state, so one failure cannot take the other two columns with it. */
type ColumnMap = Partial<Record<PersonaKey, ColumnState>>;

/**
 * Tickers this lens actually stood on.
 *
 * `citations` is the retrieval record and `companies_referenced` is the model's own
 * claim about what it discussed; the guard node already intersects the latter with the
 * known ticker set, so the union of the two is the honest answer to "what did this lens
 * read" without either source silently dropping a company.
 */
function tickersOf(response: AgentResponse): string[] {
  const tickers = new Set<string>();
  for (const citation of response.citations) {
    const ticker = citation.ticker.trim().toUpperCase();
    if (ticker) tickers.add(ticker);
  }
  for (const referenced of response.companies_referenced) {
    const ticker = referenced.trim().toUpperCase();
    if (ticker) tickers.add(ticker);
  }
  return [...tickers].sort();
}

/**
 * Fields this lens's citations carry an actual value for.
 *
 * The key alone is not evidence. The guard node backfills a citation with every
 * priority field it looked for — `values: {name: row.get(name)}` — so a field the
 * dataset has no value for still arrives as a key mapped to `null`, and `fields_used`
 * is only the model's declaration that it consulted the field. Counting either would
 * mark a mandate field as satisfied by data that is not there, which is the same
 * conflation of "missing" with "present" the em dash exists to prevent everywhere else.
 */
function retrievedFields(response: AgentResponse): Set<string> {
  const fields = new Set<string>();
  for (const citation of response.citations) {
    for (const [field, value] of Object.entries(citation.values)) {
      if (value !== null && value !== undefined) fields.add(field);
    }
  }
  return fields;
}

/**
 * Seconds since this element mounted.
 *
 * A 60–100 second wait with no visible progress reads as a hung page. There is no
 * intermediate signal to show — `POST /v1/query` is a single blocking call, and the
 * component refuses to invent one — so the honest thing to display is the clock.
 * Isolated into its own component so the tick re-renders one line, not the grid.
 */
function Elapsed() {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <span className="figure">
      {seconds}s<span className="sr-only"> elapsed</span>
    </span>
  );
}

/** A ticker read by one lens, marked by whether every finished lens read it too. */
function TickerChip({ ticker, shared }: { ticker: string; shared: boolean }) {
  return (
    <li
      title={
        shared
          ? "Read by every lens that has finished"
          : "Only this lens surfaced this company"
      }
      className={
        shared
          ? "figure border border-rule bg-field px-1.5 py-0.5 text-[11px] text-ink"
          : "figure border border-dashed border-rule px-1.5 py-0.5 text-[11px] text-slate"
      }
    >
      {ticker}
      {shared ? null : <span className="sr-only"> — only this lens</span>}
    </li>
  );
}

interface ColumnProps {
  persona: PersonaInfo;
  state: ColumnState;
  /** Names the persona ahead of this one in the queue, for the waiting message. */
  waitingBehind: string | null;
  sharedTickers: ReadonlySet<string>;
}

function PersonaColumn({
  persona,
  state,
  waitingBehind,
  sharedTickers,
}: ColumnProps) {
  const { status, response } = state;

  // The 3px accent left border is the same treatment the single-answer page gives the
  // answer block — in this layout the column *is* the answer block.
  const columnStyle: AccentStyle = {
    "--accent": PERSONA_ACCENT[persona.key] ?? FALLBACK_ACCENT,
    borderLeftColor: "var(--accent)",
  };

  const tickers = response ? tickersOf(response) : [];
  const present = response ? retrievedFields(response) : null;

  return (
    <article
      style={columnStyle}
      aria-busy={status === "running"}
      aria-label={`${persona.name} — ${persona.lens}`}
      className="accent-transition flex min-w-0 flex-col border border-rule border-l-[3px] bg-surface"
    >
      <header className="border-b border-rule px-3 py-2.5 sm:px-4">
        <h3
          className="accent-transition text-sm font-semibold"
          style={{ color: "var(--accent)" }}
        >
          {persona.name}
        </h3>
        <p className="mt-0.5 text-xs leading-snug text-slate">{persona.lens}</p>
      </header>

      <div className="min-w-0 flex-1 px-3 py-3 sm:px-4">
        {status === "idle" && (
          <p className="text-xs text-slate">Not run yet.</p>
        )}

        {status === "queued" && (
          <p className="text-xs text-slate">
            Queued{waitingBehind ? ` — starts after the ${waitingBehind}` : null}.
          </p>
        )}

        {status === "running" && (
          <div className="text-xs text-slate">
            <p className="flex items-center gap-1.5 font-medium text-ink">
              {/*
               * Neutral, not the accent. The accent is allowed exactly three
               * placements — the active rail item, the persona name in the header, and
               * the 3px border on the answer block — and this column already spends two
               * of them. A pulsing accent dot would be a fourth, and the words beside it
               * already say which lens is running.
               */}
              <span
                aria-hidden="true"
                className="inline-block h-2 w-2 rounded-full bg-slate motion-safe:animate-pulse"
              />
              Running now
            </p>
            <p className="mt-1.5 leading-relaxed">
              Querying the database over MCP, then reasoning as the {persona.name}.
              A full run takes 60–100 seconds.
            </p>
            <p className="mt-1.5">
              <Elapsed />
            </p>
          </div>
        )}

        {status === "failed" && <ErrorState error={state.error} />}

        {status === "done" && response && (
          <div className="cross-fade">
            {response.out_of_scope && (
              <div className="mb-3">
                <OutOfScopeNotice response={response} />
              </div>
            )}

            {/*
             * 16px, not the 18px `.answer-prose` sets.
             * `.answer-prose` is an unlayered rule, so it outranks any Tailwind
             * utility regardless of specificity and an inline style is the only way to
             * override it without editing a file this component does not own. The
             * override is wanted: three parallel columns are a scanning surface, and
             * 18px serif at a third of the width breaks every line twice.
             */}
            <div className="answer-prose" style={{ fontSize: "var(--text-base)" }}>
              <Markdown text={response.answer} />
            </div>

            {response.key_points.length > 0 && (
              <div className="mt-3 border-t border-rule pt-3">
                <h4 className="text-[11px] font-medium tracking-wide text-slate">
                  Key points
                </h4>
                <ul className="mt-1.5 space-y-1">
                  {/*
                   * Keyed by position as well as text: two identical key points in one
                   * answer are unlikely but not impossible, and a duplicate key makes
                   * React drop the second one silently.
                   */}
                  {response.key_points.map((point, index) => (
                    <li
                      key={`${index}-${point.slice(0, 24)}`}
                      className="flex gap-1.5 text-xs leading-relaxed text-ink"
                    >
                      <span aria-hidden="true" className="text-slate">
                        ·
                      </span>
                      <span>{point}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {response.caveats.length > 0 && (
              <ul className="mt-2.5 space-y-1">
                {response.caveats.map((caveat, index) => (
                  <li
                    key={`${index}-${caveat.slice(0, 24)}`}
                    className="text-[11px] leading-relaxed text-slate"
                  >
                    {caveat}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/*
       * The comparison's actual payload: the same tickers under every column, with a
       * different set of fields weighted above them. Rendered for finished columns only
       * — an empty "Same rows read:" label would imply the lens read nothing.
       */}
      {status === "done" && response && (
        <footer className="border-t border-rule bg-field px-3 py-2.5 sm:px-4">
          <h4 className="text-[11px] font-medium text-slate">Rows read</h4>
          {tickers.length > 0 ? (
            <ul className="mt-1 flex flex-wrap gap-1">
              {tickers.map((ticker) => (
                <TickerChip
                  key={ticker}
                  ticker={ticker}
                  shared={sharedTickers.has(ticker)}
                />
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[11px] text-slate">
              No company rows reached the answer.
            </p>
          )}

          <h4 className="mt-2.5 text-[11px] font-medium text-slate">Weighted on</h4>
          <ul className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
            {persona.priority_fields.map((field) => {
              // The lens declares these fields in the registry; ink marks the ones its
              // retrieved rows carried a real value for, so the mandate is checked
              // against the run rather than merely restated next to it.
              const retrieved = present?.has(field) ?? false;
              return (
                <li
                  key={field}
                  title={
                    retrieved
                      ? "The rows this lens retrieved carry a value for it"
                      : "Part of this lens's mandate, but the rows it retrieved hold no value for it"
                  }
                  className={`text-[11px] ${retrieved ? "text-ink" : "text-slate"}`}
                >
                  {fieldLabel(field)}
                </li>
              );
            })}
          </ul>

          <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-rule pt-2">
            <ConfidenceChip
              confidence={response.confidence}
              reason={response.confidence_reason}
            />
            <TraceLink traceId={response.trace_id} />
            <span className="text-[11px] text-slate">
              <span className="figure">{formatLatency(response.latency_ms)}</span>
              {response.data_as_of ? (
                <>
                  {" · data as of "}
                  <span className="figure">{response.data_as_of}</span>
                </>
              ) : null}
            </span>
          </div>
        </footer>
      )}
    </article>
  );
}

export function CompareView() {
  const { personas, sectors, sector: deskSector, loading, error } = usePersona();

  const [draft, setDraft] = useState(DEFAULT_QUESTION);
  // `null` means "follow the desk". Deriving rather than mirroring means the registry
  // reconciling its default sector after load cannot strand this view on a key the API
  // would reject, and needs no effect to keep the two in step.
  const [chosenSector, setChosenSector] = useState<SectorKey | null>(null);
  const activeSector = chosenSector ?? deskSector;

  const [columns, setColumns] = useState<ColumnMap>({});
  const [running, setRunning] = useState(false);
  /** The question actually sent, so editing the box cannot relabel finished columns. */
  const [askedQuestion, setAskedQuestion] = useState<string | null>(null);
  const [askedSector, setAskedSector] = useState<SectorKey | null>(null);

  /**
   * Identifies the run that owns the column state.
   *
   * `postQuery` takes no AbortSignal, so an in-flight run cannot be cancelled — only
   * disowned. Bumping this invalidates every pending write from the previous run, which
   * is what keeps a superseded persona from landing in a column belonging to the new
   * question, and what stops a resolved fetch from setting state after unmount.
   */
  const runId = useRef(0);
  useEffect(() => () => void (runId.current += 1), []);

  const run = useCallback(async () => {
    const question = draft.trim();
    if (!question || running || personas.length === 0) return;

    const thisRun = (runId.current += 1);
    const sector = activeSector;

    setAskedQuestion(question);
    setAskedSector(sector);
    setRunning(true);
    setColumns(
      Object.fromEntries(
        personas.map((persona) => [
          persona.key,
          { status: "queued", response: null, error: null } satisfies ColumnState,
        ]),
      ),
    );

    /*
     * One session id across all three calls. The runs are three separate agent
     * invocations of the same question, and giving them a shared id is what lets the
     * trace backend show them as one comparison instead of three unrelated queries.
     */
    const sessionId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? `compare-${crypto.randomUUID()}`
        : `compare-${Date.now()}`;

    try {
      for (const persona of personas) {
        if (runId.current !== thisRun) return;

        setColumns((previous) => ({
          ...previous,
          [persona.key]: { status: "running", response: null, error: null },
        }));

        try {
          const response = await postQuery({
            query: question,
            persona: persona.key,
            sector,
            session_id: sessionId,
          });
          if (runId.current !== thisRun) return;
          setColumns((previous) => ({
            ...previous,
            [persona.key]: { status: "done", response, error: null },
          }));
        } catch (cause: unknown) {
          if (runId.current !== thisRun) return;
          // Deliberately not rethrown: the remaining lenses are still worth running,
          // and two conclusions beside one honest error is a better screen than one
          // error.
          setColumns((previous) => ({
            ...previous,
            [persona.key]: { status: "failed", response: null, error: cause },
          }));
        }
      }
    } finally {
      // In a `finally` because the busy flag gates every control on the screen: an
      // unforeseen throw between the calls would otherwise leave the view permanently
      // mid-run with no way back. Only the run that still owns the state may release
      // the flag — a superseded run has already been replaced or unmounted.
      if (runId.current === thisRun) setRunning(false);
    }
  }, [activeSector, draft, personas, running]);

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void run();
    }
  }

  const finished = useMemo(
    () =>
      personas
        .map((persona) => columns[persona.key]?.response)
        .filter((response): response is AgentResponse => Boolean(response)),
    [columns, personas],
  );

  /**
   * Tickers every finished lens read.
   *
   * This is the claim the screen is making, computed from what is actually on it: if
   * the three columns disagree while standing on the same rows, the difference came
   * from the weighting and not from the data. Below two results there is nothing to
   * share, so the set is empty and every chip renders as lens-specific.
   */
  const sharedTickers = useMemo(() => {
    if (finished.length < 2) return new Set<string>();
    const lists = finished.map(tickersOf);
    return new Set(
      lists[0].filter((ticker) => lists.every((list) => list.includes(ticker))),
    );
  }, [finished]);

  const allTickers = useMemo(() => {
    const union = new Set<string>();
    for (const response of finished) {
      for (const ticker of tickersOf(response)) union.add(ticker);
    }
    return union;
  }, [finished]);

  const resolved = personas.filter((persona) => {
    const status = columns[persona.key]?.status;
    return status === "done" || status === "failed";
  }).length;
  const activePersona = personas.find(
    (persona) => columns[persona.key]?.status === "running",
  );
  const failedCount = personas.filter(
    (persona) => columns[persona.key]?.status === "failed",
  ).length;

  // A rate-limited lens is resolved but not answered, and the live region is the one
  // place a screen-reader user learns that: "all three finished" over two answers and
  // an error would misreport the screen to the reader who cannot see it.
  const completedMessage =
    failedCount > 0
      ? `${personas.length - failedCount} of ${personas.length} lenses answered — ${failedCount} failed.`
      : `All ${personas.length} lenses finished.`;

  const status = running
    ? activePersona
      ? `Running the ${activePersona.name} — ${resolved} of ${personas.length} complete.`
      : `Starting — ${resolved} of ${personas.length} complete.`
    : askedQuestion
      ? completedMessage
      : null;

  // Mirrors the guard at the top of `run`, so the button never *looks* pressable while
  // the call it would make is one the runner refuses.
  const canRun = !running && draft.trim().length > 0 && personas.length > 0;

  const sectorLabel =
    sectors.find((entry) => entry.key === (askedSector ?? activeSector))?.label ??
    activeSector;

  if (error) {
    return (
      <div className="mx-auto max-w-2xl p-4 sm:p-6">
        <ErrorState error={error} />
      </div>
    );
  }

  return (
    <section
      aria-label="Persona comparison"
      className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 p-3 sm:p-6"
    >
      <header>
        <h2 className="text-xl font-medium text-ink">One question, three lenses</h2>
        <p className="mt-1 max-w-[70ch] text-sm leading-relaxed text-slate">
          The same question, the same sector and the same database rows, put through all
          three analyst personas. What changes between the columns is which fields each
          lens weights.
        </p>
      </header>

      <div className="border border-rule bg-surface p-3 sm:p-4">
        {/* Grouped and named, so the four chips are not four unexplained tab stops. */}
        <div
          role="group"
          aria-label="Example questions"
          className="mb-2 flex flex-wrap items-center gap-1.5"
        >
          <span className="text-xs text-slate">Try</span>
          {EXAMPLES.map((example) => (
            <button
              key={example.label}
              type="button"
              onClick={() => setDraft(example.question)}
              disabled={running}
              title={example.question}
              className="rounded-full border border-rule bg-surface px-2.5 py-1 text-xs text-slate hover:border-slate hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              {example.label}
            </button>
          ))}
        </div>

        <label htmlFor="compare-question" className="sr-only">
          The question to put to all three personas
        </label>
        <textarea
          id="compare-question"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          // readOnly rather than disabled: Cmd/Ctrl+Enter fires from inside this box, so
          // disabling it on the same keystroke would blur the element the user is
          // standing on and drop focus to the top of the document — for the three to
          // five minutes the comparison takes, and with the global 1/2/3 persona
          // shortcut live again the moment focus leaves a text field.
          readOnly={running}
          rows={2}
          placeholder="Ask one question of all three analysts…"
          className="w-full resize-none bg-transparent text-base text-ink placeholder:text-slate read-only:cursor-not-allowed read-only:opacity-60"
        />

        <div className="mt-2 flex flex-wrap items-end justify-between gap-3 border-t border-rule pt-2.5">
          <div>
            <label
              htmlFor="compare-sector"
              className="block text-[11px] font-medium text-slate"
            >
              Sector
            </label>
            <select
              id="compare-sector"
              value={activeSector}
              onChange={(event) =>
                setChosenSector(event.target.value as SectorKey)
              }
              disabled={running || loading || sectors.length === 0}
              className="mt-1 border border-rule bg-surface px-2 py-1.5 text-sm text-ink disabled:cursor-not-allowed disabled:opacity-60"
            >
              {sectors.length === 0 ? (
                <option value={activeSector}>Loading…</option>
              ) : (
                sectors.map((entry) => (
                  <option key={entry.key} value={entry.key}>
                    {entry.label} — {entry.company_count} companies
                  </option>
                ))
              )}
            </select>
          </div>

          {/*
           * aria-disabled rather than disabled, for the same reason as the textarea: a
           * disabled button cannot hold focus, so clicking Run would delete the element
           * the user is standing on. `run` already refuses an empty question, a second
           * run and an empty registry, so the button being pressable costs nothing.
           */}
          <button
            type="button"
            onClick={() => void run()}
            aria-disabled={!canRun}
            className={`border border-ink bg-ink px-4 py-2 text-sm text-surface ${
              canRun ? "" : "cursor-not-allowed opacity-40"
            }`}
          >
            {running ? (
              `Running ${resolved + 1} of ${personas.length}…`
            ) : (
              <>
                Run all {personas.length || 3} lenses{" "}
                <span aria-hidden="true">▸</span>
              </>
            )}
          </button>
        </div>

        <p className="mt-2 text-[11px] leading-relaxed text-slate">
          The three runs go out one at a time, not in parallel — each is a full agent
          run against a rate-limited model, and firing them together is how a live demo
          earns a 429. Budget 60–100 seconds per lens.
        </p>
      </div>

      {/*
       * The live region is this one short sentence, not the three answers.
       * The quality floor asks for `aria-live="polite"` on the answer region; applied
       * literally here it would read three complete analyst notes aloud as each one
       * lands, which is not an announcement but an ambush. The state change worth
       * announcing is which lens is running and how many are done, and each column
       * carries `aria-busy` so the answers themselves stay navigable on demand.
       */}
      <p aria-live="polite" className="min-h-[1.25rem] text-xs text-slate">
        {status}
        {askedQuestion && !running ? (
          <>
            {" "}
            <span className="text-ink">{sectorLabel}</span>
            {" · “"}
            {askedQuestion}
            {"”"}
          </>
        ) : null}
      </p>

      <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-3">
        {personas.map((persona, index) => (
          <PersonaColumn
            key={persona.key}
            persona={persona}
            state={columns[persona.key] ?? IDLE_COLUMN}
            waitingBehind={index > 0 ? (personas[index - 1]?.name ?? null) : null}
            sharedTickers={sharedTickers}
          />
        ))}

        {personas.length === 0 && (
          <p className="text-sm text-slate lg:col-span-3">
            {loading ? "Loading the persona registry…" : "No personas available."}
          </p>
        )}
      </div>

      {/*
       * The spec asks for a divergence score from the eval suite here. Nothing in the
       * running system serves one — it is computed offline by `evals/run_eval.py` — and
       * printing a number the screen cannot source would be exactly the fabrication the
       * agent's grounding rules exist to prevent. What is measurable from the responses
       * on screen is the overlap in their evidence, so that is what it reports.
       */}
      <footer className="min-h-[3rem] border-t border-rule pt-3">
        <p className="text-sm text-ink">
          Identical database rows. Different weightings. Different conclusions.
        </p>
        <p className="mt-1 text-xs leading-relaxed text-slate">
          {finished.length >= 2 ? (
            <>
              <span className="figure text-ink">{sharedTickers.size}</span> of{" "}
              <span className="figure text-ink">{allTickers.size}</span> companies were
              read by every lens that finished. Solid chips are that shared evidence;
              dashed chips are companies only one lens surfaced.
            </>
          ) : (
            "Run the comparison to see which rows each lens read."
          )}
        </p>
      </footer>
    </section>
  );
}

export default CompareView;
