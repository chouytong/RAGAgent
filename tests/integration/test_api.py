from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ragagent.api import runs
from ragagent.api.app import app
from ragagent.api.dependencies import get_db
from ragagent.api.queue import get_queue
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
