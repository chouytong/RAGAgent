# Local deployment

Requires Git, Docker Engine/Desktop and Compose v2. Recommended: 8 GB RAM,
20 GB free disk (more for model caches and large corpora). Linux images use CPU
PyTorch to avoid an unnecessary CUDA runtime. ARM and offline operation require
compatible model packages/weights; Docker smoke verification uses amd64.

Until the stacked PRs are reviewed and merged, clone the complete review branch:

```bash
git clone --branch phase-6-evaluation-deployment https://github.com/chouytong/RAGAgent.git
cd RAGAgent
cp .env.example .env
docker compose up --build
```

After merge, normal `git clone` uses main. Open http://localhost:8080 and API docs
at http://localhost:8000/docs. Compose waits for DB/Redis health, runs Alembic and
starts API, worker and frontend. DB/Redis are internal; exposed ports bind loopback.
`docker compose down` preserves volumes. Removing volumes deletes your corpus;
back them up before doing so.

Add keys to runtime .env/secrets and configure agent providers in
`config/agents.yaml` or Settings. Different agents can use different providers.
The UI never accepts/displays key values. Restart API/worker after environment
changes; mapping edits affect new tasks. An empty/default-key deployment starts
normally, but paid chat inference requires credentials or local model mapping.
There is no automatic mock fallback.

```yaml
agents:
  supervisor: {provider: openai, model: '${SUPERVISOR_MODEL}'}
  retriever: {provider: deepseek, model: '${RETRIEVER_MODEL}'}
  analyst: {provider: anthropic, model: '${ANALYSIS_MODEL}'}
  reviewer: {provider: openai, model: '${REVIEWER_MODEL}'}
```

All-local chat: map each agent to `provider: ollama_chat`, model matching your
installed Ollama model and `api_base: http://host.docker.internal:11434`. Linux
users can run Ollama on the same Docker network or add a host gateway mapping.
Use `openai_compatible` plus api_base/api_key_env for vLLM or other compatible
servers. Compatibility with JSON mode must be verified by provider tests; failures
are explicit. Local model quality affects planning and verification.

Embeddings and reranker are independent from chat. Defaults download
all-MiniLM-L6-v2 and ms-marco-MiniLM-L-6-v2 weights on first use to named model
volume; Docling also downloads layout/OCR/table resources. Offline users must
prepopulate approved compatible caches/model paths. Review model-weight licenses.
To use hosted embeddings set EMBEDDING_BACKEND=litellm and a prefixed model
(e.g. openai/text-embedding-3-small), dimension and optional API base. Changing
embedding model/dimension requires explicit reindex/migration; mixed fingerprints
are excluded from dense search. No model weights download during core CI tests.

Manual migration: `docker compose run --rm migrate`. Do not modify the initial
frozen schema; create a new Alembic revision for changes.

## Development

```bash
uv sync
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest -q
npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run build
```

Install `uv sync --extra parsing --extra models` for real parsing/inference.
Run `uv run uvicorn ragagent.api.app:app --reload` and `uv run python -m
ragagent.worker` against your local DB/Redis (set host-specific URLs).
Integration tests require TEST_DATABASE_URL and an Alembic-migrated pgvector DB.
CI requires integration tests; a local skip is never a vector test pass.

## Troubleshooting

- `provider_key_missing`: configure runtime key or local provider. Health tests invoke a real model and may incur small charges.
- `local_embedding_failed` / `reranking_failed` / `docling_conversion_failed`: check model cache, weight access, package versions and PDF quality; retry ingestion after fixing environment. Errors are sanitized; execution trace ID identifies the job.
- Missing dense results: embedding fingerprint/dimension differs from indexed papers; reindex after configuration change.
- No entity-filter matches: annotate occurrences via chunks/entities API first. Names are not guessed from model memory.
- `insufficient_evidence`: import relevant literature, inspect filters/citations; retry budgets intentionally stop unsupported output.
- Queue unavailable: check `docker compose ps` and Redis/worker connectivity; ingestion can be retried via `/api/papers/{id}/retry`.
- Model downloads blocked: deployment needs approved Hugging Face/arXiv/provider network access, or pre-cached models. Restricted cloud environments can verify unit/DB/Compose contracts without claiming weight-backed inference success.
- SSE proxy buffering: bundled nginx disables buffering; other proxies must preserve event-stream and allow long read timeouts.
- OOM during parsing: reduce concurrent jobs, provision more RAM; RQ worker executes one job at a time by default.

Managed cloud builds can use `-f compose.yaml -f compose.cloud.yaml` to mount the
session CA at build/runtime while retaining proxy routing and TLS verification.
The optional overlay is not required for ordinary local Docker deployment.

This release is a trusted local single-user application. Public/multi-tenant
hosting, authorization, encrypted backups and automatic checkpoint resumption
are outside v1; do not expose loopback ports publicly without implementing them.
