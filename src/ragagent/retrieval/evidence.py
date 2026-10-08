import re
import unicodedata
from collections import Counter
from typing import Any

from ragagent.domain.research import (
    CitationValidation,
    Claim,
    ClaimEvidencePair,
    ClaimVerdict,
    ComparisonEntityCoverage,
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


def _entity_name(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _entity_grounded(entity: str, evidence: EvidenceRecord) -> bool:
    # Names locate entities; their presence is not a semantic support verdict.
    # ASCII boundaries reject e.g. Method A matching Method A1 while allowing
    # Chinese entity names in continuous prose.
    name = re.escape(_entity_name(entity))
    pattern = re.compile(r"(?<![a-z0-9_])" + name + r"(?![a-z0-9_])")
    return any(
        pattern.search(_entity_name(text))
        for text in [evidence.quote, evidence.paper.title]
        + [context.quote for context in evidence.source_context]
    )


def _comparison_coverage(
    entities: list[ComparisonEntityCoverage],
    evidence: dict[str, EvidenceRecord],
    supported_pairs: list[ClaimEvidencePair],
    requested_entities: list[str],
) -> tuple[list[ComparisonEntityCoverage], list[str]]:
    """Combine explicit semantic entity support with deterministic source/pair checks."""
    names = [_entity_name(item.entity) for item in entities]
    errors: list[str] = []
    if len(set(names)) < 2 or any(not name for name in names):
        errors.append("comparison_needs_two_supported_entities")
    if len(names) != len(set(names)):
        errors.append("comparison_duplicate_entity")
    if not {_entity_name(name) for name in requested_entities}.issubset(names):
        errors.append("comparison_requested_entity_missing")
    valid_pairs = {(pair.claim_id, pair.evidence_id) for pair in supported_pairs}
    accepted: list[ComparisonEntityCoverage] = []
    for entity in entities:
        pairs = [(pair.claim_id, pair.evidence_id) for pair in entity.supporting_pairs]
        if not entity.supported:
            errors.append("comparison_entity_not_semantically_supported")
        elif (
            len(pairs) != len(set(pairs))
            or not pairs
            or any(pair not in valid_pairs for pair in pairs)
        ):
            errors.append("comparison_entity_support_pair_invalid")
        elif any(
            pair.evidence_id not in evidence
            or not _entity_grounded(entity.entity, evidence[pair.evidence_id])
            for pair in entity.supporting_pairs
        ):
            errors.append("comparison_entity_not_source_grounded")
        else:
            accepted.append(entity)
    return accepted, list(dict.fromkeys(errors))


async def verify_claims(
    claims: list[Claim],
    evidence: list[EvidenceRecord],
    aspects: list[str],
    provider: ChatProvider,
    question: str = "",
    *,
    comparison: bool = False,
    comparison_entities: list[str] | None = None,
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
    entity_coverage: list[ComparisonEntityCoverage] = []
    if eligible:
        cited_ids = {eid for claim in eligible for eid in claim.evidence_ids}
        response = await provider.complete(
            "Verify each claim only against its cited exact quotes. Check numeric values, "
            "comparative statements, scope and contradictions. Reject unsupported inference. "
            "For supported_pairs, include each claim/evidence pair only when that citation "
            "supports at least one part of the claim. Different citations may support different "
            "parts of a comparison. A supported claim does not make every attached citation "
            "valid: omit irrelevant or unsupported pairs. Return each supported pair once. "
            "Optionally return supporting_span_start/end as Unicode code-point offsets "
            "in the original chunk (quote starts at evidence.span_start). Select the exact "
            "contiguous original text supporting this claim; never generate a replacement "
            "quote. Leave both null when no reliable narrower span can be selected. "
            "Also assess whether the original question and required aspects are fully answered. "
            "When comparison_required is true, return comparison_entities for at least two "
            "distinct canonical compared entities, including every requested entity. For each "
            "entity, supported must assess whether the cited quotes support that entity's "
            "compared facts, not merely whether its name occurs. List supporting_pairs from "
            "the supported claim/citation pairs only. Names must be verbatim source names "
            "in those quotes, auxiliary source quotes or paper titles. Never count aliases "
            "of one entity as different entities; do not infer support from paper count. "
            "Return one verdict per claim; do not assign a confidence probability.",
            {
                "question": question,
                "required_aspects": aspects,
                "comparison_required": comparison,
                "requested_comparison_entities": comparison_entities or [],
                "claims": [c.model_dump() for c in eligible],
                "evidence": [evidence_payload(by_id[eid]) for eid in sorted(cited_ids)],
            },
            VerificationResponse,
        )
        entity_coverage = response.comparison_entities
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
                for eid in c.evidence_ids:
                    selected = next(
                        p
                        for p in response.supported_pairs
                        if p.claim_id == c.claim_id and p.evidence_id == eid
                    )
                    item = by_id[eid]
                    start, end = selected.supporting_span_start, selected.supporting_span_end
                    # Invalid extraction falls back to the unchanged original quote.
                    valid_span = (
                        start is not None
                        and end is not None
                        and item.span_start <= start < end <= item.span_end
                        and bool(item.content[start:end].strip())
                    )
                    validated_pairs.append(
                        ClaimEvidencePair(
                            claim_id=c.claim_id,
                            evidence_id=eid,
                            supporting_span_start=start if valid_span else None,
                            supporting_span_end=end if valid_span else None,
                        )
                    )
    supported = {v.claim_id for v in verdicts if v.supported and not v.contradiction}
    covered = {c.aspect for c in claims if c.claim_id in supported}
    missing = list(dict.fromkeys([a for a in aspects if a not in covered] + model_missing))
    comparison_errors: list[str] = []
    validated_entities: list[ComparisonEntityCoverage] = []
    if comparison:
        validated_entities, comparison_errors = _comparison_coverage(
            entity_coverage, by_id, validated_pairs, comparison_entities or []
        )
        if comparison_errors:
            missing.append("comparison_entities")
    return CitationValidation(
        valid=bool(claims) and len(supported) == len(claims) and not missing,
        verdicts=verdicts,
        supported_pairs=validated_pairs,
        missing_citations=invalid,
        missing_aspects=missing,
        comparison_entities=validated_entities,
        comparison_errors=comparison_errors,
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
    # Before generation this is a usable-source gate, not a semantic comparison
    # verdict. One paper or one chunk may support several compared entities.
    if unique and plan.question_type == "comparison" and validation is not None:
        _, comparison_errors = _comparison_coverage(
            validation.comparison_entities,
            {item.evidence_id: item for item in unique.values()},
            validation.supported_pairs,
            plan.comparison_entities,
        )
        if comparison_errors:
            status = Sufficiency.PARTIAL
            reasons.extend(comparison_errors)
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
