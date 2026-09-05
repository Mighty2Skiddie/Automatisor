"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import AnswerBlock from "@/components/AnswerBlock";
import { DeskRail } from "@/components/DeskRail";
import EmptyState from "@/components/EmptyState";
import ErrorState from "@/components/ErrorState";
import { EvidencePanel } from "@/components/EvidencePanel";
import { usePersona } from "@/components/PersonaProvider";
import QuestionBar from "@/components/QuestionBar";
import { ToolTrace } from "@/components/ToolTrace";
import { TraceLink } from "@/components/TraceLink";
import { streamQuery } from "@/lib/api";
import { NODE_LABELS } from "@/lib/types";
import type {
  AgentResponse,
  EvidenceRow,
  ToolCallRecord,
} from "@/lib/types";

/** One completed exchange, kept so the transcript is a comparison. */
interface Exchange {
  id: number;
  question: string;
  response: AgentResponse;
}

export default function DeskPage() {
  const { persona, sector, personas, sectors, loading, error } = usePersona();

  const [transcript, setTranscript] = useState<Exchange[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [progress, setProgress] = useState<string>("");
  const [liveRows, setLiveRows] = useState<EvidenceRow[]>([]);
  const [liveTools, setLiveTools] = useState<ToolCallRecord[]>([]);
  const [queryError, setQueryError] = useState<unknown>(null);
  const nextId = useRef(1);

  const activePersona = personas.find((entry) => entry.key === persona);
  const latest = transcript[0];

  const ask = useCallback(
    async (question: string) => {
      setPending(question);
      setQueryError(null);
      setLiveRows([]);
      setLiveTools([]);
      setProgress(NODE_LABELS.validate ?? "Starting");

      try {
        for await (const event of streamQuery({
          query: question,
          persona,
          sector,
        })) {
          if (event.type === "progress") {
            setProgress(NODE_LABELS[event.node] ?? event.node);
          } else if (event.type === "evidence") {
            // Arrives before the answer. This ordering is the entire claim the
            // evidence panel makes, so it is rendered the moment it lands.
            setLiveRows(event.rows);
            setLiveTools(event.tool_calls);
          } else if (event.type === "response") {
            setTranscript((prior) => [
              { id: nextId.current++, question, response: event.response },
              ...prior,
            ]);
          } else if (event.type === "error") {
            // The server's own sentence, unchanged: it already names the failure and
            // the fix, and ErrorState diagnoses from that text. Replacing it with a
            // generic message would throw away the only useful part.
            setQueryError(new Error(event.detail));
          }
        }
      } catch (cause) {
        setQueryError(cause);
      } finally {
        setPending(null);
        setProgress("");
      }
    },
    [persona, sector],
  );

  const busy = pending !== null;
  const totalCompanies = sectors.reduce(
    (sum, entry) => sum + entry.company_count,
    0,
  );

  return (
    <div className="flex min-h-screen flex-col min-[720px]:flex-row">
      <DeskRail />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-rule bg-surface px-4 min-[720px]:px-6">
          <div className="flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 text-sm font-semibold">Sector Analyst</span>
            <span aria-hidden className="text-rule">
              ·
            </span>
            {/* One of the three permitted accent placements. */}
            <span
              className="accent-transition truncate text-sm font-medium"
              style={{ color: "var(--accent)" }}
            >
              {activePersona?.name ?? "—"}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-4 text-xs text-slate">
            <span className="hidden sm:inline">
              {totalCompanies} companies · {sectors.length} sectors
            </span>
            <Link
              href="/compare"
              className="rounded border border-rule px-2 py-1 font-medium hover:bg-field"
            >
              Compare all three
            </Link>
          </div>
        </header>

        {error ? (
          <div className="p-6">
            <ErrorState error={new Error(error)} />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col gap-6 p-4 min-[1100px]:flex-row min-[720px]:p-6">
            <section className="flex min-w-0 flex-1 flex-col gap-5">
              <QuestionBar onSubmit={ask} busy={busy || loading} />

              {/*
                The live region is this wrapper, which exists on every render — not the
                answer card, which is created together with its own content. A live
                region inserted at the same moment as its text is frequently not
                announced at all.
              */}
              <div aria-live="polite" aria-busy={busy} className="flex flex-col gap-5">
                {busy && (
                  <p className="text-sm text-slate">
                    <span className="figure">{progress}</span>
                    {pending ? ` — “${pending}”` : ""}
                  </p>
                )}

                {queryError !== null && <ErrorState error={queryError} />}

                {transcript.length === 0 && !busy && queryError === null && (
                  <EmptyState />
                )}

                {transcript.map((exchange) => (
                  <article key={exchange.id} className="cross-fade">
                    <p className="mb-2 text-sm text-slate">{exchange.question}</p>
                    <AnswerBlock response={exchange.response} />
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                      <ToolTrace toolCalls={exchange.response.tool_calls} />
                      <TraceLink traceId={exchange.response.trace_id} />
                    </div>
                  </article>
                ))}
              </div>
            </section>

            <aside className="w-full shrink-0 min-[1100px]:w-[380px]">
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate">
                Evidence — rows the agent read
              </h2>
              <EvidencePanel
                citations={latest?.response.citations ?? []}
                rows={liveRows}
                toolCalls={liveTools}
                streaming={busy}
              />
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
