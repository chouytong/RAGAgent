from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MetadataFilter(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    year_start: int | None = Field(default=None, ge=1000, le=2100)
    year_end: int | None = Field(default=None, ge=1000, le=2100)
    venues: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_years(self) -> "MetadataFilter":
        if self.year_start and self.year_end and self.year_start > self.year_end:
            raise ValueError("year_start exceeds year_end")
        return self


class QueryPlan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
    question_type: Literal["fact", "comparison", "synthesis", "filter"] = "fact"
    required_aspects: list[str] = Field(default_factory=list, max_length=12)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)


class PaperMetadata(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    arxiv_id: str | None = None


class EvidenceRecord(BaseModel):
    evidence_id: str
    paper: PaperMetadata
    chunk_id: str
    section_id: str
    section_path: str
    page_start: int
    page_end: int
    content: str
    quote: str
    span_start: int
    span_end: int
    scores: dict[str, float] = Field(default_factory=dict)


class Candidate(BaseModel):
    evidence: EvidenceRecord
    score: float


class SearchResult(BaseModel):
    dense: list[Candidate]
    lexical: list[Candidate]
    fused: list[Candidate]
    evidence: list[EvidenceRecord]


class Claim(BaseModel):
    claim_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    aspect: str = ""


class ClaimVerdict(BaseModel):
    claim_id: str
    supported: bool
    reason: str
    contradiction: bool = False


class CitationValidation(BaseModel):
    valid: bool
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    missing_citations: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)


class Sufficiency(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class EvidenceSufficiencyResult(BaseModel):
    status: Sufficiency
    reasons: list[str]
    evidence_count: int
    paper_count: int
    citation_coverage: float | None = None


class AnswerDraft(BaseModel):
    claims: list[Claim]
    limitations: list[str] = Field(default_factory=list)


class VerificationResponse(BaseModel):
    verdicts: list[ClaimVerdict]
    question_answered: bool = False
    missing_aspects: list[str] = Field(default_factory=list)


class QueryExpansion(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
