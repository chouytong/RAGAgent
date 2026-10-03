import asyncio
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk, Paper, Section, new_id
from ragagent.ingestion.chunker import StructureChunker
from ragagent.ingestion.parser import Parser
from ragagent.providers.ports import Embedder


async def ingest(
    session: Session,
    paper: Paper,
    parser: Parser,
    chunker: StructureChunker,
    embedder: Embedder,
    progress: Callable[[str], None] | None = None,
) -> None:
    if session.scalar(select(Chunk.id).where(Chunk.paper_id == paper.id).limit(1)):
        raise ValueError("paper_already_indexed")
    document = await asyncio.to_thread(parser.parse, Path(paper.original_path))
    drafts = chunker.chunk(document)
    if not drafts:
        raise ValueError("empty_document")
    if progress is not None:
        progress("indexing")
    vectors = await embedder.embed([chunk.content for chunk in drafts])
    if len(vectors) != len(drafts):
        raise ValueError("embedding_count_mismatch")
    sections: dict[str, str] = {}
    for draft, vector in zip(drafts, vectors, strict=True):
        for depth in range(1, len(draft.section_path) + 1):
            path = " / ".join(draft.section_path[:depth])
            if path not in sections:
                parent = " / ".join(draft.section_path[: depth - 1])
                section_id = new_id()
                session.add(
                    Section(
                        id=section_id,
                        paper_id=paper.id,
                        parent_id=sections.get(parent),
                        title=draft.section_path[depth - 1],
                        path=path,
                        ordinal=len(sections),
                    )
                )
                session.flush()
                sections[path] = section_id
        path = " / ".join(draft.section_path)
        session.add(
            Chunk(
                paper_id=paper.id,
                section_id=sections[path],
                section_path=path,
                page_start=draft.page_start,
                page_end=draft.page_end,
                element_type=draft.element_type,
                content=draft.content,
                token_count=draft.token_count,
                ordinal=draft.ordinal,
                embedding=vector,
                metadata_json={"parser": type(parser).__name__},
            )
        )
    parse_path = Path(paper.original_path).with_suffix(".parsed.json")
    parse_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    paper.embedding_model = embedder.fingerprint
    paper.status = "indexed"
    paper.error_code = None
    # Caller owns the transaction. Failures roll back all indexing changes.
    session.flush()
