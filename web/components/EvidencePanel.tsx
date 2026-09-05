import type { ReactNode } from "react";

import {
  citationHealth,
  fieldLabel,
  formatValue,
  HEALTH_COLOR,
  MISSING,
  type Health,
} from "@/lib/format";
import type { Citation, EvidenceRow, ToolCallRecord } from "@/lib/types";
import { ToolTrace } from "./ToolTrace";

/**
 * The evidence panel: the exact database rows behind the answer.
 *
 * This is the screen's argument, not its decoration. A reviewer's real question is
 * "is it retrieving, or bluffing?", and the only honest way to answer it is to show
 * the retrieved rows *before* the prose that reasons over them — which is exactly the
 * ordering the SSE stream guarantees (`progress` -> `evidence` -> `response`).
 *
 * Hence two accepted shapes. Mid-stream only `rows` exist, and they render as preview
 * cards; once the answer lands, `citations` supersede them because a citation says
 * which fields the analyst actually used, not merely which were fetched.
 */

export interface EvidencePanelProps {
  /** Fields the analyst cited. Preferred over `rows` once present. */
  citations: Citation[];
  /** Raw retrieved rows from the `evidence` stream event, available first. */
  rows?: EvidenceRow[];
  /** MCP calls made for this answer; drives the footer count and the trace. */
  toolCalls?: ToolCallRecord[];
  /** True while the stream is open, so the empty state can say what is happening. */
  streaming?: boolean;
}

/** Row keys that identify a company rather than describe its financials. */
const IDENTITY_KEYS: ReadonlySet<string> = new Set([
  "ticker",
  "name",
  "company_name",
  "sector",
  "industry",
  "country",
]);

/** Row keys that are provenance or plumbing; they render in the card footer, not as figures. */
const META_KEYS: ReadonlySet<string> = new Set([
  "id",
  "snapshot_date",
  "last_updated",
  "source",
  "profile_source",
  "financials_source",
]);

/**
 * The six figures a preview card leads with, in analyst reading order.
 *
 * A retrieved row carries thirteen financial fields; thirteen figures per company in a
 * 380px column is a data dump, not evidence. The preview is a glimpse with an explicit
 * count of what else came back — the citation, moments later, is the record.
 */
const PREVIEW_FIELDS: readonly string[] = [
  "revenue_growth",
  "operating_margin",
  "pe_ratio",
  "free_cash_flow",
  "debt_to_equity",
  "market_cap",
];

const HEALTH_LABEL: Record<Health, string> = {
  ok: "Every field present",
  partial: "Some fields missing from the dataset",
  missing: "No values in the dataset",
};

function isAbsent(value: number | string | null | undefined): boolean {
  // Mirrors the judgement inside formatValue, so the em dash and the missing colour
  // can never disagree about whether a value exists.
  if (value === null || value === undefined) return true;
  if (typeof value === "number") return !Number.isFinite(value);
  return false;
}

/**
 * A row value that can be shown as a figure.
 *
 * `get_company_detail` returns a nested `signals` list alongside the scalar columns.
 * Rendering that as a field would print an em dash captioned "not in dataset" for
 * evidence that was in fact retrieved — and count it as missing in the health dot,
 * which is the precise misreport this panel exists to prevent.
 */
function isScalar(value: unknown): value is number | string | null {
  return (
    value === null || typeof value === "number" || typeof value === "string"
  );
}

function textOf(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function HealthDot({ health }: { health: Health }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      <span
        aria-hidden="true"
        className="inline-block h-2 w-2 rounded-full"
        style={{ backgroundColor: HEALTH_COLOR[health] }}
      />
      <span className="sr-only">{HEALTH_LABEL[health]}</span>
    </span>
  );
}

function FieldRow({
  field,
  value,
}: {
  field: string;
  value: number | string | null | undefined;
}) {
  const absent = isAbsent(value);
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-slate">{fieldLabel(field)}</dt>
      <dd
        className={`figure text-sm ${absent ? "" : "text-ink"}`}
        // Absence is grey and never red: the dataset not holding a figure is a fact
        // about the dataset, not a failure of the agent.
        style={absent ? { color: HEALTH_COLOR.missing } : undefined}
        title={absent ? "not in dataset" : undefined}
      >
        {absent ? (
          <>
            <span aria-hidden="true">{MISSING}</span>
            <span className="sr-only">not in dataset</span>
          </>
        ) : (
          formatValue(field, value)
        )}
      </dd>
    </div>
  );
}

