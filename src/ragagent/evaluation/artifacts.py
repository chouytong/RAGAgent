import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ragagent.evaluation.schema import EvaluationDataset
from ragagent.providers.config import load_config
from ragagent.settings import Settings


def source_commit() -> str:
    configured = os.environ.get("GIT_COMMIT", "")
    if re.fullmatch(r"[a-f0-9]{40}", configured):
        return configured
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        head = Path(".git/HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            if not re.fullmatch(r"refs/[A-Za-z0-9_./-]+", ref):
                raise ValueError("invalid_git_reference") from None
            path = Path(".git") / ref
            if path.exists():
                head = path.read_text().strip()
            else:
                head = next(
                    line.split()[0]
                    for line in Path(".git/packed-refs").read_text().splitlines()
                    if line.endswith(" " + ref)
                )
        if not re.fullmatch(r"[a-f0-9]{40}", head):
            raise ValueError("git_commit_unavailable") from None
        return head


def manifest(dataset: EvaluationDataset, settings: Settings) -> dict[str, Any]:
    serialized = json.dumps(dataset.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return {
        "git_commit": source_commit(),
        "dataset_hash": hashlib.sha256(serialized.encode()).hexdigest(),
        "dataset_id": dataset.dataset_id,
        "label_source": dataset.label_source,
        "warning": dataset.warning,
        "timestamp": datetime.now(UTC).isoformat(),
        "model_configuration": load_config(settings.agent_config).model_dump(),
        "retrieval_configuration": {
            "candidate_top_n": settings.candidate_top_n,
            "evidence_top_k": settings.evidence_top_k,
            "rrf_k": settings.rrf_k,
            "minimum_rerank_score": settings.minimum_rerank_score,
            "embedding_backend": settings.embedding_backend,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": settings.embedding_dimension,
            "reranker_model": settings.reranker_model,
        },
    }


def write_results(directory: Path, result: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        "# Evaluation results",
        "",
        result["manifest"]["warning"],
        "",
        f"Commit: `{result['manifest']['git_commit']}`",
        f"Dataset hash: `{result['manifest']['dataset_hash']}`",
        "",
        "Metrics are calculated from this run. Null means unavailable/undefined.",
        "",
        "```json",
        json.dumps(result.get("summary", {}), indent=2),
        "```",
        "",
        "See results.json for per-query rankings, latency, models and provenance.",
    ]
    (directory / "results.md").write_text("\n".join(lines), encoding="utf-8")
