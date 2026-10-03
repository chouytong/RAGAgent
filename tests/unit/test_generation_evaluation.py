from pathlib import Path

import pytest

from ragagent.domain.research import AnswerDraft, QueryPlan
from ragagent.evaluation.generation import evaluate_generation
from ragagent.evaluation.schema import CitationPair, EvaluationCase, EvaluationDataset, RAGJudgment
from ragagent.graphs.state import AnalysisResult
from ragagent.providers.chat import MockProvider
from ragagent.settings import Settings
from tests.unit.helpers import evidence
from tests.unit.test_graphs import Search, claims, plan, verdict


@pytest.mark.parametrize("multi", [False, True])
async def test_generation_evaluators_execute_graph_and_judge(tmp_path: Path, multi: bool) -> None:
    dataset = EvaluationDataset(
        dataset_id="synthetic-test",
        label_source="synthetic",
        description="NOT A BENCHMARK",
        cases=[
            EvaluationCase(
                id="q",
                query="What method?",
                question_type="fact",
                relevant_chunk_ids=["c1"],
                expected_answer="Contrastive training.",
                required_aspects=["method"],
            )
        ],
    )
    agents = {
        "supervisor": MockProvider(
            [plan() if multi else QueryPlan(queries=["q"], required_aspects=["method"])]
        ),
        "retriever": MockProvider([]),
        "analyst": MockProvider(
            [AnalysisResult(claims=claims()) if multi else AnswerDraft(claims=claims())]
        ),
        "reviewer": MockProvider([verdict()]),
    }
    judge = MockProvider(
        [
            RAGJudgment(
                supported_pairs=[CitationPair(claim_id="c", evidence_id=evidence().evidence_id)],
                supported_claim_ids=["c"],
                answered_aspects=["method"],
            )
        ]
    )
    result = await evaluate_generation(
        dataset, Search(), agents, judge, Settings(), tmp_path, multi_agent=multi
    )
    assert result["manifest"]["judge"]["evaluation_type"] == "MODEL_BASED"
    assert result["manifest"]["judge"]["adapter"] == "MockProvider"
    assert result["summary"]["citation_precision"] == 1.0
    assert result["summary"]["answer_completeness"] == 1.0
    assert result["total_workflow_cost"] is None
    assert (tmp_path / "results.json").exists() and (tmp_path / "results.md").exists()
