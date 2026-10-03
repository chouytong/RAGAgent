import re


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
