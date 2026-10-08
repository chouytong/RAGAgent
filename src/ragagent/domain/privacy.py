"""Recognizable credential rejection at user-input and persistence boundaries."""

import re
from typing import Any

from pydantic import BaseModel, model_validator

# Reject recognizable credentials at persistence boundaries without consulting
# runtime secrets. Ordinary scientific words such as "token" are not rejected.
_CREDENTIAL = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r"|\b(?:[A-Z][A-Z0-9_]*_(?:API_KEY|SECRET|TOKEN|PASSWORD)|api_key|access_token|secret_key)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+-]{12,}"
    r"|https?://[^\s/:@]+:[^\s/@]+@"
    r"|\bBearer\s+[A-Za-z0-9._~-]{20,}"
    r"|(?:postgres(?:ql)?(?:\+psycopg)?|redis)://[^/\s@]+:[^/\s@]+@"
)


def safe_text(value: str) -> str:
    if _CREDENTIAL.search(value):
        raise ValueError("credential_content_not_allowed")
    return value.strip()


def reject_credentials(value: Any, depth: int = 0) -> None:
    if depth > 30:
        raise ValueError("input_nesting_limit")
    if isinstance(value, BaseModel):
        reject_credentials(value.model_dump(mode="json"), depth + 1)
    elif isinstance(value, str):
        safe_text(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            reject_credentials(key, depth + 1)
            reject_credentials(item, depth + 1)
            if isinstance(key, str) and isinstance(item, str):
                safe_text(key + "=" + item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_credentials(item, depth + 1)


class SensitiveInput(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def no_credentials(cls, value: Any) -> Any:
        reject_credentials(value)
        return value
