import { API_URL, ApiError } from "@/lib/api";

/**
 * A failure, named — with the command that fixes it.
 *
 * "Something went wrong" costs the reader a debugging session. Because this app is a
 * three-process system (Next, FastAPI, the MCP server), the useful thing an error can
 * say is *which* process is not answering and how to start it, so every branch below
 * resolves to a specific cause rather than a generic apology.
 */
export interface ErrorStateProps {
  error: unknown;
}

interface Diagnosis {
  /** What failed, in plain words. */
  title: string;
  /** What to do about it. */
  fix: string;
  /** A shell command that performs the fix, when one exists. */
  command?: string;
  /** The server's own message, shown as supporting detail rather than as the headline. */
  detail?: string;
  /** Accepted values, when the API rejected a persona or sector. */
  values?: string[];
}

/** A cause that can be recognised from the server's own wording alone. */
type NamedCause = "mcp" | "llm-key";

/**
 * Recover the cause from the message text.
 *
 * Necessary because the same failure reaches the UI with two different shapes. A
 * blocking `POST /v1/query` surfaces an unreachable MCP server as a 503, but the
 * streaming endpoint commits to `200 OK` with its first byte, so it can only report
 * the same failure as an in-band `error` event — which arrives here as an ordinary
 * `Error`, status and all. Matching the wording is what keeps the two paths giving
 * the reader the same instruction.
 */
function namedCause(message: string): NamedCause | null {
  if (/mcp server/i.test(message)) return "mcp";
  if (/api[_ ]key|llm provider/i.test(message)) return "llm-key";
  return null;
}

const MCP_DOWN: Omit<Diagnosis, "detail"> = {
  title: "Data service unreachable.",
  fix: "Start the MCP server on port 8765, then ask again.",
  command: "python -m app.mcp_server.server",
};

const NO_LLM_KEY: Omit<Diagnosis, "detail"> = {
  title: "No language model is configured.",
  fix: "Set GOOGLE_API_KEY in .env (GROQ_API_KEY is an optional fallback) and restart the API.",
};

function diagnoseNamedCause(cause: NamedCause, detail: string): Diagnosis {
  return { ...(cause === "mcp" ? MCP_DOWN : NO_LLM_KEY), detail };
}

function diagnose(error: unknown): Diagnosis {
  if (error instanceof ApiError) return diagnoseApiError(error);

  // fetch() rejects with a TypeError when the request never reached a server — a dead
  // port, a DNS failure or a CORS rejection. That means the API itself is down.
  if (error instanceof TypeError) {
    return {
      title: `Can't reach the API at ${API_URL}.`,
      fix: "Start the FastAPI service, then ask again.",
      command: "uvicorn app.api.main:app --port 8000",
      detail: error.message,
    };
  }

  if (error instanceof DOMException && error.name === "AbortError") {
    return {
      title: "The request was cancelled.",
      fix: "Ask again when you're ready.",
    };
  }

  const message =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";

  const cause = namedCause(message);
  if (cause) return diagnoseNamedCause(cause, message);

  return {
    title: "The request failed before an answer arrived.",
    fix: "Try again. If it repeats, check the terminal running the API for a traceback.",
    detail: message || undefined,
  };
}

function diagnoseApiError(error: ApiError): Diagnosis {
  switch (true) {
    // 422 is also what FastAPI returns for a malformed body — an over-long question,
    // say — and that one has nothing to do with the registry, so the headline only
    // blames the persona or sector when the body actually listed valid values.
    case error.status === 422:
      return error.validValues?.length
        ? {
            title: "The API rejected that persona or sector.",
            fix: "Choose one of the values it accepts:",
            detail: error.message,
            values: error.validValues,
          }
        : {
            title: "The API rejected that request.",
            fix: "Adjust the question and try again.",
            detail: error.message,
          };

    // 503 covers both service dependencies: an incomplete MCP handshake, and a missing
    // LLM key. Both are availability problems rather than bad requests, so the server
    // cannot separate them by status — its wording is the only discriminator, and
    // guessing wrong here sends the reader to restart the wrong process.
    case error.status === 503:
      return diagnoseNamedCause(namedCause(error.message) ?? "mcp", error.message);

    // The API's own per-IP sliding window, not a provider quota: a provider rate limit
    // is retried internally and never reaches the client as a 429.
    case error.status === 429:
      return {
        title: "Too many requests.",
        fix: "This API caps queries per minute from one address. Wait a moment, then ask again.",
        detail: error.message,
      };

    case error.status === 404:
      return {
        title: `The API at ${API_URL} has no such endpoint.`,
        fix: "The service is running but does not expose this route — check that the web app and the API are from the same revision.",
        detail: error.message,
      };

    case error.status >= 500:
      return {
        title: "The agent failed part-way through the request.",
        fix: "Check the terminal running the API — the traceback is logged there with the request id.",
        detail: error.message,
      };

    default:
      return {
        title: `The API refused the request (${error.status}).`,
        fix: "Adjust the request and try again.",
        detail: error.message,
      };
  }
}

export default function ErrorState({ error }: ErrorStateProps) {
  const { title, fix, command, detail, values } = diagnose(error);

  return (
    <div role="alert" className="rounded border border-rule bg-surface p-4">
      <h2 className="text-base font-medium text-ink">{title}</h2>
      <p className="mt-1 text-sm text-slate">{fix}</p>

      {values && values.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {values.map((value) => (
            <li
              key={value}
              className="rounded border border-rule bg-field px-2 py-0.5 font-mono text-xs text-ink"
            >
              {value}
            </li>
          ))}
        </ul>
      )}

      {command && (
        <pre className="mt-3 overflow-x-auto rounded border border-rule bg-field px-3 py-2 font-mono text-xs text-ink">
          <code>{command}</code>
        </pre>
      )}

      {/* The server's own wording, kept subordinate — and never a stack trace. */}
      {detail && detail !== title && (
        <p className="mt-3 border-t border-rule pt-2 text-xs text-slate">{detail}</p>
      )}
    </div>
  );
}
