# API

OpenAPI: `/docs`; all payloads have Pydantic validation. Long jobs return HTTP
202 with run ID/status/trace_id. Fetch `/api/runs/{id}` or subscribe to
`/api/runs/{id}/events`; research/RAG aliases are supported. SSE `execution`
events include node, payload and timestamp; `done` contains the final Run.
Use `Last-Event-ID` or `?after=` to replay after reconnect. Drafts are not final
until run status=completed. Failure responses carry safe error codes.

## Conversations, messages and memory

The chat API wraps existing RAG/Research Run creation; it does not replace the
single-run `/api/rag/query` or `/api/research` endpoints. Conversation mode selects
the independent graph. New turns return HTTP 202 `TurnResponse` containing
`conversation`, `user_message`, `assistant_message` and `run`. Message responses
include a nullable embedded Run snapshot for status/result restoration.

| Method and route | Request/result |
|---|---|
| POST `/api/conversations` | `{mode: "rag"\|"research", title?: string}`; 201 Conversation. Default mode is rag and omitted title enables an automatic first-message title. |
| GET `/api/conversations` | Optional `mode`, `offset` and `limit` (1–200, default 50); updated-descending conversation list. Filtering applies before pagination. |
| GET `/api/conversations/{id}` | Conversation with title/mode/timestamps/metadata and nullable `active_run_id`. |
| PATCH `/api/conversations/{id}` | `{title}` (1–200 characters); renames and disables automatic title. Mode is not mutable. |
| DELETE `/api/conversations/{id}` | Deletes conversation, messages, associated Runs/events/dispatches, summary and memories. May delete during execution; a late worker cannot publish into deleted records. |
| POST `/api/conversations/{id}/clear` | No options; deletes the same contents while retaining an empty conversation and resetting its automatic title. Requires idle conversation. |
| GET `/api/conversations/{id}/messages` | Ordered ascending by unique ordinal; `offset`, `limit` (1–500, default 100). Messages expose role/content/status/Run association and timestamps. |
| POST `/api/conversations/{id}/messages` | `{content, client_request_id: UUID, filters?: MetadataFilter}`; atomic user + queued assistant + Run + dispatch creation. Content is 1–10,000 characters. |
| POST `/api/conversations/{id}/messages/{message_id}/retry` | `{client_request_id: UUID}`; retries only the latest failed/cancelled/insufficient assistant message, preserving the earlier attempt and original user. Creates a new assistant/Run. |
| GET `/api/conversations/{id}/summary` | Summary content, covered ordinal, version and source message IDs, or null. |
| DELETE `/api/conversations/{id}/summary` | Deletes the summary record; retained messages may generate a new summary later. Requires idle state. |
| GET `/api/conversations/{id}/memories` | Explicit memories in stable created/ID order. |
| POST `/api/conversations/{id}/memories` | `{kind, content, key?, filters?}`; 201 Memory. Kinds: goal, constraint, term, preference, task. Only constraint may have filters; content 1–4,000 characters, optional key 1–128; at most 100 records. Requires idle state. |
| DELETE `/api/conversations/{id}/memories/{memory_id}` | Deletes one explicit memory. Requires idle state. |
| DELETE `/api/conversations/{id}/memory` | Deletes all structured memories and summary, retains messages/Runs. Requires idle state. |
| POST `/api/runs/{run_id}/cancel` | Cancels a queued/running rag/research Run; returns its Run state. Database ownership is revoked before best-effort RQ stopping. Completed terminal results are not rewritten. |

A repeated send with the same client request UUID and identical content/filters
returns the existing turn; it does not charge a second Run. Reusing that UUID for
a changed payload or send/retry mismatch returns 409 `idempotency_key_conflict`.
A conversation with queued/running work rejects a new turn or memory/clear mutation
with 409 `conversation_busy`. The caller should retain its UUID through an ambiguous
HTTP failure and replay that submission before inventing a new logical turn. Retry
is a new attempt and may incur new inference charges.

Follow-up execution records `conversation_context` in Run results and
`context_prepared`/`contextualize` execution events. These include original and
standalone contextualized queries, context version/configuration, source IDs,
estimated input tokens and truncation information. Errors such as
`context_resolution_ambiguous`, `context_resolution_invalid`,
`context_filters_conflict` and `context_budget_exceeded` are explicit failures.
Messages/summaries/memories are untrusted conversational context and never citation
Evidence. Structured constraint filters are intersected with turn filters by code;
natural-language memory content alone does not create a hard retrieval predicate.

The worker persists only released answers/refusal/failure states into assistant
messages. Intermediate agent drafts remain execution details. Terminal message,
Run and final event transitions commit together; existing SSE cursor replay restores
progress after a connection or application restart. Cancellation/deletion prevent
late publication but cannot guarantee cancellation of an already sent provider
request or erase charges already incurred. API clear/delete does not delete papers,
independent evaluations, desktop document caches, downloaded files or backups.

No API-key value field exists in conversation contracts. Recognizable credential
patterns in conversation fields are rejected with a safe validation response;
pattern detection cannot identify every possible secret embedded in free text.

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
terminal RQ state, or exceeding the frozen job timeout plus 120-second
grace, become failed with `worker_interrupted`. A temporary Redis read error
alone does not mark a running job failed. These are current implementation
bounds: ordinary jobs use 1800 seconds; evaluation batches use
`EVALUATION_TIMEOUT_SECONDS` (default 7200, 1800–86400). Dispatch freezes the
selected timeout in the Run request for both RQ and reconciliation. Queue age
and grace remain fixed constants.

