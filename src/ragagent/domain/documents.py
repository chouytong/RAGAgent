from pydantic import BaseModel, Field, model_validator


class Element(BaseModel):
    source_id: str | None = None
    # Docling caption references remain distinct sources, rather than appended text.
    related_source_ids: list[str] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def identify_sources(self) -> "ParsedDocument":
        for ordinal, element in enumerate(self.elements):
            if element.source_id is None:
                element.source_id = f"element:{ordinal}"
        identifiers = [element.source_id for element in self.elements]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate_source_element_id")
        return self


class SourceContext(BaseModel):
    """An original auxiliary excerpt with its own source and character offsets."""

    source_id: str
    element_type: str
    section_path: list[str] = Field(min_length=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    # content is only this original excerpt, never the whole table duplicated per row.
    content: str
    quote: str
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    # Offset of content within the parsed source Element.content.
    source_offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_quote(self) -> "SourceContext":
        if not (
            self.page_end >= self.page_start
            and 0 <= self.span_start < self.span_end <= len(self.content)
            and self.content[self.span_start : self.span_end] == self.quote
        ):
            raise ValueError("invalid_source_context_span")
        return self


class SourceSpan(BaseModel):
    source_id: str
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    chunk_start: int = Field(ge=0)
    chunk_end: int = Field(gt=0)

    @model_validator(mode="after")
    def matching_lengths(self) -> "SourceSpan":
        if (
            self.span_end <= self.span_start
            or self.chunk_end <= self.chunk_start
            or self.span_end - self.span_start != self.chunk_end - self.chunk_start
        ):
            raise ValueError("invalid_source_span")
        return self


class ChunkDraft(Element):
    token_count: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    source_context: list[SourceContext] = Field(default_factory=list)


def contextual_text(content: str, source_context: list[SourceContext]) -> str:
    """Model input may include auxiliary quotes; stored chunk text stays original."""
    context = list(
        dict.fromkeys(item.quote for item in source_context if item.quote not in content)
    )
    return "\n".join([*context, content])
