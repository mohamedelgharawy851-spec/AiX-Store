from __future__ import annotations

import asyncio
import time
from typing import Any

from ..utils import normalize_whitespace
from .cache import build_rewrite_cache_key, get_cached_rewrite, save_rewrite_cache
from .config import (
    AI_CACHE_TTL_SECONDS,
    AI_COLD_START_TIMEOUT_MS,
    AI_ENABLED,
    AI_MODE,
    AI_MODEL_ID,
    AI_REWRITE_LIMIT,
    AI_TIMEOUT_MS,
    ai_is_active,
)
from .logging import finish_ai_run, start_ai_run
from .model_manager import ModelUnavailableError, model_manager
from .prompts import REWRITE_PROMPT_VERSION, REWRITE_SYSTEM_PROMPT, build_rewrite_prompt
from .schemas import AIParseError, parse_rewrite_plan, rewrite_plan_to_json


async def generate_rewrite_plan(
    *,
    query: str,
    category_id: str | None,
    trigger_reason: str,
    deterministic_variants: list[str],
) -> dict[str, Any]:
    normalized_query = normalize_whitespace(query).lower()
    result = {
        "enabled": AI_ENABLED,
        "mode": AI_MODE,
        "invoked": False,
        "queryVariants": [],
        "selectedVariant": None,
        "modelId": AI_MODEL_ID if AI_ENABLED else None,
        "latencyMs": None,
        "fallbackReason": None,
        "cached": False,
        "rewrites": [],
    }
    if not ai_is_active() or not normalized_query:
        result["fallbackReason"] = "AI assist is disabled."
        return result

    cache_key = build_rewrite_cache_key(normalized_query, category_id, AI_MODEL_ID, REWRITE_PROMPT_VERSION)
    cached = get_cached_rewrite(cache_key)
    if cached:
        result["invoked"] = True
        result["cached"] = True
        plan_payload = cached["rewrite_json"]
        result["rewrites"] = plan_payload.get("rewrites", [])
        result["queryVariants"] = [item["text"] for item in result["rewrites"] if item.get("text")]
        return result

    prompt = build_rewrite_prompt(
        query=query,
        category_id=category_id,
        trigger_reason=trigger_reason,
        deterministic_variants=deterministic_variants,
        rewrite_limit=AI_REWRITE_LIMIT,
    )
    run_id = start_ai_run(
        run_type="search_rewrite",
        mode=AI_MODE,
        trigger_reason=trigger_reason,
        model_id=AI_MODEL_ID,
        prompt_version=REWRITE_PROMPT_VERSION,
        input_payload={
            "query": query,
            "categoryId": category_id,
            "deterministicVariants": deterministic_variants,
        },
    )
    started_at = time.perf_counter()
    timeout_seconds = (AI_COLD_START_TIMEOUT_MS if not model_manager.status().get("loaded") else AI_TIMEOUT_MS) / 1000.0
    try:
        generated_text = await asyncio.wait_for(
            asyncio.to_thread(
                model_manager.generate,
                system_prompt=REWRITE_SYSTEM_PROMPT,
                user_prompt=prompt,
            ),
            timeout=timeout_seconds,
        )
        plan = parse_rewrite_plan(
            generated_text,
            query=query,
            category_id=category_id,
            prompt_version=REWRITE_PROMPT_VERSION,
            limit=AI_REWRITE_LIMIT,
        )
        plan_payload = rewrite_plan_to_json(plan)
        save_rewrite_cache(
            cache_key=cache_key,
            normalized_query=normalized_query,
            category_id=category_id,
            model_id=AI_MODEL_ID,
            prompt_version=REWRITE_PROMPT_VERSION,
            rewrite_payload=plan_payload,
            ttl_seconds=AI_CACHE_TTL_SECONDS,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        finish_ai_run(run_id, status="success", output_payload=plan_payload, latency_ms=latency_ms)
        result["invoked"] = True
        result["latencyMs"] = latency_ms
        result["rewrites"] = plan_payload["rewrites"]
        result["queryVariants"] = [item["text"] for item in plan_payload["rewrites"] if item.get("text")]
        return result
    except asyncio.TimeoutError:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        finish_ai_run(run_id, status="timeout", latency_ms=latency_ms, error_text="Rewrite timed out.")
        result["fallbackReason"] = "AI rewrite timed out."
        result["latencyMs"] = latency_ms
        return result
    except (AIParseError, ModelUnavailableError) as exc:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        finish_ai_run(
            run_id,
            status="parse_error" if isinstance(exc, AIParseError) else "model_error",
            latency_ms=latency_ms,
            error_text=str(exc),
        )
        result["fallbackReason"] = str(exc)
        result["latencyMs"] = latency_ms
        return result
    except Exception as exc:  # pragma: no cover - defensive integration fallback
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        finish_ai_run(run_id, status="model_error", latency_ms=latency_ms, error_text=str(exc))
        result["fallbackReason"] = f"Unexpected AI error: {exc}"
        result["latencyMs"] = latency_ms
        return result
