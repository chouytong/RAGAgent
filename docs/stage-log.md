# Stage verification log

## Stage 1 — reference and architecture review

Changed: .gitignore, LICENSE, AGENTS.md, docs/reference-review.md,
docs/references/snapshot.json, docs/architecture.md, docs/stage-log.md.

Research completed before business implementation. GitHub account verified via
explicit connector link for chouytong (requested email). Shell Git credentials
belong to another account and push returned 403; use the correct GitHub connector
for all writes. Do not change or expose credentials.

Checks: Markdown/table/manual link review and JSON parse. Formatter, Ruff, mypy
and pytest are invoked but have no business source/tests to check in this
research-only stage; no test pass is claimed for absent tests.

Limitations: upstream metadata is a point-in-time observation; missing LICENSE
means no code reuse. No corpus or empirical benchmark exists yet.

## Stage 2 — data and ingestion

Changed: pyproject.toml/uv.lock, domain document contracts, SQLAlchemy normalized
models, Alembic migration/environment, parser abstraction/Docling adapter,
structure chunker, ingestion/arXiv services, settings/errors, unit/integration
tests and data-model documentation.

Checks: Ruff format/lint, strict mypy (15 source files), three unit tests;
PostgreSQL 17 + pgvector migration upgrade/downgrade/upgrade and ingestion/FTS
integration checks. Core tests download no models and use no commercial API.

Limitations: chunk token counts are reproducible lexical counts, not model
BPE counts; mathematical extraction depends on Docling recognition; arXiv
network access and actual Docling model parsing require model/network-enabled
runtime and are not asserted by fixtures. First migration freezes 384 dimensions.

## Stage 3 — retrieval, providers and evidence

Changed: typed query/filter/evidence/claim schemas; filtered DenseRetriever and
PostgreSQL FTS LexicalRetriever; RRFusion/HybridRetriever; lazy cross encoder;
independent local/LiteLLM embedding adapters; unified chat/config/mock providers;
exact-span and semantic claim verifier; evidence gate; citation parser/renderer;
provider mapping config and retrieval documentation/tests.

Checks: Ruff format/lint, strict mypy; 13 unit/integration tests on real
PostgreSQL/pgvector. All metadata filters exercised together, and absent dataset
filter returns no dense or lexical rows. Citation/spans, verifier omissions,
evidence diversity and provider secret-name validation are tested without APIs.

Limitations: semantic verification is model-based; score thresholds require
calibration on human labels. Cross-encoder/model inference needs installed model
extras and weights; integration tests intentionally use injected deterministic
embeddings/reranker to verify real SQL independently of model quality.

## Stage 4 — typed LangGraph workflows

Changed: typed RAG/MultiAgent states and updates, structured plan/subtask/analysis
contracts, current StateGraph builders, Supervisor policy, query-expansion and
revision/refusal paths, deterministic report synthesis, graph/regression tests
and agent architecture documentation.

Checks: Ruff format/lint, strict mypy; 23 tests including known-query citation
regression, real PostgreSQL graph execution with scripted LLMs, all reviewer
routes, preserved user filters, retrieval exhaustion and iteration termination.

Limitations: Supervisor task/aspect quality and semantic verdicts depend on
configured models. Unsupported contradictions force bounded revision/refusal;
supported conflicting findings can be reported with evidence. Test scripts
validate execution contracts, not scientific reasoning quality.

## Stage 5 — jobs, API, SSE and UI

Changed: API routers/contracts for upload/arXiv/library/search/RAG/research/
providers, worker/RQ queue, durable event replay, trace logging, nonsecret config
updates, runtime adapter factory and React/TypeScript/Vite UI with five pages,
validated wire contracts and citation source dialogs. Added real DB API tests.

Checks: Ruff format/lint, strict mypy (40 source files), pytest with PostgreSQL,
frontend Prettier formatting/lint, TypeScript strict check and production build.

Limitations: trusted local single-user deployment; model caches/provider keys
must be configured for inference. Worker restart/resumption is explicit via retry;
SSE events persist but automatic graph checkpoint resumption is not claimed.
Evaluation UI has no canned numbers; actual runner/API added only in stage 6.

## Stage 6 — evaluation, deployment and final verification

Changed: real retrieval/RAG/multi-agent evaluation runners and API/artifact
downloads; annotation schema, 100 unannotated forms and explicitly synthetic
pipeline dataset/corpus tool; evaluation UI metrics; Dockerfiles, Compose,
optional cloud CA overlay, environment example, Makefile and GitHub Actions;
README/evaluation/deployment documentation. Hardened ingestion ownership and
worker interruption handling, indexing progress, section descendant filters,
empty-index retrieval, all structured factual-claim verification, bounded
Supervisor replanning, nonsecret error codes and UI event reconnection.

