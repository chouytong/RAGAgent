"""Explicit labels for conversation evaluation; history is never a relevance label."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from ragagent.domain.conversation_context import ContextMessage, StructuredMemory
from ragagent.domain.privacy import reject_credentials as _safe_dataset_strings
from ragagent.evaluation.schema import EvaluationCase, EvaluationDataset
from ragagent.evaluation.validation import validate_references

ConversationDimension = Literal[
    "context_resolution", "evidence_grounding", "memory_isolation", "long_summary"
]
DIMENSIONS = ("context_resolution", "evidence_grounding", "memory_isolation", "long_summary")


class ConversationEvaluationTurn(EvaluationCase):
    query: str = Field(min_length=1, max_length=10000)
    expected_context_terms: list[str] = Field(default_factory=list, max_length=30)
    forbidden_answer_fragments: list[str] = Field(default_factory=list, max_length=30)
    expects_summary: bool = False

    @model_validator(mode="after")
    def nonempty_labels(self) -> "ConversationEvaluationTurn":
        if any(
            not text.strip()
            for text in [*self.expected_context_terms, *self.forbidden_answer_fragments]
        ):
            raise ValueError("conversation_evaluation_empty_label")
        return self


class ConversationEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    mode: Literal["rag", "research"] = "rag"
    dimensions: list[ConversationDimension] = Field(min_length=1, max_length=4)
    seed_messages: list[ContextMessage] = Field(default_factory=list, max_length=500)
    memories: list[StructuredMemory] = Field(default_factory=list, max_length=100)
    turns: list[ConversationEvaluationTurn] = Field(min_length=1, max_length=30)
    notes: str = ""

    @model_validator(mode="after")
    def unique_sources(self) -> "ConversationEvaluationCase":
        groups: list[list[str]] = [
            [turn.id for turn in self.turns],
            [message.id for message in self.seed_messages],
            [str(message.ordinal) for message in self.seed_messages],
            [memory.id for memory in self.memories],
            list(self.dimensions),
        ]
        if any(len(set(group)) != len(group) for group in groups):
            raise ValueError("conversation_evaluation_duplicate_ids")
        if any(len(message.content) > 20000 for message in self.seed_messages) or any(
            len(memory.content) > 4000 for memory in self.memories
        ):
            raise ValueError("conversation_evaluation_context_too_large")
        return self


class ConversationEvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=200)
    label_source: Literal["human", "synthetic", "unannotated"]
    description: str
    cases: list[ConversationEvaluationCase] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def annotations(self) -> "ConversationEvaluationDataset":
        # Include history, explicit memory, filters, labels and annotation notes.
        # Checking only the later ContextBuilder would allow API enqueue to write
        # the original credential-bearing request and initial artifacts first.
        _safe_dataset_strings(self.model_dump(mode="json"))
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate_case_ids")
        if self.label_source == "human" and any(
            not turn.annotated_by or not turn.annotated_at
            for case in self.cases
            for turn in case.turns
        ):
            raise ValueError("human_labels_require_annotation_provenance")
        return self

    def generation_dataset(self) -> EvaluationDataset:
        """Reuse existing answer/relevance validation, without changing its contracts."""
        return EvaluationDataset(
            dataset_id=self.dataset_id,
            label_source=self.label_source,
            description=self.description,
            cases=[
                EvaluationCase.model_validate({**turn.model_dump(), "id": f"{case.id}/{turn.id}"})
                for case in self.cases
                for turn in case.turns
            ],
        )

    def runnable(self) -> None:
        # Preserve this boundary for direct callers that mutate validated models.
        _safe_dataset_strings(self.model_dump(mode="json"))
        self.generation_dataset().generation_runnable()
        for case in self.cases:
            if "context_resolution" in case.dimensions and not any(
                turn.expected_context_terms for turn in case.turns
            ):
                raise ValueError("conversation_evaluation_requires_context_labels")
            if "memory_isolation" in case.dimensions and (
                not (case.seed_messages or case.memories)
                or not any(turn.forbidden_answer_fragments for turn in case.turns)
            ):
                raise ValueError("conversation_evaluation_requires_isolation_labels")
            if "long_summary" in case.dimensions and not any(
                turn.expects_summary and turn.expected_context_terms for turn in case.turns
            ):
                raise ValueError("conversation_evaluation_requires_summary_labels")

    @property
    def warning(self) -> str:
        return self.generation_dataset().warning


class ConversationEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: ConversationEvaluationDataset
    resume_run_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )


def validate_conversation_references(
    dataset: ConversationEvaluationDataset, session: Session
) -> None:
    dataset.runnable()
    validate_references(dataset.generation_dataset(), session)
