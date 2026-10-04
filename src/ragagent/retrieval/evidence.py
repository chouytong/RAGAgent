import re
from collections import Counter
from typing import Any

from ragagent.domain.research import (
    CitationValidation,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    EvidenceRecord,
    EvidenceSufficiencyResult,
    QueryPlan,
    Sufficiency,
    VerificationResponse,
)
from ragagent.providers.chat import ChatProvider

CITATION = re.compile(r"\[E:([0-9a-f-]{36})\]")
INLINE_CITATION = re.compile(r"\[E:[^\]]*(?:\]|$)", re.IGNORECASE)


def parse_citations(text: str) -> list[str]:
    return list(dict.fromkeys(CITATION.findall(text)))


def exact_span(evidence: EvidenceRecord) -> bool:
    return (
        0 <= evidence.span_start < evidence.span_end <= len(evidence.content)
        and evidence.content[evidence.span_start : evidence.span_end] == evidence.quote
        and all(
            0 <= source.chunk_start < source.chunk_end <= len(evidence.content)
            and 0 <= source.span_start < source.span_end
            and source.span_end - source.span_start == source.chunk_end - source.chunk_start
            for source in evidence.source_spans
        )
        and all(
            0 <= context.span_start < context.span_end <= len(context.content)
            and context.content[context.span_start : context.span_end] == context.quote
            for context in evidence.source_context
        )
    )


def evidence_payload(evidence: EvidenceRecord) -> dict[str, Any]:
    """Send exact quotes and provenance once; keep raw text local for span validation."""
    payload = evidence.model_dump(
        exclude={"content": True, "source_context": {"__all__": {"content"}}}
    )
    seen: set[str] = set()
    auxiliary = []
    for context in payload["source_context"]:
        quote = context["quote"]
        if quote not in evidence.quote and quote not in seen:
            auxiliary.append(context)
            seen.add(quote)
    payload["source_context"] = auxiliary
    return payload


def accepted_evidence(
    evidence: list[EvidenceRecord], minimum_rerank_score: float = 0.0
) -> list[EvidenceRecord]:
    """Return only evidence eligible for generation and citation verification."""
    by_id: dict[str, EvidenceRecord] = {}
    for item in evidence:
        score = item.scores.get("rerank")
        if exact_span(item) and score is not None and score >= minimum_rerank_score:
            by_id.setdefault(item.evidence_id, item)
    return list(by_id.values())


def merge_evidence(
    previous: list[EvidenceRecord],
    incoming: list[EvidenceRecord],
    minimum_rerank_score: float,
    max_records: int,
) -> tuple[list[EvidenceRecord], bool]:
    """Keep accepted prior evidence before adding new records within a fixed budget."""
    if max_records < 1:
        raise ValueError("evidence_budget_must_be_positive")
    combined = accepted_evidence(previous + incoming, minimum_rerank_score)
    return combined[:max_records], len(combined) > max_records


