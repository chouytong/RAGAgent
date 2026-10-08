# Local deployment

Requires Git, Docker Engine/Desktop and Compose v2. Recommended: 8 GB RAM,
20 GB free disk (more for model caches and large corpora). Linux images use CPU
PyTorch to avoid an unnecessary CUDA runtime. ARM and offline operation require
compatible model packages/weights; Docker smoke verification uses amd64.

Until the stacked PRs are reviewed and merged, use
[`fix/engineering-hardening`](https://github.com/chouytong/RAGAgent/tree/fix/engineering-hardening)
for the conversation/desktop upgrade, including migration `0005` and `src-tauri`.
It is based on the existing `phase-6-evaluation-deployment` RAG/Research baseline.

```bash
git clone --branch fix/engineering-hardening https://github.com/chouytong/RAGAgent.git
cd RAGAgent
# First checkout only; preserve an existing runtime .env.
cp .env.example .env
docker compose up --build
```

After merge, normal `git clone` uses main. Open http://localhost:8080 and API docs
at http://localhost:8000/docs. Compose waits for DB/Redis health, runs Alembic and
starts API, three dedicated workers and frontend. DB/Redis are internal; exposed ports bind loopback.
API `/api/health` checks process liveness; `/api/ready` also checks PostgreSQL,
Redis and a registered worker for the interactive queue. Compose's API healthcheck
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

Migration `0004` adds local conversations, messages, summaries and explicit
structured memories, and nullable conversation/idempotency fields on Runs. It
preserves existing papers/chunks/evidence, legacy Runs and execution history.
Existing deployments upgrade with Alembic; do not delete volumes or recreate the
database to add chat. Back up the local database/volumes and stop API/worker writes
while applying an upgrade:

```bash
docker compose stop api worker-interactive worker-ingestion worker-evaluation
docker compose build api worker-interactive worker-ingestion worker-evaluation migrate frontend
docker compose run --rm migrate
docker compose up -d
```

For a host development database, `uv run alembic upgrade head` applies the same
upgrade. The current head is `0005`. A downgrade from `0004` removes conversation
tables/data and associations; it is not a preservation mechanism for chat history.
Previously selected legacy last-Run browser state is not converted into a made-up
multi-turn conversation. Legacy Runs remain accessible through their existing API.

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
npm --prefix frontend run test:transport
npm --prefix frontend run desktop:check
npm --prefix frontend run desktop:test
npm --prefix frontend run desktop:build
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

## Desktop UI with local backend

Tauri 2 wraps the same React/Vite frontend in an independent application window.
The first-stage package contains the UI and a scoped Rust transport, not Python,
PostgreSQL, Redis, model weights or Docker. Start the existing local services
first; the application checks them and displays an unavailable/not-ready state
instead of a white screen. Closing the window stops its local subscriptions, not
the backend services or already queued work.

The desktop backend target is fixed at `http://127.0.0.1:8000`. Its Rust bridge
does not accept a user-supplied backend URL, use HTTP proxies or follow redirects.
Only scoped relative API routes/methods, bounded JSON/multipart bodies and Run SSE
subscriptions are allowed. The WebView has no remote network/iframe capability,
and external navigation/popups are denied. No FastAPI CORS/Origin relaxation is
required; existing local Host/same-origin protection remains intact for Web mode.
Keep Compose's `127.0.0.1` port bindings. The desktop window itself does not make
the backend safe for public or shared deployment.

Build prerequisites:

- Node 22.18+ or 24 and locked npm dependencies.
- Rust 1.90.0 from `frontend/src-tauri/rust-toolchain.toml`; Cargo.lock is committed.
- Linux: C/C++ build tools, pkg-config, GTK 3, WebKitGTK 4.1, OpenSSL and librsvg
  development libraries. On Debian/Ubuntu packages include `build-essential`,
  `pkg-config`, `libgtk-3-dev`, `libwebkit2gtk-4.1-dev`, `libssl-dev`, `librsvg2-dev`.
- Windows: Visual Studio C++ build tools and the WebView2 runtime.
- macOS: Xcode command-line developer tools and the platform WebView.

Start local infrastructure and the desktop development window:

```bash
cp .env.example .env  # only for a new checkout; preserve existing runtime settings
docker compose up -d --build
npm --prefix frontend ci
npm --prefix frontend run desktop:dev
```

`desktop:dev` runs Vite on `127.0.0.1:1420` and opens Tauri; it does not manually
open a browser. Configure runtime keys/local model mappings separately before
inference. Readiness only establishes DB/Redis/worker availability, not provider
credentials, model weights or answer quality.

```bash
npm --prefix frontend run desktop:check  # cargo check --locked
npm --prefix frontend run desktop:test   # cargo test --locked
npm --prefix frontend run desktop:build  # release native binary, no installer
npm --prefix frontend run desktop:bundle # platform installers; signing is separate
```

Without a custom Cargo target directory, the release executable is under
`frontend/src-tauri/target/release/` (`scientific-ragagent-desktop`, with `.exe` on
Windows). Start it to open the independent window. `--check-backend` checks the
real fixed `/api/health` and `/api/ready` endpoints without a GUI, exits nonzero
if unavailable and does not invoke a model. `--smoke-test` opens the window and
asserts that its rendered DOM loaded, then exits. GUI checks require a display
(or a supported Xvfb/WebKit setup); static/Rust/transport tests do not prove a
window was actually displayed. See [stage-log](stage-log.md) for actual Linux
checks and unresolved platform/GUI limits. Windows/macOS installers and signing
are not implied by a Linux binary build.

Citation PDFs and evaluation downloads use dedicated allowlisted local-resource
commands. After size/type validation they are cached privately under the platform
app-cache `documents` directory and opened by the OS default reader. Citation
paper/page information remains visible; an external PDF reader may not support
automatic page jumps. These cache files are separate from the knowledge base and
conversation database; remove them separately when deleting local copies. Each
document is limited to 128 MiB; the cache is bounded to 256 MiB and removes its
oldest managed documents when space is needed. The bridge does not expose an
arbitrary OS opener or filesystem path to the renderer.

## Web fallback and conversation configuration

The existing Compose Web UI stays at `http://localhost:8080`. For frontend
development, run `npm --prefix frontend run dev` and open
`http://127.0.0.1:5173`; Vite proxies `/api` to the local backend and preserves Host.
Both modes call the same Conversation/Message/Run APIs and can independently load
the same PostgreSQL history. No cloud conversation storage or second SQLite
database is introduced.

| Runtime setting | Default | Scope |
|---|---|---|
| `CONVERSATION_RECENT_MESSAGE_LIMIT` | 8 | 2–64 eligible recent messages, not rounds. |
| `CONVERSATION_CONTEXT_TOKEN_BUDGET` | 8192 | 4096–65536 conservative input units; one UTF-8 byte per estimated token, including rewrite instruction/schema and a wrapper reserve. |
| `CONVERSATION_SUMMARY_MAX_BYTES` | 2048 | 256–16384 UTF-8 bytes for the rolling extractive summary. |
| `CONVERSATION_MESSAGE_MAX_BYTES` | 2048 | 256–16384 bytes per recent-message/memory excerpt sent to contextualization; stored messages stay intact. |

This budget applies only to query contextualization input. It is not a measured
provider token count or a total graph/output/spending cap; existing evidence and
retry budgets remain separate. Budgets smaller than the fixed prompt/schema plus
the current question produce `context_budget_exceeded` rather than sending an
oversized request. More context may cost more and does not establish better
retrieval quality. A lossy summary can omit a necessary referent; ambiguous
follow-ups fail explicitly and can be clarified in a new turn.

Memory mutations and clear operations require an idle conversation. Clearing
summary/memory leaves history; a future turn may regenerate a summary from it.
Full clear/delete removes the conversation's message/Run/event records, without
deleting PDFs, unrelated evaluations, cache copies or backups. Back up local
history deliberately and do not use `docker compose down -v` unless data deletion
is intended.

Local volumes describe persistence, not every network boundary. Remote chat
providers receive contextualization text and selected evidence; hosted embedding
providers receive indexing/query text; remote judges receive evaluation payloads.
Use local chat/embedding/reranker/parser resources and approved cached weights
to keep inference local. No secret value belongs in a title, message or memory:
runtime keys remain environment/secrets, and recognizable credential patterns are
rejected without claiming a perfect free-text secret detector.

For a fresh empty corpus using the default hosted supervisor without an API key,
the Compose smoke runs through the frontend proxy:

```bash
python scripts/smoke.py --base-url http://127.0.0.1:8080
```

It checks readiness, the frontend, empty-index search, a real RQ job ending with
`provider_key_missing`, and terminal SSE replay through nginx. It requires an
empty corpus and unconfigured OpenAI/Anthropic/DeepSeek supervisor; it does not
test successful chat, PDF parsing or embedding/reranker weight inference, and
never substitutes mock inference. CI runs it after Compose startup. It also
verifies persisted conversation/message association, idempotency and explicit
memory/history deletion. It clears and deletes only its own temporary conversation
records; the standalone failed smoke Run/events remain available for inspection.

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

## Dedicated workload queues

Compose starts `worker-interactive`, `worker-ingestion` and `worker-evaluation`.
Each worker subscribes to exactly one queue, so a long evaluation cannot occupy
an interactive worker. This does not reserve CPU/RAM/network on a shared host;
provision those resources for model loading and the chosen concurrency.

`INTERACTIVE_QUEUE`, `INGESTION_QUEUE` and `EVALUATION_QUEUE` optionally change
queue names. Names must be distinct, valid lowercase identifiers and not the
reserved legacy `research` name. A Run freezes its queue at durable dispatch,
including retries of that dispatch; changing configuration cannot move accepted
jobs silently. Drain old names before removing their workers.

```bash
# One foreground development worker per terminal.
uv run python -m ragagent.worker --queue interactive
uv run python -m ragagent.worker --queue ingestion
uv run python -m ragagent.worker --queue evaluation
# Scale only the required workload; each replica handles one job at a time.
docker compose up -d --scale worker-interactive=2
```

`/api/ready` checks DB/Redis and a registered interactive worker. Read
`/api/queues` to check ingestion/evaluation independently; a missing dedicated
worker leaves its jobs queued until durable reconciliation's bound, then fails
with a safe code rather than switching to another workload.

For upgrades with pending jobs in the old `research` queue, stop new writes,
keep the new workers and temporarily run `python -m ragagent.worker --queue legacy`
with the same DB/Redis/settings. It consumes only already queued legacy jobs.
Remove it after the legacy queue drains. Do not purge Redis or recreate the DB.
