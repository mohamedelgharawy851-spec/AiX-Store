from __future__ import annotations

import asyncio
import time

import httpx

from ..utils import normalize_whitespace, retry_async
from .apify_schemas import ApifyQueryResult, ApifySearchResult
from .config import (
    APIFY_ACTOR_ID,
    APIFY_BASE_URL,
    APIFY_COUNTRY,
    APIFY_DOMAIN,
    APIFY_LANGUAGE,
    APIFY_MAX_PAGES_PER_QUERY,
    APIFY_RESULTS_PER_PAGE,
    APIFY_TIMEOUT_MS,
    APIFY_TOKEN,
    DISCOVERY_ENGINES,
    apify_configuration_error,
    apify_is_active,
)
from .normalization import normalize_apify_entry


def _sanitize_query(value: str) -> str:
    query = normalize_whitespace(value)
    words = query.split()
    if len(words) > 32:
        query = " ".join(words[:32])
    return query


def _build_request_payload(query_variants: list[str]) -> dict[str, object]:
    sanitized = [_sanitize_query(variant) for variant in query_variants if _sanitize_query(variant)]
    return {
        "queries": "\n".join(sanitized),
        "resultsPerPage": APIFY_RESULTS_PER_PAGE,
        "maxPagesPerQuery": APIFY_MAX_PAGES_PER_QUERY,
        "countryCode": APIFY_COUNTRY,
        "languageCode": APIFY_LANGUAGE,
        "mobileResults": False,
    }


class ApifyClient:
    async def _request_dataset_items(self, request_json: dict[str, object]) -> list[dict]:
        async def do_request() -> list[dict]:
            async with httpx.AsyncClient(timeout=APIFY_TIMEOUT_MS / 1000.0, follow_redirects=True) as client:
                response = await client.post(
                    f"{APIFY_BASE_URL}/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items",
                    params={
                        "token": APIFY_TOKEN,
                        "clean": "true",
                        "format": "json",
                    },
                    json=request_json,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, list) else []

        return await retry_async(do_request, attempts=3)

    async def health(self) -> dict[str, object]:
        config_error = apify_configuration_error()
        if not apify_is_active():
            return {
                "enabled": False,
                "available": False,
                "provider": "apify",
                "baseUrl": APIFY_BASE_URL,
                "actorId": APIFY_ACTOR_ID,
                "tokenConfigured": bool(APIFY_TOKEN),
                "reason": config_error,
            }
        try:
            async with httpx.AsyncClient(timeout=APIFY_TIMEOUT_MS / 1000.0, follow_redirects=True) as client:
                response = await client.get(
                    f"{APIFY_BASE_URL}/acts/{APIFY_ACTOR_ID}",
                    params={"token": APIFY_TOKEN},
                )
                response.raise_for_status()
            return {
                "enabled": True,
                "available": True,
                "provider": "apify",
                "baseUrl": APIFY_BASE_URL,
                "actorId": APIFY_ACTOR_ID,
                "tokenConfigured": bool(APIFY_TOKEN),
            }
        except Exception as exc:
            return {
                "enabled": True,
                "available": False,
                "provider": "apify",
                "baseUrl": APIFY_BASE_URL,
                "actorId": APIFY_ACTOR_ID,
                "tokenConfigured": bool(APIFY_TOKEN),
                "error": str(exc),
            }

    async def search(
        self,
        *,
        query_variants: list[str],
        category_id: str | None = None,
    ) -> ApifySearchResult:
        queries = [_sanitize_query(item) for item in query_variants if _sanitize_query(item)]
        request_json = _build_request_payload(queries)
        if not apify_is_active():
            return ApifySearchResult(
                queries=queries,
                actor_id=APIFY_ACTOR_ID,
                locale={"country": APIFY_COUNTRY.upper(), "language": APIFY_LANGUAGE, "domain": APIFY_DOMAIN or None},
                results=[],
                error=apify_configuration_error() or "Apify discovery is disabled or not configured.",
                request_json=request_json,
            )
        if not queries:
            return ApifySearchResult(
                queries=[],
                actor_id=APIFY_ACTOR_ID,
                locale={"country": APIFY_COUNTRY.upper(), "language": APIFY_LANGUAGE, "domain": APIFY_DOMAIN or None},
                results=[],
                error="No valid search queries provided.",
                request_json=request_json,
            )

        started_at = time.perf_counter()
        last_error = None
        try:
            payload = await self._request_dataset_items(request_json)
        except Exception as exc:
            payload = []
            last_error = str(exc)
        results: list[ApifyQueryResult] = []
        for entry in payload:
            normalized = normalize_apify_entry(entry if isinstance(entry, dict) else {})
            if normalized:
                results.append(normalized)

        if not results and len(queries) > 1:
            partial_results: list[ApifyQueryResult] = []
            partial_errors: list[str] = []
            for query in queries:
                single_request_json = _build_request_payload([query])
                try:
                    single_payload = await self._request_dataset_items(single_request_json)
                except Exception as exc:
                    partial_errors.append(f"{query}: {exc}")
                    continue
                for entry in single_payload:
                    normalized = normalize_apify_entry(entry if isinstance(entry, dict) else {})
                    if normalized:
                        partial_results.append(normalized)
            if partial_results:
                return ApifySearchResult(
                    queries=queries,
                    actor_id=APIFY_ACTOR_ID,
                    locale={"country": APIFY_COUNTRY.upper(), "language": APIFY_LANGUAGE, "domain": APIFY_DOMAIN or None},
                    results=partial_results,
                    engines=list(DISCOVERY_ENGINES),
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                    error="; ".join(partial_errors) or last_error,
                    request_json=request_json,
                )
            if partial_errors:
                last_error = "; ".join(partial_errors)

        return ApifySearchResult(
            queries=queries,
            actor_id=APIFY_ACTOR_ID,
            locale={"country": APIFY_COUNTRY.upper(), "language": APIFY_LANGUAGE, "domain": APIFY_DOMAIN or None},
            results=results,
            engines=list(DISCOVERY_ENGINES) if results else None,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            error=last_error if not results else None,
            request_json=request_json,
        )


apify_client = ApifyClient()
