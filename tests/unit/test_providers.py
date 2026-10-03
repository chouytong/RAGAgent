from pathlib import Path

import pytest
from pydantic import ValidationError

from ragagent.errors import ConfigurationError
from ragagent.providers.config import AgentModel, load_config
from ragagent.providers.embedding import validate_vectors


def test_provider_names_and_no_key_values() -> None:
    assert (
        AgentModel(provider="deepseek", model="deepseek-chat").litellm_model
        == "deepseek/deepseek-chat"
    )
    with pytest.raises(ValidationError):
        AgentModel(provider="openai", model="m", api_key_env="secret value")
    with pytest.raises(ValidationError):
        AgentModel(provider="openai_compatible", model="m")


def test_environment_model_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        "agents:\n"
        + "".join(
            f"  {role}:\n    provider: openai\n    model: ${{TEST_MODEL}}\n"
            for role in ["supervisor", "retriever", "analyst", "reviewer"]
        )
    )
    monkeypatch.setenv("TEST_MODEL", "model-1")
    assert load_config(p).agents.supervisor.model == "model-1"
    monkeypatch.delenv("TEST_MODEL")
    with pytest.raises(ConfigurationError):
        load_config(p)


def test_embedding_validation() -> None:
    with pytest.raises(ValueError):
        validate_vectors([[float("nan")]], 1, 1)
    with pytest.raises(ValueError):
        validate_vectors([[0]], 1, 1)
