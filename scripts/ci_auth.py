"""CI-only ephemeral auth. Mask bearer output; Compose receives only its hash."""

import argparse
import hashlib
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-env", type=Path)
    args = parser.parse_args()
    output = os.environ.get("GITHUB_ENV")
    if not output:
        parser.error("github_actions_environment_required")
    token = secrets.token_urlsafe(32)
    print("::add-mask::" + token)
    with Path(output).open("a") as file:
        file.write("LOCAL_AUTH_TOKEN=" + token + "\n")
    if args.compose_env:
        with args.compose_env.open("a") as file:
            file.write(
                "\nLOCAL_AUTH_TOKEN_HASH=" + hashlib.sha256(token.encode()).hexdigest() + "\n"
            )


if __name__ == "__main__":
    main()
