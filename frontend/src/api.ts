import { z } from "zod";
export const Paper = z.object({
  id: z.string(),
  title: z.string(),
  authors: z.array(z.string()),
  year: z.number().nullable(),
  venue: z.string().nullable(),
  status: z.string(),
  error_code: z.string().nullable(),
  chunk_count: z.number(),
});
export const Evidence = z.object({
  evidence_id: z.string(),
  paper: z.object({ paper_id: z.string(), title: z.string() }),
  chunk_id: z.string(),
  section_path: z.string(),
  page_start: z.number(),
  page_end: z.number(),
  quote: z.string(),
});
export type Evidence = z.infer<typeof Evidence>;
export const Result = z
  .object({
    answer: z.string().optional(),
    draft_report: z.string().optional(),
    research_plan: z.unknown().optional(),
    review_result: z.unknown().optional(),
    evidence_pool: z.array(Evidence).optional(),
    reranked_evidence: z.array(Evidence).optional(),
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
  const response = await fetch(path, {
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
  });
  if (!response.ok)
    throw new Error(
      `HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`,
    );
  return schema.parse(await response.json());
}
