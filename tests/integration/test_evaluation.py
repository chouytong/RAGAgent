import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ragagent.db.models import Chunk, Entity, Paper, Section
from ragagent.domain.research import Candidate
from ragagent.evaluation.artifacts import corpus_snapshot, verify_corpus_snapshot
from ragagent.evaluation.retrieval import evaluate_retrieval
from ragagent.evaluation.schema import EvaluationCase, EvaluationDataset
from ragagent.retrieval.service import HybridRetriever
from ragagent.settings import Settings
from tests.integration.test_retrieval import Embedder, FixtureReranker, populate


@pytest.mark.integration
async def test_real_ablation_artifacts_and_dataset_provenance(
    empty_db: Session, tmp_path: Path
) -> None:
    pid, cid = populate(empty_db)
    dataset = EvaluationDataset(
        dataset_id="synthetic-test",
        label_source="synthetic",
        description="DEMO ONLY",
        cases=[
            EvaluationCase(
                id="q",
                query="contrastive training",
                question_type="fact",
                relevant_chunk_ids=[cid],
                relevant_paper_ids=[pid],
                expected_answer="Contrastive training",
            )
        ],
    )
    result = await evaluate_retrieval(
        dataset,
        HybridRetriever(empty_db, Embedder(), FixtureReranker()),
        empty_db,
        Settings(),
        tmp_path,
    )
    assert set(result["summary"]) == {"dense", "lexical", "hybrid", "hybrid_rerank"}
    assert result["summary"]["dense"]["Recall@1"] == 1.0
    assert result["manifest"]["label_source"] == "synthetic"
    assert "NOT A BENCHMARK" in (tmp_path / "results.md").read_text()
    assert (
        len(result["manifest"]["git_commit"]) == 40
        and len(result["manifest"]["dataset_hash"]) == 64
    )
    assert json.loads((tmp_path / "results.json").read_text())["summary"] == result["summary"]
    assert result["manifest"]["corpus_snapshot"]["availability"] == "available"
    assert len(result["manifest"]["corpus_snapshot"]["hash"]) == 64
    for rows in result["per_query"].values():
        assert rows[0]["evidence"] and rows[0]["gold_labels"]["relevant_chunk_ids"] == [cid]
        assert rows[0]["evidence"][0]["quote"] == "Contrastive training improves retrieval."
    assert "Corpus hash:" in (tmp_path / "results.md").read_text()
    dataset.cases[0].relevant_chunk_ids = ["unknown"]
    with pytest.raises(ValueError, match="unknown_chunks"):
        await evaluate_retrieval(
            dataset,
            HybridRetriever(empty_db, Embedder(), FixtureReranker()),
            empty_db,
            Settings(),
            tmp_path,
        )


@pytest.mark.integration
@pytest.mark.parametrize("changed", ["paper", "chunk", "vector", "entity", "section_identity"])
def test_corpus_hash_detects_retrieval_inputs_changing(empty_db: Session, changed: str) -> None:
    pid, cid = populate(empty_db)
    initial = corpus_snapshot(empty_db)
    assert initial == corpus_snapshot(empty_db)
    if changed == "paper":
        paper = empty_db.get(Paper, pid)
        assert paper is not None
        paper.year = 2025
    elif changed in {"chunk", "vector", "section_identity"}:
        chunk = empty_db.get(Chunk, cid)
        assert chunk is not None
        if changed == "chunk":
            chunk.content = "The indexed text has changed."
        elif changed == "vector":
            chunk.embedding = [0.0, 1.0] + [0.0] * 382
        else:
            section = empty_db.get(Section, chunk.section_id)
            assert section is not None
            section.identity = "different-structure-identity"
    else:
        entity = empty_db.scalar(select(Entity).where(Entity.entity_type == "dataset"))
        assert entity is not None
        entity.name = "DifferentDataset"
    with pytest.raises(ValueError, match="corpus_changed_during_evaluation"):
        verify_corpus_snapshot(empty_db, initial)


