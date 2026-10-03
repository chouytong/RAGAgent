# API

OpenAPI: `/docs`; all payloads have Pydantic validation. Long jobs return HTTP
202 with run ID/status/trace_id. Fetch `/api/runs/{id}` or subscribe to
`/api/runs/{id}/events`; research/RAG aliases are supported. SSE `execution`
events include node, payload and timestamp; `done` contains the final Run.
Use `Last-Event-ID` or `?after=` to replay after reconnect. Drafts are not final
until run status=completed. Failure responses carry safe error codes.

- POST `/api/papers/upload`: multipart file, title, authors (semicolon-separated), year, venue. Bounded PDF upload, content-hash deduplication.
- POST `/api/papers/arxiv`: `{arxiv_id}` only; arbitrary URLs are rejected.
- GET `/api/papers` and `/api/papers/{id}`: metadata, parsing/index status, chunk count.
- GET `/api/papers/{id}/pdf` and `/chunks`: original PDF and exact chunk text for traceability/annotation.
- POST `/api/papers/{id}/retry`: retry failed/queued ingestion after correcting runtime configuration.
- POST `/api/papers/{id}/chunks/{chunk_id}/entities`: list of `{name, entity_type}` manual occurrence annotations for dataset/method/metric filters. Annotation means occurrence, not scientific validity.
- POST `/api/search`: `{query, filters}` returns dense/lexical/fused/evidence separately.
- POST `/api/rag/query`: `{query, filters}` → queued verified RAG job.
- POST `/api/research`: `{research_question, filters}` → queued Supervisor job.
- GET `/api/research/{id}` and `/events`: execution state/timeline/final report.
- GET/PUT `/api/providers`: nonsecret agent provider/model mapping. Only environment variable names, never key values.
- POST `/api/providers/test`: `{agent}` connectivity test via unified provider. This invokes the selected model and may incur API charges.

Evaluation routes are added in stage 6 after real evaluation runners exist.
The Evaluation UI is wired for those routes; it does not display fabricated
results during this intermediate stage.

Local deployment is single-user; bind frontend/API to localhost. No key is
returned by provider settings. A public or shared deployment requires a separate
authentication/authorization design. Embedding/reranker configuration remains
independent runtime configuration; provider mapping saves affect new jobs.
