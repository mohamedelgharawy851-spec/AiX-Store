from __future__ import annotations

import hashlib
import json
from typing import Any

from ..storage.db import cache_discovery_response, get_cached_discovery_response


def build_discovery_cache_key(
    context_key: str,
    variant_text: str,
    category_id: str | None,
    request_identity: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "contextKey": context_key,
            "variant": variant_text,
            "categoryId": category_id,
            "requestIdentity": request_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def get_cached_discovery(cache_key: str) -> dict[str, Any] | None:
    return get_cached_discovery_response(cache_key)


def save_discovery_cache(cache_key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
    cache_discovery_response(cache_key=cache_key, payload=payload, ttl_seconds=ttl_seconds)
