"""Real PostgreSQL/API serialization benchmark. Synthetic fixture, no quality claim.

Run in an isolated process with PYTHONPATH=<checkout>/src. Database URL is
read only from RAGAGENT_PERF_DATABASE_URL and is never included in the output.
The fixture only inserts/cleans its own deterministic conversation IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import platform
import statistics
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.orm import Session, sessionmaker

import ragagent
from ragagent.api.app import app
from ragagent.api.dependencies import get_db
from ragagent.db.models import Conversation, Message, Run

WARNING = "SYNTHETIC API PERFORMANCE FIXTURE / NOT SCIENTIFIC RETRIEVAL QUALITY"
FIXTURE_VERSION = "messages-api-perf-v1"


def identifier(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"ragagent-validation:{FIXTURE_VERSION}:{value}"))


def completed_result(turn: int) -> dict[str, Any]:
    source = (
        "Synthetic API performance text. This is invented fixture content, "
        "not a paper or research evidence. "
    ) * 10
    evidence = []
    claims = []
    for index in range(8):
        eid = identifier(f"evidence:{turn}:{index}")
        evidence.append(
            {
                "evidence_id": eid,
                "paper": {
                    "paper_id": identifier(f"paper:{index}"),
                    "title": f"SYNTHETIC PERFORMANCE FIXTURE {index}",
                    "authors": ["API Fixture"],
                    "year": 2026,
                    "venue": "NOT A PUBLICATION",
                    "arxiv_id": None,
                    "arxiv_family_id": None,
                    "arxiv_version": None,
                    "source_status": "unknown",
                },
                "chunk_id": identifier(f"chunk:{turn}:{index}"),
                "section_id": identifier(f"section:{index}"),
                "section_path": "SYNTHETIC PERFORMANCE FIXTURE",
                "page_start": 1,
                "page_end": 1,
                "content": source,
                "quote": source,
                "span_start": 0,
                "span_end": len(source),
                "scores": {"dense": 0.75, "lexical": 0.15, "rerank": 1.0},
                "source_context": [],
                "source_spans": [],
            }
        )
        claims.append(
            {
                "claim_id": f"c{index}",
                "text": f"Synthetic performance fixture statement {index}.",
                "evidence_ids": [eid],
            }
        )
    answer = "SYNTHETIC PERFORMANCE FIXTURE — no scientific conclusion. " * 12
    trace = [
        {
            "agent": "retriever" if i % 2 == 0 else "analyst",
            "iteration": i,
            "status": "completed",
            "details": {"task": "SYNTHETIC diagnostic detail. " * 20},
        }
        for i in range(40)
    ]
    return {
        "status": "completed",
        "answer": answer,
        "report": answer,
        "claims": claims,
        "evidence": evidence,
        "agent_trace": trace,
        "plan": {"subtasks": [{"id": "t1", "question": "Synthetic fixture?"}]},
        "reviewer_result": {"status": "PASS", "notes": [WARNING]},
        "limitations": [WARNING],
        "usage": {},
    }


def seed(factory: sessionmaker[Session], mode: str, count: int) -> tuple[str, dict[str, Any]]:
    cid = identifier(f"conversation:{mode}:{count}")
    timestamp = datetime(2026, 10, 7, tzinfo=UTC)
    results = []
    with factory() as db:
        # Only this script's isolated deterministic IDs are changed; no TRUNCATE.
        db.execute(delete(Conversation).where(Conversation.id == cid))
        db.add(
            Conversation(
                id=cid,
                title=f"SYNTHETIC PERF {mode} {count}",
                mode=mode,
                metadata_json={"fixture": FIXTURE_VERSION, "warning": WARNING},
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        db.flush()
        for turn in range(count // 2):
            rid = identifier(f"run:{mode}:{count}:{turn}")
            user_id = identifier(f"message:{mode}:{count}:{turn}:user")
            assistant_id = identifier(f"message:{mode}:{count}:{turn}:assistant")
            result = completed_result(turn)
            results.append(result)
            db.add(
                Run(
                    id=rid,
                    conversation_id=cid,
                    client_request_id=identifier(f"request:{mode}:{count}:{turn}"),
                    kind=mode,
                    status="completed",
                    request={
                        "conversation_id": cid,
                        "user_message_id": user_id,
                        "assistant_message_id": assistant_id,
                    },
                    result=result,
                    trace_id=identifier(f"trace:{mode}:{count}:{turn}"),
                    created_at=timestamp,
                )
            )
            db.flush()
            for role, mid, content, ordinal in (
                ("user", user_id, "Synthetic API performance query?", turn * 2),
                ("assistant", assistant_id, result["answer"], turn * 2 + 1),
            ):
                db.add(
                    Message(
                        id=mid,
                        conversation_id=cid,
                        role=role,
                        content=content,
                        ordinal=ordinal,
                        run_id=rid,
                        status="completed",
                        metadata_json={},
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
        db.commit()
        actual = db.scalar(
            select(text("count(*)")).select_from(Message).where(Message.conversation_id == cid)
        )
        if actual != count:
            raise RuntimeError("fixture_message_count_mismatch")
    canonical = json.dumps(results, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return cid, {
        "stored_run_count": count // 2,
        "stored_result_json_bytes": len(canonical.encode()),
        "stored_result_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "evidence_per_run": 8,
        "trace_entries_per_run": 40,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repeat", type=int, default=15)
    parser.add_argument(
        "--cursors",
        action="store_true",
        help="Measure additional Phase 2 cursor requests separately",
    )
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("repeat must be positive and warmup must be non-negative")
    actual_source = Path(ragagent.__file__).resolve().parent.parent
    if actual_source != args.checkout.resolve() / "src":
        raise RuntimeError("wrong_source_checkout_imported")
    url = os.environ["RAGAGENT_PERF_DATABASE_URL"]
    if not url.rsplit("/", 1)[-1].startswith("ragagent_review_"):
        raise RuntimeError("isolated_performance_database_required")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    factory = sessionmaker(engine, expire_on_commit=False)

    def database() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    for name in ("ragagent.api", "httpx", "ragagent.events"):
        logging.getLogger(name).disabled = True
    captured: list[str] = []

    def capture(*values: Any) -> None:
        captured.append(values[2])

    event.listen(engine, "before_cursor_execute", capture)
    with engine.connect() as connection:
        pg_version = connection.scalar(text("SHOW server_version"))
        vector_version = connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        )
    report: dict[str, Any] = {
        "kind": "message_api_performance",
        "label": args.label,
        "warning": WARNING,
        "fixture_version": FIXTURE_VERSION,
        "fixture_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_commit": subprocess.check_output(
            ["git", "-C", str(args.checkout), "rev-parse", "HEAD"], text=True
        ).strip(),
        "timestamp": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "postgres_version": pg_version,
        "pgvector_version": vector_version,
        "transport": "FastAPI TestClient ASGI + real PostgreSQL TCP; no network HTTP/TLS",
        "scope": (
            "DB query + Python serialization + ASGI transport; not browser rendering/model latency"
        ),
        "warmup_requests_per_case": args.warmup,
        "measured_requests_per_case": args.repeat,
        "observations": [],
    }
    # No lifespan manager: benchmark GET routes only, no dispatcher/worker setup.
    token = os.environ.get("LOCAL_AUTH_TOKEN")
    if not token:
        raise RuntimeError("benchmark_runtime_auth_token_required")
    client = TestClient(app, headers={"Authorization": "Bearer " + token})
    try:
        for mode in ("rag", "research"):
            for count in (10, 50, 100):
                cid, fixture = seed(factory, mode, count)
                endpoints = {
                    "full_history": (count, 0, count, f"limit={count}&offset=0"),
                    "tail_window_20": (
                        min(count, 20),
                        max(0, count - 20),
                        min(count, 20),
                        f"limit={min(count, 20)}&offset={max(0, count - 20)}",
                    ),
                    "single_message": (1, count - 1, 1, f"limit=1&offset={count - 1}"),
                }
                if args.cursors:
                    first = max(0, count - 50)
                    endpoints.update(
                        {
                            "initial_latest_50": (50, None, min(count, 50), "limit=50"),
                            "older_page": (
                                50,
                                None,
                                min(first, 50),
                                f"limit=50&before_ordinal={first}",
                            ),
                            "incremental_one": (50, None, 1, f"limit=50&after_ordinal={count - 2}"),
                            "incremental_empty": (
                                50,
                                None,
                                0,
                                f"limit=50&after_ordinal={count - 1}",
                            ),
                        }
                    )
                for request_kind, (limit, offset, expected, query) in endpoints.items():
                    endpoint = f"/api/conversations/{cid}/messages?{query}"
                    samples = []
                    for index in range(args.warmup + args.repeat):
                        captured.clear()
                        start = time.perf_counter_ns()
                        response = client.get(endpoint)
                        elapsed = (time.perf_counter_ns() - start) / 1e6
                        if response.status_code != 200:
                            raise RuntimeError(f"message_api_status_{response.status_code}")
                        payload = response.json()
                        if len(payload) != expected:
                            raise RuntimeError("message_api_response_count_mismatch")
                        if index >= args.warmup:
                            samples.append(
                                {
                                    "latency_ms": elapsed,
                                    "response_bytes": len(response.content),
                                    "returned_messages": len(payload),
                                    "returned_ordinals": [row["ordinal"] for row in payload],
                                    "sql_statement_count": len(captured),
                                    "select_count": sum(
                                        s.lstrip().upper().startswith("SELECT") for s in captured
                                    ),
                                    "run_result_select_count": sum(
                                        "runs.result" in s and "SELECT" in s.upper()
                                        for s in captured
                                    ),
                                }
                            )
                    latencies = sorted(s["latency_ms"] for s in samples)
                    row = {
                        "mode": mode,
                        "total_messages": count,
                        "request_kind": request_kind,
                        "limit": limit,
                        "offset": offset,
                        "query": query,
                        "fixture": fixture,
                        "samples": samples,
                        "summary": {
                            "latency_median_ms": statistics.median(latencies),
                            "latency_mean_ms": statistics.mean(latencies),
                            "latency_p95_ms": latencies[math.ceil(len(latencies) * 0.95) - 1],
                            "response_bytes": samples[0]["response_bytes"],
                            "select_count": samples[0]["select_count"],
                            "run_result_select_count": samples[0]["run_result_select_count"],
                        },
                    }
                    report["observations"].append(row)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    print(
                        json.dumps(
                            {
                                k: row[k]
                                for k in ("mode", "total_messages", "request_kind", "summary")
                            }
                        )
                    )
    finally:
        client.close()
        app.dependency_overrides.clear()
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()


if __name__ == "__main__":
    main()
