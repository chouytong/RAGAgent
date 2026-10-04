from collections.abc import Iterator
from contextlib import contextmanager
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis import Redis
from rq import Queue, Worker
from rq.command import send_stop_job_command
from rq.job import Callback, JobStatus
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from ragagent import worker
from ragagent.db.dispatch import JobDispatch
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


@pytest.mark.integration
@pytest.mark.parametrize("termination", ["exit", "stop"])
def test_real_rq_child_exit_records_terminal_failure_and_retryable_paper(
    job_sessions: sessionmaker[Session],
    redis_connection: Redis,
    monkeypatch: pytest.MonkeyPatch,
    termination: str,
) -> None:
    queue = Queue(f"review-test-{uuid4()}", connection=redis_connection)
    with job_sessions() as session:
        paper = Paper(
            title="RQ exit probe", sha256=uuid4().hex * 2, original_path="probe", status="indexing"
        )
        session.add(paper)
        session.flush()
        run = Run(kind="ingestion", status="running", request={"paper_id": paper.id})
        session.add(run)
        session.flush()
        session.add(JobDispatch(run_id=run.id))
        session.add(ExecutionEvent(run_id=run.id, node="parsing", payload={"paper_id": paper.id}))
        session.commit()
        run_id, paper_id = run.id, paper.id
    monkeypatch.setattr(worker, "session_factory", lambda: job_sessions)
    job = queue.enqueue(
        "os._exit" if termination == "exit" else "time.sleep",
        42 if termination == "exit" else 10,
        job_id=run_id,
        job_timeout=30,
        failure_ttl=60,
        on_failure=Callback("ragagent.worker.on_failure"),
        on_stopped=Callback("ragagent.worker.on_stopped"),
    )
    stop_errors: list[str] = []

    def stop_started_job() -> None:
        deadline = monotonic() + 20
        while monotonic() < deadline:
            if job.get_status(refresh=True) == JobStatus.STARTED:
                send_stop_job_command(redis_connection, job.id)
                return
            sleep(0.02)
        stop_errors.append("job_did_not_start")

    stopper = Thread(target=stop_started_job) if termination == "stop" else None
    try:
        rq_worker = Worker(
            [queue],
            connection=redis_connection,
            work_horse_killed_handler=worker.on_work_horse_killed,
        )
        if stopper is not None:
            stopper.start()
        rq_worker.work(burst=True, max_jobs=1, logging_level="WARNING")
        if stopper is not None:
            stopper.join(timeout=21)
            assert not stopper.is_alive() and not stop_errors
        assert job.get_status(refresh=True) == (
            JobStatus.FAILED if termination == "exit" else JobStatus.STOPPED
        )
        with job_sessions() as session:
            assert session.get(Run, run_id).status == "failed"
            assert session.get(Run, run_id).error_code == "worker_interrupted"
            assert session.get(Paper, paper_id).status == "failed"
            assert session.get(Paper, paper_id).error_code == "worker_interrupted"
    finally:
        job.delete(remove_from_queue=True)
        queue.delete(delete_jobs=True)
        redis_connection.delete(queue.registry_cleaning_key, rq_worker.key)
        with job_sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.execute(delete(Paper).where(Paper.id == paper_id))
            session.commit()
