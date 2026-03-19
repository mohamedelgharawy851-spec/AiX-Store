from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv as _ld


def _dotenv_path() -> Path:
    current = Path(__file__).resolve()
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        return current.parents[2] / ".env"
    for candidate in current.parents:
        env_path = candidate / ".env"
        if env_path.is_file():
            return env_path
    return current.parents[2] / ".env"


_ld(_dotenv_path())

def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_csv(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value
    return default


APIFY_ENABLED = _env_flag("AIXSTORE_APIFY_ENABLED", True)
APIFY_TOKEN = _first_env(
    "AIXSTORE_APIFY_TOKEN",
    "APIFY_TOKEN",
    "APIFY_API_TOKEN",
    "AIXSTORE_APIFY_API_TOKEN",
).strip()
APIFY_BASE_URL = _first_env("AIXSTORE_APIFY_BASE_URL", "APIFY_BASE_URL", default="https://api.apify.com/v2").rstrip(
    "/"
)
APIFY_ACTOR_ID = _first_env(
    "AIXSTORE_APIFY_ACTOR_ID",
    "APIFY_ACTOR_ID",
    default="apify~google-search-scraper",
).strip()
APIFY_TIMEOUT_MS = int(os.environ.get("AIXSTORE_APIFY_TIMEOUT_MS", "30000"))
APIFY_RESULTS_PER_PAGE = int(os.environ.get("AIXSTORE_APIFY_RESULTS_PER_PAGE", "10"))
APIFY_MAX_PAGES_PER_QUERY = int(os.environ.get("AIXSTORE_APIFY_MAX_PAGES_PER_QUERY", "1"))
APIFY_COUNTRY = os.environ.get("AIXSTORE_APIFY_COUNTRY", "US").strip().lower() or "us"
APIFY_LANGUAGE = os.environ.get("AIXSTORE_APIFY_LANGUAGE", "en").strip().lower() or "en"
APIFY_DOMAIN = os.environ.get("AIXSTORE_APIFY_DOMAIN", "com").strip().lower() or "com"
APIFY_CACHE_TTL_SECONDS = int(os.environ.get("AIXSTORE_APIFY_CACHE_TTL_SECONDS", "21600"))
APIFY_MAX_VARIANTS = int(os.environ.get("AIXSTORE_APIFY_MAX_VARIANTS", "3"))
APIFY_MAX_URLS_PER_PROVIDER = int(os.environ.get("AIXSTORE_APIFY_MAX_URLS_PER_PROVIDER", "6"))
APIFY_TOTAL_BUDGET_MS = int(os.environ.get("AIXSTORE_APIFY_TOTAL_BUDGET_MS", "25000"))
APIFY_PROVIDER_EXTRACTION_TIMEOUT_MS = int(
    os.environ.get("AIXSTORE_APIFY_PROVIDER_EXTRACTION_TIMEOUT_MS", "30000")
)
APIFY_SUPPRESSION_THRESHOLD = int(os.environ.get("AIXSTORE_APIFY_SUPPRESSION_THRESHOLD", "2"))
DISCOVERY_ALLOWLIST = _env_csv("AIXSTORE_APIFY_ALLOWLIST", "amazon.com,walmart.com,target.com")
DISCOVERY_PROVIDER_NAME = "apify"
DISCOVERY_ENGINES = ["google-search-scraper"]
DISCOVERY_LOCALE = {
    "country": APIFY_COUNTRY.upper(),
    "language": APIFY_LANGUAGE,
    "domain": APIFY_DOMAIN or None,
}

DISCOVERY_PROVIDER_BY_DOMAIN = {
    "amazon.com": "amazon_requests",
    "walmart.com": "walmart_requests",
    "target.com": "target_requests",
}


def apify_configuration_error() -> str | None:
    if not APIFY_ENABLED:
        return "Apify discovery is disabled by AIXSTORE_APIFY_ENABLED."
    if not APIFY_TOKEN:
        return "Missing Apify token. Set AIXSTORE_APIFY_TOKEN or APIFY_TOKEN."
    if not APIFY_BASE_URL:
        return "Missing Apify base URL."
    if not APIFY_ACTOR_ID:
        return "Missing Apify actor id."
    return None


def apify_is_active() -> bool:
    return apify_configuration_error() is None


def discovery_is_active() -> bool:
    return apify_is_active()
