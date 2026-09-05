/**
 * The only module that talks to the FastAPI service.
 *
 * Everything the UI knows about the agent arrives through here, so error shapes and
 * the SSE protocol are handled in one place rather than in each component.
 */

import type {
  AgentResponse,
  EvidenceRow,
  PersonaInfo,
  SectorInfo,
  StreamEvent,
  ToolCallRecord,
} from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** An error the UI can render usefully, rather than a bare `Error`. */
export class ApiError extends Error {
  readonly status: number;
  /** Valid values, when the API rejected a persona or sector. */
  readonly validValues?: string[];

  constructor(message: string, status: number, validValues?: string[]) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.validValues = validValues;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = `Request failed (${response.status})`;
  let validValues: string[] | undefined;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") detail = body.detail;
    validValues = body?.valid_sectors ?? body?.valid_personas;
  } catch {
    // A non-JSON body (a proxy error page, say) leaves the default message.
  }
  return new ApiError(detail, response.status, validValues);
}

export async function getPersonas(): Promise<PersonaInfo[]> {
  const response = await fetch(`${API_URL}/v1/personas`, { cache: "no-store" });
  if (!response.ok) throw await parseError(response);
  return response.json();
}

export async function getSectors(): Promise<SectorInfo[]> {
  const response = await fetch(`${API_URL}/v1/sectors`, { cache: "no-store" });
  if (!response.ok) throw await parseError(response);
  return response.json();
}

export interface QueryRequest {
  query: string;
  persona: string;
  sector: string;
  session_id?: string;
}

/** Blocking request. Used by /compare, which needs three complete answers. */
export async function postQuery(request: QueryRequest): Promise<AgentResponse> {
  const response = await fetch(`${API_URL}/v1/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw await parseError(response);
  return response.json();
}

/**
 * Streaming request, yielding typed events as they arrive.
 *
 * Uses fetch rather than EventSource because the endpoint is a POST with a JSON
 * body, which EventSource cannot express. Parsing is done by hand against the SSE
 * wire format: blank-line-separated records of `event:` and `data:` lines.
 *
 * A 422 is still delivered as a normal HTTP error, because the API validates the
 * persona and sector *before* the response is committed to 200 — so an invalid
 * request never reaches the stream at all.
 */
export async function* streamQuery(
  request: QueryRequest,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch(`${API_URL}/v1/query/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });

  if (!response.ok) throw await parseError(response);
  if (!response.body) throw new ApiError("The server sent no response body", 500);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE records are separated by a blank line. Anything after the last
      // separator is a partial record and stays in the buffer.
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const record = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        const parsed = parseRecord(record);
        if (parsed) yield parsed;
        separator = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseRecord(record: string): StreamEvent | null {
  let name = "";
  const dataLines: string[] = [];
  for (const line of record.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!name) return null;

  const raw = dataLines.join("\n");
  let payload: Record<string, unknown> = {};
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      return null;
    }
  }

  switch (name) {
    case "progress":
      return { type: "progress", node: String(payload.node ?? "") };
    case "evidence":
      return {
        type: "evidence",
        rows: (payload.rows as EvidenceRow[]) ?? [],
        tool_calls: (payload.tool_calls as ToolCallRecord[]) ?? [],
      };
    case "response":
      return { type: "response", response: payload as unknown as AgentResponse };
    case "error":
      return { type: "error", detail: String(payload.detail ?? "Unknown error") };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}
