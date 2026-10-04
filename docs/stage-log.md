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
