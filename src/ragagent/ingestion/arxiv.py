import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v[1-9]\d*)?")


@dataclass(frozen=True)
class ArxivMetadata:
    arxiv_id: str
    title: str
    authors: list[str]
    year: int
    source_url: str
    arxiv_family_id: str
    arxiv_version: int


def arxiv_identity(arxiv_id: str) -> tuple[str, int | None]:
    """Separate an arXiv family from an optional immutable source version."""
    if not ARXIV_ID.fullmatch(arxiv_id):
        raise ValueError("invalid_arxiv_id")
    match = re.search(r"v([1-9]\d*)$", arxiv_id)
    if match is None:
        return arxiv_id, None
    return arxiv_id[: match.start()], int(match.group(1))


def resolved_arxiv_id(entry_id: str, requested_id: str) -> tuple[str, str, int]:
    """Freeze the official Atom identity before downloading any PDF bytes."""
    url = urlparse(entry_id)
    if url.scheme not in {"http", "https"} or url.hostname != "arxiv.org":
        raise ValueError("invalid_arxiv_entry_identity")
    if not url.path.startswith("/abs/") or url.query or url.fragment:
        raise ValueError("invalid_arxiv_entry_identity")
    resolved_id = url.path.removeprefix("/abs/")
    family, version = arxiv_identity(resolved_id)
    requested_family, requested_version = arxiv_identity(requested_id)
    if (
        version is None
        or family != requested_family
        or (requested_version is not None and version != requested_version)
    ):
        raise ValueError("arxiv_version_mismatch")
    return resolved_id, family, version


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def download_arxiv(arxiv_id: str, path: Path, max_bytes: int) -> ArxivMetadata:
    arxiv_identity(arxiv_id)
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
        resolved_id, family, version = resolved_arxiv_id(
            entry.findtext("a:id", default="", namespaces=ns), arxiv_id
        )
        title = " ".join(entry.findtext("a:title", default="", namespaces=ns).split())
        authors = [a.text or "" for a in entry.findall("a:author/a:name", ns)]
        year = int(entry.findtext("a:published", default="", namespaces=ns)[:4])
        async with client.stream("GET", f"https://arxiv.org/pdf/{resolved_id}") as pdf:
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
    return ArxivMetadata(
        resolved_id, title, authors, year, f"https://arxiv.org/abs/{resolved_id}", family, version
    )
