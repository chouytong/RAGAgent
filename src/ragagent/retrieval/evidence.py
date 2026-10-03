import re

from ragagent.domain.research import (
    CitationValidation,
    Claim,
    ClaimVerdict,
    EvidenceRecord,
    EvidenceSufficiencyResult,
    QueryPlan,
    Sufficiency,
    VerificationResponse,
)
from ragagent.providers.chat import ChatProvider

CITATION = re.compile(r"\[E:([0-9a-f-]{36})\]")


def parse_citations(text: str) -> list[str]:
    return list(dict.fromkeys(CITATION.findall(text)))


def exact_span(evidence: EvidenceRecord) -> bool:
    return (
        0 <= evidence.span_start < evidence.span_end <= len(evidence.content)
        and evidence.content[evidence.span_start : evidence.span_end] == evidence.quote
    )


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
        if not c.evidence_ids or any(eid not in by_id for eid in c.evidence_ids)
    ]
    eligible = [c for c in claims if c.claim_id not in invalid]
    verdicts: list[ClaimVerdict] = [
        ClaimVerdict(claim_id=cid, supported=False, reason="missing_citation_or_invalid_span")
        for cid in invalid
    ]
    model_missing: list[str] = []
    if eligible:
        response = await provider.complete(
            "Verify each claim only against its cited exact quotes. Check numeric values, "
            "comparative statements, scope and contradictions. Reject unsupported inference. "
            "Also assess whether the original question and required aspects are fully answered. "
            "Return one verdict per claim; do not assign a confidence probability.",
            {
                "question": question,
                "required_aspects": aspects,
                "claims": [c.model_dump() for c in eligible],
                "evidence": [e.model_dump() for e in by_id.values()],
            },
            VerificationResponse,
        )
        model_missing = response.missing_aspects + (
            [] if response.question_answered else ["original_question"]
        )
        grouped: dict[str, list[ClaimVerdict]] = {}
        for v in response.verdicts:
            grouped.setdefault(v.claim_id, []).append(v)
        for c in eligible:
            matched = grouped.get(c.claim_id, [])
            verdicts.append(
                matched[0]
                if len(matched) == 1
                else ClaimVerdict(
                    claim_id=c.claim_id,
                    supported=False,
                    reason="verifier_missing_or_duplicate_verdict",
                )
            )
    supported = {v.claim_id for v in verdicts if v.supported and not v.contradiction}
    covered = {c.aspect for c in claims if c.claim_id in supported}
    missing = list(dict.fromkeys([a for a in aspects if a not in covered] + model_missing))
    return CitationValidation(
        valid=bool(claims) and len(supported) == len(claims) and not missing,
        verdicts=verdicts,
        missing_citations=invalid,
        missing_aspects=missing,
    )


def evidence_gate(
    plan: QueryPlan,
    evidence: list[EvidenceRecord],
    validation: CitationValidation | None = None,
    minimum_rerank_score: float = 0.0,
) -> EvidenceSufficiencyResult:
    unique = {
        e.chunk_id: e
        for e in evidence
        if exact_span(e) and e.scores.get("rerank", minimum_rerank_score) >= minimum_rerank_score
    }
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
        f"{c.text} " + " ".join(f"[E:{eid}]" for eid in c.evidence_ids) for c in claims
    )
