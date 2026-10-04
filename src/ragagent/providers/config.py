import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, model_validator

from ragagent.errors import ConfigurationError
from ragagent.providers.environment import runtime_value


class AgentModel(BaseModel):
    provider: Literal["openai", "anthropic", "deepseek", "ollama_chat", "openai_compatible"]
    model: str = Field(min_length=1)
    api_base: str | None = None
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")

    @model_validator(mode="after")
    def local_base(self) -> "AgentModel":
        if self.provider == "openai_compatible" and not self.api_base:
            raise ValueError("api_base required for compatible provider")
        if self.api_base:
            url = urlsplit(self.api_base)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("api_base_must_not_contain_credentials_or_query")
        if "${" in self.model:
            raise ValueError("unresolved_model_environment")
        return self

    @property
    def litellm_model(self) -> str:
        provider = "openai" if self.provider == "openai_compatible" else self.provider
        return f"{provider}/{self.model}"

    @property
    def key_environment(self) -> str | None:
        return self.api_key_env or {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }.get(self.provider)


class AgentMapping(BaseModel):
    supervisor: AgentModel
    retriever: AgentModel
    analyst: AgentModel
    reviewer: AgentModel


class ProviderConfig(BaseModel):
    agents: AgentMapping


def load_config(path: Path) -> ProviderConfig:
    def substitute(match: re.Match[str]) -> str:
        name, _, default = match.group(1).partition(":-")
        value = runtime_value(name)
        if value is None:
            value = default
        if not value:
            raise ConfigurationError("missing_model_environment")
        return value

    try:
        text = re.sub(r"\$\{([^}]+)\}", substitute, path.read_text())
        return ProviderConfig.model_validate(yaml.safe_load(text))
    except ConfigurationError:
        raise
    except Exception:
        raise ConfigurationError("invalid_provider_config") from None
