import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import ragagent.evaluation.artifacts as artifacts
from ragagent.evaluation.generation import usage_delta, usage_snapshot
from ragagent.evaluation.metrics import retrieval_metrics
from ragagent.evaluation.schema import EvaluationCase, EvaluationDataset
from ragagent.providers.chat import MockProvider, Usage
from ragagent.settings import Settings


def test_metrics_against_hand_calculated_ranking() -> None:
    result = retrieval_metrics(["noise", "a", "noise", "b"], {"a", "b", "missed"})
    assert result["Recall@1"] == 0
    assert result["Recall@5"] == pytest.approx(2 / 3)
    assert result["Precision@5"] == pytest.approx(2 / 5)
    assert result["MRR@10"] == pytest.approx(1 / 2)
    ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert result["nDCG@10"] == pytest.approx((1 / math.log2(3) + 1 / math.log2(4)) / ideal)


def test_empty_labels_and_false_human_annotation_rejected() -> None:
    with pytest.raises(ValueError):
        retrieval_metrics([], set())
    case = EvaluationCase(id="q", query="q", question_type="fact", expected_answer="")
    with pytest.raises(ValidationError):
        EvaluationDataset(dataset_id="x", label_source="human", description="", cases=[case])
    unannotated = EvaluationDataset(
        dataset_id="x", label_source="unannotated", description="", cases=[case]
    )
    with pytest.raises(ValueError):
        unannotated.runnable()


def test_expected_refusal_is_explicit_and_only_relaxes_generation_labels() -> None:
    case = EvaluationCase(
        id="q",
        query="Unknown method?",
        question_type="fact",
        expected_answer="Insufficient evidence.",
        expected_refusal=True,
    )
    dataset = EvaluationDataset(
        dataset_id="x", label_source="synthetic", description="", cases=[case]
    )
    dataset.generation_runnable()
    with pytest.raises(ValueError, match="relevance_labels"):
        dataset.runnable()
    case.expected_refusal = False
    with pytest.raises(ValueError, match="relevance_labels"):
        dataset.generation_runnable()
    case.relevant_chunk_ids = ["c"]
    with pytest.raises(ValueError, match="answer_and_aspect_labels"):
        dataset.generation_runnable()


def test_source_hash_tracks_source_edits_and_excludes_runtime_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = tmp_path / "src/ragagent/evaluation/artifacts.py"
    module.parent.mkdir(parents=True)
    module.write_text("source original")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations/0001.py").write_text("migration")
    (tmp_path / "pyproject.toml").write_text("manifest")
    monkeypatch.setattr(artifacts, "__file__", str(module))
    initial = artifacts.source_snapshot()
    (tmp_path / ".env").write_text("SECRET=review-fixture-one")
    assert artifacts.source_snapshot()["source_hash"] == initial["source_hash"]
    (tmp_path / ".env").write_text("SECRET=review-fixture-two")
    assert artifacts.source_snapshot()["source_hash"] == initial["source_hash"]
    module.write_text("source changed")
    assert artifacts.source_snapshot()["source_hash"] != initial["source_hash"]
    assert initial["source_file_count"] == 3
    assert initial["source_dirty"] is None


def test_evaluation_usage_delta_keeps_unknown_calls_and_uncalled_provider_cost() -> None:
    provider = MockProvider([])
    provider.usage = Usage()
    before = usage_snapshot(provider)
    assert usage_delta(before, before)["cost"] == 0.0
    provider.usage.record_cost(1.5)
    provider.usage.record_cost(None)
    provider.usage.record_cost(2.5)
    result = usage_delta(before, usage_snapshot(provider))
    assert result["cost"] is None
    assert result["known_cost"] == 4.0
    assert result["unknown_cost_calls"] == 1
    assert result["calls"] == 3
    after = usage_snapshot(provider)
    provider.usage.record_cost(1.0)
    assert usage_delta(after, usage_snapshot(provider))["cost"] == 1.0


