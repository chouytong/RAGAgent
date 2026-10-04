import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent import worker
from ragagent.api.papers import paper_response, update_metadata
from ragagent.api.schemas import PaperPatch
from ragagent.db.models import Author, Chunk, Paper, PaperAuthor, Run, Section
from ragagent.domain.research import Candidate, QueryPlan
from ragagent.ingestion.arxiv import ArxivMetadata
from ragagent.retrieval.service import HybridRetriever
from ragagent.settings import get_settings


def metadata(version: int) -> ArxivMetadata:
    arxiv_id = f"2408.09869v{version}"
    return ArxivMetadata(
        arxiv_id,
        "Pinned source",
        ["Alice", "Alice"],
        2024,
        f"https://arxiv.org/abs/{arxiv_id}",
        "2408.09869",
        version,
    )


@pytest.mark.integration
async def test_unversioned_reimport_resolves_latest_without_merging_equal_bytes(
    empty_db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    current_version = 1
    downloads: list[str] = []

    async def download(arxiv_id: str, path: Path, max_bytes: int) -> ArxivMetadata:
        downloads.append(arxiv_id)
        path.write_bytes(b"%PDF-1.4 identical bytes across versions")
        return metadata(current_version)

    monkeypatch.setattr(worker, "download_arxiv", download)
    run = Run(kind="arxiv", status="running", request={"arxiv_id": "2408.09869"})
    empty_db.add(run)
    empty_db.commit()
    first = await worker.arxiv_ingestion(empty_db, run)
    current_version = 2
    second = await worker.arxiv_ingestion(empty_db, run)
    duplicate = await worker.arxiv_ingestion(empty_db, run)
    assert first.id != second.id and second.id == duplicate.id
    assert first.sha256 == second.sha256
    assert (first.arxiv_family_id, first.arxiv_version) == ("2408.09869", 1)
    assert (second.arxiv_family_id, second.arxiv_version) == ("2408.09869", 2)
    assert second.source_url == "https://arxiv.org/abs/2408.09869v2"
    assert second.source_status == "unknown" and len(downloads) == 3
    assert len(list(tmp_path.glob("*.pdf"))) == 2
    assert len(list(empty_db.scalars(select(PaperAuthor)))) == 2
    run.request = {"arxiv_id": "2408.09869v2"}
    second.source_status = "withdrawn"
    empty_db.commit()
    pinned_duplicate = await worker.arxiv_ingestion(empty_db, run)
    assert pinned_duplicate.id == second.id and pinned_duplicate.source_status == "withdrawn"
    assert len(downloads) == 3  # An explicit pinned reimport is idempotent.


@pytest.mark.integration
def test_manual_source_status_is_returned_and_cannot_be_cleared(empty_db: Session) -> None:
    paper = Paper(title="Source", sha256="f" * 64, original_path="fixture")
    empty_db.add(paper)
    empty_db.commit()
    assert paper_response(empty_db, paper).source_status == "unknown"
    result = update_metadata(paper.id, PaperPatch(source_status="withdrawn"), empty_db)
    assert result.source_status == "withdrawn"
    with pytest.raises(HTTPException, match="source_status_cannot_be_null"):
        update_metadata(paper.id, PaperPatch(source_status=None), empty_db)


class Embedder:
    fingerprint = "source-fixture:384"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 383 for _ in texts]


class Reranker:
    async def rerank(self, query: str, candidates: list[Candidate], top_k: int) -> list[Candidate]:
        return candidates[:top_k]


@pytest.mark.integration
@pytest.mark.parametrize("source_status", ["unknown", "active", "withdrawn", "retracted"])
async def test_source_status_filters_dense_and_lexical(
    empty_db: Session, source_status: str
) -> None:
    paper = Paper(
        title="Source",
        sha256="f" * 64,
        original_path="fixture",
        status="indexed",
        embedding_model=Embedder.fingerprint,
    )
    empty_db.add(paper)
    empty_db.flush()
    # Exercise the public, schema-validated manual annotation path.
    update_metadata(paper.id, PaperPatch.model_validate({"source_status": source_status}), empty_db)
    section = Section(paper_id=paper.id, title="Methods", path="Methods", ordinal=0)
    empty_db.add(section)
    empty_db.flush()
    empty_db.add(
        Chunk(
            paper_id=paper.id,
            section_id=section.id,
            section_path="Methods",
            page_start=1,
            page_end=1,
            element_type="text",
            content="Contrastive retrieval source.",
            token_count=4,
            ordinal=0,
            embedding=[1.0] + [0.0] * 383,
        )
    )
    empty_db.flush()
    result = await HybridRetriever(empty_db, Embedder(), Reranker()).search(
        QueryPlan(queries=["contrastive retrieval"])
    )
    if source_status in {"withdrawn", "retracted"}:
        assert not result.dense and not result.lexical and not result.evidence
    else:
        assert result.dense and result.lexical and result.evidence
        assert result.evidence[0].paper.source_status == source_status


@pytest.mark.integration
def test_concurrent_same_version_import_keeps_one_original(
    job_sessions: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    family = f"2610.{int(uuid4().hex[:5], 16) % 100000:05d}"
    version_id = family + "v1"
    author_name = "Source concurrency " + uuid4().hex
    barrier = Barrier(2)

    async def download(arxiv_id: str, path: Path, max_bytes: int) -> ArxivMetadata:
        path.write_bytes(b"%PDF-1.4 concurrency fixture")
        barrier.wait(timeout=10)
        return ArxivMetadata(
            version_id,
            "Source concurrency",
            [author_name],
            2026,
            f"https://arxiv.org/abs/{version_id}",
            family,
            1,
        )

    monkeypatch.setattr(worker, "download_arxiv", download)
    with job_sessions() as session:
        runs = [Run(kind="arxiv", status="running", request={"arxiv_id": family}) for _ in range(2)]
        session.add_all(runs)
        session.commit()
        run_ids = [run.id for run in runs]

    def import_one(run_id: str) -> str:
        with job_sessions() as session:
            run = session.get(Run, run_id)
            assert run is not None
            return asyncio.run(worker.arxiv_ingestion(session, run)).id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(import_one, run_ids))
        assert ids[0] == ids[1]
        with job_sessions() as session:
            papers = list(session.scalars(select(Paper).where(Paper.arxiv_id == version_id)))
            assert len(papers) == 1 and Path(papers[0].original_path).is_file()
            assert len(list(tmp_path.glob("*.pdf"))) == 1
            assert (
                len(
                    list(session.scalars(select(PaperAuthor).where(PaperAuthor.paper_id == ids[0])))
                )
                == 1
            )
    finally:
        with job_sessions() as session:
            session.execute(delete(Run).where(Run.id.in_(run_ids)))
            session.execute(delete(Paper).where(Paper.arxiv_id == version_id))
            session.execute(delete(Author).where(Author.name == author_name))
            session.commit()
