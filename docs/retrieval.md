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
fingerprint; dense retrieval never mixes fingerprints. Change model → reindex.
Cross encoder is independent from chat and embeddings. Local adapters import
models lazily; network/model failures produce explicit errors, never mock success.

Evidence IDs are stable UUIDs of chunk/span. Exact span validation precedes
semantic claim verification. Citations use `[E:UUID]`; formatting is deterministic
from validated claims. Semantic verifier output is model-based, not proof or
human judgment. Missing/duplicate verdicts fail closed. Every required aspect
must be supported before release. Rerank thresholds are configurable heuristics
and should be tuned on real labels; no score is called a calibrated probability.
