import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ragagent.api.schemas import HealthResult
from ragagent.errors import ConfigurationError, ProviderError
from ragagent.providers import model_identity
from ragagent.providers.chat import LiteLLMProvider, Usage
from ragagent.providers.config import AgentModel, load_config
from ragagent.providers.embedding import LiteLLMEmbedder
from ragagent.providers.environment import runtime_value
from ragagent.runtime import clear_model_cache, make_embedder, make_reranker
from ragagent.settings import Settings


def test_hosted_index_identity_isolates_endpoints_and_revisions() -> None:
    first = LiteLLMEmbedder("openai/custom", 384, "https://A.example:443/v1/")
    same = LiteLLMEmbedder("openai/custom", 384, "https://a.example/v1")
    other = LiteLLMEmbedder("openai/custom", 384, "https://b.example/v1")
    revised = LiteLLMEmbedder("openai/custom", 384, "https://a.example/v1", "weights-v2")
    assert first.fingerprint == same.fingerprint
    assert len({first.fingerprint, other.fingerprint, revised.fingerprint}) == 3
    assert "a.example" not in first.fingerprint


@pytest.mark.parametrize(
    "endpoint",
    ["https://user:password@a.example/v1", "https://a.example/?token=test", "file:///tmp"],
)
def test_hosted_endpoint_cannot_embed_credentials(endpoint: str) -> None:
    with pytest.raises(ConfigurationError, match="invalid_embedding_api_base"):
        LiteLLMEmbedder("openai/custom", 384, endpoint)


def test_dotenv_is_read_without_exporting_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNIT_TEST_ROLE_MODEL", raising=False)
    monkeypatch.delenv("UNIT_TEST_PROVIDER_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "UNIT_TEST_ROLE_MODEL=dotenv-model\nUNIT_TEST_PROVIDER_KEY=fixture-key\n"
        "EMBEDDING_API_KEY_ENV=\nEMBEDDING_API_BASE=\nEMBEDDING_REVISION=\n"
    )
    config = tmp_path / "agents.yaml"
    config.write_text(
        "agents:\n"
        + "".join(
            f"  {role}: {{provider: openai, model: '${{UNIT_TEST_ROLE_MODEL}}'}}\n"
            for role in ["supervisor", "retriever", "analyst", "reviewer"]
        )
    )
    assert load_config(config).agents.supervisor.model == "dotenv-model"
    assert runtime_value("UNIT_TEST_PROVIDER_KEY") == "fixture-key"
    monkeypatch.setenv("UNIT_TEST_ROLE_MODEL", "environment-model")
    assert load_config(config).agents.supervisor.model == "environment-model"
    monkeypatch.setenv("UNIT_TEST_PROVIDER_KEY", "")
    assert runtime_value("UNIT_TEST_PROVIDER_KEY") == ""
    settings = Settings()
    assert settings.embedding_api_key_env is None
    assert settings.embedding_api_base is None


async def test_chat_uses_dotenv_key_and_keeps_unknown_total_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import litellm

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNIT_TEST_PROVIDER_KEY", raising=False)
    (tmp_path / ".env").write_text("UNIT_TEST_PROVIDER_KEY=fixture-key\n")
    prices: list[float | None] = [0.01, None, 0.02]
    keys: list[str | None] = []

    async def complete(**kwargs: Any) -> Any:
        keys.append(kwargs["api_key"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=4),
        )

    def price(**kwargs: Any) -> float:
        value = prices.pop(0)
        if value is None:
            raise ValueError("unknown_price")
        return value

    monkeypatch.setattr(litellm, "acompletion", complete)
    monkeypatch.setattr(litellm, "completion_cost", price)
    provider = LiteLLMProvider(
        AgentModel(provider="openai", model="test", api_key_env="UNIT_TEST_PROVIDER_KEY")
    )
    assert provider.usage.cost == 0.0
    for _ in range(3):
        assert (await provider.complete("fixture", {}, HealthResult)).ok
    assert keys == ["fixture-key"] * 3
    assert provider.usage.cost is None
    assert provider.usage.known_cost == pytest.approx(0.03)
    assert provider.usage.unknown_cost_calls == 1
    assert provider.usage.calls == 3
    assert provider.usage.prompt_tokens == 24


def test_invalid_or_unknown_price_never_becomes_a_complete_total() -> None:
    usage = Usage()
    usage.record_cost(float("nan"))
    usage.record_cost(1.0)
    assert usage.cost is None and usage.known_cost == 1.0
    assert usage.unknown_cost_calls == 1


async def test_chat_records_usage_and_cost_before_rejecting_empty_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    async def complete(**kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[], usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2)
        )

    monkeypatch.setenv("UNIT_TEST_PROVIDER_KEY", "fixture-key")
    monkeypatch.setattr(litellm, "acompletion", complete)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kwargs: 0.012)
    provider = LiteLLMProvider(
        AgentModel(provider="openai", model="test", api_key_env="UNIT_TEST_PROVIDER_KEY")
    )
    with pytest.raises(ProviderError, match="provider_request_or_schema_failed"):
        await provider.complete("fixture", {}, HealthResult)
    assert provider.usage.calls == 1
    assert provider.usage.prompt_tokens == 8 and provider.usage.completion_tokens == 2
    assert provider.usage.cost == pytest.approx(0.012)


def test_local_models_are_reused_and_loaded_once_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[tuple[str, str | None]] = []

    class Encoder:
        def __init__(self, model: str, revision: str | None = None) -> None:
            time.sleep(0.01)
            loaded.append((model, revision))

        def encode(self, texts: list[str], **kwargs: Any) -> Any:
            return SimpleNamespace(tolist=lambda: [[1.0, 0.0] for _ in texts])

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Encoder)
    )
    monkeypatch.setattr(
        model_identity,
        "resolve_hub_revision",
        lambda model, revision: "b" * 40 if revision == "new-weights" else "a" * 40,
    )
    clear_model_cache()
    settings = Settings(_env_file=None, embedding_dimension=2)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            adapters = list(executor.map(lambda _: make_embedder(settings), range(8)))
            assert len({id(adapter) for adapter in adapters}) == 1
            results = list(executor.map(lambda _: adapters[0]._encode(["fixture"]), range(8)))
        assert results == [[[1.0, 0.0]]] * 8
        assert loaded == [(settings.embedding_model, "a" * 40)]
        assert make_reranker(settings) is make_reranker(settings)
        revised = settings.model_copy(update={"embedding_revision": "new-weights"})
        assert make_embedder(revised) is not adapters[0]
        assert make_embedder(revised).fingerprint != adapters[0].fingerprint
    finally:
        clear_model_cache()
