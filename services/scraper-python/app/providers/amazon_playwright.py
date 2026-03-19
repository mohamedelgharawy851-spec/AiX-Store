from __future__ import annotations

from urllib.parse import quote_plus

from ..parsers.amazon_bs4 import parse_detail, parse_search_results
from ..utils import canonicalize_url, category_name, has_bot_block, product_id_for_url
from .base import BaseProvider, ProviderProduct, ProviderSearchResult


class AmazonPlaywrightProvider(BaseProvider):
    name = "amazon_playwright"

    def supports_url(self, url: str) -> bool:
        normalized = canonicalize_url(url)
        return normalized.startswith("https://www.amazon.com")

    async def _fetch_html(self, url: str) -> str | None:
        try:
            from playwright.async_api import async_playwright
        except Exception:
            return None

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=1500)
                html = await page.content()
                if not has_bot_block(html):
                    await page.mouse.wheel(0, 1600)
                    await page.wait_for_timeout(200)
                    html = await page.content()
                await browser.close()
                return html
        except Exception:
            return None

    async def search(self, query: str, page: int, page_size: int, category_id: str | None = None) -> ProviderSearchResult:
        html = await self._fetch_html(f"https://www.amazon.com/s?k={quote_plus(query)}&page={page}")
        if not html or has_bot_block(html):
            return ProviderSearchResult(provider="Amazon", items=[], blocked=True, message="amazon playwright blocked")
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
            html = await self._fetch_html(normalized_url)
            if not html or has_bot_block(html):
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
        html = await self._fetch_html(product["sourceUrl"])
        if not html or has_bot_block(html):
            return None
        return parse_detail(html, product)
