from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk, Paper, Section
from ragagent.domain.documents import Element, ParsedDocument
from ragagent.ingestion.chunker import StructureChunker
from ragagent.ingestion.service import ingest


class FixtureParser:
    def parse(self, path: Path) -> ParsedDocument:
        return ParsedDocument(
            title="Fixture",
            elements=[
                Element(
                    section_path=["Methods", "Training"],
                    page_start=2,
                    page_end=3,
                    element_type="text",
                    content="The method uses contrastive training.",
                )
            ],
        )


class FixtureEmbedder:
    fingerprint = "test-only:384"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 383 for _ in texts]


class DocumentParser:
    def __init__(self, document: ParsedDocument) -> None:
        self.document = document

    def parse(self, path: Path) -> ParsedDocument:
        return self.document


@pytest.mark.integration
async def test_ingestion_and_generated_search_vector(empty_db: Session, tmp_path: Path) -> None:
    p = Paper(title="Fixture", sha256="a" * 64, original_path=str(tmp_path / "p.pdf"))
    empty_db.add(p)
    empty_db.flush()
    await ingest(empty_db, p, FixtureParser(), StructureChunker(), FixtureEmbedder())
    empty_db.flush()
    c = empty_db.scalar(select(Chunk).where(Chunk.paper_id == p.id))
    assert c and c.section_path == "Methods / Training" and c.page_start == 2
    assert empty_db.scalar(select(func.count()).select_from(Section)) == 2
    assert (
        empty_db.scalar(
            select(Chunk.id).where(
                Chunk.search_vector.op("@@")(func.plainto_tsquery("english", "contrastive"))
            )
        )
        == c.id
    )
    assert p.status == "indexed"
    with pytest.raises(ValueError, match="already_indexed"):
        await ingest(empty_db, p, FixtureParser(), StructureChunker(), FixtureEmbedder())


@pytest.mark.integration
async def test_section_labels_cannot_collide_with_structural_paths(
    empty_db: Session, tmp_path: Path
) -> None:
    paper = Paper(title="Colliding paths", sha256="d" * 64, original_path=str(tmp_path / "p.pdf"))
    empty_db.add(paper)
    empty_db.flush()
    document = ParsedDocument(
        title="Colliding paths",
        elements=[
            Element(
                section_path=["A / B"],
                page_start=1,
                page_end=1,
                element_type="text",
                content="Flat heading text.",
            ),
            Element(
                section_path=["A", "B"],
                page_start=2,
                page_end=2,
                element_type="text",
                content="Nested heading text.",
            ),
        ],
    )
    await ingest(empty_db, paper, DocumentParser(document), StructureChunker(), FixtureEmbedder())
    chunks = list(empty_db.scalars(select(Chunk).order_by(Chunk.ordinal)))
    assert len(chunks) == 2 and chunks[0].section_id != chunks[1].section_id
    flat, nested = [empty_db.get(Section, c.section_id) for c in chunks]
    assert flat and nested and flat.path == nested.path == "A / B"
    assert flat.title == "A / B" and flat.parent_id is None
    assert nested.title == "B" and nested.parent_id is not None
    parent = empty_db.get(Section, nested.parent_id)
    assert parent and parent.title == "A" and parent.parent_id is None


@pytest.mark.integration
async def test_repeated_headings_have_distinct_sections_and_chunks(
    empty_db: Session, tmp_path: Path
) -> None:
    paper = Paper(title="Repeated headings", sha256="e" * 64, original_path=str(tmp_path / "p.pdf"))
    empty_db.add(paper)
    empty_db.flush()
    document = ParsedDocument(
        title="Repeated headings",
        elements=[
            Element(
                section_path=["Experiments", "Results"],
                section_ids=["heading:0", f"heading:{page}"],
                page_start=page,
                page_end=page,
                element_type="text",
                content=f"Result from experiment {page}.",
            )
            for page in (1, 2)
        ],
    )
    await ingest(empty_db, paper, DocumentParser(document), StructureChunker(), FixtureEmbedder())
    chunks = list(empty_db.scalars(select(Chunk).order_by(Chunk.ordinal)))
    assert len(chunks) == 2 and chunks[0].section_id != chunks[1].section_id
    sections = [empty_db.get(Section, c.section_id) for c in chunks]
    assert all(s and s.path == "Experiments / Results" for s in sections)
    assert sections[0] and sections[1] and sections[0].parent_id == sections[1].parent_id
    assert [c.page_start for c in chunks] == [1, 2]
