"""Real PostgreSQL persistence, atomic queue intent and conversation API lifecycle."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from redis import Redis
from rq.job import Job
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ragagent import worker
from ragagent.api import conversations, runs
from ragagent.api.app import app
from ragagent.api.dependencies import get_db
from ragagent.api.queue import RQQueue, get_queue
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import (
    Conversation,
    ConversationSummary,
    ExecutionEvent,
    Memory,
    Message,
    Run,
)
from ragagent.domain.conversation import ConversationCreate, MessageCreate
from ragagent.errors import ApplicationError
from ragagent.jobs import cancel_run, claim_run, ensure_running, fail_run, finish_run
from ragagent.providers.chat import Usage


class RecordingQueue:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def submit(self, run_id: str) -> None:
        self.ids.append(run_id)


@pytest.fixture
def conversation_client(empty_db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    empty_db.execute(delete(Conversation))
    empty_db.commit()

    def database() -> Iterator[Session]:
        yield empty_db

    monkeypatch.setattr("ragagent.api.dispatcher.reconcile", lambda: None)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_queue] = RecordingQueue
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def create(client: TestClient, mode: str = "rag") -> str:
    response = client.post("/api/conversations", json={"mode": mode})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def send(
    client: TestClient, conversation_id: str, content: str = "Which datasets?"
) -> dict[str, Any]:
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content, "client_request_id": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


@pytest.mark.integration
def test_create_rename_list_mode_immutable_and_automatic_title(
    conversation_client: TestClient,
) -> None:
    client = conversation_client
    conversation_id = create(client)
    turn = send(client, conversation_id, "  Which datasets\nare used?  ")
    assert turn["conversation"]["title"] == "Which datasets are used?"
    assert turn["conversation"]["active_run_id"] == turn["run"]["id"]
    assert (
        client.patch(f"/api/conversations/{conversation_id}", json={"title": "Datasets"}).json()[
            "title"
        ]
        == "Datasets"
    )
    assert (
        client.patch(f"/api/conversations/{conversation_id}", json={"mode": "research"}).status_code
        == 422
    )
    assert client.get("/api/conversations").json()[0]["id"] == conversation_id
    assert client.get("/api/conversations?limit=0").status_code == 422
    assert client.get(f"/api/conversations/{conversation_id}").json()["mode"] == "rag"


@pytest.mark.integration
@pytest.mark.parametrize("mode,query_key", [("rag", "query"), ("research", "research_question")])
def test_atomic_turn_order_association_and_duplicate_submission(
    conversation_client: TestClient, empty_db: Session, mode: str, query_key: str
) -> None:
    client = conversation_client
    conversation_id = create(client, mode)
    request = {
        "content": "Compare datasets",
        "client_request_id": str(uuid4()),
        "filters": {"year_start": 2023},
    }
    first = client.post(f"/api/conversations/{conversation_id}/messages", json=request).json()
    repeated = client.post(f"/api/conversations/{conversation_id}/messages", json=request)
    assert repeated.status_code == 202 and repeated.json()["run"]["id"] == first["run"]["id"]
    conflict = client.post(
        f"/api/conversations/{conversation_id}/messages", json={**request, "content": "changed"}
    )
    assert conflict.status_code == 409 and conflict.json()["detail"] == "idempotency_key_conflict"
    run = empty_db.get(Run, first["run"]["id"])
    assert run is not None and run.conversation_id == conversation_id
    assert run.request[query_key] == "Compare datasets"
    assert run.request["user_message_id"] == first["user_message"]["id"]
    assert run.request["assistant_message_id"] == first["assistant_message"]["id"]
    assert empty_db.get(JobDispatch, run.id) is not None
    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [message["ordinal"] for message in messages] == [0, 1]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert all(
        message["run_id"] == run.id and message["run"]["id"] == run.id for message in messages
    )
    busy = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "follow-up", "client_request_id": str(uuid4())},
    )
    assert busy.status_code == 409 and busy.json()["detail"] == "conversation_busy"


@pytest.mark.integration
def test_queue_outage_preserves_messages_and_dispatch(
    conversation_client: TestClient, empty_db: Session
) -> None:
    class OfflineQueue:
        def submit(self, run_id: str) -> None:
            raise ConnectionError("offline")

    app.dependency_overrides[get_queue] = OfflineQueue
    conversation_id = create(conversation_client)
    turn = send(conversation_client, conversation_id)
    assert turn["run"]["status"] == "queued" and turn["run"]["error_code"] == "queue_unavailable"
    assert empty_db.get(JobDispatch, turn["run"]["id"]).dispatched_at is None
    assert (
        len(conversation_client.get(f"/api/conversations/{conversation_id}/messages").json()) == 2
    )


@pytest.mark.integration
def test_failure_retry_keeps_original_turn_and_replays_idempotently(
    conversation_client: TestClient, empty_db: Session
) -> None:
    client = conversation_client
    conversation_id = create(client)
    turn = send(client, conversation_id)
    fail_run(empty_db, turn["run"]["id"], "provider_unavailable")
    original = empty_db.get(Message, turn["assistant_message"]["id"])
    assert original is not None and original.status == "failed"
    body = {"client_request_id": str(uuid4())}
    path = f"/api/conversations/{conversation_id}/messages/{original.id}/retry"
    retry = client.post(path, json=body)
    assert retry.status_code == 202, retry.text
    assert retry.json()["user_message"]["id"] == turn["user_message"]["id"]
    assert retry.json()["assistant_message"]["id"] != original.id
    assert retry.json()["assistant_message"]["ordinal"] == 2
    assert retry.json()["run"]["id"] != turn["run"]["id"]
    assert client.post(path, json=body).json()["run"]["id"] == retry.json()["run"]["id"]
    assert empty_db.get(Message, original.id).run_id == turn["run"]["id"]
    assert (
        client.get(f"/api/conversations/{conversation_id}/messages").json()[0]["run_id"]
        == turn["run"]["id"]
    )


@pytest.mark.integration
def test_summary_explicit_memory_delete_clear_and_busy_snapshot_protection(
    conversation_client: TestClient, empty_db: Session
) -> None:
    client = conversation_client
    conversation_id = create(client)
    path = f"/api/conversations/{conversation_id}"
    assert client.get(path + "/summary").json() is None
    memory = client.post(path + "/memories", json={"kind": "goal", "content": "Compare methods"})
    assert memory.status_code == 201 and memory.json()["filters"] is None
    constraint = client.post(
        path + "/memories",
        json={
            "kind": "constraint",
            "content": "Only recent papers",
            "filters": {"year_start": 2023},
        },
    )
    assert constraint.status_code == 201 and constraint.json()["filters"]["year_start"] == 2023
    empty_db.add(
        ConversationSummary(
            conversation_id=conversation_id,
            content="Discussion of methods (context only)",
            through_ordinal=1,
            metadata_json={"source_message_ids": ["fixture"]},
        )
    )
    empty_db.commit()
    assert client.get(path + "/summary").json()["source_message_ids"] == ["fixture"]
    assert client.delete(path + "/memories/" + memory.json()["id"]).status_code == 200
    assert len(client.get(path + "/memories").json()) == 1
    turn = send(client, conversation_id)
    assert client.delete(path + "/memory").status_code == 409
    assert client.delete(path + "/summary").status_code == 409
    assert client.post(path + "/clear").status_code == 409
    assert (
        client.post(
            path + "/memories", json={"kind": "term", "content": "A means DANN"}
        ).status_code
        == 409
    )
    fail_run(empty_db, turn["run"]["id"], "test_failure")
    assert client.delete(path + "/memory").status_code == 200
    assert (
        client.get(path + "/summary").json() is None and client.get(path + "/memories").json() == []
    )
    assert client.get(path + "/messages").json()
    assert client.post(path + "/clear").status_code == 200
    assert client.get(path + "/messages").json() == []
    assert empty_db.get(Run, turn["run"]["id"]) is None
    assert client.get(path).json()["title"] == "New chat"


@pytest.mark.integration
def test_hard_delete_active_conversation_erases_requests_events_and_late_writes(
    conversation_client: TestClient, empty_db: Session
) -> None:
    client = conversation_client
    conversation_id = create(client)
    turn = send(client, conversation_id)
    run = claim_run(empty_db, turn["run"]["id"])
    assert run is not None
    client.delete(f"/api/conversations/{conversation_id}")
    empty_db.expire_all()
    for model in (
        Conversation,
        Message,
        Memory,
        ConversationSummary,
        Run,
        ExecutionEvent,
        JobDispatch,
    ):
        assert empty_db.scalar(select(func.count()).select_from(model)) == 0
    with pytest.raises(ApplicationError, match="run_no_longer_active"):
        ensure_running(empty_db, run)
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404


@pytest.mark.integration
def test_turn_commit_failure_rolls_back_all_partial_messages(
    conversation_client: TestClient, empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = create(conversation_client)

    def failed_commit() -> None:
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(empty_db, "commit", failed_commit)
    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "q", "client_request_id": str(uuid4())},
    )
    assert response.status_code == 500
    assert empty_db.scalar(select(func.count()).select_from(Message)) == 0
    assert empty_db.scalar(select(func.count()).select_from(Run)) == 0
    assert empty_db.scalar(select(func.count()).select_from(JobDispatch)) == 0


@pytest.mark.integration
def test_database_constraints_and_concurrent_submissions(
    job_sessions: sessionmaker[Session],
) -> None:
    with job_sessions() as session:
        conversation_id = conversations.create_conversation(ConversationCreate(), session).id
    queue = RecordingQueue()
    request = MessageCreate(content="q", client_request_id=uuid4())

    def submit() -> str:
        with job_sessions() as session:
            return conversations.send_message(conversation_id, request, session, queue).run.id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(lambda _: submit(), range(2)))
        assert ids[0] == ids[1] and queue.ids == [ids[0]]
        with job_sessions() as session:
            with pytest.raises(HTTPException) as busy:
                conversations.send_message(
                    conversation_id,
                    MessageCreate(content="different", client_request_id=uuid4()),
                    session,
                    queue,
                )
            assert busy.value.status_code == 409
            session.rollback()
            session.add(
                Run(
                    kind="rag",
                    request={"query": "bad second active"},
                    conversation_id=conversation_id,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        with job_sessions() as session:
            session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            session.commit()


@pytest.mark.integration
def test_sse_reconnect_and_restart_read_persisted_assistant(
    conversation_client: TestClient, empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = create(conversation_client)
    turn = send(conversation_client, conversation_id)
    run = claim_run(empty_db, turn["run"]["id"])
    assert run is not None
    run.status, run.result = (
        "completed",
        {"answer": "Verified datasets [E:fixture]", "reranked_evidence": []},
    )
    finish_run(empty_db, run)

    class Scope:
        def __enter__(self) -> Session:
            return empty_db

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(runs, "session_factory", lambda: lambda: Scope())
    events = list(
        empty_db.scalars(
            select(ExecutionEvent)
            .where(ExecutionEvent.run_id == run.id)
            .order_by(ExecutionEvent.id)
        )
    )
    stream = conversation_client.get(
        f"/api/runs/{run.id}/events", headers={"Last-Event-ID": str(events[-2].id)}
    )
    assert '"node": "finished"' in stream.text and "event: done" in stream.text
    empty_db.expire_all()
    restored = conversation_client.get(f"/api/conversations/{conversation_id}/messages").json()[-1]
    assert (
        restored["status"] == "completed" and restored["content"] == "Verified datasets [E:fixture]"
    )
    assert restored["run"]["result"]["answer"] == restored["content"]


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["rag", "research"])
def test_cancel_api_is_durable_idempotent_and_rejects_late_success(
    conversation_client: TestClient, empty_db: Session, mode: str
) -> None:
    class UnavailableStopQueue(RecordingQueue):
        def cancel(self, run_id: str) -> None:
            raise ConnectionError("synthetic Redis outage during stop")

    app.dependency_overrides[get_queue] = UnavailableStopQueue
    client = conversation_client
    conversation_id = create(client, mode)
    turn = send(client, conversation_id)
    run = claim_run(empty_db, turn["run"]["id"])
    assert run is not None
    cancellation = client.post(f"/api/runs/{run.id}/cancel", json={})
    assert cancellation.status_code == 200 and cancellation.json()["status"] == "cancelled"
    assert client.post(f"/api/runs/{run.id}/cancel", json={}).json()["status"] == "cancelled"
    message = empty_db.get(Message, turn["assistant_message"]["id"])
    assert message is not None and message.status == "cancelled"
    assert (
        empty_db.scalar(
            select(func.count())
            .select_from(ExecutionEvent)
            .where(ExecutionEvent.run_id == run.id, ExecutionEvent.node == "cancelled")
        )
        == 1
    )
    run.status, run.result = "completed", {"answer": "late unverified publication"}
    with pytest.raises(ApplicationError, match="run_no_longer_active"):
        finish_run(empty_db, run)
    empty_db.rollback()
    assert empty_db.get(Run, turn["run"]["id"]).status == "cancelled"
    assert empty_db.get(Message, message.id).status == "cancelled"
    assert claim_run(empty_db, turn["run"]["id"]) is None
    retry = client.post(
        f"/api/conversations/{conversation_id}/messages/{message.id}/retry",
        json={"client_request_id": str(uuid4())},
    )
    assert retry.status_code == 202 and retry.json()["run"]["id"] != turn["run"]["id"]


@pytest.mark.integration
@pytest.mark.parametrize("terminal", ["completed", "insufficient_evidence", "failed", "cancelled"])
def test_terminal_run_message_event_have_one_atomic_commit(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    with job_sessions() as session:
        conversation_id = conversations.create_conversation(ConversationCreate(), session).id
        turn = conversations.send_message(
            conversation_id,
            MessageCreate(content="q", client_request_id=uuid4()),
            session,
            RecordingQueue(),
        )
        run_id, message_id = turn.run.id, turn.assistant_message.id
        run = claim_run(session, run_id)
        assert run is not None
        original_commit = session.commit
        checked = False

        def inspect_before_commit() -> None:
            nonlocal checked
            with job_sessions() as observer:
                assert observer.get(Run, run_id).status == "running"
                assert observer.get(Message, message_id).status == "running"
                assert (
                    observer.scalar(
                        select(func.count())
                        .select_from(ExecutionEvent)
                        .where(
                            ExecutionEvent.run_id == run_id,
                            ExecutionEvent.node.in_(["finished", "failed", "cancelled"]),
                        )
                    )
                    == 0
                )
            checked = True
            original_commit()

        monkeypatch.setattr(session, "commit", inspect_before_commit)
        try:
            if terminal == "failed":
                fail_run(session, run_id, "synthetic_failure")
            elif terminal == "cancelled":
                cancel_run(session, run_id)
            else:
                run.status, run.result = terminal, {"answer": "Verified answer"}
                finish_run(session, run)
            assert checked
            with job_sessions() as observer:
                assert observer.get(Run, run_id).status == terminal
                message = observer.get(Message, message_id)
                assert message.status == terminal and message.content
                assert (
                    observer.scalar(
                        select(func.count())
                        .select_from(ExecutionEvent)
                        .where(
                            ExecutionEvent.run_id == run_id,
                            ExecutionEvent.node.in_(["finished", "failed", "cancelled"]),
                        )
                    )
                    == 1
                )
        finally:
            monkeypatch.setattr(session, "commit", original_commit)
            session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            session.commit()


@pytest.mark.integration
def test_terminal_commit_error_can_rollback_message_and_result_together(
    conversation_client: TestClient, empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = create(conversation_client)
    turn = send(conversation_client, conversation_id)
    run = claim_run(empty_db, turn["run"]["id"])
    assert run is not None

    def rejected_commit() -> None:
        raise RuntimeError("synthetic database commit failure")

    monkeypatch.setattr(empty_db, "commit", rejected_commit)
    run.status, run.result = "completed", {"answer": "Verified answer"}
    with pytest.raises(RuntimeError, match="synthetic database commit failure"):
        finish_run(empty_db, run)
    empty_db.rollback()
    assert empty_db.get(Run, turn["run"]["id"]).status == "running"
    assert empty_db.get(Run, turn["run"]["id"]).result is None
    message = empty_db.get(Message, turn["assistant_message"]["id"])
    assert message.status == "running" and message.content == ""
    assert (
        empty_db.scalar(
            select(func.count())
            .select_from(ExecutionEvent)
            .where(ExecutionEvent.run_id == turn["run"]["id"], ExecutionEvent.node == "finished")
        )
        == 0
    )


@pytest.mark.integration
def test_secret_http_rejection_never_persists_or_echoes_input(
    conversation_client: TestClient, empty_db: Session
) -> None:
    conversation_id = create(conversation_client)
    synthetic_key = "sk-" + "syntheticcredential" * 3
    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": synthetic_key, "client_request_id": str(uuid4())},
    )
    assert response.status_code == 422 and synthetic_key not in response.text
    assert empty_db.scalar(select(func.count()).select_from(Message)) == 0
    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/memories",
        json={"kind": "goal", "content": synthetic_key},
    )
    assert response.status_code == 422 and synthetic_key not in response.text
    assert empty_db.scalar(select(func.count()).select_from(Memory)) == 0


@pytest.mark.integration
def test_real_redis_queued_conversation_cancel_revokes_worker_claim(
    conversation_client: TestClient,
    empty_db: Session,
    redis_connection: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = RQQueue()
    monkeypatch.setattr(queue, "connection", lambda: redis_connection)
    app.dependency_overrides[get_queue] = lambda: queue
    conversation_id = create(conversation_client)
    turn = send(conversation_client, conversation_id)
    job = Job.fetch(turn["run"]["id"], connection=redis_connection)
    try:
        assert job.get_status(refresh=True).value == "queued"
        response = conversation_client.post(f"/api/runs/{job.id}/cancel", json={})
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
        assert job.get_status(refresh=True).value == "canceled"
        assert claim_run(empty_db, job.id) is None
        message = empty_db.get(Message, turn["assistant_message"]["id"])
        assert message.status == "cancelled"
    finally:
        job.delete(remove_from_queue=True)


@pytest.mark.integration
def test_billed_response_after_cancel_is_recorded_without_authorizing_new_calls(
    job_sessions: sessionmaker[Session],
) -> None:
    with job_sessions() as session:
        conversation_id = conversations.create_conversation(ConversationCreate(), session).id
        turn = conversations.send_message(
            conversation_id,
            MessageCreate(content="q", client_request_id=uuid4()),
            session,
            RecordingQueue(),
        )
        run = claim_run(session, turn.run.id)
        assert run is not None
        adapter = SimpleNamespace(usage=Usage())
        tracked: dict[str, Usage] = {}
        worker.track_usage(session, run, tracked, "analyst", adapter)
        try:
            adapter.usage.begin_call()
            cancel_run(session, run.id)
            with pytest.raises(ApplicationError, match="run_no_longer_active"):
                adapter.usage.record_cost(0.03)
            with job_sessions() as observer:
                cancelled = observer.get(Run, turn.run.id)
                assert cancelled.status == "cancelled"
                ledger = cancelled.result["usage"]["analyst"]
                assert ledger["known_cost"] == pytest.approx(0.03)
                assert ledger["calls"] == 1 and ledger["in_flight_calls"] == 0
                assert observer.get(Message, turn.assistant_message.id).status == "cancelled"
            with pytest.raises(ApplicationError, match="run_no_longer_active"):
                adapter.usage.begin_call()
            with job_sessions() as observer:
                persisted = observer.get(Run, turn.run.id).result["usage"]["analyst"]
                assert persisted["calls"] == 1 and persisted["known_cost"] == pytest.approx(0.03)
                assert persisted["in_flight_calls"] == 0
                assert observer.get(Message, turn.assistant_message.id).status == "cancelled"
        finally:
            adapter.usage.on_update = None
            session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            session.commit()
