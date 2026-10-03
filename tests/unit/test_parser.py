import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from ragagent.ingestion.parser import DoclingParser


def test_docling_adapter_structure_captions_tables_pages_without_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def item(label: str, text: str, page: int, level: int = 1) -> Any:
        return SimpleNamespace(
            label=label, text=text, level=level, prov=[SimpleNamespace(page_no=page)]
        )

    table = item("table", "", 3)
    table.export_to_markdown = lambda doc: "| method | score |\n| A | 7 |"
    items = [
        item("section_header", "Methods", 1),
        item("section_header", "Training", 2, 2),
        item("text", "Contrastive training.", 2),
        table,
        item("caption", "Table 1: results", 3),
        item("caption", "Figure 1: architecture", 4),
        item("formula", "L = x + y", 4),
    ]

    class Document:
        def iterate_items(self, traverse_pictures: bool) -> list[tuple[Any, int]]:
            assert traverse_pictures
            return [(i, 0) for i in items]

    class Converter:
        def convert(self, path: Path) -> Any:
            return SimpleNamespace(document=Document())

    package = ModuleType("docling")
    package.__path__ = []
    module = ModuleType("docling.document_converter")
    module.DocumentConverter = Converter
    monkeypatch.setitem(sys.modules, "docling", package)
    monkeypatch.setitem(sys.modules, "docling.document_converter", module)
    parsed = DoclingParser().parse(Path("fixture.pdf"))
    assert parsed.elements[2].section_path == ["Methods", "Training"]
    assert parsed.elements[3].element_type == "table" and parsed.elements[3].page_start == 3
    assert parsed.elements[4].content == "Table 1: results"
    assert parsed.elements[5].content == "Figure 1: architecture"
    assert parsed.elements[6].content == "L = x + y"
