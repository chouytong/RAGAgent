"""Reject foreign browser origins and rebinding hosts for the local application."""

from urllib.parse import SplitResult, urlsplit

from starlette.requests import Request

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def local_authority(value: str) -> SplitResult | None:
    try:
        authority = urlsplit("//" + value)
        if (
            authority.netloc != value
            or authority.hostname not in LOCAL_HOSTS
            or authority.username is not None
            or authority.password is not None
            or any(character.isspace() for character in value)
        ):
            return None
        # Parsing the port also rejects malformed or out-of-range values.
        _ = authority.port
        return authority
    except ValueError:
        return None


def effective_port(authority: SplitResult, scheme: str) -> int:
    return authority.port if authority.port is not None else (443 if scheme == "https" else 80)


def local_request_error(request: Request) -> str | None:
    hosts = request.headers.getlist("host")
    host = local_authority(hosts[0]) if len(hosts) == 1 else None
    if host is None:
        return "untrusted_host"
    if request.method in SAFE_METHODS:
        return None
    origins = request.headers.getlist("origin")
    if not origins:
        return None  # Command-line clients do not send browser Origin headers.
    if len(origins) != 1:
        return "untrusted_origin"
    try:
        origin = urlsplit(origins[0])
        authority = local_authority(origin.netloc)
        if (
            origin.scheme not in {"http", "https"}
            or origins[0] != f"{origin.scheme}://{origin.netloc}"
            or authority is None
            or origin.hostname != host.hostname
            or origin.scheme != request.url.scheme
            or effective_port(origin, origin.scheme) != effective_port(host, request.url.scheme)
        ):
            return "untrusted_origin"
    except ValueError:
        return "untrusted_origin"
    return None
