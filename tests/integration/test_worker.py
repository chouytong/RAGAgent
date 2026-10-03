from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from ragagent import worker
from ragagent.db.models import ExecutionEvent, Paper, Run
from ragagent.errors import ApplicationError


@pytest.mark.integration
async def test_duplicate_ingestion_cannot_fail_other_job_paper(
    empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper = Paper(title="Processing", sha256="d" * 64, original_path="fixture", status="parsing")
    empty_db.add(paper)
    empty_db.flush()
    run = Run(kind="ingestion", request={"paper_id": paper.id})
    empty_db.add(run)
    empty_db.commit()

    @contextmanager
    def scope() -> Iterator[Session]:
        yield empty_db

    monkeypatch.setattr(worker, "session_factory", lambda: scope)
    with pytest.raises(ApplicationError, match="paper_already_processing"):
        await worker.execute_async(run.id)
    assert empty_db.get(Paper, paper.id).status == "parsing"
    assert empty_db.get(Run, run.id).status == "failed"


@pytest.mark.integration
def test_interrupted_worker_records_retryable_failure(
    empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper = Paper(title="Interrupted", sha256="e" * 64, original_path="fixture", status="indexing")
    empty_db.add(paper)
    empty_db.flush()
    run = Run(kind="ingestion", request={"paper_id": paper.id}, status="running")
    empty_db.add(run)
    empty_db.flush()
    empty_db.add(ExecutionEvent(run_id=run.id, node="parsing", payload={"paper_id": paper.id}))
    empty_db.commit()

    @contextmanager
    def scope() -> Iterator[Session]:
        yield empty_db

    monkeypatch.setattr(worker, "session_factory", lambda: scope)
    worker.on_failure(SimpleNamespace(id=run.id), None, None, None, None)
    assert empty_db.get(Run, run.id).error_code == "worker_interrupted"
    assert empty_db.get(Paper, paper.id).status == "failed"
