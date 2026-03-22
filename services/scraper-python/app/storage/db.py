from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..ai.category_judge import judge_ambiguous_category
from ..ai.config import ai_pipeline_is_enabled
from ..config import AUTH_TOKEN_TTL_SECONDS, CATEGORY_CONFIG, CORE_CATEGORY_IDS, DB_PATH, PASSWORD_HASH_ITERATIONS
from ..providers.base import ProviderProduct, ProviderReview
from .postgres_compat import POSTGRES_SCHEMA_PATH, get_connection as get_postgres_connection, postgres_enabled
from ..utils import (
    category_name,
    classify_category,
    expand_query_variants,
    from_cents,
    json_dumps,
    normalize_offer_prices,
    normalize_whitespace,
    now_iso,
    product_id_for_url,
    singularize_token,
    slugify,
    tokenize,
    to_cents,
)


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_WRITE_LOCK = threading.RLock()
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_SERVICE_ROLE_KEY = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
MANUALLY_BLOCKED_PRODUCT_URLS = {
    "https://www.amazon.com/Bobtot-Theater-System-Surround-Speakers/dp/B09MRW83PZ",
}
ALLOWED_PRODUCT_VIEW_ORIGIN_SURFACES = {"home", "catalog", "favorites"}
VARIANT_COLOR_TERMS = {
    "black",
    "white",
    "blue",
    "red",
    "green",
    "pink",
    "purple",
    "yellow",
    "orange",
    "brown",
    "gray",
    "grey",
    "silver",
    "gold",
    "beige",
    "ivory",
    "navy",
    "teal",
    "multi",
}
VARIANT_SIZE_PATTERN = re.compile(r"\b(?:xxs|xs|s|m|l|xl|xxl|xxxl|one size|one-size)\b", re.IGNORECASE)
PACK_COUNT_PATTERN = re.compile(r"\b\d+\s*(?:pack|count|ct)\b", re.IGNORECASE)
FAMILY_NOISE_TERMS = {
    "pack",
    "count",
    "ct",
    "size",
    "color",
    "style",
    "new",
}
RELATED_TOKEN_STOPWORDS = {
    "with",
    "for",
    "and",
    "the",
    "smart",
    "wireless",
    "black",
    "white",
    "silver",
    "gold",
    "blue",
    "red",
    "green",
    "gray",
    "grey",
    "pink",
    "purple",
    "brown",
    "gps",
    "wifi",
    "case",
    "band",
    "size",
    "inch",
    "class",
    "series",
    "sery",
    "sport",
    "light",
    "aluminum",
    "rose",
    "electronics",
    "fashion",
    "beauty",
    "food",
    "home",
    "toys",
    "sports",
    "others",
}
FEATURED_OFFERS_LIMIT = 10
FEATURED_OFFER_MAX_PER_CATEGORY = 2


@contextmanager
def get_connection():
    if postgres_enabled():
        with get_postgres_connection() as conn:
            yield conn
            return
    
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _is_locked_error(exc: Exception) -> bool:
    return "database is locked" in str(exc).lower()


@contextmanager
def _write_connection():
    with _WRITE_LOCK:
        with get_connection() as connection:
            yield connection


def initialize_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _write_connection() as connection:
        if postgres_enabled():
            try:
                connection.executescript(POSTGRES_SCHEMA_PATH.read_text("utf-8"))
            except Exception as exc:
                connection.rollback()
                logging.getLogger(__name__).warning(
                    "Schema init skipped (already exists or timeout): %s",
                    exc,
                )
            _repair_invalid_product_pricing(connection)
            connection.commit()
            return
        else:
            connection.executescript(SCHEMA_PATH.read_text("utf-8"))
            migrated = _ensure_schema_migrations(connection)
        _repair_invalid_product_pricing(connection)
        _populate_variant_metadata(connection)
        if migrated:
            _reclassify_existing_products(connection)
            # Query/category feeds are derived caches and can become polluted
            # when schema or classification fields change.
            connection.execute("DELETE FROM query_products")
            connection.execute("DELETE FROM queries")
        connection.commit()

def _clear_image_cache_dir() -> int:
    image_dir = Path(DB_PATH).parent / "session-images"
    if not image_dir.exists():
        return 0
    deleted = 0
    for file_path in sorted(image_dir.rglob("*"), reverse=True):
        if file_path.is_file():
            file_path.unlink(missing_ok=True)
            deleted += 1
        elif file_path.is_dir():
            try:
                file_path.rmdir()
            except OSError:
                pass
    image_dir.mkdir(parents=True, exist_ok=True)
    return deleted


def _count_table_rows(connection: sqlite3.Connection, table_name: str, where_clause: str = "", params: tuple[Any, ...] = ()) -> int:
    query = f"SELECT COUNT(*) AS count FROM {table_name}"
    if where_clause:
        query = f"{query} WHERE {where_clause}"
    row = connection.execute(query, params).fetchone()
    return int(row["count"] or 0) if row else 0


