from __future__ import annotations

import os


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_mode(name: str, default: str) -> str:
    value = (os.environ.get(name) or default).strip().lower()
    if value not in {"off", "shadow", "assist"}:
        return default
    return value


AI_ENABLED = _env_flag("AIXSTORE_AI_ENABLED", True)
AI_MODE = _env_mode("AIXSTORE_AI_MODE", "assist")
AI_PIPELINE_ENABLED = _env_flag("AIXSTORE_AI_PIPELINE_ENABLED", False)
AI_MODEL_ID = (
    os.environ.get("AIXSTORE_AI_MODEL")
    or os.environ.get("AIXSTORE_AI_MODEL_ID")
    or "tinyllama:latest"
).strip()
AI_OLLAMA_BASE_URL = os.environ.get("AIXSTORE_OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
AI_MAX_NEW_TOKENS = int(os.environ.get("AIXSTORE_AI_MAX_NEW_TOKENS", "96"))
AI_TEMPERATURE = float(os.environ.get("AIXSTORE_AI_TEMPERATURE", "0.0"))
AI_TOP_P = float(os.environ.get("AIXSTORE_AI_TOP_P", "0.9"))
AI_TIMEOUT_MS = int(os.environ.get("AIXSTORE_AI_TIMEOUT_MS", "1200"))
AI_COLD_START_TIMEOUT_MS = int(os.environ.get("AIXSTORE_AI_COLD_START_TIMEOUT_MS", "2500"))
AI_REWRITE_LIMIT = int(os.environ.get("AIXSTORE_AI_REWRITE_LIMIT", "5"))
AI_CACHE_TTL_SECONDS = int(os.environ.get("AIXSTORE_AI_CACHE_TTL_SECONDS", "604800"))
AI_CATEGORY_JUDGE_MIN_CONFIDENCE = float(os.environ.get("AIXSTORE_AI_CATEGORY_JUDGE_MIN_CONFIDENCE", "6.0"))


def ai_is_active() -> bool:
    return AI_ENABLED and AI_MODE in {"shadow", "assist"}


def ai_is_assist_mode() -> bool:
    return AI_ENABLED and AI_MODE == "assist"


def ai_pipeline_is_enabled() -> bool:
    return AI_ENABLED and AI_PIPELINE_ENABLED and AI_MODE in {"shadow", "assist"}
