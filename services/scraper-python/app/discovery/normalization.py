from __future__ import annotations

from urllib.parse import urlparse

from ..utils import canonicalize_url, normalize_whitespace
from .apify_schemas import ApifyQueryResult
from .config import DISCOVERY_PROVIDER_BY_DOMAIN
from .schemas import DiscoveryHit

_NOISE_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "reddit.com",
    "pinterest.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "nytimes.com",
    "forbes.com",
    "medium.com",
    "wikipedia.org",
    "fandom.com",
    "blogspot.com",
    "wordpress.com",
    "bit.ly",
    "tinyurl.com",
    "lnk.bio",
}


def normalize_domain(value: str | None) -> str:
    domain = normalize_whitespace(value).lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    return normalize_domain(parsed.hostname)


def provider_for_url(url: str) -> str | None:
    domain = domain_from_url(url)
    for candidate_domain, provider_name in DISCOVERY_PROVIDER_BY_DOMAIN.items():
        if domain == candidate_domain or domain.endswith(f".{candidate_domain}"):
            return provider_name
    return None


def is_noise_domain(domain: str | None) -> bool:
    normalized_domain = normalize_domain(domain)
    return bool(normalized_domain) and (
        normalized_domain in _NOISE_DOMAINS or any(normalized_domain.endswith(f".{candidate}") for candidate in _NOISE_DOMAINS)
    )


def is_allowed_url(url: str) -> bool:
    domain = domain_from_url(url)
    return bool(domain) and not is_noise_domain(domain)


def normalize_result(result: dict) -> DiscoveryHit | None:
    raw_url = normalize_whitespace(result.get("url") or result.get("parsed_url") or result.get("link"))
    normalized_url = canonicalize_url(raw_url)
    if not normalized_url or not is_allowed_url(normalized_url):
        return None
    provider_name = provider_for_url(normalized_url) or ""
    title = normalize_whitespace(result.get("title") or result.get("name"))
    if not title:
        return None
    snippet = normalize_whitespace(
        result.get("content") or result.get("snippet") or result.get("description") or result.get("text")
    )
    engine = normalize_whitespace(result.get("engine")) or "google-search-scraper"
    return DiscoveryHit(
        title=title,
        url=raw_url or normalized_url,
        normalized_url=normalized_url,
        domain=domain_from_url(normalized_url),
        provider_name=provider_name,
        snippet=snippet,
        source=normalize_whitespace(result.get("source")) or "organic",
        source_title=title,
        source_snippet=snippet,
        source_rank=int(result.get("source_rank") or result.get("rank") or 0) or None,
        engine=engine,
        raw_json=result if isinstance(result, dict) else {},
    )


def normalize_apify_entry(entry: dict) -> ApifyQueryResult | None:
    search_query = entry.get("searchQuery") if isinstance(entry.get("searchQuery"), dict) else {}
    query = normalize_whitespace(search_query.get("term") or entry.get("query"))
    if not query:
        return None
    hits: list[DiscoveryHit] = []
    for index, raw_result in enumerate(entry.get("organicResults") or [], start=1):
        if not isinstance(raw_result, dict):
            continue
        hit = normalize_result(
            {
                **raw_result,
                "source": "organic",
                "source_rank": index,
                "engine": "google-search-scraper",
            }
        )
        if hit:
            hits.append(hit)
    return ApifyQueryResult(query=query, hits=hits, raw_json=entry if isinstance(entry, dict) else {})
