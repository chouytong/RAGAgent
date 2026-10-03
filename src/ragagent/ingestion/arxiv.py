import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")


@dataclass(frozen=True)
class ArxivMetadata:
    arxiv_id: str
    title: str
    authors: list[str]
    year: int
    source_url: str


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def download_arxiv(arxiv_id: str, path: Path, max_bytes: int) -> ArxivMetadata:
    if not ARXIV_ID.fullmatch(arxiv_id):
        raise ValueError("invalid_arxiv_id")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(
            "https://export.arxiv.org/api/query", params={"id_list": arxiv_id}
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entry = root.find("a:entry", ns)
        if entry is None or entry.findtext("a:title", default="", namespaces=ns) == "Error":
            raise ValueError("arxiv_not_found")
        title = " ".join(entry.findtext("a:title", default="", namespaces=ns).split())
        authors = [a.text or "" for a in entry.findall("a:author/a:name", ns)]
        year = int(entry.findtext("a:published", default="", namespaces=ns)[:4])
        async with client.stream("GET", f"https://arxiv.org/pdf/{arxiv_id}") as pdf:
            pdf.raise_for_status()
            size = 0
            with path.open("wb") as target:
                async for part in pdf.aiter_bytes():
                    size += len(part)
                    if size > max_bytes:
                        raise ValueError("pdf_too_large")
                    target.write(part)
        if path.read_bytes()[:5] != b"%PDF-":
            raise ValueError("invalid_pdf_response")
    return ArxivMetadata(arxiv_id, title, authors, year, f"https://arxiv.org/abs/{arxiv_id}")
