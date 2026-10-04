# API

OpenAPI: `/docs`; all payloads have Pydantic validation. Long jobs return HTTP
202 with run ID/status/trace_id. Fetch `/api/runs/{id}` or subscribe to
`/api/runs/{id}/events`; research/RAG aliases are supported. SSE `execution`
events include node, payload and timestamp; `done` contains the final Run.
Use `Last-Event-ID` or `?after=` to replay after reconnect. Drafts are not final
until run status=completed. Failure responses carry safe error codes.

GET `/api/health` is process liveness only. GET `/api/ready` verifies PostgreSQL
`SELECT 1`, Redis ping and a registered live RQ worker for the `research` queue.
It returns `{status: ready}` on success; unavailable infrastructure returns 503
`infrastructure_unavailable`, and no queue worker returns 503
`worker_unavailable`. Readiness does not check model weights, credentials or a
successful provider inference. Compose uses readiness for the API healthcheck.

Run creation and dispatch intent commit together. If the initial Redis send
fails, the request still returns 202 with durable queued state and
`queue_unavailable`; the API's background coordinator retries it. Transport
submissions use an atomic unique RQ job ID, and a database claim prevents
duplicate execution. The coordinator runs every 15 seconds while the API is
alive, independently of worker availability. Queued jobs older than 1800
seconds become failed with `worker_unavailable`. Running jobs with missing or
terminal RQ state, or exceeding the 1800-second job timeout plus 120-second
grace, become failed with `worker_interrupted`. A temporary Redis read error
alone does not mark a running job failed. These are current implementation
constants, not user configuration options.

Terminal Run status and its final event commit in one transaction. The SSE
reader observes terminal status first, captures the final event watermark and
drains all pages through it before `done`. Replay is based on persisted events,
not browser memory or Redis pub/sub.

- POST `/api/papers/upload`: multipart file, title, authors (semicolon-separated), year, venue. Bounded PDF upload, content-hash deduplication. Concurrent uploads of the same content reuse one Paper/Run; the Paper, author links, Run and dispatch intent become visible atomically.
- POST `/api/papers/arxiv`: `{arxiv_id}` only; arbitrary URLs are rejected.
- PATCH `/api/papers/{id}`: validated metadata corrections (title/authors/year/venue).
- GET `/api/papers` and `/api/papers/{id}`: metadata, parsing/index status, chunk count. List paging uses `offset` and `limit` (1–200); the UI pages through the list.
- GET `/api/papers/{id}/pdf` and `/chunks`: original PDF and exact chunk text for traceability/annotation. PDF disposition is `inline`; browser PDF support determines whether `#page=N` opens the requested page.
- POST `/api/papers/{id}/retry`: retry failed/queued ingestion after correcting runtime configuration.
- POST `/api/papers/{id}/chunks/{chunk_id}/entities`: list of `{name, entity_type}` manual occurrence annotations for dataset/method/metric filters. Annotation means occurrence, not scientific validity.
- POST `/api/search`: `{query, filters}` returns dense/lexical/fused/evidence separately.
- POST `/api/rag/query`: `{query, filters}` → queued verified RAG job.
- POST `/api/research`: `{research_question, filters}` → queued Supervisor job.
- GET `/api/research/{id}` and `/events`: execution state/timeline/final report.
- GET/PUT `/api/providers`: nonsecret agent provider/model mapping. Only environment variable names, never key values.
- POST `/api/providers/test`: `{agent}` connectivity test via unified provider. This invokes the selected model and may incur API charges.

- POST `/api/evaluations/retrieval`, `/rag`, `/multi-agent`: `{dataset}` queues actual evaluation runners.
- GET `/api/evaluations/{id}/results.json` and `/results.md`: provenance and measured outputs.

Evaluation rejects unannotated/invalid relevance IDs. Ordinary generation cases
require expected-answer and aspect labels; explicitly labeled expected-refusal
cases follow the separate policy in [evaluation.md](evaluation.md). Semantic
judgments are model-based and record the actual judge configuration, final
workflow output, evidence snapshot and raw judgment.

Local deployment is single-user; bind frontend/API to localhost. No key is
returned by provider settings. A public or shared deployment requires a separate
authentication/authorization design. Embedding/reranker configuration remains
independent runtime configuration; provider mapping saves affect new jobs.
