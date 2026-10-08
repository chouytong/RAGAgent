"""Durable job dispatch, idempotent claims and terminal-state persistence."""

from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ragagent.conversations.presentation import message_presentation
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import Conversation, ExecutionEvent, Message, Paper, Run
from ragagent.domain.conversation import MessageStatus
from ragagent.errors import ApplicationError
from ragagent.settings import get_settings

TERMINAL_STATUSES = {"completed", "insufficient_evidence", "failed", "cancelled"}
JOB_TIMEOUT_SECONDS = 1800
RUNNING_GRACE_SECONDS = 120
QUEUED_TIMEOUT_SECONDS = 1800


def job_timeout_seconds(run: Run) -> int:
    frozen = run.request.get("_job_timeout_seconds")
    if isinstance(frozen, int) and not isinstance(frozen, bool) and 1 <= frozen <= 86400:
        return frozen
    return (
        get_settings().evaluation_timeout_seconds
        if run.kind.startswith("eval_")
        else JOB_TIMEOUT_SECONDS
    )


def freeze_job_timeout(run: Run) -> int:
    timeout = job_timeout_seconds(run)
    run.request = {**run.request, "_job_timeout_seconds": timeout}
    return timeout


class DispatchQueue(Protocol):
    def submit(self, run_id: str) -> None: ...


class InspectableQueue(DispatchQueue, Protocol):
    def status(self, run_id: str) -> str | None: ...


def lock_conversation_for_run(session: Session, run_id: str, *, skip_locked: bool = False) -> bool:
    """Use the same Conversation → Run lock order as message submission/deletion."""
    with session.no_autoflush:
        conversation_id = session.scalar(select(Run.conversation_id).where(Run.id == run_id))
        if conversation_id is None:
            return True
        return (
            session.scalar(
                select(Conversation.id)
                .where(Conversation.id == conversation_id)
                .with_for_update(skip_locked=skip_locked)
            )
            is not None
        )


def locked_run(session: Session, run_id: str, *, skip_locked: bool = False) -> Run | None:
    if not lock_conversation_for_run(session, run_id, skip_locked=skip_locked):
        return None
    return session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )


def dispatch_run(session: Session, queue: DispatchQueue, run_id: str) -> bool:
    """An interrupted send is retried with the same, atomically unique RQ job ID."""
    run = locked_run(session, run_id, skip_locked=True)
    if run is None or run.status != "queued":
        session.rollback()
        return False
    dispatch = session.get(JobDispatch, run_id)
    if dispatch is None:
        dispatch = JobDispatch(run_id=run_id)
        session.add(dispatch)
        session.flush()
    if dispatch.dispatched_at is not None:
        session.rollback()
        return False
    dispatch.attempts += 1
    timeout = freeze_job_timeout(run)
    try:
        timed_submit = getattr(queue, "submit_with_timeout", None)
        if timed_submit is not None:
            timed_submit(run_id, timeout)
        else:
            queue.submit(run_id)
    except Exception:
        # The durable intent is retained, including if Redis accepted the send but
        # its response was lost. The next attempt uses RQ's atomic uniqueness check.
        dispatch.last_error_code = run.error_code = "queue_unavailable"
        session.commit()
        return False
    dispatch.dispatched_at = datetime.now(UTC)
    dispatch.last_error_code = run.error_code = None
    session.add(ExecutionEvent(run_id=run.id, node="dispatched", payload={}))
    session.commit()
    return True


def claim_run(session: Session, run_id: str) -> Run | None:
    run = locked_run(session, run_id)
    if run is None or run.status != "queued":
        session.rollback()
        return None
    dispatch = session.get(JobDispatch, run_id)
    if dispatch is None:
        dispatch = JobDispatch(run_id=run_id)
        session.add(dispatch)
    dispatch.claimed_at = datetime.now(UTC)
    dispatch.dispatched_at = dispatch.dispatched_at or dispatch.claimed_at
    dispatch.last_error_code = None
    freeze_job_timeout(run)
    run.status, run.error_code = "running", None
    sync_assistant_message(session, run)
    session.add(
        ExecutionEvent(
            run_id=run.id, node="started", payload={"trace_id": run.trace_id, "kind": run.kind}
        )
    )
    session.commit()
    return run


def ensure_running(session: Session, run: Run) -> None:
    # Do not autoflush a proposed terminal status before checking ownership. A
    # late worker must not overwrite a failure already recorded by reconciliation.
    with session.no_autoflush:
        identity = inspect(run).identity
        run_id = str(identity[0]) if identity else run.id
        if not lock_conversation_for_run(session, run_id):
            raise ApplicationError("run_no_longer_active")
        status = session.scalar(select(Run.status).where(Run.id == run_id).with_for_update())
    if status != "running":
        raise ApplicationError("run_no_longer_active")


