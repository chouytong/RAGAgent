"""Bounded chat display metadata; source text remains in the lazy Run detail."""

from typing import Any

from pydantic import ValidationError

from ragagent.domain.research import CitationValidation, Claim, EvidenceRecord
from ragagent.retrieval.evidence import exact_span, parse_citations


def message_presentation(result: dict[str, Any]) -> dict[str, Any]:
    notes = [*result.get("limitations", [])]
    for analysis in result.get("analysis_results", []):
        if isinstance(analysis, dict):
            notes.extend(analysis.get("limitations", []))
    presentation: dict[str, Any] = {
        "limitations": list(dict.fromkeys(note[:240] for note in notes if isinstance(note, str)))[
            :8
        ],
        "citation_refs": [],
    }
    context = result.get("conversation_context")
    if isinstance(context, dict):
        presentation["conversation_context"] = {
            key: value[:300]
            for key in ("original_query", "contextualized_query")
            if isinstance(value := context.get(key), str)
        }
    review = result.get("review_result")
    raw_validation = result.get("citation_validation")
    if isinstance(review, dict):
        raw_validation = review.get("validation") if review.get("decision") == "PASS" else None
    try:
        validation = CitationValidation.model_validate(raw_validation)
        claims = [Claim.model_validate(claim) for claim in result.get("claims", [])]
        for analysis in result.get("analysis_results", []):
            for key in ("claims", "methods", "datasets", "metrics", "contradictions"):
                claims.extend(Claim.model_validate(claim) for claim in analysis.get(key, []))
        pool = [
            EvidenceRecord.model_validate(item)
            for item in result.get("evidence_pool", result.get("reranked_evidence", []))
        ]
    except (ValidationError, TypeError, AttributeError):
        return presentation  # Legacy records remain inspectable through Run detail.
    if not validation.valid:
        return presentation
    supported = {
        verdict.claim_id
        for verdict in validation.verdicts
        if verdict.supported and not verdict.contradiction
    }
    declared = {
        (claim.claim_id, evidence_id)
        for claim in claims
        if claim.claim_id in supported
        for evidence_id in claim.evidence_ids
    }
    cited = {
        pair.evidence_id
        for pair in validation.supported_pairs
        if (pair.claim_id, pair.evidence_id) in declared
    }.intersection(parse_citations(str(result.get("answer") or result.get("draft_report") or "")))
    references: dict[str, dict[str, Any]] = {}
    for item in pool:
        if item.evidence_id in cited and exact_span(item):
            references.setdefault(
                item.evidence_id,
                {
                    "evidence_id": item.evidence_id,
                    "paper_id": item.paper.paper_id,
                    "title": item.paper.title[:200],
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                },
            )
    presentation["citation_refs"] = list(references.values())[:16]
    return presentation
