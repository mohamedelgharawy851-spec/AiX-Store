from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app.jobs as jobs_module
from app.discovery.cache import build_discovery_cache_key, get_cached_discovery, save_discovery_cache
from app.discovery.apify_client import ApifyClient
from app.discovery.apify_schemas import ApifyQueryResult, ApifySearchResult
from app.discovery.normalization import normalize_apify_entry, normalize_result
from app.discovery.searxng_client import SearXNGClient
from app.discovery.searxng_expansion import (
    build_related_family_query,
    build_related_seed_queries,
    build_search_seed_queries,
)
from app.discovery.searxng_schemas import SearXNGSearchResult
from app.discovery.schemas import DiscoveryHit
from app.providers.base import ProviderProduct, ProviderSearchResult
from app.storage import db as db_module


class DiscoveryTests(unittest.TestCase):
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
            price=299.99,
            currency="USD",
            category_id=category_id,
            category=category_id.title(),
            brand="DemoBrand",
            source_image_url=f"https://images.example.com/{url_suffix}.jpg",
            rating=4.8,
            review_count=120,
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

    def test_normalize_result_maps_allowed_provider(self):
        hit = normalize_result(
            {
                "url": "https://www.target.com/p/apple-iphone-15/-/A-123456",
                "title": "Apple iPhone 15",
                "content": "Shop Apple iPhone 15 at Target.",
                "engine": "duckduckgo",
            }
        )

        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.provider_name, "target_requests")
        self.assertEqual(hit.domain, "target.com")
        self.assertTrue(hit.normalized_url.startswith("https://www.target.com/p/apple-iphone-15"))

    def test_normalize_result_preserves_unsupported_domain_as_candidate(self):
        hit = normalize_result(
            {
                "url": "https://www.bestbuy.com/site/demo-product/123.p",
                "title": "Demo Product",
                "content": "Unsupported retailer result",
                "engine": "duckduckgo",
            }
        )

        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.domain, "bestbuy.com")
        self.assertEqual(hit.provider_name, "")

    def test_normalize_result_rejects_noise_domain(self):
        hit = normalize_result(
            {
                "url": "https://www.youtube.com/watch?v=demo",
                "title": "Demo Product Video Review",
                "content": "Noise domain should not become a discovery candidate.",
                "engine": "duckduckgo",
            }
        )

        self.assertIsNone(hit)

    def test_discovery_cache_round_trip(self):
        cache_key = build_discovery_cache_key(
            "search::electronics::iphone",
            "apple iphone smartphone",
            "electronics",
            {
                "provider": "apify",
                "actorId": "apify~google-search-scraper",
                "locale": {"country": "US", "language": "en", "domain": "com"},
                "engines": ["google-search-scraper"],
            },
        )
        save_discovery_cache(
            cache_key,
            {
                "query": "apple iphone smartphone",
                "categoryId": "electronics",
                "provider": "apify",
                "actorId": "apify~google-search-scraper",
                "engines": ["google-search-scraper"],
                "hits": [{"title": "Apple iPhone 15", "normalized_url": "https://target.com/p/apple-iphone-15/-/A-123456"}],
                "latencyMs": 42,
            },
            ttl_seconds=3600,
        )

        payload = get_cached_discovery(cache_key)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["categoryId"], "electronics")
        self.assertEqual(payload["latencyMs"], 42)
        self.assertEqual(len(payload["hits"]), 1)

    def test_normalize_apify_entry_maps_organic_results(self):
        result = normalize_apify_entry(
            {
                "searchQuery": {"term": "apple iphone smartphone"},
                "organicResults": [
                    {
                        "title": "Apple iPhone 15 - Target",
                        "url": "https://www.target.com/p/apple-iphone-15/-/A-123456",
                        "description": "Shop Apple iPhone 15 at Target.",
                    }
                ],
            }
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.query, "apple iphone smartphone")
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].provider_name, "target_requests")

    def test_apify_token_alias_is_accepted(self):
        import app.discovery.config as discovery_config

        with patch.dict(
            os.environ,
            {
                "AIXSTORE_APIFY_TOKEN": "",
                "APIFY_TOKEN": "alias-token",
            },
            clear=False,
        ):
            reloaded = importlib.reload(discovery_config)
            self.assertEqual(reloaded.APIFY_TOKEN, "alias-token")
            self.assertTrue(reloaded.apify_is_active())

        importlib.reload(discovery_config)

    def test_build_search_seed_queries_uses_cleaned_apify_titles(self):
        queries = build_search_seed_queries(
            [
                {"title": "Mainstays Performance Solid Bath Towel - Walmart", "domain": "walmart.com"},
                {"title": "Threshold Quick Dry Bath Towel - Target", "domain": "target.com"},
            ],
            original_query="towel",
            selected_variant="towel walmart",
        )

        self.assertEqual(
            queries,
            [
                "mainstays performance solid bath towel",
                "mainstays performance solid bath towel walmart",
                "threshold quick dry bath towel",
                "threshold quick dry bath towel target",
                "towel",
            ],
        )

    def test_build_search_seed_queries_uses_supported_then_extra_domain_titles(self):
        queries = build_search_seed_queries(
            [
                {"title": "Apple iPhone 15 - Target", "domain": "target.com"},
                {"title": "Unlocked Pixel 9 Pro", "domain": "bestbuy.com"},
                {"title": "Premium Phone Case", "domain": "reddit.com"},
            ],
            original_query="phone",
            selected_variant="phone target",
        )

        self.assertEqual(
            queries,
            [
                "apple iphone 15",
                "apple iphone 15 target",
                "unlocked pixel 9 pro",
                "phone",
                "phone target",
            ],
        )

    def test_build_related_seed_queries_prefers_brand_title_then_related_titles(self):
        queries = build_related_seed_queries(
            {"name": "Apple Watch Series 9", "brand": "Apple", "category": "Electronics"},
            [
                {"name": "Apple Watch SE"},
                {"name": "Garmin Venu 3"},
                {"name": "Fitbit Sense 2"},
            ],
        )

        self.assertEqual(queries[0], "apple watch series 9")
        self.assertIn("apple watch se", queries)
        self.assertIn("garmin venu 3", queries)
        self.assertLessEqual(len(queries), 6)

    def test_build_related_family_query_keeps_product_family_terms(self):
        family_query = build_related_family_query(
            {"name": "Nike Running Shoes", "brand": "Nike", "category": "Fashion"},
            [
                {"name": "Adidas Sneakers"},
                {"name": "Summer Sandals"},
                {"name": "Walking Shoes"},
            ],
        )

        self.assertIn("running", family_query)
        self.assertIn("shoes", family_query)
        self.assertIn("sneakers", family_query)
        self.assertIn("sandals", family_query)

    def test_apify_client_falls_back_to_single_query_requests(self):
        client = ApifyClient()
        batch_error = RuntimeError("batch failed")
        first_single = [
            {
                "searchQuery": {"term": "iphone target"},
                "organicResults": [
                    {
                        "title": "Apple iPhone 15 - Target",
                        "url": "https://www.target.com/p/apple-iphone-15/-/A-123456",
                        "description": "Shop Apple iPhone 15 at Target.",
                    }
                ],
            }
        ]
        second_single = [
            {
                "searchQuery": {"term": "iphone walmart"},
                "organicResults": [
                    {
                        "title": "Apple iPhone 15 - Walmart",
                        "url": "https://www.walmart.com/ip/apple-iphone-15/123456",
                        "description": "Shop Apple iPhone 15 at Walmart.",
                    }
                ],
            }
        ]

        with patch.object(
            client,
            "_request_dataset_items",
            AsyncMock(side_effect=[batch_error, first_single, second_single]),
        ):
            result = asyncio.run(client.search(query_variants=["iphone target", "iphone walmart"]))

        self.assertEqual(len(result.results), 2)
        self.assertEqual(result.results[0].query, "iphone target")
        self.assertEqual(result.results[1].query, "iphone walmart")

    def test_searxng_client_falls_back_to_single_engine_requests(self):
        client = SearXNGClient()
        with patch.object(
            client,
            "_request_search_payload",
            AsyncMock(
                side_effect=[
                    RuntimeError("multi-engine failure"),
                    {
                        "results": [
                            {
                                "url": "https://www.target.com/p/apple-watch/-/A-123456",
                                "title": "Apple Watch - Target",
                                "content": "Target product listing",
                                "engine": "duckduckgo",
                            }
                        ]
                    },
                ]
            ),
        ):
            result = asyncio.run(client.search(query_text="apple watch"))

        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.engines, ["duckduckgo"])

    def test_search_uses_discovery_results_for_weak_first_page(self):
        product_id = self._seed_product(
            url_suffix="apple-iphone-15",
            title="Apple iPhone 15",
            description="Apple smartphone with OLED display",
            category_id="electronics",
            tags=["iphone", "apple", "smartphone"],
        )
        runner = jobs_module.CatalogJobRunner()
        discovery_product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/apple-iphone-15/-/A-1001",
            canonical_source_url="https://www.target.com/p/apple-iphone-15/-/A-1001",
            title="Apple iPhone 15",
            description="Apple smartphone with OLED display",
            price=299.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Apple",
            source_image_url="https://images.example.com/apple-iphone-15.jpg",
            rating=4.8,
            review_count=120,
            tags=["iphone", "apple", "smartphone"],
        )
        discovery_result = ApifySearchResult(
            queries=[jobs_module.expand_discovery_variants("iphone", "electronics")[0]],
            actor_id="apify~google-search-scraper",
            locale={"country": "US", "language": "en", "domain": "com"},
            results=[
                ApifyQueryResult(
                    query=jobs_module.expand_discovery_variants("iphone", "electronics")[0],
                    hits=[
                        DiscoveryHit(
                            title="Apple iPhone 15",
                            url="https://www.target.com/p/apple-iphone-15/-/A-1001",
                            normalized_url="https://target.com/p/apple-iphone-15/-/A-1001",
                            domain="target.com",
                            provider_name="target_requests",
                            snippet="Shop Apple iPhone 15 at Target.",
                            source="apify",
                            source_title="Apple iPhone 15",
                            source_snippet="Shop Apple iPhone 15 at Target.",
                            source_rank=1,
                            engine="google-search-scraper",
                        )
                    ],
                )
            ],
            latency_ms=12,
            request_json={"queries": "apple iphone smartphone"},
        )

        with patch.object(jobs_module, "discovery_is_active", lambda: True), patch.object(
            jobs_module.apify_client,
            "search",
            AsyncMock(return_value=discovery_result),
        ), patch.object(
            jobs_module.searxng_client,
            "search",
            AsyncMock(
                return_value=SearXNGSearchResult(
                    query="apple iphone 15",
                    page=1,
                    engines=["duckduckgo", "bing", "startpage"],
                    hits=[],
                )
            ),
        ), patch.object(
            runner._provider_map["target_requests"],
            "search_by_urls",
            AsyncMock(return_value=ProviderSearchResult(provider="target_requests", items=[discovery_product])),
        ), patch.object(
            runner,
            "_persist_products",
            AsyncMock(return_value={"productIds": [product_id], "aiCategoryJudgeUsed": False}),
        ):
            payload = asyncio.run(runner.search("iphone", page=1, page_size=20, category_id="electronics"))

        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["name"], "Apple iPhone 15")
        self.assertTrue(payload["discovery"]["invoked"])
        self.assertGreaterEqual(payload["discovery"]["candidateUrlCount"], 1)
        self.assertGreaterEqual(payload["discovery"]["acceptedUrlCount"], 1)
        self.assertEqual(payload["discovery"]["provider"], "apify")
        self.assertEqual(payload["discovery"]["selectedVariant"], jobs_module.expand_discovery_variants("iphone", "electronics")[0])

    def test_run_term_search_skips_provider_exceptions(self):
        product_id = self._seed_product(
            url_suffix="backup-smartwatch",
            title="Backup Smartwatch",
            description="Fallback provider result",
            category_id="electronics",
            tags=["watch", "smartwatch"],
        )
        runner = jobs_module.CatalogJobRunner()
        walmart_product = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/backup-smartwatch/1001",
            canonical_source_url="https://www.walmart.com/ip/backup-smartwatch/1001",
            title="Backup Smartwatch",
            description="Fallback provider result",
            price=149.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Fallback",
            source_image_url="https://images.example.com/backup-smartwatch.jpg",
            rating=4.2,
            review_count=10,
            tags=["watch", "smartwatch"],
        )

        with patch.object(
            runner._provider_map["target_requests"],
            "search",
            AsyncMock(side_effect=RuntimeError("target temporary failure")),
        ), patch.object(
            runner._provider_map["walmart_requests"],
            "search",
            AsyncMock(return_value=ProviderSearchResult(provider="Walmart", items=[walmart_product])),
        ), patch.object(
            runner,
            "_persist_products",
            AsyncMock(return_value={"productIds": [product_id], "aiCategoryJudgeUsed": False}),
        ):
            result = asyncio.run(
                runner._run_term_search(
                    search_term="smart watch",
                    provider_page=1,
                    fetch_size=8,
                    ranking_query="smart watch",
                    category_id="electronics",
                    strict_category=True,
                    provider_names=["target_requests", "walmart_requests"],
                )
            )

        self.assertEqual(result["acceptedIds"], [product_id])
        self.assertIn("target temporary failure", result["message"])

    def test_backfill_product_galleries_enriches_existing_products(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-runner/-/A-4001",
            canonical_source_url="https://www.target.com/p/demo-runner/-/A-4001",
            title="Demo Runner",
            description="Seed product for gallery backfill testing",
            price=79.99,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Stride",
            source_image_url="https://images.example.com/demo-runner-main.jpg",
            rating=4.5,
            review_count=12,
            tags=["runner", "fashion"],
        )
        product_id = db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-demo-runner-main",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )[0]
        runner = jobs_module.CatalogJobRunner()
        enriched_product = ProviderProduct(
            provider="Target",
            source_url=product.source_url,
            canonical_source_url=product.canonical_source_url,
            title=product.title,
            description=product.description,
            price=product.price,
            currency=product.currency,
            category_id=product.category_id,
            category=product.category,
            brand=product.brand,
            source_image_url=product.source_image_url,
            image_gallery_urls=[
                product.source_image_url,
                "https://images.example.com/demo-runner-side.jpg",
                "https://images.example.com/demo-runner-detail.jpg",
            ],
            rating=product.rating,
            review_count=product.review_count,
            tags=product.tags,
        )

        async def persist_products(products):
            product_ids = db_module.upsert_products(
                products,
                {
                    image_url: {
                        "local_image_key": f"img-{index}",
                        "image_mime": "image/jpeg",
                        "image_width": 640,
                        "image_height": 640,
                    }
                    for index, image_url in enumerate({item.source_image_url for item in products})
                },
                with_meta=True,
            )
            return product_ids

        with patch.object(
            runner._provider_map["target_requests"],
            "enrich_product",
            AsyncMock(return_value=enriched_product),
        ), patch.object(
            runner,
            "_persist_products",
            AsyncMock(side_effect=persist_products),
        ):
            summary = asyncio.run(runner.backfill_product_galleries(product_ids=[product_id], concurrency=1))

        self.assertEqual(summary["enrichedProducts"], 1)
        self.assertEqual(summary["failedProducts"], 0)
        self.assertGreaterEqual(summary["productsWithGallerySizeGt1"], 1)
        refreshed_product = db_module.get_product(product_id)
        self.assertIsNotNone(refreshed_product)
        assert refreshed_product is not None
        self.assertGreater(len(refreshed_product["imageGallery"]), 1)

    def test_search_page_one_merges_searxng_after_apify(self):
        target_id = self._seed_product(
            url_suffix="threshold-bath-towel",
            title="Threshold Quick Dry Bath Towel",
            description="Quick dry towel from Target",
            category_id="home",
            tags=["towel", "bath", "target"],
        )
        walmart_id = self._seed_product(
            url_suffix="mainstays-bath-towel",
            title="Mainstays Performance Solid Bath Towel",
            description="Soft bath towel from Walmart",
            category_id="home",
            tags=["towel", "bath", "walmart"],
        )
        runner = jobs_module.CatalogJobRunner()
        target_product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/threshold-bath-towel/-/A-1001",
            canonical_source_url="https://www.target.com/p/threshold-bath-towel/-/A-1001",
            title="Threshold Quick Dry Bath Towel",
            description="Quick dry towel from Target",
            price=12.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="Threshold",
            source_image_url="https://images.example.com/threshold-bath-towel.jpg",
            rating=4.6,
            review_count=55,
            tags=["towel", "bath"],
        )
        walmart_product = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/mainstays-bath-towel/1001",
            canonical_source_url="https://www.walmart.com/ip/mainstays-bath-towel/1001",
            title="Mainstays Performance Solid Bath Towel",
            description="Soft bath towel from Walmart",
            price=9.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="Mainstays",
            source_image_url="https://images.example.com/mainstays-bath-towel.jpg",
            rating=4.4,
            review_count=74,
            tags=["towel", "bath"],
        )
        selected_variant = jobs_module.expand_discovery_variants("towel")[0]
        apify_result = ApifySearchResult(
            queries=[selected_variant],
            actor_id="apify~google-search-scraper",
            locale={"country": "US", "language": "en", "domain": "com"},
            results=[
                ApifyQueryResult(
                    query=selected_variant,
                    hits=[
                        DiscoveryHit(
                            title="Threshold Quick Dry Bath Towel - Target",
                            url="https://www.target.com/p/threshold-bath-towel/-/A-1001",
                            normalized_url="https://target.com/p/threshold-bath-towel/-/A-1001",
                            domain="target.com",
                            provider_name="target_requests",
                            snippet="Quick dry towel from Target.",
                            source="apify",
                            source_title="Threshold Quick Dry Bath Towel - Target",
                            source_snippet="Quick dry towel from Target.",
                            source_rank=1,
                            engine="google-search-scraper",
                        )
                    ],
                )
            ],
            latency_ms=18,
            request_json={"queries": "bath towel target"},
        )

        async def persist_products(items):
            first_title = items[0].title
            if "Threshold" in first_title:
                return {"productIds": [target_id], "aiCategoryJudgeUsed": False}
            return {"productIds": [walmart_id], "aiCategoryJudgeUsed": False}

        with patch.object(jobs_module, "discovery_is_active", lambda: True), patch.object(
            jobs_module.apify_client,
            "search",
            AsyncMock(return_value=apify_result),
        ), patch.object(
            jobs_module.searxng_client,
            "search",
            AsyncMock(
                return_value=SearXNGSearchResult(
                    query="threshold quick dry bath towel",
                    page=1,
                    engines=["duckduckgo", "bing", "startpage"],
                    hits=[
                        DiscoveryHit(
                            title="Mainstays Performance Solid Bath Towel - Walmart",
                            url="https://www.walmart.com/ip/mainstays-bath-towel/1001",
                            normalized_url="https://walmart.com/ip/mainstays-bath-towel/1001",
                            domain="walmart.com",
                            provider_name="walmart_requests",
                            snippet="Soft bath towel from Walmart.",
                            source="organic",
                            source_title="Mainstays Performance Solid Bath Towel - Walmart",
                            source_snippet="Soft bath towel from Walmart.",
                            source_rank=1,
                            engine="duckduckgo",
                        )
                    ],
                )
            ),
        ), patch.object(
            runner._provider_map["target_requests"],
            "search_by_urls",
            AsyncMock(return_value=ProviderSearchResult(provider="target_requests", items=[target_product])),
        ), patch.object(
            runner._provider_map["walmart_requests"],
            "search_by_urls",
            AsyncMock(return_value=ProviderSearchResult(provider="walmart_requests", items=[walmart_product])),
        ), patch.object(
            runner,
            "_persist_products",
            AsyncMock(side_effect=persist_products),
        ):
            payload = asyncio.run(runner.search("towel", page=1, page_size=20))

        self.assertEqual([item["id"] for item in payload["items"][:2]], [target_id, walmart_id])
        self.assertEqual(payload["discovery"]["provider"], "apify")
        self.assertIn("google-search-scraper", payload["discovery"]["engines"])
        self.assertIn("duckduckgo", payload["discovery"]["engines"])
        self.assertGreaterEqual(payload["discovery"]["candidateUrlCount"], 2)

    def test_search_page_two_uses_searxng_only_pagination(self):
        page_one_id = self._seed_product(
            url_suffix="threshold-bath-towel",
            title="Threshold Quick Dry Bath Towel",
            description="Quick dry towel from Target",
            category_id="home",
            tags=["towel", "bath", "target"],
        )
        page_two_id = self._seed_product(
            url_suffix="project-bath-towel",
            title="Project 62 Bath Towel",
            description="Soft bath towel from Target",
            category_id="home",
            tags=["towel", "bath", "target"],
        )
        runner = jobs_module.CatalogJobRunner()
        page_two_product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/project-bath-towel/-/A-1002",
            canonical_source_url="https://www.target.com/p/project-bath-towel/-/A-1002",
            title="Project 62 Bath Towel",
            description="Soft bath towel from Target",
            price=10.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="Project 62",
            source_image_url="https://images.example.com/project-bath-towel.jpg",
            rating=4.5,
            review_count=48,
            tags=["towel", "bath"],
        )
        context_key = runner._search_context_key("towel")
        cursor = {
            "variantIndex": 0,
            "variants": [{"term": "towel", "page": 1, "exhausted": True}],
            "matchingSource": "expanded",
            "exactMatchCount": 1,
            "filteredOutCount": 0,
            "categoryJudgeUsed": False,
            "discoveryPagination": {
                "provider": "searxng",
                "seedQueries": ["towel"],
                "nextPageBySeed": {"towel": 1},
                "exhaustedSeeds": [],
                "seedIndex": 0,
            },
        }
        db_module.save_query_results(
            context_key,
            "towel",
            1,
            "cache",
            [page_one_id],
            next_page_token_json=json.dumps(cursor),
            query_kind="search",
            query_variants=jobs_module.expand_query_variants("towel"),
        )

        async def persist_products(items):
            return {"productIds": [page_two_id], "aiCategoryJudgeUsed": False}

        with patch.object(
            runner,
            "_ensure_context_results",
            AsyncMock(side_effect=AssertionError("direct provider pagination should not run")),
        ), patch.object(
            jobs_module.searxng_client,
            "search",
            AsyncMock(
                return_value=SearXNGSearchResult(
                    query="towel",
                    page=1,
                    engines=["duckduckgo", "bing", "startpage"],
                    hits=[
                        DiscoveryHit(
                            title="Project 62 Bath Towel - Target",
                            url="https://www.target.com/p/project-bath-towel/-/A-1002",
                            normalized_url="https://target.com/p/project-bath-towel/-/A-1002",
                            domain="target.com",
                            provider_name="target_requests",
                            snippet="Soft bath towel from Target.",
                            source="organic",
                            source_title="Project 62 Bath Towel - Target",
                            source_snippet="Soft bath towel from Target.",
                            source_rank=1,
                            engine="duckduckgo",
                        )
                    ],
                )
            ),
        ), patch.object(
            runner._provider_map["target_requests"],
            "search_by_urls",
            AsyncMock(return_value=ProviderSearchResult(provider="target_requests", items=[page_two_product])),
        ), patch.object(
            runner,
            "_persist_products",
            AsyncMock(side_effect=persist_products),
        ):
            payload = asyncio.run(runner.search("towel", page=2, page_size=1))

        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["id"], page_two_id)
        self.assertEqual(payload["matching"]["source"], "expanded")

    def test_related_page_two_uses_catalog_search_pagination(self):
        base_id = self._seed_product(
            url_suffix="apple-watch-9",
            title="Apple Watch Series 9",
            description="Smart watch from Apple",
            category_id="electronics",
            tags=["watch", "smartwatch", "apple"],
        )
        extra_id = self._seed_product(
            url_suffix="fitbit-sense-2",
            title="Fitbit Sense 2",
            description="Smart watch from Fitbit",
            category_id="electronics",
            tags=["watch", "smartwatch", "fitbit"],
        )
        runner = jobs_module.CatalogJobRunner()
        extra_product = db_module.get_product(extra_id)
        assert extra_product is not None

        with patch.object(
            runner,
            "search",
            AsyncMock(
                return_value={
                    "items": [extra_product],
                    "page": 2,
                    "pageSize": 3,
                    "hasMore": False,
                    "total": 3,
                }
            ),
        ) as search_mock:
            payload = asyncio.run(runner.get_related(base_id, page=2, page_size=1))

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["items"][0]["id"], extra_id)
        self.assertEqual(payload["page"], 2)
        self.assertEqual(search_mock.await_args.kwargs["page"], 2)

    def test_related_page_filters_out_the_open_product_from_search_results(self):
        base_id = self._seed_product(
            url_suffix="tennis-ball-can",
            title="Championship Tennis Ball Can",
            description="Pressurized tennis balls for match play",
            category_id="sports",
            tags=["tennis", "ball", "sports"],
        )
        related_id = self._seed_product(
            url_suffix="training-tennis-ball-pack",
            title="Training Tennis Ball Pack",
            description="Durable tennis ball pack for practice sessions",
            category_id="sports",
            tags=["tennis", "ball", "practice"],
        )
        runner = jobs_module.CatalogJobRunner()
        current_product = db_module.get_product(base_id)
        related_product = db_module.get_product(related_id)
        assert current_product is not None
        assert related_product is not None

        with patch.object(
            runner,
            "search",
            AsyncMock(
                return_value={
                    "items": [current_product, related_product],
                    "page": 1,
                    "pageSize": 4,
                    "hasMore": False,
                    "total": 2,
                }
            ),
        ):
            payload = asyncio.run(runner.get_related(base_id, page=1, page_size=2))

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual([item["id"] for item in payload["items"]], [related_id])

    def test_related_page_one_uses_search_total_for_has_more(self):
        base_id = self._seed_product(
            url_suffix="portable-ping-pong-table",
            title="Portable Ping Pong Table",
            description="Foldable table tennis table with paddles",
            category_id="others",
            tags=["ping pong", "table tennis", "portable"],
        )
        first_related_id = self._seed_product(
            url_suffix="blue-portable-table-tennis-table",
            title="Blue Portable Table Tennis Table",
            description="Tournament-style ping pong table for indoor games",
            category_id="others",
            tags=["table tennis", "ping pong", "portable"],
        )
        runner = jobs_module.CatalogJobRunner()
        first_related = db_module.get_product(first_related_id)
        assert first_related is not None

        with patch.object(
            runner,
            "search",
            AsyncMock(
                return_value={
                    "items": [first_related],
                    "page": 1,
                    "pageSize": 3,
                    "hasMore": False,
                    "total": 4,
                }
            ),
        ):
            payload = asyncio.run(runner.get_related(base_id, page=1, page_size=1))

        assert payload is not None
        self.assertTrue(payload["hasMore"])

    def test_get_related_uses_catalog_search_results_directly(self):
        base_id = self._seed_product(
            url_suffix="rubiks-cube-classic",
            title="Rubik's Cube The Original 3x3 Cube",
            description="Classic Rubik puzzle cube brain teaser",
            category_id="toys",
            tags=["rubik", "cube", "puzzle"],
        )
        related_id = self._seed_product(
            url_suffix="rubiks-speed-cube",
            title="Rubik's 3x3 Speed Cube",
            description="Magnetic Rubik speed cube",
            category_id="others",
            tags=["rubik", "cube", "speed cube"],
        )
        runner = jobs_module.CatalogJobRunner()
        current_product = db_module.get_product(base_id)
        related_product = db_module.get_product(related_id)
        assert current_product is not None
        assert related_product is not None

        with patch.object(
            runner,
            "search",
            AsyncMock(
                return_value={
                    "items": [current_product, related_product],
                    "page": 1,
                    "pageSize": 3,
                    "hasMore": False,
                    "total": 2,
                }
            ),
        ) as search_mock:
            payload = asyncio.run(runner.get_related(base_id, page=1, page_size=1))

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual([item["id"] for item in payload["items"]], [related_id])
        self.assertIsNone(search_mock.await_args.kwargs["category_id"])

    def test_get_detail_populates_related_products_from_catalog_search(self):
        base_id = self._seed_product(
            url_suffix="rubiks-cube-detail",
            title="Rubik's Cube The Original 3x3 Cube",
            description="Classic Rubik puzzle cube brain teaser",
            category_id="toys",
            tags=["rubik", "cube", "puzzle"],
        )
        related_id = self._seed_product(
            url_suffix="rubiks-cube-detail-speed",
            title="Rubik's 3x3 Speed Cube",
            description="Magnetic Rubik speed cube",
            category_id="others",
            tags=["rubik", "cube", "speed cube"],
        )
        runner = jobs_module.CatalogJobRunner()
        current_product = db_module.get_product(base_id)
        related_product = db_module.get_product(related_id)
        assert current_product is not None
        assert related_product is not None

        with patch.object(
            runner,
            "_provider_for_product",
            lambda *args, **kwargs: None,
        ), patch.object(
            runner,
            "search",
            AsyncMock(
                return_value={
                    "items": [current_product, related_product],
                    "page": 1,
                    "pageSize": 8,
                    "hasMore": False,
                    "total": 2,
                }
            ),
        ):
            payload = asyncio.run(runner.get_detail(base_id))

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual([item["id"] for item in payload["relatedProducts"]], [related_id])

    def test_related_page_ignores_old_related_cache_rows_when_search_finds_better_match(self):
        base_id = self._seed_product(
            url_suffix="nike-running-shoes",
            title="Nike Running Shoes",
            description="Performance running shoes for training",
            category_id="fashion",
            tags=["nike", "running", "shoes"],
        )
        stale_related_id = self._seed_product(
            url_suffix="summer-sandals",
            title="Summer Sandals",
            description="Comfort sandals for walking",
            category_id="fashion",
            tags=["sandals", "walking", "summer"],
        )
        search_related_id = self._seed_product(
            url_suffix="nike-pegasus-running-shoes",
            title="Nike Pegasus Running Shoes",
            description="Performance running shoes for training and race day",
            category_id="fashion",
            tags=["nike", "running", "shoes"],
        )
        runner = jobs_module.CatalogJobRunner()
        search_related = db_module.get_product(search_related_id)
        assert search_related is not None

        with db_module.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO related_products (product_id, related_product_id, score, reason)
                VALUES (?, ?, ?, ?)
                """,
                (base_id, stale_related_id, 9.8, "Old related cache row"),
            )
            connection.commit()

        with patch.object(
            runner,
            "search",
            AsyncMock(
                return_value={
                    "items": [search_related],
                    "page": 1,
                    "pageSize": 4,
                    "hasMore": False,
                    "total": 1,
                }
            ),
        ):
            payload = asyncio.run(runner.get_related(base_id, page=1, page_size=1))

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual([item["id"] for item in payload["items"]], [search_related_id])

    def test_search_falls_back_cleanly_when_discovery_fails(self):
        product_id = self._seed_product(
            url_suffix="apple-watch-se",
            title="Apple Watch SE",
            description="Smart watch for fitness and notifications",
            category_id="electronics",
            tags=["watch", "smartwatch", "apple"],
        )
        runner = jobs_module.CatalogJobRunner()

        async def ensure_context_results(**kwargs):
            db_module.save_query_results(
                kwargs["context_key"],
                kwargs["display_query"],
                1,
                "target_requests",
                [product_id],
                query_kind="search",
                category_id=kwargs["category_id"],
                query_variants=kwargs["variants"],
            )
            return {
                "exactMatchCount": 1,
                "matchingSource": "exact",
                "filteredOutCount": 0,
                "categoryJudgeUsed": False,
            }

        with patch.object(jobs_module, "discovery_is_active", lambda: True), patch.object(
            jobs_module.apify_client,
            "search",
            AsyncMock(
                return_value=ApifySearchResult(
                    queries=["smart watch"],
                    actor_id="apify~google-search-scraper",
                    locale={"country": "US", "language": "en", "domain": "com"},
                    results=[],
                    engines=["google-search-scraper"],
                    latency_ms=7,
                    error="apify unavailable",
                    request_json={"queries": "smart watch"},
                )
            ),
        ), patch.object(
            runner,
            "_ensure_context_results",
            AsyncMock(side_effect=ensure_context_results),
        ):
            payload = asyncio.run(runner.search("smart watch", page=1, page_size=20, category_id="electronics"))

        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["name"], "Apple Watch SE")
        self.assertTrue(payload["discovery"]["invoked"])
        self.assertEqual(payload["discovery"]["candidateUrlCount"], 0)
        self.assertEqual(payload["discovery"]["acceptedUrlCount"], 0)
        self.assertIsNotNone(payload["discovery"]["fallbackReason"])


if __name__ == "__main__":
    unittest.main()
