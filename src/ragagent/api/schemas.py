from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ragagent.domain.research import MetadataFilter
from ragagent.ingestion.arxiv import ARXIV_ID


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)


class ResearchRequest(BaseModel):
    research_question: str = Field(min_length=1, max_length=10000)
    filters: MetadataFilter = Field(default_factory=MetadataFilter)


class ArxivRequest(BaseModel):
    arxiv_id: str = Field(pattern=ARXIV_ID.pattern)


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
    status: str
    error_code: str | None
    chunk_count: int


class EntityAnnotation(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    entity_type: str = Field(pattern="^(dataset|method|metric|other)$")


class HealthResult(BaseModel):
    ok: bool


class ProviderTest(BaseModel):
    agent: str = Field(pattern="^(supervisor|retriever|analyst|reviewer)$")
