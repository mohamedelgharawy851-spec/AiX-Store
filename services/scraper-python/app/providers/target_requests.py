from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import quote_plus

from ..config import (
    TARGET_PRIMARY_STORE_ID,
    TARGET_PURCHASABLE_STORE_IDS,
    TARGET_STORE_LATITUDE,
    TARGET_STORE_LONGITUDE,
    TARGET_STORE_STATE,
    TARGET_STORE_ZIP,
    TARGET_TIMEZONE,
)
from ..parsers.common import build_product, build_review
from ..utils import (
    canonicalize_url,
    category_name,
    infer_category_id,
    normalize_whitespace,
    product_id_for_url,
    retry_async,
    strip_html,
    tokenize,
)
from .base import BaseProvider, ProviderProduct, ProviderSearchResult
from .http import build_client, build_headers, fetch_text_resilient

TARGET_BASE_URL = "https://www.target.com"
TARGET_CDUI_URL = "https://cdui-orchestrations.target.com/cdui_orchestrations/v1/pages/slp"
TARGET_CHANNEL = "WEB"
TARGET_PLATFORM = "WEB"


def _clean_target_text(value: str | None) -> str:
    text = normalize_whitespace(unescape(value or ""))
    if not text:
        return ""
    try:
        repaired = text.encode("latin-1").decode("utf-8")
        if repaired:
            return normalize_whitespace(repaired)
    except Exception:
        pass
    return text


def _extract_target_data(html: str) -> dict:
    marker = "__TGT_DATA__': { configurable: false, enumerable: true, value: deepFreeze(JSON.parse(\""
    start = html.find(marker)
    if start == -1:
        return {}
    start += len(marker)
    payload_chars: list[str] = []
    escaped = False
    index = start
    while index < len(html):
        char = html[index]
        if escaped:
            payload_chars.append(char)
            escaped = False
        elif char == "\\":
            payload_chars.append(char)
            escaped = True
        elif char == '"' and html.startswith("))", index + 1):
            break
        else:
            payload_chars.append(char)
        index += 1
    try:
        raw_payload = "".join(payload_chars).encode("utf-8").decode("unicode_escape")
        return json.loads(raw_payload)
    except Exception:
        return {}


