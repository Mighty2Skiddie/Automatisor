/**
 * Rendering values in the units the schema actually stores them in.
 *
 * Mirrors `format_value` in app/ui_streamlit/app.py. Getting this wrong is how a
 * correct 18.75% margin reaches the screen as "0.1875".
 */

const FRACTION_FIELDS = new Set([
  "revenue_growth",
  "gross_margin",
  "operating_margin",
  "profit_margin",
  "return_on_equity",
  "dividend_yield",
]);

const CURRENCY_FIELDS = new Set(["market_cap", "revenue", "free_cash_flow"]);

/** The em dash used for a value the dataset does not hold. */
export const MISSING = "—";

/**
 * Format one field value.
 *
 * `null` renders as an em dash, never as 0 and never as blank: "we do not have this"
 * and "this is zero" are different facts, and conflating them is the exact
 * hallucination the schema's NULL discipline exists to prevent.
 */
export function formatValue(
  field: string,
  value: number | string | null | undefined,
): string {
  if (value === null || value === undefined) return MISSING;
  if (typeof value === "string") return value;
  if (!Number.isFinite(value)) return MISSING;

  if (FRACTION_FIELDS.has(field)) return `${(value * 100).toFixed(2)}%`;
  if (field === "debt_to_equity") return `${value.toFixed(2)}x`;

  if (CURRENCY_FIELDS.has(field)) {
    const magnitude = Math.abs(value);
    if (magnitude >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
    if (magnitude >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
    if (magnitude >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
    return `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  }

  return value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Human-readable field label, e.g. `ev_to_ebitda` -> "EV / EBITDA". */
export function fieldLabel(field: string): string {
  const special: Record<string, string> = {
    ev_to_ebitda: "EV / EBITDA",
    pe_ratio: "P/E",
    debt_to_equity: "Debt / Equity",
    return_on_equity: "Return on equity",
    free_cash_flow: "Free cash flow",
    market_cap: "Market cap",
  };
  if (special[field]) return special[field];
  return field.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/**
 * Data health for one citation: how complete the retrieved evidence is.
 *
 * Absence is grey, never red — missing data is not an error, it is a fact about the
 * dataset, and colouring it as a failure would misrepresent it.
 */
export type Health = "ok" | "partial" | "missing";

export function citationHealth(values: Record<string, unknown>): Health {
  const entries = Object.values(values);
  if (entries.length === 0) return "missing";
  const nulls = entries.filter((v) => v === null || v === undefined).length;
  if (nulls === 0) return "ok";
  if (nulls === entries.length) return "missing";
  return "partial";
}

export const HEALTH_COLOR: Record<Health, string> = {
  ok: "var(--color-ok)",
  partial: "var(--color-partial)",
  missing: "var(--color-missing)",
};

export function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}
