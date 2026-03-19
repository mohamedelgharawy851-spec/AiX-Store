from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        return current.parents[1]
    for candidate in current.parents:
        if (candidate / ".gitignore").is_file() or (candidate / "package.json").is_file():
            return candidate
    return current.parents[1]


def _service_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] == '"':
            return (
                inner.replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return inner
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or any(char.isspace() for char in key):
            continue
        parsed[key] = _parse_value(value)
    return parsed


def load_env_files() -> list[str]:
    loaded_values: dict[str, str] = {}
    loaded_files: list[str] = []
    candidates = [
        _repo_root() / ".env",
        _service_root() / ".env",
        _repo_root() / ".env.local",
        _service_root() / ".env.local",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        loaded_values.update(_parse_env_file(path))
        loaded_files.append(str(path))
    for key, value in loaded_values.items():
        os.environ.setdefault(key, value)
    return loaded_files


LOADED_ENV_FILES = load_env_files()
