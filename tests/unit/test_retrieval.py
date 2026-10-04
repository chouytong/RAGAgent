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
    accepted_evidence,
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
        [
            VerificationResponse(
                question_answered=True,
                verdicts=[ClaimVerdict(claim_id="c", supported=False, reason="no")],
            )
        ]
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


async def test_supported_flag_cannot_override_contradiction() -> None:
    e = evidence()
    claim = Claim(
        claim_id="c",
        text="All methods are identical",
        evidence_ids=[e.evidence_id],
        aspect="method",
    )
    provider = MockProvider(
        [
            VerificationResponse(
                question_answered=True,
                verdicts=[
                    ClaimVerdict(
                        claim_id="c", supported=True, contradiction=True, reason="contradicted"
                    )
                ],
            )
        ]
    )
    result = await verify_claims([claim], [e], ["method"], provider, question="Compare methods")
    assert not result.valid and result.missing_aspects == ["method"]


@pytest.mark.parametrize(
    "marker",
    [
        "[E:00000000-0000-0000-0000-000000000001]",
        "[E:00000000-0000-0000-0000-000000000099]",
        "[e:00000000-0000-0000-0000-000000000099]",
        "[E:not-a-valid-citation]",
    ],
)
async def test_inline_citations_are_rejected_and_not_rendered(marker: str) -> None:
    e = evidence()
    claim = Claim(
        claim_id="c", text="The method uses training. " + marker, evidence_ids=[e.evidence_id]
    )
    provider = MockProvider([])
    result = await verify_claims([claim], [e], [], provider)
    assert not result.valid and result.missing_citations == ["c"]
    assert not provider.calls
    assert parse_citations(render_claims([claim])) == [e.evidence_id]
    assert marker not in render_claims([claim]).removesuffix(f"[E:{e.evidence_id}]")


@pytest.mark.parametrize("extra_id", ["unknown", "c"])
async def test_unknown_and_duplicate_verifier_verdicts_fail_closed(extra_id: str) -> None:
    e = evidence()
    claim = Claim(claim_id="c", text=e.quote, evidence_ids=[e.evidence_id], aspect="method")
    provider = MockProvider(
        [
            VerificationResponse(
                question_answered=True,
                verdicts=[
                    ClaimVerdict(claim_id="c", supported=True, reason="quote"),
                    ClaimVerdict(claim_id=extra_id, supported=True, reason="extra"),
                ],
            )
        ]
    )
    result = await verify_claims([claim], [e], ["method"], provider)
    assert not result.valid
    assert not result.verdicts[0].supported
    assert result.missing_aspects == ["method"]


def test_accepted_evidence_requires_a_valid_span_and_explicit_threshold_score() -> None:
    good = evidence("good")
    low = evidence("low", "00000000-0000-0000-0000-000000000002")
    low.scores = {"rerank": 0.5}
    missing = evidence("missing", "00000000-0000-0000-0000-000000000003")
    missing.scores = {}
    invalid = evidence("invalid", "00000000-0000-0000-0000-000000000004")
    invalid.quote = "invented"
    assert accepted_evidence([good, low, missing, invalid], 1.0) == [good]
