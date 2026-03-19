from __future__ import annotations

from ..config import CATEGORY_CONFIG
from ..utils import normalize_whitespace, singularize_token, tokenize
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
}


def _token_set(*parts: str | None) -> set[str]:
    return {singularize_token(token) for token in tokenize(*parts)}


def _category_conflicts(hit: DiscoveryHit, category_id: str | None) -> bool:
    if not category_id:
        return False
    config = CATEGORY_CONFIG.get(category_id, {})
    haystack_tokens = _token_set(hit.title, hit.snippet)
    return any(singularize_token(str(term)) in haystack_tokens for term in config.get("exclude_terms", []))


def score_hit(hit: DiscoveryHit, *, query_text: str, variant_text: str, category_id: str | None = None) -> float:
    if _category_conflicts(hit, category_id):
        return -10.0
    query_tokens = _token_set(query_text)
    variant_tokens = _token_set(variant_text)
    haystack_tokens = _token_set(hit.title, hit.snippet)
    haystack_text = normalize_whitespace(f"{hit.title} {hit.snippet}").lower()
    score = 0.0
    score += len(query_tokens & haystack_tokens) * 2.4
    score += len(variant_tokens & haystack_tokens) * 1.8
    if normalize_whitespace(query_text).lower() in haystack_text:
        score += 3.5
    if normalize_whitespace(variant_text).lower() in haystack_text:
        score += 2.5
    if category_id and category_id != "others":
        category_terms = _token_set(*CATEGORY_CONFIG.get(category_id, {}).get("keywords", []))
        score += len(category_terms & haystack_tokens) * 0.8
    if hit.provider_name:
        score += 1.2
    domain = normalize_whitespace(hit.domain).lower()
    if domain in _NOISE_DOMAINS or any(domain.endswith(f".{candidate}") for candidate in _NOISE_DOMAINS):
        score -= 4.0
    return score


def rank_hits(
    hits: list[DiscoveryHit],
    *,
    query_text: str,
    variant_text: str,
    category_id: str | None = None,
) -> list[DiscoveryHit]:
    scored: list[DiscoveryHit] = []
    for hit in hits:
        hit.score = score_hit(hit, query_text=query_text, variant_text=variant_text, category_id=category_id)
        if hit.score <= 0:
            continue
        scored.append(hit)
    scored.sort(key=lambda item: (item.score, item.title), reverse=True)
    deduped: list[DiscoveryHit] = []
    seen_urls: set[str] = set()
    for hit in scored:
        if hit.normalized_url in seen_urls:
            continue
        seen_urls.add(hit.normalized_url)
        deduped.append(hit)
    return deduped
