from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ragagent.domain.research import MetadataFilter


class EvaluationCase(BaseModel):
    id: str
    query: str = Field(min_length=1)
    question_type: Literal["fact", "comparison", "synthesis", "filter"]
    filters: MetadataFilter = Field(default_factory=MetadataFilter)
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    relevant_paper_ids: list[str] = Field(default_factory=list)
    expected_answer: str
    notes: str = ""
    required_aspects: list[str] = Field(default_factory=list)
    annotated_by: str | None = None
    annotated_at: str | None = None


class EvaluationDataset(BaseModel):
    dataset_id: str
    label_source: Literal["human", "synthetic", "unannotated"]
    description: str
    cases: list[EvaluationCase] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_annotations(self) -> "EvaluationDataset":
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate_case_ids")
        if self.label_source == "human" and any(
            not c.annotated_by or not c.annotated_at for c in self.cases
        ):
            raise ValueError("human_labels_require_annotation_provenance")
        return self

    def runnable(self) -> None:
        if self.label_source == "unannotated" or any(not c.relevant_chunk_ids for c in self.cases):
            raise ValueError("dataset_requires_relevance_labels")

    @property
    def warning(self) -> str:
        if self.label_source != "human":
            return "DEMO ONLY / NOT A BENCHMARK / NOT MANUALLY ANNOTATED"
        return "User-supplied human annotations; not independently verified by this software."


class EvaluationRequest(BaseModel):
    dataset: EvaluationDataset


class CitationPair(BaseModel):
    claim_id: str
    evidence_id: str


class RAGJudgment(BaseModel):
    supported_pairs: list[CitationPair]
    supported_claim_ids: list[str]
    answered_aspects: list[str]
    notes: list[str] = Field(default_factory=list)
