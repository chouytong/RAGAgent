from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent.db.models import Chunk, Evidence, Paper, Section
from ragagent.domain.documents import SourceContext
from ragagent.errors import ConfigurationError, ProviderError
from ragagent.ingestion.reembed import reembed_paper
from ragagent.retrieval.service import record

OLD_FINGERPRINT = "fixture-old:384"
NEW_FINGERPRINT = "fixture-new:384"
OLD_VECTOR = [1.0] + [0.0] * 383
NEW_VECTOR = [0.0, 1.0] + [0.0] * 382


@contextmanager
def indexed_paper(sessions: sessionmaker[Session]) -> Iterator[str]:
    header = "| Method | Accuracy (%) |"
    context = SourceContext(
        source_id="table-header",
        element_type="table",
        section_path=["Results"],
        page_start=2,
        page_end=2,
        content=header,
        quote=header,
        span_start=0,
        span_end=len(header),
    )
    with sessions() as session:
        paper = Paper(
            title="Reembedding provenance fixture",
            sha256=uuid4().hex * 2,
            original_path="fixture.pdf",
            status="indexed",
            embedding_model=OLD_FINGERPRINT,
        )
        session.add(paper)
        session.flush()
        section = Section(paper_id=paper.id, title="Results", path="Results", ordinal=0)
        session.add(section)
        session.flush()
        for ordinal in range(3):
            content = f"| Method {ordinal} | 95.0 |"
            chunk = Chunk(
                paper_id=paper.id,
                section_id=section.id,
                section_path="Results",
                page_start=2,
                page_end=2,
                element_type="table",
                content=content,
                token_count=8,
                ordinal=ordinal,
                embedding=OLD_VECTOR,
                metadata_json={"source_context": [context.model_dump()]},
            )
            session.add(chunk)
            session.flush()
            session.add(
                Evidence(
                    chunk_id=chunk.id,
                    span_start=0,
                    span_end=len(content),
                    quote=content,
                )
            )
        session.commit()
        paper_id = paper.id
    try:
        yield paper_id
    finally:
        with sessions() as session:
            session.execute(delete(Paper).where(Paper.id == paper_id))
            session.commit()


def snapshot(sessions: sessionmaker[Session], paper_id: str) -> dict[str, Any]:
    with sessions() as session:
        paper = session.get(Paper, paper_id)
        assert paper is not None
        chunks = list(
            session.scalars(select(Chunk).where(Chunk.paper_id == paper_id).order_by(Chunk.ordinal))
        )
        citations = [
            record(chunk, paper, 1.0, "dense", []).evidence.model_dump(exclude={"scores"})
            for chunk in chunks
        ]
        evidence = list(
            session.execute(
                select(Evidence.id, Evidence.chunk_id, Evidence.quote, Evidence.span_end)
                .join(Chunk)
                .where(Chunk.paper_id == paper_id)
                .order_by(Evidence.chunk_id)
            )
        )
        return {
            "fingerprint": paper.embedding_model,
            "vectors": [list(chunk.embedding) for chunk in chunks],
            "citations": citations,
            "evidence": evidence,
            "metadata": [chunk.metadata_json for chunk in chunks],
        }


@pytest.mark.integration
async def test_reembed_updates_vectors_and_identity_atomically_preserving_provenance(
    job_sessions: sessionmaker[Session],
) -> None:
    with indexed_paper(job_sessions) as paper_id:
        before = snapshot(job_sessions, paper_id)

        class RecordingEmbedder:
            fingerprint = NEW_FINGERPRINT
            inputs: list[str] = []

            async def embed(self, texts: list[str]) -> list[list[float]]:
                # An independent reader sees no partially rebuilt batches.
                assert snapshot(job_sessions, paper_id) == before
                self.inputs.extend(texts)
                return [NEW_VECTOR for _ in texts]

        embedder = RecordingEmbedder()
        with job_sessions() as session:
            result = await reembed_paper(session, paper_id, embedder, batch_size=1)
            assert result["chunk_count"] == 3
            assert snapshot(job_sessions, paper_id) == before
            session.commit()
        after = snapshot(job_sessions, paper_id)
        assert after["fingerprint"] == NEW_FINGERPRINT
        assert after["vectors"] == [NEW_VECTOR] * 3
        assert after["citations"] == before["citations"]
        assert after["evidence"] == before["evidence"]
        assert after["metadata"] == before["metadata"]
        assert embedder.inputs == [
            f"| Method | Accuracy (%) |\n| Method {ordinal} | 95.0 |" for ordinal in range(3)
        ]


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["provider", "dimension"])
async def test_reembed_later_batch_failure_rolls_back_all_vectors_and_fingerprint(
    job_sessions: sessionmaker[Session], failure: str
) -> None:
    with indexed_paper(job_sessions) as paper_id:
        before = snapshot(job_sessions, paper_id)

        class FailingEmbedder:
            fingerprint = NEW_FINGERPRINT
            calls = 0

            async def embed(self, texts: list[str]) -> list[list[float]]:
                self.calls += 1
                if self.calls == 2:
                    if failure == "provider":
                        raise ProviderError("fixture_embedding_failed")
                    return [[0.0, 1.0] for _ in texts]
                return [NEW_VECTOR for _ in texts]

        expected = ProviderError if failure == "provider" else ConfigurationError
        code = (
            "fixture_embedding_failed"
            if failure == "provider"
            else "reembedding_dimension_requires_migration"
        )
        with job_sessions() as session:
            with pytest.raises(expected, match=code):
                await reembed_paper(session, paper_id, FailingEmbedder(), batch_size=1)
            session.rollback()
        assert snapshot(job_sessions, paper_id) == before
