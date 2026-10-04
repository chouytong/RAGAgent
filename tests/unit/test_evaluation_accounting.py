import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from ragagent.domain.research import QueryPlan
from ragagent.evaluation import generation, retrieval
from ragagent.evaluation.artifacts import checkpoint_usage, usage_delta, usage_snapshot
from ragagent.evaluation.schema import EvaluationCase, EvaluationDataset
from ragagent.providers.chat import MockProvider, Usage, usage_record
from ragagent.settings import Settings
from tests.unit.test_generation_evaluation import refusal_agents, refusal_judgment
from tests.unit.test_graphs import Search

T = TypeVar("T", bound=BaseModel)


def refusal_dataset() -> EvaluationDataset:
    return EvaluationDataset(
        dataset_id="interrupted-accounting",
        label_source="synthetic",
        description="NOT A BENCHMARK",
        cases=[
            EvaluationCase(
                id="q",
                query="Unknown method?",
                question_type="fact",
                expected_answer="Insufficient evidence in the indexed literature.",
                expected_refusal=True,
            )
        ],
    )


def test_usage_observers_checkpoint_both_dispatch_and_completion_and_restore() -> None:
    persisted: list[dict[str, Any]] = []
    artifact: list[dict[str, Any]] = []
    provider = MockProvider([])
    provider.usage = Usage()

    def observer(usage: Usage) -> None:
        persisted.append(usage_record(usage))

    provider.usage.on_update = observer
    initial = usage_snapshot(provider)
    # Aliases must not recursively wrap the same Usage object twice.
    with checkpoint_usage(
        {"analyst": provider, "alias": provider},
        lambda: artifact.append(usage_delta(initial, usage_snapshot(provider))),
    ):
        provider.usage.begin_call()
        provider.usage.prompt_tokens = 10
        provider.usage.record_cost(0.75)
    assert provider.usage.on_update is observer
    assert len(persisted) == len(artifact) == 2
    assert artifact[0]["in_flight_calls"] == artifact[0]["unknown_cost_calls"] == 1
    assert artifact[0]["cost"] is None
    assert artifact[1]["in_flight_calls"] == artifact[1]["unknown_cost_calls"] == 0
    assert artifact[1]["cost"] == 0.75 and artifact[1]["prompt_tokens"] == 10


def test_checkpoint_and_restore_survive_existing_observer_failure() -> None:
    snapshots: list[dict[str, Any]] = []
    provider = MockProvider([])

    def observer(usage: Usage) -> None:
        raise RuntimeError("persist_failed")

    provider.usage.on_update = observer
    with pytest.raises(RuntimeError, match="persist_failed"):
        with checkpoint_usage(
            {"analyst": provider}, lambda: snapshots.append(usage_snapshot(provider))
        ):
            provider.usage.begin_call()
    assert provider.usage.on_update is observer
    assert snapshots[0]["cost"] is None and snapshots[0]["in_flight_calls"] == 1


async def test_generation_interruption_retains_pending_charge_and_original_observer(
    tmp_path: Path,
) -> None:
    class InterruptingProvider(MockProvider):
        async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
            self.usage.begin_call()
            raise asyncio.CancelledError()

    persisted: list[dict[str, Any]] = []
    agents = refusal_agents(1)
    interrupting = InterruptingProvider([])

    def observer(usage: Usage) -> None:
        persisted.append(usage_record(usage))

    interrupting.usage.on_update = observer
    with pytest.raises(asyncio.CancelledError):
        await generation.evaluate_generation(
            refusal_dataset(),
            Search(empty_rounds=99),
            agents,
            interrupting,
            Settings(max_retrieval_retries=0),
            tmp_path,
        )
    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["status"] == "running" and saved["pending_cases"] == 1
    assert saved["per_query"] == []
    assert saved["usage"]["judge"]["calls"] == 1
    assert saved["usage"]["judge"]["in_flight_calls"] == 1
    assert saved["usage"]["judge"]["unknown_cost_calls"] == 1
    assert saved["usage"]["judge"]["cost"] is None
    assert saved["unknown_workflow_cost_calls"] == 1
    assert saved["total_workflow_cost"] is None
    assert len(persisted) == 1 and persisted[0]["in_flight_calls"] == 1
    assert interrupting.usage.on_update is observer


