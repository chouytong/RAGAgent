from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ragagent import worker
from ragagent.db.models import Chunk, Paper, Run, Section
from ragagent.domain.research import AnswerDraft, Candidate, Claim, QueryPlan, SearchResult
from ragagent.errors import ApplicationError, ProviderError
from ragagent.providers.chat import Usage
from ragagent.settings import Settings
from tests.unit.helpers import evidence

T = TypeVar("T", bound=BaseModel)


@contextmanager
def saved_run(
    sessions: sessionmaker[Session], *, kind: str = "rag", status: str = "queued"
) -> Iterator[str]:
    with sessions() as session:
        run = Run(kind=kind, request={"query": "What method?"}, status=status)
        session.add(run)
        session.commit()
        run_id = run.id
    try:
        yield run_id
    finally:
        with sessions() as session:
            session.execute(delete(Run).where(Run.id == run_id))
            session.commit()


class ChargedScriptProvider:
    """Simulated provider charges; never reaches a network or a real paid API."""

    def __init__(self, response: BaseModel | dict[str, Any] | Exception, cost: float) -> None:
        self.response, self.cost = response, cost
        self.usage = Usage()

    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.usage.begin_call()
        if isinstance(self.response, Exception):
            self.usage.record_cost(None)
            raise self.response
        self.usage.prompt_tokens += 11
        self.usage.completion_tokens += 5
        self.usage.record_identity("fixture-pinned-model", "fixture-fingerprint")
        self.usage.record_cost(self.cost)
        # Malformed billed responses can fail schema validation after accounting.
        data = self.response.model_dump() if isinstance(self.response, BaseModel) else self.response
        return schema.model_validate(data)


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["schema", "provider"])
async def test_reused_providers_keep_failed_run_usage_separate_and_release_observers(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    item = evidence()
    candidate = Candidate(evidence=item, score=1.0)

    class Search:
        async def search(self, plan: QueryPlan, rerank: bool = True) -> SearchResult:
            return SearchResult(dense=[], lexical=[], fused=[candidate], evidence=[item])

    reviewer_response: dict[str, Any] | Exception = (
        {"not_a_verification_response": True}
        if failure == "schema"
        else ProviderError("fixture_reviewer_failed")
    )
    agents = {
        "supervisor": ChargedScriptProvider(
            QueryPlan(queries=["contrastive training"], required_aspects=["method"]), 0.03
        ),
        "retriever": ChargedScriptProvider({}, 0.0),
        "analyst": ChargedScriptProvider(
            AnswerDraft(
                claims=[
                    Claim(
                        claim_id="c1",
                        text="The method uses contrastive training.",
                        evidence_ids=[item.evidence_id],
                        aspect="method",
                    )
                ]
            ),
            0.05,
        ),
        "reviewer": ChargedScriptProvider(reviewer_response, 0.07),
    }
    monkeypatch.setattr(worker, "session_factory", lambda: job_sessions)
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(worker, "make_agents", lambda settings: agents)
    monkeypatch.setattr(worker, "make_embedder", lambda settings: SimpleNamespace())
    monkeypatch.setattr(worker, "make_reranker", lambda settings: None)
    monkeypatch.setattr(worker, "HybridRetriever", lambda *args: Search())
    with saved_run(job_sessions) as first_id, saved_run(job_sessions) as second_id:
        expected_code = "job_failed" if failure == "schema" else "fixture_reviewer_failed"
        ledgers: dict[str, dict[str, Any]] = {}
        for run_id in (first_id, second_id):
            with pytest.raises(ApplicationError, match=expected_code):
                await worker.execute_async(run_id)
            # Cached adapters must not retain callbacks to a closed session/old Run.
            assert all(adapter.usage.on_update is None for adapter in agents.values())
            with job_sessions() as session:
                failed = session.get(Run, run_id)
                assert failed is not None and failed.status == "failed"
                assert failed.error_code == expected_code
                assert failed.result is not None
                assert failed.result["usage_scope"] == "current_attempt"
                usage = failed.result["usage"]
                assert usage["supervisor"]["calls"] == 1
                assert usage["supervisor"]["cost"] == pytest.approx(0.03)
                assert usage["supervisor"]["prompt_tokens"] == 11
                assert usage["supervisor"]["provider_models"] == ["fixture-pinned-model"]
                assert usage["analyst"]["cost"] == pytest.approx(0.05)
                assert usage["reviewer"]["calls"] == 1
                assert usage["reviewer"]["in_flight_calls"] == 0
                if failure == "schema":
                    assert usage["reviewer"]["cost"] == pytest.approx(0.07)
                else:
                    assert usage["reviewer"]["cost"] is None
                    assert usage["reviewer"]["unknown_cost_calls"] == 1
                for previous_id, ledger in ledgers.items():
                    previous = session.get(Run, previous_id)
                    assert previous is not None and previous.result == ledger
                ledgers[run_id] = failed.result


@pytest.mark.integration
def test_inflight_checkpoint_survives_worker_interruption(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "session_factory", lambda: job_sessions)
    adapter = SimpleNamespace(usage=Usage())
    with saved_run(job_sessions, status="running") as run_id:
        with job_sessions() as session:
            run = session.get(Run, run_id)
            assert run is not None
            tracked: dict[str, Usage] = {}
            worker.track_usage(session, run, tracked, "supervisor", adapter)
            adapter.usage.begin_call()
        # No completion/exception checkpoint ran; emulate the surviving RQ parent.
        worker.on_failure(SimpleNamespace(id=run_id), None, None, None, None)
        with job_sessions() as session:
            failed = session.get(Run, run_id)
            assert failed is not None and failed.status == "failed"
            assert failed.error_code == "worker_interrupted"
            assert failed.result is not None
            usage = failed.result["usage"]["supervisor"]
            assert usage["calls"] == 1
            assert usage["in_flight_calls"] == 1
            assert usage["unknown_cost_calls"] == 1
            assert usage["cost"] is None and usage["known_cost"] == 0.0


@pytest.mark.integration
def test_usage_observer_does_not_commit_partial_ingestion_changes(
    job_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "session_factory", lambda: job_sessions)
    with job_sessions() as session:
        paper = Paper(
            title="Uncommitted ingestion",
            sha256=uuid4().hex * 2,
            original_path="fixture.pdf",
            status="indexing",
        )
        session.add(paper)
        session.flush()
        section = Section(paper_id=paper.id, title="Methods", path="Methods", ordinal=0)
        session.add(section)
        session.commit()
        paper_id, section_id = paper.id, section.id
    try:
        with saved_run(job_sessions, kind="ingestion", status="running") as run_id:
            with job_sessions() as session:
                run = session.get(Run, run_id)
                assert run is not None
                chunk = Chunk(
                    paper_id=paper_id,
                    section_id=section_id,
                    section_path="Methods",
                    page_start=1,
                    page_end=1,
                    element_type="text",
                    content="Partial chunk must remain uncommitted.",
                    token_count=6,
                    ordinal=0,
                    embedding=[1.0] + [0.0] * 383,
                )
                session.add(chunk)
                session.flush()
                tracked: dict[str, Usage] = {}
                adapter = SimpleNamespace(usage=Usage())
                worker.track_usage(session, run, tracked, "embedding", adapter)
                adapter.usage.begin_call()
                adapter.usage.record_cost(0.02)
                with job_sessions() as reader:
                    assert reader.scalar(select(Chunk.id).where(Chunk.paper_id == paper_id)) is None
                    checkpoint = reader.get(Run, run_id)
                    assert checkpoint is not None and checkpoint.result is not None
                    assert checkpoint.result["usage"]["embedding"]["cost"] == pytest.approx(0.02)
                session.rollback()
            with job_sessions() as reader:
                assert reader.scalar(select(Chunk.id).where(Chunk.paper_id == paper_id)) is None
    finally:
        with job_sessions() as session:
            session.execute(delete(Paper).where(Paper.id == paper_id))
            session.commit()
