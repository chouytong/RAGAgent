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
