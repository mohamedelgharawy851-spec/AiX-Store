from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from json import JSONDecodeError
from typing import Any

from ..utils import normalize_whitespace


class AIParseError(ValueError):
    pass


@dataclass(slots=True)
class RewriteCandidate:
    text: str
    strategy: str
    must_terms: list[str] = field(default_factory=list)
    optional_terms: list[str] = field(default_factory=list)
    broadness: str = "equivalent"


@dataclass(slots=True)
class RewritePlan:
    query: str
    category_id: str | None
    prompt_version: str
    rewrites: list[RewriteCandidate]


@dataclass(slots=True)
class CategoryJudgment:
    category_id: str
    confidence: float
    verdict: str
    reason: str
    used_candidates: list[str]
    prompt_version: str


def _extract_json_fragment(text: str) -> str:
    raw = normalize_whitespace(text)
    if not raw:
        raise AIParseError("Model returned empty output.")
    if "```" in raw:
        for fragment in raw.split("```"):
            candidate = fragment.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{") or candidate.startswith("["):
                return candidate
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char not in "{[":
            continue
        try:
            _, end = decoder.raw_decode(raw[index:])
            return raw[index : index + end]
        except JSONDecodeError:
            continue
    raise AIParseError("Could not find JSON in model output.")


def _normalize_terms(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    seen: list[str] = []
    for item in items:
        term = normalize_whitespace(str(item)).lower()
        if term and term not in seen:
            seen.append(term)
    return seen


def parse_rewrite_plan(
    text: str,
    *,
    query: str,
    category_id: str | None,
    prompt_version: str,
    limit: int,
) -> RewritePlan:
    payload = json.loads(_extract_json_fragment(text))
    rewrites = payload.get("rewrites")
    if not isinstance(rewrites, list):
        raise AIParseError("Rewrite payload missing 'rewrites' list.")
    normalized_query = normalize_whitespace(query).lower()
    candidates: list[RewriteCandidate] = []
    seen_texts = {normalized_query}
    for item in rewrites:
        if not isinstance(item, dict):
            continue
        rewrite_text = normalize_whitespace(str(item.get("text", ""))).lower()
        if not rewrite_text or rewrite_text in seen_texts:
            continue
        seen_texts.add(rewrite_text)
        strategy = normalize_whitespace(str(item.get("strategy", ""))) or "rewrite"
        broadness = normalize_whitespace(str(item.get("broadness", ""))).lower() or "equivalent"
        if broadness not in {"narrower", "equivalent", "broader"}:
            broadness = "equivalent"
        candidates.append(
            RewriteCandidate(
                text=rewrite_text,
                strategy=strategy,
                must_terms=_normalize_terms(item.get("must_terms")),
                optional_terms=_normalize_terms(item.get("optional_terms")),
                broadness=broadness,
            )
        )
        if len(candidates) >= limit:
            break
    if not candidates:
        raise AIParseError("Model returned no valid rewrites.")
    return RewritePlan(query=query, category_id=category_id, prompt_version=prompt_version, rewrites=candidates)


def parse_category_judgment(
    text: str,
    *,
    prompt_version: str,
    allowed_categories: set[str],
) -> CategoryJudgment:
    payload = json.loads(_extract_json_fragment(text))
    category_id = normalize_whitespace(str(payload.get("category_id", ""))).lower() or "others"
    if category_id not in allowed_categories:
        category_id = "others"
    verdict = normalize_whitespace(str(payload.get("verdict", ""))).lower() or "uncertain"
    if verdict not in {"accept", "relabel", "exclude", "uncertain"}:
        verdict = "uncertain"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except Exception as exc:  # pragma: no cover - defensive
        raise AIParseError("Category judgment confidence must be numeric.") from exc
    reason = normalize_whitespace(str(payload.get("reason", ""))) or "No reason provided."
    used_candidates = _normalize_terms(payload.get("used_candidates"))
    return CategoryJudgment(
        category_id=category_id,
        confidence=max(0.0, confidence),
        verdict=verdict,
        reason=reason,
        used_candidates=used_candidates,
        prompt_version=prompt_version,
    )


def rewrite_plan_to_json(plan: RewritePlan) -> dict[str, Any]:
    return {
        "query": plan.query,
        "categoryId": plan.category_id,
        "promptVersion": plan.prompt_version,
        "rewrites": [asdict(candidate) for candidate in plan.rewrites],
    }


def category_judgment_to_json(judgment: CategoryJudgment) -> dict[str, Any]:
    return {
        "categoryId": judgment.category_id,
        "confidence": judgment.confidence,
        "verdict": judgment.verdict,
        "reason": judgment.reason,
        "usedCandidates": judgment.used_candidates,
        "promptVersion": judgment.prompt_version,
    }
