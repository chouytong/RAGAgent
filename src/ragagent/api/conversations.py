"""Local conversation persistence over the existing durable Run/dispatch queue."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Row, Select, delete, func, select
from sqlalchemy.orm import Session

from ragagent.api.papers import DB, QueueDep
from ragagent.api.queue import JobQueue
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import (
    Conversation,
    ConversationStateRecord,
    ConversationSummary,
    ExecutionEvent,
    Memory,
    Message,
    Run,
    new_id,
)
from ragagent.domain.conversation import (
    ConversationCreate,
    ConversationMode,
    ConversationPatch,
    ConversationResponse,
    MemoryCreate,
    MemoryResponse,
    MessageCreate,
    MessageSummary,
    RetryMessage,
    RunSummary,
    SummaryResponse,
    TurnResponse,
)
from ragagent.domain.conversation_context import ConversationState
from ragagent.domain.research import MetadataFilter
from ragagent.jobs import dispatch_run, sync_assistant_message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
ACTIVE_STATUSES = ("queued", "running")
MAX_ORDINAL = 2147483647


def get_conversation(db: Session, conversation_id: str, *, lock: bool = False) -> Conversation:
    statement = select(Conversation).where(Conversation.id == conversation_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    conversation = db.scalar(statement)
    if conversation is None:
        raise HTTPException(404, "conversation_not_found")
    return conversation


def active_run_id(db: Session, conversation_id: str) -> str | None:
    return db.scalar(
        select(Run.id).where(
            Run.conversation_id == conversation_id, Run.status.in_(ACTIVE_STATUSES)
        )
    )


def require_idle(db: Session, conversation_id: str) -> None:
    if active_run_id(db, conversation_id) is not None:
        raise HTTPException(409, "conversation_busy")


def conversation_summary(conversation: Conversation, active_id: str | None) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        mode=conversation.mode,
        archived=conversation.archived,
        metadata=conversation.metadata_json,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        active_run_id=active_id,
    )


def conversation_response(db: Session, conversation: Conversation) -> ConversationResponse:
    return conversation_summary(conversation, active_run_id(db, conversation.id))


def message_selection(
    conversation_id: str,
) -> Select[Message, str, str, str, str, str | None, datetime]:
    # Project individual Run columns rather than loading a Run ORM instance:
    # SELECT Run would fetch its potentially megabyte-sized request/result too.
    return (
        select(
            Message,
            Run.id.label("summary_run_id"),
            Run.kind.label("summary_kind"),
            Run.status.label("summary_status"),
            Run.trace_id.label("summary_trace_id"),
            Run.error_code.label("summary_error_code"),
            Run.created_at.label("summary_created_at"),
        )
        .outerjoin(Run, Run.id == Message.run_id)
        .where(Message.conversation_id == conversation_id)
        .execution_options(populate_existing=True)
    )


def message_response(row: Row[Message, str, str, str, str, str | None, datetime]) -> MessageSummary:
    message = row[0]
    snapshot = (
        RunSummary(
            id=row.summary_run_id,
            kind=row.summary_kind,
            status=row.summary_status,
            trace_id=row.summary_trace_id,
            error_code=row.summary_error_code,
            created_at=row.summary_created_at,
        )
        if row.summary_run_id is not None
        else None
    )
    return MessageSummary(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        ordinal=message.ordinal,
        retry_of_message_id=message.retry_of_message_id,
        attempt_number=message.attempt_number,
        is_effective=message.is_effective,
        run_id=message.run_id,
        status=message.status,
        metadata=message.metadata_json,
        created_at=message.created_at,
        updated_at=message.updated_at,
        run=snapshot,
    )


def turn_response(db: Session, conversation: Conversation, run: Run) -> TurnResponse:
    messages = {
        row[0].id: message_response(row)
        for row in db.execute(
            message_selection(conversation.id).where(
                Message.id.in_(
                    [run.request["user_message_id"], run.request["assistant_message_id"]]
                )
            )
        )
    }
    user = messages.get(run.request["user_message_id"])
    assistant = messages.get(run.request["assistant_message_id"])
    if user is None or assistant is None:
        raise HTTPException(409, "conversation_turn_changed")
    return TurnResponse(
        conversation=conversation_response(db, conversation),
        user_message=user,
        assistant_message=assistant,
        run=RunSummary.model_validate(run),
    )


def previous_request(db: Session, conversation_id: str, client_request_id: str) -> Run | None:
    return db.scalar(
        select(Run).where(
            Run.conversation_id == conversation_id, Run.client_request_id == client_request_id
        )
    )


def next_ordinal(db: Session, conversation_id: str) -> int:
    last = db.scalar(
        select(func.max(Message.ordinal)).where(Message.conversation_id == conversation_id)
    )
    return last + 1 if last is not None else 0


def persist_turn(
    db: Session,
    queue: JobQueue,
    conversation: Conversation,
    user: Message,
    assistant: Message,
    request: dict[str, object],
    client_request_id: str,
) -> TurnResponse:
    run = Run(
        id=new_id(),
        kind=conversation.mode,
        conversation_id=conversation.id,
        client_request_id=client_request_id,
        request={
            **request,
            "conversation_id": conversation.id,
            "user_message_id": user.id,
            "assistant_message_id": assistant.id,
        },
    )
    assistant.run_id = run.id
    # A retry retains the original user/Run association instead of rewriting history.
    if user.run_id is None:
        user.run_id = run.id
    conversation.updated_at = datetime.now(UTC)
    try:
        db.add(run)
        db.flush()
        db.add_all([user, assistant, JobDispatch(run_id=run.id)])
        db.commit()  # Both messages, Run and durable queue intent become visible together.
    except Exception:
        db.rollback()
        raise
    dispatch_run(db, queue, run.id)
    return turn_response(db, conversation, run)


@router.post("", status_code=201)
def create_conversation(request: ConversationCreate, db: DB) -> ConversationResponse:
    conversation = Conversation(
        mode=request.mode,
        title=request.title or "New chat",
        metadata_json={"auto_title": request.title is None},
    )
    db.add(conversation)
    db.commit()
    return conversation_response(db, conversation)


@router.get("")
def list_conversations(
    db: DB, limit: int = 50, offset: int = 0, mode: ConversationMode | None = None
) -> list[ConversationResponse]:
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "invalid_pagination")
    statement = select(Conversation)
    if mode is not None:
        statement = statement.where(Conversation.mode == mode)
    conversations = list(
        db.scalars(
            statement.order_by(Conversation.updated_at.desc(), Conversation.id)
            .limit(limit)
            .offset(offset)
        )
    )
    if not conversations:
        return []
    active_ids = dict(
        db.execute(
            select(Run.conversation_id, Run.id).where(
                Run.conversation_id.in_([conversation.id for conversation in conversations]),
                Run.status.in_(ACTIVE_STATUSES),
            )
        ).all()
    )
    return [
        conversation_summary(conversation, active_ids.get(conversation.id))
        for conversation in conversations
    ]


@router.get("/{conversation_id}")
def read_conversation(conversation_id: str, db: DB) -> ConversationResponse:
    return conversation_response(db, get_conversation(db, conversation_id))


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: str, request: ConversationPatch, db: DB
) -> ConversationResponse:
    conversation = get_conversation(db, conversation_id, lock=True)
    conversation.title = request.title
    conversation.metadata_json = {**conversation.metadata_json, "auto_title": False}
    conversation.updated_at = datetime.now(UTC)
    db.commit()
    return conversation_response(db, conversation)


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, db: DB, queue: QueueDep) -> dict[str, str]:
    conversation = get_conversation(db, conversation_id, lock=True)
    # Revoke execution ownership with the same Conversation -> Run lock order
    # used by submission and worker transitions. Keep IDs for the lock-free RQ
    # stop below: the privacy-preserving hard delete erases the Run rows too.
    active_runs = list(
        db.scalars(
            select(Run)
            .where(Run.conversation_id == conversation_id, Run.status.in_(ACTIVE_STATUSES))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    active_ids = [run.id for run in active_runs]
    for run in active_runs:
        run.status, run.error_code = "cancelled", None
        sync_assistant_message(db, run)
        db.add(ExecutionEvent(run_id=run.id, node="cancelled", payload={"reason": "deleted"}))
    db.flush()
    db.delete(conversation)
    db.commit()  # FK cascades include Run requests/results, dispatches and events.
    stop = getattr(queue, "cancel", None)
    if stop is not None:
        for run_id in active_ids:
            try:
                stop(run_id)
            except Exception:
                # A Redis outage cannot restore ownership or block data deletion.
                pass
    return {"status": "deleted"}


@router.post("/{conversation_id}/clear")
def clear_conversation(conversation_id: str, db: DB) -> ConversationResponse:
    conversation = get_conversation(db, conversation_id, lock=True)
    require_idle(db, conversation_id)
    db.execute(delete(Run).where(Run.conversation_id == conversation_id))
    for model in (Message, ConversationSummary, ConversationStateRecord, Memory):
        db.execute(delete(model).where(model.conversation_id == conversation_id))
    conversation.title = "New chat"
    conversation.metadata_json = {"auto_title": True}
    conversation.updated_at = datetime.now(UTC)
    db.commit()
    return conversation_response(db, conversation)


def validate_message_cursor_query(request: Request) -> None:
    if any(
        len(request.query_params.getlist(name)) > 1
        for name in ("offset", "before_ordinal", "after_ordinal")
    ):
        raise HTTPException(422, "message_cursor_conflict")


@router.get("/{conversation_id}/messages", dependencies=[Depends(validate_message_cursor_query)])
def list_messages(
    conversation_id: str,
    db: DB,
    limit: int = 50,
    offset: int | None = None,
    before_ordinal: int | None = None,
    after_ordinal: int | None = None,
) -> list[MessageSummary]:
    if (
        not 1 <= limit <= 500
        or (offset is not None and not 0 <= offset <= MAX_ORDINAL)
        or (before_ordinal is not None and not 0 <= before_ordinal <= MAX_ORDINAL)
        or (after_ordinal is not None and not 0 <= after_ordinal <= MAX_ORDINAL)
    ):
        raise HTTPException(422, "invalid_pagination")
    if sum(value is not None for value in (offset, before_ordinal, after_ordinal)) > 1:
        raise HTTPException(422, "message_cursor_conflict")
    get_conversation(db, conversation_id)
    statement = message_selection(conversation_id)
    ascending = offset is not None or after_ordinal is not None
    if before_ordinal is not None:
        statement = statement.where(Message.ordinal < before_ordinal)
    elif after_ordinal is not None:
        statement = statement.where(Message.ordinal > after_ordinal)
    statement = statement.order_by(Message.ordinal if ascending else Message.ordinal.desc())
    if offset is not None:
        # Explicit offset, including offset=0, retains legacy earliest-first
        # pagination. Omitting it selects the latest bounded window instead.
        statement = statement.offset(offset)
    messages = [message_response(row) for row in db.execute(statement.limit(limit))]
    return messages if ascending else list(reversed(messages))


@router.get("/{conversation_id}/messages/{message_id}")
def read_message(conversation_id: str, message_id: str, db: DB) -> MessageSummary:
    get_conversation(db, conversation_id)
    row = db.execute(
        message_selection(conversation_id).where(Message.id == message_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "message_not_found")
    # Incremental append cursors cannot detect a placeholder that transitions at
    # the same ordinal. SSE/reconnect reconciliation refreshes this exact row.
    return message_response(row)


@router.post("/{conversation_id}/messages", status_code=202)
def send_message(
    conversation_id: str, request: MessageCreate, db: DB, queue: QueueDep
) -> TurnResponse:
    conversation = get_conversation(db, conversation_id, lock=True)
    client_id = str(request.client_request_id)
    previous = previous_request(db, conversation_id, client_id)
    if previous is not None:
        user = db.get(Message, previous.request["user_message_id"])
        if (
            user is None
            or user.content != request.content
            or previous.request.get("filters") != request.filters.model_dump()
            or previous.request.get("retry_of_message_id") is not None
        ):
            raise HTTPException(409, "idempotency_key_conflict")
        db.commit()  # Release the conversation lock before the idempotent dispatch check.
        dispatch_run(db, queue, previous.id)
        return turn_response(db, conversation, previous)
    require_idle(db, conversation_id)
    ordinal = next_ordinal(db, conversation_id)
    user = Message(
        id=new_id(),
        conversation_id=conversation_id,
        role="user",
        content=request.content,
        ordinal=ordinal,
        status="completed",
    )
    assistant = Message(
        id=new_id(),
        conversation_id=conversation_id,
        role="assistant",
        content="",
        ordinal=ordinal + 1,
        status="queued",
    )
    if conversation.metadata_json.get("auto_title") and ordinal == 0:
        conversation.title = " ".join(request.content.split())[:80]
    query_key = "query" if conversation.mode == "rag" else "research_question"
    return persist_turn(
        db,
        queue,
        conversation,
        user,
        assistant,
        {query_key: request.content, "filters": request.filters.model_dump()},
        client_id,
    )


@router.post("/{conversation_id}/messages/{message_id}/retry", status_code=202)
def retry_message(
    conversation_id: str, message_id: str, request: RetryMessage, db: DB, queue: QueueDep
) -> TurnResponse:
    conversation = get_conversation(db, conversation_id, lock=True)
    client_id = str(request.client_request_id)
    previous = previous_request(db, conversation_id, client_id)
    if previous is not None:
        if previous.request.get("retry_of_message_id") != message_id:
            raise HTTPException(409, "idempotency_key_conflict")
        db.commit()
        dispatch_run(db, queue, previous.id)
        return turn_response(db, conversation, previous)
    require_idle(db, conversation_id)
    original = db.get(Message, message_id)
    if (
        original is None
        or original.conversation_id != conversation_id
        or original.role != "assistant"
    ):
        raise HTTPException(404, "message_not_found")
    if (
        original.status not in {"failed", "cancelled", "insufficient_evidence"}
        or not original.is_effective
        or original.ordinal != next_ordinal(db, conversation_id) - 1
    ):
        raise HTTPException(409, "message_not_retryable")
    old_run = db.get(Run, original.run_id) if original.run_id else None
    if old_run is None:
        raise HTTPException(409, "message_not_retryable")
    user = db.get(Message, old_run.request["user_message_id"])
    if user is None:
        raise HTTPException(409, "conversation_turn_changed")
    assistant = Message(
        id=new_id(),
        conversation_id=conversation_id,
        role="assistant",
        content="",
        ordinal=original.ordinal + 1,
        retry_of_message_id=original.id,
        attempt_number=original.attempt_number + 1,
        is_effective=True,
        status="queued",
        metadata_json={
            "retry_of_message_id": message_id,
            "attempt_number": original.attempt_number + 1,
            "is_effective": True,
        },
    )
    original.is_effective = False
    original.metadata_json = {
        **original.metadata_json,
        "is_effective": False,
        "superseded_by_message_id": assistant.id,
    }
    query_key = "query" if conversation.mode == "rag" else "research_question"
    return persist_turn(
        db,
        queue,
        conversation,
        user,
        assistant,
        {
            query_key: user.content,
            "filters": old_run.request["filters"],
            "retry_of_message_id": message_id,
        },
        client_id,
    )


@router.get("/{conversation_id}/summary")
def read_summary(conversation_id: str, db: DB) -> SummaryResponse | None:
    get_conversation(db, conversation_id)
    summary = db.get(ConversationSummary, conversation_id)
    if summary is None:
        return None
    return SummaryResponse(
        conversation_id=summary.conversation_id,
        content=summary.content,
        through_ordinal=summary.through_ordinal,
        version=summary.version,
        source_message_ids=summary.metadata_json.get("source_message_ids", []),
        metadata=summary.metadata_json,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
    )


@router.delete("/{conversation_id}/summary")
def delete_summary(conversation_id: str, db: DB) -> dict[str, str]:
    get_conversation(db, conversation_id, lock=True)
    require_idle(db, conversation_id)
    db.execute(
        delete(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id)
    )
    db.execute(
        delete(ConversationStateRecord).where(
            ConversationStateRecord.conversation_id == conversation_id
        )
    )
    db.commit()
    return {"status": "deleted"}


def memory_response(memory: Memory) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        conversation_id=memory.conversation_id,
        kind=memory.kind,
        key=memory.key,
        content=memory.content,
        filters=MetadataFilter.model_validate(memory.filters_json)
        if memory.filters_json is not None
        else None,
        metadata=memory.metadata_json,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


@router.get("/{conversation_id}/memories")
def list_memories(conversation_id: str, db: DB) -> list[MemoryResponse]:
    get_conversation(db, conversation_id)
    return [
        memory_response(memory)
        for memory in db.scalars(
            select(Memory)
            .where(Memory.conversation_id == conversation_id)
            .order_by(Memory.created_at, Memory.id)
        )
    ]


@router.post("/{conversation_id}/memories", status_code=201)
def create_memory(conversation_id: str, request: MemoryCreate, db: DB) -> MemoryResponse:
    conversation = get_conversation(db, conversation_id, lock=True)
    require_idle(db, conversation_id)
    count = (
        db.scalar(
            select(func.count())
            .select_from(Memory)
            .where(Memory.conversation_id == conversation_id)
        )
        or 0
    )
    if count >= 100:
        raise HTTPException(409, "conversation_memory_limit")
    memory = Memory(
        conversation_id=conversation_id,
        kind=request.kind,
        key=request.key,
        content=request.content,
        filters_json=request.filters.model_dump() if request.filters is not None else None,
    )
    db.execute(
        delete(ConversationStateRecord).where(
            ConversationStateRecord.conversation_id == conversation_id
        )
    )
    db.add(memory)
    conversation.updated_at = datetime.now(UTC)
    db.commit()
    return memory_response(memory)


@router.delete("/{conversation_id}/memories/{memory_id}")
def delete_memory(conversation_id: str, memory_id: str, db: DB) -> dict[str, str]:
    get_conversation(db, conversation_id, lock=True)
    require_idle(db, conversation_id)
    memory = db.get(Memory, memory_id)
    if memory is None or memory.conversation_id != conversation_id:
        raise HTTPException(404, "memory_not_found")
    db.delete(memory)
    db.execute(
        delete(ConversationStateRecord).where(
            ConversationStateRecord.conversation_id == conversation_id
        )
    )
    db.commit()
    return {"status": "deleted"}


@router.delete("/{conversation_id}/memory")
def clear_memory(conversation_id: str, db: DB) -> dict[str, str]:
    get_conversation(db, conversation_id, lock=True)
    require_idle(db, conversation_id)
    db.execute(delete(Memory).where(Memory.conversation_id == conversation_id))
    db.execute(
        delete(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id)
    )
    db.execute(
        delete(ConversationStateRecord).where(
            ConversationStateRecord.conversation_id == conversation_id
        )
    )
    db.commit()
    return {"status": "deleted"}


@router.get("/{conversation_id}/state")
def get_intent_state(conversation_id: str, db: DB) -> ConversationState | None:
    get_conversation(db, conversation_id)
    state = db.get(ConversationStateRecord, conversation_id)
    return ConversationState.model_validate(state.content) if state is not None else None
