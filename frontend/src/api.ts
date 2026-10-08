import { z } from "zod";
import { request } from "./transport";
import { errorMessage } from "./errors";
export const SourceStatus = z.enum([
  "unknown",
  "active",
  "withdrawn",
  "retracted",
]);
export type SourceStatus = z.infer<typeof SourceStatus>;
export const SourceMetadata = z.object({
  arxiv_id: z.string().nullable().default(null),
  arxiv_family_id: z.string().nullable().default(null),
  arxiv_version: z.number().int().positive().nullable().default(null),
  source_status: SourceStatus.default("unknown"),
});
export const Paper = z.object({
  ...SourceMetadata.shape,
  id: z.string(),
  title: z.string(),
  authors: z.array(z.string()),
  year: z.number().nullable(),
  venue: z.string().nullable(),
  status: z.string(),
  error_code: z.string().nullable(),
  chunk_count: z.number(),
});
export const SourceContext = z.object({
  source_id: z.string(),
  element_type: z.string(),
  section_path: z.array(z.string()),
  page_start: z.number(),
  page_end: z.number(),
  content: z.string(),
  quote: z.string(),
  span_start: z.number(),
  span_end: z.number(),
  source_offset: z.number().default(0),
});
export const SourceSpan = z.object({
  source_id: z.string(),
  span_start: z.number(),
  span_end: z.number(),
  chunk_start: z.number(),
  chunk_end: z.number(),
});
export const Evidence = z.object({
  evidence_id: z.string(),
  paper: SourceMetadata.extend({ paper_id: z.string(), title: z.string() }),
  chunk_id: z.string(),
  section_id: z.string().optional(),
  section_path: z.string(),
  page_start: z.number(),
  page_end: z.number(),
  quote: z.string(),
  content: z.string().optional(),
  span_start: z.number().optional(),
  span_end: z.number().optional(),
  source_context: z.array(SourceContext).default([]),
  source_spans: z.array(SourceSpan).default([]),
});
export type Evidence = z.infer<typeof Evidence>;
export const SupportingPair = z.object({
  claim_id: z.string(),
  evidence_id: z.string(),
  supporting_span_start: z.number().int().nullable().optional(),
  supporting_span_end: z.number().int().nullable().optional(),
});
export type SupportingPair = z.infer<typeof SupportingPair>;
export const Result = z
  .object({
    answer: z.string().optional(),
    draft_report: z.string().optional(),
    research_plan: z.unknown().optional(),
    review_result: z.unknown().optional(),
    limitations: z.array(z.string()).default([]),
    analysis_results: z
      .array(
        z
          .object({ limitations: z.array(z.string()).default([]) })
          .passthrough(),
      )
      .default([]),
    evidence_pool: z.array(Evidence).optional(),
    reranked_evidence: z.array(Evidence).optional(),
    citation_validation: z
      .object({ supported_pairs: z.array(SupportingPair).default([]) })
      .optional(),
  })
  .passthrough();