Checks on 2026-10-03: Ruff format/lint, strict mypy (49 source files), 48 passing
pytest unit/integration/regression tests with real PostgreSQL 17/pgvector;
Alembic schema check and downgrade/upgrade round trip on the test database;
frontend Prettier, TypeScript check and production build; git diff whitespace
check. No paid APIs or model downloads are needed by these core tests.

Latest Docker images built successfully with the documented cloud CA overlay;
Compose migration/API/worker/Redis/PostgreSQL/frontend startup passed. Actual
Redis/RQ execution, sanitized provider_key_missing failure, terminal SSE replay,
empty-index search without model loading, invalid evaluation corpus references
and frontend HTTP were verified. Chromium opened all five UI pages with no
script errors. Docling/embedding/reranker packages are installed in the image;
this is not a claim that weight-backed inference or PDF model parsing ran.
The backend installs dependencies and clears caches in one layer to avoid
duplicating large model dependencies on VFS Docker builders.

Limitations: no real 100-query human-labeled benchmark, no claimed improvement
metrics, no paid provider inference or downloaded-weight inference verified in
this restricted cloud environment. Actual arXiv import and model parsing require
approved network access/caches; configured provider credentials or local models
are required for chat. Semantic evaluation remains model-based and requires
human validation. Trusted local single-user deployment, English FTS, exact
vector search and explicit retry after worker failure remain V1 boundaries.
GitHub Actions configuration is committed; local checks above do not assert a
GitHub-hosted workflow result. The six review branches are sequential PRs and
are intentionally not merged automatically.


## Independent review repairs — 2026-10-04 (Asia/Shanghai)

The user authorized implementation after the independent read-only review.
Changes were uncommitted when first verified in the `phase-6-evaluation-deployment`
checkout at baseline commit `5677745e82702bb87786fa86f741e83ddf79d2ab`. No commit,
push, pull request or merge was performed during the implementation turn.
The user subsequently authorized GitHub submission. Earlier stage entries are
historical records and do not establish acceptance of these changes.

The actual backend/migration/dependency source hash is
`fb1fa6de8ea9c6eb8ab4ad9b649abc78bef8a377589a53df08f9557ec316ca73`.
It covers 59 files: `src/**/*.py`, migration Python/Mako files, `pyproject.toml`
and `uv.lock`. The workspace reports dirty=true; the runtime reports dirty=null
because Git is unavailable in the image. Both compute the same source hash.
This identifies the tested/deployed source, not an attached source bundle, and
excludes secrets, frontend files, data and generated artifacts.

### Repairs and evidence

- Gate filtering now controls the exact evidence supplied to generation and
  verification in both workflows. Missing/low rerank scores and invalid spans
  are rejected. Unknown inline citations and missing/duplicate/unknown verdicts
  fail closed; only structured, validated citation IDs are rendered. Reviewer
  and evaluation judge receive cited exact quotes, not uncited chunk prose.
- RAG retains accepted evidence across retries. Research uses the same score
  threshold and bounded pool; changed task content cannot inherit completion
  through a reused ID. Retrieval, revision and iteration limits remain explicit.
- Run and durable dispatch intent commit together. RQ uniqueness and locked
  PostgreSQL claims prevent duplicate execution after acknowledgement loss.
  Independent API reconciliation retries dispatch and terminates expired jobs;
  Redis outages do not incorrectly declare active jobs dead. Real killed/stopped
  RQ subprocesses persist Run and owned ingestion failures. Terminal state and
  final event commit atomically; SSE drains replay pages before done.
- PDF responses are inline. Concurrent uploads deduplicate Paper/Run, shared
  authors use conflict-safe insertion, and retry/metadata changes lock and
  refresh Paper state. Readiness verifies DB, Redis and worker registration;
  liveness remains separate.
- Heading occurrence IDs and JSON structural identities prevent separator
  collisions and repeated-heading merges. Frozen migration 0001 is unchanged.
  Revision 0002 preserves legacy source links, backfills pending dispatches and
  refuses a downgrade that would lose distinct sections with identical labels.
