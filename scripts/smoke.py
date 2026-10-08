"""Verify HTTP, empty-index retrieval, real queued failure and proxied SSE.

This checks the explicit missing-key path, never substitutes mock inference.
"""

import argparse
import json
import os
import time
import urllib.request
from typing import Any
from uuid import uuid4


def request(
    base: str, path: str, data: dict[str, Any] | None = None, *, method: str | None = None
) -> tuple[int, Any]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json", **auth_headers()},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, json.load(response)


def auth_headers() -> dict[str, str]:
    token = os.environ.get("LOCAL_AUTH_TOKEN")
    if not token:
        raise RuntimeError("smoke_runtime_auth_token_required")
    return {"Authorization": "Bearer " + token}


def smoke(base: str) -> None:
    code, ready = request(base, "/api/ready")
    if code != 200 or ready.get("status") != "ready":
        raise RuntimeError("dependencies_not_ready")
    with urllib.request.urlopen(base + "/", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("frontend_unavailable")
    _, papers = request(base, "/api/papers?limit=1")
    if papers:
        raise RuntimeError("smoke_requires_empty_corpus")
    code, search = request(base, "/api/search", {"query": "deployment smoke empty corpus"})
    if code != 200 or search["evidence"]:
        raise RuntimeError("empty_index_search_failed")
    _, providers = request(base, "/api/providers")
    supervisor = providers["agents"]["supervisor"]
    if supervisor["key_configured"] or supervisor["provider"] not in {
        "openai",
        "anthropic",
        "deepseek",
    }:
        raise RuntimeError("smoke_requires_unconfigured_chat_provider")
    code, run = request(base, "/api/rag/query", {"query": "deployment smoke missing provider key"})
    if code != 202:
        raise RuntimeError("job_submission_failed")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, run = request(base, "/api/runs/" + run["id"])
        if run["status"] not in {"queued", "running"}:
            break
        time.sleep(0.25)
    if run["status"] != "failed" or run["error_code"] != "provider_key_missing":
        raise RuntimeError("worker_failure_path_not_verified")
    with urllib.request.urlopen(
        urllib.request.Request(base + "/api/runs/" + run["id"] + "/events", headers=auth_headers()),
        timeout=10,
    ) as response:
        stream = response.read().decode()
        if response.status != 200 or "text/event-stream" not in response.headers["Content-Type"]:
            raise RuntimeError("sse_response_invalid")
    if stream.count("event: execution") < 3 or stream.count("event: done") != 1:
        raise RuntimeError("sse_terminal_replay_incomplete")
    if '"node": "started"' not in stream or '"node": "failed"' not in stream:
        raise RuntimeError("worker_events_missing")
    conversation_smoke(base)
    print(
        "PASS: readiness, frontend, empty search, real RQ missing-key failure, proxied SSE, "
        "persisted conversation/message lifecycle, idempotency and explicit memory deletion"
    )


def conversation_smoke(base: str) -> None:
    _, conversation = request(base, "/api/conversations", {"mode": "rag"})
    path = "/api/conversations/" + conversation["id"]
    try:
        message = {
            "content": "conversation smoke missing provider key",
            "client_request_id": str(uuid4()),
        }
        code, turn = request(base, path + "/messages", message)
        if code != 202:
            raise RuntimeError("conversation_submission_failed")
        _, duplicate = request(base, path + "/messages", message)
        if duplicate["run"]["id"] != turn["run"]["id"]:
            raise RuntimeError("conversation_idempotency_failed")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, run = request(base, "/api/runs/" + turn["run"]["id"])
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.25)
        if run["status"] != "failed" or run["error_code"] != "provider_key_missing":
            raise RuntimeError("conversation_worker_failure_unverified")
        _, messages = request(base, path + "/messages")
        if (
            len(messages) != 2
            or [m["role"] for m in messages] != ["user", "assistant"]
            or messages[1]["status"] != "failed"
            or messages[1]["run"]["id"] != run["id"]
            or not messages[1]["content"]
        ):
            raise RuntimeError("conversation_terminal_message_missing")
        with urllib.request.urlopen(
            urllib.request.Request(
                base + "/api/runs/" + run["id"] + "/events", headers=auth_headers()
            ),
            timeout=10,
        ) as response:
            if response.read().decode().count("event: done") != 1:
                raise RuntimeError("conversation_sse_terminal_missing")
        _, memory = request(
            base,
            path + "/memories",
            {
                "kind": "constraint",
                "content": "Only papers published after 2023.",
                "filters": {"year_start": 2024},
            },
        )
        _, saved = request(base, path + "/memories")
        if len(saved) != 1 or saved[0]["id"] != memory["id"]:
            raise RuntimeError("conversation_memory_not_persisted")
        request(base, path + "/memory", method="DELETE")
        _, saved = request(base, path + "/memories")
        if saved:
            raise RuntimeError("conversation_memory_not_deleted")
        request(base, path + "/clear", {})
        _, messages = request(base, path + "/messages")
        if messages:
            raise RuntimeError("conversation_history_not_deleted")
    finally:
        request(base, path, method="DELETE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    smoke(args.base_url.rstrip("/"))
