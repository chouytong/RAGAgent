"""Read runtime values without exporting dotenv secrets into global state."""

import os
from pathlib import Path

from dotenv import dotenv_values


def runtime_value(name: str, env_file: Path | str = ".env") -> str | None:
    # An explicitly empty environment variable also overrides the dotenv value.
    if name in os.environ:
        return os.environ[name]
    value = dotenv_values(env_file, interpolate=False).get(name)
    return value if isinstance(value, str) else None