- Hosted embedding fingerprints and transport share a frozen normalized actual
  endpoint, including OpenAI SDK/environment defaults. Supported prefixes are
  openai/cohere/cohere_chat/voyage; other prefixes fail closed, and non-OpenAI
  services require an explicit endpoint. Optional revision identity, consistent
  dotenv/runtime lookup, bounded local adapter reuse and synchronized loading
  are implemented. Retrieval author lookups are batched per search.
- Unknown charges keep totals null while exposing known subtotals and unknown
  call counts. Failed calls and malformed empty-choice responses retain usage
  accounting. Mock providers remain test-only and are not production fallback.
- Evaluation judges actual final output/status and explicit expected-refusal
  labels. Wrong refusals score zero completeness. Artifacts preserve outputs,
  source evidence, gold labels, judge input/raw decisions and per-case usage.
  Configuration, actual endpoint/revisions/timeouts and workflow budgets freeze
  at start; source and corpus hashes identify inputs. Corpus changes between
  start/end checks abort the run. Retrieval evaluation does not mutate app K.
- UI preserves raw multiword/filter separators, pages beyond 50 papers, handles
  stale requests and restored SSE connections, distinguishes unsaved settings,
  and prevents repeated evaluation submission while a request is pending.
- Added 26-requirement reconstructed v1 acceptance baseline and five repair ADRs.
  These do not claim recovery of the absent historical MASTER_SPEC. README and
  architecture/API/retrieval/deployment/evaluation documentation reflect actual
  behavior and limitations. CI includes real Redis/RQ tests, browser behavior
  tests and the Compose failure-path/SSE smoke.

### Actual checks

- Full pytest suite on Python 3.12.14: **130 passed, no skips, 2 warnings**, final
  run 25.00 seconds. Integration tests used dedicated PostgreSQL 17/pgvector
  0.8.2 and Redis 7.4.2, not SQLite. They exercised real FTS/vector/filter SQL,
  graph routing, transaction/concurrency boundaries, acknowledgement loss,
  actual RQ child exit and stop, and terminal SSE interleaving/replay.
  The warnings are RQ's Python 3.12 multi-threaded fork deprecation warnings;
  the exit/stop assertions passed.
- Migration regression: real legacy Paper/Section/Chunk/Run data upgrades from
  0001, retains section/chunk links and dispatch intent, passes `alembic check`,
  rolls back a refused lossy downgrade, and completes downgrade/base/upgrade.
  Its probe database is separate from application/test data and is removed.
- Ruff check: passed. Ruff format check: 106 files already formatted. Strict
  mypy: 53 source files, no errors. `uv lock --check`: resolved 190 packages;
  existing locked package versions were not upgraded. `git diff --check` passed.
- Frontend `npm ci`, Prettier, strict TypeScript and production build passed.
  **6 Playwright browser tests passed**, including filter submission, library
  paging, settings save gating, citation source and native EventSource reconnect.
  The HTTP fixture actually received Last-Event-ID=7; its data/provider responses
  are explicitly MOCK, not proof of model inference.
- Unit-test configuration forces LiteLLM's bundled pricing metadata so an
  isolated SDK test run does not fetch a remote cost map at import time.
  The subsequent standalone unit suite passed: **85 tests in 7.62 seconds**.
- Final backend/frontend images built with the documented cloud CA overlay.
  Backend image: `33cb43c5ec06469a3fe83f57f4ba08f54fbf5264af6d924648055bda7514637a`.
  Compose startup/readiness passed and deployed schema is 0002. Container source
  hash matches the final workspace hash above. `python scripts/smoke.py` passed
  through nginx: readiness, frontend HTTP, empty-index search, real RQ missing-key
  failure and complete terminal SSE replay. This uses no mock production model.
- This environment's VFS Docker driver exhausted disk during one intermediate
  container recreation. Only this task's stopped containers, obsolete images
  and specific build-cache records were removed; named data volumes were kept.
  Final build/start/smoke then passed. Independent test containers and the exited
  one-off migration container were cleaned up; application services remain up.

