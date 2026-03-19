from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class DiscoveryHit:
    title: str
    url: str
    normalized_url: str
    domain: str
    provider_name: str
    snippet: str = ""
    source: str | None = None
    source_title: str = ""
    source_snippet: str = ""
    source_rank: int | None = None
    engine: str | None = None
    score: float = 0.0
    raw_json: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DiscoveryQueryResult:
    query: str
    category_id: str | None
    engines: list[str]
    hits: list[DiscoveryHit]
    latency_ms: int | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "categoryId": self.category_id,
            "engines": self.engines,
            "hits": [hit.to_json() for hit in self.hits],
            "latencyMs": self.latency_ms,
            "error": self.error,
        }
