# Contributor and agent instructions

## Boundaries
- `domain` owns Pydantic contracts. `db` owns SQLAlchemy persistence. `ingestion`, `retrieval`, `providers`, `graphs`, `evaluation`, `api` depend on contracts, not UI.
- Parsing, embedding, reranking and chat providers must expose protocols. Route provider SDK usage through `providers` only. Docling imports belong only in its parser adapter.
- Use current LangGraph StateGraph primitives. Never add langgraph-supervisor, Elasticsearch, OpenSearch, Neo4j, Milvus or Kubernetes without a reviewed architecture decision.
- Preserve paper → section → page → chunk → exact text provenance. Filter predicates apply to both dense and lexical retrieval.
- Never convert unsupported claims or failed verification into success. Enforce retry/revision limits in state, in addition to graph recursion limits.

## Checks and workflow
- Complete stages sequentially. Keep changes in reviewable commits/stacked PRs. Never merge a PR autonomously.
- Run `uv run ruff format .`, `uv run ruff check .`, `uv run mypy src`, `uv run pytest` for Python changes; `npm ci`, `npm run check`, `npm run build` for frontend changes.
- Unit tests require no paid APIs, model downloads, network or database. Use scripted mock providers and structured parser fixtures.
- PostgreSQL/pgvector integration tests are mandatory in CI. SQLite is not a substitute for vector/FTS verification.
- Tests must cover citation failures, filter isolation, retry exhaustion and supervisor routing, not merely happy paths.
- Record changed files, actual check outcomes and limitations in docs/stage-log.md.

## Truth and secrets
- Never invent functionality, benchmark data or evaluation gains. Synthetic data must say DEMO ONLY / NOT A BENCHMARK / NOT MANUALLY ANNOTATED.
- Keys come only from runtime environment/secrets, never config values, logs, DB or example config. Never print an environment dump or raw provider exceptions.
- Logs contain request/trace IDs and event names, not prompts, PDF text or credentials. Error responses use safe codes.
- Verify upstream licenses. Do not copy AGPL/GPL or unlicensed code into this MIT project. Check independent model-weight licenses.
- Pin dependencies with uv.lock/package-lock.json. Avoid unnecessary infrastructure and single-provider assumptions.
