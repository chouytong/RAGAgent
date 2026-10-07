"""Local persistence adapter for the bounded, evidence-independent context builder."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragagent.conversations.context import ContextBuilder, contextualize
from ragagent.db.models import ConversationSummary, ExecutionEvent, Memory, Message, Run
from ragagent.domain.conversation_context import (
    ContextConfig,
    ContextMessage,
    ResolvedContext,
    RollingSummary,
    StructuredMemory,
)
from ragagent.domain.research import MetadataFilter
from ragagent.errors import ApplicationError
from ragagent.jobs import ensure_running
from ragagent.providers.chat import ChatProvider
from ragagent.settings import Settings


def context_config(settings: Settings) -> ContextConfig:
    return ContextConfig(
        recent_message_limit=settings.conversation_recent_message_limit,
        max_context_tokens=settings.conversation_context_token_budget,
        summary_max_bytes=settings.conversation_summary_max_bytes,
        per_message_max_bytes=settings.conversation_message_max_bytes,
    )


async def prepare_context(
    session: Session, run: Run, provider: ChatProvider, settings: Settings
) -> ResolvedContext:
    user = session.get(Message, run.request.get("user_message_id"))
    if user is None or user.role != "user" or user.conversation_id != run.conversation_id:
        raise ApplicationError("conversation_message_unavailable")
    saved = session.get(ConversationSummary, run.conversation_id)
    summary = (
        RollingSummary(
            content=saved.content,
            through_ordinal=saved.through_ordinal,
            version=saved.version,
            source_message_ids=saved.metadata_json.get("source_message_ids", []),
        )
        if saved is not None and saved.through_ordinal < user.ordinal
        else RollingSummary()
    )
    superseded_summary_source = bool(
        summary.source_message_ids
        and session.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == run.conversation_id,
                Message.id.in_(summary.source_message_ids),
                Message.role == "assistant",
                Message.is_effective.is_(False),
            )
            .limit(1)
        )
    )
    history = list(
        session.scalars(
            select(Message)
            .where(
                Message.conversation_id == run.conversation_id,
                Message.ordinal < user.ordinal,
                Message.ordinal > (-1 if superseded_summary_source else summary.through_ordinal),
            )
            .order_by(Message.ordinal)
        )
    )
    memories = list(
        session.scalars(
            select(Memory)
            .where(Memory.conversation_id == run.conversation_id)
            .order_by(Memory.created_at, Memory.id)
        )
    )
    config = context_config(settings)
    bundle = ContextBuilder(config).build(
        user.content,
        [
            ContextMessage(
                id=message.id,
                ordinal=message.ordinal,
                role=message.role,
                content=message.content,
                status=message.status,
                retry_of_message_id=message.retry_of_message_id,
                attempt_number=message.attempt_number,
                is_effective=message.is_effective,
            )
            for message in history
        ],
        summary,
        [
            StructuredMemory(
                id=memory.id,
                kind=memory.kind,
                key=memory.key,
                content=memory.content,
                filters=(
                    MetadataFilter.model_validate(memory.filters_json)
                    if memory.filters_json is not None
                    else None
                ),
            )
            for memory in memories
        ],
        MetadataFilter.model_validate(run.request.get("filters", {})),
    )
    ensure_running(session, run)
    if bundle.summary.through_ordinal >= 0 and bundle.summary.version > summary.version:
        if saved is None:
            saved = ConversationSummary(conversation_id=run.conversation_id)
            session.add(saved)
        saved.content = bundle.summary.content
        saved.through_ordinal = bundle.summary.through_ordinal
        saved.version = bundle.summary.version
        saved.updated_at = datetime.now(UTC)
        saved.metadata_json = {
            "source_message_ids": bundle.summary.source_message_ids,
            "method": "deterministic_extractive_v1",
            "scientific_evidence": False,
        }
    metadata: dict[str, Any] = {
        **bundle.metadata,
        "context_config": config.model_dump(),
        "original_query": bundle.original_query,
    }
    run.result = {**(run.result or {}), "conversation_context": metadata}
    session.add(ExecutionEvent(run_id=run.id, node="context_prepared", payload=metadata))
    # Release row locks before provider/accounting callbacks use another session.
    session.commit()
    resolved = await contextualize(bundle, provider)
    ensure_running(session, run)
    # Usage callbacks checkpoint on another connection during the provider call.
    # Preserve that durable ledger when merging this session's context metadata.
    session.refresh(run, attribute_names=["result"])
    resolved.metadata["context_config"] = config.model_dump()
    run.result = {**(run.result or {}), "conversation_context": resolved.metadata}
    session.add(ExecutionEvent(run_id=run.id, node="contextualize", payload=resolved.metadata))
    session.commit()
    return resolved
