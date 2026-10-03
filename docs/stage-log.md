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
