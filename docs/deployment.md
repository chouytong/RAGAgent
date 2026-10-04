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

Only YAML `model` fields expand nonsecret environment names ending in `_MODEL`
(for example `${SUPERVISOR_MODEL:-gpt-4.1-mini-2025-04-14}`). Names containing
secret/key/token/password/credential components are rejected. Other fields,
including `api_base`, do not expand templates; the mapping API accepts resolved
model names only. Keys are looked up separately using `api_key_env` at inference.
Default chat mappings use `gpt-4.1-mini-2025-04-14`; providers may still vary their
behavior, and returned model/system identities are recorded when supplied.

The API rejects foreign/invalid Host headers. Browser write requests carrying
Origin must match the request's scheme, local hostname and port. Command-line
requests without Origin remain supported. Bundled nginx and Vite preserve Host
so same-origin requests through their proxy pass this check; nginx rejects foreign
hosts. This is local browser protection, not authentication for shared deployment.

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
`EMBEDDING_REVISION` and `RERANKER_REVISION` accept local Hub revisions. On first
identity/inference use, a tag, branch or omitted revision resolves to a real
immutable Hub commit SHA and is frozen for that adapter; loading uses the SHA.
For reproducible later processes, copy the recorded SHA into those variables.
An immutable SHA avoids Hub revision lookup, but weights must still be cached
or downloadable. Local directory models instead hash all model artifacts and
reject changes to that directory during adapter use. Adapter construction and
infrastructure health do not download weights or guarantee their availability.
Hosted embedding identity also includes its normalized API endpoint
and optional embedding revision. For a hosted service, `EMBEDDING_REVISION` is an
operator-declared index label: it does not send a revision parameter or change
the service's weights. The versioned hosted fingerprint and `local:v2` fingerprint
require reembedding existing older vectors. The local identity now includes the
resolved SHA or directory artifact hash. API-base URLs must not contain
credentials, query parameters or a fragment. A remote endpoint changing weights without changing its configured
identity still requires an operator-initiated reindex.

Rebuild an indexed paper or all indexed papers after configuring the desired
embedding identity and keeping its dimension compatible with the schema:

```bash
docker compose exec api python -m ragagent.reindex --paper-id PAPER_UUID
docker compose exec api python -m ragagent.reindex --all-indexed --batch-size 32
# With host-specific DATABASE_URL and the optional model dependencies installed:
uv run python -m ragagent.reindex --all-indexed
```

The mutually exclusive selectors are required; batch size is 1–256. Each paper
is locked and rebuilt in its own transaction. Failures roll back that paper and
stop the command with a safe error code; earlier papers remain committed. Chunk
IDs, original text, entities and citation IDs remain intact. Hosted embedding
rebuilds may incur charges. Successful paper output and failure usage output
report `usage_scope=current_attempt_cumulative`: usage accumulates from the start
of this CLI invocation, so do not sum every emitted snapshot. Accounting is
printed to stdout, not stored in a Run or persistent billing ledger. Capture stdout;
process termination before output or lost stdout can leave unrecoverable usage.
This command only reembeds existing chunks: new chunking/parser behavior requires
reingestion and new annotation labels, not reembedding.
The current upload API deduplicates an existing PDF by checksum, and retrying an
indexed paper does not parse it again. For parser/chunk changes, rebuild original
PDFs in a separate fresh corpus/database and regenerate relevance labels; retain
the old corpus and reports for provenance. No in-place rechunking endpoint is
provided.

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
Migration `0003` adds arXiv family/version and source status. Existing explicit
`vN` IDs keep that version; old unversioned imports remain version-unknown. It
does not guess versions or withdrawal status from old PDF hashes. Different
arXiv versions may share PDF bytes; uploaded PDFs retain hash deduplication.

New unversioned arXiv imports resolve and store the current official `vN` ID;
reimporting an unversioned ID checks the current version instead of reusing the
old unversioned record. Versions are separate papers, without automatic update
polling. `source_status` defaults to `unknown`, which means unverified, not active.
After checking the official source, record status explicitly:

```bash
curl -X PATCH -H 'Content-Type: application/json' \
  -d '{"source_status":"withdrawn"}' http://localhost:8000/api/papers/PAPER_UUID
```

Allowed values are `unknown`, `active`, `withdrawn`, `retracted`. Dense and lexical
search exclude the last two. There is no automatic withdrawal/retraction feed;
an operator must verify status, and old completed reports do not update themselves.

`CHUNK_OVERLAP_TOKENS` must be smaller than `CHUNK_TARGET_TOKENS`; invalid
combinations fail settings validation at startup rather than during ingestion.
The target is a lexical chunking guideline, not a provider context limit:
formulas, unknown-format tables and indivisible long rows may exceed it.

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
- Partial evaluation: download the terminal Run's artifacts even if Run status is `failed`. Completed case checkpoints can be reused with the same dataset and `resume_run_id`; changed source/configuration/corpus rejects resumption. See [evaluation](evaluation.md). The interrupted in-progress case must run again.
- `model_revision_resolution_failed`: configure an approved immutable Hub SHA or a complete local model directory; revision lookup needs network when no SHA is configured.
- `local_model_artifacts_changed`: restore immutable directory contents or construct new adapters and reembed affected papers; do not change model artifacts while a process uses them.
- Model downloads blocked: deployment needs approved Hugging Face/arXiv/provider network access, or pre-cached models. Restricted cloud environments can verify unit/DB/Compose contracts without claiming weight-backed inference success.
- SSE proxy buffering: bundled nginx disables buffering; other proxies must preserve event-stream and allow long read timeouts.
- OOM during parsing: reduce concurrent jobs, provision more RAM; RQ worker executes one job at a time by default.

Reconciliation runs in the API lifespan, not the RQ child, so it continues while
a worker is down as long as the API and PostgreSQL remain available. The current
queued bound is 1800 seconds. Ordinary jobs retain a 1800-second RQ timeout;
evaluation batches use `EVALUATION_TIMEOUT_SECONDS` (default 7200, range
1800–86400). The selected timeout freezes in the Run request at dispatch, and
reconciliation applies it plus 120 seconds grace. RQ and reconciliation use the
same timeout. Increase the evaluation bound or use a smaller dataset according
to an explicit runtime/cost budget; this is not a provider spending cap.
Worker Run usage snapshots persist before and after paid calls, including hosted
embeddings; evaluation artifacts checkpoint those updates too. Pending/interrupted
calls retain unknown cost and returned identities when known. Synchronous search
and provider connectivity tests return per-request usage in responses only; their
process/response loss is not recoverable from Runs. Reindex accounting is CLI
stdout only. These records supplement, rather than replace, provider invoices.

Managed cloud builds can use `-f compose.yaml -f compose.cloud.yaml` to mount the
session CA at build/runtime while retaining proxy routing and TLS verification.
The optional overlay is not required for ordinary local Docker deployment.

This release is a trusted local single-user application. Public/multi-tenant
hosting, authorization, encrypted backups and automatic graph checkpoint resumption
are outside v1; do not expose loopback ports publicly without implementing them.
