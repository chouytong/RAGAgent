class ApplicationError(Exception):
    """Only safe, static codes cross the API boundary."""

    code = "application_error"


class ParsingError(ApplicationError):
    code = "parsing_failed"


class ProviderError(ApplicationError):
    code = "provider_failed"


class ConfigurationError(ApplicationError):
    code = "configuration_invalid"


class EvidenceError(ApplicationError):
    code = "evidence_invalid"