function EvidenceCard({
  ticker,
  companyName,
  health,
  source,
  asOf,
  note,
  children,
}: {
  ticker: string;
  companyName: string | null;
  health: Health;
  source: string | null;
  asOf: string | null;
  note?: string;
  children: ReactNode;
}) {
  return (
    <article
      // Addressable by ticker so the answer column can highlight the row behind a
      // company name without either component holding a reference to the other.
      id={`evidence-${ticker}`}
      data-evidence-ticker={ticker}
      aria-label={`Evidence for ${ticker}`}
      className="border border-rule bg-surface px-3 py-2.5"
    >
      <header className="flex items-baseline justify-between gap-2">
        <h3 className="flex min-w-0 items-baseline gap-2">
          <span className="figure text-sm font-semibold text-ink">{ticker}</span>
          {companyName ? (
            <span className="truncate text-xs text-slate">{companyName}</span>
          ) : null}
        </h3>
        <HealthDot health={health} />
      </header>

      <dl className="mt-2 space-y-1">{children}</dl>

      <footer className="mt-2.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-t border-rule pt-1.5 text-[11px] text-slate">
        <span>{source ?? "source not recorded"}</span>
        <span aria-hidden="true">·</span>
        <span>
          as of{" "}
          {asOf ? (
            <span className="figure">{asOf}</span>
          ) : (
            <>
              <span className="figure" aria-hidden="true">
                {MISSING}
              </span>
              <span className="sr-only">date not recorded</span>
            </>
          )}
        </span>
        {note ? (
          <>
            <span aria-hidden="true">·</span>
            <span>{note}</span>
          </>
        ) : null}
      </footer>
    </article>
  );
}

function CitationCard({ citation }: { citation: Citation }) {
  // fields_used is the analyst's own record of what it read; values is the retrieval.
  // If the model returned a citation with no field list, the values still get shown —
  // dropping them would hide evidence that was genuinely retrieved.
  const fields =
    citation.fields_used.length > 0
      ? citation.fields_used
      : Object.keys(citation.values);

  return (
    <EvidenceCard
      ticker={citation.ticker}
      companyName={textOf(citation.company_name)}
      health={citationHealth(citation.values)}
      // `source` and `as_of` default to "" in the Pydantic contract, not to null, so
      // an omitted provenance arrives as an empty string. Blank is the one thing this
      // panel must never render: it reads as "no claim made" where the honest reading
      // is "not recorded".
      source={textOf(citation.source)}
      asOf={textOf(citation.as_of)}
    >
      {fields.map((field) => (
        <FieldRow key={field} field={field} value={citation.values[field]} />
      ))}
    </EvidenceCard>
  );
}

function PreviewCard({ row }: { row: EvidenceRow }) {
  const dataFields = Object.keys(row).filter(
    (key) =>
      !IDENTITY_KEYS.has(key) && !META_KEYS.has(key) && isScalar(row[key]),
  );
  const preferred = PREVIEW_FIELDS.filter((field) => dataFields.includes(field));
  // Resolved once, so the "+N more" count describes what is actually hidden. Deriving
  // it from the preferred list while rendering the fallback list overstated it by the
  // number of fields on screen.
  const shown =
    preferred.length > 0
      ? preferred
      : dataFields.slice(0, PREVIEW_FIELDS.length);
  const withheld = dataFields.length - shown.length;

  const values: Record<string, number | string | null> = {};
  for (const field of dataFields) values[field] = row[field] ?? null;

  const ticker = textOf(row.ticker) ?? "Unknown";

  return (
    <EvidenceCard
      ticker={ticker}
      companyName={textOf(row.name) ?? textOf(row.company_name)}
      health={citationHealth(values)}
      source={
        textOf(row.source) ??
        textOf(row.financials_source) ??
        textOf(row.profile_source)
      }
      asOf={textOf(row.snapshot_date)}
      note={withheld > 0 ? `+${withheld} more fields retrieved` : undefined}
    >
      {shown.map((field) => (
        <FieldRow key={field} field={field} value={row[field]} />
      ))}
    </EvidenceCard>
  );
}

