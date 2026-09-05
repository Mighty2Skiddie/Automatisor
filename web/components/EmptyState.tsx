/**
 * What the answer column shows before the first question.
 *
 * Deliberately quiet and illustration-free: the screen is an instrument, and an empty
 * instrument should look at rest, not like a marketing panel waiting to be filled.
 */
export default function EmptyState() {
  return (
    // Left-aligned, per the type rules: nothing on this screen is centred body text,
    // and the empty state sits on the same measure the answer will occupy.
    <div className="flex min-h-[16rem] flex-col justify-center py-12">
      <p className="max-w-[52ch] text-base text-slate">
        Pick a persona and a sector, then ask about the companies in the dataset.
      </p>
      <p className="mt-3 max-w-[52ch] text-xs text-slate">
        Every figure in an answer comes from a live database read, shown in the evidence
        panel as it happens.
      </p>
    </div>
  );
}
