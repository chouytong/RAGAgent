"""Release identity is packaged metadata, with Git used only in source checkouts."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ragagent import __version__


def build_info() -> dict[str, Any]:
    try:
        packaged = json.loads(Path(__file__).with_name("build-metadata.json").read_text())
        if not isinstance(packaged, dict):
            packaged = {}
    except (OSError, ValueError):
        packaged = {}
    commit = (
        os.environ.get("RAGAGENT_SOURCE_COMMIT")
        or os.environ.get("GIT_COMMIT")
        or packaged.get("source_commit")
    )
    dirty: bool | None = packaged.get("dirty")
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        try:
            root = Path(__file__).resolve().parents[2]
            commit = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
            dirty = bool(
                subprocess.check_output(
                    ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                ).strip()
            )
        except (OSError, subprocess.SubprocessError):
            commit = "unknown"
            dirty = None
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        commit = "unknown"
    built = os.environ.get("RAGAGENT_BUILD_TIME") or packaged.get("built_at_utc") or "unknown"
    return {
        "version": __version__,
        "source_commit": commit,
        "dirty": dirty if isinstance(dirty, bool) else None,
        "built_at_utc": built
        if isinstance(built, str) and re.fullmatch(r"[0-9TZ:+. -]{10,40}|unknown", built)
        else "unknown",
    }
