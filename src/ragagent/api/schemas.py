from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ragagent.domain.privacy import SensitiveInput
from ragagent.domain.research import MetadataFilter, SearchResult
from ragagent.ingestion.arxiv import ARXIV_ID


class QueryRequest(SensitiveInput):
    query: str = Field(min_length=1, max_length=10000)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)


class SearchResponse(SearchResult):
    usage: dict[str, dict[str, Any]]
    usage_scope: Literal["current_request"] = "current_request"


class ResearchRequest(SensitiveInput):
    research_question: str = Field(min_length=1, max_length=10000)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)


class ArxivRequest(BaseModel):
    arxiv_id: str = Field(pattern="^" + ARXIV_ID.pattern + "$")


class RunResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    kind: str
    status: str
    trace_id: str
    error_code: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime


class PaperResponse(BaseModel):
    id: str
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    arxiv_id: str | None
    arxiv_family_id: str | None = None
    arxiv_version: int | None = None
    source_status: Literal["unknown", "active", "withdrawn", "retracted"] = "unknown"
    status: str
    error_code: str | None
    chunk_count: int


class EntityAnnotation(SensitiveInput):
    name: str = Field(min_length=1, max_length=256)
    entity_type: str = Field(pattern="^(dataset|method|metric|other)$")


class HealthResult(BaseModel):
    ok: bool


class ProviderTest(BaseModel):
    agent: str = Field(pattern="^(supervisor|retriever|analyst|reviewer)$")


class PaperPatch(SensitiveInput):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    authors: list[str] | None = Field(default=None, max_length=100)
    year: int | None = Field(default=None, ge=1000, le=2100)
    venue: str | None = Field(default=None, max_length=256)
    source_status: Literal["unknown", "active", "withdrawn", "retracted"] | None = None
