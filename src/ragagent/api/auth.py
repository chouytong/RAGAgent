"""Local owner authentication; backend stores verifier hashes, never bearer values."""

import hashlib
import hmac
import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ragagent.errors import ApplicationError, error_payload
from ragagent.settings import get_settings

TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
COOKIE_NAME = "ragagent_local_session"
SESSION_SECONDS = 8 * 3600
router = APIRouter(prefix="/api/auth", tags=["local authentication"])
# Raw session values exist only in the HttpOnly response cookie. Entries are hashes.
_sessions: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def expected_verifiers() -> tuple[str, ...]:
    configured = get_settings().local_auth_token_hash
    hashes = [configured] if configured is not None else []
    # Optional additional Web development credential: process environment only.
    token = os.environ.get("LOCAL_AUTH_TOKEN")
    if token is not None:
        if not TOKEN_SHAPE.fullmatch(token):
            raise ApplicationError("local_auth_configuration_invalid")
        hashes.append(digest(token))
    return tuple(dict.fromkeys(hashes))


def expected_verifier() -> str | None:
    values = expected_verifiers()
    return values[0] if values else None


def _prune(now: float) -> None:
    for identifier, (_, expires) in list(_sessions.items()):
        if expires <= now:
            _sessions.pop(identifier, None)


def authenticated_verifier(request: Request) -> str | None:
    verifiers = expected_verifiers()
    headers = request.headers.getlist("authorization")
    if headers:
        if len(headers) != 1:
            return None
        scheme, _, token = headers[0].partition(" ")
        if scheme.lower() != "bearer" or not TOKEN_SHAPE.fullmatch(token):
            return None
        supplied = digest(token)
        return next(
            (expected for expected in verifiers if hmac.compare_digest(supplied, expected)), None
        )
    cookie = request.cookies.get(COOKIE_NAME, "")
    occurrences = sum(
        part.strip().startswith(COOKIE_NAME + "=")
        for header in request.headers.getlist("cookie")
        for part in header.split(";")
    )
    if occurrences != 1 or not TOKEN_SHAPE.fullmatch(cookie):
        return None
    now = time.monotonic()
    with _lock:
        _prune(now)
        session = _sessions.get(digest(cookie))
        if session and session[0] in verifiers and session[1] > now:
            return session[0]
    return None


def authenticated(request: Request) -> bool:
    return authenticated_verifier(request) is not None


def auth_error(request: Request) -> str | None:
    if not request.url.path.startswith("/api/") or request.url.path in {
        "/api/health",
        "/api/ready",
        "/api/auth/status",
    }:
        return None
    return None if authenticated(request) else "local_auth_required"


@router.get("/status", openapi_extra={"security": []})
def status(request: Request) -> dict[str, bool]:
    verifier = expected_verifier()
    return {"initialized": verifier is not None, "authenticated": authenticated(request)}


@router.post("/session")
def create_session(request: Request) -> JSONResponse:
    # Middleware checks the bearer; also enforce it for direct callers.
    if not request.headers.get("authorization") or not authenticated(request):
        return JSONResponse(status_code=401, content=error_payload("local_auth_required"))
    verifier = authenticated_verifier(request)
    assert verifier is not None
    cookie = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        _prune(now)
        if len(_sessions) >= 128:
            raise ApplicationError("local_session_limit")
        _sessions[digest(cookie)] = (verifier, now + SESSION_SECONDS)
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/api",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.delete("/session")
def delete_session(request: Request) -> JSONResponse:
    cookie = request.cookies.get(COOKIE_NAME, "")
    if TOKEN_SHAPE.fullmatch(cookie):
        with _lock:
            _sessions.pop(digest(cookie), None)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return response