def test_retrieval_manifest_freezes_actual_model_revisions_and_endpoint() -> None:
    embedder = SimpleNamespace(
        model="openai/actual-embedding",
        revision="embedding-revision-1",
        api_base="https://embedding.example/v1",
        dimension=384,
        fingerprint="actual-fingerprint",
    )
    reranker = SimpleNamespace(model_name="actual-reranker", revision="reranker-revision-1")
    search = SimpleNamespace(
        dense=SimpleNamespace(embedder=embedder),
        reranker=reranker,
        fusion=SimpleNamespace(k=60),
        top_n=30,
        top_k=8,
    )
    snapshot = artifacts.retrieval_snapshot(search, Settings())
    embedder.model = "changed-embedding"
    embedder.revision = "embedding-revision-2"
    embedder.api_base = "https://changed.example/v1"
    reranker.model_name = "changed-reranker"
    reranker.revision = "reranker-revision-2"
    assert snapshot["embedding_model"] == "openai/actual-embedding"
    assert snapshot["embedding_revision"] == "embedding-revision-1"
    assert snapshot["embedding_api_base"] == "https://embedding.example/v1"
    assert snapshot["reranker_model"] == "actual-reranker"
    assert snapshot["reranker_revision"] == "reranker-revision-1"


def test_manifest_fallback_distinguishes_configured_revisions_from_actual_adapters() -> None:
    dataset = EvaluationDataset(
        dataset_id="fixture",
        label_source="synthetic",
        description="NOT A BENCHMARK",
        cases=[EvaluationCase(id="q", query="q", question_type="fact", expected_answer="answer")],
    )
    settings = Settings(
        embedding_revision="configured-embedding-revision",
        embedding_api_base="https://configured.example/v1",
        reranker_revision="configured-reranker-revision",
    )
    fallback = artifacts.manifest(dataset, settings)["retrieval_configuration"]
    assert fallback["configuration_available"] is False
    assert fallback["embedding_revision"] == "configured-embedding-revision"
    assert fallback["embedding_api_base"] == "https://configured.example/v1"
    assert fallback["reranker_revision"] == "configured-reranker-revision"
    missing = artifacts.retrieval_snapshot(SimpleNamespace(), Settings())
    assert missing["embedding_revision"] is None
    assert missing["embedding_api_base"] is None
    assert missing["reranker_revision"] is None


def test_workflow_cost_includes_embedding_and_excludes_judge() -> None:
    from ragagent.evaluation.artifacts import workflow_usage

    chat = MockProvider([])
    embedding = MockProvider([])
    judge = MockProvider([])
    for provider in (chat, embedding, judge):
        provider.usage = Usage()
    before = [usage_snapshot(provider) for provider in (chat, embedding, judge)]
    chat.usage.record_cost(2.0)
    embedding.usage.prompt_tokens = 30
    embedding.usage.record_identity("embedding-snapshot-1")
    embedding.usage.record_cost(0.25)
    judge.usage.record_cost(0.5)
    usage = {
        name: usage_delta(snapshot, usage_snapshot(provider))
        for name, snapshot, provider in zip(
            ("analyst", "embedding", "judge"), before, (chat, embedding, judge), strict=True
        )
    }
    assert workflow_usage(usage)["total_workflow_cost"] == 2.25
    assert workflow_usage(usage)["total_workflow_tokens"] == 30
    assert usage["embedding"]["provider_models"] == ["embedding-snapshot-1"]
    embedding.usage.record_cost(None)
    usage["embedding"] = usage_delta(before[1], usage_snapshot(embedding))
    assert workflow_usage(usage)["total_workflow_cost"] is None
    assert workflow_usage(usage)["unknown_workflow_cost_calls"] == 1


def test_failed_evaluation_artifact_is_downloadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragagent.api.evaluations import artifact
    from ragagent.db.models import Run

    run = Run(id="00000000-0000-0000-0000-000000000001", kind="eval_rag", status="failed")
    path = tmp_path / "evaluations" / run.id
    path.mkdir(parents=True)
    (path / "results.json").write_text('{"status": "partial"}')
    monkeypatch.setattr(
        "ragagent.api.evaluations.get_settings", lambda: Settings(data_dir=tmp_path)
    )
    db = SimpleNamespace(get=lambda model, run_id: run)
    response = artifact(run.id, "results.json", db)
    assert response.path == path / "results.json"
