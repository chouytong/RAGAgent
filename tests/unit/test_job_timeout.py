from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ragagent import jobs
from ragagent.api import queue as queue_module
from ragagent.api.queue import RQQueue
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import Run
from ragagent.settings import Settings


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("rag", 1800),
        ("research", 1800),
        ("ingestion", 1800),
        ("arxiv", 1800),
        ("eval_rag", 10800),
        ("eval_multi_agent", 10800),
        ("eval_retrieval", 10800),
    ],
)
def test_evaluation_timeout_is_separate_from_ordinary_jobs(
    monkeypatch: pytest.MonkeyPatch, kind: str, expected: int
) -> None:
    settings = Settings(_env_file=None, evaluation_timeout_seconds=10800)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    assert jobs.job_timeout_seconds(Run(kind=kind, request={})) == expected


def test_evaluation_timeout_is_frozen_against_later_setting_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    run = Run(kind="eval_rag", request={"dataset": {}})
    assert jobs.freeze_job_timeout(run) == 7200
    settings.evaluation_timeout_seconds = 1800
    assert jobs.job_timeout_seconds(run) == 7200
    assert jobs.freeze_job_timeout(run) == 7200
    assert run.request == {"dataset": {}, "_job_timeout_seconds": 7200}


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 86401, "7200", None])
def test_invalid_frozen_timeout_does_not_bypass_setting_limits(
    monkeypatch: pytest.MonkeyPatch, invalid: Any
) -> None:
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))
    run = Run(kind="eval_retrieval", request={"_job_timeout_seconds": invalid})
    assert jobs.freeze_job_timeout(run) == 7200


def test_rq_receives_selected_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[dict[str, Any]] = []
    queue = RQQueue()
    monkeypatch.setattr(queue, "connection", lambda: None)
    monkeypatch.setattr(
        queue_module,
        "Queue",
        lambda *args, **kwargs: SimpleNamespace(
            enqueue=lambda *args, **kwargs: submitted.append(kwargs)
        ),
    )
    queue.submit_with_timeout("evaluation-id", 7200)
    queue.submit("ordinary-id")
    assert [item["job_timeout"] for item in submitted] == [7200, 1800]
    assert [item["job_id"] for item in submitted] == ["evaluation-id", "ordinary-id"]


class DispatchSession:
    def __init__(self, run: Run, dispatch: JobDispatch) -> None:
        self.run, self.dispatch = run, dispatch

    def get(self, model: Any, identifier: str) -> JobDispatch:
        assert model is JobDispatch and identifier == self.run.id
        return self.dispatch

    def scalars(self, statement: Any) -> list[str]:
        return [self.run.id]

    def add(self, value: Any) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def test_dispatch_retry_and_reconciliation_use_same_frozen_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    settings = Settings(_env_file=None)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    run = Run(id="eval-id", kind="eval_rag", request={}, status="queued", created_at=now)
    dispatch = JobDispatch(run_id=run.id, created_at=now, attempts=0)
    session = DispatchSession(run, dispatch)
    monkeypatch.setattr(jobs, "locked_run", lambda *args, **kwargs: run)

    class RecordingQueue:
        timeouts: list[int] = []

        def submit(self, run_id: str) -> None:
            raise AssertionError("evaluation dispatch must pass its selected timeout")

        def submit_with_timeout(self, run_id: str, timeout: int) -> None:
            self.timeouts.append(timeout)
            if len(self.timeouts) == 1:
                raise ConnectionError("lost_enqueue_ack")

        def status(self, run_id: str) -> str:
            return "started"

    queue = RecordingQueue()
    assert not jobs.dispatch_run(session, queue, run.id)
    settings.evaluation_timeout_seconds = 1800
    assert jobs.dispatch_run(session, queue, run.id)
    assert queue.timeouts == [7200, 7200]
    run.status = "running"
    dispatch.claimed_at = now - timedelta(seconds=3600)
    failures: list[str] = []
    monkeypatch.setattr(jobs, "fail_run", lambda *args: failures.append(args[2]))
    jobs.reconcile_jobs(session, queue, now=now)
    assert failures == []
    jobs.reconcile_jobs(session, queue, now=now + timedelta(seconds=3721))
    assert failures == ["worker_interrupted"]
