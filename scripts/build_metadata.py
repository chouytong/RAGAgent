"""Generate nonsecret packaged release metadata; no dependency on runtime Git."""

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit = os.environ.get("RAGAGENT_SOURCE_COMMIT", "")
    dirty = None
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            dirty = bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain", "--untracked-files=no"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except (OSError, subprocess.SubprocessError):
            commit = "unknown"
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        commit = "unknown"
    built = os.environ.get("RAGAGENT_BUILD_TIME", "")
    if not re.fullmatch(r"[0-9TZ:+. -]{10,40}", built):
        built = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"source_commit": commit, "dirty": dirty, "built_at_utc": built}, indent=2)
        + "\n"
    )


if __name__ == "__main__":
    main()
