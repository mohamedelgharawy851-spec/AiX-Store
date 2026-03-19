from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from ..providers.base import ProviderProduct, ProviderReview
from ..utils import (
    absolute_url,
    category_name,
    decode_srcset,
    extract_first_number,
    infer_category_id,
    normalize_offer_prices,
    normalize_whitespace,
    parse_float,
    strip_html,
    tokenize,
)


def soup_for(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in urls:
        normalized = normalize_whitespace(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def pick_image_urls(node, base_url: str) -> list[str]:
    if node is None:
        return []
    urls: list[str] = []
    candidates = [
        node.get("src"),
        node.get("data-src"),
        node.get("data-old-hires"),
        node.get("data-a-dynamic-image"),
        decode_srcset(node.get("srcset")),
        decode_srcset(node.get("data-srcset")),
    ]
    for candidate in candidates:
        raw = normalize_whitespace(candidate)
        if not raw:
            continue
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    urls.extend(absolute_url(base_url, key) for key in parsed.keys())
            except json.JSONDecodeError:
                continue
            continue
        url = absolute_url(base_url, raw)
        if url:
            urls.append(url)
    return _dedupe_urls(urls)


def pick_image_url(node, base_url: str) -> str:
    urls = pick_image_urls(node, base_url)
    return urls[0] if urls else ""


def build_product(
    *,
    provider: str,
    source_url: str,
    title: str,
    description: str,
    price: float | None,
    currency: str = "USD",
    category_id: str | None = None,
    brand: str | None = None,
    source_image_url: str,
    original_price: float | None = None,
    rating: float | None = None,
    review_count: int | None = None,
    tags: list[str] | None = None,
    image_gallery_urls: list[str] | None = None,
    family_key: str | None = None,
    variant_label: str | None = None,
    variant_attributes: dict[str, str] | None = None,
    raw_json: dict[str, Any] | None = None,
    reviews: list[ProviderReview] | None = None,
) -> ProviderProduct | None:
    cleaned_title = normalize_whitespace(title)
    cleaned_image_url = normalize_whitespace(source_image_url)
    normalized_price, normalized_original_price = normalize_offer_prices(price, original_price)
    if not cleaned_title or normalized_price is None or not cleaned_image_url or not source_url:
        return None
    inferred_category = category_id or infer_category_id(cleaned_title, description, brand)
    return ProviderProduct(
        provider=provider,
        source_url=source_url,
        canonical_source_url=source_url,
        title=cleaned_title,
        description=normalize_whitespace(description) or cleaned_title,
        price=normalized_price,
        original_price=normalized_original_price,
        currency=currency or "USD",
        category_id=inferred_category,
        category=category_name(inferred_category),
        brand=normalize_whitespace(brand) or None,
        source_image_url=cleaned_image_url,
        rating=float(rating or 0.0),
        review_count=int(review_count or 0),
        tags=tags or tokenize(cleaned_title, description, brand),
        image_gallery_urls=_dedupe_urls([cleaned_image_url, *(image_gallery_urls or [])]),
        family_key=normalize_whitespace(family_key) or None,
        variant_label=normalize_whitespace(variant_label) or None,
        variant_attributes={normalize_whitespace(key): normalize_whitespace(value) for key, value in (variant_attributes or {}).items() if normalize_whitespace(key) and normalize_whitespace(value)},
        raw_json=raw_json or {},
        reviews=reviews or [],
    )


def build_review(review_id: str, author_name: str, body: str, rating_text: str | None, published_at: str | None = None):
    body_text = strip_html(body)
    if not body_text:
        return None
    return ProviderReview(
        id=review_id,
        author_name=normalize_whitespace(author_name) or "Verified customer",
        rating=parse_float(rating_text) or 0.0,
        body=body_text,
        published_at=normalize_whitespace(published_at) or None,
        raw_json={},
    )


def rating_from_text(value: str | None) -> float:
    return parse_float(value) or 0.0


def review_count_from_text(value: str | None) -> int:
    return extract_first_number(value)
