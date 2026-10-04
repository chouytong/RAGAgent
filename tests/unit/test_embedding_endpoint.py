import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ragagent.errors import ConfigurationError
from ragagent.providers.embedding import LiteLLMEmbedder


@pytest.mark.parametrize(
    "explicit,sdk_base,base_url,api_base,expected",
    [
        (
            "https://explicit.example/v1/",
            "https://sdk.example",
            "https://url.example",
            None,
            "https://explicit.example/v1",
        ),
        (
            None,
            "https://sdk.example/v1/",
            "https://url.example",
            "https://api.example",
            "https://sdk.example/v1",
        ),
        (None, None, "https://url.example/v1/", "https://api.example", "https://url.example/v1"),
        (None, None, None, "https://api.example/v1/", "https://api.example/v1"),
        (None, None, None, None, "https://api.openai.com/v1"),
    ],
)
def test_openai_embedding_endpoint_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit: str | None,
    sdk_base: str | None,
    base_url: str | None,
    api_base: str | None,
    expected: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(api_base=sdk_base))
    for name, value in [("OPENAI_BASE_URL", base_url), ("OPENAI_API_BASE", api_base)]:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert LiteLLMEmbedder("openai/custom", 2, explicit).api_base == expected


def test_openai_embedding_endpoint_reads_local_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(api_base=None))
    (tmp_path / ".env").write_text(
        "OPENAI_BASE_URL=https://dotenv.example/v1/\nOPENAI_API_BASE=https://ignored.example/v1\n"
    )
    assert LiteLLMEmbedder("openai/custom", 2).api_base == "https://dotenv.example/v1"


@pytest.mark.parametrize("provider", ["cohere", "cohere_chat", "voyage"])
def test_non_openai_embedding_requires_explicit_base(provider: str) -> None:
    with pytest.raises(ConfigurationError, match="embedding_api_base_required"):
        LiteLLMEmbedder(f"{provider}/custom", 2)
    assert (
        LiteLLMEmbedder(f"{provider}/custom", 2, "https://explicit.example/v1/").api_base
        == "https://explicit.example/v1"
    )


@pytest.mark.parametrize("provider", ["unknown", "ollama", "sagemaker"])
def test_unverified_embedding_provider_is_rejected_even_with_explicit_base(provider: str) -> None:
    with pytest.raises(ConfigurationError, match="unsupported_embedding_provider"):
        LiteLLMEmbedder(f"{provider}/custom", 2, "https://explicit.example/v1")


async def test_sdk_transport_uses_frozen_endpoint_after_environment_and_global_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    main_module = importlib.import_module("litellm.main")
    transported: list[str] = []

    def transport(**kwargs: Any) -> Any:
        transported.append(kwargs["api_base"])
        return litellm.EmbeddingResponse(
            model="custom",
            data=[{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
            usage={"prompt_tokens": 1, "total_tokens": 1},
        )

    monkeypatch.setattr(main_module.openai_chat_completions, "embedding", transport)
    monkeypatch.setattr(litellm, "api_base", None)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://first.example/v1/")
    monkeypatch.setenv("UNIT_TEST_EMBEDDING_KEY", "fixture-not-real")
    first = LiteLLMEmbedder("openai/custom", 2, api_key_env="UNIT_TEST_EMBEDDING_KEY")
    first_fingerprint = first.fingerprint
    monkeypatch.setenv("OPENAI_BASE_URL", "https://second.example/v1")
    second = LiteLLMEmbedder("openai/custom", 2, api_key_env="UNIT_TEST_EMBEDDING_KEY")
    monkeypatch.setattr(litellm, "api_base", "https://later-global.example/v1")
    assert await first.embed(["fixture"]) == [[1.0, 0.0]]
    assert await second.embed(["fixture"]) == [[1.0, 0.0]]
    assert transported == ["https://first.example/v1", "https://second.example/v1"]
    assert first.fingerprint == first_fingerprint and first.fingerprint != second.fingerprint
