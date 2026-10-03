from typing import TypedDict

from pydantic import BaseModel, Field, model_validator

from ragagent.db.models import new_id
from ragagent.domain.research import (
    Candidate,
    CitationValidation,
    Claim,
    EvidenceRecord,
    EvidenceSufficiencyResult,
    MetadataFilter,
    QueryPlan,
)


class RAGState(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    query_type: str = "fact"
    query_plan: QueryPlan | None = None
    filters: MetadataFilter = Field(default_factory=MetadataFilter)
    retrieved_candidates: list[Candidate] = Field(default_factory=list)
    reranked_evidence: list[EvidenceRecord] = Field(default_factory=list)
    answer: str = ""
    claims: list[Claim] = Field(default_factory=list)
    citation_validation: CitationValidation | None = None
    sufficiency: EvidenceSufficiencyResult | None = None
    retrieval_attempt: int = 0
    status: str = "planning"
    errors: list[str] = Field(default_factory=list)
    trace_id: str = Field(default_factory=new_id)


class RAGUpdate(TypedDict, total=False):
    query_type: str
    query_plan: QueryPlan
    retrieved_candidates: list[Candidate]
    reranked_evidence: list[EvidenceRecord]
    answer: str
    claims: list[Claim]
    citation_validation: CitationValidation | None
    sufficiency: EvidenceSufficiencyResult
    retrieval_attempt: int
    status: str
    errors: list[str]


class SubTask(BaseModel):
    task_id: str
    question: str
    aspect: str
    queries: list[str] = Field(min_length=1, max_length=6)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)


class ResearchPlan(BaseModel):
    objective: str
    required_aspects: list[str] = Field(min_length=1, max_length=12)
    subtasks: list[SubTask] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_tasks(self) -> "ResearchPlan":
        ids = [s.task_id for s in self.subtasks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_subtask_ids")
        if any(s.aspect not in self.required_aspects for s in self.subtasks):
            raise ValueError("subtask_aspect_not_in_plan")
        return self


class AnalysisResult(BaseModel):
    claims: list[Claim]
    methods: list[Claim] = Field(default_factory=list)
    datasets: list[Claim] = Field(default_factory=list)
    metrics: list[Claim] = Field(default_factory=list)
    contradictions: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    def factual_claims(self) -> list[Claim]:
        by_id: dict[str, Claim] = {}
        for claim in (
            self.claims + self.methods + self.datasets + self.metrics + self.contradictions
        ):
            if claim.claim_id in by_id and by_id[claim.claim_id] != claim:
                raise ValueError("conflicting_claim_id")
            by_id[claim.claim_id] = claim
        return list(by_id.values())

    @model_validator(mode="after")
    def consistent_claim_ids(self) -> "AnalysisResult":
        self.factual_claims()
        return self


class ReviewResult(BaseModel):
    decision: str = Field(pattern="^(PASS|NEED_MORE_EVIDENCE|NEED_REVISION)$")
    validation: CitationValidation
    issues: list[str] = Field(default_factory=list)


class MultiAgentState(BaseModel):
    research_question: str = Field(min_length=1, max_length=10000)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)
    research_plan: ResearchPlan | None = None
    subtasks: list[SubTask] = Field(default_factory=list)
    current_tasks: list[str] = Field(default_factory=list)
    completed_tasks: list[str] = Field(default_factory=list)
    evidence_pool: list[EvidenceRecord] = Field(default_factory=list)
    analysis_results: list[AnalysisResult] = Field(default_factory=list)
    draft_report: str = ""
    review_result: ReviewResult | None = None
    revision_count: int = 0
    retrieval_count: int = 0
    iteration: int = 0
    status: str = "planning"
    errors: list[str] = Field(default_factory=list)
    trace_id: str = Field(default_factory=new_id)
    trace_metadata: dict[str, str] = Field(default_factory=dict)


class ResearchUpdate(TypedDict, total=False):
    research_plan: ResearchPlan
    subtasks: list[SubTask]
    current_tasks: list[str]
    completed_tasks: list[str]
    evidence_pool: list[EvidenceRecord]
    analysis_results: list[AnalysisResult]
    draft_report: str
    review_result: ReviewResult
    revision_count: int
    retrieval_count: int
    iteration: int
    status: str
    errors: list[str]
