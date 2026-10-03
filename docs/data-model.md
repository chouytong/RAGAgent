# Data model

Paper has ordered PaperAuthor links to Author; Section has an explicit parent
and unique paper/path. Chunk records UUID, paper/section, complete section path,
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
execution timeline. Redis is only the queue. Credentials never enter these
records. Uploaded PDFs are content-hashed and deduplicated.

The first migration is a frozen explicit schema generated from this model,
including pgvector extension and FTS index. Use Alembic, never `create_all` as
an application startup migration. Embedding dimensionality is fixed at first
migration; changing it requires an explicit migration and complete reindex.
Chunk token counts use deterministic lexical tokens, not a provider tokenizer;
provider context limits must be enforced independently.
