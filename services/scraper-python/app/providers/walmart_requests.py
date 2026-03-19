from __future__ import annotations

import json
from urllib.parse import quote_plus

from ..parsers.common import build_product, build_review
from ..utils import (
    absolute_url,
    canonicalize_url,
    category_name,
    infer_category_id,
    normalize_whitespace,
    product_id_for_url,
    retry_async,
    tokenize,
)
from .base import BaseProvider, ProviderProduct, ProviderSearchResult
from .http import fetch_text_resilient


def _extract_next_data(html: str) -> dict:
    marker = '<script id="__NEXT_DATA__" type="application/json">'
    start = html.find(marker)
    if start == -1:
        return {}
    start += len(marker)
    end = html.find("</script>", start)
    if end == -1:
        return {}
    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError:
        return {}


def _pick_image(image_info: dict | None) -> str:
    urls = _extract_images(image_info)
    return urls[0] if urls else ""


def _extract_images(image_info: dict | None) -> list[str]:
    if not image_info:
        return []
    urls = [normalize_whitespace(image_info.get("thumbnailUrl"))]
    for image in image_info.get("allImages") or []:
        urls.append(normalize_whitespace(image.get("url")))
        urls.append(normalize_whitespace(image.get("imageUrl")))
    seen: set[str] = set()
    ordered: list[str] = []
    for value in urls:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _parse_search(html: str, query: str, category_id: str | None) -> list[ProviderProduct]:
    payload = _extract_next_data(html)
    items = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("initialData", {})
        .get("searchResult", {})
        .get("itemStacks", [{}])[0]
        .get("items", [])
    )
    results: list[ProviderProduct] = []
    for item in items:
        source_url = canonicalize_url(absolute_url("https://www.walmart.com", item.get("canonicalUrl")))
        price = item.get("priceInfo", {}).get("currentPrice", {}).get("price") or item.get("priceInfo", {}).get("linePrice")
        image_url = _pick_image(item.get("imageInfo"))
        image_gallery_urls = _extract_images(item.get("imageInfo"))
        product = build_product(
            provider="Walmart",
            source_url=source_url,
            title=item.get("name", ""),
            description=item.get("shortDescription") or item.get("catalogProductType") or item.get("name", ""),
            price=price,
            currency="USD",
            category_id=category_id or infer_category_id(query, item.get("catalogProductType", "")),
            brand=item.get("brand") or item.get("sellerName") or item.get("manufacturerName"),
            source_image_url=image_url,
            image_gallery_urls=image_gallery_urls,
            original_price=item.get("priceInfo", {}).get("wasPrice", {}).get("price") or item.get("priceInfo", {}).get("wasPrice"),
            rating=item.get("averageRating"),
            review_count=item.get("numberOfReviews"),
            tags=tokenize(item.get("name", ""), query, item.get("catalogProductType", "")),
            raw_json={
                "usItemId": item.get("usItemId"),
                "color": item.get("color"),
                "size": item.get("size"),
                "variantGroupId": item.get("variantCriteria", {}).get("variantGroupId") if isinstance(item.get("variantCriteria"), dict) else None,
            },
        )
        if product:
            results.append(product)
    return results


def _extract_item_id(source_url: str) -> str:
    segments = [segment for segment in source_url.split("/") if segment]
    for segment in reversed(segments):
        if segment.isdigit():
            return segment
    return ""


def _parse_detail(html: str, product: dict) -> ProviderProduct | None:
    payload = _extract_next_data(html)
    data = payload.get("props", {}).get("pageProps", {}).get("initialData", {}).get("data", {})
    product_data = data.get("product", {})
    reviews_data = data.get("reviews", {})
    summary = reviews_data.get("reviewSummary", {}).get("summary") or reviews_data.get("topPositiveReview", {}).get("reviewText")
    review_items = reviews_data.get("customerReviews", [])[:10]
    reviews = []
    for review_index, item in enumerate(review_items):
        review = build_review(
            review_id=str(item.get("reviewId") or f"walmart-{product['id']}-{review_index}"),
            author_name=item.get("userNickname") or item.get("author") or "",
            body=item.get("reviewText") or "",
            rating_text=str(item.get("rating") or ""),
            published_at=item.get("reviewSubmissionTime"),
        )
        if review:
            reviews.append(review)
    image_gallery_urls = _extract_images(product_data.get("imageInfo"))
    return build_product(
        provider="Walmart",
        source_url=canonicalize_url(
            absolute_url("https://www.walmart.com", product_data.get("canonicalUrl")) or product["sourceUrl"]
        ),
        title=product_data.get("name") or product["name"],
        description=summary or product.get("description") or product.get("name"),
        price=product_data.get("priceInfo", {}).get("currentPrice", {}).get("price") or product["price"],
        currency=product.get("currency") or "USD",
        category_id=product.get("categoryId"),
        brand=product_data.get("brand") or product.get("brand"),
        source_image_url=_pick_image(product_data.get("imageInfo")) or product["sourceImageUrl"],
        image_gallery_urls=image_gallery_urls or [product.get("sourceImageUrl", "")],
        original_price=product_data.get("priceInfo", {}).get("wasPrice", {}).get("price"),
        rating=reviews_data.get("averageOverallRating") or product.get("rating", 0),
        review_count=reviews_data.get("totalReviewCount") or product.get("reviewCount", 0),
        tags=tokenize(product_data.get("name"), summary, product.get("category")),
        raw_json={
            "source": "detail",
            "usItemId": product_data.get("usItemId") or product.get("raw_json", {}).get("usItemId"),
            "variantGroupId": product_data.get("variantCriteria", {}).get("variantGroupId") if isinstance(product_data.get("variantCriteria"), dict) else None,
            "color": product_data.get("color") or product.get("raw_json", {}).get("color"),
            "size": product_data.get("size") or product.get("raw_json", {}).get("size"),
        },
        reviews=reviews,
    )


class WalmartRequestsProvider(BaseProvider):
    name = "walmart_requests"

    def supports_url(self, url: str) -> bool:
        normalized = canonicalize_url(url)
        return normalized.startswith("https://www.walmart.com")

    async def search(self, query: str, page: int, page_size: int, category_id: str | None = None) -> ProviderSearchResult:
        url = f"https://www.walmart.com/search?q={quote_plus(query)}&page={page}"

        async def fetch_html():
            return await fetch_text_resilient(url, "https://www.walmart.com/")

        html = await retry_async(fetch_html)
        items = _parse_search(html, query, category_id)
        blocked = not items
        return ProviderSearchResult(provider="Walmart", items=items[:page_size], blocked=blocked)

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

            async def fetch_html():
                return await fetch_text_resilient(normalized_url, "https://www.walmart.com/")

            try:
                html = await retry_async(fetch_html)
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
        return ProviderSearchResult(provider="Walmart", items=items[:page_size], blocked=False)

    async def enrich_product(self, product: dict) -> ProviderProduct | None:
        item_id = _extract_item_id(product["sourceUrl"])
        if not item_id:
            return None

        async def fetch_html():
            return await fetch_text_resilient(f"https://www.walmart.com/reviews/product/{item_id}", product["sourceUrl"])

        html = await retry_async(fetch_html)
        return _parse_detail(html, product)
