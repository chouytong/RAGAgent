from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from ragagent.api.schemas import ArxivRequest, PaperPatch
from ragagent.ingestion.arxiv import arxiv_identity, download_arxiv, resolved_arxiv_id


def atom(entry_id: str) -> str:
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f"<id>{entry_id}</id><title> A scientific paper </title>"
        "<author><name>Alice</name></author><published>2024-08-10T00:00:00Z</published>"
        "</entry></feed>"
    )


@pytest.mark.parametrize("requested", ["2408.09869", "2408.09869v2"])
async def test_pdf_download_uses_atom_version(
    requested: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "export.arxiv.org":
            assert request.url.params["id_list"] == requested
            return httpx.Response(200, text=atom("http://arxiv.org/abs/2408.09869v2"))
        assert str(request.url) == "https://arxiv.org/pdf/2408.09869v2"
        return httpx.Response(200, content=b"%PDF-1.4 deterministic fixture")

    client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=transport, **kw))
    path = tmp_path / "paper.pdf"
    metadata = await download_arxiv(requested, path, 1024)
    assert metadata.arxiv_id == "2408.09869v2"
    assert metadata.arxiv_family_id == "2408.09869" and metadata.arxiv_version == 2
    assert metadata.source_url == "https://arxiv.org/abs/2408.09869v2"
    assert path.read_bytes() == b"%PDF-1.4 deterministic fixture" and len(requests) == 2


@pytest.mark.parametrize(
    "entry_id,requested",
    [
        ("http://arxiv.org/abs/2408.09869", "2408.09869"),
        ("http://arxiv.org/abs/2408.09869v2", "2408.09869v1"),
        ("http://arxiv.org/abs/2408.12345v1", "2408.09869"),
        ("https://example.org/abs/2408.09869v2", "2408.09869"),
        ("http://arxiv.org/abs/2408.09869v2?version=3", "2408.09869"),
    ],
)
def test_atom_cannot_change_requested_identity(entry_id: str, requested: str) -> None:
    with pytest.raises(ValueError):
        resolved_arxiv_id(entry_id, requested)


async def test_mismatched_atom_never_downloads_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "export.arxiv.org"
        return httpx.Response(200, text=atom("http://arxiv.org/abs/2408.09869v2"))

    client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=transport, **kw))
    # Avoid retry delays when checking a deterministic upstream identity error.
    with pytest.raises(ValueError, match="arxiv_version_mismatch"):
        await download_arxiv.__wrapped__("2408.09869v1", tmp_path / "paper.pdf", 1024)
    assert not (tmp_path / "paper.pdf").exists()


def test_source_annotations_and_legacy_id_validation() -> None:
    assert arxiv_identity("hep-ex/9608001v3") == ("hep-ex/9608001", 3)
    assert arxiv_identity("2408.09869") == ("2408.09869", None)
    assert PaperPatch(source_status="withdrawn").source_status == "withdrawn"
    with pytest.raises(ValidationError):
        PaperPatch.model_validate({"source_status": "verified"})
    with pytest.raises(ValidationError):
        ArxivRequest(arxiv_id="2408.09869v0")
