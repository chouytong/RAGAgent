"""Freeze model artifacts before their identity is used for vectors or inference."""

import hashlib
import re
import threading
from pathlib import Path

from ragagent.errors import ConfigurationError, ProviderError

IMMUTABLE_REVISION = re.compile(r"[0-9a-fA-F]{40}\Z")


def resolve_hub_revision(model: str, revision: str | None) -> str:
    """Resolve a Hub branch/tag once, without downloading or trusting model code."""
    if revision is not None and IMMUTABLE_REVISION.fullmatch(revision):
        return revision.lower()
    from huggingface_hub import HfApi

    try:
        resolved = HfApi().model_info(model, revision=revision or "main").sha
        if not isinstance(resolved, str) or not IMMUTABLE_REVISION.fullmatch(resolved):
            raise ValueError("immutable_revision_missing")
        return resolved.lower()
    except Exception:
        # Neither remote errors nor configured paths may leak into API/log output.
        raise ProviderError("model_revision_resolution_failed") from None


def directory_digest(directory: Path) -> str:
    """Include weights, tokenizer, configs and submodels in a deterministic digest."""
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and not {".git", "__pycache__"}.intersection(path.relative_to(directory).parts)
    )
    if not files:
        raise ConfigurationError("model_directory_empty")
    try:
        for path in files:
            name = path.relative_to(directory).as_posix().encode()
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            file_digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    file_digest.update(block)
            digest.update(file_digest.digest())
    except OSError:
        raise ProviderError("local_model_identity_failed") from None
    return digest.hexdigest()


class LocalModelIdentity:
    """Lazy identity: constructing adapters never needs model files or network."""

    def __init__(self, model: str, revision: str | None) -> None:
        self.model = model
        self.requested_revision = revision
        self._revision: str | None = None
        self._directory: Path | None = None
        self._lock = threading.Lock()

    @property
    def revision(self) -> str:
        with self._lock:
            if self._revision is None:
                directory = Path(self.model).expanduser()
                if directory.is_dir():
                    self._directory = directory.resolve()
                    self._revision = "sha256:" + directory_digest(self._directory)
                elif directory.is_absolute() or self.model.startswith(("./", "../", "~")):
                    raise ConfigurationError("model_directory_missing")
                else:
                    self._revision = resolve_hub_revision(self.model, self.requested_revision)
            return self._revision

    @property
    def sdk_revision(self) -> str | None:
        resolved = self.revision
        self.verify_directory()
        return None if self._directory is not None else resolved

    def verify_directory(self) -> None:
        if self._directory is not None:
            current = "sha256:" + directory_digest(self._directory)
            if current != self._revision:
                raise ProviderError("local_model_artifacts_changed")
