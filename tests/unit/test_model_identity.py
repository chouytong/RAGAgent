import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from ragagent.errors import ConfigurationError, ProviderError
from ragagent.providers.embedding import LocalEmbedder
from ragagent.providers.model_identity import LocalModelIdentity
from ragagent.settings import Settings


def test_hub_alias_resolves_once_and_every_load_uses_the_same_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revisions = ["a" * 40, "b" * 40]
    calls: list[tuple[str, str]] = []
    loads: list[str] = []

    def metadata(model: str, revision: str) -> Any:
        calls.append((model, revision))
        return SimpleNamespace(sha=revisions.pop(0))

    class Encoder:
        def __init__(self, model: str, revision: str) -> None:
            loads.append(revision)

        def encode(self, texts: list[str], **options: Any) -> Any:
            return SimpleNamespace(tolist=lambda: [[1.0, 0.0] for _ in texts])

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=lambda: SimpleNamespace(model_info=metadata)),
    )
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Encoder)
    )
    embedder = LocalEmbedder("org/model", 2, "main")
    assert calls == []  # Startup/configuration remains offline.
    original = embedder.fingerprint
    embedder._encode(["first"])
    embedder._encode(["second"])
    assert embedder.fingerprint == original
    assert embedder.revision == "a" * 40
    assert calls == [("org/model", "main")]
    assert loads == ["a" * 40]
    assert embedder.usage.cost == 0.0
    other = LocalEmbedder("org/model", 2, "main")
    assert other.fingerprint != original


def test_explicit_immutable_revision_does_not_resolve_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    identity = LocalModelIdentity("org/model", "A" * 40)
    assert identity.revision == "a" * 40
    assert identity.sdk_revision == "a" * 40


@pytest.mark.parametrize("sha", [None, "main", "a" * 12])
def test_unresolved_hub_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch, sha: str | None
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            HfApi=lambda: SimpleNamespace(
                model_info=lambda *args, **kwargs: SimpleNamespace(sha=sha)
            )
        ),
    )
    with pytest.raises(ProviderError, match="model_revision_resolution_failed"):
        _ = LocalEmbedder("org/model", 2).fingerprint


def test_local_artifact_changes_invalidate_identity_even_with_same_configured_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"hidden_size":2}')
    (model / "weights.safetensors").write_bytes(b"first-weights")
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    first = LocalEmbedder(str(model), 2, "operator-label")
    original = first.fingerprint
    assert first.revision.startswith("sha256:") and first.identity.sdk_revision is None
    assert LocalEmbedder(str(model), 2, "other-label").fingerprint == original
    (model / "weights.safetensors").write_bytes(b"updated-weights")
    with pytest.raises(ProviderError, match="local_model_artifacts_changed"):
        _ = first.fingerprint
    assert LocalEmbedder(str(model), 2, "operator-label").fingerprint != original


def test_missing_local_directory_cannot_be_interpreted_as_hub_alias(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="model_directory_missing"):
        _ = LocalModelIdentity(str(tmp_path / "missing"), None).revision


@pytest.mark.parametrize("target,overlap", [(32, 50), (50, 50), (100, 101)])
def test_invalid_chunk_overlap_rejected_at_configuration_load(target: int, overlap: int) -> None:
    with pytest.raises(ValidationError, match="chunk_overlap_tokens must be less"):
        Settings(_env_file=None, chunk_target_tokens=target, chunk_overlap_tokens=overlap)


def test_valid_overlap_and_evaluation_timeout_bounds() -> None:
    settings = Settings(_env_file=None, chunk_target_tokens=32, chunk_overlap_tokens=31)
    assert settings.evaluation_timeout_seconds == 7200
    for timeout in (1799, 86401):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, evaluation_timeout_seconds=timeout)
