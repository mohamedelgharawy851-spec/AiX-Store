from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderReview:
    id: str
    author_name: str
    rating: float
    body: str
    published_at: str | None = None
    raw_json: dict[str, Any] | None = None


@dataclass(slots=True)
class ProviderProduct:
    provider: str
    source_url: str
    canonical_source_url: str
    title: str
    description: str
    price: float
    currency: str
    category_id: str
    category: str
    brand: str | None
    source_image_url: str
    rating: float = 0.0
    review_count: int = 0
    original_price: float | None = None
    tags: list[str] = field(default_factory=list)
    image_gallery_urls: list[str] = field(default_factory=list)
    family_key: str | None = None
    variant_label: str | None = None
    variant_attributes: dict[str, str] = field(default_factory=dict)
    raw_json: dict[str, Any] = field(default_factory=dict)
    reviews: list[ProviderReview] = field(default_factory=list)


@dataclass(slots=True)
class ProviderSearchResult:
    provider: str
    items: list[ProviderProduct]
    blocked: bool = False
    next_page_token: str | None = None
    message: str | None = None


class BaseProvider:
    name = "base"

    async def search(self, query: str, page: int, page_size: int, category_id: str | None = None) -> ProviderSearchResult:
        raise NotImplementedError

    def supports_url(self, url: str) -> bool:
        return False

    async def search_by_urls(
        self,
        urls: list[str],
        page_size: int,
        category_id: str | None = None,
    ) -> ProviderSearchResult:
        return ProviderSearchResult(provider=self.name, items=[], blocked=False, message="url extraction unsupported")

    async def enrich_product(self, product: dict[str, Any]) -> ProviderProduct | None:
        return None
