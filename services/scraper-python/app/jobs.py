from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from .ai.config import AI_MODEL_ID, ai_pipeline_is_enabled
from .config import (
    CATEGORY_CONFIG,
    CORE_CATEGORY_IDS,
    DEFAULT_BOOTSTRAP_COUNT,
    DEFAULT_PAGE_SIZE,
    FAST_PROVIDER_PRIORITY,
    PROVIDER_BLOCK_COOLDOWN_SECONDS,
    PROVIDER_PRIORITY,
    SEARCH_FALLBACK_PROVIDER_PRIORITY,
    STRONG_SEARCH_RESULT_THRESHOLD,
)
from .discovery import apify_client
from .discovery.cache import build_discovery_cache_key, get_cached_discovery, save_discovery_cache
from .discovery.config import (
    APIFY_ACTOR_ID,
    APIFY_CACHE_TTL_SECONDS,
    APIFY_COUNTRY,
    APIFY_DOMAIN,
    APIFY_LANGUAGE,
    APIFY_MAX_URLS_PER_PROVIDER,
    APIFY_MAX_VARIANTS,
    APIFY_PROVIDER_EXTRACTION_TIMEOUT_MS,
    APIFY_RESULTS_PER_PAGE,
    APIFY_SUPPRESSION_THRESHOLD,
    APIFY_TOTAL_BUDGET_MS,
    DISCOVERY_ENGINES,
    DISCOVERY_LOCALE,
    DISCOVERY_PROVIDER_NAME,
    apify_configuration_error,
    discovery_is_active,
)
from .discovery.ranking import rank_hits
from .discovery.searxng_client import SEARXNG_ENGINES, searxng_client
from .discovery.searxng_expansion import (
    build_related_family_query,
    build_related_seed_queries,
    build_search_seed_queries,
    dedupe_seed_queries,
)
from .providers.amazon_playwright import AmazonPlaywrightProvider
from .providers.amazon_requests import AmazonRequestsProvider
from .providers.target_requests import TargetRequestsProvider
from .providers.walmart_requests import WalmartRequestsProvider
from .storage.db import (
    append_query_results,
    category_counts,
    clear_query_results,
    count_products,
    count_query_results,
    get_cached_discovery_response,
    get_suppressed_discovery_urls,
    get_product,
    get_product_with_reviews,
    get_query_metadata,
    get_related_products,
    has_bootstrap_coverage,
    filter_related_product_candidates,
    list_active_product_ids,
    list_products,
    list_query_products,
    mark_discovery_failure,
    rank_product_ids_for_query,
    replace_reviews,
    search_cached_products,
    save_query_results,
    set_query_status,
    store_discovery_hits,
    upsert_products,
)
from .storage.images import cache_image
from .utils import (
    category_name,
    classify_category,
    expand_discovery_variants,
    expand_query_variants,
    normalize_whitespace,
    singularize_token,
    tokenize,
)


def _decode_json_value(value: Any, default: Any):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except Exception:
            return default
    return default


def _decode_string_list(value: Any) -> list[str]:
    decoded = _decode_json_value(value, [])
    if not isinstance(decoded, list):
        return []
    return [normalize_whitespace(str(item)) for item in decoded if normalize_whitespace(str(item))]


