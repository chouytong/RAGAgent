import copy
import json

import pytest

from ragagent.conversations.presentation import message_presentation
from tests.unit.helpers import evidence


def result() -> dict:
    source = evidence()
    return {
        "answer": f"A supported claim. [E:{source.evidence_id}]",
        "claims": [
            {"claim_id": "c1", "text": "A supported claim.", "evidence_ids": [source.evidence_id]}
        ],
        "citation_validation": {
            "valid": True,
            "verdicts": [{"claim_id": "c1", "supported": True, "reason": "exact"}],
            "supported_pairs": [{"claim_id": "c1", "evidence_id": source.evidence_id}],
        },
        "reranked_evidence": [source.model_dump()],
        "limitations": [f"Note {i} " + "x" * 1000 for i in range(20)],
        "conversation_context": {
            "original_query": "q" * 1000,
            "contextualized_query": "r" * 1000,
            "recent_messages": "PRIVATE CONTEXT",
        },
        "execution_trace": "UNRELEASED DRAFT" * 10000,
    }


def test_presentation_is_bounded_and_excludes_heavy_source_and_context() -> None:
    value = result()
    original = copy.deepcopy(value)
    small = message_presentation(value)
    assert value == original
    assert len(small["limitations"]) == 8
    assert all(len(note) <= 240 for note in small["limitations"])
    assert len(small["conversation_context"]["original_query"]) == 300
    assert len(small["citation_refs"]) == 1
    assert (
        small["citation_refs"][0]["paper_id"] == value["reranked_evidence"][0]["paper"]["paper_id"]
    )
    encoded = json.dumps(small)
    assert len(encoded) < 4000
    assert value["reranked_evidence"][0]["quote"] not in encoded
    assert "PRIVATE CONTEXT" not in encoded and "UNRELEASED DRAFT" not in encoded


@pytest.mark.parametrize(
    "failure", ["invalid", "contradiction", "undeclared", "unpaired", "unrendered", "bad_span"]
)
def test_only_final_supported_exact_citations_enter_the_manifest(failure: str) -> None:
    value = result()
    if failure == "invalid":
        value["citation_validation"]["valid"] = False
    elif failure == "contradiction":
        value["citation_validation"]["verdicts"][0]["contradiction"] = True
    elif failure == "undeclared":
        value["claims"] = []
    elif failure == "unpaired":
        value["citation_validation"]["supported_pairs"] = []
    elif failure == "unrendered":
        value["answer"] = "No source marker."
    else:
        value["reranked_evidence"][0]["quote"] = "Invented text"
    assert message_presentation(value)["citation_refs"] == []


def test_research_and_legacy_projection_keep_lazy_detail_available() -> None:
    value = result()
    value["draft_report"] = value.pop("answer")
    value["evidence_pool"] = value.pop("reranked_evidence")
    value["analysis_results"] = [{"claims": value.pop("claims"), "limitations": ["Analysis limit"]}]
    value["review_result"] = {"decision": "PASS", "validation": value.pop("citation_validation")}
    assert len(message_presentation(value)["citation_refs"]) == 1
    value["review_result"]["decision"] = "NEED_REVISION"
    assert message_presentation(value)["citation_refs"] == []
    assert message_presentation({"answer": "Legacy result"}) == {
        "limitations": [],
        "citation_refs": [],
    }
