import re

_PUBLIC_MESSAGES = {
    "local_auth_required": ("Local authorization required.", False),
    "local_auth_not_initialized": ("Pair the local backend before connecting.", False),
    "invalid_request": ("Invalid request; rejected input is not echoed.", False),
    "infrastructure_unavailable": ("Database or queue infrastructure is unavailable.", True),
    "worker_unavailable": ("Interactive worker is not available.", True),
    "provider_key_missing": ("Configure the provider credential server-side.", False),
    "provider_request_or_schema_failed": ("Provider request or response validation failed.", True),
    "internal_error": ("Request failed; use the request ID to inspect safe diagnostics.", False),
}


def error_payload(code: str, request_id: str | None = None) -> dict[str, object]:
    safe = code if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "internal_error"
    message, retryable = _PUBLIC_MESSAGES.get(
        safe, ("Operation failed; check configuration and the safe error code.", False)
    )
    identifier = request_id if request_id and re.fullmatch(r"[0-9a-f-]{36}", request_id) else None
    return {
        "error_code": safe,
        "message": message,
        "retryable": retryable,
        "details": None,
        "request_id": identifier,
    }


class ApplicationError(Exception):
    """Only safe, static codes cross the API boundary."""

    code = "application_error"

    def __init__(self, code: str | None = None) -> None:
        if code is not None and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            self.code = code
        super().__init__(self.code)


class ParsingError(ApplicationError):
    code = "parsing_failed"


class ProviderError(ApplicationError):
    code = "provider_failed"


class ConfigurationError(ApplicationError):
    code = "configuration_invalid"


class EvidenceError(ApplicationError):
    code = "evidence_invalid"


class EvaluationError(ApplicationError, ValueError):
    code = "evaluation_invalid"
