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
API `/api/health` checks process liveness; `/api/ready` also checks PostgreSQL,
Redis and a registered worker for the research queue. Compose's API healthcheck
uses `/api/ready`. Model downloads and successful inference are separate from
infrastructure readiness.
`docker compose down` preserves volumes. Removing volumes deletes your corpus;
back them up before doing so.

Add keys to runtime .env/secrets and configure agent providers in
`config/agents.yaml` or Settings. Different agents can use different providers.
The UI never accepts/displays key values. Restart API/worker after environment
changes; mapping edits affect new tasks. An empty/default-key deployment starts
normally, but paid chat inference requires credentials or local model mapping.
There is no automatic mock fallback.

Local commands read provider model variables and keys from the process
environment first, then `.env`; an explicitly empty process variable overrides
a file value. Dotenv secrets are not copied into global process environment.
Compose supplies its `env_file` values normally. Restart API/worker to apply
runtime settings changes consistently.

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
(e.g. openai/text-embedding-3-small) and its dimension. Supported hosted prefixes
are `openai`, `cohere`, `cohere_chat` and `voyage`; unknown prefixes fail closed
with `unsupported_embedding_provider`. Non-OpenAI prefixes require explicit
`EMBEDDING_API_BASE` or return `embedding_api_base_required`. OpenAI-compatible
embedding servers use `openai/<model>` and an explicit base.

For OpenAI, the effective endpoint is chosen at adapter construction in this
order: explicit `EMBEDDING_API_BASE`, already-loaded LiteLLM global `api_base`,
runtime `OPENAI_BASE_URL`, runtime `OPENAI_API_BASE`, then
`https://api.openai.com/v1`. The normalized endpoint is frozen and used for both
the index fingerprint and every SDK call; changing environment variables or SDK
globals later does not retarget an existing adapter. The default key variables
are `OPENAI_API_KEY`, `COHERE_API_KEY` (both Cohere prefixes) and `VOYAGE_API_KEY`;
`EMBEDDING_API_KEY_ENV` can select another runtime key variable.

Changing the embedding model, endpoint or revision identity requires reindexing;
changing dimensionality also requires migration. Mixed fingerprints are excluded
from dense search. No model weights download during core CI tests.
`EMBEDDING_REVISION` and `RERANKER_REVISION` optionally pin supported local model
revisions. Hosted embedding identity also includes its normalized API endpoint
and optional embedding revision. For a hosted service, `EMBEDDING_REVISION` is an
operator-declared index label: it does not send a revision parameter or change
the service's weights. This repair's versioned hosted fingerprint
requires reindexing existing hosted vectors; the default local fingerprint is
unchanged. API-base URLs must not contain credentials, query parameters or a
fragment. A remote endpoint changing weights without changing its configured
identity still requires an operator-initiated reindex.

Local models are cached within each API process by configuration, with serialized
first loading and bounded cache size. An RQ work subprocess has its own memory;
the cache does not preserve loaded models across worker jobs.

Manual migration: `docker compose run --rm migrate`. Do not modify the initial
frozen schema; create a new Alembic revision for changes.
The engineering-repair migration backfills section identities and durable
dispatch records for pending Runs. Existing section/chunk links are retained;
previously merged headings require original-PDF reingestion. Its downgrade
refuses to restore the old unique display-path constraint if new duplicate
display paths exist, preventing data loss rather than deleting sections.

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
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
```

Install `uv sync --extra parsing --extra models` for real parsing/inference.
Run `uv run uvicorn ragagent.api.app:app --reload` and `uv run python -m
ragagent.worker` against your local DB/Redis (set host-specific URLs).
Integration tests require TEST_DATABASE_URL and an Alembic-migrated pgvector DB.
Use a dedicated test database: fixtures truncate test records. The migration
probe creates and drops a separate temporary database, so the
`TEST_DATABASE_URL` role must also have the PostgreSQL `CREATEDB` attribute. Real RQ
failure/dispatch tests require `TEST_REDIS_URL` pointing to a dedicated Redis
instance or unused logical database; tests clear that test queue/database.
CI requires integration tests; a local skip is never a vector test pass.

For a fresh empty corpus using the default hosted supervisor without an API key,
the Compose smoke runs through the frontend proxy:

```bash
python scripts/smoke.py --base-url http://127.0.0.1:8080
```

It checks readiness, the frontend, empty-index search, a real RQ job ending with
`provider_key_missing`, and terminal SSE replay through nginx. It requires an
empty corpus and unconfigured OpenAI/Anthropic/DeepSeek supervisor; it does not
test successful chat, PDF parsing or embedding/reranker weight inference, and
never substitutes mock inference. CI runs it after Compose startup. It leaves
the failed smoke Run/events available for inspection and does not delete data.

## Troubleshooting

- `provider_key_missing`: configure runtime key or local provider. Health tests invoke a real model and may incur small charges.
- `local_embedding_failed` / `reranking_failed` / `docling_conversion_failed`: check model cache, weight access, package versions and PDF quality; retry ingestion after fixing environment. Errors are sanitized; execution trace ID identifies the job.
- Missing dense results: embedding fingerprint/dimension differs from indexed papers; reindex after configuration change.
- No entity-filter matches: annotate occurrences via chunks/entities API first. Names are not guessed from model memory.
- `insufficient_evidence`: import relevant literature, inspect filters/citations; retry budgets intentionally stop unsupported output.
- `queue_unavailable`: accepted Runs retain durable dispatch intent; the API coordinator retries every 15 seconds. Check Redis/worker connectivity. Queued jobs fail with `worker_unavailable` after 1800 seconds; ingestion can then be retried through `/api/papers/{id}/retry`.
- `worker_interrupted`: an abnormal RQ work-subprocess exit, missing/terminal queue job or running-time limit has failed the Run and its owned in-progress ingestion. Restore the worker and retry ingestion; partially executed RAG/research graphs are not checkpoint-resumed.
- Model downloads blocked: deployment needs approved Hugging Face/arXiv/provider network access, or pre-cached models. Restricted cloud environments can verify unit/DB/Compose contracts without claiming weight-backed inference success.
- SSE proxy buffering: bundled nginx disables buffering; other proxies must preserve event-stream and allow long read timeouts.
- OOM during parsing: reduce concurrent jobs, provision more RAM; RQ worker executes one job at a time by default.

Reconciliation runs in the API lifespan, not the RQ child, so it continues while
a worker is down as long as the API and PostgreSQL remain available. Current
fixed bounds are 1800 seconds queued and 1920 seconds running (1800-second RQ
timeout plus 120 seconds grace). They are not environment configuration options.

Managed cloud builds can use `-f compose.yaml -f compose.cloud.yaml` to mount the
session CA at build/runtime while retaining proxy routing and TLS verification.
The optional overlay is not required for ordinary local Docker deployment.

This release is a trusted local single-user application. Public/multi-tenant
hosting, authorization, encrypted backups and automatic checkpoint resumption
are outside v1; do not expose loopback ports publicly without implementing them.