Terminal Run status and its final event commit in one transaction. The SSE
reader observes terminal status first, captures the final event watermark and
drains all pages through it before `done`. Replay is based on persisted events,
not browser memory or Redis pub/sub.

- POST `/api/papers/upload`: multipart file, title, authors (semicolon-separated), year, venue. Bounded PDF upload, content-hash deduplication. Concurrent uploads of the same content reuse one Paper/Run; the Paper, author links, Run and dispatch intent become visible atomically.
- POST `/api/papers/arxiv`: `{arxiv_id}` only; arbitrary URLs are rejected. The official Atom identity must resolve to a matching `vN` before a versioned PDF is downloaded; versions are separate source records.
- PATCH `/api/papers/{id}`: validated metadata corrections (title/authors/year/venue) and manual `source_status` (`unknown`, `active`, `withdrawn`, `retracted`). Status cannot be explicitly null; withdrawn/retracted sources are excluded from both search channels.
- GET `/api/papers` and `/api/papers/{id}`: metadata, parsing/index status, chunk count, `arxiv_family_id`, `arxiv_version` and `source_status`. Old unversioned imports retain null version; unknown status means unverified. List paging uses `offset` and `limit` (1–200); the UI pages through the list.
- GET `/api/papers/{id}/pdf` and `/chunks`: original PDF and exact chunk text for traceability/annotation. PDF disposition is `inline`; browser PDF support determines whether `#page=N` opens the requested page.
- POST `/api/papers/{id}/retry`: retry failed/queued ingestion after correcting runtime configuration.
- POST `/api/papers/{id}/chunks/{chunk_id}/entities`: list of `{name, entity_type}` manual occurrence annotations for dataset/method/metric filters. Annotation means occurrence, not scientific validity.
- POST `/api/search`: `{query, filters}` returns dense/lexical/fused/evidence separately, plus the request's `usage.embedding` delta and `usage_scope=current_request`. Search execution errors return a safe `error_code` with the same usage fields.
- POST `/api/rag/query`: `{query, filters}` → queued verified RAG job.
- POST `/api/research`: `{research_question, filters}` → queued Supervisor job.
- GET `/api/research/{id}` and `/events`: execution state/timeline/final report.
- GET/PUT `/api/providers`: nonsecret agent provider/model mapping. `api_key_env` is a runtime variable name, never a key value. PUT accepts resolved model names and rejects unresolved templates in model/base fields; YAML alone expands safe nonsecret `*_MODEL` variables in model fields.
- POST `/api/providers/test`: `{agent}` connectivity test via unified provider. This invokes the selected model and may incur API charges. Success and `ApplicationError` failure responses include `usage.chat` and `usage_scope=current_request`; failures include a safe `error_code`.

- POST `/api/evaluations/retrieval`, `/rag`, `/multi-agent`: `{dataset, resume_run_id?}` queues actual evaluation runners. Resumption creates a new Run, retaining completed case checkpoints only if dataset, source, models, workflow/retrieval/judge configuration and corpus snapshot match. A live or wrong-kind prior Run, missing artifact or mismatched identity is rejected.
- POST `/api/evaluations/conversation`: `{dataset, resume_run_id?}` with conversation cases, mode, seed messages/explicit memories and ordered labeled turns. Measures context resolution, evidence grounding, memory isolation and long-summary behavior using actual configured pipelines. Resume retains only fully completed conversations; an interrupted conversation reruns as a whole. See [evaluation](evaluation.md#conversational-rag-and-research).
- GET `/api/evaluations/{id}/results.json` and `/results.md`: provenance and measured outputs for completed or failed terminal Runs, including partial checkpoints. The artifact's evaluation status and case counts distinguish partial results from a completed benchmark.

Evaluation rejects unannotated/invalid relevance IDs. Ordinary generation cases
require expected-answer and aspect labels; explicitly labeled expected-refusal
cases follow the separate policy in [evaluation.md](evaluation.md). Semantic
judgments are model-based and record the actual judge configuration, final
workflow output, evidence snapshot and raw judgment.
Synchronous `/api/search` and `/api/providers/test` return usage in their response
only; they do not create Runs or persist a billing ledger. Process termination or
a lost response cannot recover their request accounting. Preserve these responses
and reconcile with provider billing when auditing synchronous costs.

Queued worker usage includes chat and hosted embeddings; Run results persist
current-attempt usage before dispatch and after accounting for each paid call.
Evaluation artifacts also checkpoint at each usage update, in addition to each
case result. Unknown/in-flight charges make complete totals unknown.
Resumed evaluation artifacts retain `previous_attempt_usage` separately from the
new attempt's cost, supplementing the last artifact with prior persisted Run usage
when available (`usage_source=persisted_run`). See [evaluation.md](evaluation.md)
for accounting scope.

Local deployment is single-user; bind frontend/API to localhost. Invalid/foreign
Host headers receive `untrusted_host`; unsafe browser methods carrying a foreign
Origin receive `untrusted_origin`. Origin must match scheme, local hostname and
port; command-line clients without Origin remain supported. Proxies must preserve
Host as the bundled nginx/Vite configurations do. These checks do not authenticate
clients. No key is
returned by provider settings. A public or shared deployment requires a separate
authentication/authorization design. Embedding/reranker configuration remains
independent runtime configuration; provider mapping saves affect new jobs.

The desktop renderer uses scoped native IPC rather than cross-origin browser
fetch. The Rust bridge accepts allowlisted relative API routes and validated
payloads, targets only `http://127.0.0.1:8000`, and disables proxies/redirects.
It does not forward renderer-supplied Host/Origin/Authorization or expose arbitrary
network URLs. Web clients remain same-origin; no permissive CORS rule is added.
