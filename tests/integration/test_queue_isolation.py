"""Real RQ/PG occupancy probe; scripted scientific providers, no quality claim."""

import multiprocessing
import os
import time
from uuid import uuid4

import pytest
from redis import Redis
from rq import Queue, Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from ragagent import worker
from ragagent.api.papers import enqueue
from ragagent.api.queue import RQQueue
from ragagent.db.models import Run
from ragagent.domain.research import (
    AnswerDraft,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    QueryPlan,
    VerificationResponse,
)
from ragagent.jobs import claim_run, finish_run
from ragagent.providers.chat import MockProvider
from tests.unit.helpers import evidence
from tests.unit.test_graphs import RoundSearch


@pytest.mark.integration
def test_running_evaluation_cannot_occupy_the_interactive_worker(
    job_sessions: sessionmaker[Session],
    redis_connection: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_key, release_key = f"queue-probe:{uuid4()}:started", f"queue-probe:{uuid4()}:release"
    original_execute = worker.execute_async
    monkeypatch.setattr(worker, "session_factory", lambda: job_sessions)

    async def occupancy_probe(run_id: str) -> None:
        with job_sessions() as session:
            current = session.get(Run, run_id)
            if current is not None and current.kind == "eval_rag":
                current = claim_run(session, run_id)
                assert current is not None
                connection = Redis.from_url(os.environ["TEST_REDIS_URL"])
                connection.set(start_key, "started", ex=30)
                assert connection.blpop(release_key, timeout=15) is not None
                current.status, current.result = (
                    "completed",
                    {"warning": "SYNTHETIC QUEUE OCCUPANCY PROBE / NOT EVALUATION QUALITY"},
                )
                finish_run(session, current)
                connection.close()
                return
        await original_execute(run_id)

    source = evidence()
    monkeypatch.setattr(worker, "execute_async", occupancy_probe)
    monkeypatch.setattr(worker, "make_embedder", lambda _: object())
    monkeypatch.setattr(worker, "make_reranker", lambda _: object())
    monkeypatch.setattr(worker, "HybridRetriever", lambda *args: RoundSearch([[source]]))
    monkeypatch.setattr(
        worker,
        "make_agents",
        lambda _: {
            "supervisor": MockProvider(
                [QueryPlan(queries=["contrastive training"], required_aspects=["method"])]
            ),
            "retriever": MockProvider([]),
            "analyst": MockProvider(
                [
                    AnswerDraft(
                        claims=[
                            Claim(
                                claim_id="c",
                                text="Contrastive training improves retrieval.",
                                evidence_ids=[source.evidence_id],
                                aspect="method",
                            )
                        ]
                    )
                ]
            ),
            "reviewer": MockProvider(
                [
                    VerificationResponse(
                        question_answered=True,
                        verdicts=[
                            ClaimVerdict(claim_id="c", supported=True, reason="SCRIPTED PROBE")
                        ],
                        supported_pairs=[
                            ClaimEvidencePair(claim_id="c", evidence_id=source.evidence_id)
                        ],
                    )
                ]
            ),
        },
    )
    transport = RQQueue()
    monkeypatch.setattr(transport, "connection", lambda: redis_connection)
    ids: list[str] = []
    processes = []

    def run_worker(name: str) -> None:
        job_sessions.kw["bind"].dispose(close=False)
        connection = Redis.from_url(os.environ["TEST_REDIS_URL"])
        Worker([Queue(name, connection=connection)], connection=connection).work(
            burst=True, max_jobs=1, logging_level="WARNING"
        )

    context = multiprocessing.get_context("fork")
    try:
        with job_sessions() as session:
            evaluation = enqueue(session, transport, "eval_rag", {"probe": "SYNTHETIC OCCUPANCY"})
            ids.append(evaluation.id)
        evaluation_process = context.Process(target=run_worker, args=("evaluation",))
        processes.append(evaluation_process)
        evaluation_process.start()
        deadline = time.monotonic() + 10
        while not redis_connection.exists(start_key) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert redis_connection.exists(start_key)
        begin = time.perf_counter()
        with job_sessions() as session:
            interactive = enqueue(
                session, transport, "rag", {"query": "What method improves retrieval?"}
            )
            ids.append(interactive.id)
        interactive_process = context.Process(target=run_worker, args=("interactive",))
        processes.append(interactive_process)
        interactive_process.start()
        interactive_process.join(timeout=10)
        assert not interactive_process.is_alive() and interactive_process.exitcode == 0
        elapsed = (time.perf_counter() - begin) * 1000
        with job_sessions() as session:
            assert session.get(Run, evaluation.id).status == "running"
            result = session.get(Run, interactive.id)
            assert result.status == "completed" and source.evidence_id in result.result["answer"]
            assert result.request["_queue_name"] == "interactive"
        redis_connection.rpush(release_key, "release")
        evaluation_process.join(timeout=10)
        assert not evaluation_process.is_alive() and evaluation_process.exitcode == 0
        with job_sessions() as session:
            assert session.get(Run, evaluation.id).status == "completed"
        print(
            f"SYNTHETIC QUEUE OCCUPANCY PROBE interactive_completion_ms={elapsed:.3f}; "
            "evaluation_still_running_at_interactive_completion=true"
        )
    finally:
        redis_connection.rpush(release_key, "release")
        for process in processes:
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        for identifier in ids:
            try:
                Job.fetch(identifier, connection=redis_connection).delete(remove_from_queue=True)
            except NoSuchJobError:
                pass  # Production result_ttl=0 already removes successful RQ jobs.
        redis_connection.delete(start_key, release_key)
        with job_sessions() as session:
            session.execute(delete(Run).where(Run.id.in_(ids)))
            session.commit()
