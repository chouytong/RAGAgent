"""Synthetic labels, real PostgreSQL/vector/FTS retrieval, real context and graphs."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent import worker
from ragagent.api.app import app
from ragagent.api.dependencies import get_db
from ragagent.api.queue import get_queue
from ragagent.db.dispatch import JobDispatch
from ragagent.db.models import Chunk, ExecutionEvent, Paper, Run, Section
from ragagent.domain.conversation_context import ContextConfig, ContextMessage
from ragagent.domain.research import (
    AnswerDraft,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    QueryPlan,
    VerificationResponse,
)
from ragagent.evaluation.conversation import evaluate_conversation
from ragagent.evaluation.conversation_schema import (
    ConversationEvaluationCase,
    validate_conversation_references,
)
from ragagent.evaluation.schema import CitationPair, RAGJudgment
from ragagent.graphs.state import AnalysisResult, ResearchPlan, SubTask
from ragagent.providers import chat
from ragagent.providers.chat import MockProvider
from ragagent.retrieval.service import HybridRetriever
from ragagent.settings import Settings
from tests.integration.test_retrieval import Embedder, FixtureReranker, populate
from tests.unit.test_conversation_evaluation import (
    comparison_case,
    comparison_rewrite,
    credential_payload,
    dataset,
    rewrite,
    turn,
)

T = TypeVar("T", bound=BaseModel)


class QuotedFacts(MockProvider):
    """Tests only: derive scripted claims from the real retrieved quote/ID."""

    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.calls.append(schema)
        self.usage.record_cost(None)
        supplied = payload["evidence"]
        assert supplied and "content" not in supplied[0]
        assert all("500" not in item["quote"] for item in supplied)
        fact = Claim(
            claim_id="retrieved-fact",
            text=supplied[0]["quote"],
            evidence_ids=[supplied[0]["evidence_id"]],
            aspect="method",
        )
        value = (
            AnalysisResult(claims=[fact])
            if schema is AnalysisResult
            else AnswerDraft(claims=[fact])
        )
        return schema.model_validate(value.model_dump())


class QuoteVerifier(MockProvider):
    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.calls.append(schema)
        self.usage.record_cost(None)
        claims = [Claim.model_validate(value) for value in payload["claims"]]
        return schema.model_validate(
            VerificationResponse(
                supported_pairs=[
                    ClaimEvidencePair(claim_id=c.claim_id, evidence_id=eid)
                    for c in claims
                    for eid in c.evidence_ids
                ],
                question_answered=True,
                verdicts=[
                    ClaimVerdict(
                        claim_id=c.claim_id, supported=True, reason="synthetic exact quote fixture"
                    )
                    for c in claims
                ],
            ).model_dump()
        )


class QuoteJudge(MockProvider):
    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.calls.append(schema)
        self.usage.record_cost(None)
        claims = [Claim.model_validate(value) for value in payload["claims"]]
        return schema.model_validate(
            RAGJudgment(
                supported_pairs=[
                    CitationPair(claim_id=c.claim_id, evidence_id=eid)
                    for c in claims
                    for eid in c.evidence_ids
                ],
                supported_claim_ids=[c.claim_id for c in claims],
                answered_aspects=payload["required_aspects"],
            ).model_dump()
        )


class LargestSampleFacts(MockProvider):
    """Test-only comparison script operating on two freshly retrieved exact quotes."""

    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.calls.append(schema)
        self.usage.record_cost(None)
        assert "recent_messages" not in payload and "conversation_summary" not in payload
        supplied = payload["evidence"]
        assert len(supplied) == 2 and "500" not in json.dumps(supplied)
        samples: dict[str, tuple[int, str]] = {}
        for source in supplied:
            match = re.fullmatch(r"(Paper \w+) enrolled (\d+) participants\.", source["quote"])
            assert match is not None
            samples[match[1]] = (int(match[2]), source["evidence_id"])
        assert {name: value[0] for name, value in samples.items()} == {
            "Paper Alpha": 20,
            "Paper Beta": 80,
        }
        smaller, largest = sorted(samples, key=lambda name: samples[name][0])
        fact = Claim(
            claim_id="retrieved-comparison",
            text=(
                f"{largest} has the largest sample: {samples[largest][0]} participants "
                f"compared with {samples[smaller][0]} participants in {smaller}."
            ),
            evidence_ids=[source["evidence_id"] for source in supplied],
            aspect="sample_size",
        )
        value = (
            AnalysisResult(claims=[fact])
            if schema is AnalysisResult
            else AnswerDraft(claims=[fact])
        )
        return schema.model_validate(value.model_dump())


@pytest.mark.integration
@pytest.mark.parametrize(
    "path",
    [
        ("description",),
        ("cases", 0, "notes"),
        ("cases", 0, "seed_messages", 0, "content"),
        ("cases", 0, "memories", 0, "content"),
        ("cases", 0, "memories", 0, "filters", "authors", 0),
        ("cases", 0, "turns", 0, "query"),
        ("cases", 0, "turns", 0, "expected_answer"),
        ("cases", 0, "turns", 0, "expected_context_terms", 0),
        ("cases", 0, "turns", 0, "forbidden_answer_fragments", 0),
        ("cases", 0, "turns", 0, "filters", "datasets", 0),
    ],
    ids=lambda path: ".".join(map(str, path)),
)
async def test_credentials_are_rejected_before_evaluation_run_or_artifact_creation(
    empty_db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str | int, ...],
) -> None:
    credential = "sk-" + "syntheticfixture" * 2
    submitted: list[str] = []

    class RecordingQueue:
        def submit(self, run_id: str) -> None:
            submitted.append(run_id)

    def db_override() -> Iterator[Session]:
        yield empty_db

    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr("ragagent.api.evaluations.get_settings", lambda: settings)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_queue] = RecordingQueue
    before = [
        empty_db.scalar(select(func.count()).select_from(model))
        for model in (Run, JobDispatch, ExecutionEvent)
    ]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/evaluations/conversation",
                json={"dataset": credential_payload(path, credential)},
            )
        assert response.status_code == 422
        assert response.json()["error_code"] == "invalid_request"
        assert credential not in response.text and "syntheticfixture" not in response.text
        assert not submitted
        assert before == [
            empty_db.scalar(select(func.count()).select_from(model))
            for model in (Run, JobDispatch, ExecutionEvent)
        ]
        assert not (tmp_path / "evaluations").exists()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_comparison_followup_uses_two_real_papers_instead_of_historical_winner(
    empty_db: Session, tmp_path: Path, mode: str
) -> None:
    paper_ids, chunk_ids = [], []
    for name, size in (("Alpha", 20), ("Beta", 80)):
        paper = Paper(
            title=f"Paper {name}",
            sha256=uuid4().hex * 2,
            original_path="synthetic-comparison-fixture",
            status="indexed",
            embedding_model="test:384",
        )
        empty_db.add(paper)
        empty_db.flush()
        section = Section(paper_id=paper.id, title="Methods", path="Methods", ordinal=0)
        empty_db.add(section)
        empty_db.flush()
        chunk = Chunk(
            paper_id=paper.id,
            section_id=section.id,
            section_path="Methods",
            page_start=1,
            page_end=1,
            element_type="text",
            content=f"Paper {name} enrolled {size} participants.",
            token_count=7,
            ordinal=0,
            embedding=[1.0] + [0.0] * 383,
        )
        empty_db.add(chunk)
        empty_db.flush()
        paper_ids.append(paper.id)
        chunk_ids.append(chunk.id)
    case = comparison_case(mode)
    labeled_turn = case.turns[0]
    labeled_turn.relevant_chunk_ids, labeled_turn.relevant_paper_ids = chunk_ids, paper_ids
    labeled_turn.filters.paper_ids = paper_ids
    comparison_plan = (
        ResearchPlan(
            objective="Compare actual sample sizes",
            required_aspects=["sample_size"],
            subtasks=[
                SubTask(
                    task_id="samples",
                    aspect="sample_size",
                    question="Compare Paper Alpha and Paper Beta sample sizes.",
                    queries=["Paper Alpha Paper Beta participants"],
                )
            ],
        )
        if mode == "research"
        else QueryPlan(
            queries=["Paper Alpha Paper Beta participants"], required_aspects=["sample_size"]
        )
    )
    rewriter = MockProvider([comparison_rewrite()])
    report = await evaluate_conversation(
        dataset(case),
        HybridRetriever(empty_db, Embedder(), FixtureReranker()),
        {
            "supervisor": MockProvider([comparison_plan]),
            "retriever": rewriter,
            "analyst": LargestSampleFacts([]),
            "reviewer": QuoteVerifier([]),
        },
        QuoteJudge([]),
        Settings(),
        tmp_path,
    )
    assert report["status"] == "completed" and report["corpus_verification"] == "verified"
    row = report["per_conversation"][0]["turns"][0]
    assert row["contextualized_query"] == comparison_rewrite().contextualized_query
    assert row["dimension_metrics"]["context_resolution"]["resolution_accuracy"] == 1.0
    assert row["dimension_metrics"]["memory_isolation"]["memory_isolation_accuracy"] == 1.0
    assert row["metrics"]["citation_precision"] == row["metrics"]["citation_recall"] == 1.0
    assert row["metrics"]["answer_completeness"] == 1.0
    assert {source["paper"]["paper_id"] for source in row["evidence"]} == set(paper_ids)
    assert {source["chunk_id"] for source in row["evidence"]} == set(chunk_ids)
    assert all(
        set(plan["filters"]["paper_ids"]) == set(paper_ids) for plan in row["retrieval_plans"]
    )
    assert labeled_turn.expected_answer in row["actual_output"]
    assert "500" not in row["actual_output"]
    assert row["context"]["used_message_ids"] == ["candidates"]
    assert row["structural_checks"]["evidence_from_current_retrieval"] is True
    assert rewriter.calls == [type(comparison_rewrite())]


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["rag", "research"])
async def test_conversation_evaluation_real_retrieval_and_local_context_isolation(
    empty_db: Session, tmp_path: Path, mode: str
) -> None:
    pid, cid = populate(empty_db)
    before_runs = empty_db.scalar(select(func.count()).select_from(Run))
    query = "What training method does it use?"
    labeled_turn = turn(
        query=query,
        relevant_chunk_ids=[cid],
        relevant_paper_ids=[pid],
        expected_answer="Contrastive training improves retrieval.",
        expected_context_terms=["Contrastive"],
        forbidden_answer_fragments=["500"],
        expects_summary=True,
        filters={"paper_ids": [pid]},
    )
    case = ConversationEvaluationCase(
        id="real-corpus",
        mode=mode,
        dimensions=["context_resolution", "evidence_grounding", "memory_isolation", "long_summary"],
        seed_messages=[
            ContextMessage(
                id="m1",
                ordinal=0,
                role="user",
                content=(
                    "Contrastive supposedly uses 500 participants. "
                    "This is an intentionally incorrect conversation statement."
                ),
            ),
            *[
                ContextMessage(
                    id=f"m{i}", ordinal=i - 1, role="user", content="Continue reviewing."
                )
                for i in range(2, 16)
            ],
        ],
        turns=[labeled_turn],
    )
    value = dataset(case)
    validate_conversation_references(value, empty_db)
    plan = (
        ResearchPlan(
            objective="Inspect the method",
            required_aspects=["method"],
            subtasks=[
                SubTask(
                    task_id="method",
                    aspect="method",
                    question="Contrastive training method?",
                    queries=["contrastive training"],
                )
            ],
        )
        if mode == "research"
        else QueryPlan(queries=["contrastive training"], required_aspects=["method"])
    )
    report = await evaluate_conversation(
        value,
        HybridRetriever(empty_db, Embedder(), FixtureReranker()),
        {
            "supervisor": MockProvider([plan]),
            "retriever": MockProvider([rewrite("Contrastive", "m1")]),
            "analyst": QuotedFacts([]),
            "reviewer": QuoteVerifier([]),
        },
        QuoteJudge([]),
        Settings(),
        tmp_path,
        context_config=ContextConfig(recent_message_limit=2),
    )
    assert report["status"] == "completed"
    assert report["missing_dimensions"] == []
    assert report["corpus_verification"] == "verified"
    assert report["manifest"]["corpus_snapshot"]["availability"] == "available"
    assert report["summary"]["evidence_grounding"]["citation_precision"] == 1.0
    assert report["summary"]["memory_isolation"]["memory_isolation_accuracy"] == 1.0
    assert report["summary"]["long_summary"]["summary_context_accuracy"] == 1.0
    row = report["per_conversation"][0]["turns"][0]
    assert [item["chunk_id"] for item in row["evidence"]] == [cid]
    assert row["evidence"][0]["paper"]["paper_id"] == pid
    assert row["context"]["used_message_ids"] == ["m1"]
    assert row["summary"]["through_ordinal"] >= 0
    assert "500" not in row["actual_output"]
    assert row["structural_checks"]["evidence_from_current_retrieval"] is True
    assert row["usage"]["retriever"]["calls"] == 1
    assert before_runs == empty_db.scalar(select(func.count()).select_from(Run))
    assert (
        json.loads((tmp_path / "results.json").read_text())["manifest"]["label_source"]
        == "synthetic"
    )
    value.cases[0].turns[0].relevant_chunk_ids = ["unknown"]
    with pytest.raises(ValueError, match="unknown_chunks_or_papers"):
        validate_conversation_references(value, empty_db)


@pytest.mark.integration
async def test_conversation_evaluation_api_worker_and_artifact_use_existing_run_lifecycle(
    job_sessions: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with job_sessions() as db:
        paper = Paper(
            title="Evaluation pipeline fixture",
            sha256=uuid4().hex * 2,
            original_path="fixture",
            status="indexed",
            embedding_model="test:384",
        )
        db.add(paper)
        db.flush()
        section = Section(paper_id=paper.id, title="Methods", path="Methods", ordinal=0)
        db.add(section)
        db.flush()
        chunk = Chunk(
            paper_id=paper.id,
            section_id=section.id,
            section_path="Methods",
            page_start=1,
            page_end=1,
            element_type="text",
            content="Contrastive training improves retrieval.",
            token_count=5,
            ordinal=0,
            embedding=[1.0] + [0.0] * 383,
        )
        db.add(chunk)
        db.commit()
        pid, cid = paper.id, chunk.id
    submitted: list[str] = []

    class RecordingQueue:
        def submit(self, run_id: str) -> None:
            submitted.append(run_id)

    def db_override() -> Iterator[Session]:
        with job_sessions() as db:
            yield db

    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr("ragagent.api.evaluations.get_settings", lambda: settings)
    monkeypatch.setattr(worker, "session_factory", lambda: job_sessions)
    monkeypatch.setattr(worker, "make_embedder", lambda settings: Embedder())
    monkeypatch.setattr(worker, "make_reranker", lambda settings: FixtureReranker())
    monkeypatch.setattr(
        worker,
        "make_agents",
        lambda settings: {
            "supervisor": MockProvider(
                [QueryPlan(queries=["contrastive"], required_aspects=["method"])]
            ),
            "retriever": MockProvider([]),
            "analyst": QuotedFacts([]),
            "reviewer": QuoteVerifier([]),
        },
    )
    monkeypatch.setattr(chat, "LiteLLMProvider", lambda *args, **kwargs: QuoteJudge([]))
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_queue] = RecordingQueue
    run_id: str | None = None
    try:
        value = dataset(
            ConversationEvaluationCase(
                id="queued",
                dimensions=["evidence_grounding"],
                turns=[
                    turn(
                        relevant_chunk_ids=[cid],
                        relevant_paper_ids=[pid],
                        filters={"paper_ids": [pid]},
                    )
                ],
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/evaluations/conversation", json={"dataset": value.model_dump(mode="json")}
            )
            assert response.status_code == 202
            run_id = response.json()["id"]
            assert submitted == [run_id]
            assert response.json()["kind"] == "eval_conversation"
            await worker.execute_async(run_id)
            result = await client.get(f"/api/runs/{run_id}")
            assert result.status_code == 200 and result.json()["status"] == "completed"
            assert result.json()["result"]["kind"] == "conversation"
            artifact = await client.get(f"/api/evaluations/{run_id}/results.json")
            assert artifact.status_code == 200
            assert artifact.json()["corpus_verification"] == "verified"
            assert artifact.json()["summary"]["evidence_grounding"]["citation_precision"] == 1.0
            value.cases[0].turns[0].relevant_chunk_ids = []
            assert (
                await client.post(
                    "/api/evaluations/conversation", json={"dataset": value.model_dump(mode="json")}
                )
            ).status_code == 422
    finally:
        app.dependency_overrides.clear()
        with job_sessions() as db:
            if run_id is not None:
                db.execute(delete(Run).where(Run.id == run_id))
            db.execute(delete(Paper).where(Paper.id == pid))
            db.commit()
