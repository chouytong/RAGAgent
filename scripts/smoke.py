"""Verify HTTP, empty-index retrieval, real queued failure and proxied SSE.

This checks the explicit missing-key path, never substitutes mock inference.
"""

import argparse
import json
import time
import urllib.request
from typing import Any


def request(base: str, path: str, data: dict[str, Any] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, json.load(response)


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
        base + "/api/runs/" + run["id"] + "/events", timeout=10
    ) as response:
        stream = response.read().decode()
        if response.status != 200 or "text/event-stream" not in response.headers["Content-Type"]:
            raise RuntimeError("sse_response_invalid")
    if stream.count("event: execution") < 3 or stream.count("event: done") != 1:
        raise RuntimeError("sse_terminal_replay_incomplete")
    if '"node": "started"' not in stream or '"node": "failed"' not in stream:
        raise RuntimeError("worker_events_missing")
    print("PASS: readiness, frontend, empty search, real RQ missing-key failure, proxied SSE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    smoke(args.base_url.rstrip("/"))
