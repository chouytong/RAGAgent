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
Reranking uses `QueryPlan.rerank_query`: the original RAG question or the current
research subtask question, preserved across expansion. For a direct caller that
omits it, the target joins all plan queries; it no longer selects only the first.
Section filters match full paths and their descendants or exact headings. Filters include paper IDs, authors, year bounds, venues, sections, entity types,
datasets, methods and metrics. Lists are OR within a field and fields are AND.
Entity matching requires recorded chunk annotations, not guessing from memory.
Dense and lexical search also exclude papers marked `withdrawn` or `retracted`;
`unknown` remains eligible with that status present in evidence metadata. Status
is operator-supplied, not automatically inferred from text or indexing success.

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
weights. Local Hub revisions resolve lazily to immutable commit SHAs, which are
passed to model construction. Local directories instead use a verified artifact
hash. The new `local:v2` fingerprint includes this resolved identity; old local
fingerprints are excluded until explicitly reembedded. An omitted revision
freezes one adapter, not all future processes: record and configure the SHA for
reproduction across runs.
Changing embedding identity requires reindexing; changing dimension additionally requires
migration. Remote services that silently replace weights under unchanged
configuration cannot be detected automatically.
Use `python -m ragagent.reindex --paper-id UUID` or `--all-indexed` to update
vectors/fingerprint in one transaction per paper while retaining chunk IDs.
Reembedding cannot repair old cuts or merged parser sections.
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
verifier output is model-based, not proof or human judgment. It must return
`supported_pairs` for every attached claim/evidence pair, in addition to the
claim-level verdict. A comparison may use different papers to support different
parts, but a single supporting paper does not validate an unrelated citation.
Unknown/duplicate pairs and missing support for any attached citation fail
closed and require revision; citations are not silently discarded. Unknown,
missing or duplicate verdicts fail closed. Every required aspect must be supported before
release.
The semantic reviewer receives only cited Evidence IDs, their exact quotes and
source metadata plus separately sourced table headers/captions when present;
uncited evidence and other text outside those explicit source spans are excluded.
Analysis and review use one quote payload per evidence item, omitting duplicate
full `content`; original text remains local for deterministic span validation.

Chunking retains decimal/scientific-notation tokens. Formulas are atomic.
Recognized Markdown tables split between complete rows, with exact header and
linked caption excerpts in `SourceContext`; an unrecognized serialization stays
atomic rather than guessing columns. Auxiliary source text is not inserted into
stored `Chunk.content`, so its independent source ID/page/offsets are preserved.
Embedding and reranking include those context quotes without duplicating text
already in the chunk. These policies may produce chunks above the lexical target;
they do not establish real PDF extraction fidelity or provider tokenizer limits.

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
