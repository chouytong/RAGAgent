"""Explicit synthetic corpus seed, never a scientific benchmark.

Uses configured real embeddings; LiteLLM embeddings may incur API charges.
"""

import asyncio
import hashlib
from pathlib import Path

from ragagent.db.models import Chunk, Paper, Section
from ragagent.db.session import session_factory
from ragagent.domain.documents import Element, ParsedDocument
from ragagent.ingestion.chunker import StructureChunker
from ragagent.runtime import make_embedder
from ragagent.settings import get_settings


def pdf(path: Path, text: str) -> None:
    lines = [text[i : i + 85] for i in range(0, len(text), 85)]
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = (
        "BT /F1 10 Tf 50 750 Td " + " ".join(f"({s}) Tj 0 -14 Td" for s in escaped) + " ET"
    ).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        f"{offset:010} 00000 n \n".encode() for offset in offsets[1:]
    )
    data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    path.write_bytes(data)


async def main() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    embedder = make_embedder(settings)
    text = (
        "DEMO ONLY. This is a synthetic corpus, not a published research result. "
        "Contrastive training is used for retrieval."
    )
    draft = StructureChunker().chunk(
        ParsedDocument(
            title="Synthetic Demo",
            elements=[
                Element(
                    section_path=["Methods"],
                    page_start=1,
                    page_end=1,
                    element_type="text",
                    content=text,
                )
            ],
        )
    )[0]
    vector = (await embedder.embed([draft.content]))[0]
    with session_factory()() as session:
        if session.get(Paper, "22222222-2222-4222-8222-222222222222"):
            print("Synthetic demo already exists; no data modified.")
            return
        path = settings.data_dir / "synthetic-demo.pdf"
        pdf(path, text)
        paper = Paper(
            id="22222222-2222-4222-8222-222222222222",
            title="DEMO ONLY synthetic paper",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            original_path=str(path),
            status="indexed",
            embedding_model=embedder.fingerprint,
        )
        session.add(paper)
        session.flush()
        section = Section(paper_id=paper.id, title="Methods", path="Methods", ordinal=0)
        session.add(section)
        session.flush()
        session.add(
            Chunk(
                id="11111111-1111-4111-8111-111111111111",
                paper_id=paper.id,
                section_id=section.id,
                section_path="Methods",
                page_start=1,
                page_end=1,
                element_type="text",
                content=draft.content,
                token_count=draft.token_count,
                ordinal=0,
                embedding=vector,
                metadata_json={"label_source": "synthetic", "warning": "NOT A BENCHMARK"},
            )
        )
        session.commit()
    print("DEMO ONLY / NOT A BENCHMARK / NOT MANUALLY ANNOTATED")


if __name__ == "__main__":
    asyncio.run(main())