async def test_retrieval_interruption_retains_pending_embedding_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = SimpleNamespace(usage=Usage())
    persisted: list[dict[str, Any]] = []

    def observer(usage: Usage) -> None:
        persisted.append(usage_record(usage))

    embedder.usage.on_update = observer

    class InterruptingSearch:
        def __init__(self, *args: Any) -> None:
            self.dense = SimpleNamespace(embedder=embedder, search=self.search)
            self.reranker = None
            self.top_n, self.top_k = 30, 10
            self.fusion = SimpleNamespace(k=60)

        async def search(self, *args: Any, **kwargs: Any) -> None:
            embedder.usage.begin_call()
            raise asyncio.CancelledError()

    monkeypatch.setattr(retrieval, "validate_references", lambda *args: None)
    monkeypatch.setattr(
        retrieval, "corpus_snapshot", lambda session: {"availability": "available", "hash": "test"}
    )
    monkeypatch.setattr(retrieval, "HybridRetriever", InterruptingSearch)
    dataset = refusal_dataset()
    dataset.cases[0].relevant_chunk_ids = ["c1"]
    with pytest.raises(asyncio.CancelledError):
        await retrieval.evaluate_retrieval(
            dataset, InterruptingSearch(), SimpleNamespace(), Settings(), tmp_path
        )
    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["status"] == "running" and saved["pending_cases"] == 1
    assert saved["usage"]["embedding"]["in_flight_calls"] == 1
    assert saved["unknown_workflow_cost_calls"] == 1
    assert saved["total_workflow_cost"] is None
    assert len(persisted) == 1 and embedder.usage.on_update is observer


async def test_resume_retains_more_complete_persisted_usage_and_attempt_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        generation,
        "corpus_snapshot",
        lambda session: {"availability": "available", "hash": "fixed-test-corpus"},
    )
    monkeypatch.setattr(generation, "verify_corpus_snapshot", lambda session, initial: None)
    source = tmp_path / "previous"
    first = await generation.evaluate_generation(
        refusal_dataset(),
        Search(empty_rounds=99),
        refusal_agents(1),
        MockProvider([{"invalid": "scripted judge failure"}]),
        Settings(max_retrieval_retries=0),
        source,
    )
    first["previous_attempt_usage"] = [{"timestamp": "older", "usage": {"analyst": {"cost": 2}}}]
    (source / "results.json").write_text(json.dumps(first))
    # Simulate a kill between the worker's database checkpoint and the artifact checkpoint.
    authoritative = {
        "supervisor": {
            "prompt_tokens": 99,
            "completion_tokens": 25,
            "known_cost": 7.5,
            "unknown_cost_calls": 1,
            "calls": 3,
            "in_flight_calls": 1,
            "cost": None,
        }
    }
    agents = refusal_agents(1)
    second = await generation.evaluate_generation(
        refusal_dataset(),
        Search(empty_rounds=99),
        agents,
        MockProvider([refusal_judgment()]),
        Settings(max_retrieval_retries=0),
        tmp_path / "resumed",
        resume_directory=source,
        resume_usage=authoritative,
    )
    assert second["status"] == "completed"
    assert second["previous_attempt_usage"][0] == first["previous_attempt_usage"][0]
    prior = second["previous_attempt_usage"][1]
    assert prior["usage_source"] == "persisted_run"
    assert prior["usage"]["supervisor"]["known_cost"] == 7.5
    assert prior["usage"]["supervisor"]["cost"] is None
    assert prior["usage"]["supervisor"]["in_flight_calls"] == 1
    assert prior["usage"]["supervisor"]["prompt_tokens"] == 99
    assert prior["usage"]["judge"] == first["usage"]["judge"]
    assert second["usage_scope"] == "current_attempt"
    assert second["usage"]["supervisor"]["calls"] == 1
    assert second["known_workflow_cost"] == 0
    assert agents["supervisor"].usage.on_update is None
    assert agents["supervisor"].calls == [QueryPlan]
