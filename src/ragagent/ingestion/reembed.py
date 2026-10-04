"""Rebuild embedding vectors without replacing source chunks or citation IDs."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk, Paper
from ragagent.domain.documents import SourceContext, contextual_text
from ragagent.errors import ApplicationError, ConfigurationError
from ragagent.providers.embedding import validate_vectors
from ragagent.providers.ports import Embedder


async def reembed_paper(
    session: Session, paper_id: str, embedder: Embedder, *, batch_size: int = 32
) -> dict[str, str | int]:
    """Caller commits/rolls back; readers see all old vectors or all new vectors."""
    if not 1 <= batch_size <= 256:
        raise ConfigurationError("invalid_reembedding_batch_size")
    paper = session.scalar(
        select(Paper)
        .where(Paper.id == paper_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if paper is None:
        raise ApplicationError("paper_not_found")
    if paper.status != "indexed":
        raise ApplicationError("paper_not_indexed")
    chunks = list(
        session.scalars(
            select(Chunk).where(Chunk.paper_id == paper_id).order_by(Chunk.ordinal, Chunk.id)
        )
    )
    if not chunks:
        raise ApplicationError("paper_has_no_chunks")
    fingerprint = embedder.fingerprint
    expected_dimension = len(chunks[0].embedding)
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        texts = [
            contextual_text(
                chunk.content,
                [
                    SourceContext.model_validate(item)
                    for item in chunk.metadata_json.get("source_context", [])
                ],
            )
            for chunk in batch
        ]
        vectors = await embedder.embed(texts)
        if any(len(vector) != expected_dimension for vector in vectors):
            raise ConfigurationError("reembedding_dimension_requires_migration")
        validate_vectors(vectors, len(batch), expected_dimension)
        if embedder.fingerprint != fingerprint:
            raise ConfigurationError("embedding_identity_changed_during_rebuild")
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
    paper.embedding_model = fingerprint
    session.flush()
    return {"paper_id": paper.id, "chunk_count": len(chunks), "embedding_fingerprint": fingerprint}
