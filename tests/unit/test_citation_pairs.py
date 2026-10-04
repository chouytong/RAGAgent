import pytest
from pydantic import ValidationError

from ragagent.domain.research import (
    AnswerDraft,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    EvidenceRecord,
    QueryPlan,
    VerificationResponse,
)
from ragagent.graphs.rag import build_rag
from ragagent.graphs.research import build_research
from ragagent.graphs.state import AnalysisResult, MultiAgentState, RAGState
from ragagent.providers.chat import MockProvider
from ragagent.retrieval.evidence import verify_claims
from tests.unit.helpers import evidence
from tests.unit.test_graphs import RoundSearch, plan


def two_sources() -> list[EvidenceRecord]:
    return [evidence(), evidence("c2", "00000000-0000-0000-0000-000000000002", "p2")]


def paired_claim(sources: list[EvidenceRecord]) -> Claim:
    return Claim(
        claim_id="c",
        text="The method uses contrastive training.",
        evidence_ids=[e.evidence_id for e in sources],
        aspect="method",
    )


def pair_response(claim: Claim, ids: list[str]) -> VerificationResponse:
    return VerificationResponse(
        question_answered=True,
        verdicts=[ClaimVerdict(claim_id=claim.claim_id, supported=True, reason="exact quote")],
        supported_pairs=[
            ClaimEvidencePair(claim_id=claim.claim_id, evidence_id=eid) for eid in ids
        ],
    )


@pytest.mark.parametrize("failure", ["missing", "duplicate", "unknown_evidence", "unknown_claim"])
async def test_claim_support_cannot_override_invalid_citation_pairs(failure: str) -> None:
    sources = two_sources()
    claim = paired_claim(sources)
    response = pair_response(claim, claim.evidence_ids)
    if failure == "missing":
        response.supported_pairs.pop()
    elif failure == "duplicate":
        response.supported_pairs.append(response.supported_pairs[0])
    elif failure == "unknown_evidence":
        response.supported_pairs.append(ClaimEvidencePair(claim_id="c", evidence_id="unknown"))
    else:
        response.supported_pairs.append(
            ClaimEvidencePair(claim_id="unknown", evidence_id=sources[0].evidence_id)
        )
    validation = await verify_claims([claim], sources, ["method"], MockProvider([response]))
    assert not validation.valid
    assert not validation.verdicts[0].supported
    assert validation.supported_pairs == []


async def test_comparison_accepts_citations_supporting_different_parts() -> None:
    sources = two_sources()
    for source, text in zip(
        sources, ["Method A has accuracy 90%.", "Method B has accuracy 85%."], strict=True
    ):
        source.content = source.quote = text
        source.span_end = len(text)
    claim = paired_claim(sources)
    claim.text = "Method A exceeds Method B's accuracy by 5 percentage points."
    response = pair_response(claim, claim.evidence_ids)
    validation = await verify_claims([claim], sources, ["method"], MockProvider([response]))
    assert validation.valid
    assert validation.supported_pairs == response.supported_pairs


def test_verifier_requires_explicit_pair_results() -> None:
    with pytest.raises(ValidationError):
        VerificationResponse.model_validate(
            {
                "question_answered": True,
                "verdicts": [{"claim_id": "c", "supported": True, "reason": "legacy output"}],
            }
        )


async def test_duplicate_citations_are_rejected_before_model_call() -> None:
    sources = two_sources()[:1]
    claim = paired_claim(sources)
    claim.evidence_ids *= 2
    reviewer = MockProvider([])
    validation = await verify_claims([claim], sources, ["method"], reviewer)
    assert not validation.valid and not reviewer.calls


@pytest.mark.parametrize("workflow", ["rag", "research"])
async def test_workflows_refuse_a_true_claim_with_an_unrelated_citation(workflow: str) -> None:
    sources = two_sources()
    unrelated = "This paper studies a different method."
    sources[1].content = sources[1].quote = unrelated
    sources[1].span_end = len(unrelated)
    claim = paired_claim(sources)
    # The reviewer correctly supports the claim with only the first citation.
    reviewer = MockProvider([pair_response(claim, [sources[0].evidence_id])])
    if workflow == "rag":
        result = RAGState.model_validate(
            await build_rag(
                RoundSearch([sources]),
                MockProvider([QueryPlan(queries=["q"], required_aspects=["method"])]),
                MockProvider([]),
                MockProvider([AnswerDraft(claims=[claim])]),
                reviewer,
                max_retries=0,
            ).ainvoke(RAGState(query="What method?"))
        )
        assert result.status == "insufficient_evidence"
        output = result.answer
    else:
        research = MultiAgentState.model_validate(
            await build_research(
                RoundSearch([sources]),
                MockProvider([plan()]),
                MockProvider([]),
                MockProvider([AnalysisResult(claims=[claim])]),
                reviewer,
                max_retrievals=1,
                max_revisions=0,
            ).ainvoke(MultiAgentState(research_question="What method?"))
        )
        assert research.status == "insufficient_evidence"
        output = research.draft_report
    assert "[E:" not in output
    assert claim.text not in output
