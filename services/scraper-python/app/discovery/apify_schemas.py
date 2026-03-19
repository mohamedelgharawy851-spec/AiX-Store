from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .schemas import DiscoveryHit


@dataclass(slots=True)
class ApifyQueryResult:
    query: str
    hits: list[DiscoveryHit]
    raw_json: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "hits": [hit.to_json() for hit in self.hits],
            "rawJson": self.raw_json,
        }


@dataclass(slots=True)
class ApifySearchResult:
    queries: list[str]
    actor_id: str
    locale: dict[str, str | None]
    results: list[ApifyQueryResult]
    provider: str = "apify"
    engines: list[str] = field(default_factory=lambda: ["google-search-scraper"])
    latency_ms: int | None = None
    error: str | None = None
    request_json: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "provider": self.provider,
            "actorId": self.actor_id,
            "engines": self.engines,
            "locale": self.locale,
            "results": [result.to_json() for result in self.results],
            "latencyMs": self.latency_ms,
            "error": self.error,
            "requestJson": self.request_json,
        }

