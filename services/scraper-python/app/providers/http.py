from __future__ import annotations

import asyncio
import random
import urllib.request

import httpx

from ..config import PROXY_URL, REQUEST_TIMEOUT_SECONDS, USER_AGENTS
from ..utils import normalize_whitespace, retry_async

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def build_client() -> httpx.AsyncClient:
    transport_args = {"proxy": PROXY_URL} if PROXY_URL else {}
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        **transport_args,
    )


def build_headers(referer: str | None = None) -> dict[str, str]:
    headers = {
        "user-agent": random.choice(USER_AGENTS),
        "accept-language": "en-US,en;q=0.9",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "cache-control": "no-cache",
    }
    if referer:
        headers["referer"] = referer
    return headers


async def _fetch_text_via_httpx(url: str, referer: str | None = None) -> str:
    async with build_client() as client:
        response = await client.get(url, headers=build_headers(referer))
        if response.status_code in RETRYABLE_STATUS_CODES:
            response.raise_for_status()
        response.raise_for_status()
        if not normalize_whitespace(response.text):
            raise ValueError("empty response body")
        return response.text


async def fetch_text_via_urllib(url: str, referer: str | None = None) -> str:
    headers = build_headers(referer)

    def _load() -> str:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", "ignore")

    return await asyncio.to_thread(_load)


async def fetch_text_resilient(url: str, referer: str | None = None, *, attempts: int = 3) -> str:
    last_error: Exception | None = None
    try:
        return await retry_async(lambda: _fetch_text_via_httpx(url, referer), attempts=max(1, attempts))
    except Exception as exc:
        last_error = exc
    try:
        return await retry_async(lambda: fetch_text_via_urllib(url, referer), attempts=max(2, attempts - 1))
    except Exception as exc:
        last_error = exc
    raise last_error or RuntimeError(f"failed to fetch {url}")
