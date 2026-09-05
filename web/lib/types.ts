/**
 * Mirrors the Pydantic contract in app/agent/schemas.py exactly.
 *
 * If a field is added there it must be added here, or the UI will silently drop it.
 */

export type Confidence = "high" | "medium" | "low";

export type PersonaKey = "mf_analyst" | "equity_analyst" | "pe_analyst";

export type SectorKey = "tech" | "retail" | "manufacturing" | "logistics";

export interface Citation {
  ticker: string;
  company_name: string;
  fields_used: string[];
  /** The exact retrieved value per field. `null` means the dataset has no value. */
  values: Record<string, number | string | null>;
  source: string;
  as_of: string;
}

export interface ToolCallRecord {
  name: string;
  arguments: Record<string, unknown>;
  row_count: number;
  error: string | null;
  tool_call_id: string;
}

export interface AgentResponse {
  answer: string;
  key_points: string[];
  companies_referenced: string[];
  citations: Citation[];
  caveats: string[];
  out_of_scope: boolean;

  persona: string;
  persona_lens: string;
  sector: string;
  confidence: Confidence;
  confidence_reason: string;
  data_as_of: string | null;
  tools_called: string[];
  tool_calls: ToolCallRecord[];
  guard_flags: string[];
  llm_provider: string;
  trace_id: string | null;
  latency_ms: number;
}

export interface PersonaInfo {
  key: PersonaKey;
  name: string;
  lens: string;
  priority_fields: string[];
}

export interface SectorInfo {
  key: SectorKey;
  label: string;
  description: string;
  company_count: number;
  latest_snapshot: string | null;
}

/** A row of retrieved evidence, as it arrives on the `evidence` stream event. */
export type EvidenceRow = Record<string, number | string | null>;

/**
 * Events emitted by `POST /v1/query/stream`.
 *
 * The answer does NOT arrive token by token — it is a structured object, and a
 * half-parsed schema is not a partial answer. What the stream does guarantee is
 * ordering: node progress, then the retrieved evidence, then the complete response,
 * then a terminal `done`. Evidence arriving before the answer is the point.
 */
export type StreamEvent =
  | { type: "progress"; node: string }
  | { type: "evidence"; rows: EvidenceRow[]; tool_calls: ToolCallRecord[] }
  | { type: "response"; response: AgentResponse }
  | { type: "error"; detail: string }
  | { type: "done" };

/** Graph node names, mapped to what the user should be told is happening. */
export const NODE_LABELS: Record<string, string> = {
  validate: "Starting",
  guard_input: "Checking the question",
  plan: "Deciding what to look up",
  tools: "Querying the database over MCP",
  record_tools: "Reading the rows",
  verify_grounding: "Verifying it retrieved evidence",
  compose: "Reasoning as the analyst",
  guard_output: "Checking every figure against the data",
  refuse: "Declining",
  refuse_ungrounded: "Declining — nothing retrieved",
};
