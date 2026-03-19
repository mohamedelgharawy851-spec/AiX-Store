from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..utils import hash_text, normalize_whitespace, now_iso


def _get_connection():
    from ..storage import db as db_module

    return db_module.get_connection()


def build_rewrite_cache_key(normalized_query: str, category_id: str | None, model_id: str, prompt_version: str) -> str:
    raw = f"{normalized_query}|{category_id or 'all'}|{model_id}|{prompt_version}"
    return hash_text(raw)


def get_cached_rewrite(cache_key: str) -> dict[str, Any] | None:
    current_time = datetime.now(timezone.utc).isoformat()
    connection = _get_connection()
    try:
        row = connection.execute(
            """
            SELECT cache_key, normalized_query, category_id, model_id, prompt_version, rewrite_json, created_at, expires_at
            FROM ai_query_cache
            WHERE cache_key = ? AND expires_at > ?
            """,
            (cache_key, current_time),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    payload = dict(row)
    payload["rewrite_json"] = row["rewrite_json"] if isinstance(row["rewrite_json"], dict) else json.loads(str(row["rewrite_json"]))
    return payload


def save_rewrite_cache(
    *,
    cache_key: str,
    normalized_query: str,
    category_id: str | None,
    model_id: str,
    prompt_version: str,
    rewrite_payload: dict[str, Any],
    ttl_seconds: int,
) -> None:
    created_at = now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    connection = _get_connection()
    try:
        connection.execute(
            """
            INSERT OR REPLACE INTO ai_query_cache (
              cache_key, normalized_query, category_id, model_id, prompt_version, rewrite_json, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                normalize_whitespace(normalized_query).lower(),
                normalize_whitespace(category_id).lower() or None,
                model_id,
                prompt_version,
                json.dumps(rewrite_payload, ensure_ascii=True, separators=(",", ":")),
                created_at,
                expires_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()