def sync_assistant_message(session: Session, run: Run) -> None:
    """Persist the message beside its Run transition; never publish an intermediate draft."""
    if run.conversation_id is None:
        return
    message = session.scalar(
        select(Message).where(
            Message.id == run.request.get("assistant_message_id"),
            Message.conversation_id == run.conversation_id,
            Message.run_id == run.id,
            Message.role == "assistant",
        )
    )
    if message is None:
        raise ApplicationError("conversation_message_unavailable")
    message.status = cast(MessageStatus, run.status)
    message.updated_at = datetime.now(UTC)
    message.metadata_json = {**message.metadata_json, "error_code": run.error_code}
    if run.status == "completed":
        result = run.result or {}
        message.content = str(result.get("answer") or result.get("draft_report") or "")
        if not message.content:
            raise ApplicationError("conversation_answer_missing")
        message.metadata_json = {
            **message.metadata_json,
            "presentation": message_presentation(result),
        }
    elif run.status == "insufficient_evidence":
        message.content = "Insufficient verified literature evidence to answer this question."
    elif run.status == "failed":
        message.content = "This request failed. You can retry it or start a new turn."
    elif run.status == "cancelled":
        message.content = "This request was cancelled."


def finish_run(session: Session, run: Run) -> None:
    if run.status not in TERMINAL_STATUSES:
        raise ApplicationError("invalid_terminal_state")
    ensure_running(session, run)
    sync_assistant_message(session, run)
    session.add(ExecutionEvent(run_id=run.id, node="finished", payload={"status": run.status}))
    session.commit()


def fail_run(session: Session, run_id: str, code: str) -> None:
    run = locked_run(session, run_id)
    if run is None or run.status in TERMINAL_STATUSES:
        session.rollback()
        return
    run.status, run.error_code = "failed", code
    sync_assistant_message(session, run)
    # Only the run that persisted a parsing event owns this ingestion. A second
    # duplicate run must never mark another job's paper as failed.
    parsing = session.scalar(
        select(ExecutionEvent)
        .where(ExecutionEvent.run_id == run.id, ExecutionEvent.node == "parsing")
        .order_by(ExecutionEvent.id.desc())
    )
    if parsing is not None:
        paper = session.get(Paper, parsing.payload.get("paper_id"))
        if paper is not None and paper.status in {"parsing", "indexing"}:
            paper.status, paper.error_code = "failed", code
    session.add(
        ExecutionEvent(
            run_id=run.id, node="failed", payload={"error_code": code, "trace_id": run.trace_id}
        )
    )
    session.commit()


def cancel_run(session: Session, run_id: str) -> Run | None:
    """Revoke worker ownership before attempting a best-effort queue stop."""
    run = locked_run(session, run_id)
    if run is None:
        session.rollback()
        return None
    if run.kind not in {"rag", "research"}:
        session.rollback()
        raise ApplicationError("run_cancel_not_supported")
    if run.status not in TERMINAL_STATUSES:
        run.status, run.error_code = "cancelled", None
        sync_assistant_message(session, run)
        session.add(ExecutionEvent(run_id=run.id, node="cancelled", payload={}))
    session.commit()
    return run


def reconcile_jobs(
    session: Session,
    queue: InspectableQueue,
    *,
    now: datetime | None = None,
) -> None:
    """Repair dispatch gaps and explicitly terminate jobs that cannot still run."""
    now = now or datetime.now(UTC)
    run_ids = list(
        session.scalars(
            select(Run.id).where(Run.status.in_(["queued", "running"])).order_by(Run.created_at)
        )
    )
    session.rollback()
    redis_available = True
    for run_id in run_ids:
        run = locked_run(session, run_id, skip_locked=True)
        if run is None or run.status not in {"queued", "running"}:
            session.rollback()
            continue
        dispatch = session.get(JobDispatch, run_id)
        if dispatch is None:
            dispatch = JobDispatch(run_id=run_id, created_at=run.created_at)
            session.add(dispatch)
            session.flush()
        started_at = dispatch.claimed_at or run.created_at
        if run.status == "running" and now - started_at > timedelta(
            seconds=job_timeout_seconds(run) + RUNNING_GRACE_SECONDS
        ):
            fail_run(session, run.id, "worker_interrupted")
            continue
        if run.status == "queued" and now - dispatch.created_at > timedelta(
            seconds=QUEUED_TIMEOUT_SECONDS
        ):
            fail_run(session, run.id, "worker_unavailable")
            continue
        if not redis_available:
            if run.status == "queued":
                run.error_code = dispatch.last_error_code = "queue_unavailable"
                session.commit()
            else:
                session.rollback()
            continue
        try:
            status = queue.status(run.id)
        except Exception:
            # A Redis outage is not evidence that an already-running worker died.
            if run.status == "queued":
                run.error_code = dispatch.last_error_code = "queue_unavailable"
                session.commit()
            else:
                session.rollback()
            # Still check all remaining deadlines without another network timeout
            # for every Run. An active job must not starve older pending jobs.
            redis_available = False
            continue
        if status in {"failed", "stopped", "finished", "canceled"} or (
            run.status == "running" and status is None
        ):
            fail_run(session, run.id, "worker_interrupted")
            continue
        if run.status == "queued" and status is None:
            dispatch.dispatched_at = None
            session.commit()
            dispatch_run(session, queue, run.id)
        elif run.status == "queued" and dispatch.dispatched_at is None:
            # This also resolves a lost enqueue acknowledgement without duplicating
            # the Redis job or allowing two workers to claim the same Run.
            session.commit()
            dispatch_run(session, queue, run.id)
        else:
            session.rollback()
