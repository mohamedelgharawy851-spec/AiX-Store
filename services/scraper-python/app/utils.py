from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from datetime import datetime, timezone
from html import unescape
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

from .config import (
    CATEGORY_CONFIG,
    CATEGORY_SCORE_MARGIN,
    CATEGORY_STRICT_SCORE_THRESHOLD,
    MAX_QUERY_VARIANTS,
    QUERY_RETRY_ATTEMPTS,
    SEARCH_ACRONYM_MAP,
    SEARCH_SYNONYM_MAP,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_whitespace(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def slugify(value: str | None) -> str:
    text = normalize_whitespace(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def canonicalize_url(value: str | None) -> str:
    raw = normalize_whitespace(value)
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme:
        return raw
    hostname = parsed.hostname or ""
    cleaned = parsed._replace(
        netloc=hostname,
        params="",
        query="" if hostname.endswith("amazon.com") else parsed.query,
        fragment="",
    )
    return urlunparse(cleaned)


def absolute_url(base_url: str, href: str | None) -> str:
    raw = normalize_whitespace(href)
    if not raw:
        return ""
    return canonicalize_url(urljoin(base_url, raw))


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", value.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def to_cents(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value) * 100))


def from_cents(value: int | None) -> float:
    return round((value or 0) / 100.0, 2)


def normalize_offer_prices(
    price: float | int | None,
    original_price: float | int | None = None,
    *,
    max_discount_percent: float = 95.0,
) -> tuple[float | None, float | None]:
    if price is None:
        return None, None
    try:
        normalized_price = round(float(price), 2)
    except (TypeError, ValueError):
        return None, None
    if normalized_price <= 0:
        return None, None

    if original_price is None:
        return normalized_price, None

    try:
        normalized_original_price = round(float(original_price), 2)
    except (TypeError, ValueError):
        return normalized_price, None

    if normalized_original_price <= normalized_price:
        return normalized_price, None

    discount_percent = ((normalized_original_price - normalized_price) / normalized_original_price) * 100.0
    if discount_percent <= 0 or discount_percent >= max_discount_percent:
        return normalized_price, None

    return normalized_price, normalized_original_price


def extract_first_number(value: str | None) -> int:
    if not value:
        return 0
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else 0


def tokenize(*parts: str | None) -> list[str]:
    seen: list[str] = []
    for part in parts:
        for token in re.findall(r"[a-z0-9]+", normalize_whitespace(part).lower()):
            if len(token) < 2:
                continue
            candidates = [token]
            if token.endswith("ies") and len(token) > 4:
                candidates.append(f"{token[:-3]}y")
            elif token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
                candidates.append(token[:-1])
            for candidate in candidates:
                if candidate not in seen:
                    seen.append(candidate)
    return seen


def singularize_token(token: str) -> str:
    text = normalize_whitespace(token).lower()
    if text.endswith("ies") and len(text) > 4:
        return f"{text[:-3]}y"
    if text.endswith("s") and len(text) > 4 and not text.endswith("ss"):
        return text[:-1]
    return text


BOOK_FORMAT_TERMS = (
    "paperback",
    "hardcover",
    "board book",
    "mass market",
    "spiral bound",
    "library binding",
    "picture book",
    "workbook",
    "study guide",
)


def looks_like_book_product(*parts: str | None) -> bool:
    text = " ".join(normalize_whitespace(part).lower() for part in parts if normalize_whitespace(part))
    if not text:
        return False
    return any(term in text for term in BOOK_FORMAT_TERMS)


def infer_category_id(*parts: str | None) -> str:
    query = " ".join(normalize_whitespace(part).lower() for part in parts if part)
    if looks_like_book_product(query):
        return "others"
    for category_id, config in CATEGORY_CONFIG.items():
        keywords = config.get("keywords", [])
        if any(keyword in query for keyword in keywords):
            return category_id
    return "others"


def classify_category(
    *parts: str | None,
    source_category_id: str | None = None,
    extra_terms: list[str] | None = None,
) -> dict[str, object]:
    normalized_parts = [normalize_whitespace(part).lower() for part in parts if normalize_whitespace(part)]
    text = " ".join(normalized_parts)
    book_like = looks_like_book_product(text, " ".join(extra_terms or []))
    tokens = tokenize(text, *(extra_terms or []))
    token_set = {singularize_token(token) for token in tokens}
    scores: dict[str, float] = {}
    matched_terms: dict[str, list[str]] = {}

    def matches_term(term: str) -> bool:
        candidate = normalize_whitespace(term).lower()
        if not candidate:
            return False
        if " " in candidate:
            return candidate in text
        return singularize_token(candidate) in token_set

    for category_id, config in CATEGORY_CONFIG.items():
        score = 0.0
        matches: list[str] = []
        for phrase in config.get("strong_phrases", []):
            candidate = normalize_whitespace(str(phrase)).lower()
            if candidate and candidate in text:
                score += 3.5
                matches.append(candidate)
        for term in config.get("include_terms", []):
            candidate = normalize_whitespace(str(term)).lower()
            if matches_term(candidate):
                score += 1.6
                matches.append(candidate)
        for term in config.get("keywords", []):
            candidate = normalize_whitespace(str(term)).lower()
            if matches_term(candidate):
                score += 1.2
                matches.append(candidate)
        for term in config.get("query_bonus_terms", []):
            candidate = normalize_whitespace(str(term)).lower()
            if matches_term(candidate):
                score += 0.8
                matches.append(candidate)
        for term in config.get("exclude_terms", []):
            candidate = normalize_whitespace(str(term)).lower()
            if matches_term(candidate):
                score -= 2.4
        if source_category_id == category_id:
            score += 1.4
        scores[category_id] = round(score, 3)
        matched_terms[category_id] = sorted(set(matches))

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_category, best_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0
    candidates = [
        {
            "category_id": category_id,
            "category": category_name(category_id),
            "score": float(score),
            "matched_terms": matched_terms.get(category_id, []),
        }
        for category_id, score in ordered[:3]
    ]
    if book_like:
        matched_terms["others"] = sorted(set([*matched_terms.get("others", []), "book_like"]))
        best_category = "others"
    elif best_score < CATEGORY_STRICT_SCORE_THRESHOLD or (best_score - second_score) < CATEGORY_SCORE_MARGIN:
        best_category = "others"
    return {
        "category_id": best_category,
        "category": category_name(best_category),
        "confidence": float(best_score if best_category != "others" else 0.0),
        "scores": scores,
        "matched_terms": matched_terms.get(best_category, []),
        "candidates": candidates,
    }


def expand_query_variants(query: str, category_id: str | None = None) -> list[str]:
    normalized_query = normalize_whitespace(query).lower()
    if not normalized_query:
        return []
    rewritten_variants: list[str] = []
    fallback_variants: list[str] = [normalized_query]
    tokens = tokenize(normalized_query)

    def add_rewritten(candidate: str) -> None:
        value = normalize_whitespace(candidate).lower()
        if value and value not in rewritten_variants:
            rewritten_variants.append(value)

    def add_fallback(candidate: str) -> None:
        value = normalize_whitespace(candidate).lower()
        if value and value not in rewritten_variants and value not in fallback_variants:
            fallback_variants.append(value)

    for phrase, related in SEARCH_SYNONYM_MAP.items():
        if phrase in normalized_query:
            for candidate in related:
                add_rewritten(candidate)

    for token in tokens:
        for candidate in SEARCH_SYNONYM_MAP.get(token, []):
            add_rewritten(candidate)
        for candidate in SEARCH_ACRONYM_MAP.get(token, []):
            add_rewritten(candidate)
        singular = singularize_token(token)
        if singular and singular != token:
            add_fallback(singular)

    variants = [*rewritten_variants, *fallback_variants]
    deduped: list[str] = []
    for variant in variants:
        if variant and variant not in deduped:
            deduped.append(variant)
    return deduped[:MAX_QUERY_VARIANTS]


def expand_discovery_variants(query: str, category_id: str | None = None) -> list[str]:
    base_variants = expand_query_variants(query, category_id)
    if not base_variants:
        return []

    retailers = ["target", "walmart", "amazon"]
    weighted: list[str] = []

    def add_variant(candidate: str) -> None:
        value = normalize_whitespace(candidate).lower()
        if value and value not in weighted:
            weighted.append(value)

    for variant in base_variants:
        retailer_in_query = next((retailer for retailer in retailers if retailer in variant.split()), None)
        if retailer_in_query:
            add_variant(variant)
            continue
        for retailer in retailers:
            add_variant(f"{variant} {retailer}")
    for variant in base_variants:
        add_variant(variant)
    return weighted[:MAX_QUERY_VARIANTS]


def category_name(category_id: str) -> str:
    return str(CATEGORY_CONFIG.get(category_id, CATEGORY_CONFIG["others"])["name"])


def product_id_for_url(source_url: str) -> str:
    return hash_text(canonicalize_url(source_url))[:16]


def json_dumps(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
    )


def decode_srcset(value: str | None) -> str:
    if not value:
        return ""
    parts = [segment.strip().split(" ")[0] for segment in value.split(",") if segment.strip()]
    return parts[-1] if parts else ""


def strip_html(value: str | None) -> str:
    return normalize_whitespace(unescape(re.sub(r"<[^>]+>", " ", value or "")))


def ensure_unique_by_key(items: Iterable[dict], key: str) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        item_key = str(item.get(key, "")).strip()
        if not item_key or item_key in seen:
            continue
        seen.add(item_key)
        result.append(item)
    return result


def has_bot_block(text: str) -> bool:
    content = (text or "").lower()
    markers = [
        "sorry! something went wrong",
        "automated access",
        "captcha",
        "robot or human",
        "enter the characters you see below",
    ]
    return any(marker in content for marker in markers)


async def retry_async(coro_factory, attempts: int = QUERY_RETRY_ATTEMPTS):
    last_error = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except Exception as exc:  # pragma: no cover - exercised in integration
            last_error = exc
            if attempt == attempts - 1:
                raise
            delay = (2**attempt) + random.random()
            await asyncio.sleep(delay)
    if last_error:
        raise last_error
    return None
