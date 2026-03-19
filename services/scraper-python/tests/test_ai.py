from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app.jobs as jobs_module
from app.ai import rewrite_service
from app.ai.category_judge import judge_ambiguous_category
from app.ai.cache import build_rewrite_cache_key, save_rewrite_cache
from app.ai.model_manager import ModelUnavailableError
from app.providers.base import ProviderProduct
from app.storage import db as db_module


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self.temp_dir.name) / "catalog.db"
        db_module.initialize_database()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _seed_product(self, *, url_suffix: str, title: str, description: str, category_id: str, tags: list[str]) -> str:
        product = ProviderProduct(
            provider="Target",
            source_url=f"https://www.target.com/p/{url_suffix}/-/A-1001",
            canonical_source_url=f"https://www.target.com/p/{url_suffix}/-/A-1001",
            title=title,
            description=description,
            price=199.99,
            currency="USD",
            category_id=category_id,
            category=category_id.title(),
            brand="DemoBrand",
            source_image_url=f"https://images.example.com/{url_suffix}.jpg",
            rating=4.7,
            review_count=80,
            tags=tags,
        )
        product_ids = db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": f"img-{url_suffix}",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )
        return product_ids[0]

    def test_generate_rewrite_plan_uses_cache(self):
        cache_key = build_rewrite_cache_key(
            "portable pc",
            "electronics",
            rewrite_service.AI_MODEL_ID,
            rewrite_service.REWRITE_PROMPT_VERSION,
        )
        save_rewrite_cache(
            cache_key=cache_key,
            normalized_query="portable pc",
            category_id="electronics",
            model_id=rewrite_service.AI_MODEL_ID,
            prompt_version=rewrite_service.REWRITE_PROMPT_VERSION,
            rewrite_payload={
                "query": "portable pc",
                "categoryId": "electronics",
                "promptVersion": rewrite_service.REWRITE_PROMPT_VERSION,
                "rewrites": [
                    {
                        "text": "portable computer",
                        "strategy": "acronym_expand",
                        "must_terms": ["portable", "computer"],
                        "optional_terms": ["pc"],
                        "broadness": "equivalent",
                    }
                ],
            },
            ttl_seconds=3600,
        )
        with patch.object(rewrite_service, "AI_ENABLED", True), patch.object(
            rewrite_service, "AI_MODE", "shadow"
        ), patch.object(rewrite_service, "ai_is_active", lambda: True):
            payload = asyncio.run(
                rewrite_service.generate_rewrite_plan(
                    query="portable pc",
                    category_id="electronics",
                    trigger_reason="weak_results",
                    deterministic_variants=["portable pc"],
                )
            )
        self.assertTrue(payload["invoked"])
        self.assertTrue(payload["cached"])
        self.assertEqual(payload["queryVariants"], ["portable computer"])

    def test_search_pipeline_reports_ai_disabled(self):
        product_id = self._seed_product(
            url_suffix="portable-pc",
            title="Portable PC Sleeve",
            description="Protective portable computer sleeve for travel",
            category_id="electronics",
            tags=["portable", "pc", "computer"],
        )
        context_key = "search::all::portable pc"
        stored_variants = jobs_module.expand_query_variants("portable pc")
        db_module.save_query_results(
            context_key,
            "portable pc",
            1,
            "target_requests",
            [product_id],
            query_kind="search",
            category_id=None,
            query_variants=stored_variants,
        )

        runner = jobs_module.CatalogJobRunner()
        with patch.object(
            jobs_module, "ai_pipeline_is_enabled", lambda: False
        ), patch.object(
            jobs_module, "discovery_is_active", lambda: False
        ), patch.object(
            runner,
            "_ensure_context_results",
            AsyncMock(
                return_value={
                    "exactMatchCount": 1,
                    "matchingSource": "exact",
                    "filteredOutCount": 0,
                    "categoryJudgeUsed": False,
                }
            ),
        ):
            payload = asyncio.run(runner.search("portable pc", page=1, page_size=20))

        self.assertEqual(len(payload["items"]), 1)
        self.assertFalse(payload["hasMore"])
        self.assertFalse(payload["ai"]["enabled"])
        self.assertFalse(payload["ai"]["invoked"])
        self.assertEqual(payload["ai"]["mode"], "off")
        self.assertEqual(payload["ai"]["fallbackReason"], "AI assist is disabled in the live search pipeline.")

    def test_search_uses_retailer_variants_for_discovery_only(self):
        runner = jobs_module.CatalogJobRunner()
        with patch.object(
            jobs_module, "ai_pipeline_is_enabled", lambda: False
        ), patch.object(
            runner,
            "_run_discovery_search",
            AsyncMock(
                return_value={
                    "enabled": True,
                    "invoked": True,
                    "provider": "apify",
                    "engines": ["google-search-scraper"],
                    "queriedVariants": ["dog toy target", "dog toy walmart", "dog toy amazon"],
                    "selectedVariant": "dog toy target",
                    "domainsConsidered": [],
                    "domainsAccepted": [],
                    "candidateUrlCount": 0,
                    "acceptedUrlCount": 0,
                    "latencyMs": 30,
                    "fallbackReason": "Apify discovery produced no accepted products.",
                    "actorId": "apify~google-search-scraper",
                    "locale": {"country": "US", "language": "en", "domain": "com"},
                    "acceptedIds": [],
                    "exactMatchCount": 0,
                    "filteredOutCount": 0,
                    "categoryJudgeUsed": False,
                }
            ),
        ) as discovery_mock, patch.object(
            runner,
            "_ensure_context_results",
            AsyncMock(
                return_value={
                "exactMatchCount": 1,
                "matchingSource": "expanded",
                "filteredOutCount": 0,
                "categoryJudgeUsed": False,
                }
            ),
        ) as ensure_mock:
            payload = asyncio.run(runner.search("dog toy", page=1, page_size=20))

        discovery_call = discovery_mock.await_args.kwargs
        ensure_call = ensure_mock.await_args.kwargs
        self.assertIn("dog toy target", discovery_call["variants"])
        self.assertIn("dog toy walmart", discovery_call["variants"])
        self.assertEqual(ensure_call["variants"], jobs_module.expand_query_variants("dog toy"))
        self.assertEqual(payload["queryVariants"], jobs_module.expand_query_variants("dog toy"))
        self.assertEqual(payload["discovery"]["provider"], "apify")

    def test_category_judge_falls_back_on_model_error(self):
        rule_classification = {
            "category_id": "others",
            "category": "Others",
            "confidence": 0.0,
            "scores": {"others": 0.0, "electronics": 3.0, "fashion": 2.0},
            "matched_terms": [],
            "candidates": [
                {"category_id": "electronics", "category": "Electronics", "score": 3.0, "matched_terms": ["pc"]},
                {"category_id": "fashion", "category": "Fashion", "score": 2.0, "matched_terms": []},
            ],
        }
        with patch("app.ai.category_judge.ai_is_active", lambda: True), patch(
            "app.ai.category_judge.model_manager.generate",
            side_effect=ModelUnavailableError("Ollama unavailable"),
        ):
            judgment = judge_ambiguous_category(
                title="Portable PC Sleeve",
                description="Protective portable computer sleeve",
                brand="DemoBrand",
                tags=["portable", "pc"],
                provider_name="Target",
                source_category_id="electronics",
                rule_classification=rule_classification,
            )
        self.assertFalse(judgment["used"])
        self.assertEqual(judgment["category_source"], "rules")
        self.assertEqual(judgment["category_id"], "others")


if __name__ == "__main__":
    unittest.main()
