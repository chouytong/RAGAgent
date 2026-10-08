"""Real PostgreSQL/vector/worker flows with explicitly scripted, unpaid providers."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent import worker
from ragagent.conversations.service import prepare_context
from ragagent.db.models import (
    Chunk,
    Conversation,
    ConversationStateRecord,
    ConversationSummary,
    ExecutionEvent,
    Memory,
    Message,
    Paper,
    Run,
    Section,
    new_id,
)
from ragagent.domain.conversation_context import QueryContextualization
from ragagent.domain.research import AnswerDraft, QueryPlan, VerificationResponse
from ragagent.graphs.state import AnalysisResult, ResearchPlan
from ragagent.jobs import claim_run
from ragagent.providers.chat import Usage
from ragagent.settings import Settings
from tests.integration.test_retrieval import Embedder, FixtureReranker

T = TypeVar("T", bound=BaseModel)
TEXT = (
    "Dataset A contains 120 participants. Dataset B contains 80 participants. "
    "The method uses contrastive training."
)


class ScientificScript:
    """Fixture only: deterministic outputs test orchestration, not model quality."""

    def __init__(self) -> None:
        self.usage = Usage()
        self.payloads: list[tuple[type[BaseModel], dict[str, Any]]] = []

    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.payloads.append((schema, payload))
        self.usage.begin_call()
        self.usage.record_cost(0.01)
        response: dict[str, Any]
        if schema is QueryContextualization:
            original = payload["current_query"]
            mention = next(
                (value for value in ("the second one", "Which one", "it") if value in original),
                None,
            )
            if mention is None:
                response = {"status": "resolved", "contextualized_query": original}
            else:
                name = "Dataset B" if mention == "the second one" else "Dataset A"
                sources = [(m["id"], m["content"]) for m in payload["recent_messages"]]
                sources += re.findall(
                    r"\[message:([\w-]+)\] (?:user|assistant): ([^\n]+)",
                    payload["conversation_summary"]["content"],
                )
                sources += [
                    (item["source_id"], item["resolved_text"])
                    for item in payload.get("resolved_intent_entities_not_evidence", [])
                    if item["source_kind"] == "message"
                ]
                identity = next(
                    identity for identity, content in reversed(sources) if name in content
                )
                response = {
                    "status": "resolved",
                    "contextualized_query": original.replace(mention, name),
                    "used_message_ids": [identity],
                    "referents": [
                        {
                            "mention": mention,
                            "resolved_text": name,
                            "source_kind": "message",
                            "source_id": identity,
                        }
                    ],
                }
                if mention == "Which one":
                    response["contextualized_query"] = (
                        "Which dataset, Dataset A or Dataset B, has the largest sample size?"
                    )
                    other_id = next(
                        identity
                        for identity, content in reversed(sources)
                        if "Dataset B" in content
                    )
                    response["used_message_ids"] = list(dict.fromkeys([identity, other_id]))
                    response["referents"].append(
                        {
                            "mention": mention,
                            "resolved_text": "Dataset B",
                            "source_kind": "message",
                            "source_id": other_id,
                        }
                    )
        elif schema is QueryPlan:
            response = {"queries": [payload["query"]], "required_aspects": ["dataset"]}
        elif schema is ResearchPlan:
            question = payload["research_question"]
            response = {
                "objective": question,
                "required_aspects": ["dataset"],
                "subtasks": [
                    {
                        "task_id": "datasets",
                        "question": question,
                        "aspect": "dataset",
                        "queries": [question],
                    }
                ],
            }
        elif schema in {AnswerDraft, AnalysisResult}:
            question = payload.get("query", payload.get("question", ""))
            quote = (
                "The method uses contrastive training."
                if "methodology" in question
                else "Dataset B contains 80 participants."
                if "Dataset B" in question and "largest" not in question
                else "Dataset A contains 120 participants."
            )
            response = {
                "claims": [
                    {
                        "claim_id": "dataset",
                        "text": quote,
                        "aspect": "dataset",
                        "evidence_ids": [payload["evidence"][0]["evidence_id"]],
                    }
                ]
            }
            if "Which datasets" in question:
                response["claims"].append(
                    {
                        "claim_id": "second",
                        "text": "Dataset B contains 80 participants.",
                        "aspect": "dataset",
                        "evidence_ids": [payload["evidence"][0]["evidence_id"]],
                    }
                )
        elif schema is VerificationResponse:
            response = {
                "question_answered": True,
                "verdicts": [
                    {"claim_id": c["claim_id"], "supported": True, "reason": "fixture quote"}
                    for c in payload["claims"]
                ],
                "supported_pairs": [
                    {"claim_id": c["claim_id"], "evidence_id": eid}
                    for c in payload["claims"]
                    for eid in c["evidence_ids"]
                ],
            }
        else:
            raise AssertionError(f"Unexpected fixture schema: {schema.__name__}")
        return schema.model_validate(response)


@contextmanager
def local_conversation(sessions: sessionmaker[Session], mode: str) -> Iterator[tuple[str, str]]:
    with sessions() as session:
        paper = Paper(
            title="DEMO ONLY / NOT A BENCHMARK",
            sha256=uuid4().hex * 2,
            original_path="fixture.pdf",
            year=2024,
            status="indexed",
            embedding_model="test:384",
        )
        conversation = Conversation(mode=mode, title="Fixture")
        session.add_all([paper, conversation])
        session.flush()
        section = Section(paper_id=paper.id, title="Datasets", path="Datasets", ordinal=0)
        session.add(section)
        session.flush()
        session.add(
            Chunk(
                paper_id=paper.id,
                section_id=section.id,
                section_path="Datasets",
                page_start=1,
                page_end=1,
                element_type="text",
                content=TEXT,
                token_count=22,
                ordinal=0,
                embedding=[1.0] + [0.0] * 383,
            )
        )
        session.commit()
        cid, pid = conversation.id, paper.id
    try:
        yield cid, pid
    finally:
        with sessions() as session:
            session.execute(delete(Conversation).where(Conversation.id == cid))
            session.execute(delete(Paper).where(Paper.id == pid))
            session.commit()


def queued_turn(sessions: sessionmaker[Session], cid: str, pid: str, query: str) -> str:
    with sessions() as session:
        conversation = session.get(Conversation, cid)
        assert conversation is not None
        ordinals = list(
            session.scalars(select(Message.ordinal).where(Message.conversation_id == cid))
        )
        ordinal = max(ordinals, default=-1) + 1
        uid, aid = new_id(), new_id()
        run = Run(
            conversation_id=cid,
            kind=conversation.mode,
            client_request_id=new_id(),
            request={
                "query" if conversation.mode == "rag" else "research_question": query,
                "filters": {"paper_ids": [pid]},
                "user_message_id": uid,
                "assistant_message_id": aid,
            },
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                Message(
                    id=uid,
                    conversation_id=cid,
                    role="user",
                    content=query,
                    ordinal=ordinal,
                    run_id=run.id,
                ),
                Message(
                    id=aid,
                    conversation_id=cid,
                    role="assistant",
                    content="",
                    ordinal=ordinal + 1,
                    run_id=run.id,
                    status="queued",
                ),
            ]
        )
        session.commit()
        return run.id


def configure_worker(
    monkeypatch: pytest.MonkeyPatch, sessions: sessionmaker[Session]
) -> dict[str, ScientificScript]:
    agents = {
        name: ScientificScript() for name in ("supervisor", "retriever", "analyst", "reviewer")
    }
    monkeypatch.setattr(worker, "session_factory", lambda: sessions)
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: Settings(_env_file=None, conversation_recent_message_limit=2),
    )
    monkeypatch.setattr(worker, "make_agents", lambda _: agents)
    monkeypatch.setattr(worker, "make_embedder", lambda _: Embedder())
    monkeypatch.setattr(worker, "make_reranker", lambda _: FixtureReranker())
    return agents


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_persisted_followup_and_restart_keep_fresh_cited_evidence(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    agents = configure_worker(monkeypatch, job_sessions)
    with local_conversation(job_sessions, mode) as (cid, pid):
        queries = [
            "Which datasets are used?",
            "Which one has the largest sample size?",
            "What methodology did it use?",
        ]
        for query in queries:
            rid = queued_turn(job_sessions, cid, pid, query)
            await worker.execute_async(rid)
            # A new Session each turn emulates app/worker restart: no process history is used.
            with job_sessions() as session:
                run = session.get(Run, rid)
                assert run is not None and run.status == "completed"
                assert run.result is not None
                message = session.get(Message, run.request["assistant_message_id"])
                assert message is not None and message.status == run.status
                assert "[E:" in message.content
                context = run.result["conversation_context"]
                assert context["original_query"] == query
                state = session.get(ConversationStateRecord, cid)
                assert state is not None and state.version == queries.index(query) + 1
                assert state.content["scientific_evidence"] is False
                assert state.content["through_ordinal"] < message.ordinal
                if query != queries[0]:
                    assert "Dataset A" in context["contextualized_query"]
                    assert context["used_message_ids"]
                    assert state.content["resolved_entities"]
                    assert all(
                        "participants" not in entity["resolved_text"]
                        for entity in state.content["resolved_entities"]
                    )
                if query == queries[1]:
                    assert "Dataset B" in context["contextualized_query"]
                    assert "largest" in context["contextualized_query"]
                    assert "120 participants" in message.content
                validation = (
                    run.result["citation_validation"]
                    if mode == "rag"
                    else run.result["review_result"]["validation"]
                )
                assert validation["valid"]
                assert session.scalar(
                    select(ExecutionEvent.id).where(
                        ExecutionEvent.run_id == rid, ExecutionEvent.node == "finished"
                    )
                )
                if query == queries[-1]:
                    assert "contrastive training" in message.content
                    summary = session.get(ConversationSummary, cid)
                    assert summary is not None and summary.through_ordinal == 1
                    assert summary.metadata_json["scientific_evidence"] is False
        assert all(adapter.usage.on_update is None for adapter in agents.values())


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_false_local_history_and_memory_cannot_supply_scientific_answer(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    agents = configure_worker(monkeypatch, job_sessions)
    with local_conversation(job_sessions, mode) as (cid, pid):
        with job_sessions() as session:
            session.add(
                Message(
                    conversation_id=cid,
                    role="user",
                    content="Dataset A has 500 participants.",
                    ordinal=0,
                )
            )
            session.add(
                Memory(conversation_id=cid, kind="term", content="Dataset A has 500 participants.")
            )
            session.commit()
        # Keep exercising false context at the contextualizer after the standalone gate.
        rid = queued_turn(job_sessions, cid, pid, "How many participants does it contain?")
        await worker.execute_async(rid)
        with job_sessions() as session:
            run = session.get(Run, rid)
            assert run is not None and run.status == "completed" and run.result
            answer = session.get(Message, run.request["assistant_message_id"])
            assert answer is not None and "120 participants" in answer.content
            assert "500" not in answer.content
        assert "500" in json.dumps([p for _, p in agents["retriever"].payloads])
        assert "500" not in json.dumps([p for _, p in agents["analyst"].payloads])
        assert "500" not in json.dumps([p for _, p in agents["reviewer"].payloads])


@pytest.mark.integration
async def test_context_checkpoint_retains_rewrite_charge_before_graph_dispatch(
    job_sessions: sessionmaker[Session],
) -> None:
    with local_conversation(job_sessions, "rag") as (cid, pid):
        with job_sessions() as session:
            session.add_all(
                [
                    Message(
                        conversation_id=cid,
                        role="user",
                        content="Which datasets were used?",
                        ordinal=0,
                    ),
                    Message(
                        conversation_id=cid,
                        role="assistant",
                        content="Dataset A and Dataset B.",
                        ordinal=1,
                    ),
                ]
            )
            session.commit()
        rid = queued_turn(job_sessions, cid, pid, "Which one has the largest sample size?")
        with job_sessions() as session:
            run = claim_run(session, rid)
            assert run is not None
            provider = ScientificScript()
            tracked: dict[str, Usage] = {}
            worker.track_usage(session, run, tracked, "retriever", provider)
            try:
                await prepare_context(session, run, provider, Settings(_env_file=None))
                # The process could die here, before any graph callback can repair accounting.
                with job_sessions() as reader:
                    saved = reader.get(Run, rid)
                    assert saved is not None and saved.result is not None
                    assert saved.result["usage"]["retriever"]["calls"] == 1
                    assert saved.result["usage"]["retriever"]["cost"] == pytest.approx(0.01)
                    assert saved.result["conversation_context"]["rewrite_status"] == "resolved"
            finally:
                provider.usage.on_update = None


@pytest.mark.integration
async def test_restart_recovers_old_entity_after_summary_has_lost_its_text(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ragagent.domain.conversation_context import ConversationState, ResolvedReferent

    agents = configure_worker(monkeypatch, job_sessions)
    with local_conversation(job_sessions, "rag") as (cid, pid):
        with job_sessions() as session:
            anchor = Message(
                conversation_id=cid, role="assistant", ordinal=0, content="Dataset A and Dataset B."
            )
            session.add(anchor)
            session.flush()
            session.add(
                Message(
                    conversation_id=cid, role="user", ordinal=100, content="Later unrelated notes."
                )
            )
            session.add(
                ConversationSummary(
                    conversation_id=cid,
                    through_ordinal=90,
                    version=1,
                    content="Unverified summary with old entity clipped.",
                    metadata_json={"source_message_ids": []},
                )
            )
            state = ConversationState(
                through_ordinal=1,
                resolved_entities=[
                    ResolvedReferent(
                        mention="it",
                        resolved_text="Dataset A",
                        source_kind="message",
                        source_id=anchor.id,
                    )
                ],
            )
            session.add(
                ConversationStateRecord(
                    conversation_id=cid, through_ordinal=1, version=1, content=state.model_dump()
                )
            )
            session.commit()
            anchor_id = anchor.id
        rid = queued_turn(job_sessions, cid, pid, "How many participants does it contain?")
        await worker.execute_async(rid)
        with job_sessions() as session:
            run = session.get(Run, rid)
            assert run is not None and run.status == "completed" and run.result
            assert run.result["conversation_context"]["used_message_ids"] == [anchor_id]
            assert "120 participants" in run.result["answer"]
            state_record = session.get(ConversationStateRecord, cid)
            assert state_record is not None and state_record.version == 2
            assert state_record.content["scientific_evidence"] is False
        assert "resolved_intent_entities_not_evidence" in agents["retriever"].payloads[0][1]
        assert "resolved_intent_entities_not_evidence" not in agents["analyst"].payloads[0][1]