export const Run = z.object({
  id: z.string(),
  kind: z.string(),
  status: z.string(),
  trace_id: z.string(),
  error_code: z.string().nullable(),
  result: Result.nullable(),
});
export type Run = z.infer<typeof Run>;
/** Lightweight list/turn state. Scientific evidence stays in the Run detail API. */
export const RunSummary = Run.omit({ result: true });
export type RunSummary = z.infer<typeof RunSummary>;
export const Event = z.object({
  node: z.string(),
  payload: z.record(z.string(), z.unknown()),
  time: z.string(),
});
export type Event = z.infer<typeof Event>;
export const Model = z.object({
  provider: z.enum([
    "openai",
    "anthropic",
    "deepseek",
    "ollama_chat",
    "openai_compatible",
  ]),
  model: z.string(),
  api_base: z.string().nullable(),
  api_key_env: z.string().nullable(),
  key_configured: z.boolean().optional(),
});
export const Mapping = z.object({ agents: z.record(z.string(), Model) });
export type Mapping = z.infer<typeof Mapping>;
export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  body?: unknown,
  method = "POST",
  signal?: AbortSignal,
): Promise<T> {
  const response = await request(path, {
    signal,
    method: body === undefined ? "GET" : method,
    headers:
      body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  }).catch((error: unknown) => {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    const code =
      error instanceof Error && /^[a-z][a-z0-9_]{0,100}$/.test(error.message)
        ? error.message
        : "local_backend_unavailable";
    throw new Error(errorMessage(code));
  });
  if (!response.ok) {
    let code = "request_failed";
    try {
      const error = await response.json();
      if (typeof error.error_code === "string") code = error.error_code;
      else if (typeof error.detail === "string") code = error.detail;
    } catch {
      /* Do not display proxy HTML or unstructured exceptions. */
    }
    const identifier = response.headers.get("X-Request-ID");
    throw new Error(
      `HTTP ${response.status}: ${errorMessage(code)}${identifier && /^[0-9a-f-]{36}$/.test(identifier) ? ` · 请求 ${identifier}` : ""}`,
    );
  }
  try {
    return schema.parse(
      response.status === 204 ? undefined : await response.json(),
    );
  } catch {
    throw new Error(errorMessage("invalid_response"));
  }
}
export const ConversationMode = z.enum(["rag", "research"]);
export type ConversationMode = z.infer<typeof ConversationMode>;
export const Conversation = z.object({
  id: z.string(),
  title: z.string(),
  mode: ConversationMode,
  archived: z.boolean(),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
  updated_at: z.string(),
  active_run_id: z.string().nullable(),
});
export type Conversation = z.infer<typeof Conversation>;
export const Message = z.object({
  id: z.string(),
  conversation_id: z.string(),
  role: z.enum(["user", "assistant", "system"]),
  content: z.string(),
  ordinal: z.number().int(),
  run_id: z.string().nullable(),
  status: z.enum([
    "queued",
    "running",
    "completed",
    "insufficient_evidence",
    "failed",
    "cancelled",
  ]),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
  updated_at: z.string(),
  run: RunSummary.nullable(),
  retry_of_message_id: z.string().nullable().default(null),
  attempt_number: z.number().int().positive().default(1),
  is_effective: z.boolean().default(true),
});
export type Message = z.infer<typeof Message>;
export const Turn = z.object({
  conversation: Conversation,
  user_message: Message,
  assistant_message: Message,
  run: RunSummary,
});
export const Summary = z.object({
  conversation_id: z.string(),
  content: z.string(),
  through_ordinal: z.number().int(),
  version: z.number().int(),
  source_message_ids: z.array(z.string()),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Summary = z.infer<typeof Summary>;
export const ConversationState = z.object({
  version: z.number().int().positive(),
  through_ordinal: z.number().int(),
  goals: z.array(
    z.object({
      source_kind: z.string(),
      source_id: z.string(),
      content: z.string(),
    }),
  ),
  constraints: z.record(z.string(), z.unknown()),
  constraint_memory_ids: z.array(z.string()),
  resolved_entities: z.array(
    z.object({
      mention: z.string(),
      resolved_text: z.string(),
      source_kind: z.string(),
      source_id: z.string(),
    }),
  ),
  important_terms: z.array(
    z.object({
      source_kind: z.string(),
      source_id: z.string(),
      content: z.string(),
    }),
  ),
  open_questions: z.array(
    z.object({
      source_kind: z.string(),
      source_id: z.string(),
      content: z.string(),
    }),
  ),
  scientific_evidence: z.literal(false),
});
export type ConversationState = z.infer<typeof ConversationState>;
export const MemoryKind = z.enum([
  "goal",
  "constraint",
  "term",
  "preference",
  "task",
]);
export type MemoryKind = z.infer<typeof MemoryKind>;
export const Memory = z.object({
  id: z.string(),
  conversation_id: z.string(),
  kind: MemoryKind,
  key: z.string().nullable(),
  content: z.string(),
  filters: z.record(z.string(), z.unknown()).nullable(),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Memory = z.infer<typeof Memory>;
