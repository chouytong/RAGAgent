import pytest

from ragagent.domain.documents import Element, ParsedDocument, contextual_text
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


@pytest.mark.parametrize(
    "number",
    [
        "0.95",
        "-0.95",
        "−0.95",
        "1.23e-5",
        "99.95%",
        "1,234.56",
        ".05",
        "-.05",
        "−.05",
        ".05e-3",
        "1.23e−5",
        ".05%",
    ],
)
def test_numeric_values_are_atomic_at_every_nearby_boundary(number: str) -> None:
    text = " ".join(["padding"] * 29) + f" accuracy = {number} confidence."
    document = ParsedDocument(title="numeric", elements=[element(text, 1)])
    for target in range(30, 38):
        chunks = StructureChunker(target, 0).chunk(document)
        assert sum(number in chunk.content for chunk in chunks) == 1
        for chunk in chunks:
            for span in chunk.source_spans:
                source = document.elements[0]
                assert (
                    source.content[span.span_start : span.span_end]
                    == chunk.content[span.chunk_start : span.chunk_end]
                )


def test_long_equations_remain_one_exact_original_element() -> None:
    formula = r"L = \sum_{i=1}^{1000} \log (p_i) + 0.95 \times \alpha"
    document = ParsedDocument(title="equation", elements=[element(formula, 1, kind="formula")])
    chunks = StructureChunker(8, 2).chunk(document)
    assert len(chunks) == 1
    assert chunks[0].content == formula
    assert chunks[0].token_count > 8


def test_table_chunks_keep_complete_rows_and_separate_exact_header_caption_sources() -> None:
    header = "| Method | Accuracy (%) |\n| --- | --- |\n"
    rows = [f"| Method {index} | {90 + index}.95 |\n" for index in range(8)]
    table = element(header + "".join(rows), 3, kind="table")
    table.source_id = "#/tables/0"
    table.related_source_ids = ["#/texts/1"]
    caption = element(
        "Table 3: accuracy on the held-out set, reported as percent.", 3, kind="caption"
    )
    caption.source_id = "#/texts/1"
    document = ParsedDocument(title="table", elements=[table, caption])
    chunks = [c for c in StructureChunker(35, 5).chunk(document) if c.element_type == "table"]
    assert len(chunks) > 1
    assert "".join(chunk.content for chunk in chunks) == table.content
    assert all(sum(row in chunk.content for chunk in chunks) == 1 for row in rows)
    assert all(header not in chunk.content for chunk in chunks[1:])
    sources = {element.source_id: element.content for element in document.elements}
    for chunk in chunks:
        assert [context.element_type for context in chunk.source_context] == [
            "table_header",
            "caption",
        ]
        for context in chunk.source_context:
            start = context.source_offset + context.span_start
            end = context.source_offset + context.span_end
            assert sources[context.source_id][start:end] == context.quote
        span = chunk.source_spans[0]
        assert table.content[span.span_start : span.span_end] == chunk.content
        model_input = contextual_text(chunk.content, chunk.source_context)
        assert header in model_input and caption.content in model_input
        assert model_input.count(header) == 1


def test_tables_without_recognized_rows_are_atomic_and_do_not_merge() -> None:
    first = element("Unstructured " * 40, 1, kind="table")
    second = element("Another table", 1, kind="table")
    chunks = StructureChunker(8, 2).chunk(ParsedDocument(title="tables", elements=[first, second]))
    assert [chunk.content for chunk in chunks] == [first.content, second.content]


def test_adjacent_figure_caption_is_not_attached_to_table() -> None:
    table = element("| X |\n| --- |\n| 0.95 |\n", 1, kind="table")
    figure = element("Figure 1: unrelated architecture.", 1, kind="caption")
    chunks = StructureChunker(8, 2).chunk(ParsedDocument(title="t", elements=[table, figure]))
    assert all(context.element_type != "caption" for context in chunks[0].source_context)


def test_ambiguous_caption_between_tables_is_not_assigned_to_either() -> None:
    first = element("| X |\n| --- |\n| 0.95 |\n", 1, kind="table")
    caption = element("Table 1: metric in percent.", 1, kind="caption")
    second = element("| X |\n| --- |\n| 0.85 |\n", 1, kind="table")
    document = ParsedDocument(title="t", elements=[first, caption, second])
    tables = [c for c in StructureChunker(8, 2).chunk(document) if c.element_type == "table"]
    assert len(tables) == 2
    assert all(context.element_type != "caption" for c in tables for context in c.source_context)


def test_units_note_outside_recognized_table_rows_preserves_atomic_original() -> None:
    text = (
        "| Method | Metric |\n| --- | --- |\n| A | 0.95 |\nUnits: all measurements are percent.\n"
    )
    document = ParsedDocument(title="t", elements=[element(text, 1, kind="table")])
    chunks = StructureChunker(8, 2).chunk(document)
    assert len(chunks) == 1 and chunks[0].content == text
