from types import SimpleNamespace
from typing import Any

import pytest

from ragagent.api.schemas import HealthResult
from ragagent.errors import ProviderError
from ragagent.providers.chat import LiteLLMProvider
from ragagent.providers.config import AgentModel


async def test_actual_sdk_adapter_contract_with_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    seen: dict[str, Any] = {}

    async def completion(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=4),
        )

    monkeypatch.setenv("UNIT_TEST_PROVIDER_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(litellm, "acompletion", completion)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kwargs: 0.0)
    provider = LiteLLMProvider(
        AgentModel(provider="openai", model="test", api_key_env="UNIT_TEST_PROVIDER_KEY")
    )
    result = await provider.complete("test", {}, HealthResult)
    assert result.ok and seen["model"] == "openai/test" and seen["timeout"] == 60
    assert provider.usage.prompt_tokens == 8 and provider.usage.completion_tokens == 4
    assert "test-only-not-a-real-key" not in provider.model.model_dump_json()


@pytest.mark.parametrize("backend", ["openai", "anthropic", "deepseek", "ollama_chat"])
async def test_provider_raw_exception_does_not_escape(
    monkeypatch: pytest.MonkeyPatch, backend: str, caplog: pytest.LogCaptureFixture
) -> None:
    import litellm

    async def fail(**kwargs: Any) -> Any:
        raise RuntimeError("sk-" + "TESTSECRET" * 4 + " raw transport content must be hidden")

    monkeypatch.setenv("UNIT_TEST_PROVIDER_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(litellm, "acompletion", fail)
    provider = LiteLLMProvider(
        AgentModel(provider=backend, model="test", api_key_env="UNIT_TEST_PROVIDER_KEY")
    )
    with pytest.raises(ProviderError) as error:
        await provider.complete("test", {}, HealthResult)
    assert error.value.code == "provider_request_or_schema_failed"
    assert "raw transport" not in str(error.value)
    assert "TESTSECRET" not in caplog.text + str(error.value) + provider.model.model_dump_json()