/**
 * One card per company, which is what the spec asks for — so two citations naming the
 * same ticker merge instead of rendering twice.
 *
 * Nothing in the contract stops a model emitting a ticker twice (one citation per
 * field group, say), and two cards for one company would collide on both the React key
 * and the `evidence-<ticker>` id the answer column uses to highlight a row, where the
 * first match silently wins.
 */
function mergeByTicker(citations: Citation[]): Citation[] {
  const byTicker = new Map<string, Citation>();

  for (const citation of citations) {
    const existing = byTicker.get(citation.ticker);
    if (!existing) {
      byTicker.set(citation.ticker, citation);
      continue;
    }

    // A retrieved figure is never overwritten by a null for the same field: absence in
    // one citation is not evidence of absence in the dataset.
    const values = { ...existing.values };
    for (const [field, value] of Object.entries(citation.values)) {
      if (values[field] === null || values[field] === undefined) {
        values[field] = value;
      }
    }

    byTicker.set(citation.ticker, {
      ...existing,
      company_name: textOf(existing.company_name) ?? citation.company_name,
      fields_used: [
        ...new Set([...existing.fields_used, ...citation.fields_used]),
      ],
      values,
      source: textOf(existing.source) ?? citation.source,
      as_of: textOf(existing.as_of) ?? citation.as_of,
    });
  }

  return [...byTicker.values()];
}

function plural(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

export function EvidencePanel({
  citations,
  rows = [],
  toolCalls = [],
  streaming = false,
}: EvidencePanelProps) {
  const companies = mergeByTicker(citations);
  const cited = companies.length > 0;
  const previewing = !cited && rows.length > 0;
  const populated = cited || previewing;

  // Rows read is the retrieval count; cited is the subset the analyst leaned on. When
  // they differ, saying so is more honest than showing one number for both.
  const rowsRead = rows.length > 0 ? rows.length : companies.length;

  const summary = populated
    ? [
        plural(rowsRead, "row"),
        cited && companies.length !== rowsRead
          ? `${companies.length} cited`
          : null,
        plural(toolCalls.length, "tool call"),
      ]
        .filter((part): part is string => part !== null)
        .join(" · ")
    : null;

  return (
    <section
      aria-label="Evidence"
      className="flex h-full min-h-0 w-full flex-col bg-field"
    >
      <header className="shrink-0 border-b border-rule px-4 py-3">
        <h2 className="text-sm font-medium text-ink">Rows the agent read</h2>
        <p className="mt-0.5 text-xs text-slate">
          {previewing
            ? "Retrieved. The analyst has not reasoned over them yet."
            : "Every figure in the answer comes from these rows."}
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {cited ? (
          <div className="space-y-2">
            {companies.map((citation) => (
              <CitationCard key={citation.ticker} citation={citation} />
            ))}
          </div>
        ) : previewing ? (
          <div className="space-y-2">
            {rows.map((row, index) => (
              <PreviewCard key={textOf(row.ticker) ?? `row-${index}`} row={row} />
            ))}
          </div>
        ) : (
          <p className="max-w-[32ch] text-xs leading-relaxed text-slate">
            {streaming
              ? "Querying the database over MCP. Rows appear here before the answer does."
              : "Nothing retrieved yet. Ask a question and the rows the agent reads will land here first."}
          </p>
        )}
      </div>

      <div className="shrink-0 border-t border-rule bg-surface">
        {/* Polite and scoped to the one line that changes: announcing every card as it
            arrives would talk over a screen-reader user for the whole stream. */}
        <p
          role="status"
          aria-live="polite"
          className="px-4 py-2 text-xs text-slate"
        >
          {summary ?? (streaming ? "Retrieving…" : "No rows retrieved")}
        </p>
        <ToolTrace toolCalls={toolCalls} />
      </div>
    </section>
  );
}

export default EvidencePanel;