class CatalogJobRunner:
    def __init__(self) -> None:
        self._provider_map = {
            "target_requests": TargetRequestsProvider(),
            "walmart_requests": WalmartRequestsProvider(),
            "amazon_requests": AmazonRequestsProvider(),
            "amazon_playwright": AmazonPlaywrightProvider(),
        }
        self._provider_blocked_until: dict[str, float] = defaultdict(float)

    def _provider_for_product(self, provider_value: str | None):
        normalized_provider = normalize_whitespace(provider_value).lower().replace(" ", "_")
        if normalized_provider in self._provider_map:
            return self._provider_map[normalized_provider]
        alias_map = {
            "target": "target_requests",
            "walmart": "walmart_requests",
            "amazon": "amazon_requests",
        }
        aliased_provider = alias_map.get(normalized_provider)
        if aliased_provider:
            return self._provider_map.get(aliased_provider)
        return None

    def _provider_sequence(self, provider_names: list[str] | tuple[str, ...] | None = None):
        now = asyncio.get_running_loop().time()
        for provider_name in provider_names or PROVIDER_PRIORITY:
            if self._provider_blocked_until.get(provider_name, 0.0) > now:
                continue
            yield self._provider_map[provider_name]

    async def _safe_provider_search(
        self,
        provider,
        *,
        query: str,
        page: int,
        page_size: int,
        category_id: str | None = None,
    ) -> tuple[Any | None, str | None]:
        try:
            return (
                await provider.search(
                    query=query,
                    page=page,
                    page_size=page_size,
                    category_id=category_id,
                ),
                None,
            )
        except Exception as exc:
            return None, f"{provider.name} failed: {exc}"

    async def _safe_provider_search_by_urls(
        self,
        provider,
        *,
        urls: list[str],
        page_size: int,
        category_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[Any | None, str | None]:
        try:
            if timeout_seconds is None:
                return await provider.search_by_urls(urls, page_size=page_size, category_id=category_id), None
            return (
                await asyncio.wait_for(
                    provider.search_by_urls(urls, page_size=page_size, category_id=category_id),
                    timeout=timeout_seconds,
                ),
                None,
            )
        except asyncio.TimeoutError:
            return None, f"{provider.name} url extraction timed out"
        except Exception as exc:
            return None, f"{provider.name} url extraction failed: {exc}"

    async def _persist_products(self, products):
        async def fetch_image_meta(image_url: str) -> tuple[str, dict[str, Any] | None]:
            try:
                return image_url, await asyncio.wait_for(cache_image(image_url), timeout=2.5)
            except Exception:
                return image_url, None

        unique_image_urls: list[str] = []
        for product in products:
            if product.source_image_url and product.source_image_url not in unique_image_urls:
                unique_image_urls.append(product.source_image_url)

        image_meta_by_url: dict[str, dict[str, Any]] = {}
        results = await asyncio.gather(*(fetch_image_meta(image_url) for image_url in unique_image_urls))
        for image_url, image_meta in results:
            if image_meta:
                image_meta_by_url[image_url] = image_meta
        return upsert_products(products, image_meta_by_url, with_meta=True)

    def _category_context_key(self, category_id: str) -> str:
        return f"category::{category_id}"

    def _search_context_key(self, query: str, category_id: str | None = None) -> str:
        normalized_query = normalize_whitespace(query).lower()
        return f"search::{category_id or 'all'}::{normalized_query}"

    def _category_variants(self, category_id: str) -> list[str]:
        config = CATEGORY_CONFIG.get(category_id, CATEGORY_CONFIG["others"])
        variants = [
            normalize_whitespace(str(term)).lower()
            for term in config.get("section_queries", []) or config.get("seed_queries", [])
            if normalize_whitespace(str(term))
        ]
        if not variants:
            variants = [category_name(category_id).lower()]
        return variants

    def _is_product_detail_url(self, provider_name: str, url: str) -> bool:
        normalized_url = normalize_whitespace(url).lower()
        if provider_name == "target_requests":
            return "/p/" in normalized_url
        if provider_name == "walmart_requests":
            return "/ip/" in normalized_url
        if provider_name == "amazon_requests":
            return "/dp/" in normalized_url or "/gp/product/" in normalized_url
        return False

    def _empty_cursor(self, variants: list[str]) -> dict[str, Any]:
        return {
            "variantIndex": 0,
            "variants": [{"term": term, "page": 1, "exhausted": False} for term in variants],
            "matchingSource": "exact",
            "exactMatchCount": 0,
            "filteredOutCount": 0,
            "categoryJudgeUsed": False,
        }

    def _normalize_discovery_pagination(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        seed_queries = dedupe_seed_queries([str(item) for item in value.get("seedQueries") or []], limit=6)
        if not seed_queries:
            return None
        raw_next_page = value.get("nextPageBySeed") if isinstance(value.get("nextPageBySeed"), dict) else {}
        exhausted = {
            str(item)
            for item in value.get("exhaustedSeeds") or []
            if normalize_whitespace(str(item)) in seed_queries
        }
        next_page_by_seed: dict[str, int] = {}
        for seed_query in seed_queries:
            try:
                next_page = max(1, int(raw_next_page.get(seed_query, 1)))
            except Exception:
                next_page = 1
            next_page_by_seed[seed_query] = next_page
        try:
            seed_index = int(value.get("seedIndex", 0))
        except Exception:
            seed_index = 0
        return {
            "provider": "searxng",
            "seedQueries": seed_queries,
            "nextPageBySeed": next_page_by_seed,
            "exhaustedSeeds": sorted(exhausted),
            "seedIndex": seed_index % max(len(seed_queries), 1),
        }

    def _build_discovery_pagination(self, seed_queries: list[str], *, start_page: int) -> dict[str, Any] | None:
        deduped = dedupe_seed_queries(seed_queries, limit=6)
        if not deduped:
            return None
        return {
            "provider": "searxng",
            "seedQueries": deduped,
            "nextPageBySeed": {seed_query: max(1, int(start_page)) for seed_query in deduped},
            "exhaustedSeeds": [],
            "seedIndex": 0,
        }

    def _load_cursor(self, metadata: dict[str, Any] | None, variants: list[str]) -> dict[str, Any]:
        raw_cursor = metadata.get("next_page_token_json") if metadata else None
        parsed = _decode_json_value(raw_cursor, {}) if raw_cursor else {}
        if not isinstance(parsed, dict):
            parsed = {}
        parsed_variants = parsed.get("variants") or []
        current_terms = [normalize_whitespace(str(item.get("term"))).lower() for item in parsed_variants if item.get("term")]
        if current_terms != variants:
            return self._empty_cursor(variants)
        parsed["discoveryPagination"] = self._normalize_discovery_pagination(parsed.get("discoveryPagination"))
        return parsed

    def _discovery_cursor_has_more(self, cursor: dict[str, Any]) -> bool:
        discovery_pagination = self._normalize_discovery_pagination(cursor.get("discoveryPagination"))
        if not discovery_pagination:
            return False
        exhausted = set(discovery_pagination.get("exhaustedSeeds") or [])
        return any(seed_query not in exhausted for seed_query in discovery_pagination.get("seedQueries") or [])

    def _cursor_has_more(self, cursor: dict[str, Any]) -> bool:
        return any(not bool(item.get("exhausted")) for item in cursor.get("variants", [])) or self._discovery_cursor_has_more(
            cursor
        )

    def _next_cursor_index(self, cursor: dict[str, Any]) -> int | None:
        variants = cursor.get("variants", [])
        if not variants:
            return None
        start_index = int(cursor.get("variantIndex", 0))
        for offset in range(len(variants)):
            index = (start_index + offset) % len(variants)
            if not variants[index].get("exhausted"):
                return index
        return None

    def _next_discovery_seed(self, cursor: dict[str, Any]) -> str | None:
        discovery_pagination = self._normalize_discovery_pagination(cursor.get("discoveryPagination"))
        if not discovery_pagination:
            return None
        seed_queries = discovery_pagination.get("seedQueries") or []
        exhausted = set(discovery_pagination.get("exhaustedSeeds") or [])
        if not seed_queries:
            return None
        start_index = int(discovery_pagination.get("seedIndex", 0))
        for offset in range(len(seed_queries)):
            index = (start_index + offset) % len(seed_queries)
            seed_query = seed_queries[index]
            if seed_query in exhausted:
                continue
            discovery_pagination["seedIndex"] = (index + 1) % max(len(seed_queries), 1)
            cursor["discoveryPagination"] = discovery_pagination
            return seed_query
        return None

    def _merge_engines(self, *engine_groups: list[str]) -> list[str]:
        merged: list[str] = []
        for engine_group in engine_groups:
            for engine in engine_group:
                normalized_engine = normalize_whitespace(engine)
                if normalized_engine and normalized_engine not in merged:
                    merged.append(normalized_engine)
        return merged

    def _related_family_tokens(self, value: str | None) -> set[str]:
        return {
            singularize_token(token)
            for token in tokenize(value or "")
            if token and len(token) > 2 and not any(character.isdigit() for character in token)
        }

    def _product_related_tokens(self, product: dict[str, Any]) -> set[str]:
        parts: list[str] = [
            str(product.get("name") or product.get("title") or ""),
            str(product.get("description") or ""),
            str(product.get("category") or product.get("categoryId") or ""),
        ]
        tags = product.get("tags")
        if isinstance(tags, list):
            parts.extend(str(tag) for tag in tags if normalize_whitespace(tag))
        return self._related_family_tokens(" ".join(parts))

    def _matches_related_family(
        self,
        *,
        anchor_product: dict[str, Any],
        candidate: dict[str, Any],
        family_query: str,
        strict_category_id: str | None,
    ) -> bool:
        candidate_category_id = normalize_whitespace(candidate.get("categoryId") or candidate.get("category") or "").lower()
        if strict_category_id and candidate_category_id != strict_category_id:
            return False
        anchor_tokens = self._product_related_tokens(anchor_product)
        family_tokens = self._related_family_tokens(family_query)
        candidate_tokens = self._product_related_tokens(candidate)
        if not candidate_tokens:
            return False
        overlap = candidate_tokens & (anchor_tokens | family_tokens)
        if strict_category_id:
            return len(overlap) >= 1
        anchor_overlap = candidate_tokens & anchor_tokens
        return len(overlap) >= 2 and len(anchor_overlap) >= 1

    def _list_context_items(self, context_key: str, *, category_id: str | None = None) -> list[dict[str, Any]]:
        total = count_query_results(context_key)
        if total <= 0:
            return []
        return list_query_products(context_key, page=1, page_size=total, category_id=category_id).get("items", [])

    async def _run_term_search(
        self,
        search_term: str,
        provider_page: int,
        fetch_size: int,
        ranking_query: str,
        category_id: str | None = None,
        strict_category: bool = False,
        provider_names: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        attempted_provider_names: list[str] = []
        last_message = None
        filtered_out = 0
        exact_match_count = 0
        for provider in self._provider_sequence(provider_names):
            attempted_provider_names.append(provider.name)
            result, error_message = await self._safe_provider_search(
                provider,
                query=search_term,
                page=provider_page,
                page_size=fetch_size,
                category_id=category_id,
            )
            if error_message:
                last_message = error_message
                continue
            if not result:
                continue
            if result.blocked:
                self._provider_blocked_until[provider.name] = (
                    asyncio.get_running_loop().time() + PROVIDER_BLOCK_COOLDOWN_SECONDS
                )
            else:
                self._provider_blocked_until.pop(provider.name, None)
            if result.message:
                last_message = result.message
            if not result.items:
                continue
            try:
                persisted = await self._persist_products(result.items)
            except Exception as exc:
                last_message = f"{provider.name} persist failed: {exc}"
                continue
            if not persisted["productIds"]:
                continue
            ranked_ids, exact_count, filtered_count = rank_product_ids_for_query(
                persisted["productIds"],
                ranking_query,
                category_id=category_id,
                strict_category=strict_category,
            )
            filtered_out += filtered_count
            exact_match_count += exact_count
            if not ranked_ids:
                continue
            return {
                "provider": result.provider,
                "providerNames": attempted_provider_names,
                "acceptedIds": ranked_ids,
                "message": last_message,
                "filteredOutCount": filtered_out,
                "exactMatchCount": exact_match_count,
                "categoryJudgeUsed": bool(persisted["aiCategoryJudgeUsed"]),
            }
        return {
            "provider": None,
            "providerNames": attempted_provider_names,
            "acceptedIds": [],
            "message": last_message or "No provider returned products",
            "filteredOutCount": filtered_out,
            "exactMatchCount": exact_match_count,
            "categoryJudgeUsed": False,
        }

    async def _fetch_searxng_hits(
        self,
        *,
        context_key: str,
        query_text: str,
        ranking_query: str,
        category_id: str | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        request_identity = {
            "provider": "searxng",
            "engines": list(SEARXNG_ENGINES),
            "page": max(1, int(page)),
        }
        cache_key = build_discovery_cache_key(context_key, query_text, category_id, request_identity)
        cached = get_cached_discovery(cache_key) or get_cached_discovery_response(cache_key)
        if cached is not None:
            return {
                "query": query_text,
                "page": max(1, int(page)),
                "hits": list(cached.get("hits") or []),
                "latencyMs": int(cached.get("latencyMs") or 0),
                "engines": list(cached.get("engines") or SEARXNG_ENGINES),
                "error": cached.get("error"),
                "requestJson": cached.get("requestJson") or cached.get("request_json") or {},
                "cached": True,
            }

        result = await searxng_client.search(
            query_text=query_text,
            category_id=category_id,
            engines=list(SEARXNG_ENGINES),
            page=max(1, int(page)),
        )
        ranked_hits = rank_hits(
            result.hits,
            query_text=ranking_query,
            variant_text=query_text,
            category_id=category_id,
        )
        hits_payload = [hit.to_json() for hit in ranked_hits]
        cache_payload = {
            "query": query_text,
            "page": max(1, int(page)),
            "categoryId": category_id,
            "provider": "searxng",
            "engines": result.engines,
            "hits": hits_payload,
            "latencyMs": result.latency_ms,
            "error": result.error,
            "requestJson": result.request_json,
        }
        save_discovery_cache(cache_key, cache_payload, ttl_seconds=APIFY_CACHE_TTL_SECONDS)
        store_discovery_hits(
            context_key=context_key,
            variant_text=f"{query_text}::page::{max(1, int(page))}",
            query_text=ranking_query,
            category_id=category_id,
            provider="searxng",
            request_payload=result.request_json,
            engines=result.engines,
            hits=hits_payload,
            status="error" if result.error and not hits_payload else "success",
            error_message=result.error,
        )
        return {
            "query": query_text,
            "page": max(1, int(page)),
            "hits": hits_payload,
            "latencyMs": int(result.latency_ms or 0),
            "engines": list(result.engines),
            "error": result.error,
            "requestJson": result.request_json,
            "cached": False,
        }

    async def _extract_discovery_products(
        self,
        *,
        entries: list[tuple[dict[str, Any], str]],
        ranking_query: str,
        page_size: int,
        category_id: str | None = None,
        allow_query_fallback: bool,
        target_count: int | None = None,
        excluded_ids: set[str] | None = None,
        strict_category: bool = False,
    ) -> dict[str, Any]:
        provider_priority = ["target_requests", "walmart_requests", "amazon_requests"]
        grouped_entries: dict[str, list[tuple[dict[str, Any], str]]] = {provider_name: [] for provider_name in provider_priority}
        grouped_urls: dict[str, set[str]] = {provider_name: set() for provider_name in provider_priority}
        suppressed_urls = get_suppressed_discovery_urls(minimum_failures=APIFY_SUPPRESSION_THRESHOLD)
        considered_domains: set[str] = set()
        accepted_domains: set[str] = set()
        candidate_url_count = 0
        accepted_url_count = 0
        for hit, fallback_query in entries:
            candidate_url_count += 1
            domain = str(hit.get("domain") or "")
            if domain:
                considered_domains.add(domain)
            normalized_url = str(hit.get("normalized_url") or hit.get("normalizedUrl") or "")
            provider_name = str(hit.get("provider_name") or hit.get("providerName") or "")
            if (
                not normalized_url
                or not provider_name
                or provider_name not in grouped_entries
                or normalized_url in suppressed_urls
                or normalized_url in grouped_urls[provider_name]
                or len(grouped_entries[provider_name]) >= APIFY_MAX_URLS_PER_PROVIDER
            ):
                continue
            grouped_urls[provider_name].add(normalized_url)
            grouped_entries[provider_name].append((hit, fallback_query))
            if domain:
                accepted_domains.add(domain)
            accepted_url_count += 1

        accepted_ids: list[str] = []
        seen_ids: set[str] = set(excluded_ids or set())
        exact_match_count = 0
        filtered_out_count = 0
        category_judge_used = False
        for provider_name in provider_priority:
            provider_entries = grouped_entries.get(provider_name) or []
            if not provider_entries:
                continue
            provider = self._provider_map[provider_name]
            urls = [str(hit.get("normalized_url") or hit.get("normalizedUrl") or "") for hit, _ in provider_entries]
            detail_urls = [url for url in urls if self._is_product_detail_url(provider_name, url)]
            result = None
            error_message = None
            if detail_urls:
                result, error_message = await self._safe_provider_search_by_urls(
                    provider,
                    urls=detail_urls,
                    page_size=page_size,
                    category_id=category_id,
                    timeout_seconds=APIFY_PROVIDER_EXTRACTION_TIMEOUT_MS / 1000.0,
                )
            if allow_query_fallback and (not result or not result.items):
                fallback_queries = dedupe_seed_queries([fallback_query for _, fallback_query in provider_entries], limit=3)
                for fallback_query in fallback_queries:
                    query_result, fallback_error = await self._safe_provider_search(
                        provider,
                        query=fallback_query,
                        page=1,
                        page_size=min(max(page_size, 6), 12),
                        category_id=category_id,
                    )
                    if fallback_error:
                        error_message = fallback_error
                    if query_result and query_result.items:
                        result = query_result
                        break
            if not result or not result.items:
                for url in urls:
                    mark_discovery_failure(
                        url,
                        provider_name,
                        "provider_url_timeout" if result is None and error_message and "timed out" in error_message else "provider_url_empty",
                    )
                continue
            try:
                persisted = await self._persist_products(result.items)
            except Exception:
                for url in urls:
                    mark_discovery_failure(url, provider_name, "provider_persist_failed")
                continue
            category_judge_used = category_judge_used or bool(persisted["aiCategoryJudgeUsed"])
            ranked_ids, exact_count, filtered_count = rank_product_ids_for_query(
                persisted["productIds"],
                ranking_query,
                category_id=category_id,
                strict_category=strict_category,
            )
            exact_match_count += exact_count
            filtered_out_count += filtered_count
            if not ranked_ids:
                for url in urls:
                    mark_discovery_failure(url, provider_name, "provider_products_filtered")
                continue
            for product_id in ranked_ids:
                if product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                accepted_ids.append(product_id)
            if target_count and len(accepted_ids) >= target_count:
                break

        return {
            "acceptedIds": accepted_ids,
            "candidateUrlCount": candidate_url_count,
            "acceptedUrlCount": accepted_url_count,
            "domainsConsidered": sorted(domain for domain in considered_domains if domain),
            "domainsAccepted": sorted(domain for domain in accepted_domains if domain),
            "exactMatchCount": exact_match_count,
            "filteredOutCount": filtered_out_count,
            "categoryJudgeUsed": category_judge_used,
        }

    async def _run_discovery_search(
        self,
        *,
        context_key: str,
        display_query: str,
        variants: list[str],
        page_size: int,
        category_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "enabled": discovery_is_active(),
            "invoked": False,
            "provider": DISCOVERY_PROVIDER_NAME if discovery_is_active() else None,
            "engines": list(DISCOVERY_ENGINES) if discovery_is_active() else [],
            "queriedVariants": [],
            "selectedVariant": None,
            "domainsConsidered": [],
            "domainsAccepted": [],
            "candidateUrlCount": 0,
            "acceptedUrlCount": 0,
            "latencyMs": None,
            "fallbackReason": None,
            "actorId": APIFY_ACTOR_ID if discovery_is_active() else None,
            "locale": dict(DISCOVERY_LOCALE) if discovery_is_active() else None,
            "acceptedIds": [],
            "exactMatchCount": 0,
            "filteredOutCount": 0,
            "categoryJudgeUsed": False,
        }
        if not discovery_is_active():
            payload["fallbackReason"] = apify_configuration_error() or "Apify discovery is disabled."
            return payload

        deadline = asyncio.get_running_loop().time() + (APIFY_TOTAL_BUDGET_MS / 1000.0)
        normalized_display_query = normalize_whitespace(display_query).lower()
        deduped_variants: list[str] = []
        for variant in variants:
            normalized_variant = normalize_whitespace(variant).lower()
            if normalized_variant and normalized_variant not in deduped_variants:
                deduped_variants.append(normalized_variant)
        if len(deduped_variants) > 1 and normalized_display_query in deduped_variants:
            non_raw_variants = [variant for variant in deduped_variants if variant != normalized_display_query]
            if APIFY_MAX_VARIANTS <= 1:
                selected_variants = [normalized_display_query]
            else:
                selected_variants = [*non_raw_variants[: APIFY_MAX_VARIANTS - 1], normalized_display_query]
        else:
            selected_variants = deduped_variants[:APIFY_MAX_VARIANTS]
        if not selected_variants:
            payload["fallbackReason"] = "No valid discovery variants."
            return payload
        latency_total = 0
        discovery_target_count = max(4, (page_size + 1) // 2)
        request_identity = {
            "provider": DISCOVERY_PROVIDER_NAME,
            "actorId": APIFY_ACTOR_ID,
            "country": APIFY_COUNTRY,
            "language": APIFY_LANGUAGE,
            "domain": APIFY_DOMAIN,
            "resultsPerPage": APIFY_RESULTS_PER_PAGE,
        }
        cached_hits_by_variant: dict[str, dict[str, Any]] = {}
        variants_to_fetch: list[str] = []
        payload["invoked"] = True
        payload["queriedVariants"] = list(selected_variants)

        for variant in selected_variants:
            cache_key = build_discovery_cache_key(context_key, variant, category_id, request_identity)
            cached = get_cached_discovery(cache_key) or get_cached_discovery_response(cache_key)
            if cached and cached.get("hits") and not cached.get("error"):
                latency_total += int(cached.get("latencyMs") or 0)
                payload["engines"] = list(cached.get("engines") or payload["engines"])
                payload["actorId"] = cached.get("actorId") or payload["actorId"]
                payload["locale"] = cached.get("locale") or payload["locale"]
                cached_hits_by_variant[variant] = cached
            else:
                variants_to_fetch.append(variant)

        if variants_to_fetch:
            if asyncio.get_running_loop().time() >= deadline:
                payload["fallbackReason"] = "Apify discovery budget exhausted."
            else:
                try:
                    discovery_result = await asyncio.wait_for(
                        apify_client.search(query_variants=variants_to_fetch, category_id=category_id),
                        timeout=max(0.1, deadline - asyncio.get_running_loop().time()),
                    )
                except asyncio.TimeoutError:
                    discovery_result = None
                    payload["fallbackReason"] = "Apify discovery timed out."
                if discovery_result:
                    latency_total += int(discovery_result.latency_ms or 0)
                    payload["engines"] = discovery_result.engines or payload["engines"]
                    payload["actorId"] = discovery_result.actor_id or payload["actorId"]
                    payload["locale"] = discovery_result.locale or payload["locale"]
                    results_by_query = {result.query: result for result in discovery_result.results}
                    for variant in variants_to_fetch:
                        variant_result = results_by_query.get(variant)
                        ranked_hits = rank_hits(
                            variant_result.hits if variant_result else [],
                            query_text=display_query,
                            variant_text=variant,
                            category_id=category_id,
                        )
                        hits_payload = [hit.to_json() for hit in ranked_hits]
                        cache_key = build_discovery_cache_key(context_key, variant, category_id, request_identity)
                        cache_payload = {
                            "query": variant,
                            "queries": discovery_result.queries,
                            "categoryId": category_id,
                            "provider": DISCOVERY_PROVIDER_NAME,
                            "actorId": discovery_result.actor_id,
                            "locale": discovery_result.locale,
                            "engines": discovery_result.engines,
                            "hits": hits_payload,
                            "latencyMs": discovery_result.latency_ms,
                            "error": discovery_result.error,
                            "requestJson": discovery_result.request_json,
                        }
                        save_discovery_cache(cache_key, cache_payload, ttl_seconds=APIFY_CACHE_TTL_SECONDS)
                        store_discovery_hits(
                            context_key=context_key,
                            variant_text=variant,
                            query_text=display_query,
                            category_id=category_id,
                            provider=DISCOVERY_PROVIDER_NAME,
                            request_payload=discovery_result.request_json,
                            engines=discovery_result.engines,
                            hits=hits_payload,
                            status="error" if discovery_result.error and not hits_payload else "success",
                            error_message=discovery_result.error,
                        )
                        cached_hits_by_variant[variant] = cache_payload
                    if discovery_result.error and payload["fallbackReason"] is None:
                        payload["fallbackReason"] = discovery_result.error

        apify_entries: list[tuple[dict[str, Any], str]] = []
        ranked_apify_hits: list[dict[str, Any]] = []
        for variant in selected_variants:
            hits_payload = list((cached_hits_by_variant.get(variant) or {}).get("hits", []) or [])
            if not hits_payload:
                continue
            if not payload["selectedVariant"]:
                payload["selectedVariant"] = variant
            apify_entries.extend((hit, variant) for hit in hits_payload)
            ranked_apify_hits.extend(hits_payload)

        ranked_apify_hits.sort(
            key=lambda item: (float(item.get("score") or 0.0), str(item.get("title") or "")),
            reverse=True,
        )
        seed_queries = (
            build_search_seed_queries(
                ranked_apify_hits,
                original_query=display_query,
                selected_variant=payload["selectedVariant"],
            )
            if ranked_apify_hits
            else []
        )
        searxng_entries: list[tuple[dict[str, Any], str]] = []
        searxng_latency_total = 0
        searxng_errors: list[str] = []
        if seed_queries and asyncio.get_running_loop().time() < deadline:
            searxng_results = await asyncio.gather(
                *[
                    self._fetch_searxng_hits(
                        context_key=context_key,
                        query_text=seed_query,
                        ranking_query=display_query,
                        category_id=category_id,
                        page=1,
                    )
                    for seed_query in seed_queries
                ],
                return_exceptions=True,
            )
            for seed_query, result in zip(seed_queries, searxng_results):
                if isinstance(result, Exception):
                    searxng_errors.append(str(result))
                    continue
                searxng_latency_total += int(result.get("latencyMs") or 0)
                if result.get("engines"):
                    payload["engines"] = self._merge_engines(payload["engines"], list(result.get("engines") or []))
                if result.get("error"):
                    searxng_errors.append(str(result.get("error")))
                searxng_entries.extend((hit, seed_query) for hit in result.get("hits") or [])

        merged_entries: list[tuple[dict[str, Any], str]] = []
        seen_urls: set[str] = set()
        for hit, fallback_query in [*apify_entries, *searxng_entries]:
            normalized_url = str(hit.get("normalized_url") or hit.get("normalizedUrl") or "")
            if not normalized_url or normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            merged_entries.append((hit, fallback_query))

        extraction_payload = await self._extract_discovery_products(
            entries=merged_entries,
            ranking_query=display_query,
            page_size=page_size,
            category_id=category_id,
            allow_query_fallback=True,
            target_count=discovery_target_count,
            strict_category=bool(category_id),
        )

        payload["acceptedIds"] = extraction_payload["acceptedIds"]
        payload["exactMatchCount"] = int(extraction_payload["exactMatchCount"])
        payload["filteredOutCount"] = int(extraction_payload["filteredOutCount"])
        payload["candidateUrlCount"] = int(extraction_payload["candidateUrlCount"])
        payload["acceptedUrlCount"] = int(extraction_payload["acceptedUrlCount"])
        payload["domainsConsidered"] = extraction_payload["domainsConsidered"]
        payload["domainsAccepted"] = extraction_payload["domainsAccepted"]
        payload["latencyMs"] = latency_total or None
        if searxng_latency_total:
            payload["latencyMs"] = int(payload["latencyMs"] or 0) + searxng_latency_total
        payload["categoryJudgeUsed"] = bool(extraction_payload["categoryJudgeUsed"])
        if seed_queries:
            payload["_pagination"] = self._build_discovery_pagination(seed_queries, start_page=2)
        if payload["invoked"] and not payload["acceptedIds"] and payload["fallbackReason"] is None:
            payload["fallbackReason"] = searxng_errors[0] if searxng_errors else "Apify discovery produced no accepted products."
        return payload

    async def _ensure_context_results(
        self,
        context_key: str,
        display_query: str,
        query_kind: str,
        page: int,
        page_size: int,
        variants: list[str],
        category_id: str | None = None,
        strict_category: bool = False,
    ) -> dict[str, Any]:
        metadata = get_query_metadata(context_key) or {}
        stored_variants = metadata.get("query_variants_json")
        if page == 1 and stored_variants and _decode_string_list(stored_variants) != variants:
            clear_query_results(context_key)
            metadata = {}

        cursor = self._load_cursor(metadata, variants)
        target_count = page * page_size
        current_count = count_query_results(context_key)
        max_search_variant_attempts = min(2, len(variants)) if query_kind == "search" and page == 1 else len(variants)
        attempted_variants = 0
        fetch_size = min(max(page_size + 2, 8), 12) if query_kind == "search" else max(page_size * 2, 12)
        primary_timeout = 2.5 if query_kind == "search" else None
        fallback_timeout = 1.5 if query_kind == "search" else None
        set_query_status(
            context_key,
            display_query,
            "running",
            query_kind=query_kind,
            category_id=category_id,
            query_variants=variants,
            next_page_token_json=json.dumps(cursor, separators=(",", ":")),
        )

        while current_count < target_count and self._cursor_has_more(cursor):
            if query_kind == "search" and page == 1 and attempted_variants >= max_search_variant_attempts:
                break
            next_index = self._next_cursor_index(cursor)
            if next_index is None:
                break
            variant = cursor["variants"][next_index]
            primary_provider_names = FAST_PROVIDER_PRIORITY
            attempted_variants += 1
            try:
                if query_kind == "search":
                    result = await asyncio.wait_for(
                        self._run_term_search(
                            search_term=str(variant["term"]),
                            provider_page=int(variant.get("page", 1)),
                            fetch_size=fetch_size,
                            ranking_query=display_query if query_kind == "search" else str(variant["term"]),
                            category_id=category_id,
                            strict_category=strict_category,
                            provider_names=primary_provider_names,
                        ),
                        timeout=primary_timeout,
                    )
                else:
                    result = await self._run_term_search(
                        search_term=str(variant["term"]),
                        provider_page=int(variant.get("page", 1)),
                        fetch_size=fetch_size,
                        ranking_query=display_query if query_kind == "search" else str(variant["term"]),
                        category_id=category_id,
                        strict_category=strict_category,
                        provider_names=primary_provider_names,
                    )
            except asyncio.TimeoutError:
                result = {
                    "provider": None,
                    "providerNames": list(primary_provider_names),
                    "acceptedIds": [],
                    "message": f"Timed out fetching {variant['term']}.",
                    "filteredOutCount": 0,
                    "exactMatchCount": 0,
                    "categoryJudgeUsed": False,
                }
            if (
                not result["acceptedIds"]
                and query_kind == "search"
                and not strict_category
                and next_index == 0
                and SEARCH_FALLBACK_PROVIDER_PRIORITY
            ):
                try:
                    fallback_result = await asyncio.wait_for(
                        self._run_term_search(
                            search_term=str(variant["term"]),
                            provider_page=int(variant.get("page", 1)),
                            fetch_size=fetch_size,
                            ranking_query=display_query,
                            category_id=category_id,
                            strict_category=False,
                            provider_names=SEARCH_FALLBACK_PROVIDER_PRIORITY,
                        ),
                        timeout=fallback_timeout,
                    )
                except asyncio.TimeoutError:
                    fallback_result = {
                        "provider": None,
                        "providerNames": list(SEARCH_FALLBACK_PROVIDER_PRIORITY),
                        "acceptedIds": [],
                        "message": f"Fallback timed out fetching {variant['term']}.",
                        "filteredOutCount": 0,
                        "exactMatchCount": 0,
                        "categoryJudgeUsed": False,
                    }
                result = {
                    "provider": fallback_result["provider"] or result["provider"],
                    "providerNames": [*result["providerNames"], *fallback_result["providerNames"]],
                    "acceptedIds": fallback_result["acceptedIds"],
                    "message": fallback_result["message"] or result["message"],
                    "filteredOutCount": int(result["filteredOutCount"]) + int(fallback_result["filteredOutCount"]),
                    "exactMatchCount": int(result["exactMatchCount"]) + int(fallback_result["exactMatchCount"]),
                    "categoryJudgeUsed": bool(result.get("categoryJudgeUsed")) or bool(
                        fallback_result.get("categoryJudgeUsed")
                    ),
                }
            cursor["filteredOutCount"] = int(cursor.get("filteredOutCount", 0)) + int(result["filteredOutCount"])
            cursor["categoryJudgeUsed"] = bool(cursor.get("categoryJudgeUsed")) or bool(result.get("categoryJudgeUsed"))
            if next_index == 0:
                cursor["exactMatchCount"] = int(cursor.get("exactMatchCount", 0)) + int(result["exactMatchCount"])
            elif result["acceptedIds"]:
                cursor["matchingSource"] = "expanded" if query_kind == "search" else "category_feed"

            variant["page"] = int(variant.get("page", 1)) + 1
            if not result["acceptedIds"]:
                variant["exhausted"] = True
            else:
                append_query_results(
                    context_key,
                    display_query,
                    provider=result["provider"] or "cache",
                    product_ids=result["acceptedIds"],
                    page_size=page_size,
                    next_page_token_json=json.dumps(cursor, separators=(",", ":")),
                    query_kind=query_kind,
                    category_id=category_id,
                    query_variants=variants,
                )
                current_count = count_query_results(context_key)

            cursor["variantIndex"] = (next_index + 1) % max(len(cursor["variants"]), 1)
            set_query_status(
                context_key,
                display_query,
                "running",
                query_kind=query_kind,
                category_id=category_id,
                query_variants=variants,
                next_page_token_json=json.dumps(cursor, separators=(",", ":")),
            )

        set_query_status(
            context_key,
            display_query,
            "idle",
            query_kind=query_kind,
            category_id=category_id,
            query_variants=variants,
            next_page_token_json=json.dumps(cursor, separators=(",", ":")),
        )
        return cursor

    async def _ensure_search_show_more_results(
        self,
        *,
        context_key: str,
        display_query: str,
        page: int,
        page_size: int,
        category_id: str | None,
        provider_variants: list[str],
        cursor: dict[str, Any],
    ) -> dict[str, Any]:
        discovery_pagination = self._normalize_discovery_pagination(cursor.get("discoveryPagination"))
        if not discovery_pagination:
            seed_queries = dedupe_seed_queries([display_query, *provider_variants], limit=5)
            discovery_pagination = self._build_discovery_pagination(seed_queries, start_page=1)
            cursor["discoveryPagination"] = discovery_pagination
        if not discovery_pagination:
            return cursor

        target_count = page * page_size
        current_count = count_query_results(context_key)
        next_page_token_json = json.dumps(cursor, separators=(",", ":"))
        set_query_status(
            context_key,
            display_query,
            "running",
            query_kind="search",
            category_id=category_id,
            query_variants=provider_variants,
            next_page_token_json=next_page_token_json,
        )

        while current_count < target_count and self._discovery_cursor_has_more(cursor):
            seed_query = self._next_discovery_seed(cursor)
            if not seed_query:
                break
            discovery_pagination = self._normalize_discovery_pagination(cursor.get("discoveryPagination")) or {}
            current_page = int((discovery_pagination.get("nextPageBySeed") or {}).get(seed_query, 1))
            searxng_payload = await self._fetch_searxng_hits(
                context_key=context_key,
                query_text=seed_query,
                ranking_query=display_query,
                category_id=category_id,
                page=current_page,
            )
            accepted_payload = await self._extract_discovery_products(
                entries=[(hit, seed_query) for hit in searxng_payload.get("hits") or []],
                ranking_query=display_query,
                page_size=page_size,
                category_id=category_id,
                allow_query_fallback=False,
                target_count=max(target_count - current_count, page_size),
                strict_category=bool(category_id),
            )
            next_page_by_seed = discovery_pagination.get("nextPageBySeed") or {}
            next_page_by_seed[seed_query] = current_page + 1
            if not accepted_payload["acceptedIds"]:
                exhausted = set(discovery_pagination.get("exhaustedSeeds") or [])
                exhausted.add(seed_query)
                discovery_pagination["exhaustedSeeds"] = sorted(exhausted)
            else:
                append_query_results(
                    context_key,
                    display_query,
                    "searxng",
                    accepted_payload["acceptedIds"],
                    page_size=page_size,
                    next_page_token_json=json.dumps(cursor, separators=(",", ":")),
                    query_kind="search",
                    category_id=category_id,
                    query_variants=provider_variants,
                )
                current_count = count_query_results(context_key)
                cursor["matchingSource"] = "expanded"
                cursor["filteredOutCount"] = int(cursor.get("filteredOutCount", 0)) + int(
                    accepted_payload["filteredOutCount"]
                )
                cursor["exactMatchCount"] = int(cursor.get("exactMatchCount", 0)) + int(
                    accepted_payload["exactMatchCount"]
                )
                cursor["categoryJudgeUsed"] = bool(cursor.get("categoryJudgeUsed")) or bool(
                    accepted_payload.get("categoryJudgeUsed")
                )
            cursor["discoveryPagination"] = discovery_pagination
            next_page_token_json = json.dumps(cursor, separators=(",", ":"))
            set_query_status(
                context_key,
                display_query,
                "running",
                query_kind="search",
                category_id=category_id,
                query_variants=provider_variants,
                next_page_token_json=next_page_token_json,
            )

        set_query_status(
            context_key,
            display_query,
            "idle",
            query_kind="search",
            category_id=category_id,
            query_variants=provider_variants,
            next_page_token_json=json.dumps(cursor, separators=(",", ":")),
        )
        return cursor

    async def _ensure_related_show_more_results(
        self,
        *,
        product: dict[str, Any],
        related_context_key: str,
        page: int,
        page_size: int,
        user_id: str | None,
        session_id: str | None,
        cursor: dict[str, Any],
        seed_queries: list[str],
    ) -> dict[str, Any]:
        strict_category_id = str(product.get("categoryId") or "")
        if strict_category_id == "others":
            strict_category_id = ""
        display_query = normalize_whitespace(product.get("name") or product.get("title"))
        discovery_pagination = self._normalize_discovery_pagination(cursor.get("discoveryPagination"))
        if not discovery_pagination:
            discovery_pagination = self._build_discovery_pagination(seed_queries, start_page=1)
            cursor["discoveryPagination"] = discovery_pagination
        if not discovery_pagination:
            return cursor

        target_count = max(1, page - 1) * page_size
        current_count = count_query_results(related_context_key)
        set_query_status(
            related_context_key,
            display_query,
            "running",
            query_kind="related",
            category_id=strict_category_id or None,
            query_variants=seed_queries,
            next_page_token_json=json.dumps(cursor, separators=(",", ":")),
        )

        base_related = get_related_products(
            str(product.get("id")),
            page=1,
            page_size=page_size,
            user_id=user_id,
            session_id=session_id,
        ) or {"items": []}
        family_query = build_related_family_query(product, base_related.get("items", [])) or display_query
        excluded_ids = {str(product.get("id"))}
        excluded_ids.update(str(item.get("id")) for item in base_related.get("items", []) if item.get("id"))

        while current_count < target_count and self._discovery_cursor_has_more(cursor):
            seed_query = self._next_discovery_seed(cursor)
            if not seed_query:
                break
            stored_extra_items = self._list_context_items(
                related_context_key,
                category_id=strict_category_id or None,
            )
            excluded_ids.update(str(item.get("id")) for item in stored_extra_items if item.get("id"))
            discovery_pagination = self._normalize_discovery_pagination(cursor.get("discoveryPagination")) or {}
            current_page = int((discovery_pagination.get("nextPageBySeed") or {}).get(seed_query, 1))
            searxng_payload = await self._fetch_searxng_hits(
                context_key=related_context_key,
                query_text=seed_query,
                ranking_query=family_query,
                category_id=strict_category_id or None,
                page=current_page,
            )
            accepted_payload = await self._extract_discovery_products(
                entries=[(hit, seed_query) for hit in searxng_payload.get("hits") or []],
                ranking_query=family_query,
                page_size=page_size,
                category_id=strict_category_id or None,
                allow_query_fallback=False,
                target_count=max(target_count - current_count, page_size),
                excluded_ids=excluded_ids,
                strict_category=bool(strict_category_id),
            )
            next_page_by_seed = discovery_pagination.get("nextPageBySeed") or {}
            next_page_by_seed[seed_query] = current_page + 1
            accepted_ids = [
                product_id
                for product_id in accepted_payload["acceptedIds"]
                if self._matches_related_family(
                    anchor_product=product,
                    candidate=get_product(product_id, user_id=user_id) or {},
                    family_query=family_query,
                    strict_category_id=strict_category_id or None,
                )
            ]
            accepted_payload["filteredOutCount"] = int(accepted_payload.get("filteredOutCount", 0)) + max(
                len(accepted_payload["acceptedIds"]) - len(accepted_ids),
                0,
            )
            if not accepted_ids:
                should_exhaust_seed = not bool(searxng_payload.get("hits")) or current_page >= 3
                if should_exhaust_seed:
                    exhausted = set(discovery_pagination.get("exhaustedSeeds") or [])
                    exhausted.add(seed_query)
                    discovery_pagination["exhaustedSeeds"] = sorted(exhausted)
            else:
                append_query_results(
                    related_context_key,
                    display_query,
                    "searxng",
                    accepted_ids,
                    page_size=page_size,
                    next_page_token_json=json.dumps(cursor, separators=(",", ":")),
                    query_kind="related",
                    category_id=strict_category_id or None,
                    query_variants=seed_queries,
                )
                current_count = count_query_results(related_context_key)
                cursor["matchingSource"] = "expanded"
                cursor["filteredOutCount"] = int(cursor.get("filteredOutCount", 0)) + int(
                    accepted_payload["filteredOutCount"]
                )
                cursor["exactMatchCount"] = int(cursor.get("exactMatchCount", 0)) + int(
                    accepted_payload["exactMatchCount"]
                )
                cursor["categoryJudgeUsed"] = bool(cursor.get("categoryJudgeUsed")) or bool(
                    accepted_payload.get("categoryJudgeUsed")
                )
                excluded_ids.update(str(product_id) for product_id in accepted_ids)
            cursor["discoveryPagination"] = discovery_pagination
            set_query_status(
                related_context_key,
                display_query,
                "running",
                query_kind="related",
                category_id=strict_category_id or None,
                query_variants=seed_queries,
                next_page_token_json=json.dumps(cursor, separators=(",", ":")),
            )

        set_query_status(
            related_context_key,
            display_query,
            "idle",
            query_kind="related",
            category_id=strict_category_id or None,
            query_variants=seed_queries,
            next_page_token_json=json.dumps(cursor, separators=(",", ":")),
        )
        return cursor

    async def _seed_term(self, query: str, category_id: str | None) -> None:
        await self._run_term_search(
            search_term=query,
            provider_page=1,
            fetch_size=8,
            ranking_query=query,
            category_id=category_id,
            strict_category=bool(category_id),
        )

    async def ensure_bootstrap(self, count: int = DEFAULT_BOOTSTRAP_COUNT, user_id: str | None = None) -> dict[str, Any]:
        if count_products() >= count and has_bootstrap_coverage():
            return list_products(page=1, page_size=count, user_id=user_id)

        tasks = []
        for category_id, config in CATEGORY_CONFIG.items():
            seed_queries = list(config.get("seed_queries", []))
            if seed_queries:
                tasks.append(self._seed_term(seed_queries[0], category_id))
        if tasks:
            await asyncio.gather(*tasks)

        for category_id, config in CATEGORY_CONFIG.items():
            for seed_query in list(config.get("seed_queries", []))[1:]:
                if count_products() >= count and has_bootstrap_coverage():
                    break
                await self._seed_term(seed_query, category_id)

        if not has_bootstrap_coverage():
            for category_id in CORE_CATEGORY_IDS:
                await self._seed_term(category_name(category_id), category_id)

        return list_products(page=1, page_size=count, user_id=user_id)

    def _baseline_queries_for_category(self, category_id: str) -> list[str]:
        config = CATEGORY_CONFIG.get(category_id, CATEGORY_CONFIG["others"])
        deduped: list[str] = []
        seen: set[str] = set()
        for query_group in ("section_queries", "seed_queries"):
            for raw_query in config.get(query_group, []) or []:
                normalized_query = normalize_whitespace(str(raw_query))
                lowered_query = normalized_query.lower()
                if normalized_query and lowered_query not in seen:
                    seen.add(lowered_query)
                    deduped.append(normalized_query)
        fallback_query = normalize_whitespace(category_name(category_id))
        if fallback_query and fallback_query.lower() not in seen:
            deduped.append(fallback_query)
        return deduped

    async def _reseed_category_baseline(self, category_id: str, target_count: int) -> dict[str, Any]:
        before_count = count_products(category_id)
        current_count = before_count
        queries_run: list[dict[str, Any]] = []
        fetch_size = max(DEFAULT_PAGE_SIZE, min(target_count, 24))

        for query_text in self._baseline_queries_for_category(category_id):
            if current_count >= target_count:
                break
            page_runs: list[dict[str, Any]] = []
            for provider_page in range(1, 4):
                if current_count >= target_count:
                    break
                result = await self._run_term_search(
                    search_term=query_text,
                    provider_page=provider_page,
                    fetch_size=fetch_size,
                    ranking_query=query_text,
                    category_id=category_id,
                    strict_category=True,
                )
                next_count = count_products(category_id)
                page_runs.append(
                    {
                        "page": provider_page,
                        "provider": result.get("provider"),
                        "acceptedCount": len(result.get("acceptedIds") or []),
                        "countAfterPage": next_count,
                        "message": result.get("message"),
                    }
                )
                if next_count <= current_count and not result.get("acceptedIds"):
                    current_count = next_count
                    break
                current_count = next_count
            queries_run.append(
                {
                    "query": query_text,
                    "pages": page_runs,
                    "countAfter": current_count,
                }
            )

        return {
            "before": before_count,
            "after": current_count,
            "target": target_count,
            "queriesRun": queries_run,
        }

    async def reseed_full_catalog_baseline(self, *, per_category_target: int = 24) -> dict[str, Any]:
        targets = {category_id: per_category_target for category_id in CATEGORY_CONFIG.keys()}
        results: dict[str, dict[str, Any]] = {}
        semaphore = asyncio.Semaphore(2)

        async def reseed_category(category_id: str) -> None:
            async with semaphore:
                results[category_id] = await self._reseed_category_baseline(category_id, targets[category_id])

        await asyncio.gather(*(reseed_category(category_id) for category_id in targets))
        return {
            "targets": targets,
            "results": results,
            "categoryCounts": category_counts(),
            "totalActiveProducts": count_products(),
        }

    async def backfill_product_galleries(
        self,
        *,
        product_ids: list[str] | None = None,
        concurrency: int = 4,
    ) -> dict[str, Any]:
        resolved_product_ids = [product_id for product_id in (product_ids or list_active_product_ids()) if product_id]
        if not resolved_product_ids:
            return {
                "requestedProducts": 0,
                "enrichedProducts": 0,
                "skippedProducts": 0,
                "failedProducts": 0,
                "productsWithGallerySizeGt1": 0,
            }

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def enrich_product_gallery(product_id: str) -> str:
            async with semaphore:
                product = get_product(product_id)
                if not product:
                    return "skipped"
                provider = self._provider_for_product(product.get("provider"))
                if not provider:
                    return "skipped"
                try:
                    enriched = await provider.enrich_product(product)
                except Exception:
                    return "failed"
                if not enriched:
                    return "skipped"
                try:
                    persisted = await self._persist_products([enriched])
                except Exception:
                    return "failed"
                persisted_ids = persisted.get("productIds", []) if isinstance(persisted, dict) else persisted
                if not persisted_ids:
                    return "skipped"
                if enriched.reviews:
                    for persisted_id in persisted_ids:
                        replace_reviews(persisted_id, enriched.reviews)
                return "enriched"

        statuses = await asyncio.gather(*(enrich_product_gallery(product_id) for product_id in resolved_product_ids))
        gallery_count = sum(
            1
            for product_id in resolved_product_ids
            if len((get_product(product_id) or {}).get("imageGallery") or []) > 1
        )
        return {
            "requestedProducts": len(resolved_product_ids),
            "enrichedProducts": sum(1 for status in statuses if status == "enriched"),
            "skippedProducts": sum(1 for status in statuses if status == "skipped"),
            "failedProducts": sum(1 for status in statuses if status == "failed"),
            "productsWithGallerySizeGt1": gallery_count,
        }

    async def list_category(self, category_id: str, page: int, page_size: int, user_id: str | None = None) -> dict[str, Any]:
        context_key = self._category_context_key(category_id)
        variants = self._category_variants(category_id)
        metadata = get_query_metadata(context_key) or {}
        stored_variants = metadata.get("query_variants_json")
        if stored_variants and _decode_string_list(stored_variants) != variants:
            clear_query_results(context_key)
            metadata = {}
        cursor = self._load_cursor(metadata, variants)
        payload = list_products(page=page, page_size=page_size, category_id=category_id, user_id=user_id)
        payload["contextKey"] = context_key
        payload["contextType"] = "category"
        payload["appliedCategoryId"] = category_id
        payload["strictCategory"] = True
        payload["queryVariants"] = variants
        payload["matching"] = {
            "source": "category_feed",
            "exactMatchCount": len(payload["items"]),
            "filteredOutCount": int(cursor.get("filteredOutCount", 0)),
        }
        payload["hasMore"] = payload["hasMore"] or self._cursor_has_more(cursor)

        if (page > 1 or not payload["items"]) and self._cursor_has_more(cursor):
            try:
                cursor = await self._ensure_context_results(
                    context_key=context_key,
                    display_query=category_name(category_id),
                    query_kind="category",
                    page=page,
                    page_size=page_size,
                    variants=variants,
                    category_id=category_id,
                    strict_category=True,
                )
            except Exception as exc:
                payload["enrichment"] = {
                    "state": "degraded",
                    "sourceProviders": PROVIDER_PRIORITY,
                    "lastUpdatedAt": payload.get("metadata", {}).get("last_completed_at") if payload.get("metadata") else None,
                    "message": f"Using cached category results: {exc}",
                }
                return payload
            payload = list_products(page=page, page_size=page_size, category_id=category_id, user_id=user_id)
            payload["contextKey"] = context_key
            payload["contextType"] = "category"
            payload["appliedCategoryId"] = category_id
            payload["strictCategory"] = True
            payload["hasMore"] = payload["hasMore"] or self._cursor_has_more(cursor)
            payload["matching"] = {
                "source": "category_feed",
                "exactMatchCount": len(payload["items"]),
                "filteredOutCount": int(cursor.get("filteredOutCount", 0)),
            }
            payload["queryVariants"] = variants
        return payload

    async def search(
        self,
        query: str,
        page: int,
        page_size: int,
        category_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_query = normalize_whitespace(query).lower()
        context_key = self._search_context_key(normalized_query, category_id)
        provider_variants = expand_query_variants(normalized_query, category_id)
        discovery_variants = expand_discovery_variants(normalized_query, category_id)
        query_classification = classify_category(normalized_query)
        pipeline_ai_enabled = ai_pipeline_is_enabled()
        discovery_payload = {
            "enabled": discovery_is_active(),
            "invoked": False,
            "provider": DISCOVERY_PROVIDER_NAME if discovery_is_active() else None,
            "engines": list(DISCOVERY_ENGINES) if discovery_is_active() else [],
            "queriedVariants": [],
            "selectedVariant": None,
            "domainsConsidered": [],
            "domainsAccepted": [],
            "candidateUrlCount": 0,
            "acceptedUrlCount": 0,
            "latencyMs": None,
            "fallbackReason": None,
            "actorId": APIFY_ACTOR_ID if discovery_is_active() else None,
            "locale": dict(DISCOVERY_LOCALE) if discovery_is_active() else None,
        }
        ai_payload = {
            "enabled": pipeline_ai_enabled,
            "mode": "off",
            "invoked": False,
            "triggerReason": None,
            "queryVariants": [],
            "selectedVariant": None,
            "categoryJudgeUsed": False,
            "modelId": AI_MODEL_ID if pipeline_ai_enabled else None,
            "latencyMs": None,
            "fallbackReason": "AI assist is disabled in the live search pipeline.",
        }

        def finalize_stage_one(payload: dict[str, Any], message: str | None = None) -> dict[str, Any]:
            payload["contextKey"] = context_key
            payload["contextType"] = "search"
            payload["appliedQuery"] = query
            payload["appliedCategoryId"] = category_id
            payload["strictCategory"] = bool(category_id)
            payload["queryVariants"] = payload.get("queryVariants") or provider_variants
            payload["matching"] = payload.get("matching") or {
                "source": "cached_fallback",
                "exactMatchCount": len(payload.get("items") or []),
                "filteredOutCount": 0,
            }
            payload["enrichment"] = {
                "state": "idle",
                "sourceProviders": [],
                "lastUpdatedAt": payload.get("metadata", {}).get("last_completed_at") if payload.get("metadata") else None,
                "message": message,
            }
            payload["ai"] = ai_payload
            payload["discovery"] = discovery_payload
            return payload

        metadata = get_query_metadata(context_key) or {}
        stored_variants = metadata.get("query_variants_json")
        if stored_variants and _decode_string_list(stored_variants) != provider_variants:
            clear_query_results(context_key)
            metadata = {}
        payload = list_query_products(context_key, page=page, page_size=page_size, user_id=user_id)
        cursor = self._load_cursor(metadata, provider_variants)
        if page == 1 and not payload["items"] and not self._cursor_has_more(cursor):
            clear_query_results(context_key)
            metadata = {}
            payload = list_query_products(context_key, page=page, page_size=page_size, user_id=user_id)
            cursor = self._load_cursor(metadata, provider_variants)
        payload["hasMore"] = payload["hasMore"] or self._cursor_has_more(cursor)
        exact_cached = search_cached_products(
            normalized_query,
            page=page,
            page_size=page_size,
            category_id=category_id,
            user_id=user_id,
        )

        if (
            category_id
            and query_classification["category_id"] not in {category_id, "others"}
            and not exact_cached["items"]
        ):
            exact_cached["contextKey"] = context_key
            exact_cached["contextType"] = "search"
            exact_cached["appliedQuery"] = query
            exact_cached["appliedCategoryId"] = category_id
            exact_cached["strictCategory"] = True
            exact_cached["queryVariants"] = provider_variants
            exact_cached["matching"] = {
                "source": "exact",
                "exactMatchCount": 0,
                "filteredOutCount": 0,
            }
            exact_cached["hasMore"] = False
            exact_cached["enrichment"] = {
                "state": "idle",
                "sourceProviders": FAST_PROVIDER_PRIORITY,
                "lastUpdatedAt": None,
                "message": "No strict matches for this section.",
            }
            exact_cached["hasMore"] = False
            exact_cached["ai"] = ai_payload
            exact_cached["discovery"] = discovery_payload
            return exact_cached

        if exact_cached.get("total", 0):
            return finalize_stage_one(
                exact_cached,
                message="Served from existing products in the database.",
            )

        if payload.get("total", 0):
            payload["hasMore"] = payload["hasMore"] or self._cursor_has_more(cursor)
            return finalize_stage_one(
                payload,
                message="Served from stored search results in the database.",
            )

        if page > 1:
            if count_query_results(context_key) == 0:
                baseline_payload = search_cached_products(
                    normalized_query,
                    page=1,
                    page_size=page_size,
                    category_id=category_id,
                    user_id=user_id,
                )
                baseline_ids = [str(item.get("id")) for item in baseline_payload.get("items", []) if item.get("id")]
                if baseline_ids:
                    save_query_results(
                        context_key,
                        query,
                        1,
                        "cache",
                        baseline_ids,
                        next_page_token_json=json.dumps(cursor, separators=(",", ":")),
                        query_kind="search",
                        category_id=category_id,
                        query_variants=provider_variants,
                    )
            if len(payload["items"]) < page_size:
                cursor = await self._ensure_search_show_more_results(
                    context_key=context_key,
                    display_query=query,
                    page=page,
                    page_size=page_size,
                    category_id=category_id,
                    provider_variants=provider_variants,
                    cursor=cursor,
                )
                payload = list_query_products(context_key, page=page, page_size=page_size, category_id=category_id, user_id=user_id)
            payload["matching"] = {
                "source": cursor.get("matchingSource", "expanded"),
                "exactMatchCount": int(cursor.get("exactMatchCount", 0)),
                "filteredOutCount": int(cursor.get("filteredOutCount", 0)),
            }
            payload["queryVariants"] = provider_variants
            payload["hasMore"] = payload["hasMore"] or self._cursor_has_more(cursor)
            ai_payload["categoryJudgeUsed"] = bool(cursor.get("categoryJudgeUsed", False))
            payload["contextKey"] = context_key
            payload["contextType"] = "search"
            payload["appliedQuery"] = query
            payload["appliedCategoryId"] = category_id
            payload["strictCategory"] = bool(category_id)
            payload["enrichment"] = {
                "state": "idle",
                "sourceProviders": PROVIDER_PRIORITY,
                "lastUpdatedAt": payload.get("metadata", {}).get("last_completed_at") if payload.get("metadata") else None,
                "message": payload.get("metadata", {}).get("last_error") if payload.get("metadata") else None,
            }
            payload["hasMore"] = False
            payload["ai"] = ai_payload
            payload["discovery"] = discovery_payload
            return payload

        cached_exact_matches = int(payload.get("matching", {}).get("exactMatchCount", 0))
        weak_cached_results = (
            page == 1
            and payload.get("items")
            and payload.get("matching", {}).get("source") == "cached_fallback"
            and cached_exact_matches < min(STRONG_SEARCH_RESULT_THRESHOLD, page_size)
        )
        should_fetch_live_results = not (
            page == 1
            and payload.get("items")
            and payload.get("matching", {}).get("source") == "cached_fallback"
            and cached_exact_matches >= min(6, page_size)
        )

        if page == 1 and payload.get("items") and not should_fetch_live_results and discovery_is_active():
            discovery_payload["fallbackReason"] = "Deterministic results were strong enough."
        elif page > 1 and discovery_is_active():
            discovery_payload["fallbackReason"] = "Discovery only runs on the first page."

        if page == 1 and should_fetch_live_results and (len(payload["items"]) < page_size or weak_cached_results):
            try:
                discovery_payload = await self._run_discovery_search(
                    context_key=context_key,
                    display_query=query,
                    variants=discovery_variants,
                    page_size=page_size,
                    category_id=category_id,
                )
            except Exception as exc:
                discovery_payload["fallbackReason"] = f"Discovery degraded: {exc}"
                discovery_payload["acceptedIds"] = []
            discovery_pagination = discovery_payload.pop("_pagination", None)
            if discovery_payload["acceptedIds"]:
                clear_query_results(context_key)
                discovery_cursor = self._load_cursor({}, provider_variants)
                if discovery_pagination:
                    discovery_cursor["discoveryPagination"] = discovery_pagination
                next_page_token_json = json.dumps(discovery_cursor, separators=(",", ":"))
                first_page_ids = discovery_payload["acceptedIds"][:page_size]
                overflow_ids = discovery_payload["acceptedIds"][page_size:]
                save_query_results(
                    context_key,
                    query,
                    1,
                    DISCOVERY_PROVIDER_NAME,
                    first_page_ids,
                    next_page_token_json=next_page_token_json,
                    query_kind="search",
                    category_id=category_id,
                    query_variants=provider_variants,
                )
                if overflow_ids:
                    append_query_results(
                        context_key,
                        query,
                        DISCOVERY_PROVIDER_NAME,
                        overflow_ids,
                        page_size=page_size,
                        next_page_token_json=next_page_token_json,
                        query_kind="search",
                        category_id=category_id,
                        query_variants=provider_variants,
                    )
                cursor = discovery_cursor
                payload = list_query_products(context_key, page=page, page_size=page_size, user_id=user_id)
                payload["matching"] = {
                    "source": "expanded" if discovery_payload.get("selectedVariant") and discovery_payload["selectedVariant"] != normalized_query else "exact",
                    "exactMatchCount": int(discovery_payload["exactMatchCount"]),
                    "filteredOutCount": int(discovery_payload["filteredOutCount"]),
                }
                payload["queryVariants"] = provider_variants
                payload["hasMore"] = payload["hasMore"] or self._cursor_has_more(cursor)
                should_fetch_live_results = False
                ai_payload["categoryJudgeUsed"] = bool(discovery_payload.get("categoryJudgeUsed"))

        if should_fetch_live_results and (len(payload["items"]) < page_size or weak_cached_results):
            try:
                cursor = await self._ensure_context_results(
                    context_key=context_key,
                    display_query=query,
                    query_kind="search",
                    page=page,
                    page_size=page_size,
                    variants=provider_variants,
                    category_id=category_id,
                    strict_category=bool(category_id),
                )
            except Exception as exc:
                payload["enrichment"] = {
                    "state": "degraded",
                    "sourceProviders": PROVIDER_PRIORITY,
                    "lastUpdatedAt": payload.get("metadata", {}).get("last_completed_at") if payload.get("metadata") else None,
                    "message": f"Using cached search results: {exc}",
                }
                payload["ai"] = ai_payload
                payload["discovery"] = discovery_payload
                return payload
            payload = list_query_products(context_key, page=page, page_size=page_size, user_id=user_id)
            matching_source = "exact"
            if int(cursor.get("exactMatchCount", 0)) < STRONG_SEARCH_RESULT_THRESHOLD and len(provider_variants) > 1:
                matching_source = cursor.get("matchingSource", "expanded")
            payload["matching"] = {
                "source": matching_source,
                "exactMatchCount": int(cursor.get("exactMatchCount", 0)),
                "filteredOutCount": int(cursor.get("filteredOutCount", 0)),
            }
            payload["queryVariants"] = provider_variants
            payload["hasMore"] = payload["hasMore"] or self._cursor_has_more(cursor)
        else:
            payload["matching"] = payload.get("matching") or {
                "source": "exact",
                "exactMatchCount": len(payload["items"]),
                "filteredOutCount": 0,
            }
            payload["queryVariants"] = payload.get("queryVariants") or provider_variants

        ai_payload["categoryJudgeUsed"] = bool(cursor.get("categoryJudgeUsed", False)) or bool(
            discovery_payload.get("categoryJudgeUsed", False)
        )

        payload["contextKey"] = context_key
        payload["contextType"] = "search"
        payload["appliedQuery"] = query
        payload["appliedCategoryId"] = category_id
        payload["strictCategory"] = bool(category_id)
        payload["hasMore"] = False
        payload["enrichment"] = {
            "state": "idle",
            "sourceProviders": PROVIDER_PRIORITY,
            "lastUpdatedAt": payload.get("metadata", {}).get("last_completed_at") if payload.get("metadata") else None,
            "message": payload.get("metadata", {}).get("last_error") if payload.get("metadata") else None,
        }
        payload["ai"] = ai_payload
        payload["discovery"] = discovery_payload
        return payload

    async def get_detail(
        self,
        product_id: str,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        detail = get_product_with_reviews(product_id, user_id=user_id, session_id=session_id)
        if not detail:
            return None
        provider = self._provider_for_product(detail.get("provider"))
        if provider and not detail.get("reviews"):
            try:
                enriched = await provider.enrich_product(detail)
            except Exception:
                enriched = None
            if enriched:
                try:
                    persisted = await self._persist_products([enriched])
                except Exception:
                    persisted = {"productIds": []}
                if persisted["productIds"] and enriched.reviews:
                    replace_reviews(product_id, enriched.reviews)
        return get_product_with_reviews(product_id, user_id=user_id, session_id=session_id)

    async def get_related(
        self,
        product_id: str,
        page: int,
        page_size: int,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        product = get_product(product_id, user_id=user_id)
        if not product:
            return None
        if page == 1:
            payload = get_related_products(product_id, page=page, page_size=page_size, user_id=user_id, session_id=session_id)
            if not payload:
                return None
            seed_queries = build_related_seed_queries(product, payload.get("items", []))
            payload["hasMore"] = bool(payload.get("hasMore")) or bool(seed_queries)
            return payload

        related_context_key = f"related::{product_id}"
        base_related = get_related_products(product_id, page=1, page_size=page_size, user_id=user_id, session_id=session_id) or {
            "items": [],
            "total": 0,
        }
        seed_queries = build_related_seed_queries(product, base_related.get("items", []))
        metadata = get_query_metadata(related_context_key) or {}
        stored_variants = metadata.get("query_variants_json")
        if stored_variants and _decode_string_list(stored_variants) != seed_queries:
            clear_query_results(related_context_key)
            metadata = {}
        cursor = self._load_cursor(metadata, seed_queries)
        strict_category_id = str(product.get("categoryId") or "")
        if strict_category_id == "others":
            strict_category_id = ""
        internal_page = max(1, page - 1)
        payload = list_query_products(
            related_context_key,
            page=internal_page,
            page_size=page_size,
            category_id=strict_category_id or None,
            user_id=user_id,
        )
        if len(payload["items"]) < page_size:
            cursor = await self._ensure_related_show_more_results(
                product=product,
                related_context_key=related_context_key,
                page=page,
                page_size=page_size,
                user_id=user_id,
                session_id=session_id,
                cursor=cursor,
                seed_queries=seed_queries,
            )
            payload = list_query_products(
                related_context_key,
                page=internal_page,
                page_size=page_size,
                category_id=strict_category_id or None,
                user_id=user_id,
            )
        filtered_items = filter_related_product_candidates(product, payload.get("items", []))
        return {
            "items": filtered_items,
            "page": page,
            "pageSize": page_size,
            "hasMore": bool(payload.get("hasMore")) or self._cursor_has_more(cursor),
            "total": int(base_related.get("total", 0) or 0) + count_query_results(related_context_key),
        }


job_runner = CatalogJobRunner()
