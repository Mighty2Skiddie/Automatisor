"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

/**
 * The question input at the foot of the answer column.
 *
 * It owns the draft text and nothing else: the parent decides what a submitted
 * question means (which persona, which sector, whether a request is in flight), so
 * this component stays reusable by `/` and `/compare` alike.
 */
export interface QuestionBarProps {
  onSubmit: (query: string) => void;
  busy: boolean;
}

/**
 * Drawn from the README's "Things worth trying" table.
 *
 * The last one is deliberate: SpaceX is not in the dataset, and watching the agent
 * say so is the fastest demonstration that it is grounded rather than fluent.
 */
const EXAMPLES: ReadonlyArray<{ label: string; question: string }> = [
  {
    label: "Put money to work?",
    question: "Is this sector a good place to put money to work right now?",
  },
  {
    label: "Core holding vs. avoid",
    question: "Which would fit a long-term core holding versus a name to avoid?",
  },
  {
    label: "Margin profile",
    question:
      "Walk me through the margin profile — who's improving and who's under pressure?",
  },
  {
    label: "Ask about SpaceX",
    question: "What do you think about SpaceX?",
  },
];

export default function QuestionBar({ onSubmit, busy }: QuestionBarProps) {
  const [draft, setDraft] = useState("");
  // Rendered server-side as "Ctrl" and corrected after mount, because the platform is
  // only knowable in the browser and a hydration mismatch is not worth a glyph.
  const [modifier, setModifier] = useState("Ctrl");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (/Mac|iPhone|iPad|iPod/.test(navigator.userAgent)) setModifier("⌘");
  }, []);

  const focusInput = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.focus();
    // Caret to the end, so filling from a chip and typing on continues the sentence.
    const end = textarea.value.length;
    textarea.setSelectionRange(end, end);
  }, []);

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      focusInput();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusInput]);

  const submit = useCallback(() => {
    const query = draft.trim();
    if (!query || busy) return;
    onSubmit(query);
    // Cleared because the submitted question is echoed in the transcript above; leaving
    // it here would read as an unsent draft.
    setDraft("");
  }, [busy, draft, onSubmit]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
    }
  }

  function applyExample(question: string) {
    setDraft(question);
    focusInput();
  }

  const canSubmit = draft.trim().length > 0 && !busy;

  return (
    <form onSubmit={handleSubmit} aria-busy={busy} className="w-full">
      {/* Grouped and named, so the four buttons are not four unexplained tab stops. */}
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
            onClick={() => applyExample(example.question)}
            disabled={busy}
            title={example.question}
            className="rounded-full border border-rule bg-surface px-2.5 py-1 text-xs text-slate hover:border-slate hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {example.label}
          </button>
        ))}
      </div>

      <div className="rounded border border-rule bg-surface p-3">
        <label htmlFor="question-input" className="sr-only">
          Your question about the companies in this sector
        </label>
        <textarea
          id="question-input"
          name="query"
          ref={textareaRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          // readOnly rather than disabled: a disabled element cannot hold focus, so
          // freezing the box the instant the question is sent would throw the caret to
          // the top of the document and strand anyone working from the keyboard.
          readOnly={busy}
          rows={3}
          placeholder="Ask something about the companies in this sector…"
          className="w-full resize-none bg-transparent text-base text-ink placeholder:text-slate read-only:cursor-not-allowed read-only:opacity-60"
        />

        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-slate">
            {/*
              The separators stay in the accessible name: hiding them collapsed the two
              keys into one unreadable token ("CtrlEnter") for a screen reader.
            */}
            <kbd className="font-mono">{modifier}</kbd> + <kbd className="font-mono">Enter</kbd>{" "}
            to ask · <kbd className="font-mono">{modifier}</kbd> +{" "}
            <kbd className="font-mono">K</kbd> to focus
          </p>
          {/*
            aria-disabled rather than disabled, for the same reason as the textarea:
            clicking Ask must not delete the element the user is standing on. `submit`
            already refuses an empty or in-flight request, so the button being pressable
            costs nothing.
          */}
          <button
            type="submit"
            aria-disabled={!canSubmit}
            className={`min-w-[6rem] rounded bg-ink px-4 py-1.5 text-sm text-surface ${
              canSubmit ? "" : "cursor-not-allowed opacity-40"
            }`}
          >
            {busy ? "Asking…" : "Ask"}
          </button>
        </div>
      </div>
    </form>
  );
}
