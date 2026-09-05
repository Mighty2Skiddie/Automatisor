import type { ToolCallRecord } from "@/lib/types";

/**
 * The MCP calls behind an answer: name, arguments, rows returned, and any error.
 *
 * The evidence panel shows *what* came back; this shows *what was asked for*. Together
 * they close the loop a reviewer needs — the agent never reaches the database except
 * through these calls, so listing them verbatim is the claim's proof rather than its
 * restatement.
 *
 * Native <details> rather than a state hook: disclosure is a solved browser behaviour
 * with keyboard and screen-reader support already correct, and it keeps this component
 * renderable on the server.
 */

export interface ToolTraceProps {
  toolCalls: ToolCallRecord[];
  /** Force the list open. Errors open it regardless — a failed call must not hide. */
  defaultOpen?: boolean;
}

/** Compact, faithful rendering of one argument value; no truncation of a query shape. */
function formatArgument(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    // A circular or otherwise unserialisable value is still worth naming.
    return String(value);
  }
}

function ArgumentList({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (entries.length === 0) {
    return <p className="mt-1 text-[11px] text-slate">no arguments</p>;
  }
  return (
    <dl className="mt-1 space-y-0.5">
      {entries.map(([key, value]) => (
        <div key={key} className="flex gap-1.5 text-[11px] leading-snug">
          <dt className="shrink-0 text-slate">{key}</dt>
          <dd className="figure min-w-0 break-words text-ink">
            {formatArgument(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ToolCallEntry({ call }: { call: ToolCallRecord }) {
  return (
    <li className="border-t border-rule px-4 py-2 first:border-t-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="figure text-xs font-semibold text-ink">{call.name}</span>
        <span className="figure shrink-0 text-[11px] text-slate">
          {call.row_count} {call.row_count === 1 ? "row" : "rows"}
        </span>
      </div>
      <ArgumentList args={call.arguments} />
      {call.error ? (
        // The warning colour carries the signal as a rule, not as the text itself:
        // #B07A0B on this background is 3.2:1, which fails AA for body copy, and an
        // error message is the last string on the panel that may be hard to read.
        <p
          className="mt-1 border-l-2 pl-2 text-[11px] leading-snug text-ink"
          style={{ borderColor: "var(--color-partial)" }}
        >
          <span className="font-semibold">Failed:</span> {call.error}
        </p>
      ) : null}
    </li>
  );
}

export function ToolTrace({ toolCalls, defaultOpen }: ToolTraceProps) {
  // Nothing was called: the evidence panel's summary already says so, and an empty
  // disclosure box would be a control that does nothing.
  if (toolCalls.length === 0) return null;

  const failures = toolCalls.filter((call) => call.error !== null).length;
  const open = defaultOpen ?? failures > 0;

  return (
    <details open={open} className="group border-t border-rule">
      <summary className="flex cursor-pointer list-none items-baseline justify-between gap-2 px-4 py-2 text-xs text-slate hover:text-ink [&::-webkit-details-marker]:hidden">
        <span>
          MCP calls <span className="figure">({toolCalls.length})</span>
          {failures > 0 ? (
            <span className="text-ink">
              {" · "}
              <span
                aria-hidden="true"
                className="mr-1 inline-block h-1.5 w-1.5 rounded-full align-middle"
                style={{ backgroundColor: "var(--color-partial)" }}
              />
              {failures} failed
            </span>
          ) : null}
        </span>
        {/* The native triangle is suppressed so the summary sits on the panel's
            baseline grid; these two spans are its replacement, swapped by CSS so the
            label stays truthful without this component holding open/closed state. */}
        <span aria-hidden="true" className="shrink-0 text-[11px]">
          <span className="group-open:hidden">show</span>
          <span className="hidden group-open:inline">hide</span>
        </span>
      </summary>
      <ul className="border-t border-rule bg-field/60 pb-1">
        {toolCalls.map((call, index) => (
          <ToolCallEntry key={call.tool_call_id || `${call.name}-${index}`} call={call} />
        ))}
      </ul>
    </details>
  );
}

export default ToolTrace;
