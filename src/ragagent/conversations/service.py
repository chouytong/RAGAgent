"""Local persistence adapter for the bounded, evidence-independent context builder."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ragagent.conversations.context import ContextBuilder, contextualize, is_intent_entity
from ragagent.db.models import (
    ConversationStateRecord,
    ConversationSummary,
    ExecutionEvent,
    Memory,
    Message,
    Run,
)
from ragagent.domain.conversation_context import (
    ContextConfig,
    ContextMessage,
    ConversationState,
    ResolvedContext,
    ResolvedReferent,
    RollingSummary,
    StateText,
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
        recent_tokens=settings.conversation_recent_tokens,
        summary_tokens=settings.conversation_summary_tokens,
        memory_tokens=settings.conversation_memory_tokens,
        memory_top_k=settings.conversation_memory_top_k,
    )


async def prepare_context(
    session: Session, run: Run, provider: ChatProvider, settings: Settings
) -> ResolvedContext:
    user = session.get(Message, run.request.get("user_message_id"))
    if user is None or user.role != "user" or user.conversation_id != run.conversation_id:
        raise ApplicationError("conversation_message_unavailable")
    saved_state = session.get(ConversationStateRecord, run.conversation_id)
    prior_state = (
        ConversationState.model_validate(saved_state.content)
        if saved_state is not None and saved_state.through_ordinal < user.ordinal
        else ConversationState()
    )
    entity_message_ids = [
        entity.source_id
        for entity in prior_state.resolved_entities
        if entity.source_kind == "message"
    ]
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
                or_(
                    Message.ordinal
                    > (-1 if superseded_summary_source else summary.through_ordinal),
                    Message.id.in_(entity_message_ids),
                ),
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
    typed_memories = [
        StructuredMemory(
            id=memory.id,
            kind=memory.kind,
            key=memory.key,
            content=memory.content,
            filters=MetadataFilter.model_validate(memory.filters_json)
            if memory.filters_json is not None
            else None,
        )
        for memory in memories
    ]
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
        typed_memories,
        MetadataFilter.model_validate(run.request.get("filters", {})),
        state=prior_state,
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
    entities = [
        *[
            entity
            for entity in prior_state.resolved_entities
            if entity.resolved_text
            in (
                bundle.message_sources if entity.source_kind == "message" else bundle.memory_sources
            ).get(entity.source_id, "")
        ],
        *[
            ResolvedReferent.model_validate(entity)
            for entity in resolved.metadata.get("referents", [])
        ],
    ]
    unique_entities = {
        (entity.source_kind, entity.source_id, entity.resolved_text.casefold()): entity
        for entity in entities
    }
    state = ConversationState(
        version=(saved_state.version + 1) if saved_state is not None else 1,
        through_ordinal=user.ordinal,
        goals=[
            StateText(source_kind="memory", source_id=memory.id, content=memory.content[:256])
            for memory in typed_memories
            if memory.kind in {"goal", "task"}
        ][:8],
        constraints=bundle.filters,
        constraint_memory_ids=[
            memory.id for memory in typed_memories if memory.filters is not None
        ],
        resolved_entities=list(unique_entities.values())[-20:],
        important_terms=[
            StateText(source_kind="memory", source_id=memory.id, content=memory.content[:256])
            for memory in typed_memories
            if memory.kind == "term" and is_intent_entity(memory.content)
        ][:12],
        open_questions=[
            StateText(source_kind="message", source_id=user.id, content=user.content[:256])
        ],
    )
    if saved_state is None:
        saved_state = ConversationStateRecord(conversation_id=run.conversation_id)
        session.add(saved_state)
    saved_state.content = state.model_dump()
    saved_state.version, saved_state.through_ordinal = state.version, state.through_ordinal
    saved_state.updated_at = datetime.now(UTC)
    resolved.metadata["conversation_state_version"] = state.version
    resolved.metadata["state_scientific_evidence"] = False
    # SQLAlchemy JSON mutation tracking needs a fresh mapping after metadata changes.
    run.result = {**(run.result or {}), "conversation_context": resolved.metadata}
    session.add(ExecutionEvent(run_id=run.id, node="contextualize", payload=resolved.metadata))
    session.commit()
    return resolved
