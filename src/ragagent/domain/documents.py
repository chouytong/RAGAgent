from pydantic import BaseModel, Field, model_validator


class Element(BaseModel):
    section_path: list[str] = Field(min_length=1)
    # Node identities distinguish repeated headings; paths remain human-readable labels.
    section_ids: list[str] = Field(default_factory=list)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    element_type: str
    content: str

    @model_validator(mode="after")
    def valid_section_ids(self) -> "Element":
        if self.section_ids and len(self.section_ids) != len(self.section_path):
            raise ValueError("section_ids must match section_path depth")
        return self


class ParsedDocument(BaseModel):
    title: str
    elements: list[Element]


class ChunkDraft(Element):
    token_count: int = Field(ge=1)
    ordinal: int = Field(ge=0)
