import pytest

from ragagent.domain.documents import Element, ParsedDocument
from ragagent.ingestion.chunker import StructureChunker


def element(text: str, page: int, path: str = "Methods", kind: str = "text") -> Element:
    return Element(
        content=text, page_start=page, page_end=page, section_path=[path], element_type=kind
    )


def test_sections_pages_types_overlap() -> None:
    doc = ParsedDocument(
        title="test",
        elements=[
            element("one two three four five six", 1),
            element("seven eight nine ten", 2),
            element("table results", 3, kind="table"),
            element("result text", 4, path="Results"),
        ],
    )
    chunks = StructureChunker(6, 2).chunk(doc)
    assert chunks[1].content.startswith("five six")
    assert chunks[1].page_start == 1 and chunks[1].page_end == 2
    assert chunks[-2].element_type == "table"
    assert chunks[-1].section_path == ["Results"]
    assert all(c.token_count <= 6 for c in chunks)


def test_empty_and_invalid_overlap() -> None:
    assert StructureChunker().chunk(ParsedDocument(title="empty", elements=[])) == []
    with pytest.raises(ValueError):
        StructureChunker(4, 4)


def test_chinese_tokens_and_exact_text() -> None:
    text = "科研方法。结果分析。"
    chunks = StructureChunker(5, 0).chunk(ParsedDocument(title="t", elements=[element(text, 1)]))
    assert "".join(c.content for c in chunks) == text


def test_same_heading_occurrences_are_not_merged() -> None:
    first = element("First experiment result.", 1)
    first.section_ids = ["heading:0"]
    second = element("Second experiment result.", 2)
    second.section_ids = ["heading:1"]
    chunks = StructureChunker().chunk(ParsedDocument(title="t", elements=[first, second]))
    assert [c.content for c in chunks] == [first.content, second.content]
    assert [c.section_ids for c in chunks] == [["heading:0"], ["heading:1"]]
    assert [c.page_start for c in chunks] == [1, 2]
