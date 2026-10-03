import math

import pytest
from pydantic import ValidationError

from ragagent.evaluation.metrics import retrieval_metrics
from ragagent.evaluation.schema import EvaluationCase, EvaluationDataset


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