Tests used the locked core/dev environment in `/tmp/ragagent-review-venv`, with
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src:.`, dedicated TEST_DATABASE_URL and
TEST_REDIS_URL, and pytest cache disabled. Network permission for the execution
sandbox was required to allow local sockets and async thread wakeups; unit tests
made no paid calls and downloaded no model weights. Container package installation
is not weight-backed inference. These local checks do not establish a hosted
GitHub Actions result.

### Compatibility and remaining verification

- Existing hosted embedding vectors need rebuilding for the v2 fingerprint;
  default local fingerprints remain compatible. Hosted revision is an operator
  index label, not remote weight pinning. Silent remote weight replacement still
  requires explicit operator action. Previously merged section occurrences cannot
  be reconstructed from existing rows; accurate recovery requires PDF reingestion.
- Real Docling PDF/OCR/table/equation fidelity, end-to-end PDF/model indexing,
  downloaded embedding/reranker inference, real arXiv import and successful
  configured-provider inference remain **UNVERIFIED**. Semantic verification and
  generation evaluation remain model-based and need independent human validation.
- No human-annotated 100-query benchmark, improvement result, production load/
  memory measurement, hosted CI result or comprehensive dependency/model-license
  security certification is claimed. Corpus hashes compare run boundaries, not
  a locked transactional snapshot or an archived complete corpus.
- Trusted local single-user operation, English FTS, exact vector search and
  explicit retry rather than checkpoint resumption remain v1 boundaries.

### GitHub submission — 2026-10-04

The user requested publishing the validated repairs to
`phase-6-evaluation-deployment` through the linked repository owner's GitHub
account. Backend/tests, frontend/deployment/CI and documentation are separate
reviewable commits. Publishing this branch updates its existing stacked PR;
the local verification results above remain distinct from hosted CI outcomes.

### Changed files

- `.env.example`
- `.github/workflows/ci.yml`
- `README.md`
- `compose.yaml`
- `docs/MASTER_SPEC.md`
- `docs/adr/0001-structural-section-identity.md`
- `docs/adr/0002-gated-evidence-and-citation-release.md`
- `docs/adr/0003-versioned-tasks-and-bounded-evidence.md`
- `docs/adr/0004-provider-and-index-identity.md`
- `docs/adr/0005-durable-dispatch-and-terminal-events.md`
- `docs/adr/README.md`
- `docs/agents.md`
- `docs/api.md`
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/deployment.md`
- `docs/evaluation.md`
- `docs/retrieval.md`
- `docs/stage-log.md`
- `evals/retrieval/annotation-template.json`
- `evals/retrieval/schema.json`
- `frontend/.gitignore`
- `frontend/.prettierignore`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/playwright.config.ts`
- `frontend/src/Evaluation.tsx`
- `frontend/src/Knowledge.tsx`
- `frontend/src/Settings.tsx`
- `frontend/src/Tasks.tsx`
- `frontend/src/api.ts`
- `frontend/src/components.tsx`
- `frontend/tests/ui.spec.ts`
- `migrations/env.py`
- `migrations/versions/0002_section_identity_and_job_dispatch.py`
- `pyproject.toml`
- `scripts/annotation_template.py`
- `scripts/smoke.py`
- `src/ragagent/api/app.py`
- `src/ragagent/api/dispatcher.py`
- `src/ragagent/api/evaluations.py`
- `src/ragagent/api/papers.py`
- `src/ragagent/api/providers.py`
- `src/ragagent/api/queue.py`
- `src/ragagent/api/runs.py`
- `src/ragagent/db/dispatch.py`
- `src/ragagent/db/models.py`
- `src/ragagent/domain/documents.py`
- `src/ragagent/evaluation/artifacts.py`
- `src/ragagent/evaluation/generation.py`
- `src/ragagent/evaluation/retrieval.py`
- `src/ragagent/evaluation/schema.py`
- `src/ragagent/graphs/rag.py`
- `src/ragagent/graphs/research.py`
- `src/ragagent/ingestion/chunker.py`
- `src/ragagent/ingestion/parser.py`
- `src/ragagent/ingestion/service.py`
- `src/ragagent/jobs.py`
- `src/ragagent/providers/chat.py`
- `src/ragagent/providers/config.py`
- `src/ragagent/providers/embedding.py`
- `src/ragagent/providers/environment.py`
- `src/ragagent/retrieval/evidence.py`
- `src/ragagent/retrieval/reranker.py`
- `src/ragagent/retrieval/service.py`
- `src/ragagent/runtime.py`
- `src/ragagent/settings.py`
- `src/ragagent/worker.py`
- `tests/integration/conftest.py`
- `tests/integration/test_api.py`
- `tests/integration/test_dispatch.py`
- `tests/integration/test_evaluation.py`
- `tests/integration/test_graph_execution.py`
- `tests/integration/test_ingestion.py`
- `tests/integration/test_migrations.py`
- `tests/integration/test_retrieval.py`
- `tests/integration/test_sse_race.py`
- `tests/integration/test_upload_concurrency.py`
- `tests/integration/test_worker.py`
- `tests/unit/conftest.py`
- `tests/unit/test_chunker.py`
- `tests/unit/test_embedding_endpoint.py`
- `tests/unit/test_evaluation.py`
- `tests/unit/test_generation_evaluation.py`
- `tests/unit/test_graphs.py`
- `tests/unit/test_parser.py`
- `tests/unit/test_reranker.py`
- `tests/unit/test_retrieval.py`
- `tests/unit/test_runtime_configuration.py`
- `uv.lock`


## Scientific review corrections — 2026-10-04

Baseline: `phase-6-evaluation-deployment` at
`a4495d8cbf3142c78a9159c7d87b0629be21a450`. The repair checkout is
`/workspace/RAGAgent-fixes`, local branch `fix-scientific-review`. The checks below
were completed locally before publication. The user subsequently authorized
publishing these repairs to `phase-6-evaluation-deployment` in three reviewable
commits: backend/tests, frontend and documentation. Publication does not include
merging or deployment. GitHub commit history records the published SHAs; hosted
CI outcomes remain separate from the local checks below. The earlier submission
record above describes the baseline history, not this repair pass.

### Review findings addressed

1. Provider YAML is parsed before safe model-only interpolation; secret variables
   and unresolved API templates are rejected. Host/Origin guards and preserved
   proxy Host protect local browser writes (`providers/config.py`, `api/security.py`).
2. Scientific numbers, including leading-dot decimals and Unicode exponent minus,
   remain atomic. Tables retain rows and independently located headers/captions;
   equations and unrecognized table layouts remain atomic (`ingestion/chunker.py`).
3. Every attached claim/evidence pair must receive explicit support, with missing,
   unknown and duplicated pairs failing closed (`retrieval/evidence.py`).
4. arXiv imports freeze canonical `vN`; same-byte versions remain separate.
   Migration 0003 retains unknown legacy versions and source status; both search
   channels exclude manually withdrawn/retracted sources (`ingestion/arxiv.py`,
   `worker.py`, `db/models.py`, `retrieval/service.py`, paper API/UI).
5. Reranking uses the original question/subtask; direct multi-query callers use
   all queries as fallback (`graphs/rag.py`, `graphs/research.py`, `retrieval/service.py`).
6. `python -m ragagent.reindex` atomically reembeds per paper while preserving
   chunk/citation IDs. A failed batch rolls back all changes for that paper;
   dimensions cannot silently change (`ingestion/reembed.py`, `reindex.py`).
7. Local Hub models freeze a real immutable revision before identity/loading;
   local directories hash artifacts and detect changes. Dated chat defaults and
   actual returned model/system identities are recorded (`providers/model_identity.py`).
8. Worker calls checkpoint unknown charges before dispatch and accounted usage
   afterward in a separate database transaction, without committing partial
   indexing. Reused adapters start a fresh Run ledger and release observers.
   Evaluation checkpoints per case and paid-call update, retains failure artifacts,
   resumes only compatible completed rows, and keeps prior-attempt costs separate.
   Frozen evaluation timeouts are shared by RQ and reconciliation (`worker.py`,
   `providers/chat.py`, `evaluation/*`, `jobs.py`, `api/queue.py`).
9. Hosted embedding tokens/costs join worker/evaluation totals; synchronous
   search and provider connectivity responses also return request-level usage.
   Unknown charges remain null rather than becoming zero (`providers/embedding.py`,
   `api/app.py`, `api/providers.py`).
10. Analyzer/verifier/judge inputs include exact quotes once, excluding duplicated
    main and auxiliary content (`retrieval/evidence.py:evidence_payload`).
11. Settings validate overlap versus target before jobs start (`settings.py`).

### Actual verification

- Locked Python dependencies installed with `uv sync --locked` into `.venv`;
  checks used that same environment directly, avoiding cache permission issues.
- `.venv/bin/ruff format .`, final `ruff format --check .` and `ruff check .`:
  passed, 125 Python files formatted.
- `.venv/bin/mypy src`: passed, 57 source files.
- Full `.venv/bin/pytest -q`: **271 passed**, including **60 real PostgreSQL/
  pgvector and Redis/RQ integration tests**; no skips or failures. Two existing
  RQ fork deprecation warnings occurred in process-interruption tests.
- Database migration `alembic upgrade head` reached 0003 in a disposable test
  database. Migration integration tests cover legacy upgrade, retained source
  versions and guarded downgrade.
- New/expanded tests verify numeric boundaries and source offsets, claim/citation
  support pairs, canonical versions/concurrent imports, metadata/status filters,
  multi-query reranking, model identity mocks, hosted fees and request deltas,
  atomic reembedding, rollback, paid failure/interruption checkpoints, observer
  lifetime, partial evaluation/resume and frozen timeout reconciliation.
- `npm ci`, `npm run check`, `npm run build`, `npm run lint`: passed.
  Playwright: **9 passed**, including source status/version, independent table
  context offsets and separate limitations. Browser tests use MOCK HTTP/SSE.
- `nginx:1.28.0-alpine nginx -t` with the actual nginx configuration: passed.
  `docker compose config --quiet` with synthetic example configuration: passed;
  the temporary `.env` copy was removed. No application Compose stack was started.
- `python -m ragagent.reindex --help` and `git diff --check`: passed.
- Model/provider tests use scripted mocks or an intercepted SDK transport with
  offline model/cost-map settings. Network permission was needed for local IPC,
  test PostgreSQL/Redis and Docker; no paid provider or model-weight download was
  used. Disposable test services and temporary config are not a deployment.

### Limits and required operator work

- Real Docling extraction of scientific PDFs, real embedding/reranker/model
  inference, provider billing totals, adversarial prompt resistance, human gold
  evaluation and the complete application image/deployment remain unverified.
  Passing mocks/contracts is not a scientific benchmark or release acceptance.
- The environment did not provide approved Hugging Face metadata/weight access;
  no default SHA was guessed. Cross-process reproduction requires the actual
  recorded SHA, cached weights/provider access and an annotated evaluation corpus.
- Existing local fingerprints require migration and explicit reembedding. Old
  chunks do not gain corrected numeric cuts or missing table/paragraph context
  from reembedding; rebuild from original PDFs with new relevance annotations.
  The upload API reuses matching checksums and has no in-place rechunking route;
  use a separate fresh corpus/database while retaining the original corpus.
- Source status stays unknown until manually verified. There is no automatic
  retraction feed, and old completed reports do not refresh after status edits.
- Synchronous search/provider-test usage exists in responses only; reindex
  usage exists in stdout only. Lost responses/stdout or process termination
  cannot recover those fees from the persistent worker/evaluation ledger.
- Evaluation resumes completed cases/modes, not interrupted graph internals;
  timeouts bound duration, not currency. Shared/public deployment authentication
  and backend non-root/container hardening are not implemented by this repair.

### Changed files

- `.env.example`
- `README.md`
- `config/agents.yaml`
- `docker/nginx.conf`
- `docs/adr/0006-review-corrections.md`
- `docs/adr/README.md`
- `docs/agents.md`
- `docs/api.md`
- `docs/data-model.md`
- `docs/deployment.md`
- `docs/evaluation.md`
- `docs/retrieval.md`
- `docs/stage-log.md`
- `frontend/src/Knowledge.tsx`
- `frontend/src/Tasks.tsx`
- `frontend/src/api.ts`
- `frontend/src/components.tsx`
- `frontend/tests/ui.spec.ts`
- `frontend/vite.config.ts`
- `migrations/versions/0003_paper_source_identity.py`
- `src/ragagent/api/app.py`
- `src/ragagent/api/evaluations.py`
- `src/ragagent/api/papers.py`
- `src/ragagent/api/providers.py`
- `src/ragagent/api/queue.py`
- `src/ragagent/api/schemas.py`
- `src/ragagent/api/security.py`
- `src/ragagent/db/models.py`
- `src/ragagent/domain/documents.py`
- `src/ragagent/domain/research.py`
- `src/ragagent/evaluation/artifacts.py`
- `src/ragagent/evaluation/generation.py`
- `src/ragagent/evaluation/retrieval.py`
- `src/ragagent/evaluation/schema.py`
- `src/ragagent/graphs/rag.py`
- `src/ragagent/graphs/research.py`
- `src/ragagent/graphs/state.py`
- `src/ragagent/ingestion/arxiv.py`
- `src/ragagent/ingestion/chunker.py`
- `src/ragagent/ingestion/parser.py`
- `src/ragagent/ingestion/reembed.py`
- `src/ragagent/ingestion/service.py`
- `src/ragagent/jobs.py`
- `src/ragagent/providers/chat.py`
- `src/ragagent/providers/config.py`
- `src/ragagent/providers/embedding.py`
- `src/ragagent/providers/model_identity.py`
- `src/ragagent/reindex.py`
- `src/ragagent/retrieval/evidence.py`
- `src/ragagent/retrieval/reranker.py`
- `src/ragagent/retrieval/service.py`
- `src/ragagent/settings.py`
- `src/ragagent/worker.py`
- `tests/integration/test_evaluation.py`
- `tests/integration/test_graph_execution.py`
- `tests/integration/test_migrations.py`
- `tests/integration/test_reembed.py`
- `tests/integration/test_source_identity.py`
- `tests/integration/test_usage_persistence.py`
- `tests/unit/test_api_accounting.py`
- `tests/unit/test_api_security.py`
- `tests/unit/test_arxiv_source.py`
- `tests/unit/test_chunker.py`
- `tests/unit/test_citation_pairs.py`
- `tests/unit/test_embedding_usage.py`
- `tests/unit/test_evaluation.py`
- `tests/unit/test_evaluation_accounting.py`
- `tests/unit/test_generation_evaluation.py`
- `tests/unit/test_graphs.py`
- `tests/unit/test_job_timeout.py`
- `tests/unit/test_model_identity.py`
- `tests/unit/test_multiquery_retrieval.py`
- `tests/unit/test_parser.py`
- `tests/unit/test_provider_config_security.py`
- `tests/unit/test_reranker.py`
- `tests/unit/test_retrieval.py`
- `tests/unit/test_runtime_configuration.py`


## Product upgrade: local conversations and desktop (2026-10-06)

Added formal Conversation/Message/Summary/Memory persistence, Alembic 0004,
idempotent conversation APIs, atomic worker/message terminal transitions, safe
cancellation and late-response usage accounting. Contextualization remains above
the separate evidence-first RAG/Research graphs: bounded recent context, lossy
local rolling extracts, explicit intersected filter memories and source-linked
query rewrites never become scientific Evidence.

The shared React UI now supports durable chats, Markdown/citations, folding
research details, memory management, paginated history, restart/SSE recovery and
retry/cancel. Tauri adds a scoped fixed-loopback Rust request/SSE/file bridge,
with bounded cancellation bookkeeping and native health/offline handling. The
new conversation evaluator executes actual context/graphs/retrieval/verification
and a separate judge; four dimensions and partial/resume accounting are exposed.

Final review repaired visible/submitted filter mismatch, failed draft release,
late desktop cancel capacity leakage, impossible minimum input budgets, historical
comparison-winner narrowing, credential-shaped values in context filters and
conversation evaluation payloads, and rewriting over an independently checkpointed
usage ledger. Added regressions retain the existing assertions.

### Actual validation

- `.venv/bin/ruff format --check .`, `ruff check .`, `mypy src`: passed.
- Full `pytest -q` with real PostgreSQL/pgvector and Redis/RQ: 461 passed
  (360 unit + 101 integration), 23.90 seconds; two upstream RQ fork deprecation
  warnings in deliberate real child-exit/stop tests. No skipped integration tests.
- `npm ci`, `npm run lint`, `check`, `build`: passed. System Chromium Playwright:
  26 passed (original 9 retained + 17 chat scenarios); transport tests: 8 passed.
- Cargo fmt/check/clippy with warnings denied, locked dependencies, Rust tests:
  7 passed; `npm run desktop:build`: release native binary built.
- Actual native `--check-backend`: local_backend_ready. Actual Xvfb/DBus GUI
  `--smoke-test`: desktop=true, root/loaded=true, backend-status ready, exit 0.
  Debian WebKit dependencies were extracted and relocated only in a temporary
  sysroot; system files and WebKit sandbox were not altered.
- Docker frontend/backend image builds and Compose configuration/migration/ready
  checks: passed. `python scripts/smoke.py` verifies real missing-key RQ failure,
  proxied terminal SSE, persisted conversation/message association, idempotency
  and explicit memory/history deletion. No paid inference or downloaded weights.
- A temporary 32 GB VFS validation environment filled during repeated image
  builds/container copies; test build cache/outdated test images were reclaimed
  and affected build checks rerun. No repository source or user data was deleted.

### Practical limitations

Scripted providers and synthetic corpus fixtures test the production mechanics,
not real-model scientific accuracy. Human gold datasets, genuine model inference
and measured quality/cost gains remain unverified. Windows/macOS packaging/signing,
physical operating-system IME behavior and OS PDF viewer interaction were not
tested. Context budgeting is a conservative byte estimate for rewrite input,
not an actual tokenizer reading or currency cap. Summary is lossy; deletion does
not erase backups or independent desktop PDF cache copies. Remote inference still
sends necessary text to the configured provider. The JS bundle has a size advisory.

The requested 13-part handover is in [product-upgrade-report.md](product-upgrade-report.md).

Published review branch: `feature/desktop-conversations`, stacked on
`phase-6-evaluation-deployment`. Remote backend commit
`0a4a073cbf671850986394cd72b82ced626183a5` and UI/desktop commit
`e0042c1c43c0b1f66f89a1aed5d584e1231a211a` have Git tree hashes matching the
corresponding fully tested local commits. The actual-model, real-paper native
multi-turn/restart/Research acceptance scenario remains unverified.

### Changed files

- `.dockerignore`
- `.env.example`
- `.github/workflows/desktop.yml`
- `README.md`
- `docs/adr/0007-local-conversations-and-desktop.md`
- `docs/adr/README.md`
- `docs/api.md`
- `docs/architecture.md`
- `docs/conversation-memory.md`
- `docs/data-model.md`
- `docs/deployment.md`
- `docs/evaluation.md`
- `docs/product-upgrade-report.md`
- `docs/stage-log.md`
- `evals/conversation/README.md`
- `evals/conversation/annotation-template.json`
- `evals/conversation/demo.json`
- `evals/conversation/schema.json`
- `frontend/.prettierignore`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/src-tauri/.gitignore`
- `frontend/src-tauri/Cargo.lock`
- `frontend/src-tauri/Cargo.toml`
- `frontend/src-tauri/build.rs`
- `frontend/src-tauri/capabilities/main.json`
- `frontend/src-tauri/icon.svg`
- `frontend/src-tauri/icons/128x128.png`
- `frontend/src-tauri/icons/128x128@2x.png`
- `frontend/src-tauri/icons/32x32.png`
- `frontend/src-tauri/icons/64x64.png`
- `frontend/src-tauri/icons/Square107x107Logo.png`
- `frontend/src-tauri/icons/Square142x142Logo.png`
- `frontend/src-tauri/icons/Square150x150Logo.png`
- `frontend/src-tauri/icons/Square284x284Logo.png`
- `frontend/src-tauri/icons/Square30x30Logo.png`
- `frontend/src-tauri/icons/Square310x310Logo.png`
- `frontend/src-tauri/icons/Square44x44Logo.png`
- `frontend/src-tauri/icons/Square71x71Logo.png`
- `frontend/src-tauri/icons/Square89x89Logo.png`
- `frontend/src-tauri/icons/StoreLogo.png`
- `frontend/src-tauri/icons/icon.icns`
- `frontend/src-tauri/icons/icon.ico`
- `frontend/src-tauri/icons/icon.png`
- `frontend/src-tauri/permissions/local-api.toml`
- `frontend/src-tauri/rust-toolchain.toml`
- `frontend/src-tauri/src/bridge.rs`
- `frontend/src-tauri/src/main.rs`
- `frontend/src-tauri/tauri.conf.json`
- `frontend/src/Evaluation.tsx`
- `frontend/src/Knowledge.tsx`
- `frontend/src/Memory.tsx`
- `frontend/src/Tasks.tsx`
- `frontend/src/api.ts`
- `frontend/src/components.tsx`
- `frontend/src/main.tsx`
- `frontend/src/style.css`
- `frontend/src/transport.ts`
- `frontend/tests/chat-fixtures.ts`
- `frontend/tests/chat.spec.ts`
- `frontend/tests/transport/transport.test.ts`
- `frontend/tests/ui.spec.ts`
- `frontend/vite.config.ts`
- `migrations/versions/0004_conversations_and_memory.py`
- `scripts/smoke.py`
- `src/ragagent/api/app.py`
- `src/ragagent/api/conversations.py`
- `src/ragagent/api/evaluations.py`
- `src/ragagent/api/queue.py`
- `src/ragagent/api/runs.py`
- `src/ragagent/conversations/context.py`
- `src/ragagent/conversations/service.py`
- `src/ragagent/db/models.py`
- `src/ragagent/domain/conversation.py`
- `src/ragagent/domain/conversation_context.py`
- `src/ragagent/evaluation/conversation.py`
- `src/ragagent/evaluation/conversation_schema.py`
- `src/ragagent/jobs.py`
- `src/ragagent/settings.py`
- `src/ragagent/worker.py`
- `tests/conftest.py`
- `tests/integration/test_conversation_evaluation.py`
- `tests/integration/test_conversation_migration.py`
- `tests/integration/test_conversation_worker.py`
- `tests/integration/test_conversations.py`
- `tests/integration/test_migrations.py`
- `tests/unit/test_conversation_context.py`
- `tests/unit/test_conversation_contracts.py`
- `tests/unit/test_conversation_evaluation.py`
