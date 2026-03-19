from __future__ import annotations

import json

from ..config import CATEGORY_CONFIG
from ..utils import normalize_whitespace

REWRITE_PROMPT_VERSION = "rewrite.v1"
CATEGORY_JUDGE_PROMPT_VERSION = "category-judge.v1"

REWRITE_SYSTEM_PROMPT = (
    "Rewrite e-commerce queries. Return JSON only."
)

CATEGORY_JUDGE_SYSTEM_PROMPT = (
    "Judge product category. Return JSON only."
)


def build_rewrite_prompt(
    *,
    query: str,
    category_id: str | None,
    trigger_reason: str,
    deterministic_variants: list[str],
    rewrite_limit: int,
) -> str:
    category_rules = CATEGORY_CONFIG.get(category_id or "others", CATEGORY_CONFIG["others"])
    payload = {
        "task": "rewrite",
        "query": normalize_whitespace(query),
        "category_id": category_id,
        "category_name": category_rules["name"],
        "why": trigger_reason,
        "known": deterministic_variants[:3],
        "include_terms": category_rules.get("include_terms", []),
        "exclude_terms": category_rules.get("exclude_terms", []),
        "limit": rewrite_limit,
        "instructions": "Return {'rewrites':[...]} only. Each item needs text,strategy,must_terms,optional_terms,broadness. No prose. No category drift.",
        "example": {
            "rewrites": [
                {
                    "text": "gaming notebook",
                    "strategy": "synonym",
                    "must_terms": ["gaming", "notebook"],
                    "optional_terms": ["laptop"],
                    "broadness": "equivalent",
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def build_category_judge_prompt(
    *,
    title: str,
    description: str,
    brand: str | None,
    tags: list[str],
    provider_name: str,
    source_category_id: str | None,
    candidate_scores: list[dict[str, object]],
) -> str:
    categories = {
        category_id: {
            "name": config["name"],
            "include_terms": config.get("include_terms", []),
            "exclude_terms": config.get("exclude_terms", []),
            "strong_phrases": config.get("strong_phrases", []),
        }
        for category_id, config in CATEGORY_CONFIG.items()
    }
    payload = {
        "task": "judge_category",
        "product": {
            "title": normalize_whitespace(title),
            "description": normalize_whitespace(description),
            "brand": normalize_whitespace(brand),
            "tags": tags[:6],
            "provider": normalize_whitespace(provider_name),
            "source_category_id": normalize_whitespace(source_category_id),
        },
        "top_candidates": candidate_scores,
        "allowed_categories": {key: value["name"] for key, value in categories.items()},
        "instructions": "Return {category_id,confidence,verdict,reason,used_candidates}. If unsure use category_id 'others' and verdict 'uncertain'.",
        "example": {
            "category_id": "electronics",
            "confidence": 7.8,
            "verdict": "accept",
            "reason": "Title and description strongly indicate an electronics device.",
            "used_candidates": ["electronics", "others"],
        },
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
