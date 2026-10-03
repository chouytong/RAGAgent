import pytest
from pydantic import ValidationError

from ragagent.domain.research import (
    Claim,
    ClaimVerdict,
    MetadataFilter,
    QueryPlan,
    Sufficiency,
    VerificationResponse,
)
from ragagent.providers.chat import MockProvider
from ragagent.retrieval.evidence import (
    evidence_gate,
    exact_span,
    parse_citations,
    render_claims,
    verify_claims,
)
from ragagent.retrieval.fusion import RRFusion
from tests.unit.helpers import candidate, evidence


def test_rrf_deduplicates_and_scores() -> None:
    r = RRFusion(60).fuse([[candidate("a"), candidate("a"), candidate("b")], [candidate("b")]], 2)
    assert r[0].evidence.chunk_id == "b"
    assert r[0].score == pytest.approx(1 / 63 + 1 / 61)
    assert r[1].score == pytest.approx(1 / 61)


def test_filter_years() -> None:
    with pytest.raises(ValidationError):
        MetadataFilter(year_start=2024, year_end=2020)


async def test_invalid_citation_and_span_do_not_call_model() -> None:
    e = evidence()
    e.quote = "fabricated text"
    assert not exact_span(e)
    mock = MockProvider([])
    result = await verify_claims(
        [Claim(claim_id="c", text="claim", evidence_ids=[e.evidence_id])], [e], [], mock
    )
    assert not result.valid and not mock.calls


async def test_semantic_rejection_missing_aspect_and_verifier_omission() -> None:
    e = evidence()
    claim = Claim(claim_id="c", text="Unsupported", evidence_ids=[e.evidence_id], aspect="method")
    mock = MockProvider(
        [VerificationResponse(verdicts=[ClaimVerdict(claim_id="c", supported=False, reason="no")])]
    )
    result = await verify_claims([claim], [e], ["method"], mock)
    assert not result.valid and result.missing_aspects == ["method"]
    omitted = await verify_claims([claim], [e], [], MockProvider([{"verdicts": []}]))
    assert not omitted.valid


def test_gate_diversity_and_citations() -> None:
    assert evidence_gate(QueryPlan(queries=["q"]), []).status == Sufficiency.INSUFFICIENT
    assert (
        evidence_gate(QueryPlan(queries=["q"], question_type="comparison"), [evidence()]).status
        == Sufficiency.PARTIAL
    )
    claim = Claim(claim_id="c", text="Fact", evidence_ids=[evidence().evidence_id])
    assert parse_citations(render_claims([claim])) == claim.evidence_ids
