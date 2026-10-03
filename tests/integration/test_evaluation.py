import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

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
    dataset.cases[0].relevant_chunk_ids = ["unknown"]
    with pytest.raises(ValueError, match="unknown_chunks"):
        await evaluate_retrieval(
            dataset,
            HybridRetriever(empty_db, Embedder(), FixtureReranker()),
            empty_db,
            Settings(),
            tmp_path,
        )
