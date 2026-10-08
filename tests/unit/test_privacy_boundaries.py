import pytest
from pydantic import ValidationError

from ragagent.api.schemas import QueryRequest, ResearchRequest
from ragagent.domain.privacy import reject_credentials
from ragagent.evaluation.schema import EvaluationDataset
from ragagent.providers.config import AgentModel


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "SYNTHETIC" * 4,
        "Bearer " + "SYNTHETIC" * 8,
        "postgresql://user:password@localhost/db",
        "redis://user:password@localhost/0",
        "OPENAI_API_KEY=" + "SYNTHETIC" * 4,
    ],
)
def test_secret_patterns_rejected_recursively_at_persisted_input_boundaries(secret: str) -> None:
    for payload in [
        {"nested": [secret]},
        {"OPENAI_API_KEY": "SYNTHETIC" * 4},
        {secret: "ordinary"},
    ]:
        with pytest.raises(ValueError, match="credential_content_not_allowed"):
            reject_credentials(payload)
    with pytest.raises(ValidationError):
        QueryRequest(query=secret)
    with pytest.raises(ValidationError):
        ResearchRequest(research_question=secret)
    with pytest.raises(ValidationError):
        AgentModel(provider="openai", model=secret)


def test_mutated_evaluation_revalidates_before_execution() -> None:
    spec = EvaluationDataset.model_validate(
        {
            "dataset_id": "SYNTHETIC",
            "label_source": "synthetic",
            "description": "NOT A BENCHMARK",
            "cases": [
                {
                    "id": "1",
                    "query": "ordinary",
                    "question_type": "fact",
                    "relevant_chunk_ids": ["1"],
                    "expected_answer": "ordinary",
                }
            ],
        }
    )
    spec.cases[0].notes = "Bearer " + "SYNTHETIC" * 8
    with pytest.raises(ValueError, match="credential_content_not_allowed"):
        spec.runnable()
