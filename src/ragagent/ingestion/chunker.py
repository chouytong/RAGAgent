import re
from dataclasses import dataclass

from ragagent.domain.documents import ChunkDraft, Element, ParsedDocument

# Reproducible lexical tokens; not the tokenization of any chat/embedding model.
TOKEN = re.compile(r"[\u3400-\u9fff]|[^\W\u3400-\u9fff]+|[^\w\s]", re.UNICODE)


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
        for element in document.elements:
            if group and (
                element.section_path != group[-1].section_path
                or element.element_type != group[-1].element_type
            ):
                result.extend(self._split(group, len(result)))
                group = []
            if element.content.strip():
                group.append(element)
        if group:
            result.extend(self._split(group, len(result)))
        return result

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
            chunks.append(
                ChunkDraft(
                    section_path=elements[0].section_path,
                    page_start=min(e.page_start for e in covered),
                    page_end=max(e.page_end for e in covered),
                    element_type=elements[0].element_type,
                    content=content[a:b],
                    token_count=end - start,
                    ordinal=ordinal + len(chunks),
                )
            )
            if end == len(tokens):
                break
            start = max(start + 1, end - self.overlap_tokens)
        return chunks
