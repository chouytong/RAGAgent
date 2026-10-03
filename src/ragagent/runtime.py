from ragagent.errors import ConfigurationError
from ragagent.providers.chat import LiteLLMProvider
from ragagent.providers.config import load_config
from ragagent.providers.embedding import LiteLLMEmbedder, LocalEmbedder
from ragagent.providers.ports import Embedder
from ragagent.retrieval.reranker import CrossEncoderReranker, Reranker
from ragagent.settings import Settings


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedding_backend == "local":
        return LocalEmbedder(settings.embedding_model, settings.embedding_dimension)
    if settings.embedding_backend == "litellm":
        return LiteLLMEmbedder(
            settings.embedding_model, settings.embedding_dimension, settings.embedding_api_base
        )
    raise ConfigurationError("unknown_embedding_backend")


def make_reranker(settings: Settings) -> Reranker:
    if settings.reranker_backend != "local":
        raise ConfigurationError("unknown_reranker_backend")
    return CrossEncoderReranker(settings.reranker_model)


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
