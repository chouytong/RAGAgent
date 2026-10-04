import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ragagent.errors import ConfigurationError
from ragagent.providers.config import AgentModel, load_config

ROLES = ("supervisor", "retriever", "analyst", "reviewer")


def write_config(path: Path, model: str, api_base: str | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "agents": {
                    role: {"provider": "openai", "model": model, "api_base": api_base}
                    for role in ROLES
                }
            }
        )
    )


@pytest.mark.parametrize("variable", ["OPENAI_API_KEY", "DATABASE_URL", "ACCESS_TOKEN_MODEL"])
def test_model_templates_cannot_read_secret_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    path = tmp_path / "agents.yaml"
    write_config(path, f"${{{variable}}}")

    def unexpected_environment_read(name: str) -> str:
        raise AssertionError("secret variable must be rejected before environment lookup")

    monkeypatch.setattr("ragagent.providers.config.runtime_value", unexpected_environment_read)
    with pytest.raises(ConfigurationError, match="unsafe_model_environment"):
        load_config(path)


def test_model_environment_substitution_cannot_modify_yaml_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "agents.yaml"
    model = 'model-name"\napi_base: https://injected.invalid'
    monkeypatch.setenv("TEST_MODEL", model)
    write_config(path, "${TEST_MODEL}")
    config = load_config(path)
    assert config.agents.supervisor.model == model
    assert config.agents.supervisor.api_base is None


def test_default_model_and_standard_role_variables_remain_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "agents.yaml"
    monkeypatch.delenv("UNIT_TEST_ROLE_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    write_config(path, "${UNIT_TEST_ROLE_MODEL:-default-model}")
    assert load_config(path).agents.supervisor.model == "default-model"
    monkeypatch.setenv("SUPERVISOR_MODEL", "configured-model")
    write_config(path, "${SUPERVISOR_MODEL}")
    assert load_config(path).agents.supervisor.model == "configured-model"


@pytest.mark.parametrize("field", ["model", "api_base"])
def test_api_models_reject_unexpanded_placeholders(field: str) -> None:
    values = {"provider": "openai", "model": "fixture-model"}
    values[field] = "https://example.invalid/${OPENAI_API_KEY}"
    with pytest.raises(ValidationError, match="unresolved_provider_environment"):
        AgentModel.model_validate(values)
