from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue, Worker
from sqlalchemy.orm import Session

from ragagent.api import runs
from ragagent.api.app import app
from ragagent.api.dependencies import get_db
from ragagent.api.queue import RQQueue, get_queue
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import ExecutionEvent, Run
from ragagent.settings import get_settings


class RecordingQueue:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def submit(self, run_id: str) -> None:
        self.ids.append(run_id)


@pytest.fixture
def client(
    empty_db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    def db_override() -> Iterator[Session]:
        yield empty_db

    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    monkeypatch.setattr("ragagent.api.papers.get_settings", lambda: settings)
    monkeypatch.setattr("ragagent.api.dispatcher.reconcile", lambda: None)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_queue] = lambda: RecordingQueue()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.integration
def test_upload_dedup_metadata_invalid_pdf(client: TestClient) -> None:
    result = client.post(
        "/api/papers/upload",
        files={"file": ("paper.pdf", b"%PDF-1.4 fixture", "application/pdf")},
        data={"title": "Title", "authors": "Alice;Bob", "year": "2024", "venue": "ICML"},
    )
    assert result.status_code == 202 and result.headers["x-request-id"]
    paper_id = client.get("/api/papers").json()[0]["id"]
    paper = client.get("/api/papers/" + paper_id).json()
    assert paper["authors"] == ["Alice", "Bob"] and paper["status"] == "queued"
    pdf = client.get(f"/api/papers/{paper_id}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-disposition"].startswith("inline;")
    changed = client.patch("/api/papers/" + paper_id, json={"authors": ["Carol"], "year": 2025})
    assert changed.status_code == 200 and changed.json()["authors"] == ["Carol"]
    assert changed.json()["year"] == 2025
    duplicate = client.post(
        "/api/papers/upload", files={"file": ("paper.pdf", b"%PDF-1.4 fixture", "application/pdf")}
    )
    assert duplicate.json()["id"] == result.json()["id"]
    assert (
        client.post("/api/papers/upload", files={"file": ("not.pdf", b"not pdf")}).status_code
        == 422
    )
    assert (
        client.post("/api/papers/arxiv", json={"arxiv_id": "http://127.0.0.1/private"}).status_code
        == 422
    )


@pytest.mark.integration
def test_queue_outage_returns_durable_accepted_run(client: TestClient, empty_db: Session) -> None:
    class OfflineQueue:
        def submit(self, run_id: str) -> None:
            raise ConnectionError("offline")

    app.dependency_overrides[get_queue] = OfflineQueue
    response = client.post("/api/rag/query", json={"query": "q"})
    assert response.status_code == 202
    run = response.json()
    assert run["status"] == "queued" and run["error_code"] == "queue_unavailable"
    dispatch = empty_db.get(JobDispatch, run["id"])
    assert dispatch is not None and dispatch.dispatched_at is None


@pytest.mark.integration
def test_readiness_requires_database_redis_and_registered_worker(
    client: TestClient, redis_connection: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RQQueue, "connection", lambda self: redis_connection)
    assert client.get("/api/health").status_code == 200
    unavailable = client.get("/api/ready")
    assert (
        unavailable.status_code == 503 and unavailable.json()["error_code"] == "worker_unavailable"
    )
    queue = Queue("research", connection=redis_connection)
    rq_worker = Worker([queue], connection=redis_connection)
    rq_worker.register_birth()
    try:
        assert client.get("/api/ready").json() == {"status": "ready"}
    finally:
        rq_worker.register_death()
        redis_connection.delete(rq_worker.key)


@pytest.mark.integration
def test_job_status_and_durable_sse(
    client: TestClient, empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = client.post("/api/research", json={"research_question": "Compare methods"}).json()
    run = empty_db.get(Run, r["id"])
    assert run
    run.status = "completed"
    run.result = {"draft_report": "Verified result"}
    empty_db.add(ExecutionEvent(run_id=run.id, node="plan", payload={"objective": "Compare"}))
    empty_db.commit()

    class Scope:
        def __enter__(self) -> Session:
            return empty_db

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(runs, "session_factory", lambda: lambda: Scope())
    stream = client.get("/api/research/" + r["id"] + "/events")
    assert "event: execution" in stream.text and "event: done" in stream.text
    assert "Verified result" in stream.text
    assert client.get("/api/research/" + r["id"]).json()["status"] == "completed"
    assert (
        client.get(
            "/api/research/" + r["id"] + "/events", headers={"Last-Event-ID": "-1"}
        ).status_code
        == 422
    )
