from __future__ import annotations

import time

import httpx

from ..utils import normalize_whitespace, retry_async
from .normalization import normalize_result
from .searxng_schemas import SearXNGSearchResult

SEARXNG_BASE_URL = "http://127.0.0.1:8088"
SEARXNG_ENGINES = ["duckduckgo", "bing", "startpage"]
SEARXNG_TIMEOUT_MS = 4000
SEARXNG_DEFAULT_LIMIT = 10


def _sanitize_query(value: str) -> str:
    query = normalize_whitespace(value)
    words = query.split()
    if len(words) > 32:
        return " ".join(words[:32])
    return query


class SearXNGClient:
    async def _request_search_payload(self, request_json: dict[str, object]) -> dict[str, object]:
        async def do_request() -> dict[str, object]:
            async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT_MS / 1000.0, follow_redirects=True) as client:
                response = await client.get(f"{SEARXNG_BASE_URL}/search", params=request_json)
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}

        return await retry_async(do_request, attempts=3)

    async def health(self) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT_MS / 1000.0, follow_redirects=True) as client:
                response = await client.get(
                    f"{SEARXNG_BASE_URL}/search",
                    params={
                        "q": "ping",
                        "format": "json",
                        "engines": ",".join(SEARXNG_ENGINES),
                        "safesearch": 0,
                    },
                )
                response.raise_for_status()
            return {
                "enabled": True,
                "available": True,
                "provider": "searxng",
                "baseUrl": SEARXNG_BASE_URL,
                "engines": list(SEARXNG_ENGINES),
            }
        except Exception as exc:
            return {
                "enabled": True,
                "available": False,
                "provider": "searxng",
                "baseUrl": SEARXNG_BASE_URL,
                "engines": list(SEARXNG_ENGINES),
                "error": str(exc),
            }

    async def search(
        self,
        *,
        query_text: str,
        page: int = 1,
        category_id: str | None = None,
        engines: list[str] | None = None,
        limit: int | None = None,
    ) -> SearXNGSearchResult:
        del category_id
        query = _sanitize_query(query_text)
        selected_engines = [normalize_whitespace(engine).lower() for engine in (engines or SEARXNG_ENGINES) if normalize_whitespace(engine)]
        if not selected_engines:
            selected_engines = list(SEARXNG_ENGINES)
        result_limit = max(1, int(limit or SEARXNG_DEFAULT_LIMIT))
        request_json = {
            "q": query,
            "format": "json",
            "pageno": max(1, int(page)),
            "engines": ",".join(selected_engines),
            "safesearch": 0,
        }
        if not query:
            return SearXNGSearchResult(
                query="",
                page=max(1, int(page)),
                engines=selected_engines,
                hits=[],
                error="No valid search query provided.",
                request_json=request_json,
            )

        started_at = time.perf_counter()
        engine_groups: list[list[str]] = [selected_engines]
        if len(selected_engines) > 1:
            engine_groups.extend([[engine] for engine in selected_engines])

        last_error = None
        best_empty_engines = list(selected_engines)
        for engine_group in engine_groups:
            current_request_json = {
                **request_json,
                "engines": ",".join(engine_group),
            }
            try:
                payload = await self._request_search_payload(current_request_json)
            except Exception as exc:
                last_error = str(exc)
                continue

            raw_results = payload.get("results") if isinstance(payload, dict) else []
            hits = []
            for index, raw_result in enumerate(raw_results[:result_limit] if isinstance(raw_results, list) else [], start=1):
                if not isinstance(raw_result, dict):
                    continue
                normalized = normalize_result(
                    {
                        **raw_result,
                        "source": normalize_whitespace(raw_result.get("source")) or "organic",
                        "source_rank": index,
                        "engine": normalize_whitespace(raw_result.get("engine")) or engine_group[0],
                    }
                )
                if normalized:
                    hits.append(normalized)
            if hits:
                return SearXNGSearchResult(
                    query=query,
                    page=max(1, int(page)),
                    engines=engine_group,
                    hits=hits,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                    request_json=current_request_json,
                )
            best_empty_engines = engine_group

        return SearXNGSearchResult(
            query=query,
            page=max(1, int(page)),
            engines=best_empty_engines,
            hits=[],
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            error=last_error,
            request_json={**request_json, "engines": ",".join(best_empty_engines)},
        )


searxng_client = SearXNGClient()
