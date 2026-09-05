import { fieldLabel } from "@/lib/format";
import type { AgentResponse } from "@/lib/types";

/**
 * The agent declining, presented as correct behaviour rather than as a failure.
 *
 * `out_of_scope` is the single most valuable thing this system does: it is the moment
 * the agent had the opportunity to invent a number about a company it has never seen
 * and did not take it. Styling that as an error — red, an alert icon, `role="alert"` —
 * would teach the reader to read the honest path as the broken one, so this is a quiet
 * grey card that names the boundary and hands the reader back to the analyst's note.
 */
export interface OutOfScopeNoticeProps {
  response: AgentResponse;
}

export function OutOfScopeNotice({ response }: OutOfScopeNoticeProps) {
  // Sector keys share the snake_case shape that `fieldLabel` exists to humanise, so
  // "manufacturing" and "free_cash_flow" resolve through the same transform.
  const sector = fieldLabel(response.sector);

  return (
    <div className="rounded-sm border border-rule bg-field p-3 sm:p-4">
      <p className="text-sm font-medium text-ink">Outside this dataset</p>

      <p className="mt-1 max-w-[58ch] text-sm leading-relaxed text-slate">
        The question reaches past the {sector} companies this desk holds. The analyst
        said so instead of estimating — the note below is the boundary in its own words,
        not a failed lookup.
      </p>

      {response.tool_calls.length > 0 && (
        <p className="mt-2 text-xs text-slate">
          The database was still queried:{" "}
          <span className="figure text-ink">{response.tool_calls.length}</span>{" "}
          {response.tool_calls.length === 1 ? "call" : "calls"} returned{" "}
          <span className="figure text-ink">
            {response.tool_calls.reduce((total, call) => total + call.row_count, 0)}
          </span>{" "}
          rows, and the analyst still placed the question outside them.
        </p>
      )}
    </div>
  );
}

export default OutOfScopeNotice;
