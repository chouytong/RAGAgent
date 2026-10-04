import sys
from types import SimpleNamespace
from typing import Any

import pytest

from ragagent.errors import ConfigurationError, ProviderError
from ragagent.providers.embedding import LiteLLMEmbedder


async def test_hosted_embedding_accounts_tokens_cost_and_actual_response_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def embedding(**kwargs: Any) -> Any:
        return SimpleNamespace(
            data=[{"index": 0, "embedding": [1.0, 0.0]}],
            usage=SimpleNamespace(prompt_tokens=7),
            model="embedding-version-from-provider",
            system_fingerprint="fp_example",
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(aembedding=embedding, completion_cost=lambda **kwargs: 0.012),
    )
    provider = LiteLLMEmbedder("openai/custom", 2, "https://api.example/v1")
    assert await provider.embed(["fixture"]) == [[1.0, 0.0]]
    assert provider.usage.prompt_tokens == 7
    assert provider.usage.completion_tokens == 0
    assert provider.usage.calls == 1
    assert provider.usage.cost == pytest.approx(0.012)
    assert provider.usage.provider_models == ["embedding-version-from-provider"]
    assert provider.usage.system_fingerprints == ["fp_example"]


async def test_unknown_embedding_price_or_request_failure_keeps_total_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[Any] = [
        SimpleNamespace(
            data=[{"index": 0, "embedding": [1.0, 0.0]}], usage=SimpleNamespace(prompt_tokens=3)
        ),
        TimeoutError("potentially billed"),
    ]

    async def embedding(**kwargs: Any) -> Any:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def unknown_price(**kwargs: Any) -> float:
        raise ValueError("no_price_available")

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(aembedding=embedding, completion_cost=unknown_price),
    )
    provider = LiteLLMEmbedder("openai/custom", 2, "https://api.example/v1")
    await provider.embed(["fixture"])
    with pytest.raises(ProviderError, match="embedding_failed"):
        await provider.embed(["failed fixture"])
    assert provider.usage.prompt_tokens == 3
    assert provider.usage.calls == 2
    assert provider.usage.unknown_cost_calls == 2
    assert provider.usage.cost is None and provider.usage.known_cost == 0.0


async def test_invalid_embedding_vectors_still_retain_successful_call_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def embedding(**kwargs: Any) -> Any:
        return SimpleNamespace(
            data=[{"index": 0, "embedding": [0.0, 0.0]}], usage=SimpleNamespace(prompt_tokens=5)
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(aembedding=embedding, completion_cost=lambda **kwargs: 0.03),
    )
    provider = LiteLLMEmbedder("openai/custom", 2, "https://api.example/v1")
    with pytest.raises(ProviderError, match="embedding_failed"):
        await provider.embed(["fixture"])
    assert provider.usage.calls == 1 and provider.usage.prompt_tokens == 5
    assert provider.usage.cost == pytest.approx(0.03)


async def test_missing_explicit_key_does_not_record_an_uncalled_provider(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UNIT_TEST_EMBEDDING_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace())
    provider = LiteLLMEmbedder(
        "openai/custom", 2, "https://api.example/v1", api_key_env="UNIT_TEST_EMBEDDING_KEY"
    )
    with pytest.raises(ConfigurationError, match="embedding_key_missing"):
        await provider.embed(["fixture"])
    assert provider.usage.calls == 0 and provider.usage.cost == 0.0
