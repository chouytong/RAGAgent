from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from redis import Redis
from rq import Queue
from rq.job import Job
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent.api.papers import enqueue
from ragagent.api.queue import RQQueue
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import ExecutionEvent, Run
from ragagent.errors import ApplicationError
from ragagent.jobs import claim_run, finish_run, reconcile_jobs


@pytest.mark.integration
def test_committed_outbox_recovers_lost_redis_ack_without_duplicate_job(
    empty_db: Session, redis_connection: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_queue = RQQueue()
    monkeypatch.setattr(real_queue, "connection", lambda: redis_connection)

    class LostAcknowledgement:
        def submit(self, run_id: str) -> None:
            real_queue.submit(run_id)
            raise ConnectionError("simulated acknowledgement loss")

    run = enqueue(empty_db, LostAcknowledgement(), "rag", {"query": "q"})
    queue = Queue("research", connection=redis_connection)
    try:
        dispatch = empty_db.get(JobDispatch, run.id)
        assert dispatch is not None and dispatch.dispatched_at is None
        assert run.status == "queued" and run.error_code == "queue_unavailable"
        assert queue.job_ids.count(run.id) == 1
        reconcile_jobs(empty_db, real_queue)
        assert dispatch.dispatched_at is not None and dispatch.attempts == 2
        assert run.status == "queued" and run.error_code is None
        assert queue.job_ids.count(run.id) == 1
        reconcile_jobs(empty_db, real_queue)
        assert queue.job_ids.count(run.id) == 1 and dispatch.attempts == 2
    finally:
        Job.fetch(run.id, connection=redis_connection).delete(remove_from_queue=True)


@pytest.mark.integration
def test_pending_outbox_recovers_after_no_redis_send(empty_db: Session) -> None:
    run = Run(kind="research", request={"research_question": "q"})
    empty_db.add(run)
    empty_db.flush()
    empty_db.add(JobDispatch(run_id=run.id))
    empty_db.commit()  # Simulates the crash point before the first enqueue attempt.

    class AvailableQueue:
        submitted: list[str] = []

        def status(self, run_id: str) -> str | None:
            return "queued" if run_id in self.submitted else None

        def submit(self, run_id: str) -> None:
            self.submitted.append(run_id)

    queue = AvailableQueue()
    reconcile_jobs(empty_db, queue)
    assert queue.submitted == [run.id]
    assert empty_db.get(JobDispatch, run.id).dispatched_at is not None
    reconcile_jobs(empty_db, queue)
    assert queue.submitted == [run.id]


@pytest.mark.integration
def test_concurrent_workers_can_claim_run_only_once(job_sessions: sessionmaker[Session]) -> None:
    with job_sessions() as session:
        run = Run(kind="rag", request={"query": "q"})
        session.add(run)
        session.flush()
        session.add(JobDispatch(run_id=run.id))
        session.commit()
        run_id = run.id
    try:

        def claim() -> bool:
            with job_sessions() as session:
                return claim_run(session, run_id) is not None

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: claim(), range(2)))
        assert sorted(claims) == [False, True]
        with job_sessions() as session:
            assert session.get(Run, run_id).status == "running"
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ExecutionEvent)
                    .where(ExecutionEvent.run_id == run_id, ExecutionEvent.node == "started")
                )
                == 1
            )
    finally:
        with job_sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.commit()


@pytest.mark.integration
def test_reconcile_lost_running_job_and_prevent_late_worker_success(empty_db: Session) -> None:
    run = Run(kind="rag", request={"query": "q"}, status="running")
    empty_db.add(run)
    empty_db.flush()
    empty_db.add(JobDispatch(run_id=run.id, claimed_at=datetime.now(UTC)))
    empty_db.commit()

    class MissingQueue:
        def status(self, run_id: str) -> None:
            return None

        def submit(self, run_id: str) -> None:
            raise AssertionError("running job must not be redispatched")

    reconcile_jobs(empty_db, MissingQueue())
    assert run.status == "failed" and run.error_code == "worker_interrupted"
    run.status, run.result = "completed", {"answer": "late result"}
    with pytest.raises(ApplicationError, match="run_no_longer_active"):
        finish_run(empty_db, run)
    empty_db.rollback()
    assert empty_db.get(Run, run.id).status == "failed"
    assert empty_db.get(Run, run.id).result is None


@pytest.mark.integration
def test_redis_outage_does_not_fail_active_run_but_expired_jobs_terminate(
    empty_db: Session,
) -> None:
    now = datetime.now(UTC)
    active = Run(kind="rag", request={"query": "q"}, status="running")
    expired = Run(kind="research", request={"research_question": "q"})
    empty_db.add_all([active, expired])
    empty_db.flush()
    empty_db.add_all(
        [
            JobDispatch(run_id=active.id, claimed_at=now),
            JobDispatch(run_id=expired.id, created_at=now - timedelta(hours=1)),
        ]
    )
    empty_db.commit()

    class OfflineQueue:
        def status(self, run_id: str) -> None:
            raise ConnectionError("offline")

        def submit(self, run_id: str) -> None:
            raise AssertionError("offline status query must not dispatch")

    reconcile_jobs(empty_db, OfflineQueue(), now=now)
    assert active.status == "running"
    assert expired.status == "failed" and expired.error_code == "worker_unavailable"
    reconcile_jobs(empty_db, OfflineQueue(), now=now + timedelta(hours=1))
    assert active.status == expired.status == "failed"
    assert active.error_code == "worker_interrupted"
    assert expired.error_code == "worker_unavailable"
