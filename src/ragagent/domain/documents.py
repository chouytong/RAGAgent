from pydantic import BaseModel, Field


class Element(BaseModel):
    section_path: list[str]
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    element_type: str
    content: str


class ParsedDocument(BaseModel):
    title: str
    elements: list[Element]


class ChunkDraft(Element):
    token_count: int = Field(ge=1)
    ordinal: int = Field(ge=0)
