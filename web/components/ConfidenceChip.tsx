import { HEALTH_COLOR, type Health } from "@/lib/format";
import type { Confidence } from "@/lib/types";

/**
 * Confidence, with the reasoning behind it one keystroke away.
 *
 * A bare "confidence: high" is a number an analyst cannot audit and therefore cannot
 * use; the value is only worth showing next to *why* the agent settled on it. The
 * disclosure is a native `<details>`, which means the expand/collapse works with no
 * client JavaScript, is keyboard operable and screen-reader labelled for free, and
 * survives a hydration failure — a control this small should not cost a bundle.
 */

/**
 * Confidence reuses the data-health palette rather than owning a fourth colour scale:
 * "how complete is this evidence" and "how sure is this reading of it" are the same
 * question asked at two altitudes, and one palette keeps them legible as one system.
 */
const HEALTH_FOR: Record<Confidence, Health> = {
  high: "ok",
  medium: "partial",
  low: "missing",
};

export interface ConfidenceChipProps {
  confidence: Confidence;
  /** The agent's stated reason. Empty renders a static chip with no disclosure. */
  reason: string;
  className?: string;
}

/**
 * The dot carries the colour and the text stays in `--ink`.
 *
 * `--partial` and `--missing` sit at roughly 3.2–3.7:1 on white: fine for a graphical
 * indicator, short of AA for 12px text. Colouring the label instead of the dot would
 * fail the contrast floor on two of the three states.
 */
function HealthDot({ health }: { health: Health }) {
  return (
    <span
      aria-hidden="true"
      className="h-2 w-2 shrink-0 rounded-full"
      style={{ backgroundColor: HEALTH_COLOR[health] }}
    />
  );
}

export function ConfidenceChip({ confidence, reason, className }: ConfidenceChipProps) {
  const health = HEALTH_FOR[confidence];
  const chip = (
    <>
      <HealthDot health={health} />
      <span className="text-slate">Confidence</span>
      <span className="font-medium text-ink">{confidence}</span>
    </>
  );

  if (reason.trim() === "") {
    return (
      <span
        className={`inline-flex items-center gap-1.5 rounded-sm border border-rule bg-field px-2 py-1 text-xs ${className ?? ""}`}
      >
        {chip}
      </span>
    );
  }

  return (
    <details className={`group min-w-0 ${className ?? ""}`}>
      <summary
        // `list-none` plus the WebKit pseudo-element removes both vendors' default
        // triangle, so the chip owns its own affordance and reads as one control.
        className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm border border-rule bg-field px-2 py-1 text-xs [&::-webkit-details-marker]:hidden"
      >
        {chip}
        <span
          aria-hidden="true"
          className="text-slate motion-safe:transition-transform motion-safe:duration-200 group-open:rotate-90"
        >
          ▸
        </span>
        <span className="sr-only">— show why</span>
      </summary>

      {/*
       * The disclosure's rule is neutral `--rule`, not the health colour. `--ok` is the
       * same value as `--mf`, so tinting a vertical border here would put a green stripe
       * beside the answer block's green accent stripe whenever the Mutual Fund desk is
       * active — a fourth accent placement in a design that allows exactly three. The
       * dot above already carries the health signal.
       */}
      <p className="mt-2 max-w-[52ch] border-l-2 border-rule pl-3 text-xs leading-relaxed text-slate">
        {reason}
      </p>
    </details>
  );
}

export default ConfidenceChip;
