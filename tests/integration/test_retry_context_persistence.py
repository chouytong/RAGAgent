"""Persisted retry lineage replaces stale summary anchors before contextualization."""

import json
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from ragagent.api import conversations
from ragagent.conversations.service import prepare_context
from ragagent.db.models import Conversation, ConversationSummary, Message
from ragagent.domain.conversation import ConversationCreate, MessageCreate, RetryMessage
from ragagent.jobs import claim_run, finish_run
from ragagent.settings import Settings
from tests.integration.test_conversations import RecordingQueue

T = TypeVar("T", bound=BaseModel)


@pytest.mark.integration
async def test_followup_rebuilds_persisted_summary_without_superseded_attempt(
    job_sessions: sessionmaker[Session],
) -> None:
    class CapturingResolver:
        payload: dict[str, Any] | None = None

        async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
            self.payload = payload
            return schema.model_validate(
                {
                    "status": "resolved",
                    "contextualized_query": "What methodology does Dataset B use?",
                    "used_message_ids": [retry.assistant_message.id],
                    "referents": [
                        {
                            "mention": "it",
                            "resolved_text": "Dataset B",
                            "source_kind": "message",
                            "source_id": retry.assistant_message.id,
                        }
                    ],
                }
            )

    queue = RecordingQueue()
    resolver = CapturingResolver()
    with job_sessions() as session:
        conversation_id = conversations.create_conversation(ConversationCreate(), session).id
        try:
            first = conversations.send_message(
                conversation_id,
                MessageCreate(content="Which datasets?", client_request_id=uuid4()),
                session,
                queue,
            )
            run = claim_run(session, first.run.id)
            assert run is not None
            run.status = "insufficient_evidence"
            finish_run(session, run)
            original = session.get(Message, first.assistant_message.id)
            assert original is not None
            # Legacy versions could summarize refusals, including text later
            # superseded by an accepted retry. Preserve it as an audit fixture.
            original.content = "Old refusal about Dataset A."
            saved = ConversationSummary(
                conversation_id=conversation_id,
                content=f"[message:{original.id}] assistant: Old refusal about Dataset A.",
                through_ordinal=original.ordinal,
                version=1,
                metadata_json={"source_message_ids": [original.id]},
            )
            session.add(saved)
            session.commit()
            retry = conversations.retry_message(
                conversation_id,
                original.id,
                RetryMessage(client_request_id=uuid4()),
                session,
                queue,
            )
            retried_run = claim_run(session, retry.run.id)
            assert retried_run is not None
            retried_run.status, retried_run.result = "completed", {"answer": "Dataset B."}
            finish_run(session, retried_run)
            followup = conversations.send_message(
                conversation_id,
                MessageCreate(content="What methodology does it use?", client_request_id=uuid4()),
                session,
                queue,
            )
            followup_run = claim_run(session, followup.run.id)
            assert followup_run is not None
            resolved = await prepare_context(session, followup_run, resolver, Settings())
            assert resolved.contextualized_query == "What methodology does Dataset B use?"
            assert resolver.payload is not None
            assert "Dataset A" not in json.dumps(resolver.payload)
            assert "Dataset B" in json.dumps(resolver.payload)
            session.refresh(saved)
            assert (
                saved.version > 1 and original.id not in saved.metadata_json["source_message_ids"]
            )
            assert "Dataset A" not in saved.content
            assert session.get(Message, original.id).content == "Old refusal about Dataset A."
            assert session.get(Message, original.id).is_effective is False
        finally:
            session.rollback()
            session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            session.commit()
