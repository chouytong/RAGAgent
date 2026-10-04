from pathlib import Path
from typing import Protocol

from ragagent.domain.documents import Element, ParsedDocument
from ragagent.errors import ParsingError


class Parser(Protocol):
    def parse(self, path: Path) -> ParsedDocument: ...


class DoclingParser:
    def parse(self, path: Path) -> ParsedDocument:
        # Lazy import: core tests do not install or download layout/OCR models.
        try:
            from docling.document_converter import DocumentConverter

            document = DocumentConverter().convert(path).document
            elements: list[Element] = []
            headings: list[str] = []
            section_ids: list[str] = []
            heading_ordinal = 0
            for item, _level in document.iterate_items(traverse_pictures=True):
                label = str(getattr(item.label, "value", item.label))
                if label == "section_header":
                    depth = max(1, int(getattr(item, "level", 1)))
                    headings = headings[: depth - 1] + [item.text]
                    section_ids = section_ids[: depth - 1] + [f"heading:{heading_ordinal}"]
                    heading_ordinal += 1
                provenance = getattr(item, "prov", [])
                if not provenance:
                    continue
                if label == "table":
                    text = item.export_to_markdown(doc=document)
                else:
                    text = getattr(item, "text", "")
                if not text.strip():
                    continue
                pages = [p.page_no for p in provenance]
                elements.append(
                    Element(
                        section_path=headings or ["Preamble"],
                        section_ids=section_ids or ["preamble"],
                        page_start=min(pages),
                        page_end=max(pages),
                        element_type=label,
                        content=text,
                        source_id=getattr(item, "self_ref", None),
                        related_source_ids=[
                            ref.cref
                            for ref in getattr(item, "captions", [])
                            if hasattr(ref, "cref")
                        ],
                    )
                )
            if not elements:
                raise ParsingError("no_parseable_elements")
            return ParsedDocument(title=path.stem, elements=elements)
        except ParsingError:
            raise
        except Exception as exc:
            raise ParsingError("docling_conversion_failed") from exc
