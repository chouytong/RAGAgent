import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier, local
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent.api.papers import retry_ingestion, upload
from ragagent.db.models import Author, Paper, PaperAuthor, Run
from ragagent.settings import get_settings


class RecordingQueue:
    def submit(self, run_id: str) -> None:
        pass


@pytest.mark.integration
@pytest.mark.parametrize("same_pdf", [True, False])
def test_concurrent_uploads_deduplicate_pdf_and_shared_authors(
    job_sessions: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_pdf: bool,
) -> None:
    marker = uuid4().hex
    names = [f"Alice-{marker}", f"Bob-{marker}"]
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    monkeypatch.setattr("ragagent.api.papers.get_settings", lambda: settings)
    barrier, seen = Barrier(2), local()
    engine = job_sessions.kw["bind"]

    def simultaneous_absence(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if "WHERE papers.sha256 =" in statement and not getattr(seen, "lookup", False):
            seen.lookup = True
            barrier.wait(timeout=5)

    event.listen(engine, "after_cursor_execute", simultaneous_absence)
    results = []
    try:

        def submit(index: int) -> Any:
            payload = b"%PDF-1.4 fixture " + marker.encode()
            if not same_pdf:
                payload += str(index).encode()
            with job_sessions() as session:
                return asyncio.run(
                    upload(
                        session,
                        RecordingQueue(),
                        UploadFile(file=BytesIO(payload), filename=f"{marker}.pdf"),
                        title=marker,
                        authors=";".join(names if index == 0 else list(reversed(names))),
                        year=2024,
                        venue=None,
                    )
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        assert len({result.id for result in results}) == (1 if same_pdf else 2)
        with job_sessions() as session:
            papers = list(session.scalars(select(Paper).where(Paper.title == marker)))
            assert len(papers) == (1 if same_pdf else 2)
            assert (
                session.scalar(
                    select(func.count()).select_from(Author).where(Author.name.in_(names))
                )
                == 2
            )
            for paper in papers:
                linked_names = list(
                    session.scalars(
                        select(Author.name)
                        .join(PaperAuthor)
                        .where(PaperAuthor.paper_id == paper.id)
                        .order_by(PaperAuthor.position)
                    )
                )
                assert linked_names in [names, list(reversed(names))]
            assert len(list(tmp_path.glob("*.pdf"))) == len(papers)
    finally:
        event.remove(engine, "after_cursor_execute", simultaneous_absence)
        with job_sessions() as session:
            paper_ids = list(session.scalars(select(Paper.id).where(Paper.title == marker)))
            session.execute(
                delete(Run).where(
                    Run.kind == "ingestion", Run.request["paper_id"].as_string().in_(paper_ids)
                )
            )
            session.execute(delete(Paper).where(Paper.id.in_(paper_ids)))
            session.execute(delete(Author).where(Author.name.in_(names)))
            session.commit()


@pytest.mark.integration
def test_retry_refreshes_processing_status_instead_of_overwriting_stale_paper(
    job_sessions: sessionmaker[Session],
) -> None:
    with job_sessions() as session:
        paper = Paper(title="Retry status probe", sha256=uuid4().hex * 2, original_path="probe")
        session.add(paper)
        session.commit()
        paper_id = paper.id
    try:
        with job_sessions() as stale:
            cached = stale.get(Paper, paper_id)
            assert cached.status == "queued"
            with job_sessions() as processing:
                processing.get(Paper, paper_id).status = "parsing"
                processing.commit()
            with pytest.raises(HTTPException) as rejected:
                retry_ingestion(paper_id, stale, RecordingQueue())
            assert rejected.value.status_code == 409
            assert cached.status == "parsing"
        with job_sessions() as session:
            assert session.get(Paper, paper_id).status == "parsing"
    finally:
        with job_sessions() as session:
            session.execute(delete(Paper).where(Paper.id == paper_id))
            session.commit()
