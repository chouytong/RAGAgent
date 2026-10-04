import threading
from functools import lru_cache

from ragagent.errors import ConfigurationError
from ragagent.providers.chat import LiteLLMProvider
from ragagent.providers.config import load_config
from ragagent.providers.embedding import LiteLLMEmbedder, LocalEmbedder
from ragagent.providers.ports import Embedder
from ragagent.retrieval.reranker import CrossEncoderReranker, Reranker
from ragagent.settings import Settings

_adapter_lock = threading.RLock()


@lru_cache(maxsize=2)
def _local_embedder(model: str, dimension: int, revision: str | None) -> LocalEmbedder:
    return LocalEmbedder(model, dimension, revision)


@lru_cache(maxsize=2)
def _local_reranker(model: str, revision: str | None) -> CrossEncoderReranker:
    return CrossEncoderReranker(model, revision)


def clear_model_cache() -> None:
    with _adapter_lock:
        _local_embedder.cache_clear()
        _local_reranker.cache_clear()


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedding_backend == "local":
        with _adapter_lock:
            return _local_embedder(
                settings.embedding_model, settings.embedding_dimension, settings.embedding_revision
            )
    if settings.embedding_backend == "litellm":
        return LiteLLMEmbedder(
            settings.embedding_model,
            settings.embedding_dimension,
            settings.embedding_api_base,
            settings.embedding_revision,
            settings.embedding_api_key_env,
        )
    raise ConfigurationError("unknown_embedding_backend")


def make_reranker(settings: Settings) -> Reranker:
    if settings.reranker_backend != "local":
        raise ConfigurationError("unknown_reranker_backend")
    with _adapter_lock:
        return _local_reranker(settings.reranker_model, settings.reranker_revision)


def make_agents(settings: Settings) -> dict[str, LiteLLMProvider]:
    config = load_config(settings.agent_config)
    return {
        name: LiteLLMProvider(model, settings.provider_timeout)
        for name, model in [
            ("supervisor", config.agents.supervisor),
            ("retriever", config.agents.retriever),
            ("analyst", config.agents.analyst),
            ("reviewer", config.agents.reviewer),
        ]
    }
