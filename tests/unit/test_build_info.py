import json
from pathlib import Path

import pytest

import ragagent.build_info as identity
from ragagent.evaluation.artifacts import source_commit


def test_packaged_provenance_needs_neither_git_binary_nor_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("RAGAGENT_SOURCE_COMMIT", raising=False)
    module = tmp_path / "src/ragagent/build_info.py"
    module.parent.mkdir(parents=True)
    monkeypatch.setattr(identity, "__file__", str(module))

    def missing(*args: object, **kwargs: object) -> str:
        raise FileNotFoundError("synthetic missing git")

    monkeypatch.setattr(identity.subprocess, "check_output", missing)
    assert identity.build_info()["source_commit"] == "unknown"
    assert source_commit() == "unknown"
    module.with_name("build-metadata.json").write_text(
        json.dumps(
            {"source_commit": "a" * 40, "built_at_utc": "2026-10-08T00:00:00Z", "dirty": False}
        )
    )
    assert source_commit() == "a" * 40
    assert identity.build_info()["dirty"] is False
    assert identity.build_info()["built_at_utc"] == "2026-10-08T00:00:00Z"
    monkeypatch.setenv("RAGAGENT_SOURCE_COMMIT", "b" * 40)
    assert source_commit() == "b" * 40


def test_build_metadata_rejects_invalid_shape_without_leaking_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = tmp_path / "src/ragagent/build_info.py"
    module.parent.mkdir(parents=True)
    monkeypatch.setattr(identity, "__file__", str(module))
    monkeypatch.setenv("RAGAGENT_SOURCE_COMMIT", "invalid-secret-value")
    monkeypatch.setenv("RAGAGENT_BUILD_TIME", "invalid-secret-value")

    def missing(*args: object, **kwargs: object) -> str:
        raise FileNotFoundError()

    monkeypatch.setattr(identity.subprocess, "check_output", missing)
    assert "invalid-secret" not in json.dumps(identity.build_info())
    for value in ["[]", "malformed", '{"dirty": "private"}']:
        module.with_name("build-metadata.json").write_text(value)
        assert identity.build_info()["source_commit"] == "unknown"
