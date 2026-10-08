"""Hash actual installer files; an absent Windows bundle fails CI."""

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifacts = sorted(
        path
        for path in args.bundle.rglob("*")
        if path.is_file() and path.suffix in {".msi", ".exe"}
    )
    if not {".msi", ".exe"}.issubset({path.suffix for path in artifacts}):
        parser.error("msi_and_nsis_artifacts_required")
    desktop = json.loads(Path("frontend/src-tauri/tauri.conf.json").read_text())
    metadata = json.loads(args.metadata.read_text())
    manifest = {
        **metadata,
        "version": desktop["version"],
        "platform": "windows-x86_64",
        "signing": "unsigned",
        "artifacts": [
            {
                "path": str(path.relative_to(args.bundle)),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in artifacts
        ],
    }
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
