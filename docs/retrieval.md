# Retrieval and evidence

QueryPlan queries and validated MetadataFilter are applied to **both** dense and
lexical SQL before ranking. Dense uses pgvector cosine distance; lexical uses
PostgreSQL `websearch_to_tsquery` / `ts_rank_cd` on a generated English tsvector.
English FTS is a v1 limitation for non-English corpora; embedding retrieval is
independent. Exact dense search avoids filtered ANN recall assumptions.

RRF adds `1/(k + rank)` per unique chunk per ranking, then selects candidate
N. CrossEncoder predicts query/chunk pairs and ranks evidence K. Dense, lexical,
fused candidates and reranked evidence are returned independently. Within-list
duplicates cannot inflate RRF. Expanded queries contribute independent rankings.
Section filters match full paths and their descendants or exact headings. Filters include paper IDs, authors, year bounds, venues, sections, entity types,
datasets, methods and metrics. Lists are OR within a field and fields are AND.
Entity matching requires recorded chunk annotations, not guessing from memory.

Embeddings have independent backend/model/dimension configuration and a stored
fingerprint; dense retrieval only compares the query fingerprint's indexed
vectors. Hosted endpoint identity also participates so same-name/same-dimension
models at different endpoints cannot share an index by accident. Hosted
fingerprints use a versioned hash of backend/model/dimension/normalized endpoint
and optional `EMBEDDING_REVISION`; credential-bearing or query/fragment endpoints
are rejected. Hosted embedding prefixes are limited to `openai`, `cohere`,
`cohere_chat` and `voyage`; other prefixes fail closed with
`unsupported_embedding_provider`. Non-OpenAI prefixes require an explicit
`EMBEDDING_API_BASE` (`embedding_api_base_required` otherwise). Compatible servers
use `openai/<model>` with an explicit base.

The OpenAI adapter freezes its effective endpoint when constructed, choosing
explicit `EMBEDDING_API_BASE`, then an already loaded LiteLLM global `api_base`,
then runtime `OPENAI_BASE_URL`, `OPENAI_API_BASE`, and finally
`https://api.openai.com/v1`. Both fingerprinting and each SDK request use that
same normalized explicit endpoint; later environment/global changes do not
retarget an existing adapter. For hosted services, `EMBEDDING_REVISION` is an
operator-supplied index identity label, not a request to change or pin server
weights. Local revisions are passed to model construction. Local default
fingerprints remain compatible; a configured revision becomes part of identity.
Changing embedding identity requires reindexing; changing dimension additionally requires
migration. Remote services that silently replace weights under unchanged
configuration cannot be detected automatically.
Cross encoder is independent from chat and embeddings. Local adapters import
models lazily; network/model failures produce explicit errors, never mock success.
Local embedding/reranker adapters are reused within an API process with bounded
configuration caches and synchronized first loading. RQ subprocesses do not share
that process memory, so this does not promise model reuse across worker jobs.

Evidence IDs are stable UUIDs of chunk/span. Exact span validation precedes
semantic claim verification. Citations use `[E:UUID]`; formatting is deterministic
from validated structured `evidence_ids`. Claim text containing citation markers,
including malformed or case-varied markers, fails verification; the formatter
removes embedded markers defensively before appending validated IDs. Semantic
verifier output is model-based, not proof or human judgment. Unknown, missing or
duplicate verdicts fail closed. Every required aspect must be supported before
release.
The semantic reviewer receives only cited Evidence IDs, their exact quotes and
source metadata; uncited evidence and text outside the cited span are excluded.

Both graphs accept only exact-span evidence with an explicit rerank score at or
above `MINIMUM_RERANK_SCORE` (default 0). Generation and verification share that
accepted pool. Diagnostic dense/lexical/fused rankings can include rejected
candidates; they are not released claim support. Retry pools deduplicate by
Evidence ID, retaining prior accepted records before new records within their
configured budgets (`RAG_EVIDENCE_BUDGET=48`, `RESEARCH_EVIDENCE_BUDGET=96`
by default). Pool truncation records
`evidence_budget_exhausted`; ordinary workflow budgets still determine when to
stop or refuse. Rerank thresholds are configurable heuristics and should be
tuned on real labels; no score is called a calibrated probability.

Numerical fusion and filtered SQL tests do not establish real-model reranking or
scientific-literature quality. Those need weights, representative PDFs and labels.
