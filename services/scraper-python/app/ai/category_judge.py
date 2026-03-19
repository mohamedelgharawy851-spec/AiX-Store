from __future__ import annotations

import concurrent.futures
import time
from datetime import datetime, timezone
from typing import Any

from ..config import CATEGORY_CONFIG
from ..utils import category_name, normalize_whitespace
from .config import (
    AI_CATEGORY_JUDGE_MIN_CONFIDENCE,
    AI_COLD_START_TIMEOUT_MS,
    AI_MODE,
    AI_MODEL_ID,
    AI_TIMEOUT_MS,
    ai_is_active,
    ai_is_assist_mode,
)
from .logging import finish_ai_run, start_ai_run
from .model_manager import ModelUnavailableError, model_manager
from .prompts import CATEGORY_JUDGE_PROMPT_VERSION, CATEGORY_JUDGE_SYSTEM_PROMPT, build_category_judge_prompt
from .schemas import AIParseError, category_judgment_to_json, parse_category_judgment

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def _should_judge(rule_classification: dict[str, Any], source_category_id: str | None) -> bool:
    if not ai_is_active():
        return False
    top_candidates = rule_classification.get("candidates") or []
    top_score = float(rule_classification.get("confidence", 0.0) or 0.0)
    second_score = float(top_candidates[1]["score"]) if len(top_candidates) > 1 else 0.0
    source_id = normalize_whitespace(source_category_id).lower()
    winner = normalize_whitespace(str(rule_classification.get("category_id", ""))).lower()
    return (
        winner == "others"
        or top_score < 4.0
        or (top_score - second_score) < 2.0
        or (source_id and source_id in CATEGORY_CONFIG and source_id != winner)
    )


def judge_ambiguous_category(
    *,
    title: str,
    description: str,
    brand: str | None,
    tags: list[str],
    provider_name: str,
    source_category_id: str | None,
    rule_classification: dict[str, Any],
) -> dict[str, Any]:
    default_result = {
        "invoked": False,
        "used": False,
        "category_id": str(rule_classification.get("category_id", "others")),
        "category": category_name(str(rule_classification.get("category_id", "others"))),
        "category_source": "rules",
        "ai_category_id": None,
        "ai_category_confidence": None,
        "ai_category_reason": None,
        "ai_category_updated_at": None,
    }
    if not _should_judge(rule_classification, source_category_id):
        return default_result

    run_id = start_ai_run(
        run_type="category_judge",
        mode=AI_MODE,
        trigger_reason="ambiguous_category",
        model_id=AI_MODEL_ID,
        prompt_version=CATEGORY_JUDGE_PROMPT_VERSION,
        input_payload={
            "title": title,
            "description": description,
            "brand": brand,
            "tags": tags,
            "provider": provider_name,
            "sourceCategoryId": source_category_id,
            "ruleClassification": rule_classification,
        },
    )
    prompt = build_category_judge_prompt(
        title=title,
        description=description,
        brand=brand,
        tags=tags,
        provider_name=provider_name,
        source_category_id=source_category_id,
        candidate_scores=list(rule_classification.get("candidates") or [])[:3],
    )
    started_at = time.perf_counter()
    future = _executor.submit(
        model_manager.generate,
        system_prompt=CATEGORY_JUDGE_SYSTEM_PROMPT,
        user_prompt=prompt,
    )
    timeout_seconds = (AI_COLD_START_TIMEOUT_MS if not model_manager.status().get("loaded") else AI_TIMEOUT_MS) / 1000.0
    try:
        generated_text = future.result(timeout=timeout_seconds)
        judgment = parse_category_judgment(
            generated_text,
            prompt_version=CATEGORY_JUDGE_PROMPT_VERSION,
            allowed_categories=set(CATEGORY_CONFIG.keys()),
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        finish_ai_run(run_id, status="success", output_payload=category_judgment_to_json(judgment), latency_ms=latency_ms)
        final_category_id = default_result["category_id"]
        used = False
        if ai_is_assist_mode() and judgment.verdict in {"accept", "relabel"} and judgment.confidence >= AI_CATEGORY_JUDGE_MIN_CONFIDENCE:
            final_category_id = judgment.category_id
            used = judgment.category_id != default_result["category_id"] or judgment.verdict == "accept"
        return {
            "invoked": True,
            "used": used,
            "category_id": final_category_id,
            "category": category_name(final_category_id),
            "category_source": "ai" if used and judgment.category_id == final_category_id else "rules",
            "ai_category_id": judgment.category_id,
            "ai_category_confidence": judgment.confidence,
            "ai_category_reason": judgment.reason,
            "ai_category_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except concurrent.futures.TimeoutError:
        finish_ai_run(run_id, status="timeout", latency_ms=int((time.perf_counter() - started_at) * 1000), error_text="Category judge timed out.")
        return default_result
    except (AIParseError, ModelUnavailableError) as exc:
        finish_ai_run(
            run_id,
            status="parse_error" if isinstance(exc, AIParseError) else "model_error",
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            error_text=str(exc),
        )
        return default_result
    except Exception as exc:  # pragma: no cover - defensive integration fallback
        finish_ai_run(run_id, status="model_error", latency_ms=int((time.perf_counter() - started_at) * 1000), error_text=str(exc))
        return default_result
