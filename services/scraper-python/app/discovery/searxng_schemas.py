from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import DiscoveryHit


@dataclass(slots=True)
class SearXNGQueryResult:
    query: str
    page: int
    hits: list[DiscoveryHit]
    raw_json: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "page": self.page,
            "hits": [hit.to_json() for hit in self.hits],
            "rawJson": self.raw_json,
        }


@dataclass(slots=True)
class SearXNGSearchResult:
    query: str
    page: int
    engines: list[str]
    hits: list[DiscoveryHit]
    provider: str = "searxng"
    latency_ms: int | None = None
    error: str | None = None
    request_json: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "page": self.page,
            "provider": self.provider,
            "engines": self.engines,
            "hits": [hit.to_json() for hit in self.hits],
            "latencyMs": self.latency_ms,
            "error": self.error,
            "requestJson": self.request_json,
        }
