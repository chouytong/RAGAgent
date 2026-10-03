from sqlalchemy import Select, func, select

from ragagent.db.models import Author, Chunk, ChunkEntity, Entity, Paper, PaperAuthor
from ragagent.domain.research import MetadataFilter


def apply_filters(
    statement: Select[Chunk, Paper, float], filters: MetadataFilter
) -> Select[Chunk, Paper, float]:
    if filters.paper_ids:
        statement = statement.where(Paper.id.in_(filters.paper_ids))
    if filters.year_start is not None:
        statement = statement.where(Paper.year >= filters.year_start)
    if filters.year_end is not None:
        statement = statement.where(Paper.year <= filters.year_end)
    if filters.venues:
        statement = statement.where(
            func.lower(Paper.venue).in_([x.lower() for x in filters.venues])
        )
    if filters.authors:
        subquery = (
            select(PaperAuthor.paper_id)
            .join(Author)
            .where(func.lower(Author.name).in_([x.lower() for x in filters.authors]))
        )
        statement = statement.where(Paper.id.in_(subquery))
    if filters.sections:
        # Exact full path or exact leaf heading. Bound parameters prevent SQL injection.
        from ragagent.db.models import Section

        statement = statement.where(
            Chunk.section_path.in_(filters.sections)
            | Chunk.section_id.in_(select(Section.id).where(Section.title.in_(filters.sections)))
        )
    for names, kind in [
        (filters.datasets, "dataset"),
        (filters.methods, "method"),
        (filters.metrics, "metric"),
    ]:
        if names:
            subquery = (
                select(ChunkEntity.chunk_id)
                .join(Entity)
                .where(
                    Entity.entity_type == kind,
                    func.lower(Entity.name).in_([x.lower() for x in names]),
                )
            )
            statement = statement.where(Chunk.id.in_(subquery))
    if filters.entity_types:
        subquery = (
            select(ChunkEntity.chunk_id)
            .join(Entity)
            .where(Entity.entity_type.in_(filters.entity_types))
        )
        statement = statement.where(Chunk.id.in_(subquery))
    return statement