async def verify_claims(
    claims: list[Claim],
    evidence: list[EvidenceRecord],
    aspects: list[str],
    provider: ChatProvider,
    question: str = "",
) -> CitationValidation:
    if len({c.claim_id for c in claims}) != len(claims):
        return CitationValidation(
            valid=False, missing_citations=[c.claim_id for c in claims], missing_aspects=aspects
        )
    by_id = {e.evidence_id: e for e in evidence if exact_span(e)}
    invalid = [
        c.claim_id
        for c in claims
        if INLINE_CITATION.search(c.text)
        or not c.evidence_ids
        or len(set(c.evidence_ids)) != len(c.evidence_ids)
        or any(eid not in by_id for eid in c.evidence_ids)
    ]
    eligible = [c for c in claims if c.claim_id not in invalid]
    verdicts: list[ClaimVerdict] = [
        ClaimVerdict(claim_id=cid, supported=False, reason="missing_citation_or_invalid_span")
        for cid in invalid
    ]
    model_missing: list[str] = []
    validated_pairs: list[ClaimEvidencePair] = []
    if eligible:
        cited_ids = {eid for claim in eligible for eid in claim.evidence_ids}
        response = await provider.complete(
            "Verify each claim only against its cited exact quotes. Check numeric values, "
            "comparative statements, scope and contradictions. Reject unsupported inference. "
            "For supported_pairs, include each claim/evidence pair only when that citation "
            "supports at least one part of the claim. Different citations may support different "
            "parts of a comparison. A supported claim does not make every attached citation "
            "valid: omit irrelevant or unsupported pairs. Return each supported pair once. "
            "Also assess whether the original question and required aspects are fully answered. "
            "Return one verdict per claim; do not assign a confidence probability.",
            {
                "question": question,
                "required_aspects": aspects,
                "claims": [c.model_dump() for c in eligible],
                "evidence": [evidence_payload(by_id[eid]) for eid in sorted(cited_ids)],
            },
            VerificationResponse,
        )
        model_missing = response.missing_aspects + (
            [] if response.question_answered else ["original_question"]
        )
        grouped: dict[str, list[ClaimVerdict]] = {}
        for v in response.verdicts:
            grouped.setdefault(v.claim_id, []).append(v)
        unexpected = set(grouped) - {c.claim_id for c in eligible}
        expected_pairs = {(c.claim_id, eid) for c in eligible for eid in c.evidence_ids}
        pair_counts = Counter((p.claim_id, p.evidence_id) for p in response.supported_pairs)
        invalid_pair_set = any(
            p not in expected_pairs or count != 1 for p, count in pair_counts.items()
        )
        for c in eligible:
            matched = grouped.get(c.claim_id, [])
            if len(matched) != 1 or unexpected:
                verdict = ClaimVerdict(
                    claim_id=c.claim_id,
                    supported=False,
                    reason="verifier_unknown_verdict"
                    if unexpected
                    else "verifier_missing_or_duplicate_verdict",
                )
            elif invalid_pair_set:
                verdict = ClaimVerdict(
                    claim_id=c.claim_id,
                    supported=False,
                    reason="verifier_unknown_or_duplicate_support_pair",
                )
            elif matched[0].supported and any(
                pair_counts[(c.claim_id, eid)] != 1 for eid in c.evidence_ids
            ):
                verdict = ClaimVerdict(
                    claim_id=c.claim_id,
                    supported=False,
                    reason="verifier_missing_support_pair",
                )
            else:
                verdict = matched[0]
            verdicts.append(verdict)
            if verdict.supported and not verdict.contradiction:
                validated_pairs.extend(
                    ClaimEvidencePair(claim_id=c.claim_id, evidence_id=eid)
                    for eid in c.evidence_ids
                )
    supported = {v.claim_id for v in verdicts if v.supported and not v.contradiction}
    covered = {c.aspect for c in claims if c.claim_id in supported}
    missing = list(dict.fromkeys([a for a in aspects if a not in covered] + model_missing))
    return CitationValidation(
        valid=bool(claims) and len(supported) == len(claims) and not missing,
        verdicts=verdicts,
        supported_pairs=validated_pairs,
        missing_citations=invalid,
        missing_aspects=missing,
    )


def evidence_gate(
    plan: QueryPlan,
    evidence: list[EvidenceRecord],
    validation: CitationValidation | None = None,
    minimum_rerank_score: float = 0.0,
) -> EvidenceSufficiencyResult:
    unique = {e.chunk_id: e for e in accepted_evidence(evidence, minimum_rerank_score)}
    papers = {e.paper.paper_id for e in unique.values()}
    reasons: list[str] = []
    status = Sufficiency.SUFFICIENT
    if not unique:
        status = Sufficiency.INSUFFICIENT
        reasons.append("no_usable_evidence")
    elif plan.question_type == "comparison" and len(papers) < 2:
        status = Sufficiency.PARTIAL
        reasons.append("comparison_needs_multiple_papers")
    if validation is not None and not validation.valid:
        status = (
            Sufficiency.INSUFFICIENT
            if not any(v.supported for v in validation.verdicts)
            else Sufficiency.PARTIAL
        )
        reasons.append("citation_or_aspect_verification_failed")
    coverage = None
    if validation is not None and validation.verdicts:
        coverage = sum(v.supported and not v.contradiction for v in validation.verdicts) / len(
            validation.verdicts
        )
    return EvidenceSufficiencyResult(
        status=status,
        reasons=reasons,
        evidence_count=len(unique),
        paper_count=len(papers),
        citation_coverage=coverage,
    )


def render_claims(claims: list[Claim]) -> str:
    return "\n\n".join(
        INLINE_CITATION.sub("", c.text).strip()
        + " "
        + " ".join(f"[E:{eid}]" for eid in c.evidence_ids)
        for c in claims
    )