@pytest.mark.integration
async def test_retrieval_evaluation_does_not_change_application_top_k(
    empty_db: Session,
    tmp_path: Path,
) -> None:
    pid, cid = populate(empty_db)
    search = HybridRetriever(empty_db, Embedder(), FixtureReranker(), top_k=3)
    dataset = EvaluationDataset(
        dataset_id="synthetic",
        label_source="synthetic",
        description="NOT A BENCHMARK",
        cases=[
            EvaluationCase(
                id="q",
                query="contrastive training",
                question_type="fact",
                relevant_chunk_ids=[cid],
                relevant_paper_ids=[pid],
                expected_answer="Contrastive training.",
            )
        ],
    )
    result = await evaluate_retrieval(dataset, search, empty_db, Settings(), tmp_path)
    assert search.top_k == 3
    assert result["manifest"]["retrieval_configuration"]["evidence_top_k"] == 3
    assert result["manifest"]["retrieval_configuration"]["evaluation_top_k"] == 10


@pytest.mark.integration
async def test_retrieval_partial_resume_retries_only_failed_mode_and_keeps_embedding_cost(
    empty_db: Session,
    tmp_path: Path,
) -> None:
    from ragagent.errors import ProviderError
    from ragagent.providers.chat import Usage

    class PaidFixtureEmbedder(Embedder):
        def __init__(self) -> None:
            self.usage = Usage()

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.usage.prompt_tokens += len(texts)
            self.usage.record_cost(0.01)
            return await super().embed(texts)

    class FailOnceReranker(FixtureReranker):
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.calls = 0

        async def rerank(
            self, query: str, candidates: list[Candidate], top_k: int
        ) -> list[Candidate]:
            self.calls += 1
            if self.fail:
                self.fail = False
                raise ProviderError("fixture_reranker_failed")
            return await super().rerank(query, candidates, top_k)

    pid, cid = populate(empty_db)
    empty_db.commit()
    dataset = EvaluationDataset(
        dataset_id="resume-fixture",
        label_source="synthetic",
        description="NOT A BENCHMARK",
        cases=[
            EvaluationCase(
                id=f"q{i}",
                query="contrastive training",
                question_type="fact",
                relevant_chunk_ids=[cid],
                relevant_paper_ids=[pid],
                expected_answer="Contrastive.",
            )
            for i in (1, 2)
        ],
    )
    source = tmp_path / "source"
    first = await evaluate_retrieval(
        dataset,
        HybridRetriever(empty_db, PaidFixtureEmbedder(), FailOnceReranker(True)),
        empty_db,
        Settings(),
        source,
    )
    assert first["status"] == "partial" and first["failed_cases"] == 1
    assert first["summary_case_count"]["hybrid_rerank"] == 1
    assert first["summary_case_count"]["dense"] == 2
    assert first["per_query"]["hybrid_rerank"][0]["error_code"] == "fixture_reranker_failed"
    assert first["total_workflow_cost"] == pytest.approx(0.06)
    resumed_embedding, resumed_reranker = PaidFixtureEmbedder(), FailOnceReranker(False)
    resumed = await evaluate_retrieval(
        dataset,
        HybridRetriever(empty_db, resumed_embedding, resumed_reranker),
        empty_db,
        Settings(),
        tmp_path / "resumed",
        resume_directory=source,
    )
    assert resumed["status"] == "completed" and resumed["failed_cases"] == 0
    assert resumed_embedding.usage.calls == resumed_reranker.calls == 1
    assert resumed["total_workflow_cost"] == pytest.approx(0.01)
    assert resumed["previous_attempt_usage"][0]["usage"]["embedding"][
        "known_cost"
    ] == pytest.approx(0.06)
    assert all(count == 2 for count in resumed["summary_case_count"].values())

    paper = empty_db.get(Paper, pid)
    assert paper is not None
    paper.source_status = "withdrawn"
    with pytest.raises(ValueError, match="resume_identity_mismatch"):
        await evaluate_retrieval(
            dataset,
            HybridRetriever(empty_db, PaidFixtureEmbedder(), FailOnceReranker(False)),
            empty_db,
            Settings(),
            tmp_path / "changed",
            resume_directory=source,
        )