def _extract_api_key(html: str) -> str:
    patterns = [
        r'apiKeyProduction\\":\\"([^\\"]+)',
        r'apiKeyProduction":"([^"]+)"',
        r'"apiKey":"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return normalize_whitespace(match.group(1))
    return ""


def _fallback_page_path(query: str) -> str:
    return f"/s/{quote_plus(normalize_whitespace(query)).replace('+', '%20')}"


def _extract_search_context(html: str, query: str) -> dict[str, str]:
    target_data = _extract_target_data(html)
    page_path = ""
    visitor_id = ""
    preloaded_queries = target_data.get("__PRELOADED_QUERIES__", {}).get("queries", [])
    for entry in preloaded_queries:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        query_key = entry[0]
        payload = entry[1]
        if not isinstance(payload, dict):
            continue
        query_key_text = json.dumps(query_key)
        if not visitor_id and "site-top-of-funnel/get-cookies" in query_key_text:
            visitor_id = normalize_whitespace(payload.get("visitorId"))
        if "get-page-content" in query_key_text:
            page_path = normalize_whitespace(
                payload.get("data", {}).get("metadata", {}).get("seo_data", {}).get("canonical_url")
            )

    return {
        "api_key": _extract_api_key(html),
        "visitor_id": visitor_id,
        "page_path": page_path or _fallback_page_path(query),
    }


def _find_search_response(payload: dict) -> dict:
    for module in payload.get("data_source_modules", []):
        module_data = module.get("module_data", {})
        search_response = module_data.get("search_response")
        if isinstance(search_response, dict) and search_response.get("products"):
            return search_response
    return {}


def _pick_image_url(enrichment: dict) -> str:
    urls = _extract_image_urls(enrichment)
    return urls[0] if urls else ""


def _extract_image_urls(enrichment: dict) -> list[str]:
    image_info = enrichment.get("image_info", {})
    image_candidates = [
        image_info.get("primary_image", {}).get("url"),
        enrichment.get("images", {}).get("primary_image_url"),
        image_info.get("swatch_image", {}).get("url"),
    ]
    urls: list[str] = []
    for candidate in image_candidates:
        value = normalize_whitespace(candidate)
        if value:
            urls.append(value)
    for alternate in image_info.get("alternate_images", []):
        value = normalize_whitespace(alternate.get("url"))
        if value:
            urls.append(value)
    seen: set[str] = set()
    ordered: list[str] = []
    for value in urls:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _product_description_text(description_data: dict) -> str:
    downstream = _clean_target_text(description_data.get("downstream_description"))
    if downstream:
        return downstream

    soft_bullets = description_data.get("soft_bullets", {}).get("bullets") or []
    if soft_bullets:
        return _clean_target_text(" ".join(soft_bullets[:3]))

    bullet_descriptions = description_data.get("bullet_descriptions") or []
    if bullet_descriptions:
        return _clean_target_text(" ".join(strip_html(bullet) for bullet in bullet_descriptions[:3]))

    return _clean_target_text(description_data.get("title"))


def _review_count(ratings_and_reviews: dict) -> int:
    statistics = ratings_and_reviews.get("statistics", {})
    review_count = statistics.get("review_count")
    if review_count is not None:
        return int(review_count or 0)
    return int(statistics.get("rating", {}).get("count") or 0)


def _build_target_product(summary: dict, query: str, category_id: str | None) -> ProviderProduct | None:
    item = summary.get("item", {})
    enrichment = item.get("enrichment", {})
    description_data = item.get("product_description", {})
    ratings_and_reviews = summary.get("ratings_and_reviews", {})
    title = _clean_target_text(description_data.get("title"))
    description = _product_description_text(description_data) or title
    image_url = _pick_image_url(enrichment)
    image_gallery_urls = _extract_image_urls(enrichment)
    source_url = canonicalize_url(enrichment.get("buy_url"))
    item_type = _clean_target_text(item.get("product_classification", {}).get("item_type", {}).get("name"))
    brand = _clean_target_text(item.get("primary_brand", {}).get("name"))
    rating = ratings_and_reviews.get("statistics", {}).get("rating", {}).get("average")
    review_count = _review_count(ratings_and_reviews)
    price = summary.get("price", {}).get("current_retail")
    original_price = summary.get("price", {}).get("reg_retail")

    return build_product(
        provider="Target",
        source_url=source_url,
        title=title,
        description=description,
        price=price,
        currency="USD",
        category_id=category_id or infer_category_id(query, title, item_type),
        brand=brand,
        source_image_url=image_url,
        image_gallery_urls=image_gallery_urls,
        original_price=original_price,
        rating=rating,
        review_count=review_count,
        tags=tokenize(title, description, item_type, query, brand),
        raw_json={
            "source": "target-cdui",
            "tcin": summary.get("tcin"),
            "parentTcin": summary.get("parent_tcin") or item.get("parent", {}).get("tcin"),
            "color": item.get("color_family") or enrichment.get("color"),
            "size": item.get("size") or item.get("dimensions"),
            "isSponsored": bool(summary.get("is_sponsored_sku")),
        },
    )


def _parse_products(payload: dict | str, query: str, category_id: str | None) -> list[ProviderProduct]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []

    search_response = _find_search_response(payload)
    summaries = search_response.get("products") or []
    seen_urls: set[str] = set()
    products: list[ProviderProduct] = []
    for summary in summaries:
        product = _build_target_product(summary, query=query, category_id=category_id)
        if not product or product.canonical_source_url in seen_urls:
            continue
        seen_urls.add(product.canonical_source_url)
        products.append(product)
    return products


def _find_detail_product(target_data: dict) -> dict:
    preloaded_queries = target_data.get("__PRELOADED_QUERIES__", {}).get("queries", [])
    for entry in preloaded_queries:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        payload = entry[1]
        if not isinstance(payload, dict):
            continue
        product = payload.get("data", {}).get("product")
        if isinstance(product, dict) and product.get("item"):
            return product
    return {}


def _build_reviews(product_id: str, ratings_and_reviews: dict) -> list:
    reviews = []
    for index, item in enumerate((ratings_and_reviews.get("most_recent") or [])[:10]):
        title = _clean_target_text(item.get("title"))
        body = _clean_target_text(item.get("text"))
        review_text = f"{title}. {body}".strip(". ") if title else body
        review = build_review(
            review_id=normalize_whitespace(item.get("id")) or f"target-{product_id}-{index}",
            author_name=item.get("author", {}).get("nickname") or "Target guest",
            body=review_text,
            rating_text=str(item.get("rating", {}).get("value") or ""),
            published_at=item.get("rating", {}).get("submitted_at"),
        )
        if review:
            reviews.append(review)
    return reviews


def _parse_detail(html: str, product: dict) -> ProviderProduct | None:
    target_data = _extract_target_data(html)
    product_data = _find_detail_product(target_data)
    if not product_data:
        return None

    item = product_data.get("item", {})
    description_data = item.get("product_description", {})
    ratings_and_reviews = product_data.get("ratings_and_reviews", {})
    title = _clean_target_text(description_data.get("title") or product.get("name"))
    description = _product_description_text(description_data) or _clean_target_text(product.get("description"))
    item_type = _clean_target_text(item.get("product_classification", {}).get("item_type", {}).get("name"))
    brand = _clean_target_text(item.get("primary_brand", {}).get("name") or product.get("brand"))
    reviews = _build_reviews(product.get("id", "target"), ratings_and_reviews)
    image_gallery_urls = _extract_image_urls(item.get("enrichment", {}))

    return build_product(
        provider="Target",
        source_url=canonicalize_url(product.get("sourceUrl") or item.get("enrichment", {}).get("buy_url")),
        title=title,
        description=description,
        price=product_data.get("price", {}).get("current_retail_min") or product.get("price"),
        currency=product.get("currency") or "USD",
        category_id=product.get("categoryId") or infer_category_id(title, description, item_type),
        brand=brand,
        source_image_url=_pick_image_url(item.get("enrichment", {})) or product.get("sourceImageUrl", ""),
        image_gallery_urls=image_gallery_urls or [product.get("sourceImageUrl", "")],
        original_price=product_data.get("price", {}).get("reg_retail_max"),
        rating=ratings_and_reviews.get("statistics", {}).get("rating", {}).get("average") or product.get("rating", 0),
        review_count=_review_count(ratings_and_reviews) or product.get("reviewCount", 0),
        tags=tokenize(title, description, item_type, brand, product.get("category")),
        raw_json={
            "source": "target-detail",
            "tcin": product_data.get("tcin"),
            "parentTcin": item.get("parent", {}).get("tcin"),
            "color": item.get("color_family") or item.get("enrichment", {}).get("color"),
            "size": item.get("size") or item.get("dimensions"),
        },
        reviews=reviews,
    )


class TargetRequestsProvider(BaseProvider):
    name = "target_requests"

    def __init__(self) -> None:
        self._store_context = {
            "store_id": TARGET_PRIMARY_STORE_ID,
            "zip": TARGET_STORE_ZIP,
            "state": TARGET_STORE_STATE,
            "latitude": TARGET_STORE_LATITUDE,
            "longitude": TARGET_STORE_LONGITUDE,
        }

    async def _fetch_search_html(self, query: str) -> str:
        url = f"{TARGET_BASE_URL}/s?searchTerm={quote_plus(query)}"
        return await fetch_text_resilient(url, f"{TARGET_BASE_URL}/")

    async def _fetch_product_html(self, url: str) -> str:
        return await fetch_text_resilient(url, f"{TARGET_BASE_URL}/")

    def supports_url(self, url: str) -> bool:
        normalized = canonicalize_url(url)
        return normalized.startswith(TARGET_BASE_URL)

    async def search(self, query: str, page: int, page_size: int, category_id: str | None = None) -> ProviderSearchResult:
        html = ""
        context = {"api_key": "", "visitor_id": "", "page_path": _fallback_page_path(query)}
        for _ in range(2):
            html = await retry_async(lambda: self._fetch_search_html(query))
            context = _extract_search_context(html, query)
            if context["api_key"] and context["visitor_id"]:
                break
        if not context["api_key"] or not context["visitor_id"]:
            return ProviderSearchResult(provider="Target", items=[], blocked=True, message="target context missing")

        offset = max(page - 1, 0) * page_size
        params = {
            "key": context["api_key"],
            "platform": TARGET_PLATFORM,
            "sapphire_channel": TARGET_CHANNEL,
            "sapphire_page": context["page_path"],
            "channel": TARGET_CHANNEL,
            "page": context["page_path"],
            "visitor_id": context["visitor_id"],
            "purchasable_store_ids": TARGET_PURCHASABLE_STORE_IDS,
            "latitude": self._store_context["latitude"],
            "longitude": self._store_context["longitude"],
            "state": self._store_context["state"],
            "store_id": self._store_context["store_id"],
            "zip": self._store_context["zip"],
            "has_pending_inputs": "false",
            "offset": str(offset),
            "keyword": normalize_whitespace(query),
            "count": str(page_size),
            "default_purchasability_filter": "true",
            "include_sponsored": "true",
            "new_search": "true" if offset == 0 else "false",
            "spellcheck": "true",
            "store_ids": TARGET_PURCHASABLE_STORE_IDS,
            "is_seo_bot": "false",
            "include_data_source_modules": "true",
            "query_string": f"searchTerm={normalize_whitespace(query)}",
            "timezone": TARGET_TIMEZONE,
        }

        async def do_request():
            async with build_client() as client:
                response = await client.get(
                    TARGET_CDUI_URL,
                    params=params,
                    headers=build_headers(f"{TARGET_BASE_URL}{context['page_path']}"),
                )
                response.raise_for_status()
                return response

        response = None
        for _ in range(2):
            response = await retry_async(do_request)
            items = _parse_products(response.json(), query, category_id)
            if items:
                return ProviderSearchResult(
                    provider="Target",
                    items=items[:page_size],
                    blocked=False,
                    message=None,
                )
            html = await retry_async(lambda: self._fetch_search_html(query))
            context = _extract_search_context(html, query)
            if not context["api_key"] or not context["visitor_id"]:
                break
            params["key"] = context["api_key"]
            params["visitor_id"] = context["visitor_id"]
            params["page"] = context["page_path"]
            params["sapphire_page"] = context["page_path"]
        assert response is not None
        return ProviderSearchResult(
            provider="Target",
            items=[],
            blocked=False,
            message="target search empty",
        )

    async def enrich_product(self, product: dict) -> ProviderProduct | None:
        async def do_request():
            async with build_client() as client:
                response = await client.get(product["sourceUrl"], headers=build_headers(f"{TARGET_BASE_URL}/"))
                response.raise_for_status()
                return response

        response = await retry_async(do_request)
        return _parse_detail(response.text, product)

    async def search_by_urls(
        self,
        urls: list[str],
        page_size: int,
        category_id: str | None = None,
    ) -> ProviderSearchResult:
        items: list[ProviderProduct] = []
        seen_urls: set[str] = set()
        for url in urls:
            normalized_url = canonicalize_url(url)
            if not normalized_url or normalized_url in seen_urls or not self.supports_url(normalized_url):
                continue
            seen_urls.add(normalized_url)
            try:
                html = await retry_async(lambda: self._fetch_product_html(normalized_url))
            except Exception:
                continue
            product = _parse_detail(
                html,
                {
                    "id": product_id_for_url(normalized_url),
                    "name": "",
                    "description": "",
                    "price": 0.0,
                    "currency": "USD",
                    "categoryId": category_id,
                    "brand": "",
                    "sourceUrl": normalized_url,
                    "sourceImageUrl": "",
                    "reviewCount": 0,
                    "category": category_name(category_id or "others"),
                },
            )
            if product:
                items.append(product)
            if len(items) >= page_size:
                break
        return ProviderSearchResult(provider="Target", items=items[:page_size], blocked=False)
