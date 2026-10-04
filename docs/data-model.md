# Data model

Paper has ordered PaperAuthor links to Author; Section has an explicit parent
and unique `(paper_id, identity)`. Its heading/path is a display label, not a
unique identity. Parser elements and chunk drafts carry `section_ids` parallel
to `section_path`; real Docling heading occurrences receive distinct node IDs.
Ingestion encodes node ancestry as structured JSON identity. Fixtures without
node IDs use the heading array as structured JSON, avoiding delimiter collisions
but requiring node IDs to distinguish repeated identical paths.
The existing section filter is a display-path/heading predicate and can match
multiple structural nodes with the same label; it is not a section-ID selector.

Chunk records UUID, paper/section, complete section path,
pages, element type, exact text, ordinal, lexical token count, JSON metadata,
configured-dimensional vector and a generated English tsvector with GIN index.

Entity has a type and normalized name. ChunkEntity links occurrences to exact
chunks. Dataset/Method/Metric extend Entity; EntityRelation includes a source
chunk. Entity filters operate on recorded occurrences; missing annotations do
not match. Annotation can be supplied manually; automated extraction must be
verified before persistence.

Evidence stores a chunk FK, exact character offsets/quote and retrieval scores.
API evidence joins paper/section/page metadata. Claims reference these evidence
IDs. Quotes must equal `chunk.content[span_start:span_end]`.

Run and ExecutionEvent persist job requests/results and a replayable ordered
execution timeline. `JobDispatch` records one durable intent per Run, dispatch
attempts/error code, dispatch time and claim time. It commits with the Run;
database row locks protect dispatch and execution claims. The terminal event
commits with terminal Run state so SSE can drain its stable watermark.
Redis is only the queue. Credentials never enter these
records. Uploaded PDFs are content-hashed and deduplicated.

The first migration is a frozen explicit schema generated from this model,
including pgvector extension and FTS index. Use Alembic, never `create_all` as
an application startup migration. Embedding dimensionality is fixed at first
migration; changing it requires an explicit migration and complete reindex.
The section-identity migration assigns each existing section a `legacy:<UUID>`
identity without splitting its path. It cannot recover sections already merged
by the former display-path key; reingest those original PDFs to reconstruct the
hierarchy. New revisions preserve the frozen initial migration.
Chunk token counts use deterministic lexical tokens, not a provider tokenizer;
provider context limits must be enforced independently.
