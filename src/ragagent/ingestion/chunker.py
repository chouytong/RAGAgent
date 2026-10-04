import re
from dataclasses import dataclass

from ragagent.domain.documents import ChunkDraft, Element, ParsedDocument, SourceContext, SourceSpan

# Reproducible lexical tokens; not the tokenization of any chat/embedding model.
TOKEN = re.compile(
    r"[+\-\u2212]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][+\-\u2212]?\d+)?(?:%)?"
    r"|[\u3400-\u9fff]|[^\W\u3400-\u9fff]+|[^\w\s]",
    re.UNICODE,
)
TABLE_CAPTION = re.compile(r"^\s*(?:table\b|表\s*\d)", re.IGNORECASE)


def context_excerpt(element: Element, start: int = 0, end: int | None = None) -> SourceContext:
    content = element.content[start:end]
    assert element.source_id is not None
    return SourceContext(
        source_id=element.source_id,
        element_type=element.element_type,
        section_path=element.section_path,
        page_start=element.page_start,
        page_end=element.page_end,
        content=content,
        quote=content,
        span_start=0,
        span_end=len(content),
        source_offset=start,
    )


@dataclass(frozen=True)
class StructureChunker:
    target_tokens: int = 400
    overlap_tokens: int = 50

    def __post_init__(self) -> None:
        if self.target_tokens < 2 or not 0 <= self.overlap_tokens < self.target_tokens:
            raise ValueError("overlap must be smaller than target token count")

    def chunk(self, document: ParsedDocument) -> list[ChunkDraft]:
        result: list[ChunkDraft] = []
        group: list[Element] = []
        for index, element in enumerate(document.elements):
            if element.element_type in {"table", "formula"}:
                if group:
                    result.extend(self._split(group, len(result)))
                    group = []
                if element.content.strip():
                    if element.element_type == "table":
                        result.extend(self._table(document, index, len(result)))
                    else:
                        # Equations are atomic even when they exceed the lexical target.
                        result.append(self._atomic(element, len(result)))
                continue
            if group and (
                element.section_path != group[-1].section_path
                or element.section_ids != group[-1].section_ids
                or element.element_type != group[-1].element_type
            ):
                result.extend(self._split(group, len(result)))
                group = []
            if element.content.strip():
                group.append(element)
        if group:
            result.extend(self._split(group, len(result)))
        return result

    @staticmethod
    def _atomic(element: Element, ordinal: int) -> ChunkDraft:
        assert element.source_id is not None
        return ChunkDraft(
            **element.model_dump(),
            token_count=len(list(TOKEN.finditer(element.content))),
            ordinal=ordinal,
            source_spans=[
                SourceSpan(
                    source_id=element.source_id,
                    span_start=0,
                    span_end=len(element.content),
                    chunk_start=0,
                    chunk_end=len(element.content),
                )
            ],
        )

    def _table(self, document: ParsedDocument, index: int, ordinal: int) -> list[ChunkDraft]:
        table = document.elements[index]
        contexts: list[SourceContext] = []
        captions = [
            element
            for element in document.elements
            if element.source_id in table.related_source_ids and element.element_type == "caption"
        ]
        if not table.related_source_ids:
            # Conservative fallback for parsers without explicit table-caption links.
            adjacent_captions = []
            for neighbor in (index - 1, index + 1):
                if 0 <= neighbor < len(document.elements):
                    element = document.elements[neighbor]
                    if (
                        element.element_type == "caption"
                        and TABLE_CAPTION.match(element.content)
                        and element.section_path == table.section_path
                        and element.section_ids == table.section_ids
                        and element.page_start <= table.page_end
                        and element.page_end >= table.page_start
                    ):
                        adjacent_tables = sum(
                            document.elements[candidate].element_type == "table"
                            for candidate in (neighbor - 1, neighbor + 1)
                            if 0 <= candidate < len(document.elements)
                        )
                        if adjacent_tables == 1:
                            adjacent_captions.append(element)
            if len(adjacent_captions) == 1:
                captions.extend(adjacent_captions)
        contexts.extend(context_excerpt(caption) for caption in captions if caption.content.strip())
        lines = table.content.splitlines(keepends=True)
        separator = None
        for position, line in enumerate(lines):
            cells = line.strip().strip("|").split("|")
            if (
                "|" in line
                and cells
                and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells)
            ):
                separator = position
                break
        invalid_rows = separator is not None and any(
            line.strip() and not (line.strip().startswith("|") and line.strip().endswith("|"))
            for line in lines[separator + 1 :]
        )
        if separator is None or invalid_rows:
            # Unknown serialization: preserve the entire original table rather than guess rows.
            chunk = self._atomic(table, ordinal)
            chunk.source_context = contexts
            return [chunk]
        header_end = sum(len(line) for line in lines[: separator + 1])
        header = context_excerpt(table, 0, header_end)
        header.element_type = "table_header"
        contexts.insert(0, header)
        # Each range is contiguous original markdown; repeated headers are separate quotes.
        ranges: list[tuple[int, int]] = []
        start, offset, count = 0, header_end, len(list(TOKEN.finditer(table.content[:header_end])))
        for row in lines[separator + 1 :]:
            row_count = len(list(TOKEN.finditer(row)))
            if (
                row_count
                and offset > start
                and offset > header_end
                and count + row_count > self.target_tokens
            ):
                ranges.append((start, offset))
                start, count = offset, 0
            offset += len(row)
            count += row_count
        if offset > start:
            ranges.append((start, offset))
        chunks: list[ChunkDraft] = []
        assert table.source_id is not None
        for start, end in ranges:
            content = table.content[start:end]
            chunks.append(
                ChunkDraft(
                    **{**table.model_dump(), "content": content},
                    token_count=len(list(TOKEN.finditer(content))),
                    ordinal=ordinal + len(chunks),
                    source_context=contexts,
                    source_spans=[
                        SourceSpan(
                            source_id=table.source_id,
                            span_start=start,
                            span_end=end,
                            chunk_start=0,
                            chunk_end=len(content),
                        )
                    ],
                )
            )
        return chunks

    def _split(self, elements: list[Element], ordinal: int) -> list[ChunkDraft]:
        content = "\n".join(e.content for e in elements)
        spans: list[tuple[int, int, Element]] = []
        offset = 0
        for element in elements:
            spans.append((offset, offset + len(element.content), element))
            offset += len(element.content) + 1
        tokens = list(TOKEN.finditer(content))
        chunks: list[ChunkDraft] = []
        start = 0
        while start < len(tokens):
            end = min(start + self.target_tokens, len(tokens))
            # Prefer a sentence boundary near the target; never split by characters.
            if end < len(tokens):
                for boundary in range(end, start + int(self.target_tokens * 0.75), -1):
                    if tokens[boundary - 1].group() in {".", "!", "?", "。", ";"}:
                        end = boundary
                        break
            a, b = tokens[start].start(), tokens[end - 1].end()
            covered = [e for left, right, e in spans if left < b and right > a]
            source_spans = []
            for left, right, element in spans:
                if left < b and right > a:
                    assert element.source_id is not None
                    low, high = max(a, left), min(b, right)
                    source_spans.append(
                        SourceSpan(
                            source_id=element.source_id,
                            span_start=low - left,
                            span_end=high - left,
                            chunk_start=low - a,
                            chunk_end=high - a,
                        )
                    )
            chunks.append(
                ChunkDraft(
                    section_path=elements[0].section_path,
                    section_ids=elements[0].section_ids,
                    page_start=min(e.page_start for e in covered),
                    page_end=max(e.page_end for e in covered),
                    element_type=elements[0].element_type,
                    content=content[a:b],
                    token_count=end - start,
                    ordinal=ordinal + len(chunks),
                    source_spans=source_spans,
                )
            )
            if end == len(tokens):
                break
            start = max(start + 1, end - self.overlap_tokens)
        return chunks
