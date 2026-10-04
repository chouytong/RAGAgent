import json
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from ragagent.domain.research import AnswerDraft, QueryPlan
from ragagent.evaluation.generation import evaluate_generation, judge_metrics
from ragagent.evaluation.schema import CitationPair, EvaluationCase, EvaluationDataset, RAGJudgment
from ragagent.graphs.state import AnalysisResult
from ragagent.providers.chat import MockProvider
from ragagent.providers.config import AgentModel
from ragagent.settings import Settings
from tests.unit.helpers import evidence
from tests.unit.test_graphs import Search, claims, plan, verdict

T = TypeVar("T", bound=BaseModel)


class RecordingProvider(MockProvider):
    def __init__(
        self,
        responses: list[BaseModel],
        model: AgentModel | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(responses)
        self.model = model
        self.timeout = timeout
        self.payloads: list[dict[str, Any]] = []

    async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
        self.payloads.append(payload)
        return await super().complete(instruction, payload, schema)


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
    saved = json.loads((tmp_path / "results.json").read_text())
    row = saved["per_query"][0]
    assert row["actual_output"] and "[E:" in row["actual_output"]
    assert row["evidence"][0]["quote"] == evidence().quote
    assert row["evidence"][0]["paper"]["paper_id"] == "p1"
    assert row["evidence"][0]["page_start"] == 2
    assert row["gold_labels"]["required_aspects"] == ["method"]
    assert row["judgment"]["supported_claim_ids"] == ["c"]
    assert row["judge_input"]["actual_output"] == row["actual_output"]
    assert row["judge_input"]["workflow_status"] == "completed"
    assert "content" not in row["judge_input"]["evidence"][0]
    assert saved["manifest"]["corpus_snapshot"]["availability"] == "unavailable"
    assert len(saved["manifest"]["evidence_snapshot_hash"]) == 64
    assert len(saved["manifest"]["source_hash"]) == 64
    assert "migrations" in saved["manifest"]["source_hash_scope"]


@pytest.mark.parametrize("expected_refusal", [False, True])
@pytest.mark.parametrize("judge_supports_refusal", [False, True])
async def test_refusal_requires_explicit_gold_and_judged_actual_output(
    expected_refusal: bool,
    judge_supports_refusal: bool,
) -> None:
    judge = RecordingProvider(
        [
            RAGJudgment(
                supported_pairs=[],
                supported_claim_ids=["invented"],
                answered_aspects=["method"],
                refusal_supported=judge_supports_refusal,
            )
        ]
    )
    result = await judge_metrics(
        [],
        [],
        "Insufficient evidence." if expected_refusal else "The method is contrastive.",
        ["method"],
        {"c1"},
        judge,
        actual_output="Insufficient evidence in the indexed literature.",
        workflow_status="insufficient_evidence",
        expected_refusal=expected_refusal,
    )
    expected = float(expected_refusal and judge_supports_refusal)
    assert result.metrics["answer_completeness"] == expected
    assert result.metrics["refusal_correctness"] == expected
    assert result.metrics["citation_precision"] is None
    assert result.metrics["unsupported_claim_rate"] is None
    assert judge.payloads[0]["workflow_status"] == "insufficient_evidence"
    assert judge.payloads[0]["actual_output"].startswith("Insufficient evidence")
    assert result.judgment.answered_aspects == ["method"]


@pytest.mark.parametrize("status,output", [("completed", "Answer."), ("insufficient_evidence", "")])
async def test_expected_refusal_cannot_accept_factual_or_empty_output(
    status: str, output: str
) -> None:
    result = await judge_metrics(
        claims() if status == "completed" else [],
        [evidence()],
        "Insufficient evidence.",
        [],
        set(),
        MockProvider(
            [
                RAGJudgment(
                    supported_pairs=[],
                    supported_claim_ids=[],
                    answered_aspects=[],
                    refusal_supported=True,
                )
            ]
        ),
        actual_output=output,
        workflow_status=status,
        expected_refusal=True,
    )
    assert result.metrics["refusal_correctness"] == 0.0
    assert result.metrics["answer_completeness"] == 0.0


async def test_unknown_judgment_references_do_not_inflate_metrics() -> None:
    result = await judge_metrics(
        claims(),
        [evidence()],
        "Contrastive training.",
        ["method"],
        {"c1"},
        MockProvider(
            [
                RAGJudgment(
                    supported_pairs=[
                        CitationPair(claim_id="invented", evidence_id=evidence().evidence_id),
                        CitationPair(claim_id="c", evidence_id="invented-evidence"),
                    ],
                    supported_claim_ids=["invented", "c"],
                    answered_aspects=["method", "invented-aspect"],
                )
            ]
        ),
        actual_output="The method uses contrastive training.",
        workflow_status="completed",
    )
    assert result.metrics["citation_precision"] == 0.0
    assert result.metrics["citation_completeness"] == 0.0
    assert result.metrics["unsupported_claim_rate"] == 1.0
    assert result.metrics["answer_completeness"] == 0.0


async def test_judge_only_receives_cited_exact_quotes_not_other_source_text() -> None:
    cited = evidence().model_copy(update={"quote": "method", "span_start": 4, "span_end": 10})
    other = evidence(eid="00000000-0000-0000-0000-000000000002", cid="c2")
    judge = RecordingProvider(
        [
            RAGJudgment(
                supported_pairs=[],
                supported_claim_ids=[],
                answered_aspects=[],
            )
        ]
    )
    await judge_metrics(
        claims(),
        [cited, other],
        "Contrastive training.",
        ["method"],
        {"c1"},
        judge,
        actual_output="The method uses contrastive training.",
        workflow_status="completed",
    )
    supplied = judge.payloads[0]["evidence"]
    assert len(supplied) == 1 and supplied[0]["quote"] == "method"
    assert "content" not in supplied[0]


async def test_generation_refusal_case_can_run_without_relevance_or_aspect_labels(
    tmp_path: Path,
) -> None:
    dataset = EvaluationDataset(
        dataset_id="refusal",
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
    agents = {
        "supervisor": MockProvider([QueryPlan(queries=["q"], required_aspects=["method"])]),
        "retriever": MockProvider([]),
        "analyst": MockProvider([]),
        "reviewer": MockProvider([]),
    }
    judge = RecordingProvider(
        [
            RAGJudgment(
                supported_pairs=[],
                supported_claim_ids=[],
                answered_aspects=[],
                refusal_supported=True,
            )
        ]
    )
    result = await evaluate_generation(
        dataset,
        Search(empty_rounds=99),
        agents,
        judge,
        Settings(max_retrieval_retries=0),
        tmp_path,
    )
    assert result["completed_cases"] == 0
    assert result["summary"]["refusal_correctness"] == 1.0
    assert result["summary"]["citation_recall"] is None
    assert result["per_query"][0]["gold_labels"]["expected_refusal"] is True


async def test_manifest_uses_actual_instantiated_models_despite_config_edit(tmp_path: Path) -> None:
    config = tmp_path / "agents.yaml"
    config.write_text("initial configuration")
    settings = Settings(
        agent_config=config,
        max_retrieval_retries=1,
        max_revisions=1,
        max_iterations=12,
        rag_evidence_budget=16,
        research_evidence_budget=32,
    )
    workflow_model = AgentModel(provider="openai", model="workflow-original")
    judge_model = AgentModel(provider="anthropic", model="independent-judge-original")

    class EditingAnalyst(RecordingProvider):
        async def complete(self, instruction: str, payload: dict[str, Any], schema: type[T]) -> T:
            config.write_text("changed provider configuration")
            settings.max_retrieval_retries = 4
            settings.max_revisions = 4
            settings.max_iterations = 20
            settings.rag_evidence_budget = 64
            settings.research_evidence_budget = 128
            settings.provider_timeout = 99
            return await super().complete(instruction, payload, schema)

    agents = {
        "supervisor": RecordingProvider(
            [QueryPlan(queries=["q"], required_aspects=["method"])], workflow_model
        ),
        "retriever": RecordingProvider([], workflow_model),
        "analyst": EditingAnalyst([AnswerDraft(claims=claims())], workflow_model, timeout=17),
        "reviewer": RecordingProvider([verdict()], workflow_model),
    }
    judge = RecordingProvider(
        [
            RAGJudgment(
                supported_pairs=[CitationPair(claim_id="c", evidence_id=evidence().evidence_id)],
                supported_claim_ids=["c"],
                answered_aspects=["method"],
            )
        ],
        judge_model,
        timeout=23,
    )
    dataset = EvaluationDataset(
        dataset_id="provenance",
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
    result = await evaluate_generation(dataset, Search(), agents, judge, settings, tmp_path)
    assert config.read_text() == "changed provider configuration"
    assert (
        result["manifest"]["model_configuration"]["agents"]["analyst"]["model"]
        == "workflow-original"
    )
    assert result["manifest"]["judge"]["provider"] == "anthropic"
    assert result["manifest"]["judge"]["model"] == "independent-judge-original"
    assert (
        result["manifest"]["model_configuration"]["agents"]["analyst"]["request_timeout_seconds"]
        == 17
    )
    assert result["manifest"]["judge"]["request_timeout_seconds"] == 23
    assert (
        result["manifest"]["model_configuration"]["agents"]["retriever"]["request_timeout_seconds"]
        is None
    )
    assert result["manifest"]["workflow_configuration"] == {
        "max_retrieval_retries": 1,
        "max_revisions": 1,
        "max_iterations": 12,
        "rag_evidence_budget": 16,
        "research_evidence_budget": 32,
    }
