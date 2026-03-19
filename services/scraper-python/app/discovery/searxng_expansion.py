from __future__ import annotations

import re
from typing import Any

from ..utils import normalize_whitespace

_RETAILER_BY_DOMAIN = {
    "amazon.com": "amazon",
    "target.com": "target",
    "walmart.com": "walmart",
}
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
_TITLE_SUFFIX_RE = re.compile(r"\s*(?:[-|])\s*(target|walmart(?:\.com)?|amazon(?:\.com)?)\s*$", re.IGNORECASE)
_RELATED_FAMILY_STOPWORDS = {
    "with",
    "for",
    "and",
    "the",
    "from",
    "plus",
    "new",
    "pack",
    "set",
    "count",
    "size",
    "inch",
    "oz",
    "electronics",
    "fashion",
    "beauty",
    "food",
    "home",
    "toys",
    "sports",
    "others",
}


def _field(hit: Any, name: str) -> str:
    if isinstance(hit, dict):
        return normalize_whitespace(hit.get(name))
    return normalize_whitespace(getattr(hit, name, ""))


def retailer_for_domain(domain: str | None) -> str | None:
    normalized_domain = normalize_whitespace(domain).lower()
    return _RETAILER_BY_DOMAIN.get(normalized_domain)


def is_noise_domain(domain: str | None) -> bool:
    normalized_domain = normalize_whitespace(domain).lower()
    return bool(normalized_domain) and (
        normalized_domain in _NOISE_DOMAINS or any(normalized_domain.endswith(f".{candidate}") for candidate in _NOISE_DOMAINS)
    )


def _is_seed_candidate(title: str, domain: str | None) -> bool:
    if is_noise_domain(domain):
        return False
    return len([token for token in normalize_whitespace(title).split(" ") if token]) >= 2


def clean_seed_title(title: str) -> str:
    cleaned = _TITLE_SUFFIX_RE.sub("", normalize_whitespace(title))
    return normalize_whitespace(cleaned).lower()


def dedupe_seed_queries(queries: list[str], *, limit: int = 5) -> list[str]:
    deduped: list[str] = []
    for query in queries:
        normalized = normalize_whitespace(query).lower()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


def _family_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]+", normalize_whitespace(value).lower()):
        if len(token) < 2 and not token.isdigit():
            continue
        if token in _RELATED_FAMILY_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def build_search_seed_queries(
    apify_hits: list[Any],
    *,
    original_query: str,
    selected_variant: str | None = None,
) -> list[str]:
    preferred_titles: list[tuple[str, str | None]] = []
    supplemental_titles: list[tuple[str, str | None]] = []
    seen_titles: set[str] = set()
    for hit in apify_hits:
        domain = _field(hit, "domain")
        retailer = retailer_for_domain(domain)
        cleaned_title = clean_seed_title(_field(hit, "title"))
        if not cleaned_title or cleaned_title in seen_titles or not _is_seed_candidate(cleaned_title, domain):
            continue
        seen_titles.add(cleaned_title)
        if retailer:
            preferred_titles.append((cleaned_title, retailer))
        else:
            supplemental_titles.append((cleaned_title, None))
        if len(preferred_titles) + len(supplemental_titles) >= 6:
            break

    seed_titles = preferred_titles[:3]
    if len(seed_titles) < 3:
        seed_titles.extend(supplemental_titles[: 3 - len(seed_titles)])

    queries: list[str] = []
    for cleaned_title, retailer in seed_titles:
        queries.append(cleaned_title)
        if retailer:
            queries.append(f"{cleaned_title} {retailer}")

    if len(seed_titles) < 3:
        queries.append(original_query)
        if selected_variant:
            queries.append(selected_variant)
    return dedupe_seed_queries(queries, limit=5)


def build_related_seed_queries(product: dict[str, Any], related_items: list[dict[str, Any]]) -> list[str]:
    title = clean_seed_title(normalize_whitespace(product.get("name") or product.get("title")))
    brand = normalize_whitespace(product.get("brand"))
    category_label = clean_seed_title(normalize_whitespace(product.get("category") or product.get("categoryId")))
    queries: list[str] = []
    if title:
        queries.append(title)
    normalized_title = title.lower()
    if brand and brand.lower() not in normalized_title:
        queries.append(clean_seed_title(f"{brand} {title}".strip()))
    for item in related_items[:3]:
        related_title = normalize_whitespace(item.get("name") or item.get("title"))
        if related_title:
            queries.append(clean_seed_title(related_title))
    if category_label and category_label != "others" and not any(category_label in query for query in queries):
        if title:
            queries.append(clean_seed_title(f"{category_label} {title}"))
        else:
            queries.append(category_label)
    return dedupe_seed_queries(queries, limit=6)


def build_related_family_query(product: dict[str, Any], related_items: list[dict[str, Any]]) -> str:
    title = clean_seed_title(normalize_whitespace(product.get("name") or product.get("title")))
    brand = normalize_whitespace(product.get("brand"))
    category_label = clean_seed_title(normalize_whitespace(product.get("category") or product.get("categoryId")))
    phrases: list[str] = []
    if title:
        phrases.append(title)
    if brand and title and brand.lower() not in title.lower():
        phrases.append(clean_seed_title(f"{brand} {title}"))
    for item in related_items[:3]:
        related_title = clean_seed_title(normalize_whitespace(item.get("name") or item.get("title")))
        if related_title:
            phrases.append(related_title)
    if category_label and category_label != "others":
        phrases.append(category_label)

    ordered_tokens: list[str] = []
    token_weights: dict[str, int] = {}
    token_positions: dict[str, int] = {}
    anchor_token_list = _family_tokens(title)
    anchor_tokens = set(anchor_token_list)
    for phrase_index, phrase in enumerate(phrases):
        weight = 3 if phrase_index == 0 else 2 if phrase_index == 1 else 1
        for token in _family_tokens(phrase):
            token_weights[token] = token_weights.get(token, 0) + weight
            if token not in token_positions:
                token_positions[token] = len(ordered_tokens)
                ordered_tokens.append(token)

    query_tokens: list[str] = []
    seen_tokens: set[str] = set()

    def add_token(token: str) -> None:
        if token and token not in seen_tokens:
            seen_tokens.add(token)
            query_tokens.append(token)

    for token in anchor_token_list:
        add_token(token)

    related_token_lists = [_family_tokens(phrase) for phrase in phrases[1:4]]
    max_related_length = max((len(tokens) for tokens in related_token_lists), default=0)
    for index in range(max_related_length):
        for tokens in related_token_lists:
            if index < len(tokens):
                add_token(tokens[index])

    ranked_tokens = sorted(
        ordered_tokens,
        key=lambda token: (-token_weights[token], -(1 if token in anchor_tokens else 0), token_positions[token]),
    )
    for token in ranked_tokens:
        if len(query_tokens) >= 10:
            break
        add_token(token)
    if category_label and category_label != "others":
        for token in _family_tokens(category_label):
            if len(query_tokens) >= 10:
                break
            add_token(token)
    return normalize_whitespace(" ".join(query_tokens[:10]))
