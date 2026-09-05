/**
 * A link to the Langfuse trace for this exact answer.
 *
 * Langfuse is optional in this deployment, so `trace_id` is null whenever no keys are
 * configured. That case renders *nothing at all*: a dead link or an empty slot would
 * promise observability the running system does not have, which is the same class of
 * dishonesty the agent's grounding rules exist to prevent.
 */

/**
 * Read at build time by Next.js. Defaults to the same host as `LANGFUSE_HOST` in
 * .env.example, so the link is correct for the common cloud-tier setup without any
 * extra frontend configuration.
 */
const LANGFUSE_HOST = (
  process.env.NEXT_PUBLIC_LANGFUSE_HOST ?? "https://cloud.langfuse.com"
).replace(/\/+$/, "");

export interface TraceLinkProps {
  traceId: string | null;
}

export function TraceLink({ traceId }: TraceLinkProps) {
  const id = traceId?.trim();
  if (!id) return null;

  // `/trace/:id` is project-agnostic and redirects to the project-scoped view, so the
  // UI does not need to know a Langfuse project id to build a working link.
  const href = `${LANGFUSE_HOST}/trace/${encodeURIComponent(id)}`;

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      title={`Langfuse trace ${id}`}
      className="inline-flex items-baseline gap-1 border border-rule bg-surface px-2 py-1 text-xs text-slate hover:text-ink"
    >
      <span>Trace</span>
      <span aria-hidden="true">↗</span>
      <span className="sr-only">— opens the Langfuse trace in a new tab</span>
    </a>
  );
}

export default TraceLink;
