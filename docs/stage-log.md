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
