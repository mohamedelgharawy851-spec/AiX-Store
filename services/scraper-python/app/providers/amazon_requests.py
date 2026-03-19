from __future__ import annotations

from urllib.parse import quote_plus

from ..parsers.amazon_bs4 import parse_detail, parse_search_results
from ..utils import canonicalize_url, category_name, has_bot_block, product_id_for_url, retry_async
from .base import BaseProvider, ProviderProduct, ProviderSearchResult
from .http import fetch_text_resilient


class AmazonRequestsProvider(BaseProvider):
    name = "amazon_requests"

    def supports_url(self, url: str) -> bool:
        normalized = canonicalize_url(url)
        return normalized.startswith("https://www.amazon.com")

    async def search(self, query: str, page: int, page_size: int, category_id: str | None = None) -> ProviderSearchResult:
        url = f"https://www.amazon.com/s?k={quote_plus(query)}&page={page}"

        async def do_request():
            return await fetch_text_resilient(url, "https://www.amazon.com/")

        html = await retry_async(do_request)
        if has_bot_block(html):
            return ProviderSearchResult(
                provider="Amazon",
                items=[],
                blocked=True,
                message="amazon bot block",
            )

        items = parse_search_results(html, query=query, category_id=category_id)
        return ProviderSearchResult(provider="Amazon", items=items[:page_size], blocked=False)

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

            async def do_request():
                return await fetch_text_resilient(normalized_url, "https://www.amazon.com/")

            try:
                html = await retry_async(do_request)
            except Exception:
                continue
            if has_bot_block(html):
                continue
            product = parse_detail(
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
        return ProviderSearchResult(provider="Amazon", items=items[:page_size], blocked=False)

    async def enrich_product(self, product: dict) -> ProviderProduct | None:
        async def do_request():
            return await fetch_text_resilient(product["sourceUrl"], "https://www.amazon.com/")

        html = await retry_async(do_request)
        if has_bot_block(html):
            return None
        return parse_detail(html, product)
