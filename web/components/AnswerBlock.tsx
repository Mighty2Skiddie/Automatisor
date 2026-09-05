import { formatLatency, fieldLabel } from "@/lib/format";
import type { AgentResponse } from "@/lib/types";
import ConfidenceChip from "./ConfidenceChip";
import Markdown from "./Markdown";
import OutOfScopeNotice from "./OutOfScopeNotice";

/**
 * One answer, rendered whole.
 *
 * The answer arrives as a complete structured object — there is no token stream to
 * animate — so this component's job is not motion but *provenance*: the accent stripe
 * says which desk produced it, the metadata row says what it cost and how much of the
 * database it actually touched, and the caveats stay on the page instead of behind a
 * disclosure. A reader should be able to judge the answer without trusting it.
 *
 * Server-rendered: nothing here holds state. The one interactive control, the
 * confidence disclosure, is a native `<details>`.
 */

export interface AnswerBlockProps {
  response: AgentResponse;
  /**
   * Display name of the persona that produced this answer.
   *
   * `response.persona` carries the registry *key* (`mf_analyst`), not a label, and the
   * response has no display name on it. The caller already holds the persona list from
   * `/v1/personas`, so it passes the name down rather than this file keeping a
   * key-to-name table that would drift the moment a persona is renamed.
   */
  personaName?: string;
}

function plural(count: number, singular: string, pluralForm: string): string {
  return count === 1 ? singular : pluralForm;
}

/**
 * A figure and its unit. The number is set in the mono face so that a column of answer
 * cards has its latencies and counts landing on the same character grid.
 */
function Stat({ value, label }: { value: string; label: string }) {
  return (
    <span className="text-xs text-slate">
      <span className="figure text-ink">{value}</span> {label}
    </span>
  );
}

export function AnswerBlock({ response, personaName }: AnswerBlockProps) {
  const companyCount = response.companies_referenced.length;
  const toolCallCount = response.tool_calls.length;

  return (
    <article
      /*
       * The answer region is polite rather than assertive: it should be announced when
       * it lands, not interrupt whatever the reader is already hearing. It is not
       * atomic, so a screen reader reads the new answer rather than re-reading the
       * whole card including the metadata every time.
       */
      aria-live="polite"
      aria-label="Analyst answer"
      /*
       * Which desk produced *this* card, as data. The accent rules in globals.css are
       * currently scoped to `html[data-persona]`, so this attribute is inert today and
       * every card inherits the live persona's accent — which means a card answered by
       * the Equity desk wears the PE stripe the moment the reader switches, while its
       * badge still names Equity. Loosening those selectors from `html[data-persona=…]`
       * to `[data-persona=…]` makes each card carry the accent it was answered with; the
       * attribute is here so that fix is a one-line change in the stylesheet.
       */
      data-persona={response.persona}
      className="accent-transition rounded-r-sm border border-l-[3px] border-rule bg-surface p-4 sm:p-6"
      /*
       * One of exactly three places the persona accent is allowed to appear (spec §2,
       * principle 2). It is read from `--accent`, so no persona colour is ever named
       * inside a component.
       */
      style={{ borderLeftColor: "var(--accent)" }}
    >
      {/*
       * Persona and sector are stamped on every card because the transcript is meant to
       * be read as a comparison — switching desks does not clear history, so a card two
       * questions back has to say for itself which desk produced it. This is a badge,
       * so it carries the persona's *name*: `persona_lens` is a four-sentence mandate
       * that belongs in the rail, where the reader chooses a desk, not repeated in full
       * above every answer. The name stays in `--ink`; the accent is spent on the left
       * border and nowhere else on this card.
       */}
      <p className="text-xs leading-relaxed text-slate">
        <span className="text-ink">{personaName ?? response.persona}</span>
        {" · "}
        {fieldLabel(response.sector)}
      </p>

      {response.out_of_scope && (
        <div className="mt-3">
          <OutOfScopeNotice response={response} />
        </div>
      )}

      <Markdown text={response.answer} className="answer-prose mt-4 text-ink" />

      {response.key_points.length > 0 && (
        <section className="mt-6">
          <h3 className="flex items-center gap-3 text-xs font-medium text-slate">
            Key points
            <span aria-hidden="true" className="h-px flex-1 bg-rule" />
          </h3>
          <ul className="mt-2.5 ml-4 list-disc space-y-1.5 text-[15px] leading-relaxed text-ink marker:text-slate">
            {response.key_points.map((point, index) => (
              <li key={`${index}-${point.slice(0, 24)}`}>{point}</li>
            ))}
          </ul>
        </section>
      )}

      {/*
       * `items-start` keeps the stats aligned with the chip's first line, so opening the
       * confidence disclosure pushes content down without dragging its neighbours out of
       * alignment.
       */}
      <div className="mt-5 flex flex-wrap items-start gap-x-5 gap-y-2 border-t border-rule pt-3">
        <ConfidenceChip
          confidence={response.confidence}
          reason={response.confidence_reason}
        />
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 py-1">
          <Stat
            value={String(companyCount)}
            label={plural(companyCount, "company", "companies")}
          />
          <Stat
            value={String(toolCallCount)}
            label={plural(toolCallCount, "tool call", "tool calls")}
          />
          <Stat value={formatLatency(response.latency_ms)} label="end to end" />
        </div>
      </div>

      {/*
       * Caveats are set small and grey but never collapsed. A limitation the reader has
       * to click to discover is a limitation the product is hiding.
       */}
      {response.caveats.length > 0 && (
        <section className="mt-3 border-t border-rule pt-3">
          <h3 className="text-xs font-medium text-slate">Caveats</h3>
          <ul className="mt-1.5 ml-4 list-disc space-y-1 text-xs leading-relaxed text-slate marker:text-slate">
            {response.caveats.map((caveat, index) => (
              <li key={`${index}-${caveat.slice(0, 24)}`}>{caveat}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

export default AnswerBlock;
