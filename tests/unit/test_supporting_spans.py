"""Scripted verifier offsets, not scientific correctness or model quality."""

import pytest

from ragagent.domain.research import Claim, ClaimEvidencePair, ClaimVerdict, VerificationResponse
from ragagent.providers.chat import MockProvider
from ragagent.retrieval.evidence import verify_claims
from tests.unit.helpers import evidence


@pytest.mark.parametrize(
    "offsets", [(3, 11), (-1, 8), (8, 3), (3, 1000), (None, 8), (3, None), (None, None)]
)
async def test_narrow_span_is_original_unicode_text_or_original_chunk_fallback(
    offsets: tuple[int | None, int | None],
) -> None:
    item = evidence()
    item.content = "😀前言方法达到23%。后续内容"
    item.span_start, item.span_end = 3, len(item.content)
    item.quote = item.content[item.span_start : item.span_end]
    original = item.model_dump()
    response = VerificationResponse(
        question_answered=True,
        verdicts=[ClaimVerdict(claim_id="c", supported=True, reason="SCRIPTED")],
        supported_pairs=[
            ClaimEvidencePair(
                claim_id="c",
                evidence_id=item.evidence_id,
                supporting_span_start=offsets[0],
                supporting_span_end=offsets[1],
            )
        ],
    )
    validation = await verify_claims(
        [Claim(claim_id="c", text="方法达到23%。", evidence_ids=[item.evidence_id])],
        [item],
        [],
        MockProvider([response]),
    )
    assert validation.valid
    pair = validation.supported_pairs[0]
    expected = (3, 11) if offsets == (3, 11) else (None, None)
    assert (pair.supporting_span_start, pair.supporting_span_end) == expected
    if pair.supporting_span_start is not None and pair.supporting_span_end is not None:
        assert (
            item.content[pair.supporting_span_start : pair.supporting_span_end] == "方法达到23%。"
        )
    assert item.model_dump() == original  # Never replace quote/content with a generated quote.


async def test_unsupported_claim_cannot_release_narrow_span() -> None:
    item = evidence()
    response = VerificationResponse(
        question_answered=False,
        verdicts=[ClaimVerdict(claim_id="c", supported=False, reason="SCRIPTED")],
        supported_pairs=[
            ClaimEvidencePair(
                claim_id="c",
                evidence_id=item.evidence_id,
                supporting_span_start=0,
                supporting_span_end=5,
            )
        ],
    )
    result = await verify_claims(
        [Claim(claim_id="c", text="unsupported", evidence_ids=[item.evidence_id])],
        [item],
        [],
        MockProvider([response]),
    )
    assert not result.valid and not result.supported_pairs
