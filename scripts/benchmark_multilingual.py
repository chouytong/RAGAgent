"""Run an approved licensed corpus/model matrix; never generate gold labels."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from pydantic import ValidationError

from ragagent.evaluation.multilingual import MultilingualBenchmark, run_matrix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url-env", default="TEST_DATABASE_URL")
    args = parser.parse_args()
    try:
        spec = MultilingualBenchmark.model_validate_json(args.manifest.read_text())
    except (ValidationError, ValueError, OSError):
        parser.error("invalid_or_unsafe_benchmark_manifest")
    database = os.environ.get(args.database_url_env)
    if not database:
        parser.error("isolated_database_environment_required")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    args.output.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run_matrix(spec, database, args.output))
    (args.output / "matrix.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