def reset_product_linked_state(*, preserve_search_history: bool = True) -> dict[str, dict[str, int]]:
    deleted: dict[str, int] = {}
    with _WRITE_LOCK:
        with get_connection() as connection:
            deleted["query_products"] = _count_table_rows(connection, "query_products")
            deleted["queries"] = _count_table_rows(connection, "queries")
            deleted["reviews"] = _count_table_rows(connection, "reviews")
            deleted["related_products"] = _count_table_rows(connection, "related_products")
            deleted["discovery_cache"] = _count_table_rows(connection, "discovery_cache")
            deleted["discovery_hits"] = _count_table_rows(connection, "discovery_hits")
            deleted["discovery_queries"] = _count_table_rows(connection, "discovery_queries")
            deleted["discovery_suppression"] = _count_table_rows(connection, "discovery_suppression")
            deleted["featured_offer_snapshots"] = _count_table_rows(connection, "featured_offer_snapshots")
            deleted["products"] = _count_table_rows(connection, "products")
            deleted["user_favorites"] = _count_table_rows(connection, "user_favorites")
            deleted["user_recommendations"] = _count_table_rows(connection, "user_recommendations")
            if preserve_search_history:
                event_where = (
                    "event_type IN ('product_view', 'source_open') OR product_id IS NOT NULL OR canonical_source_url IS NOT NULL"
                )
                deleted["user_events_product_linked"] = _count_table_rows(connection, "user_events", event_where)
            else:
                deleted["user_events_product_linked"] = _count_table_rows(connection, "user_events")

            connection.execute("DELETE FROM query_products")
            connection.execute("DELETE FROM queries")
            connection.execute("DELETE FROM reviews")
            connection.execute("DELETE FROM related_products")
            connection.execute("DELETE FROM discovery_cache")
            connection.execute("DELETE FROM discovery_hits")
            connection.execute("DELETE FROM discovery_queries")
            connection.execute("DELETE FROM discovery_suppression")
            connection.execute("DELETE FROM featured_offer_snapshots")
            connection.execute("DELETE FROM user_favorites")
            connection.execute("DELETE FROM user_recommendations")
            if preserve_search_history:
                connection.execute(
                    """
                    DELETE FROM user_events
                    WHERE event_type IN ('product_view', 'source_open')
                       OR product_id IS NOT NULL
                       OR canonical_source_url IS NOT NULL
                    """
                )
            else:
                connection.execute("DELETE FROM user_events")
            connection.execute("DELETE FROM products")
            connection.commit()
            if not postgres_enabled():
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.commit()
                connection.execute("VACUUM")
                connection.commit()

        deleted["cached_images"] = _clear_image_cache_dir()
    return {"deleted": deleted}


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if postgres_enabled():
        rows = connection.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table_name,),
        ).fetchall()
        return {str(row["name"]) for row in rows}
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_schema_migrations(connection: sqlite3.Connection) -> bool:
    if postgres_enabled():
        return False
    migrated = False
    product_columns = _table_columns(connection, "products")
    product_column_defs = {
        "source_category_id": "TEXT",
        "source_category": "TEXT",
        "canonical_category_id": "TEXT NOT NULL DEFAULT 'others'",
        "canonical_category": "TEXT NOT NULL DEFAULT 'Others'",
        "category_confidence": "REAL NOT NULL DEFAULT 0",
        "category_scores_json": "TEXT NOT NULL DEFAULT '{}'",
        "matched_terms_json": "TEXT NOT NULL DEFAULT '[]'",
        "category_source": "TEXT NOT NULL DEFAULT 'rules'",
        "ai_category_id": "TEXT",
        "ai_category_confidence": "REAL",
        "ai_category_reason": "TEXT",
        "ai_category_updated_at": "TEXT",
        "image_gallery_json": "TEXT NOT NULL DEFAULT '[]'",
        "family_key": "TEXT",
        "variant_label": "TEXT",
        "variant_attributes_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for column_name, column_def in product_column_defs.items():
        if column_name not in product_columns:
            connection.execute(f"ALTER TABLE products ADD COLUMN {column_name} {column_def}")
            migrated = True

    query_columns = _table_columns(connection, "queries")
    query_column_defs = {
        "query_kind": "TEXT NOT NULL DEFAULT 'search'",
        "category_id": "TEXT",
        "query_variants_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    for column_name, column_def in query_column_defs.items():
        if column_name not in query_columns:
            connection.execute(f"ALTER TABLE queries ADD COLUMN {column_name} {column_def}")
            migrated = True

    user_event_columns = _table_columns(connection, "user_events")
    if "session_id" not in user_event_columns:
        connection.execute("ALTER TABLE user_events ADD COLUMN session_id TEXT")
        migrated = True
    user_event_column_defs = {
        "canonical_source_url": "TEXT",
        "product_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for column_name, column_def in user_event_column_defs.items():
        if column_name not in user_event_columns:
            connection.execute(f"ALTER TABLE user_events ADD COLUMN {column_name} {column_def}")
            migrated = True

    discovery_query_columns = _table_columns(connection, "discovery_queries")
    discovery_query_defs = {
        "provider": "TEXT NOT NULL DEFAULT 'apify'",
        "request_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for column_name, column_def in discovery_query_defs.items():
        if column_name not in discovery_query_columns:
            connection.execute(f"ALTER TABLE discovery_queries ADD COLUMN {column_name} {column_def}")
            migrated = True

    discovery_hit_columns = _table_columns(connection, "discovery_hits")
    discovery_hit_defs = {
        "source": "TEXT",
        "source_title": "TEXT",
        "source_snippet": "TEXT",
        "source_rank": "INTEGER",
    }
    for column_name, column_def in discovery_hit_defs.items():
        if column_name not in discovery_hit_columns:
            connection.execute(f"ALTER TABLE discovery_hits ADD COLUMN {column_name} {column_def}")
            migrated = True

    connection.execute("CREATE INDEX IF NOT EXISTS idx_queries_kind_category ON queries(query_kind, category_id)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_events_user_session_created ON user_events(user_id, session_id, created_at DESC)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_favorites_user_url ON user_favorites(user_id, canonical_source_url)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_favorites_user_created ON user_favorites(user_id, created_at DESC)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS featured_offer_snapshots (
          period_key TEXT PRIMARY KEY,
          product_ids_json TEXT NOT NULL,
          generated_at TEXT NOT NULL,
          expires_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_featured_offer_snapshots_expires ON featured_offer_snapshots(expires_at)"
    )
    return migrated


def _reclassify_existing_products(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT id, title, description, brand, tags_json, source_category_id, category_id
        FROM products
        """
    ).fetchall()
    for row in rows:
        tags = _decode_json_array(row["tags_json"])
        source_category_id = normalize_whitespace(row["source_category_id"]) or str(row["category_id"])
        classification = classify_category(
            row["title"],
            row["description"],
            row["brand"],
            source_category_id=source_category_id,
            extra_terms=tags,
        )
        connection.execute(
            """
            UPDATE products
            SET source_category_id = COALESCE(source_category_id, ?),
                source_category = COALESCE(source_category, ?),
                canonical_category_id = ?,
                canonical_category = ?,
                category_confidence = ?,
                category_scores_json = ?,
                matched_terms_json = ?,
                category_id = ?,
                category = ?,
                category_source = 'rules',
                ai_category_id = NULL,
                ai_category_confidence = NULL,
                ai_category_reason = NULL,
                ai_category_updated_at = NULL
            WHERE id = ?
            """,
            (
                source_category_id,
                category_name(source_category_id),
                classification["category_id"],
                classification["category"],
                classification["confidence"],
                json_dumps(classification["scores"]),
                json_dumps(classification["matched_terms"]),
                classification["category_id"],
                classification["category"],
                row["id"],
            ),
        )


def _populate_variant_metadata(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT id, provider, title, brand, raw_json, source_image_url, image_gallery_json
        FROM products
        """
    ).fetchall()
    for row in rows:
        raw_json = _decode_json_object(row["raw_json"])
        attributes = _extract_variant_attributes(row["title"], raw_json)
        family_key = _build_family_key(str(row["provider"]), str(row["title"]), row["brand"], raw_json, attributes)
        variant_label = _build_variant_label(attributes, raw_json)
        gallery_urls = _merge_image_gallery_urls(
            row["source_image_url"],
            _decode_json_array(row["image_gallery_json"]),
        )
        connection.execute(
            """
            UPDATE products
            SET family_key = ?,
                variant_label = ?,
                variant_attributes_json = ?,
                image_gallery_json = ?
            WHERE id = ?
            """,
            (
                family_key,
                variant_label,
                json_dumps(attributes),
                json_dumps(gallery_urls),
                row["id"],
            ),
        )


def _repair_invalid_product_pricing(connection: sqlite3.Connection) -> None:
    invalid_zero_price_rows = connection.execute(
        """
        SELECT id
        FROM products
        WHERE is_active = 1 AND price_cents <= 0
        """
    ).fetchall()
    invalid_zero_price_ids = [str(row["id"]) for row in invalid_zero_price_rows if row["id"]]
    blocked_query = "SELECT id FROM products WHERE 0"
    blocked_params: tuple[str, ...] = ()
    if MANUALLY_BLOCKED_PRODUCT_URLS:
        blocked_query = f"""
        SELECT id
        FROM products
        WHERE canonical_source_url IN ({",".join("?" for _ in MANUALLY_BLOCKED_PRODUCT_URLS)})
        """
        blocked_params = tuple(MANUALLY_BLOCKED_PRODUCT_URLS)
    blocked_product_rows = connection.execute(blocked_query, blocked_params).fetchall()
    invalid_zero_price_ids.extend(str(row["id"]) for row in blocked_product_rows if row["id"])
    invalid_zero_price_ids = sorted(set(invalid_zero_price_ids))
    if invalid_zero_price_ids:
        placeholders = ",".join("?" for _ in invalid_zero_price_ids)
        current_time = now_iso()
        connection.execute(
            f"UPDATE products SET is_active = 0, updated_at = ? WHERE id IN ({placeholders})",
            (current_time, *invalid_zero_price_ids),
        )
        connection.execute(f"DELETE FROM query_products WHERE product_id IN ({placeholders})", invalid_zero_price_ids)
        connection.execute(
            f"DELETE FROM related_products WHERE product_id IN ({placeholders}) OR related_product_id IN ({placeholders})",
            [*invalid_zero_price_ids, *invalid_zero_price_ids],
        )
        connection.execute(f"DELETE FROM user_favorites WHERE product_id IN ({placeholders})", invalid_zero_price_ids)
        connection.execute(f"DELETE FROM user_recommendations WHERE product_id IN ({placeholders})", invalid_zero_price_ids)

    connection.execute(
        """
        UPDATE products
        SET original_price_cents = NULL,
            updated_at = ?
        WHERE original_price_cents IS NOT NULL
          AND (
            original_price_cents <= price_cents
            OR price_cents <= 0
            OR ((original_price_cents - price_cents) * 100.0 / original_price_cents) >= 95
          )
        """,
        (now_iso(),),
    )


def _now_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _decode_json_array(value: str | list[Any] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [normalize_whitespace(str(item)) for item in value if normalize_whitespace(str(item))]
    try:
        data = json.loads(value) if isinstance(value, str) else value
        return [normalize_whitespace(item) for item in data if normalize_whitespace(item)]
    except Exception:
        return []


def _normalize_lookup_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_whitespace(value).lower())


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = normalize_whitespace(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _merge_image_gallery_urls(primary_url: str | None, *collections: list[str] | None) -> list[str]:
    ordered: list[str] = []
    if primary_url:
        ordered.append(primary_url)
    for collection in collections:
        ordered.extend(collection or [])
    return _dedupe_strings(ordered)


def _decode_image_gallery(value: str | None, primary_url: str | None = None) -> list[str]:
    urls = _decode_json_array(value)
    return _merge_image_gallery_urls(primary_url, urls)


def _find_nested_value(payload: Any, candidate_keys: set[str]) -> str | None:
    queue: list[Any] = [payload]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key, value in current.items():
                normalized_key = _normalize_lookup_key(str(key))
                if normalized_key in candidate_keys and value is not None and not isinstance(value, (dict, list)):
                    normalized_value = normalize_whitespace(str(value))
                    if normalized_value and normalized_value.lower() not in {"none", "null", "n/a", "na"}:
                        return normalized_value
                queue.append(value)
        elif isinstance(current, list):
            queue.extend(current)
    return None


def _extract_variant_attributes(title: str, raw_json: dict[str, Any] | None) -> dict[str, str]:
    payload = raw_json or {}
    lowered_title = normalize_whitespace(title).lower()
    attributes: dict[str, str] = {}

    explicit_color = _find_nested_value(payload, {"color", "colorname", "selectedcolor", "variantcolor", "swatchcolor"})
    explicit_size = _find_nested_value(payload, {"size", "sizename", "selectedsize"})
    explicit_style = _find_nested_value(payload, {"style", "stylename", "selectedstyle", "flavor", "scent"})

    if explicit_color:
        attributes["color"] = explicit_color
    else:
        for color in VARIANT_COLOR_TERMS:
            if re.search(rf"\b{re.escape(color)}\b", lowered_title):
                attributes["color"] = color.title()
                break

    if explicit_size:
        attributes["size"] = explicit_size
    else:
        match = VARIANT_SIZE_PATTERN.search(lowered_title)
        if match:
            attributes["size"] = normalize_whitespace(match.group(0)).upper().replace("ONE-SIZE", "One Size")

    if explicit_style:
        attributes["style"] = explicit_style

    return {key: value for key, value in attributes.items() if normalize_whitespace(value)}


def _build_variant_label(attributes: dict[str, str], raw_json: dict[str, Any] | None) -> str | None:
    explicit_label = _find_nested_value(raw_json or {}, {"variantlabel", "variantname"})
    if explicit_label:
        return explicit_label
    ordered_values = [attributes.get(key, "") for key in ("color", "size", "style")]
    label = " / ".join(value for value in ordered_values if value)
    return label or None


def _build_family_key(provider: str, title: str, brand: str | None, raw_json: dict[str, Any] | None, attributes: dict[str, str]) -> str:
    payload = raw_json or {}
    native_identifier = _find_nested_value(
        payload,
        {
            "parentid",
            "parenttcin",
            "parentasin",
            "groupid",
            "variantgroupid",
            "itemgroupid",
            "styleparentid",
        },
    )
    provider_slug = slugify(provider) or "product"
    if native_identifier:
        digest = hashlib.sha1(f"{provider_slug}:{native_identifier}".encode("utf-8")).hexdigest()[:16]
        return f"{provider_slug}:{digest}"

    cleaned_title = normalize_whitespace(title).lower()
    for value in attributes.values():
        candidate = normalize_whitespace(value).lower()
        if not candidate:
            continue
        cleaned_title = re.sub(rf"\b{re.escape(candidate)}\b", " ", cleaned_title)
    cleaned_title = PACK_COUNT_PATTERN.sub(" ", cleaned_title)
    cleaned_title = VARIANT_SIZE_PATTERN.sub(" ", cleaned_title)
    tokens = [
        token
        for token in tokenize(cleaned_title, brand)
        if token not in VARIANT_COLOR_TERMS and token not in FAMILY_NOISE_TERMS
    ]
    base_text = " ".join(tokens[:12]) or normalize_whitespace(title).lower()
    digest = hashlib.sha1(f"{provider_slug}:{normalize_whitespace(brand).lower()}:{base_text}".encode("utf-8")).hexdigest()[:16]
    return f"{provider_slug}:{digest}"


def _row_identity_key(row: sqlite3.Row) -> str:
    family_key = normalize_whitespace(row["family_key"])
    provider = normalize_whitespace(row["provider"]).lower()
    if family_key and provider:
        return f"{provider}::{family_key}"
    return str(row["id"])


def _row_sort_key(row: sqlite3.Row) -> tuple[int, float, str, int]:
    return (
        int(row["review_count"] or 0),
        float(row["rating"] or 0.0),
        normalize_whitespace(row["updated_at"]),
        1 if row["original_price_cents"] is not None else 0,
    )


def _dedupe_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    selected: dict[str, sqlite3.Row] = {}
    order: list[str] = []
    for row in rows:
        key = _row_identity_key(row)
        if key not in selected:
            selected[key] = row
            order.append(key)
            continue
        if _row_sort_key(row) > _row_sort_key(selected[key]):
            selected[key] = row
    return [selected[key] for key in order]


def _product_identity_key(product: dict[str, Any]) -> str:
    family_key = normalize_whitespace(product.get("familyKey"))
    provider = normalize_whitespace(product.get("provider")).lower()
    if family_key and provider:
        return f"{provider}::{family_key}"
    return str(product.get("id") or "")


def _product_sort_key(product: dict[str, Any]) -> tuple[int, float, str, int]:
    return (
        int(product.get("reviewCount") or 0),
        float(product.get("rating") or 0.0),
        normalize_whitespace(product.get("createdAt") or ""),
        1 if product.get("originalPrice") is not None else 0,
    )


def _dedupe_product_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = _product_identity_key(item)
        if not key:
            continue
        if key not in selected:
            selected[key] = item
            order.append(key)
            continue
        if _product_sort_key(item) > _product_sort_key(selected[key]):
            selected[key] = item
    return [selected[key] for key in order]


def _product_to_row(product: ProviderProduct, image_meta: dict[str, Any]) -> dict[str, Any]:
    normalized_price, normalized_original_price = normalize_offer_prices(product.price, product.original_price)
    if normalized_price is None:
        return {}
    canonical_source_url = normalize_whitespace(product.canonical_source_url or product.source_url)
    if canonical_source_url in MANUALLY_BLOCKED_PRODUCT_URLS:
        return {}
    product_id = product_id_for_url(canonical_source_url)
    title = normalize_whitespace(product.title)
    description = normalize_whitespace(product.description) or title
    tags = product.tags or tokenize(title, description, product.brand)
    raw_json = product.raw_json or {}
    variant_attributes = _extract_variant_attributes(title, raw_json)
    variant_label = normalize_whitespace(product.variant_label) or _build_variant_label(variant_attributes, raw_json)
    family_key = normalize_whitespace(product.family_key) or _build_family_key(
        product.provider,
        title,
        product.brand,
        raw_json,
        variant_attributes,
    )
    image_gallery_urls = _merge_image_gallery_urls(product.source_image_url, product.image_gallery_urls)
    classification = classify_category(
        title,
        description,
        product.brand,
        source_category_id=product.category_id,
        extra_terms=tags,
    )
    ai_judgment = {
        "invoked": False,
        "used": False,
        "category_id": str(classification.get("category_id", "others")),
        "category": category_name(str(classification.get("category_id", "others"))),
        "category_source": "rules",
        "ai_category_id": None,
        "ai_category_confidence": None,
        "ai_category_reason": None,
        "ai_category_updated_at": None,
    }
    if ai_pipeline_is_enabled():
        ai_judgment = judge_ambiguous_category(
            title=title,
            description=description,
            brand=product.brand,
            tags=tags,
            provider_name=product.provider,
            source_category_id=product.category_id,
            rule_classification=classification,
        )
    final_category_id = str(ai_judgment["category_id"])
    current_time = now_iso()
    return {
        "id": product_id,
        "provider": product.provider,
        "source_url": product.source_url,
        "canonical_source_url": canonical_source_url,
        "title": title,
        "slug": slugify(f"{title}-{product_id}") or product_id,
        "description": description,
        "price_cents": to_cents(normalized_price) or 0,
        "original_price_cents": to_cents(normalized_original_price),
        "currency": product.currency or "USD",
        "rating": float(product.rating or 0.0),
        "review_count": int(product.review_count or 0),
        "source_category_id": product.category_id,
        "source_category": category_name(product.category_id),
        "canonical_category_id": final_category_id,
        "canonical_category": category_name(final_category_id),
        "category_confidence": float(
            ai_judgment["ai_category_confidence"]
            if ai_judgment["used"] and ai_judgment["ai_category_confidence"] is not None
            else classification["confidence"]
        ),
        "category_scores_json": json_dumps(classification["scores"]),
        "matched_terms_json": json_dumps(classification["matched_terms"]),
        "category_id": final_category_id,
        "category": category_name(final_category_id),
        "category_source": str(ai_judgment["category_source"]),
        "ai_category_id": ai_judgment["ai_category_id"],
        "ai_category_confidence": ai_judgment["ai_category_confidence"],
        "ai_category_reason": ai_judgment["ai_category_reason"],
        "ai_category_updated_at": ai_judgment["ai_category_updated_at"],
        "brand": normalize_whitespace(product.brand) or None,
        "source_image_url": product.source_image_url,
        "image_gallery_json": json_dumps(image_gallery_urls),
        "family_key": family_key,
        "variant_label": variant_label,
        "variant_attributes_json": json_dumps(variant_attributes),
        "local_image_key": image_meta["local_image_key"],
        "image_mime": image_meta["image_mime"],
        "image_width": int(image_meta["image_width"]),
        "image_height": int(image_meta["image_height"]),
        "tags_json": json_dumps(tags),
        "raw_json": json_dumps(raw_json),
        "created_at": current_time,
        "updated_at": current_time,
        "last_verified_at": current_time,
        "is_active": 1,
        "_ai_category_judge_used": bool(ai_judgment["invoked"]),
        "_ai_category_applied": bool(ai_judgment["used"]),
    }


def _row_to_product(row: sqlite3.Row) -> dict[str, Any]:
    has_reviews = int(row["review_count"] or 0) > 0
    price = from_cents(row["price_cents"])
    _, original_price = normalize_offer_prices(
        price,
        from_cents(row["original_price_cents"]) if row["original_price_cents"] else None,
    )
    variant_attributes = {
        key: value
        for key, value in _decode_json_object(row["variant_attributes_json"]).items()
        if normalize_whitespace(str(key)) and normalize_whitespace(str(value))
    }
    variant_label = normalize_whitespace(row["variant_label"]) or None
    gallery_urls = _decode_image_gallery(row["image_gallery_json"], row["source_image_url"])
    image_gallery = [
        {
            "id": f"{row['id']}:img:{index}",
            "url": url,
            "altText": row["title"],
            "variantLabel": variant_label,
        }
        for index, url in enumerate(gallery_urls)
    ]
    return {
        "id": row["id"],
        "slug": row["slug"],
        "provider": row["provider"],
        "name": row["title"],
        "categoryId": row["category_id"],
        "category": row["category"],
        "categorySource": row["category_source"],
        "sourceCategoryId": row["source_category_id"],
        "categoryConfidence": float(row["category_confidence"] or 0.0),
        "description": row["description"],
        "price": price,
        "originalPrice": original_price,
        "currency": row["currency"],
        "rating": float(row["rating"] or 0.0),
        "imageUrl": row["source_image_url"],
        "imageAltText": row["title"],
        "reviewCount": int(row["review_count"] or 0),
        "sourceSite": row["provider"],
        "sourceUrl": row["source_url"],
        "sourceImageUrl": row["source_image_url"],
        "imageGallery": image_gallery,
        "familyKey": normalize_whitespace(row["family_key"]) or None,
        "variantLabel": variant_label,
        "variantAttributes": variant_attributes,
        "localImageKey": row["local_image_key"],
        "hasReviews": has_reviews,
        "tags": _decode_json_array(row["tags_json"]),
        "brand": row["brand"],
        "createdAt": row["created_at"],
    }


def _decode_json_object(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _snapshot_from_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": product.get("id"),
        "slug": product.get("slug"),
        "provider": product.get("provider"),
        "name": product.get("name"),
        "categoryId": product.get("categoryId"),
        "category": product.get("category"),
        "description": product.get("description"),
        "price": product.get("price"),
        "originalPrice": product.get("originalPrice"),
        "currency": product.get("currency"),
        "rating": product.get("rating"),
        "imageUrl": product.get("imageUrl"),
        "imageAltText": product.get("imageAltText"),
        "reviewCount": product.get("reviewCount"),
        "hasReviews": product.get("hasReviews"),
        "tags": product.get("tags") or [],
        "sourceSite": product.get("sourceSite"),
        "sourceUrl": product.get("sourceUrl"),
        "sourceImageUrl": product.get("sourceImageUrl"),
        "imageGallery": product.get("imageGallery") or [],
        "familyKey": product.get("familyKey"),
        "variantLabel": product.get("variantLabel"),
        "variantAttributes": product.get("variantAttributes") or {},
        "brand": product.get("brand"),
        "createdAt": product.get("createdAt"),
    }


def _snapshot_from_row(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    return _snapshot_from_product(_row_to_product(row))


def _restore_snapshot(value: str | None) -> dict[str, Any] | None:
    snapshot = _decode_json_object(value)
    return snapshot or None


def favorite_product_ids_for_user(user_id: str, product_ids: list[str], connection: sqlite3.Connection | None = None) -> set[str]:
    if not user_id or not product_ids:
        return set()
    if connection is None:
        with get_connection() as conn:
            return favorite_product_ids_for_user(user_id, product_ids, connection=conn)

    placeholders = ",".join("?" for _ in product_ids)
    rows = connection.execute(
        f"""
        SELECT product_id
        FROM user_favorites
        WHERE user_id = ? AND product_id IN ({placeholders})
        """,
        [user_id, *product_ids],
    ).fetchall()
    return {str(row["product_id"]) for row in rows if row["product_id"]}


def annotate_products_with_favorites(
    items: list[dict[str, Any]], user_id: str | None, connection: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    if not items:
        return items
    product_ids = [str(item.get("id")) for item in items if item.get("id")]
    favorite_ids = favorite_product_ids_for_user(user_id or "", product_ids, connection=connection) if user_id else set()
    for item in items:
        item["isFavorite"] = bool(user_id and item.get("id") and str(item.get("id")) in favorite_ids)
    return items


def _build_categories(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    counts = {
        row["category_id"]: int(row["count"])
        for row in connection.execute(
            "SELECT category_id, COUNT(*) AS count FROM products WHERE is_active = 1 GROUP BY category_id"
        ).fetchall()
    }
    categories = []
    for category_id, config in CATEGORY_CONFIG.items():
        count = counts.get(category_id, 0)
        if not count:
            continue
        categories.append(
            {
                "id": category_id,
                "name": config["name"],
                "shortLabel": str(config["name"])[:2].upper(),
                "color": config["color"],
                "icon": config["icon"],
                "count": count,
            }
        )
    return categories


def _offer_snapshot_period_key(current_time: datetime | None = None) -> str:
    return (current_time or _now_datetime()).date().isoformat()


def _discount_percentage_from_row(row: sqlite3.Row | dict[str, Any]) -> float:
    price_cents = int(_record_value(row, "price_cents", "priceCents") or 0)
    original_price_cents = int(_record_value(row, "original_price_cents", "originalPriceCents") or 0)
    if price_cents <= 0 or original_price_cents <= price_cents:
        return 0.0
    return ((original_price_cents - price_cents) * 100.0) / max(original_price_cents, 1)


def _offer_recency_score(row: sqlite3.Row | dict[str, Any]) -> float:
    reference = _parse_iso(_record_value(row, "last_verified_at", "updated_at", "lastVerifiedAt", "updatedAt"))
    if not reference:
        return 0.0
    age_hours = max((_now_datetime() - reference).total_seconds() / 3600.0, 0.0)
    return max(0.0, 1.5 - min(age_hours, 72.0) / 48.0)


def _offer_daily_bias(period_key: str, product_id: str) -> float:
    digest = hashlib.sha1(f"{period_key}:{product_id}".encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 1000) / 1000.0


def _offer_snapshot_expiry(period_key: str) -> str:
    expires_at = _parse_iso(f"{period_key}T00:00:00+00:00") or _now_datetime()
    return (expires_at + timedelta(days=1)).isoformat()


def _discounted_product_rows(connection: sqlite3.Connection, limit: int = 600) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT *
        FROM products
        WHERE is_active = 1
          AND price_cents > 0
          AND original_price_cents IS NOT NULL
          AND original_price_cents > price_cents
          AND ((original_price_cents - price_cents) * 100.0 / original_price_cents) < 95
        ORDER BY updated_at DESC, rating DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _load_featured_offer_rows_from_ids(
    connection: sqlite3.Connection,
    product_ids: list[str],
    *,
    limit: int,
) -> list[sqlite3.Row] | None:
    deduped_ids = [product_id for product_id in dict.fromkeys(product_ids) if normalize_whitespace(product_id)]
    if not deduped_ids:
        return None
    placeholders = ",".join("?" for _ in deduped_ids)
    rows = connection.execute(
        f"""
        SELECT *
        FROM products
        WHERE id IN ({placeholders})
          AND is_active = 1
          AND price_cents > 0
          AND original_price_cents IS NOT NULL
          AND original_price_cents > price_cents
          AND ((original_price_cents - price_cents) * 100.0 / original_price_cents) < 95
        """,
        deduped_ids,
    ).fetchall()
    row_by_id = {str(row["id"]): row for row in rows if row["id"]}
    ordered_rows: list[sqlite3.Row] = []
    for product_id in deduped_ids[:limit]:
        row = row_by_id.get(product_id)
        if not row:
            return None
        ordered_rows.append(row)
    return ordered_rows


def _generate_featured_offer_product_ids(connection: sqlite3.Connection, period_key: str, limit: int) -> list[str]:
    candidates = _discounted_product_rows(connection)
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in candidates:
        discount_percentage = _discount_percentage_from_row(row)
        if discount_percentage <= 0 or discount_percentage >= 95:
            continue
        rating_score = float(row["rating"] or 0.0) * 2.2
        review_score = min(int(row["review_count"] or 0), 250) / 18.0
        freshness_score = _offer_recency_score(row) * 8.0
        confidence_score = min(float(row["category_confidence"] or 0.0), 1.0) * 2.0
        daily_bias = _offer_daily_bias(period_key, str(row["id"]))
        scored.append(
            (
                (discount_percentage * 1.45) + rating_score + review_score + freshness_score + confidence_score + daily_bias,
                row,
            )
        )
    scored.sort(key=lambda item: (item[0], float(item[1]["rating"] or 0.0), normalize_whitespace(item[1]["updated_at"])), reverse=True)

    selected_ids: list[str] = []
    category_counts: dict[str, int] = {}
    seen_family_keys: set[str] = set()
    for _, row in scored:
        category_id = normalize_whitespace(row["category_id"]).lower()
        if category_id and category_counts.get(category_id, 0) >= FEATURED_OFFER_MAX_PER_CATEGORY:
            continue
        family_key = normalize_whitespace(row["family_key"])
        if family_key and family_key in seen_family_keys:
            continue
        selected_ids.append(str(row["id"]))
        if category_id:
            category_counts[category_id] = category_counts.get(category_id, 0) + 1
        if family_key:
            seen_family_keys.add(family_key)
        if len(selected_ids) >= limit:
            break
    return selected_ids


def _store_featured_offer_snapshot(connection: sqlite3.Connection, period_key: str, product_ids: list[str]) -> None:
    current_time = now_iso()
    connection.execute(
        """
        INSERT INTO featured_offer_snapshots (period_key, product_ids_json, generated_at, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(period_key) DO UPDATE SET
          product_ids_json = excluded.product_ids_json,
          generated_at = excluded.generated_at,
          expires_at = excluded.expires_at
        """,
        (
            period_key,
            json_dumps(product_ids),
            current_time,
            _offer_snapshot_expiry(period_key),
        ),
    )


def _build_offers(connection: sqlite3.Connection, limit: int = FEATURED_OFFERS_LIMIT) -> list[dict[str, Any]]:
    current_time = _now_datetime()
    period_key = _offer_snapshot_period_key(current_time)
    snapshot_row = connection.execute(
        """
        SELECT product_ids_json, expires_at
        FROM featured_offer_snapshots
        WHERE period_key = ?
        """,
        (period_key,),
    ).fetchone()

    if snapshot_row:
        expires_at = _parse_iso(snapshot_row["expires_at"])
        if expires_at and expires_at > current_time:
            cached_rows = _load_featured_offer_rows_from_ids(
                connection,
                _decode_json_array(snapshot_row["product_ids_json"]),
                limit=limit,
            )
            if cached_rows:
                return [_row_to_product(row) for row in cached_rows[:limit]]

    product_ids = _generate_featured_offer_product_ids(connection, period_key, limit)
    if not product_ids:
        return []
    _store_featured_offer_snapshot(connection, period_key, product_ids)
    rows = _load_featured_offer_rows_from_ids(connection, product_ids, limit=limit) or []
    return [_row_to_product(row) for row in rows[:limit]]


def _interleaved_products(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    rows = _dedupe_rows(rows)
    queues: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        queues.setdefault(str(row["category_id"]), []).append(row)
    ordered: list[dict[str, Any]] = []
    while any(queues.values()):
        for category_id in list(queues.keys()):
            if queues[category_id]:
                ordered.append(_row_to_product(queues[category_id].pop(0)))
    return ordered


def _catalog_response(
    connection: sqlite3.Connection,
    items: list[dict[str, Any]],
    total: int,
    page: int,
    page_size: int,
    context_key: str = "home",
    context_type: str = "home",
    applied_query: str | None = None,
    applied_category_id: str | None = None,
    strict_category: bool = False,
    query_variants: list[str] | None = None,
    matching: dict[str, Any] | None = None,
    enrichment: dict[str, Any] | None = None,
    dedupe_items: bool = True,
) -> dict[str, Any]:
    response_items = _dedupe_product_list(items) if dedupe_items else items
    return {
        "contextKey": context_key,
        "contextType": context_type,
        "appliedQuery": applied_query,
        "appliedCategoryId": applied_category_id,
        "strictCategory": strict_category,
        "items": response_items,
        "offers": _build_offers(connection),
        "categories": _build_categories(connection),
        "total": total,
        "page": page,
        "pageSize": page_size,
        "hasMore": page * page_size < total,
        "queryVariants": query_variants or [],
        "matching": matching
        or {
            "source": "home" if context_type == "home" else "cached_fallback",
            "exactMatchCount": len(items),
            "filteredOutCount": 0,
        },
        "enrichment": enrichment
        or {
            "state": "idle",
            "sourceProviders": [],
            "lastUpdatedAt": None,
            "message": None,
        },
    }


def upsert_products(
    products: list[ProviderProduct],
    image_meta_by_url: dict[str, dict[str, Any]],
    *,
    with_meta: bool = False,
) -> list[str] | dict[str, Any]:
    if not products:
        return {"productIds": [], "aiCategoryJudgeUsed": False, "aiCategoryApplied": False} if with_meta else []
    product_ids: list[str] = []
    ai_category_judge_used = False
    ai_category_applied = False
    with _write_connection() as connection:
        for product in products:
            image_meta = image_meta_by_url.get(product.source_image_url)
            if not image_meta:
                continue
            row = _product_to_row(product, image_meta)
            if not row:
                continue
            existing_row = connection.execute(
                "SELECT image_gallery_json, family_key, variant_label, variant_attributes_json FROM products WHERE canonical_source_url = ?",
                (row["canonical_source_url"],),
            ).fetchone()
            if existing_row:
                row["image_gallery_json"] = json_dumps(
                    _merge_image_gallery_urls(
                        row["source_image_url"],
                        _decode_json_array(existing_row["image_gallery_json"]),
                        _decode_json_array(row["image_gallery_json"]),
                    )
                )
                if not row["family_key"]:
                    row["family_key"] = normalize_whitespace(existing_row["family_key"]) or None
                if not row["variant_label"]:
                    row["variant_label"] = normalize_whitespace(existing_row["variant_label"]) or None
                existing_variant_attributes = _decode_json_object(existing_row["variant_attributes_json"])
                if existing_variant_attributes:
                    merged_variant_attributes = {
                        **existing_variant_attributes,
                        **_decode_json_object(row["variant_attributes_json"]),
                    }
                    row["variant_attributes_json"] = json_dumps(merged_variant_attributes)
            ai_category_judge_used = ai_category_judge_used or bool(row.get("_ai_category_judge_used"))
            ai_category_applied = ai_category_applied or bool(row.get("_ai_category_applied"))
            product_ids.append(row["id"])
            connection.execute(
                """
                INSERT INTO products (
                  id, provider, source_url, canonical_source_url, title, slug, description,
                  price_cents, original_price_cents, currency, rating, review_count, source_category_id,
                  source_category, canonical_category_id, canonical_category, category_confidence, category_scores_json,
                  matched_terms_json, category_id, category, category_source, ai_category_id, ai_category_confidence,
                  ai_category_reason, ai_category_updated_at, brand, source_image_url, image_gallery_json, family_key,
                  variant_label, variant_attributes_json, local_image_key, image_mime, image_width,
                  image_height, tags_json, raw_json, created_at, updated_at, last_verified_at, is_active
                ) VALUES (
                  :id, :provider, :source_url, :canonical_source_url, :title, :slug, :description,
                  :price_cents, :original_price_cents, :currency, :rating, :review_count, :source_category_id,
                  :source_category, :canonical_category_id, :canonical_category, :category_confidence, :category_scores_json,
                  :matched_terms_json, :category_id, :category, :category_source, :ai_category_id, :ai_category_confidence,
                  :ai_category_reason, :ai_category_updated_at, :brand, :source_image_url, :image_gallery_json, :family_key,
                  :variant_label, :variant_attributes_json, :local_image_key, :image_mime, :image_width,
                  :image_height, :tags_json, :raw_json, :created_at, :updated_at, :last_verified_at, :is_active
                )
                ON CONFLICT(canonical_source_url) DO UPDATE SET
                  provider=excluded.provider,
                  source_url=excluded.source_url,
                  title=excluded.title,
                  slug=excluded.slug,
                  description=CASE
                    WHEN length(excluded.description) > length(products.description) THEN excluded.description
                    ELSE products.description
                  END,
                  price_cents=excluded.price_cents,
                  original_price_cents=COALESCE(excluded.original_price_cents, products.original_price_cents),
                  currency=excluded.currency,
                  rating=CASE WHEN excluded.rating > products.rating THEN excluded.rating ELSE products.rating END,
                  review_count=CASE WHEN excluded.review_count > products.review_count THEN excluded.review_count ELSE products.review_count END,
                  source_category_id=COALESCE(excluded.source_category_id, products.source_category_id),
                  source_category=COALESCE(excluded.source_category, products.source_category),
                  canonical_category_id=excluded.canonical_category_id,
                  canonical_category=excluded.canonical_category,
                  category_confidence=excluded.category_confidence,
                  category_scores_json=excluded.category_scores_json,
                  matched_terms_json=excluded.matched_terms_json,
                  category_id=excluded.category_id,
                  category=excluded.category,
                  category_source=excluded.category_source,
                  ai_category_id=excluded.ai_category_id,
                  ai_category_confidence=excluded.ai_category_confidence,
                  ai_category_reason=excluded.ai_category_reason,
                  ai_category_updated_at=excluded.ai_category_updated_at,
                  brand=COALESCE(excluded.brand, products.brand),
                  source_image_url=excluded.source_image_url,
                  image_gallery_json=excluded.image_gallery_json,
                  family_key=COALESCE(excluded.family_key, products.family_key),
                  variant_label=COALESCE(excluded.variant_label, products.variant_label),
                  variant_attributes_json=CASE
                    WHEN excluded.variant_attributes_json IS NOT NULL AND excluded.variant_attributes_json != '{}'
                      THEN excluded.variant_attributes_json
                    ELSE products.variant_attributes_json
                  END,
                  local_image_key=excluded.local_image_key,
                  image_mime=excluded.image_mime,
                  image_width=excluded.image_width,
                  image_height=excluded.image_height,
                  tags_json=excluded.tags_json,
                  raw_json=excluded.raw_json,
                  updated_at=excluded.updated_at,
                  last_verified_at=excluded.last_verified_at,
                  is_active=1
                """,
                row,
            )
        connection.commit()
    if with_meta:
        return {
            "productIds": product_ids,
            "aiCategoryJudgeUsed": ai_category_judge_used,
            "aiCategoryApplied": ai_category_applied,
        }
    return product_ids


def replace_reviews(product_id: str, reviews: list[ProviderReview]) -> None:
    with _write_connection() as connection:
        connection.execute("DELETE FROM reviews WHERE product_id = ?", (product_id,))
        for review in reviews:
            connection.execute(
                """
                INSERT OR REPLACE INTO reviews (id, product_id, author_name, rating, body, published_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review.id,
                    product_id,
                    review.author_name,
                    review.rating,
                    review.body,
                    review.published_at,
                    json_dumps(review.raw_json or {}),
                ),
            )
        connection.commit()


def save_query_results(
    normalized_query: str,
    display_query: str,
    page_number: int,
    provider: str,
    product_ids: list[str],
    next_page_token_json: str | None = None,
    query_kind: str = "search",
    category_id: str | None = None,
    query_variants: list[str] | None = None,
) -> None:
    discovered_at = now_iso()
    with _write_connection() as connection:
        connection.execute(
            """
            INSERT INTO queries (
              normalized_query, display_query, query_kind, category_id, status, last_requested_at, last_started_at, last_completed_at, last_error, next_page_token_json, query_variants_json
            ) VALUES (?, ?, ?, ?, 'idle', ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(normalized_query) DO UPDATE SET
              display_query=excluded.display_query,
              query_kind=excluded.query_kind,
              category_id=excluded.category_id,
              status='idle',
              last_completed_at=excluded.last_completed_at,
              last_error=NULL,
              next_page_token_json=excluded.next_page_token_json,
              query_variants_json=excluded.query_variants_json
            """,
            (
                normalized_query,
                display_query,
                query_kind,
                category_id,
                discovered_at,
                discovered_at,
                discovered_at,
                next_page_token_json,
                json_dumps(query_variants or []),
            ),
        )
        connection.execute(
            "DELETE FROM query_products WHERE normalized_query = ? AND page_number = ?",
            (normalized_query, page_number),
        )
        for rank, product_id in enumerate(product_ids, start=1):
            connection.execute(
                """
                INSERT OR REPLACE INTO query_products (normalized_query, product_id, rank, page_number, provider, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized_query, product_id, rank, page_number, provider, discovered_at),
        )
        connection.commit()


def append_query_results(
    normalized_query: str,
    display_query: str,
    provider: str,
    product_ids: list[str],
    page_size: int,
    next_page_token_json: str | None = None,
    query_kind: str = "search",
    category_id: str | None = None,
    query_variants: list[str] | None = None,
) -> None:
    if not product_ids:
        return
    discovered_at = now_iso()
    with _write_connection() as connection:
        connection.execute(
            """
            INSERT INTO queries (
              normalized_query, display_query, query_kind, category_id, status, last_requested_at, last_started_at, last_completed_at, last_error, next_page_token_json, query_variants_json
            ) VALUES (?, ?, ?, ?, 'idle', ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(normalized_query) DO UPDATE SET
              display_query=excluded.display_query,
              query_kind=excluded.query_kind,
              category_id=excluded.category_id,
              status='idle',
              last_requested_at=excluded.last_requested_at,
              last_completed_at=excluded.last_completed_at,
              last_error=NULL,
              next_page_token_json=excluded.next_page_token_json,
              query_variants_json=excluded.query_variants_json
            """,
            (
                normalized_query,
                display_query,
                query_kind,
                category_id,
                discovered_at,
                discovered_at,
                discovered_at,
                next_page_token_json,
                json_dumps(query_variants or []),
            ),
        )
        existing_rows = connection.execute(
            "SELECT product_id FROM query_products WHERE normalized_query = ? ORDER BY page_number ASC, rank ASC",
            (normalized_query,),
        ).fetchall()
        seen_ids = {str(row["product_id"]) for row in existing_rows}
        position = len(existing_rows)
        for product_id in product_ids:
            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            page_number = (position // page_size) + 1
            rank = (position % page_size) + 1
            connection.execute(
                """
                INSERT OR REPLACE INTO query_products (normalized_query, product_id, rank, page_number, provider, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized_query, product_id, rank, page_number, provider, discovered_at),
            )
            position += 1
        connection.commit()


def clear_query_results(normalized_query: str) -> None:
    with _write_connection() as connection:
        connection.execute("DELETE FROM query_products WHERE normalized_query = ?", (normalized_query,))
        connection.execute("DELETE FROM queries WHERE normalized_query = ?", (normalized_query,))
        connection.commit()


def set_query_status(
    normalized_query: str,
    display_query: str,
    status: str,
    error_message: str | None = None,
    query_kind: str = "search",
    category_id: str | None = None,
    query_variants: list[str] | None = None,
    next_page_token_json: str | None = None,
) -> None:
    current_time = now_iso()
    with _write_connection() as connection:
        connection.execute(
            """
            INSERT INTO queries (
              normalized_query, display_query, query_kind, category_id, status, last_requested_at, last_started_at, last_completed_at, last_error, next_page_token_json, query_variants_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_query) DO UPDATE SET
              display_query=excluded.display_query,
              query_kind=excluded.query_kind,
              category_id=excluded.category_id,
              status=excluded.status,
              last_requested_at=excluded.last_requested_at,
              last_started_at=CASE WHEN excluded.status='running' THEN excluded.last_started_at ELSE queries.last_started_at END,
              last_completed_at=CASE WHEN excluded.status!='running' THEN excluded.last_completed_at ELSE queries.last_completed_at END,
              last_error=excluded.last_error,
              next_page_token_json=COALESCE(excluded.next_page_token_json, queries.next_page_token_json),
              query_variants_json=excluded.query_variants_json
            """,
            (
                normalized_query,
                display_query,
                query_kind,
                category_id,
                status,
                current_time,
                current_time if status == "running" else None,
                current_time if status != "running" else None,
                error_message,
                next_page_token_json,
                json_dumps(query_variants or []),
            ),
        )
        connection.commit()


def get_query_metadata(normalized_query: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM queries WHERE normalized_query = ?", (normalized_query,)).fetchone()
    return dict(row) if row else None


def cache_discovery_response(cache_key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
    current_time = _now_datetime()
    expires_at = current_time + timedelta(seconds=ttl_seconds)
    try:
        with _write_connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO discovery_cache (cache_key, payload_json, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (cache_key, json_dumps(payload), current_time.isoformat(), expires_at.isoformat()),
            )
            connection.commit()
    except Exception as exc:
        if not _is_locked_error(exc):
            raise


def get_cached_discovery_response(cache_key: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT payload_json, expires_at FROM discovery_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        expires_at = _parse_iso(row["expires_at"])
        if expires_at and expires_at <= _now_datetime():
            connection.execute("DELETE FROM discovery_cache WHERE cache_key = ?", (cache_key,))
            connection.commit()
            return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def store_discovery_hits(
    *,
    context_key: str,
    variant_text: str,
    query_text: str,
    category_id: str | None,
    engines: list[str],
    hits: list[dict[str, Any]],
    provider: str = "apify",
    request_payload: dict[str, Any] | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    current_time = now_iso()
    try:
        with _write_connection() as connection:
            connection.execute(
                """
                INSERT INTO discovery_queries (
                  context_key, variant_text, query_text, category_id, provider, request_json, engines_json, status, last_requested_at, last_completed_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(context_key, variant_text) DO UPDATE SET
                  query_text=excluded.query_text,
                  category_id=excluded.category_id,
                  provider=excluded.provider,
                  request_json=excluded.request_json,
                  engines_json=excluded.engines_json,
                  status=excluded.status,
                  last_requested_at=excluded.last_requested_at,
                  last_completed_at=excluded.last_completed_at,
                  last_error=excluded.last_error
                """,
                (
                    context_key,
                    normalize_whitespace(variant_text).lower(),
                    query_text,
                    category_id,
                    normalize_whitespace(provider) or "apify",
                    json_dumps(request_payload or {}),
                    json_dumps(engines),
                    status,
                    current_time,
                    current_time if status != "running" else None,
                    error_message,
                ),
            )
            connection.execute(
                "DELETE FROM discovery_hits WHERE context_key = ? AND variant_text = ?",
                (context_key, normalize_whitespace(variant_text).lower()),
            )
            for rank, hit in enumerate(hits, start=1):
                normalized_url = normalize_whitespace(hit.get("normalized_url") or hit.get("normalizedUrl"))
                if not normalized_url:
                    continue
                hit_id = hashlib.sha1(
                    f"{context_key}|{normalize_whitespace(variant_text).lower()}|{normalized_url}".encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT OR REPLACE INTO discovery_hits (
                      id, context_key, variant_text, rank, engine, source, source_title, source_snippet, source_rank, domain, title, snippet, url, normalized_url, provider_name, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hit_id,
                        context_key,
                        normalize_whitespace(variant_text).lower(),
                        rank,
                        normalize_whitespace(hit.get("engine")),
                        normalize_whitespace(hit.get("source")),
                        normalize_whitespace(hit.get("source_title") or hit.get("sourceTitle")),
                        normalize_whitespace(hit.get("source_snippet") or hit.get("sourceSnippet")),
                        int(hit.get("source_rank") or hit.get("sourceRank") or 0) or None,
                        normalize_whitespace(hit.get("domain")),
                        normalize_whitespace(hit.get("title")),
                        normalize_whitespace(hit.get("snippet")),
                        normalize_whitespace(hit.get("url")),
                        normalized_url,
                        normalize_whitespace(hit.get("provider_name") or hit.get("providerName")),
                        current_time,
                    ),
                )
            connection.commit()
    except Exception as exc:
        if not _is_locked_error(exc):
            raise


def list_discovery_hits(context_key: str) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT dq.*, dh.rank, dh.engine, dh.source, dh.source_title, dh.source_snippet, dh.source_rank, dh.domain, dh.title, dh.snippet, dh.url, dh.normalized_url, dh.provider_name, dh.created_at
            FROM discovery_queries dq
            LEFT JOIN discovery_hits dh
              ON dh.context_key = dq.context_key
             AND dh.variant_text = dq.variant_text
            WHERE dq.context_key = ?
            ORDER BY dq.last_requested_at DESC, dh.rank ASC
            """,
            (context_key,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_discovery_failure(normalized_url: str, provider_name: str | None, reason: str) -> None:
    url_value = normalize_whitespace(normalized_url)
    if not url_value:
        return
    current_time = now_iso()
    try:
        with _write_connection() as connection:
            connection.execute(
                """
                INSERT INTO discovery_suppression (normalized_url, provider_name, failure_count, last_failure_reason, updated_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(normalized_url) DO UPDATE SET
                  provider_name=excluded.provider_name,
                  failure_count=discovery_suppression.failure_count + 1,
                  last_failure_reason=excluded.last_failure_reason,
                  updated_at=excluded.updated_at
                """,
                (url_value, normalize_whitespace(provider_name) or None, normalize_whitespace(reason) or "unknown", current_time),
            )
            connection.commit()
    except Exception as exc:
        if not _is_locked_error(exc):
            raise


def get_suppressed_discovery_urls(provider_name: str | None = None, minimum_failures: int = 2) -> set[str]:
    with get_connection() as connection:
        if provider_name:
            rows = connection.execute(
                """
                SELECT normalized_url
                FROM discovery_suppression
                WHERE provider_name = ? AND failure_count >= ?
                """,
                (normalize_whitespace(provider_name), minimum_failures),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT normalized_url FROM discovery_suppression WHERE failure_count >= ?",
                (minimum_failures,),
            ).fetchall()
    return {normalize_whitespace(row["normalized_url"]) for row in rows if normalize_whitespace(row["normalized_url"])}


def count_query_results(normalized_query: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM query_products WHERE normalized_query = ?",
            (normalized_query,),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def count_products(category_id: str | None = None) -> int:
    with get_connection() as connection:
        if category_id:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM products WHERE is_active = 1 AND category_id = ?",
                (category_id,),
            ).fetchone()
        else:
            row = connection.execute("SELECT COUNT(*) AS count FROM products WHERE is_active = 1").fetchone()
    return int(row["count"]) if row else 0


def category_counts() -> dict[str, int]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT category_id, COUNT(*) AS count FROM products WHERE is_active = 1 GROUP BY category_id"
        ).fetchall()
    return {str(row["category_id"]): int(row["count"]) for row in rows}


def list_active_product_ids(category_id: str | None = None) -> list[str]:
    with get_connection() as connection:
        if category_id:
            rows = connection.execute(
                "SELECT id FROM products WHERE is_active = 1 AND category_id = ? ORDER BY updated_at DESC",
                (category_id,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT id FROM products WHERE is_active = 1 ORDER BY updated_at DESC").fetchall()
    return [str(row["id"]) for row in rows if row["id"]]


def has_bootstrap_coverage() -> bool:
    counts = category_counts()
    if not all(counts.get(category_id, 0) > 0 for category_id in CORE_CATEGORY_IDS):
        return False
    coverage_probes = {
        "electronics": ["iphone", "macbook", "smart watch"],
        "food": ["protein bar", "pasta", "cereal"],
        "fashion": ["hoodie", "jacket", "sneaker"],
        "beauty": ["moisturizer", "serum", "lip balm"],
        "home": ["area rug", "bed frame", "air fryer"],
        "toys": ["lego", "board game", "plush"],
    }
    with get_connection() as connection:
        for category_id, probes in coverage_probes.items():
            has_probe_match = False
            for probe in probes:
                like_value = f"%{normalize_whitespace(probe).lower()}%"
                row = connection.execute(
                    """
                    SELECT 1
                    FROM products
                    WHERE is_active = 1
                      AND category_id = ?
                      AND (lower(title) LIKE ? OR lower(description) LIKE ?)
                    LIMIT 1
                    """,
                    (category_id, like_value, like_value),
                ).fetchone()
                if row:
                    has_probe_match = True
                    break
            if not has_probe_match:
                return False
    return True


def list_products(page: int, page_size: int, category_id: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    with get_connection() as connection:
        if category_id:
            rows = connection.execute(
                "SELECT * FROM products WHERE is_active = 1 AND category_id = ? ORDER BY updated_at DESC",
                (category_id,),
            ).fetchall()
            deduped_rows = _dedupe_rows(rows)
            ordered = [_row_to_product(row) for row in deduped_rows]
        else:
            rows = connection.execute("SELECT * FROM products WHERE is_active = 1 ORDER BY updated_at DESC").fetchall()
            ordered = _interleaved_products(rows)
        total = len(ordered)
        items = ordered[offset : offset + page_size]
        payload = _catalog_response(
            connection,
            annotate_products_with_favorites(items, user_id, connection=connection),
            total,
            page,
            page_size,
            context_key=f"category::{category_id}" if category_id else "home",
            context_type="category" if category_id else "home",
            applied_category_id=category_id,
            strict_category=bool(category_id),
            matching={"source": "category_feed" if category_id else "home", "exactMatchCount": len(items), "filteredOutCount": 0},
        )
        payload["offers"] = annotate_products_with_favorites(payload["offers"], user_id, connection=connection)
        return payload


def list_query_products(
    normalized_query: str,
    page: int,
    page_size: int,
    category_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    with get_connection() as connection:
        params: list[Any] = [normalized_query]
        category_sql = ""
        if category_id:
            category_sql = " AND p.category_id = ?"
            params.append(category_id)
        rows = connection.execute(
            f"""
            SELECT p.*, qp.rank, qp.page_number
            FROM query_products qp
            INNER JOIN products p ON p.id = qp.product_id AND p.is_active = 1
            WHERE qp.normalized_query = ?{category_sql}
            ORDER BY qp.page_number ASC, qp.rank ASC
            """,
            params,
        ).fetchall()
        total = len(rows)
        items = annotate_products_with_favorites(
            [_row_to_product(row) for row in rows[offset : offset + page_size]],
            user_id,
            connection=connection,
        )
        metadata = connection.execute("SELECT * FROM queries WHERE normalized_query = ?", (normalized_query,)).fetchone()
        metadata_dict = dict(metadata) if metadata else {}
        payload = _catalog_response(
            connection,
            items,
            total,
            page,
            page_size,
            context_key=normalized_query,
            context_type=metadata_dict.get("query_kind") or "search",
            applied_query=metadata_dict.get("display_query"),
            applied_category_id=metadata_dict.get("category_id"),
            strict_category=bool(metadata_dict.get("category_id")),
            query_variants=_decode_json_array(metadata_dict.get("query_variants_json")),
            matching={
                "source": "cached_fallback",
                "exactMatchCount": len(items),
                "filteredOutCount": 0,
            },
            dedupe_items=False,
        )
        payload["metadata"] = metadata_dict or None
        payload["offers"] = annotate_products_with_favorites(payload["offers"], user_id, connection=connection)
        return payload


def _build_search_haystack_parts(row: sqlite3.Row) -> list[str]:
    return [
        normalize_whitespace(row["title"]).lower(),
        normalize_whitespace(row["description"]).lower(),
        normalize_whitespace(row["brand"]).lower(),
        " ".join(_decode_json_array(row["tags_json"])).lower(),
        normalize_whitespace(row["category"]).lower(),
    ]


def _build_search_token_set(*parts: str) -> set[str]:
    return {singularize_token(token) for token in tokenize(*parts)}


def _matches_phrase_or_token(value: str, haystack: str, haystack_tokens: set[str]) -> bool:
    normalized_value = normalize_whitespace(value).lower()
    if not normalized_value:
        return False
    if " " in normalized_value:
        return normalized_value in haystack
    return singularize_token(normalized_value) in haystack_tokens


def _record_value(record: sqlite3.Row | dict[str, Any], *keys: str) -> Any:
    if isinstance(record, dict):
        for key in keys:
            if key in record:
                return record.get(key)
        return None
    for key in keys:
        try:
            return record[key]
        except Exception:
            continue
    return None


def _related_record_tags(record: sqlite3.Row | dict[str, Any]) -> list[str]:
    if isinstance(record, dict):
        raw_tags = record.get("tags")
        if isinstance(raw_tags, list):
            return [normalize_whitespace(str(tag)).lower() for tag in raw_tags if normalize_whitespace(str(tag))]
    return [
        normalize_whitespace(str(tag)).lower()
        for tag in _decode_json_array(_record_value(record, "tags_json"))
        if normalize_whitespace(str(tag))
    ]


def _build_related_token_set(record: sqlite3.Row | dict[str, Any]) -> set[str]:
    title = normalize_whitespace(_record_value(record, "title", "name")).lower()
    description = normalize_whitespace(_record_value(record, "description")).lower()
    tags = _related_record_tags(record)
    category_id = normalize_whitespace(_record_value(record, "category_id", "categoryId")).lower()
    category_name_value = normalize_whitespace(_record_value(record, "category")).lower()
    brand_tokens = _build_search_token_set(normalize_whitespace(_record_value(record, "brand")).lower())
    category_noise_tokens: set[str] = set()
    if category_name_value:
        category_noise_tokens |= _build_search_token_set(category_name_value)
    if category_id:
        category_noise_tokens |= _build_search_token_set(category_name(category_id), category_id)
    tokens = _build_search_token_set(title, " ".join(tags), description)
    return {
        token
        for token in tokens
        if token
        and token not in RELATED_TOKEN_STOPWORDS
        and token not in brand_tokens
        and token not in category_noise_tokens
        and not any(character.isdigit() for character in token)
        and len(token) > 2
    }


def _related_overlap_metrics(
    anchor_record: sqlite3.Row | dict[str, Any],
    candidate_record: sqlite3.Row | dict[str, Any],
) -> tuple[int, float]:
    anchor_tokens = _build_related_token_set(anchor_record)
    candidate_tokens = _build_related_token_set(candidate_record)
    shared_token_count = len(anchor_tokens & candidate_tokens)
    overlap_ratio = shared_token_count / max(1, min(len(anchor_tokens), len(candidate_tokens)))
    return shared_token_count, overlap_ratio


def _related_candidate_passes_threshold(
    anchor_record: sqlite3.Row | dict[str, Any],
    candidate_record: sqlite3.Row | dict[str, Any],
    *,
    shared_query_count: int = 0,
    shared_token_count: int | None = None,
    overlap_ratio: float | None = None,
) -> bool:
    anchor_provider = normalize_whitespace(_record_value(anchor_record, "provider")).lower()
    anchor_family_key = normalize_whitespace(_record_value(anchor_record, "family_key", "familyKey"))
    candidate_provider = normalize_whitespace(_record_value(candidate_record, "provider")).lower()
    candidate_family_key = normalize_whitespace(_record_value(candidate_record, "family_key", "familyKey"))
    if (
        anchor_provider
        and anchor_family_key
        and candidate_provider
        and candidate_family_key
        and anchor_provider == candidate_provider
        and anchor_family_key == candidate_family_key
    ):
        return True

    if shared_token_count is None or overlap_ratio is None:
        shared_token_count, overlap_ratio = _related_overlap_metrics(anchor_record, candidate_record)

    anchor_category_id = normalize_whitespace(_record_value(anchor_record, "category_id", "categoryId")).lower()
    candidate_category_id = normalize_whitespace(_record_value(candidate_record, "category_id", "categoryId")).lower()
    anchor_brand = normalize_whitespace(_record_value(anchor_record, "brand")).lower()
    candidate_brand = normalize_whitespace(_record_value(candidate_record, "brand")).lower()
    same_brand = bool(anchor_brand and candidate_brand and anchor_brand == candidate_brand)
    same_category = bool(anchor_category_id and candidate_category_id and anchor_category_id == candidate_category_id)

    if shared_query_count >= 2 and shared_token_count >= 1:
        return True
    if shared_query_count >= 1 and same_category and shared_token_count >= 1 and overlap_ratio >= 0.3:
        return True
    if shared_query_count >= 1 and same_brand and shared_token_count >= 1:
        return True

    if not same_category:
        if same_brand and shared_token_count >= 2 and (
            overlap_ratio >= 0.28 or "others" in {anchor_category_id, candidate_category_id}
        ):
            return True
        return False

    if anchor_category_id == "others":
        if same_brand and shared_token_count >= 2 and overlap_ratio >= 0.4:
            return True
        if shared_query_count >= 1 and shared_token_count >= 2:
            return True
        return shared_token_count >= 3 and overlap_ratio >= 0.55

    if shared_token_count >= 3:
        return True
    if same_brand and shared_token_count >= 2:
        return True
    if shared_token_count >= 2 and (overlap_ratio >= 0.28 or shared_query_count >= 1):
        return True
    if overlap_ratio >= 0.6 and shared_token_count >= 1:
        return True
    if same_brand and shared_token_count >= 1 and overlap_ratio >= 0.35:
        return True
    return False


def filter_related_product_candidates(anchor_product: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate or str(candidate.get("id") or "") == str(anchor_product.get("id") or ""):
            continue
        shared_token_count, overlap_ratio = _related_overlap_metrics(anchor_product, candidate)
        if not _related_candidate_passes_threshold(
            anchor_product,
            candidate,
            shared_token_count=shared_token_count,
            overlap_ratio=overlap_ratio,
        ):
            continue
        filtered.append(candidate)
    return _dedupe_product_list(filtered)


def _shared_query_count_lookup(
    connection: sqlite3.Connection,
    product_id: str,
    candidate_ids: list[str],
) -> dict[str, int]:
    unique_candidate_ids = [candidate_id for candidate_id in dict.fromkeys(candidate_ids) if candidate_id and candidate_id != product_id]
    if not unique_candidate_ids:
        return {}
    placeholders = ",".join("?" for _ in unique_candidate_ids)
    rows = connection.execute(
        f"""
        SELECT qp2.product_id AS candidate_id, COUNT(DISTINCT qp1.normalized_query) AS shared_query_count
        FROM query_products qp1
        INNER JOIN query_products qp2
          ON qp1.normalized_query = qp2.normalized_query
         AND qp1.product_id != qp2.product_id
        INNER JOIN queries q1
          ON q1.normalized_query = qp1.normalized_query
        WHERE qp1.product_id = ?
          AND qp2.product_id IN ({placeholders})
          AND COALESCE(q1.query_kind, 'search') = 'search'
        GROUP BY qp2.product_id
        """,
        (product_id, *unique_candidate_ids),
    ).fetchall()
    return {str(row["candidate_id"]): int(row["shared_query_count"] or 0) for row in rows if row["candidate_id"]}


def _query_specific_adjustment(normalized_query: str, haystack: str) -> tuple[float, bool]:
    score = 0.0
    strong_match = False
    if normalized_query == "mac":
        if any(marker in haystack for marker in ["macbook", "imac", "apple laptop", "apple computer"]):
            score += 6.0
            strong_match = True
        if any(marker in haystack for marker in ["mac-compatible", "mac compatible", "for mac", "compatible with mac"]):
            score -= 5.0
        if any(marker in haystack for marker in ["case", "sleeve", "cover", "adapter", "dock", "hub"]) and "macbook" not in haystack:
            score -= 2.5
    elif normalized_query == "clock":
        if any(marker in haystack for marker in ["wall clock", "alarm clock", "desk clock", "travel alarm"]):
            score += 6.0
            strong_match = True
        if any(marker in haystack for marker in ["base clock", "boost clock", "clock speed"]):
            score -= 6.0
    return score, strong_match


def _normalize_search_score(score: float) -> float:
    if score <= 0:
        return 0.0
    return round(min(score / 10.0, 1.0), 4)


def search_cached_products(
    query: str,
    page: int,
    page_size: int,
    category_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    normalized_query = normalize_whitespace(query).lower()
    tokens = tokenize(normalized_query)
    if not tokens:
        return list_products(page=page, page_size=page_size, category_id=category_id)
    query_classification = classify_category(normalized_query)
    query_category_id = str(query_classification["category_id"])
    query_variants = expand_query_variants(normalized_query, category_id)
    category_terms: set[str] = set()
    if query_category_id != "others":
        category_config = CATEGORY_CONFIG.get(query_category_id, CATEGORY_CONFIG["others"])
        category_terms = {
            normalize_whitespace(category_name(query_category_id)).lower(),
            *[normalize_whitespace(str(term)).lower() for term in category_config.get("keywords", [])],
            *[normalize_whitespace(str(term)).lower() for term in category_config.get("query_bonus_terms", [])],
        }

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM products
            WHERE is_active = 1
            ORDER BY updated_at DESC
            LIMIT 500
            """
        ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        exact_match_count = 0
        for row in rows:
            if category_id and row["category_id"] != category_id:
                continue
            haystack_parts = _build_search_haystack_parts(row)
            haystack = " ".join(haystack_parts)
            haystack_tokens = _build_search_token_set(*haystack_parts)
            score = 0.0
            text_match = False
            token_match_count = 0
            strong_variant_match = False
            exact_phrase_match = False
            query_specific_strong_match = False
            for token in tokens:
                if singularize_token(token) in haystack_tokens:
                    score += 2.6
                    text_match = True
                    token_match_count += 1
            if _matches_phrase_or_token(normalized_query, haystack, haystack_tokens):
                score += 4.0
                text_match = True
                exact_phrase_match = True
                exact_match_count += 1
            for variant in query_variants[1:]:
                normalized_variant = normalize_whitespace(variant).lower()
                if (
                    normalized_variant
                    and normalized_variant != normalized_query
                    and _matches_phrase_or_token(normalized_variant, haystack, haystack_tokens)
                ):
                    score += 4.6
                    text_match = True
                    strong_variant_match = True
            if strong_variant_match:
                exact_match_count += 1
            query_adjustment, query_specific_strong_match = _query_specific_adjustment(normalized_query, haystack)
            score += query_adjustment
            if query_specific_strong_match:
                exact_match_count += 1
            strong_text_match = (
                exact_phrase_match
                or strong_variant_match
                or query_specific_strong_match
                or token_match_count >= min(2, len(tokens))
            )
            category_only_match = False
            if not text_match and category_terms and normalized_query in category_terms and row["category_id"] == query_category_id:
                score += 3.0
                category_only_match = True
            if normalize_whitespace(row["category"]).lower() in normalized_query and row["category_id"] == query_category_id:
                score += 2.5
                category_only_match = True
            if not text_match and not category_only_match:
                continue
            if row["category_id"] == "others" and float(row["category_confidence"] or 0.0) < 0.45 and not strong_text_match:
                continue
            if query_category_id != "others" and row["category_id"] not in {query_category_id, "others"} and not strong_text_match:
                continue
            if query_category_id != "others":
                if row["category_id"] == query_category_id:
                    score += 2.2
                elif row["category_id"] == "others":
                    score -= 0.9
            score += float(row["rating"] or 0.0) * 0.35
            score += min(int(row["review_count"] or 0), 250) / 250.0
            score += min(float(row["category_confidence"] or 0.0), 8.0) * 0.4
            normalized_score = _normalize_search_score(score)
            if (
                normalized_score >= 0.22
                or strong_text_match
                or (category_only_match and row["category_id"] == query_category_id)
            ):
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], float(item[1]["rating"] or 0.0), item[1]["updated_at"]), reverse=True)
        products: list[dict[str, Any]] = []
        normalized_scores: list[float] = []
        for raw_score, row in scored:
            product = _row_to_product(row)
            product["score"] = _normalize_search_score(raw_score)
            normalized_scores.append(float(product["score"]))
            products.append(product)
        good_result_count = sum(1 for score in normalized_scores if score >= 0.3)
        products = annotate_products_with_favorites(products, user_id, connection=connection)
        offset = max(page - 1, 0) * page_size
        payload = _catalog_response(
            connection,
            products[offset : offset + page_size],
            len(products),
            page,
            page_size,
            context_key=f"search::{category_id or 'all'}::{normalized_query}",
            context_type="search",
            applied_query=query,
            applied_category_id=category_id,
            strict_category=bool(category_id),
            query_variants=query_variants,
            matching={
                "source": "cached_fallback",
                "exactMatchCount": exact_match_count,
                "filteredOutCount": 0,
                "goodResultCount": good_result_count,
                "averageScore": round(sum(normalized_scores) / len(normalized_scores), 4) if normalized_scores else 0.0,
                "topScore": normalized_scores[0] if normalized_scores else 0.0,
            },
            dedupe_items=False,
        )
        payload["offers"] = annotate_products_with_favorites(payload["offers"], user_id, connection=connection)
        return payload


def rank_product_ids_for_query(
    product_ids: list[str],
    query_text: str,
    category_id: str | None = None,
    strict_category: bool = False,
) -> tuple[list[str], int, int]:
    if not product_ids:
        return [], 0, 0
    normalized_query = normalize_whitespace(query_text).lower()
    query_tokens = tokenize(normalized_query)
    query_category_id = str(classify_category(normalized_query)["category_id"])
    query_variants = expand_query_variants(normalized_query, category_id)
    with get_connection() as connection:
        placeholders = ",".join("?" for _ in product_ids)
        rows = connection.execute(
            f"SELECT * FROM products WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
    row_by_id = {str(row["id"]): row for row in rows}
    ranked: list[tuple[float, str]] = []
    filtered_out = 0
    exact_match_count = 0
    for product_id in product_ids:
        row = row_by_id.get(product_id)
        if not row:
            continue
        row_category_id = str(row["category_id"])
        if strict_category and category_id and row_category_id != category_id:
            filtered_out += 1
            continue
        if category_id and row_category_id != category_id:
            filtered_out += 1
            continue
        haystack_parts = _build_search_haystack_parts(row)
        haystack = " ".join(haystack_parts)
        haystack_tokens = _build_search_token_set(*haystack_parts)
        score = 0.0
        token_match_count = 0
        strong_variant_match = False
        strong_text_match = False
        if _matches_phrase_or_token(normalized_query, haystack, haystack_tokens):
            score += 5.0
            exact_match_count += 1
            strong_text_match = True
        for token in query_tokens:
            if singularize_token(token) in haystack_tokens:
                score += 1.5
                token_match_count += 1
        for variant in query_variants[1:]:
            if _matches_phrase_or_token(variant, haystack, haystack_tokens):
                score += 3.2
                strong_variant_match = True
        if strong_variant_match:
            exact_match_count += 1
            strong_text_match = True
        query_adjustment, query_specific_strong_match = _query_specific_adjustment(normalized_query, haystack)
        score += query_adjustment
        if query_specific_strong_match:
            exact_match_count += 1
            strong_text_match = True
        strong_text_match = strong_text_match or token_match_count >= min(2, len(query_tokens))
        if token_match_count <= 0 and not strong_text_match:
            filtered_out += 1
            continue
        if float(row["category_confidence"] or 0.0) <= 0 and row_category_id == "others" and not strong_text_match:
            filtered_out += 1
            continue
        if row_category_id == "others" and float(row["category_confidence"] or 0.0) < 0.45 and not strong_text_match:
            filtered_out += 1
            continue
        if query_category_id != "others" and row_category_id not in {query_category_id, "others"} and not strong_text_match:
            filtered_out += 1
            continue
        if category_id and row_category_id == category_id:
            score += 2.0
        score += min(float(row["category_confidence"] or 0.0), 8.0) * 0.6
        score += float(row["rating"] or 0.0) * 0.4
        score += min(int(row["review_count"] or 0), 200) / 200.0
        normalized_score = _normalize_search_score(score)
        if normalized_score < 0.24 and not strong_text_match:
            filtered_out += 1
            continue
        ranked.append((score, product_id))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [product_id for _, product_id in ranked], exact_match_count, filtered_out


def get_product(product_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        product = _row_to_product(row) if row else None
        if product:
            annotate_products_with_favorites([product], user_id, connection=connection)
        return product


def get_source_image_url(local_image_key: str) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT source_image_url FROM products WHERE local_image_key = ? LIMIT 1",
            (local_image_key,),
        ).fetchone()
    return str(row["source_image_url"]) if row else None


def _compute_related_scores(
    connection: sqlite3.Connection,
    product_id: str,
    limit: int,
    user_id: str | None = None,
    session_id: str | None = None,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    product_row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product_row:
        return [], 0

    current_category_id = str(product_row["category_id"])
    current_related_tokens = _build_related_token_set(product_row)
    strict_anchor_category = bool(current_category_id and current_category_id != "others")
    if not strict_anchor_category and not current_related_tokens:
        return [], 0

    # 1. Try fetching from related_products table first (as requested)
    rel_rows = connection.execute(
        """
        SELECT p.*, rp.score as table_score, rp.reason
        FROM related_products rp
        JOIN products p ON p.id = rp.related_product_id
        WHERE rp.product_id = ? AND p.is_active = 1
        ORDER BY rp.score DESC
        """,
        (product_id,)
    ).fetchall()
    
    if rel_rows:
        shared_query_counts = _shared_query_count_lookup(
            connection,
            product_id,
            [str(row["id"]) for row in rel_rows if row["id"]],
        )
        filtered_rel_rows: list[sqlite3.Row] = []
        for row in rel_rows:
            shared_token_count, overlap_ratio = _related_overlap_metrics(product_row, row)
            if not _related_candidate_passes_threshold(
                product_row,
                row,
                shared_query_count=shared_query_counts.get(str(row["id"]), 0),
                shared_token_count=shared_token_count,
                overlap_ratio=overlap_ratio,
            ):
                continue
            filtered_rel_rows.append(row)
        if filtered_rel_rows:
            total = len(filtered_rel_rows)
            sliced = [_row_to_product(row) for row in filtered_rel_rows[offset : offset + limit]]
            return sliced, total

    # 2. Fallback to same-category products with keyword scoring
    affinity_lookup = {}
    recent_category_bias: dict[str, float] = {}
    recent_tag_bias: dict[str, float] = {}
    if user_id:
        preference_context = _build_event_affinities(connection, user_id, session_id=session_id)
        affinity_lookup = preference_context["affinities"]
        recent_category_bias = preference_context["recentCategoryBias"]
        recent_tag_bias = preference_context["recentTagBias"]

    # Fetch candidates from the same category when classification is strong.
    # For weakly classified anchors, rely on stronger token/query overlap instead of broad category fallback.
    category_filter = "AND p.category_id = ?" if strict_anchor_category else ""
    params = [product_id, product_id]
    if strict_anchor_category:
        params.append(current_category_id)

    rows = connection.execute(
        f"""
        SELECT p.*, (
          COALESCE(shared.shared_queries, 0) * 5 +
          p.rating * 2
        ) AS base_score
        FROM products current
        CROSS JOIN products p
        LEFT JOIN (
          SELECT qp2.product_id, COUNT(DISTINCT qp1.normalized_query) AS shared_queries
          FROM query_products qp1
          INNER JOIN query_products qp2
            ON qp1.normalized_query = qp2.normalized_query
           AND qp1.product_id != qp2.product_id
          INNER JOIN queries q1
            ON q1.normalized_query = qp1.normalized_query
          WHERE qp1.product_id = ?
            AND COALESCE(q1.query_kind, 'search') = 'search'
          GROUP BY qp2.product_id
        ) shared ON shared.product_id = p.id
        WHERE current.id = ? AND p.id != current.id AND p.is_active = 1
        {category_filter}
        ORDER BY p.rating DESC, p.updated_at DESC
        LIMIT 200
        """,
        params,
    ).fetchall()
    shared_query_counts = _shared_query_count_lookup(
        connection,
        product_id,
        [str(row["id"]) for row in rows if row["id"]],
    )

    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        score = float(row["base_score"] or 0.0)
        row_related_tokens = _build_related_token_set(row)
        shared_related_tokens = current_related_tokens & row_related_tokens
        shared_token_count = len(shared_related_tokens)
        overlap_ratio = shared_token_count / max(1, min(len(current_related_tokens), len(row_related_tokens)))
        shared_query_count = shared_query_counts.get(str(row["id"]), 0)

        if not _related_candidate_passes_threshold(
            product_row,
            row,
            shared_query_count=shared_query_count,
            shared_token_count=shared_token_count,
            overlap_ratio=overlap_ratio,
        ):
            continue

        # Keyword bonus is very strong to ensure relevance
        score += shared_token_count * 10.0
        score += overlap_ratio * 15.0
        score += shared_query_count * 6.0
        
        if user_id:
            score += affinity_lookup.get(("category", str(row["category_id"])), 0.0) * 0.7
            score += affinity_lookup.get(("brand", normalize_whitespace(row["brand"]).lower()), 0.0) * 0.35
            score += sum(affinity_lookup.get(("tag", tag.lower()), 0.0) for tag in row_related_tokens) * 0.2
            score += recent_category_bias.get(str(row["category_id"]).lower(), 0.0) * 0.6
            score += sum(recent_tag_bias.get(tag.lower(), 0.0) for tag in row_related_tokens) * 0.15
            
        scored.append((score, row))

    scored.sort(key=lambda item: (item[0], float(item[1]["rating"] or 0.0), int(item[1]["review_count"] or 0)), reverse=True)
    ranked_rows: list[sqlite3.Row] = []
    seen_keys: set[str] = set()
    for _, row in scored:
        key = _row_identity_key(row)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ranked_rows.append(row)
        
    total = len(ranked_rows)
    sliced = [_row_to_product(row) for row in ranked_rows[offset : offset + limit]]
    return sliced, total


def get_related_products(
    product_id: str,
    page: int,
    page_size: int,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    offset = max(page - 1, 0) * page_size
    with get_connection() as connection:
        items, total = _compute_related_scores(
            connection,
            product_id,
            limit=page_size,
            user_id=user_id,
            session_id=session_id,
            offset=offset,
        )
        if not total and not items:
            return None
        annotate_products_with_favorites(items, user_id, connection=connection)
        return {
            "items": items,
            "page": page,
            "pageSize": page_size,
            "hasMore": page * page_size < total,
            "total": total,
        }


def _variant_summary_from_row(row: sqlite3.Row, current_product_id: str) -> dict[str, Any]:
    product = _row_to_product(row)
    return {
        "productId": product["id"],
        "familyKey": product.get("familyKey"),
        "label": product.get("variantLabel") or product["name"],
        "attributes": product.get("variantAttributes") or {},
        "price": product["price"],
        "originalPrice": product.get("originalPrice"),
        "imageUrl": product.get("sourceImageUrl") or product["imageUrl"],
        "imageGallery": product.get("imageGallery") or [],
        "isCurrent": product["id"] == current_product_id,
    }


def get_product_with_reviews(
    product_id: str,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    with get_connection() as connection:
        product_row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product_row:
            return None
        product = _row_to_product(product_row)
        review_rows = connection.execute(
            "SELECT * FROM reviews WHERE product_id = ? ORDER BY published_at DESC, id ASC LIMIT 10",
            (product_id,),
        ).fetchall()
        reviews = [
            {
                "id": row["id"],
                "authorName": row["author_name"],
                "rating": float(row["rating"] or 0.0),
                "body": row["body"],
                "publishedAt": row["published_at"],
            }
            for row in review_rows
        ]
        related_products, _ = _compute_related_scores(
            connection,
            product_id,
            limit=6,
            user_id=user_id,
            session_id=session_id,
        )
        variant_rows: list[sqlite3.Row] = []
        family_key = normalize_whitespace(product_row["family_key"])
        if family_key:
            variant_rows = connection.execute(
                """
                SELECT *
                FROM products
                WHERE is_active = 1
                  AND provider = ?
                  AND family_key = ?
                ORDER BY updated_at DESC
                LIMIT 20
                """,
                (product_row["provider"], family_key),
            ).fetchall()
            
            annotate_products_with_favorites([product], user_id, connection=connection)
            annotate_products_with_favorites(related_products, user_id, connection=connection)
            product["reviews"] = reviews
            product["relatedProducts"] = _dedupe_product_list(related_products)
            product["variantOptions"] = [
                _variant_summary_from_row(row, product_id)
                for row in (variant_rows or [product_row])
            ]
    return product


def _normalize_email(email: str) -> str:
    return normalize_whitespace(email).lower()


def _password_hash(password: str, salt_hex: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PASSWORD_HASH_ITERATIONS,
    )
    return digest.hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_expiry() -> str:
    return (_now_datetime() + timedelta(seconds=AUTH_TOKEN_TTL_SECONDS)).isoformat()


def _supabase_auth_enabled() -> bool:
    return postgres_enabled() and bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _supabase_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> httpx.Response:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/json",
    }
    headers["Authorization"] = f"Bearer {token or SUPABASE_SERVICE_ROLE_KEY}"
    logging.getLogger(__name__).debug("Supabase request: %s %s", method, path)
    response = httpx.request(
        method,
        f"{SUPABASE_URL.rstrip('/')}{path}",
        headers=headers,
        json=json_body,
        params=query,
        timeout=20.0,
        follow_redirects=True,
    )
    logging.getLogger(__name__).debug("Supabase response: %s", response.status_code)
    response.raise_for_status()
    return response


def _decode_token_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}


def _upsert_profile_for_supabase_user(connection, user_payload: dict[str, Any], legacy_user_id: str | None = None) -> None:
    user_id = str(user_payload.get("id") or "").strip()
    email = normalize_whitespace(user_payload.get("email")).lower()
    if not user_id or not email:
        return
    user_metadata = user_payload.get("user_metadata") if isinstance(user_payload.get("user_metadata"), dict) else {}
    app_metadata = user_payload.get("app_metadata") if isinstance(user_payload.get("app_metadata"), dict) else {}
    current_time = now_iso()
    created_at = user_payload.get("created_at") or current_time
    updated_at = user_payload.get("updated_at") or current_time
    connection.execute(
        """
        INSERT INTO profiles (
          id, legacy_user_id, email, full_name, phone, address, city, country, role, level, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
          legacy_user_id = COALESCE(EXCLUDED.legacy_user_id, profiles.legacy_user_id),
          email = EXCLUDED.email,
          full_name = COALESCE(EXCLUDED.full_name, profiles.full_name),
          phone = COALESCE(EXCLUDED.phone, profiles.phone),
          address = COALESCE(EXCLUDED.address, profiles.address),
          city = COALESCE(EXCLUDED.city, profiles.city),
          country = COALESCE(EXCLUDED.country, profiles.country),
          role = COALESCE(EXCLUDED.role, profiles.role),
          level = COALESCE(EXCLUDED.level, profiles.level),
          updated_at = EXCLUDED.updated_at
        """,
        (
            user_id,
            legacy_user_id,
            email,
            normalize_whitespace(user_metadata.get("full_name") or user_metadata.get("name")) or None,
            normalize_whitespace(user_metadata.get("phone")) or None,
            normalize_whitespace(user_metadata.get("address")) or None,
            normalize_whitespace(user_metadata.get("city")) or None,
            normalize_whitespace(user_metadata.get("country")) or None,
            normalize_whitespace(app_metadata.get("role")) or "user",
            int(user_metadata.get("level") or 1),
            created_at,
            updated_at,
        ),
    )


def _public_user_payload(connection: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    if _supabase_auth_enabled():
        user_row = connection.execute(
            """
            SELECT id, email, full_name, phone, address, city, country, role, level, created_at
            FROM profiles
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
    else:
        user_row = connection.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not user_row:
        raise ValueError("User not found")
    top_interests = get_top_interests(user_id, limit=6, connection=connection)
    stats_row = connection.execute(
        """
        SELECT
          SUM(CASE WHEN event_type = 'search' THEN 1 ELSE 0 END) AS searches,
          SUM(CASE WHEN event_type = 'product_view' THEN 1 ELSE 0 END) AS product_views,
          SUM(CASE WHEN event_type = 'source_open' THEN 1 ELSE 0 END) AS source_opens
        FROM user_events
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    return {
        "id": user_row["id"],
        "email": user_row["email"],
        "createdAt": user_row["created_at"],
        "fullName": user_row.get("full_name") if isinstance(user_row, dict) else None,
        "phone": user_row.get("phone") if isinstance(user_row, dict) else None,
        "address": user_row.get("address") if isinstance(user_row, dict) else None,
        "city": user_row.get("city") if isinstance(user_row, dict) else None,
        "country": user_row.get("country") if isinstance(user_row, dict) else None,
        "role": user_row.get("role") if isinstance(user_row, dict) else "user",
        "level": int((user_row.get("level") if isinstance(user_row, dict) else 1) or 1),
        "topInterests": top_interests,
        "stats": {
            "searches": int(stats_row["searches"] or 0),
            "productViews": int(stats_row["product_views"] or 0),
            "sourceOpens": int(stats_row["source_opens"] or 0),
        },
    }


def create_user(email: str, password: str) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("Enter a valid email address.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    if _supabase_auth_enabled():
        try:
            response = _supabase_request(
                "POST",
                "/auth/v1/admin/users",
                json_body={
                    "email": normalized_email,
                    "password": password,
                    "email_confirm": True,
                    "app_metadata": {"provider": "email", "role": "user"},
                },
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.lower()
            if "already" in detail or "exists" in detail:
                raise ValueError("This email already has an account.") from exc
            raise ValueError("Sign up failed.") from exc
        user_payload = (response.json() or {}).get("user") or (response.json() or {})
        with get_connection() as connection:
            _upsert_profile_for_supabase_user(connection, user_payload)
            connection.commit()
        return authenticate_user(normalized_email, password)

    current_time = now_iso()
    user_id = secrets.token_hex(12)
    salt_hex = secrets.token_hex(16)
    token = secrets.token_urlsafe(32)
    with get_connection() as connection:
        existing = connection.execute("SELECT id FROM users WHERE email = ?", (normalized_email,)).fetchone()
        if existing:
            raise ValueError("This email already has an account.")
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, password_salt, created_at, updated_at, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, normalized_email, _password_hash(password, salt_hex), salt_hex, current_time, current_time, current_time),
        )
        session_id = secrets.token_hex(12)
        connection.execute(
            """
            INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (session_id, user_id, _hash_token(token), current_time, current_time, _session_expiry()),
        )
        connection.commit()
        user = _public_user_payload(connection, user_id)
    return {"token": token, "user": user}


def authenticate_user(email: str, password: str) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    if _supabase_auth_enabled():
        logger.info("Authenticating user via Supabase: %s", normalized_email)
        try:
            response = _supabase_request(
                "POST",
                "/auth/v1/token",
                json_body={"email": normalized_email, "password": password},
                query={"grant_type": "password"},
            )
        except httpx.HTTPStatusError as exc:
            logging.getLogger(__name__).warning(
                "Supabase login failed for %s with status %s: %s",
                normalized_email,
                exc.response.status_code if exc.response is not None else "unknown",
                (exc.response.text if exc.response is not None else str(exc))[:500],
            )
            raise ValueError("Invalid email or password.") from exc
        except Exception as exc:
            logging.getLogger(__name__).exception("Unexpected login failure for %s", normalized_email)
            raise ValueError("Login failed. Try again in a moment.") from exc
        payload = response.json() or {}
        access_token = str(payload.get("access_token") or "").strip()
        user_payload = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        if not access_token or not user_payload:
            logging.getLogger(__name__).warning(
                "Supabase login returned incomplete payload for %s: keys=%s",
                normalized_email,
                sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            )
            raise ValueError("Invalid email or password.")

        logger.info("Supabase login successful for %s, fetching profile from DB", normalized_email)
        with get_connection() as connection:
            logger.debug("Upserting profile for user %s", user_payload.get("id"))
            _upsert_profile_for_supabase_user(connection, user_payload)
            connection.commit()
            logger.debug("Fetching public user payload for user %s", user_payload.get("id"))
            user = _public_user_payload(connection, str(user_payload["id"]))
        logger.info("Authentication complete for %s", normalized_email)
        return {"token": access_token, "user": user}

    current_time = now_iso()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, password_hash, password_salt FROM users WHERE email = ?",
            (normalized_email,),
        ).fetchone()
        if not row:
            raise ValueError("Invalid email or password.")
        expected_hash = _password_hash(password, row["password_salt"])
        if not hmac.compare_digest(expected_hash, row["password_hash"]):
            raise ValueError("Invalid email or password.")
        connection.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (current_time, current_time, row["id"]),
        )
        connection.execute("UPDATE sessions SET is_active = 0 WHERE user_id = ?", (row["id"],))
        token = secrets.token_urlsafe(32)
        connection.execute(
            """
            INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (secrets.token_hex(12), row["id"], _hash_token(token), current_time, current_time, _session_expiry()),
        )
        connection.commit()
        user = _public_user_payload(connection, row["id"])
    return {"token": token, "user": user}


def get_auth_context_by_token(token: str, touch: bool = True) -> dict[str, Any] | None:
    if not token:
        return None
    if _supabase_auth_enabled():
        try:
            response = _supabase_request("GET", "/auth/v1/user", token=token)
        except httpx.HTTPStatusError:
            return None
        user_payload = response.json() or {}
        user_id = normalize_whitespace(user_payload.get("id"))
        if not user_id:
            return None
        claims = _decode_token_claims(token)
        session_id = normalize_whitespace(claims.get("session_id")) or hashlib.sha1(token.encode("utf-8")).hexdigest()[:24]
        with get_connection() as connection:
            _upsert_profile_for_supabase_user(connection, user_payload)
            connection.commit()
        return {"user_id": user_id, "session_id": session_id}

    token_hash = _hash_token(token)
    current_time = now_iso()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT s.id AS session_id, s.user_id, s.expires_at, s.is_active
            FROM sessions s
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not row or not int(row["is_active"] or 0):
            return None
        expires_at = _parse_iso(row["expires_at"])
        if expires_at and expires_at <= _now_datetime():
            connection.execute("UPDATE sessions SET is_active = 0 WHERE token_hash = ?", (token_hash,))
            connection.commit()
            return None
        if touch:
            connection.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                (current_time, _session_expiry(), token_hash),
            )
            connection.commit()
        return {"user_id": str(row["user_id"]), "session_id": str(row["session_id"])}


def get_user_by_token(token: str, touch: bool = True) -> dict[str, Any] | None:
    context = get_auth_context_by_token(token, touch=touch)
    if not context:
        return None
    with get_connection() as connection:
        return _public_user_payload(connection, context["user_id"])


def get_user_id_by_token(token: str) -> str | None:
    context = get_auth_context_by_token(token, touch=False)
    return str(context["user_id"]) if context else None


def logout_user(token: str) -> None:
    if _supabase_auth_enabled():
        try:
            _supabase_request("POST", "/auth/v1/logout", token=token)
        except Exception:
            return
        return
    token_hash = _hash_token(token)
    with get_connection() as connection:
        connection.execute("UPDATE sessions SET is_active = 0 WHERE token_hash = ?", (token_hash,))
        connection.commit()


def _decayed_score(score: float, updated_at: str | None) -> float:
    updated = _parse_iso(updated_at)
    if not updated:
        return score
    age_days = max((_now_datetime() - updated).total_seconds() / 86400.0, 0.0)
    if age_days <= 7:
        factor = 1.0
    else:
        factor = max(0.35, 1.0 - ((age_days - 7.0) * 0.03))
    return score * factor


def _bump_affinity(connection: sqlite3.Connection, user_id: str, affinity_type: str, affinity_key: str, delta: float, current_time: str) -> None:
    normalized_key = normalize_whitespace(affinity_key).lower()
    if not normalized_key or delta <= 0:
        return
    connection.execute(
        """
        INSERT INTO user_affinities (user_id, affinity_type, affinity_key, score, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, affinity_type, affinity_key) DO UPDATE SET
          score = user_affinities.score + excluded.score,
          updated_at = excluded.updated_at
        """,
        (user_id, affinity_type, normalized_key, delta, current_time),
    )


def get_top_interests(user_id: str, limit: int = 6, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    if connection is None:
        with get_connection() as conn:
            return get_top_interests(user_id, limit, connection=conn)

    rows = connection.execute(
        """
        SELECT affinity_type, affinity_key, score, updated_at
        FROM user_affinities
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchall()
    scored = [
        {
            "type": row["affinity_type"],
            "key": row["affinity_key"],
            "score": round(_decayed_score(float(row["score"]), row["updated_at"]), 2),
        }
        for row in rows
    ]
    scored = [row for row in scored if row["score"] > 0]
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored[:limit]


def _event_filter_clause(session_id: str | None) -> tuple[str, list[Any]]:
    if not session_id:
        return "", []
    return " AND ue.session_id = ?", [session_id]


def _dominant_category_id(
    recent_category_bias: dict[str, float],
    affinities: dict[tuple[str, str], float],
) -> str | None:
    category_scores: dict[str, float] = {}
    for category_id, score in recent_category_bias.items():
        normalized_category = normalize_whitespace(category_id).lower()
        if normalized_category:
            category_scores[normalized_category] = category_scores.get(normalized_category, 0.0) + float(score)
    for (affinity_type, affinity_key), score in affinities.items():
        if affinity_type != "category":
            continue
        normalized_category = normalize_whitespace(affinity_key).lower()
        if normalized_category:
            category_scores[normalized_category] = category_scores.get(normalized_category, 0.0) + (float(score) * 0.6)
    ranked = [
        (category_id, score)
        for category_id, score in sorted(category_scores.items(), key=lambda item: item[1], reverse=True)
        if category_id and category_id != "others"
    ]
    if not ranked:
        return None
    top_category, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score < 3.5:
        return None
    if (top_score - second_score) < 1.5 and top_score < (second_score * 1.35):
        return None
    return top_category


def _build_event_affinities(
    connection: sqlite3.Connection,
    user_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    event_filter, params = _event_filter_clause(session_id)
    rows = connection.execute(
        f"""
        SELECT ue.*, p.tags_json, p.brand, p.provider, p.category_id AS product_category_id
        FROM user_events ue
        LEFT JOIN products p ON p.id = ue.product_id
        WHERE ue.user_id = ?{event_filter}
        ORDER BY ue.created_at DESC
        LIMIT 200
        """,
        [user_id, *params],
    ).fetchall()
    affinities: dict[tuple[str, str], float] = {}
    recent_category_bias: dict[str, float] = {}
    recent_tag_bias: dict[str, float] = {}
    viewed_ids: set[str] = set()
    for index, row in enumerate(rows):
        recency = max(0.35, 1.0 - (index * 0.04))
        event_type = str(row["event_type"])
        category_id = normalize_whitespace(row["category_id"] or row["product_category_id"]).lower()
        if row["product_id"] and event_type in {"product_view", "source_open"}:
            viewed_ids.add(str(row["product_id"]))
        if category_id:
            base_delta = 1.5 if event_type == "category_view" else 2.2 if event_type == "product_view" else 3.4 if event_type == "source_open" else 0.8
            affinities[("category", category_id)] = affinities.get(("category", category_id), 0.0) + (base_delta * recency)
            recent_category_bias[category_id] = recent_category_bias.get(category_id, 0.0) + (base_delta * recency)
        if row["query_text"]:
            for token in tokenize(str(row["query_text"])):
                affinities[("tag", token)] = affinities.get(("tag", token), 0.0) + (2.0 * recency)
                recent_tag_bias[token] = recent_tag_bias.get(token, 0.0) + (2.4 * recency)
        row_tags = _decode_json_array(row["tags_json"])
        tag_delta = 2.0 if event_type == "product_view" else 4.0 if event_type == "source_open" else 0.0
        for tag in row_tags:
            normalized_tag = normalize_whitespace(tag).lower()
            if not normalized_tag:
                continue
            affinities[("tag", normalized_tag)] = affinities.get(("tag", normalized_tag), 0.0) + (tag_delta * recency)
            recent_tag_bias[normalized_tag] = recent_tag_bias.get(normalized_tag, 0.0) + (tag_delta * recency)
        brand = normalize_whitespace(row["brand"]).lower()
        site = normalize_whitespace(row["provider"]).lower()
        if brand:
            brand_delta = 1.0 if event_type == "product_view" else 2.0 if event_type == "source_open" else 0.0
            affinities[("brand", brand)] = affinities.get(("brand", brand), 0.0) + (brand_delta * recency)
        if site and event_type == "source_open":
            affinities[("site", site)] = affinities.get(("site", site), 0.0) + (1.0 * recency)
    return {
        "affinities": affinities,
        "recentCategoryBias": recent_category_bias,
        "recentTagBias": recent_tag_bias,
        "viewedIds": viewed_ids,
    }


def _build_favorite_affinities(connection: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
          uf.product_id AS favorite_product_id,
          uf.product_snapshot_json AS favorite_product_snapshot_json,
          p.*
        FROM user_favorites uf
        LEFT JOIN products p ON p.id = uf.product_id
        WHERE uf.user_id = ?
        ORDER BY uf.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    affinities: dict[tuple[str, str], float] = {}
    category_bias: dict[str, float] = {}
    favorite_ids: set[str] = set()
    favorite_family_keys: set[tuple[str, str]] = set()
    for row in rows:
        snapshot = _restore_snapshot(row["favorite_product_snapshot_json"]) or {}
        provider = normalize_whitespace(row["provider"] if row["id"] else snapshot.get("provider")).lower()
        category_id = normalize_whitespace(row["category_id"] if row["id"] else snapshot.get("categoryId")).lower()
        brand = normalize_whitespace(row["brand"] if row["id"] else snapshot.get("brand")).lower()
        tags = (
            _decode_json_array(row["tags_json"])
            if row["id"]
            else [str(item) for item in snapshot.get("tags", []) if isinstance(item, str)]
        )
        family_key = normalize_whitespace(row["family_key"] if row["id"] else snapshot.get("familyKey"))
        favorite_id = normalize_whitespace(row["id"] if row["id"] else row["favorite_product_id"])
        if favorite_id:
            favorite_ids.add(favorite_id)
        if provider and family_key:
            favorite_family_keys.add((provider, family_key))
        if category_id:
            affinities[("category", category_id)] = affinities.get(("category", category_id), 0.0) + 5.0
            category_bias[category_id] = category_bias.get(category_id, 0.0) + 5.0
        if brand:
            affinities[("brand", brand)] = affinities.get(("brand", brand), 0.0) + 3.0
        if provider:
            affinities[("site", provider)] = affinities.get(("site", provider), 0.0) + 0.8
        for tag in tags:
            normalized_tag = normalize_whitespace(tag).lower()
            if normalized_tag:
                affinities[("tag", normalized_tag)] = affinities.get(("tag", normalized_tag), 0.0) + 1.5
    return {
        "affinities": affinities,
        "categoryBias": category_bias,
        "favoriteIds": favorite_ids,
        "favoriteFamilyKeys": favorite_family_keys,
    }


def _favorite_signal_strength(
    favorite_affinities: dict[tuple[str, str], float],
    *,
    category_id: str,
    brand: str,
    provider: str,
    tags: list[str],
) -> float:
    score = 0.0
    score += favorite_affinities.get(("category", category_id), 0.0) * 2.2
    score += favorite_affinities.get(("brand", brand), 0.0) * 1.45
    score += favorite_affinities.get(("site", provider), 0.0) * 0.45
    score += sum(favorite_affinities.get(("tag", tag.lower()), 0.0) for tag in tags) * 0.9
    return score


def _build_history_trending_products(
    connection: sqlite3.Connection,
    user_id: str,
    *,
    limit: int = 6,
    session_id: str | None = None,
    exclude_ids: set[str] | None = None,
    exclude_family_keys: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    event_filter, params = _event_filter_clause(session_id)
    event_rows = connection.execute(
        f"""
        SELECT ue.*, p.tags_json, p.brand, p.provider, p.category_id AS product_category_id
        FROM user_events ue
        LEFT JOIN products p ON p.id = ue.product_id
        WHERE ue.user_id = ?{event_filter}
          AND ue.event_type IN ('search', 'product_view', 'source_open')
        ORDER BY ue.created_at DESC
        LIMIT 120
        """,
        [user_id, *params],
    ).fetchall()
    if not event_rows:
        return []

    tag_scores: dict[str, float] = {}
    category_scores: dict[str, float] = {}
    brand_scores: dict[str, float] = {}
    site_scores: dict[str, float] = {}
    viewed_ids: set[str] = set(exclude_ids or set())
    for index, row in enumerate(event_rows):
        recency = max(0.3, 1.0 - (index * 0.045))
        event_type = str(row["event_type"])
        if row["product_id"] and event_type in {"product_view", "source_open"}:
            viewed_ids.add(str(row["product_id"]))
        category_id = normalize_whitespace(row["category_id"] or row["product_category_id"]).lower()
        if category_id:
            category_scores[category_id] = category_scores.get(category_id, 0.0) + (
                (2.2 if event_type == "product_view" else 3.1 if event_type == "source_open" else 1.4) * recency
            )
        if row["query_text"]:
            for token in tokenize(str(row["query_text"])):
                tag_scores[token] = tag_scores.get(token, 0.0) + (1.8 * recency)
        for tag in _decode_json_array(row["tags_json"]):
            normalized_tag = normalize_whitespace(tag).lower()
            if normalized_tag:
                tag_scores[normalized_tag] = tag_scores.get(normalized_tag, 0.0) + (
                    (1.8 if event_type == "product_view" else 2.4 if event_type == "source_open" else 0.0) * recency
                )
        brand = normalize_whitespace(row["brand"]).lower()
        if brand:
            brand_scores[brand] = brand_scores.get(brand, 0.0) + (
                (0.8 if event_type == "product_view" else 1.4 if event_type == "source_open" else 0.0) * recency
            )
        provider = normalize_whitespace(row["provider"]).lower()
        if provider and event_type == "source_open":
            site_scores[provider] = site_scores.get(provider, 0.0) + (0.7 * recency)

    candidate_rows = connection.execute(
        "SELECT * FROM products WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 400"
    ).fetchall()
    ranked: list[tuple[float, sqlite3.Row]] = []
    seen_keys: set[str] = set()
    for row in candidate_rows:
        product_id = str(row["id"])
        provider = normalize_whitespace(row["provider"]).lower()
        family_key = normalize_whitespace(row["family_key"])
        if product_id in viewed_ids:
            continue
        if exclude_family_keys and provider and family_key and (provider, family_key) in exclude_family_keys:
            continue
        tags = _decode_json_array(row["tags_json"])
        row_category_id = normalize_whitespace(row["category_id"]).lower()
        brand = normalize_whitespace(row["brand"]).lower()
        category_signal = category_scores.get(row_category_id, 0.0)
        tag_signal = sum(tag_scores.get(tag.lower(), 0.0) for tag in tags)
        brand_signal = brand_scores.get(brand, 0.0)
        site_signal = site_scores.get(provider, 0.0)
        if category_signal <= 0 and tag_signal <= 0 and brand_signal <= 0 and site_signal <= 0:
            continue
        history_score = category_signal * 1.35
        history_score += tag_signal * 0.55
        history_score += brand_signal * 0.45
        history_score += site_signal * 0.2
        history_score += float(row["rating"] or 0.0) * 0.55
        history_score += min(int(row["review_count"] or 0), 200) / 90.0
        if history_score < 2.25:
            continue
        key = _row_identity_key(row)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ranked.append((history_score, row))

    ranked.sort(key=lambda item: (item[0], float(item[1]["rating"] or 0.0), int(item[1]["review_count"] or 0)), reverse=True)
    return [_row_to_product(row) for _, row in ranked[:limit]]


def record_user_event(
    user_id: str,
    event_type: str,
    product_id: str | None = None,
    category_id: str | None = None,
    query_text: str | None = None,
    source_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> None:
    current_time = now_iso()
    metadata = metadata or {}
    origin_surface = normalize_whitespace(
        metadata.get("originSurface") if isinstance(metadata.get("originSurface"), str) else metadata.get("origin_surface")
    ).lower() or ("catalog" if event_type == "product_view" else "unknown")
    normalized_session_id = normalize_whitespace(session_id) or None
    if event_type == "product_view" and origin_surface not in ALLOWED_PRODUCT_VIEW_ORIGIN_SURFACES:
        return
    with get_connection() as connection:
        product_row = None
        if product_id:
            product_row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        resolved_category_id = category_id or (str(product_row["category_id"]) if product_row else None)
        canonical_source_url = normalize_whitespace(
            metadata.get("canonicalSourceUrl")
            or metadata.get("canonical_source_url")
            or (product_row["canonical_source_url"] if product_row else source_url)
            or source_url
        ) or None
        source_url_value = normalize_whitespace(source_url or (product_row["source_url"] if product_row else canonical_source_url)) or None
        product_snapshot = (
            metadata.get("productSnapshot")
            if isinstance(metadata.get("productSnapshot"), dict)
            else metadata.get("product_snapshot")
            if isinstance(metadata.get("product_snapshot"), dict)
            else _snapshot_from_row(product_row)
        )
        normalized_query = normalize_whitespace(query_text) or None
        existing_event = None
        if event_type == "search" and normalized_query:
            existing_event = connection.execute(
                """
                SELECT id
                FROM user_events
                WHERE user_id = ?
                  AND COALESCE(session_id, '') = COALESCE(?, '')
                  AND event_type = 'search'
                  AND lower(COALESCE(query_text, '')) = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, normalized_session_id, normalized_query.lower()),
            ).fetchone()
        elif event_type == "product_view" and product_id:
            existing_event = connection.execute(
                """
                SELECT id
                FROM user_events
                WHERE user_id = ?
                  AND COALESCE(session_id, '') = COALESCE(?, '')
                  AND event_type = 'product_view'
                  AND product_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, normalized_session_id, product_id),
            ).fetchone()

        if existing_event:
            connection.execute(
                """
                UPDATE user_events
                SET category_id = ?,
                    query_text = ?,
                    source_url = ?,
                    canonical_source_url = ?,
                    product_snapshot_json = ?,
                    metadata_json = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    resolved_category_id,
                    normalized_query,
                    source_url_value,
                    canonical_source_url,
                    json_dumps(product_snapshot),
                    json_dumps(metadata),
                    current_time,
                    str(existing_event["id"]),
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO user_events (
                  id, user_id, session_id, event_type, product_id, category_id, query_text, source_url,
                  canonical_source_url, product_snapshot_json, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    secrets.token_hex(12),
                    user_id,
                    normalized_session_id,
                    event_type,
                    product_id,
                    resolved_category_id,
                    normalized_query,
                    source_url_value,
                    canonical_source_url,
                    json_dumps(product_snapshot),
                    json_dumps(metadata),
                    current_time,
                ),
            )

        if resolved_category_id:
            category_delta = 1.5 if event_type == "category_view" else 2.0 if event_type == "product_view" else 3.0 if event_type == "source_open" else 0.0
            _bump_affinity(connection, user_id, "category", resolved_category_id, category_delta, current_time)

        if event_type == "search" and query_text:
            for token in tokenize(query_text):
                _bump_affinity(connection, user_id, "tag", token, 2.0, current_time)

        if product_row:
            tags = _decode_json_array(product_row["tags_json"])
            brand = normalize_whitespace(product_row["brand"]).lower()
            site = normalize_whitespace(product_row["provider"]).lower()
            tag_delta = 2.0 if event_type == "product_view" else 4.0 if event_type == "source_open" else 0.0
            brand_delta = 1.0 if event_type == "product_view" else 2.0 if event_type == "source_open" else 0.0
            site_delta = 1.0 if event_type == "source_open" else 0.0
            for tag in tags:
                _bump_affinity(connection, user_id, "tag", tag, tag_delta, current_time)
            if brand:
                _bump_affinity(connection, user_id, "brand", brand, brand_delta, current_time)
            if site:
                _bump_affinity(connection, user_id, "site", site, site_delta, current_time)

        connection.commit()


def invalidate_user_recommendations(user_id: str) -> None:
    with _write_connection() as connection:
        connection.execute("DELETE FROM user_recommendations WHERE user_id = ?", (user_id,))
        connection.commit()


def add_user_favorite(user_id: str, product_id: str) -> dict[str, Any]:
    current_time = now_iso()
    with _write_connection() as connection:
        product_row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product_row:
            raise ValueError("Product not found.")
        snapshot = _snapshot_from_row(product_row)
        connection.execute(
            """
            INSERT INTO user_favorites (user_id, product_id, canonical_source_url, product_snapshot_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, product_id) DO UPDATE SET
              canonical_source_url=excluded.canonical_source_url,
              product_snapshot_json=excluded.product_snapshot_json,
              updated_at=excluded.updated_at
            """,
            (
                user_id,
                product_id,
                normalize_whitespace(product_row["canonical_source_url"]),
                json_dumps(snapshot),
                current_time,
                current_time,
            ),
        )
        connection.commit()
    invalidate_user_recommendations(user_id)
    favorite = get_user_favorite(user_id, product_id)
    if not favorite:
        raise ValueError("Favorite could not be stored.")
    return favorite


def remove_user_favorite(user_id: str, product_id: str) -> bool:
    with _write_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM user_favorites WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
        connection.commit()
    invalidate_user_recommendations(user_id)
    return bool(cursor.rowcount)


def get_user_favorite(user_id: str, product_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
              uf.user_id AS favorite_user_id,
              uf.product_id AS favorite_product_id,
              uf.canonical_source_url AS favorite_canonical_source_url,
              uf.product_snapshot_json AS favorite_product_snapshot_json,
              uf.created_at AS favorite_created_at,
              uf.updated_at AS favorite_updated_at,
              p.*
            FROM user_favorites uf
            LEFT JOIN products p ON p.id = uf.product_id
            WHERE uf.user_id = ? AND uf.product_id = ?
            """,
            (user_id, product_id),
        ).fetchone()
    if not row:
        return None
    if row["id"]:
        product = _row_to_product(row)
    else:
        product = _restore_snapshot(row["favorite_product_snapshot_json"]) or {}
    if not product:
        return None
    product["isFavorite"] = True
    product["favoritedAt"] = row["favorite_created_at"]
    deduped = _dedupe_product_list([product])
    return deduped[0] if deduped else None


def list_user_favorites(user_id: str, page: int, page_size: int) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
              uf.user_id AS favorite_user_id,
              uf.product_id AS favorite_product_id,
              uf.canonical_source_url AS favorite_canonical_source_url,
              uf.product_snapshot_json AS favorite_product_snapshot_json,
              uf.created_at AS favorite_created_at,
              uf.updated_at AS favorite_updated_at,
              p.*
            FROM user_favorites uf
            LEFT JOIN products p ON p.id = uf.product_id
            WHERE uf.user_id = ?
            ORDER BY uf.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        if row["id"]:
            product = _row_to_product(row)
        else:
            product = _restore_snapshot(row["favorite_product_snapshot_json"]) or {}
        if not product:
            continue
        product["isFavorite"] = True
        product["favoritedAt"] = row["favorite_created_at"]
        items.append(product)
    deduped_items = _dedupe_product_list(items)
    total = len(deduped_items)
    return {
        "items": deduped_items[offset : offset + page_size],
        "page": page,
        "pageSize": page_size,
        "hasMore": page * page_size < total,
    }


def refresh_user_recommendations(user_id: str, limit: int = 120, session_id: str | None = None) -> None:
    started_at = time.perf_counter()
    current_time = now_iso()
    with get_connection() as connection:
        favorite_context = _build_favorite_affinities(connection, user_id)
        preference_context = _build_event_affinities(connection, user_id, session_id=session_id)
        favorite_affinities = favorite_context["affinities"]
        affinities = preference_context["affinities"]
        recent_category_bias = preference_context["recentCategoryBias"]
        recent_tag_bias = preference_context["recentTagBias"]
        viewed_ids = preference_context["viewedIds"]
        favorite_category_bias = favorite_context["categoryBias"]
        favorite_ids = favorite_context["favoriteIds"]
        favorite_family_keys = favorite_context["favoriteFamilyKeys"]
        has_favorite_context = bool(favorite_ids or favorite_family_keys or favorite_affinities)
        dominant_category_id = _dominant_category_id(favorite_category_bias, favorite_affinities)
        if not dominant_category_id:
            dominant_category_id = _dominant_category_id(recent_category_bias, affinities)
        cleared = connection.execute("DELETE FROM user_recommendations WHERE user_id = ?", (user_id,)).rowcount
        rows = connection.execute(
            "SELECT * FROM products WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 400"
        ).fetchall()
        scored: list[tuple[float, str, sqlite3.Row]] = []
        for row in rows:
            row_category_id = normalize_whitespace(row["category_id"]).lower()
            row_provider = normalize_whitespace(row["provider"]).lower()
            row_family_key = normalize_whitespace(row["family_key"])
            if row["id"] in favorite_ids:
                continue
            if row_provider and row_family_key and (row_provider, row_family_key) in favorite_family_keys:
                continue
            if dominant_category_id and row_category_id != dominant_category_id:
                continue
            tags = _decode_json_array(row["tags_json"])
            row_brand = normalize_whitespace(row["brand"]).lower()
            favorite_signal_score = _favorite_signal_strength(
                favorite_affinities,
                category_id=row_category_id,
                brand=row_brand,
                provider=row_provider,
                tags=tags,
            )
            recent_signal_score = recent_category_bias.get(row_category_id, 0.0) + (
                sum(recent_tag_bias.get(tag.lower(), 0.0) for tag in tags) * 0.35
            )
            if has_favorite_context and favorite_signal_score <= 0 and recent_signal_score <= 0:
                continue

            score = float(row["rating"] or 0.0) * 1.75
            score += min(int(row["review_count"] or 0), 200) / 28.0
            score += favorite_signal_score
            score += affinities.get(("category", row_category_id), 0.0) * 1.2
            score += affinities.get(("brand", row_brand), 0.0) * 0.55
            score += sum(affinities.get(("tag", tag.lower()), 0.0) for tag in tags) * 0.35
            score += affinities.get(("site", row_provider), 0.0) * 0.2
            score += recent_signal_score
            reason = row["category"]
            if tags:
                top_tag = max(
                    tags,
                    key=lambda tag: favorite_affinities.get(("tag", tag.lower()), 0.0) + affinities.get(("tag", tag.lower()), 0.0),
                )
                if favorite_affinities.get(("tag", top_tag.lower()), 0.0) > 0 or affinities.get(("tag", top_tag.lower()), 0.0) > 0:
                    reason = top_tag
            if dominant_category_id and row_category_id == dominant_category_id:
                score += 3.0
                reason = category_name(dominant_category_id)
            if row["id"] in viewed_ids:
                score *= 0.3
            if has_favorite_context and favorite_signal_score < 1.2 and recent_signal_score < 2.0:
                score *= 0.55
            scored.append((score, reason, row))

        scored.sort(key=lambda item: (item[0], float(item[2]["rating"] or 0.0), int(item[2]["review_count"] or 0)), reverse=True)
        seen_keys: set[str] = set()
        inserted = 0
        for score, reason, row in scored:
            key = _row_identity_key(row)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            connection.execute(
                """
                INSERT OR REPLACE INTO user_recommendations (user_id, product_id, score, reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, row["id"], round(score, 4), f"Based on {reason}", current_time),
            )
            inserted += 1
            if inserted >= limit:
                break
        connection.commit()
    logger.info(
        "Refreshed recommendations for user %s (cleared=%s inserted=%s duration_ms=%d)",
        user_id,
        cleared,
        inserted,
        round((time.perf_counter() - started_at) * 1000),
    )


def list_user_recommendations(user_id: str, page: int, page_size: int, session_id: str | None = None) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    with get_connection() as connection:
        try:
            refresh_user_recommendations(user_id, session_id=session_id)
        except Exception as exc:
            logger.warning("Skipping recommendation refresh for user %s: %s", user_id, exc)
        rows = connection.execute(
            """
            SELECT p.*, ur.reason
            FROM user_recommendations ur
            INNER JOIN products p ON p.id = ur.product_id
            WHERE ur.user_id = ?
            ORDER BY ur.score DESC, p.updated_at DESC
            """,
            (user_id,),
        ).fetchall()
        deduped_rows = _dedupe_rows(rows)
        preference_context = _build_event_affinities(connection, user_id, session_id=session_id)
        favorite_context = _build_favorite_affinities(connection, user_id)
        based_on_lookup: dict[str, float] = {}
        for (_, key), score in favorite_context["affinities"].items():
            based_on_lookup[key] = based_on_lookup.get(key, 0.0) + float(score)
        for (_, key), score in preference_context["affinities"].items():
            based_on_lookup[key] = based_on_lookup.get(key, 0.0) + (float(score) * 0.6)
        based_on = [
            key
            for key, score in sorted(
                based_on_lookup.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            if score > 0
        ]
        items = annotate_products_with_favorites(
            [_row_to_product(row) for row in deduped_rows[offset : offset + page_size]],
            user_id,
            connection=connection,
        )
        recommended_ids = {str(row["id"]) for row in deduped_rows if row["id"]}
        trending = annotate_products_with_favorites(
            _build_history_trending_products(
                connection,
                user_id,
                limit=6,
                session_id=session_id,
                exclude_ids=recommended_ids | favorite_context["favoriteIds"],
                exclude_family_keys=favorite_context["favoriteFamilyKeys"],
            ),
            user_id,
            connection=connection,
        )
        return {
            "items": items,
            "page": page,
            "pageSize": page_size,
            "hasMore": page * page_size < len(deduped_rows),
            "basedOn": based_on,
            "trending": trending,
        }


def list_user_history(user_id: str, page: int, page_size: int, session_id: str | None = None) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    try:
        with get_connection() as connection:
            event_filter, params = _event_filter_clause(session_id)
            all_rows = connection.execute(
                f"""
                SELECT ue.*, p.title AS product_title, p.category AS product_category
                FROM user_events ue
                LEFT JOIN products p ON p.id = ue.product_id
                WHERE ue.user_id = ?{event_filter}
                  AND ue.event_type IN ('search', 'product_view')
                ORDER BY ue.created_at DESC
                """,
                (user_id, *params),
            ).fetchall()
            rows: list[sqlite3.Row] = []
            seen_history_keys: set[str] = set()
            for row in all_rows:
                history_key = (
                    f"search::{normalize_whitespace(row['query_text']).lower()}"
                    if str(row["event_type"]) == "search"
                    else f"product::{normalize_whitespace(row['product_id'])}"
                )
                if history_key in seen_history_keys:
                    continue
                seen_history_keys.add(history_key)
                rows.append(row)
            items = []
            for row in rows[offset : offset + page_size]:
                event_type = str(row["event_type"])
                product_snapshot = _restore_snapshot(row["product_snapshot_json"])
                title = (
                    row["product_title"]
                    or (product_snapshot or {}).get("name")
                    or row["query_text"]
                    or category_name(str(row["category_id"] or "others"))
                )
                source_host = ""
                effective_source_url = normalize_whitespace(
                    row["source_url"] or row["canonical_source_url"] or (product_snapshot or {}).get("sourceUrl")
                )
                if effective_source_url:
                    parts = effective_source_url.split("/")
                    source_host = parts[2] if len(parts) > 2 else normalize_whitespace(row["source_url"])
                subtitle = {
                    "search": f'Searched for "{row["query_text"]}"',
                    "product_view": f'Viewed {row["product_category"] or (product_snapshot or {}).get("category") or "product"}',
                    "source_open": f"Opened seller page on {source_host or 'store'}",
                    "category_view": f'Browsed {category_name(str(row["category_id"] or "others"))}',
                }.get(event_type, event_type.replace("_", " ").title())
                items.append(
                    {
                        "id": row["id"],
                        "type": event_type,
                        "title": title,
                        "subtitle": subtitle,
                        "productId": row["product_id"],
                        "categoryId": row["category_id"],
                        "queryText": row["query_text"],
                        "sourceUrl": effective_source_url or None,
                        "canonicalSourceUrl": normalize_whitespace(
                            row["canonical_source_url"] or (product_snapshot or {}).get("sourceUrl")
                        )
                        or None,
                        "productSnapshot": product_snapshot,
                        "createdAt": row["created_at"],
                    }
                )
            total = len(rows)
            return {
                "items": items,
                "page": page,
                "pageSize": page_size,
                "total": total,
                "hasMore": page * page_size < total,
            }
    except Exception as exc:
        logger.warning("Falling back to empty history for user %s: %s", user_id, exc)
        return {
            "items": [],
            "page": page,
            "pageSize": page_size,
            "total": 0,
            "hasMore": False,
        }
