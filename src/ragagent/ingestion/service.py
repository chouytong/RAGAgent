import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk, Paper, Section, new_id
from ragagent.ingestion.chunker import StructureChunker
from ragagent.ingestion.parser import Parser
from ragagent.providers.ports import Embedder


def section_identity(path: list[str], node_ids: list[str]) -> str:
    # A structural encoding avoids collisions between a heading containing " / "
    # and an actual parent/child path. Node IDs additionally preserve occurrences.
    return json.dumps(["nodes", node_ids] if node_ids else ["path", path], ensure_ascii=False)


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
            identity = section_identity(draft.section_path[:depth], draft.section_ids[:depth])
            if identity not in sections:
                parent = section_identity(
                    draft.section_path[: depth - 1], draft.section_ids[: depth - 1]
                )
                section_id = new_id()
                session.add(
                    Section(
                        id=section_id,
                        paper_id=paper.id,
                        parent_id=sections.get(parent) if depth > 1 else None,
                        title=draft.section_path[depth - 1],
                        path=path,
                        identity=identity,
                        ordinal=len(sections),
                    )
                )
                session.flush()
                sections[identity] = section_id
        path = " / ".join(draft.section_path)
        identity = section_identity(draft.section_path, draft.section_ids)
        session.add(
            Chunk(
                paper_id=paper.id,
                section_id=sections[identity],
                section_path=path,
                page_start=draft.page_start,
                page_end=draft.page_end,
                element_type=draft.element_type,
                content=draft.content,
                token_count=draft.token_count,
                ordinal=draft.ordinal,
                embedding=vector,
                metadata_json={"parser": type(parser).__name__, "section_ids": draft.section_ids},
            )
        )
    parse_path = Path(paper.original_path).with_suffix(".parsed.json")
    parse_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    paper.embedding_model = embedder.fingerprint
    paper.status = "indexed"
    paper.error_code = None
    # Caller owns the transaction. Failures roll back all indexing changes.
    session.flush()
