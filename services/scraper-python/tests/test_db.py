from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.providers.base import ProviderProduct, ProviderReview
from app.storage import db as db_module
from app.utils import classify_category, expand_query_variants


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self.temp_dir.name) / "catalog.db"
        db_module.initialize_database()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_product(
        self,
        *,
        url_suffix: str,
        title: str,
        description: str,
        category_id: str,
        provider: str = "Target",
        brand: str = "Demo",
        price: float = 29.99,
        rating: float = 4.5,
        review_count: int = 20,
        tags: list[str] | None = None,
        family_key: str | None = None,
    ) -> ProviderProduct:
        return ProviderProduct(
            provider=provider,
            source_url=f"https://www.example.com/{provider.lower()}/{url_suffix}",
            canonical_source_url=f"https://www.example.com/{provider.lower()}/{url_suffix}",
            title=title,
            description=description,
            price=price,
            currency="USD",
            category_id=category_id,
            category=category_id.title(),
            brand=brand,
            source_image_url=f"https://images.example.com/{url_suffix}.jpg",
            rating=rating,
            review_count=review_count,
            tags=tags or [],
            family_key=family_key,
        )

    def _image_meta(self, products: list[ProviderProduct]) -> dict[str, dict[str, object]]:
        return {
            product.source_image_url: {
                "local_image_key": f"img-{index}",
                "image_mime": "image/jpeg",
                "image_width": 640,
                "image_height": 640,
            }
            for index, product in enumerate(products, start=1)
            if product.source_image_url
        }

    def _prepare_candidates(self, products: list[ProviderProduct]) -> list[dict[str, object]]:
        prepared = db_module.prepare_product_candidates(products, self._image_meta(products))
        return prepared["candidates"]

    def _upsert(self, products: list[ProviderProduct]) -> list[str]:
        return db_module.upsert_products(products, self._image_meta(products))

    def test_upsert_and_list_products(self):
        product = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/demo-tv/1001",
            canonical_source_url="https://www.walmart.com/ip/demo-tv/1001",
            title="Demo TV",
            description="Demo television for testing",
            price=299.99,
            original_price=349.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/demo-tv.jpg",
            rating=4.4,
            review_count=12,
            tags=["demo", "tv"],
        )
        db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-demo-tv",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )

        payload = db_module.list_products(page=1, page_size=20)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["name"], "Demo TV")
        self.assertEqual(payload["items"][0]["localImageKey"], "img-demo-tv")
        self.assertEqual(payload["items"][0]["imageUrl"], "/images/img-demo-tv")

    def test_upsert_persists_products_when_image_prefetch_fails(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-kettle/-/A-1003",
            canonical_source_url="https://www.target.com/p/demo-kettle/-/A-1003",
            title="Demo Kettle",
            description="Tea kettle without prefetched image metadata",
            price=39.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="Demo",
            source_image_url="https://images.example.com/demo-kettle.jpg",
            rating=4.3,
            review_count=17,
            tags=["kettle", "kitchen"],
        )

        product_ids = db_module.upsert_products([product], {})

        self.assertEqual(len(product_ids), 1)
        payload = db_module.list_products(page=1, page_size=20)
        self.assertEqual(payload["total"], 1)
        expected_key = hashlib.sha1(product.source_image_url.encode("utf-8")).hexdigest()
        self.assertEqual(payload["items"][0]["localImageKey"], expected_key)
        self.assertEqual(payload["items"][0]["imageUrl"], f"/images/{expected_key}")

    def test_upsert_skips_products_without_any_image(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-imageless/-/A-1004",
            canonical_source_url="https://www.target.com/p/demo-imageless/-/A-1004",
            title="Imageless Product",
            description="This product should never be stored",
            price=19.99,
            currency="USD",
            category_id="others",
            category="Others",
            brand="Demo",
            source_image_url="",
            image_gallery_urls=[],
            rating=4.1,
            review_count=2,
            tags=["imageless"],
        )

        product_ids = db_module.upsert_products([product], {})

        self.assertEqual(product_ids, [])
        payload = db_module.list_products(page=1, page_size=20)
        self.assertEqual(payload["total"], 0)

    def test_initialize_database_purges_existing_products_without_images(self):
        primary = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-imageless-cleanup/-/A-1005",
            canonical_source_url="https://www.target.com/p/demo-imageless-cleanup/-/A-1005",
            title="Imageless Cleanup Product",
            description="Stored first, then stripped of images",
            price=29.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="Demo",
            source_image_url="https://images.example.com/demo-imageless-cleanup.jpg",
            rating=4.2,
            review_count=4,
            tags=["cleanup"],
        )
        related = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-cleanup-related/-/A-1006",
            canonical_source_url="https://www.target.com/p/demo-cleanup-related/-/A-1006",
            title="Cleanup Related Product",
            description="Valid product kept after purge",
            price=31.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="Demo",
            source_image_url="https://images.example.com/demo-cleanup-related.jpg",
            rating=4.5,
            review_count=7,
            tags=["cleanup", "related"],
        )
        product_ids = db_module.upsert_products(
            [primary, related],
            {
                primary.source_image_url: {
                    "local_image_key": "img-demo-imageless-cleanup",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                related.source_image_url: {
                    "local_image_key": "img-demo-cleanup-related",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )
        auth = db_module.create_user("imageless@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None
        db_module.add_user_favorite(user["id"], product_ids[0])
        db_module.record_user_event(user["id"], "product_view", product_id=product_ids[0])
        db_module.save_query_results(
            "search::all::cleanup",
            "cleanup",
            1,
            "cache",
            [product_ids[0]],
            query_kind="search",
            query_variants=["cleanup"],
        )
        with db_module.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO related_products (product_id, related_product_id, score, reason)
                VALUES (?, ?, ?, ?)
                """,
                (product_ids[0], product_ids[1], 7.5, "cleanup"),
            )
            connection.execute(
                "UPDATE products SET source_image_url = '', image_gallery_json = '[]' WHERE id = ?",
                (product_ids[0],),
            )
            connection.commit()

        db_module.initialize_database()

        self.assertIsNone(db_module.get_product(product_ids[0]))
        self.assertIsNotNone(db_module.get_product(product_ids[1]))
        with db_module.get_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM products WHERE id = ?", (product_ids[0],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM query_products WHERE product_id = ?", (product_ids[0],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM related_products WHERE product_id = ? OR related_product_id = ?", (product_ids[0], product_ids[0])).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM user_favorites WHERE product_id = ?", (product_ids[0],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM user_events WHERE product_id = ?", (product_ids[0],)).fetchone()[0], 0)

    def test_extract_product_keywords_keeps_core_title_phrases(self):
        keywords = db_module.extract_product_keywords(
            {
                "name": "LEGO Technic Rubik's Cube 3x3 Puzzle Set",
                "description": "Classic Rubik puzzle cube brain teaser and building toy",
                "categoryId": "toys",
                "category": "Toys",
                "tags": ["rubik", "cube", "puzzle"],
            }
        )

        self.assertTrue(any("rubik" in keyword and "cube" in keyword for keyword in keywords))
        self.assertLessEqual(len(keywords), 8)

    def test_find_related_products_hybrid_limits_results_to_same_category(self):
        anchor = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/rubiks-cube-classic/-/A-7301",
            canonical_source_url="https://www.target.com/p/rubiks-cube-classic/-/A-7301",
            title="Rubik's Cube The Original 3x3 Cube",
            description="Classic Rubik puzzle cube brain teaser and fidget toy",
            price=10.49,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Spin Master",
            source_image_url="https://images.example.com/rubiks-cube-classic.jpg",
            rating=4.8,
            review_count=120,
            tags=["rubik", "cube", "puzzle", "brain teaser"],
        )
        same_category_match = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/rubiks-speed-cube/7302",
            canonical_source_url="https://www.walmart.com/ip/rubiks-speed-cube/7302",
            title="Rubik's Puzzle Cube Toy",
            description="Classic Rubik cube puzzle toy for brain teaser fun",
            price=14.99,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Rubik's",
            source_image_url="https://images.example.com/rubiks-speed-cube.jpg",
            rating=4.6,
            review_count=58,
            tags=["rubik", "cube", "speed cube", "puzzle"],
        )
        cross_category_match = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/lego-batmobile/-/A-7303",
            canonical_source_url="https://www.target.com/p/lego-batmobile/-/A-7303",
            title="LEGO DC Batman Batmobile",
            description="Creative building toy for Batman fans",
            price=29.99,
            currency="USD",
            category_id="others",
            category="Others",
            brand="LEGO",
            source_image_url="https://images.example.com/lego-batmobile.jpg",
            rating=4.8,
            review_count=92,
            tags=["lego", "building toy", "batman"],
        )
        product_ids = db_module.upsert_products(
            [anchor, same_category_match, cross_category_match],
            {
                anchor.source_image_url: {
                    "local_image_key": "img-rubiks-cube-classic",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                same_category_match.source_image_url: {
                    "local_image_key": "img-rubiks-speed-cube",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                cross_category_match.source_image_url: {
                    "local_image_key": "img-lego-batmobile",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        payload = db_module.find_related_products_hybrid(product_ids[0], page=1, page_size=10)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["items"][0]["id"], product_ids[1])
        self.assertIn(product_ids[1], [item["id"] for item in payload["items"]])
        self.assertNotIn(product_ids[2], [item["id"] for item in payload["items"]])
        self.assertTrue(any("rubik" in keyword and "cube" in keyword for keyword in payload["keywords"]))

    def test_batch_requested_category_forces_all_ranked_candidates_into_same_category(self):
        products = [
            self._make_product(
                url_suffix="rubiks-original",
                title="Rubik's Cube Original 3x3 Puzzle",
                description="Classic cube puzzle toy",
                category_id="toys",
                tags=["rubik", "cube", "puzzle"],
                family_key="rubik-family",
            ),
            self._make_product(
                url_suffix="rubiks-speed",
                title="Rubik's Speed Cube Puzzle",
                description="Speed cube product scraped as others",
                category_id="others",
                tags=["rubik", "cube", "speed cube"],
                family_key="rubik-family",
            ),
        ]

        candidates = self._prepare_candidates(products)
        ranked_candidates, exact_count, filtered_count = db_module.rank_product_candidates_for_query(
            candidates,
            "rubik cube",
            category_id="toys",
            strict_category=True,
        )
        resolved_category_id = db_module.resolve_batch_category(ranked_candidates, "toys")
        normalized_candidates = db_module.apply_batch_category_to_candidates(
            ranked_candidates,
            resolved_category_id=resolved_category_id,
            requested_category_id="toys",
        )
        persisted = db_module.upsert_prepared_product_candidates(
            normalized_candidates,
            requested_category_id="toys",
            with_meta=True,
        )

        self.assertEqual(exact_count, 2)
        self.assertEqual(filtered_count, 0)
        self.assertEqual(resolved_category_id, "toys")
        self.assertEqual(len(persisted["productIds"]), 2)
        stored = [db_module.get_product(product_id) for product_id in persisted["productIds"]]
        self.assertTrue(all(product is not None and product["categoryId"] == "toys" for product in stored))

    def test_batch_resolution_uses_dominant_non_other_category_without_request(self):
        products = [
            self._make_product(
                url_suffix="rubiks-classic-a",
                title="Rubik's Cube Puzzle Toy Classic",
                description="Classic cube brain teaser toy",
                category_id="toys",
                tags=["rubik", "cube", "puzzle", "toy"],
            ),
            self._make_product(
                url_suffix="rubiks-classic-b",
                title="Rubik's Cube Puzzle Toy Gift Set",
                description="Gift set with cube puzzle toy",
                category_id="toys",
                tags=["rubik", "cube", "gift", "toy"],
            ),
            self._make_product(
                url_suffix="rubiks-generic-c",
                title="Rubik's Cube Accessory Bundle",
                description="Bundle scraped into others",
                category_id="others",
                tags=["rubik", "cube"],
            ),
        ]

        candidates = self._prepare_candidates(products)
        ranked_candidates, _, _ = db_module.rank_product_candidates_for_query(candidates, "rubik cube")
        resolved_category_id = db_module.resolve_batch_category(ranked_candidates, None)
        normalized_candidates = db_module.apply_batch_category_to_candidates(
            ranked_candidates,
            resolved_category_id=resolved_category_id,
        )
        persisted = db_module.upsert_prepared_product_candidates(normalized_candidates, with_meta=True)

        self.assertEqual(resolved_category_id, "toys")
        stored = [db_module.get_product(product_id) for product_id in persisted["productIds"]]
        self.assertTrue(all(product is not None and product["categoryId"] == "toys" for product in stored))

    def test_batch_resolution_keeps_all_other_batches_in_others(self):
        products = [
            self._make_product(
                url_suffix="utility-basket-a",
                title="Utility Storage Basket",
                description="Generic organizer basket",
                category_id="others",
                tags=["basket", "storage", "organizer"],
            ),
            self._make_product(
                url_suffix="utility-basket-b",
                title="Storage Basket Organizer",
                description="Generic basket and organizer",
                category_id="others",
                tags=["basket", "storage"],
            ),
        ]

        candidates = self._prepare_candidates(products)
        ranked_candidates, _, _ = db_module.rank_product_candidates_for_query(candidates, "storage basket")
        resolved_category_id = db_module.resolve_batch_category(ranked_candidates, None)
        normalized_candidates = db_module.apply_batch_category_to_candidates(
            ranked_candidates,
            resolved_category_id=resolved_category_id,
        )
        persisted = db_module.upsert_prepared_product_candidates(normalized_candidates, with_meta=True)

        self.assertEqual(resolved_category_id, "others")
        stored = [db_module.get_product(product_id) for product_id in persisted["productIds"]]
        self.assertTrue(all(product is not None and product["categoryId"] == "others" for product in stored))

    def test_ranked_batch_persistence_does_not_store_rejected_candidates(self):
        relevant = self._make_product(
            url_suffix="kitchen-blender",
            title="Kitchen Blender",
            description="Countertop blender for smoothies",
            category_id="home",
            tags=["blender", "kitchen", "smoothie"],
        )
        unrelated = self._make_product(
            url_suffix="tennis-ball-can",
            title="Championship Tennis Ball Can",
            description="Pressurized tennis balls for match play",
            category_id="sports",
            tags=["tennis", "ball", "sports"],
        )

        candidates = self._prepare_candidates([relevant, unrelated])
        ranked_candidates, exact_count, filtered_count = db_module.rank_product_candidates_for_query(candidates, "blender")
        persisted = db_module.upsert_prepared_product_candidates(ranked_candidates, with_meta=True)

        self.assertEqual(exact_count, 1)
        self.assertEqual(filtered_count, 1)
        self.assertEqual(len(persisted["productIds"]), 1)
        payload = db_module.list_products(page=1, page_size=10)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["name"], "Kitchen Blender")

    def test_upsert_prepared_candidates_does_not_downgrade_non_other_category(self):
        original = self._make_product(
            url_suffix="rubiks-upgrade",
            title="Rubik's Cube Original",
            description="Classic cube puzzle toy",
            category_id="toys",
            tags=["rubik", "cube", "puzzle"],
        )
        product_id = self._upsert([original])[0]

        downgraded_candidate = self._make_product(
            url_suffix="rubiks-upgrade",
            title="Rubik's Cube Original",
            description="Same item recollected as generic other",
            category_id="others",
            tags=["rubik", "cube"],
        )
        candidates = self._prepare_candidates([downgraded_candidate])
        db_module.upsert_prepared_product_candidates(candidates, with_meta=True)

        refreshed = db_module.get_product(product_id)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed["categoryId"], "toys")

    def test_query_results_and_append_share_one_collection_code(self):
        products = [
            self._make_product(
                url_suffix=f"chair-{index}",
                title=f"Accent Chair Variant {index}",
                description="Living room accent chair",
                category_id="home",
                tags=["chair", "accent", "home"],
            )
            for index in range(1, 5)
        ]
        product_ids = self._upsert(products)

        db_module.save_query_results(
            "search::home::chair",
            "chair",
            page_number=1,
            provider="cache",
            product_ids=product_ids[:2],
            query_kind="search",
            category_id="home",
            query_variants=["chair"],
        )
        metadata = db_module.get_query_metadata("search::home::chair")
        assert metadata is not None
        collection_code = metadata["active_collection_code"]

        db_module.append_query_results(
            "search::home::chair",
            "chair",
            provider="cache",
            product_ids=product_ids[2:],
            page_size=2,
            query_kind="search",
            category_id="home",
            query_variants=["chair"],
        )

        refreshed_metadata = db_module.get_query_metadata("search::home::chair")
        assert refreshed_metadata is not None
        self.assertEqual(refreshed_metadata["active_collection_code"], collection_code)
        stored = [db_module.get_product(product_id) for product_id in product_ids]
        self.assertTrue(all(product is not None and product["collectionCode"] == collection_code for product in stored))
        with db_module.get_connection() as connection:
            pages = connection.execute(
                """
                SELECT product_id, page_number
                FROM collection_group_products
                WHERE group_code = ?
                ORDER BY page_number ASC, rank ASC
                """,
                (collection_code,),
            ).fetchall()
        self.assertEqual(len(pages), 4)
        self.assertEqual([int(row["page_number"]) for row in pages], [1, 1, 2, 2])

    def test_related_products_prioritize_same_collection_before_category_fallback(self):
        products = [
            self._make_product(
                url_suffix="accent-chair-anchor",
                title="Harbor Accent Chair",
                description="Modern accent chair for living room seating",
                category_id="home",
                provider="Target",
                brand="Harbor",
                price=120.0,
                tags=["chair", "accent", "living room"],
                family_key="harbor-chair",
            ),
            self._make_product(
                url_suffix="accent-chair-match-a",
                title="Harbor Accent Chair Linen",
                description="Matching accent chair collected with the anchor product",
                category_id="home",
                provider="Target",
                brand="Harbor",
                price=118.0,
                tags=["chair", "accent", "linen"],
                family_key="harbor-chair",
            ),
            self._make_product(
                url_suffix="accent-chair-match-b",
                title="Harbor Accent Chair Velvet",
                description="Another matching accent chair from the same collection",
                category_id="home",
                provider="Target",
                brand="Harbor",
                price=125.0,
                tags=["chair", "accent", "velvet"],
                family_key="harbor-chair-alt",
            ),
            self._make_product(
                url_suffix="accent-chair-fallback",
                title="Modern Accent Lounge Chair",
                description="Same-category fallback result",
                category_id="home",
                provider="Walmart",
                brand="Moderno",
                price=129.0,
                tags=["chair", "accent", "lounge"],
                family_key="moderno-chair",
            ),
        ]
        product_ids = self._upsert(products)
        anchor_id, grouped_a_id, grouped_b_id, fallback_id = product_ids
        db_module.save_query_results(
            "search::home::accent-chair",
            "accent chair",
            page_number=1,
            provider="cache",
            product_ids=[anchor_id, grouped_a_id, grouped_b_id],
            query_kind="search",
            category_id="home",
            query_variants=["accent chair"],
        )

        payload = db_module.get_related_products(anchor_id, page=1, page_size=3)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIsNotNone(payload["groupCode"])
        self.assertEqual(payload["groupMatchCount"], 2)
        self.assertTrue(payload["fallbackUsed"])
        self.assertEqual(payload["items"][2]["id"], fallback_id)
        self.assertEqual({payload["items"][0]["id"], payload["items"][1]["id"]}, {grouped_a_id, grouped_b_id})

    def test_related_product_fallback_excludes_cross_category_matches(self):
        products = [
            self._make_product(
                url_suffix="rubiks-anchor",
                title="Rubik's Cube Original 3x3",
                description="Classic cube puzzle toy",
                category_id="toys",
                provider="Target",
                brand="Rubik's",
                price=14.0,
                tags=["rubik", "cube", "puzzle"],
            ),
            self._make_product(
                url_suffix="rubiks-same-category",
                title="Rubik's Speed Cube",
                description="Same-category speed cube toy",
                category_id="toys",
                provider="Walmart",
                brand="Rubik's",
                price=16.0,
                tags=["rubik", "cube", "speed cube"],
            ),
            self._make_product(
                url_suffix="rubiks-cross-category",
                title="Rubik's Cube Desk Organizer",
                description="Strong text overlap but stored in others",
                category_id="others",
                provider="Target",
                brand="Rubik's",
                price=18.0,
                tags=["rubik", "cube", "desk"],
            ),
        ]
        product_ids = self._upsert(products)
        anchor_id, same_category_id, cross_category_id = product_ids
        db_module.save_query_results(
            "search::toys::rubik-cube",
            "rubik cube",
            page_number=1,
            provider="cache",
            product_ids=[anchor_id],
            query_kind="search",
            category_id="toys",
            query_variants=["rubik cube"],
        )

        payload = db_module.get_related_products(anchor_id, page=1, page_size=5)

        self.assertIsNotNone(payload)
        assert payload is not None
        returned_ids = [item["id"] for item in payload["items"]]
        self.assertIn(same_category_id, returned_ids)
        self.assertNotIn(cross_category_id, returned_ids)
        self.assertEqual(payload["groupMatchCount"], 0)
        self.assertTrue(payload["fallbackUsed"])

    def test_reset_product_linked_state_preserves_users_sessions_and_search_history(self):
        primary = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-speaker-black/-/A-5001",
            canonical_source_url="https://www.target.com/p/demo-speaker-black/-/A-5001",
            title="Demo Speaker Black",
            description="Primary product for reset testing",
            price=99.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/reset-speaker-black.jpg",
            rating=4.5,
            review_count=14,
            tags=["speaker", "audio"],
        )
        secondary = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-speaker-white/-/A-5002",
            canonical_source_url="https://www.target.com/p/demo-speaker-white/-/A-5002",
            title="Demo Speaker White",
            description="Secondary product for related/reset testing",
            price=109.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/reset-speaker-white.jpg",
            rating=4.7,
            review_count=10,
            tags=["speaker", "audio"],
        )
        product_ids = db_module.upsert_products(
            [primary, secondary],
            {
                primary.source_image_url: {
                    "local_image_key": "img-reset-black",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                secondary.source_image_url: {
                    "local_image_key": "img-reset-white",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )
        db_module.replace_reviews(
            product_ids[0],
            [
                ProviderReview(
                    id="review-reset-1",
                    author_name="Tester",
                    rating=4.0,
                    body="Useful review",
                )
            ],
        )
        db_module.save_query_results("speaker", "speaker", page_number=1, provider="cache", product_ids=product_ids)
        auth = db_module.create_user("reset@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        self.assertIsNotNone(user)
        assert user is not None
        db_module.record_user_event(user["id"], "search", query_text="speaker", session_id="login-a")
        db_module.record_user_event(
            user["id"],
            "product_view",
            product_id=product_ids[0],
            session_id="login-a",
            metadata={"originSurface": "home"},
        )
        db_module.add_user_favorite(user["id"], product_ids[0])

        image_cache_dir = db_module.DB_PATH.parent / "session-images"
        image_cache_dir.mkdir(parents=True, exist_ok=True)
        (image_cache_dir / "stale-image.jpg").write_text("stale", "utf-8")

        with db_module.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO related_products (product_id, related_product_id, score, reason)
                VALUES (?, ?, ?, ?)
                """,
                (product_ids[0], product_ids[1], 8.5, "Reset test related row"),
            )
            connection.execute(
                """
                INSERT INTO user_recommendations (user_id, product_id, score, reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user["id"], product_ids[0], 9.0, "Reset test recommendation", db_module.now_iso()),
            )
            connection.execute(
                """
                INSERT INTO discovery_queries (
                  context_key, variant_text, query_text, category_id, provider, request_json,
                  engines_json, status, last_requested_at, last_completed_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "search::all::speaker",
                    "speaker",
                    "speaker",
                    None,
                    "apify",
                    "{}",
                    "[]",
                    "success",
                    db_module.now_iso(),
                    db_module.now_iso(),
                    None,
                ),
            )
            connection.execute(
                """
                INSERT INTO discovery_hits (
                  id, context_key, variant_text, rank, engine, source, source_title, source_snippet, source_rank,
                  domain, title, snippet, url, normalized_url, provider_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "dh-reset",
                    "search::all::speaker",
                    "speaker",
                    1,
                    "google-search-scraper",
                    "organic",
                    primary.title,
                    primary.description,
                    1,
                    "target.com",
                    primary.title,
                    primary.description,
                    primary.source_url,
                    primary.canonical_source_url,
                    "target_requests",
                    db_module.now_iso(),
                ),
            )
            connection.execute(
                """
                INSERT INTO discovery_cache (cache_key, payload_json, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("reset-cache", "{}", db_module.now_iso(), db_module.now_iso()),
            )
            connection.execute(
                """
                INSERT INTO discovery_suppression (normalized_url, provider_name, failure_count, last_failure_reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (primary.canonical_source_url, "target_requests", 1, "Reset test failure", db_module.now_iso()),
            )
            connection.commit()

        summary = db_module.reset_product_linked_state()

        self.assertEqual(summary["deleted"]["products"], 2)
        self.assertEqual(summary["deleted"]["cached_images"], 1)
        self.assertGreaterEqual(summary["deleted"]["user_events_product_linked"], 1)

        with db_module.get_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM query_products").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM related_products").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM user_favorites").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM user_recommendations").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM discovery_queries").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM discovery_hits").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM discovery_cache").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM discovery_suppression").fetchone()[0], 0)
            remaining_search_events = connection.execute(
                "SELECT COUNT(*) FROM user_events WHERE event_type = 'search'"
            ).fetchone()[0]
            remaining_product_events = connection.execute(
                "SELECT COUNT(*) FROM user_events WHERE event_type = 'product_view'"
            ).fetchone()[0]
            self.assertEqual(remaining_search_events, 1)
            self.assertEqual(remaining_product_events, 0)

    def test_initialize_database_clears_stale_related_caches_only(self):
        anchor = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-cube/-/A-5011",
            canonical_source_url="https://www.target.com/p/demo-cube/-/A-5011",
            title="Demo Cube Puzzle",
            description="Puzzle cube used for related cache invalidation testing",
            price=14.99,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Demo",
            source_image_url="https://images.example.com/demo-cube.jpg",
            rating=4.6,
            review_count=18,
            tags=["cube", "puzzle", "toy"],
        )
        related = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/demo-speed-cube/5012",
            canonical_source_url="https://www.walmart.com/ip/demo-speed-cube/5012",
            title="Demo Speed Cube",
            description="Speed cube match for related cache invalidation testing",
            price=12.99,
            currency="USD",
            category_id="others",
            category="Others",
            brand="Demo",
            source_image_url="https://images.example.com/demo-speed-cube.jpg",
            rating=4.4,
            review_count=11,
            tags=["cube", "speed cube", "puzzle"],
        )
        product_ids = db_module.upsert_products(
            [anchor, related],
            {
                anchor.source_image_url: {
                    "local_image_key": "img-demo-cube",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                related.source_image_url: {
                    "local_image_key": "img-demo-speed-cube",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        db_module.save_query_results("speaker", "speaker", page_number=1, provider="cache", product_ids=[product_ids[0]])
        with db_module.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO related_products (product_id, related_product_id, score, reason)
                VALUES (?, ?, ?, ?)
                """,
                (product_ids[0], product_ids[1], 8.9, "Stale related cache row"),
            )
            connection.execute(
                """
                INSERT INTO queries (
                  normalized_query, display_query, query_kind, category_id, status, last_requested_at, last_started_at,
                  last_completed_at, last_error, next_page_token_json, query_variants_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"related::{product_ids[0]}",
                    anchor.title,
                    "related",
                    None,
                    "success",
                    db_module.now_iso(),
                    None,
                    db_module.now_iso(),
                    None,
                    None,
                    "[]",
                ),
            )
            connection.execute(
                """
                INSERT INTO query_products (normalized_query, product_id, rank, page_number, provider, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"related::{product_ids[0]}",
                    product_ids[1],
                    1,
                    1,
                    "cache",
                    db_module.now_iso(),
                ),
            )
            connection.execute(
                """
                INSERT INTO discovery_queries (
                  context_key, variant_text, query_text, category_id, provider, request_json,
                  engines_json, status, last_requested_at, last_completed_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"related::{product_ids[0]}",
                    "demo cube",
                    "demo cube",
                    None,
                    "apify",
                    "{}",
                    "[]",
                    "success",
                    db_module.now_iso(),
                    db_module.now_iso(),
                    None,
                ),
            )
            connection.execute(
                """
                INSERT INTO discovery_hits (
                  id, context_key, variant_text, rank, engine, source, source_title, source_snippet, source_rank,
                  domain, title, snippet, url, normalized_url, provider_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "dh-related-stale",
                    f"related::{product_ids[0]}",
                    "demo cube",
                    1,
                    "google-search-scraper",
                    "organic",
                    related.title,
                    related.description,
                    1,
                    "walmart.com",
                    related.title,
                    related.description,
                    related.source_url,
                    related.canonical_source_url,
                    "walmart_requests",
                    db_module.now_iso(),
                ),
            )
            connection.commit()

        db_module.initialize_database()

        with db_module.get_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM related_products").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM queries WHERE normalized_query LIKE 'related::%'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM query_products WHERE normalized_query LIKE 'related::%'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM discovery_queries WHERE context_key LIKE 'related::%'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM discovery_hits WHERE context_key LIKE 'related::%'").fetchone()[0],
                0,
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM queries WHERE normalized_query = 'speaker'").fetchone()[0], 1)

    def test_upsert_skips_zero_price_and_sanitizes_impossible_discounts(self):
        invalid = ProviderProduct(
            provider="Amazon",
            source_url="https://www.amazon.com/dp/INVALID01",
            canonical_source_url="https://www.amazon.com/dp/INVALID01",
            title="Broken Speaker Listing",
            description="Malformed product with zero price",
            price=0.0,
            original_price=199.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Broken",
            source_image_url="https://images.example.com/broken-speaker.jpg",
            rating=4.1,
            review_count=3,
            tags=["speaker", "broken"],
        )
        suspicious_offer = ProviderProduct(
            provider="Amazon",
            source_url="https://www.amazon.com/dp/SUSPICIOUS1",
            canonical_source_url="https://www.amazon.com/dp/SUSPICIOUS1",
            title="Suspicious Discount Speaker",
            description="Speaker with impossible markdown",
            price=11.0,
            original_price=14132.06,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/suspicious-speaker.jpg",
            rating=4.4,
            review_count=8,
            tags=["speaker", "discount"],
        )
        valid_offer = ProviderProduct(
            provider="Amazon",
            source_url="https://www.amazon.com/dp/VALIDOFFER1",
            canonical_source_url="https://www.amazon.com/dp/VALIDOFFER1",
            title="Valid Discount Speaker",
            description="Speaker with a normal markdown",
            price=89.99,
            original_price=99.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/valid-speaker.jpg",
            rating=4.6,
            review_count=14,
            tags=["speaker", "audio"],
        )

        db_module.upsert_products(
            [invalid, suspicious_offer, valid_offer],
            {
                invalid.source_image_url: {
                    "local_image_key": "img-broken-speaker",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                suspicious_offer.source_image_url: {
                    "local_image_key": "img-suspicious-speaker",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                valid_offer.source_image_url: {
                    "local_image_key": "img-valid-speaker",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        payload = db_module.list_products(page=1, page_size=10)
        self.assertEqual(payload["total"], 2)
        by_name = {item["name"]: item for item in payload["items"]}
        self.assertNotIn("Broken Speaker Listing", by_name)
        self.assertIsNone(by_name["Suspicious Discount Speaker"]["originalPrice"])
        self.assertEqual(by_name["Valid Discount Speaker"]["originalPrice"], 99.99)

    def test_list_query_products_excludes_inactive_products(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-speaker/-/A-9001",
            canonical_source_url="https://www.target.com/p/demo-speaker/-/A-9001",
            title="Demo Speaker",
            description="Speaker used for query cache testing",
            price=59.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/query-speaker.jpg",
            rating=4.2,
            review_count=11,
            tags=["speaker", "audio"],
        )
        product_id = db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-query-speaker",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )[0]

        db_module.save_query_results("speaker", "speaker", page_number=1, provider="cache", product_ids=[product_id])

        with db_module.get_connection() as connection:
            connection.execute("UPDATE products SET is_active = 0 WHERE id = ?", (product_id,))
            connection.commit()

        payload = db_module.list_query_products("speaker", page=1, page_size=10)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)

    def test_auth_events_and_recommendations(self):
        electronics = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-tv/-/A-1001",
            canonical_source_url="https://www.target.com/p/demo-tv/-/A-1001",
            title="Demo TV",
            description="A television for testing recommendations",
            price=199.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="DemoBrand",
            source_image_url="https://images.example.com/demo-tv.jpg",
            rating=4.7,
            review_count=80,
            tags=["tv", "electronics"],
        )
        beauty = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-cream/-/A-1002",
            canonical_source_url="https://www.target.com/p/demo-cream/-/A-1002",
            title="Demo Cream",
            description="A beauty product for testing recommendations",
            price=19.99,
            currency="USD",
            category_id="beauty",
            category="Beauty",
            brand="Glow",
            source_image_url="https://images.example.com/demo-cream.jpg",
            rating=4.3,
            review_count=22,
            tags=["beauty", "cream"],
        )
        product_ids = db_module.upsert_products(
            [electronics, beauty],
            {
                electronics.source_image_url: {
                    "local_image_key": "img-demo-tv",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                beauty.source_image_url: {
                    "local_image_key": "img-demo-cream",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        auth = db_module.create_user("test@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        self.assertIsNotNone(user)
        self.assertEqual(user["email"], "test@example.com")

        db_module.record_user_event(user["id"], "search", query_text="tv")
        db_module.record_user_event(user["id"], "product_view", product_id=product_ids[0])
        recommendations = db_module.list_user_recommendations(user["id"], page=1, page_size=5)
        history = db_module.list_user_history(user["id"], page=1, page_size=5)

        self.assertGreaterEqual(len(recommendations["items"]), 1)
        self.assertIn("electronics", [item["categoryId"] for item in recommendations["items"][:3]])
        self.assertEqual(history["items"][0]["type"], "product_view")

    def test_history_only_tracks_allowed_product_origins_and_dedupes(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-sneaker/-/A-1201",
            canonical_source_url="https://www.target.com/p/demo-sneaker/-/A-1201",
            title="Demo Sneaker Black",
            description="A fashion sneaker used for history testing",
            price=79.99,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Demo",
            source_image_url="https://images.example.com/demo-sneaker-black.jpg",
            rating=4.5,
            review_count=40,
            tags=["sneaker", "fashion"],
        )
        product_id = db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-history-sneaker",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )[0]
        auth = db_module.create_user("history-origin@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])

        db_module.record_user_event(user["id"], "product_view", product_id=product_id, metadata={"originSurface": "home"}, session_id="login-a")
        db_module.record_user_event(user["id"], "product_view", product_id=product_id, metadata={"originSurface": "home"}, session_id="login-a")
        db_module.record_user_event(user["id"], "product_view", product_id=product_id, metadata={"originSurface": "history"}, session_id="login-a")
        db_module.record_user_event(user["id"], "search", query_text="demo sneaker", session_id="login-a")
        db_module.record_user_event(user["id"], "search", query_text="demo sneaker", session_id="login-a")

        history = db_module.list_user_history(user["id"], page=1, page_size=10, session_id="login-a")
        self.assertEqual(len([item for item in history["items"] if item["type"] == "product_view"]), 1)
        self.assertEqual(len([item for item in history["items"] if item["type"] == "search"]), 1)

    def test_family_dedupe_keeps_retailers_distinct(self):
        target_black = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-runner-black/-/A-2001",
            canonical_source_url="https://www.target.com/p/demo-runner-black/-/A-2001",
            title="Demo Runner Sneaker Black",
            description="Color variant for family dedupe testing",
            price=69.99,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Stride",
            source_image_url="https://images.example.com/demo-runner-black.jpg",
            rating=4.4,
            review_count=18,
            tags=["runner", "sneaker"],
        )
        target_white = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-runner-white/-/A-2002",
            canonical_source_url="https://www.target.com/p/demo-runner-white/-/A-2002",
            title="Demo Runner Sneaker White",
            description="Another color variant for family dedupe testing",
            price=69.99,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Stride",
            source_image_url="https://images.example.com/demo-runner-white.jpg",
            rating=4.6,
            review_count=22,
            tags=["runner", "sneaker"],
        )
        walmart_variant = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/demo-runner-black/2003",
            canonical_source_url="https://www.walmart.com/ip/demo-runner-black/2003",
            title="Demo Runner Sneaker Black",
            description="Same family, different retailer",
            price=64.99,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Stride",
            source_image_url="https://images.example.com/demo-runner-walmart.jpg",
            rating=4.2,
            review_count=9,
            tags=["runner", "sneaker"],
        )
        db_module.upsert_products(
            [target_black, target_white, walmart_variant],
            {
                target_black.source_image_url: {
                    "local_image_key": "img-runner-black",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                target_white.source_image_url: {
                    "local_image_key": "img-runner-white",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                walmart_variant.source_image_url: {
                    "local_image_key": "img-runner-walmart",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        payload = db_module.list_products(page=1, page_size=10, category_id="fashion")
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual({item["provider"] for item in payload["items"]}, {"Target", "Walmart"})
        self.assertTrue(all(item.get("familyKey") for item in payload["items"]))

    def test_product_detail_includes_gallery_and_variants(self):
        black_variant = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-backpack-black/-/A-3001",
            canonical_source_url="https://www.target.com/p/demo-backpack-black/-/A-3001",
            title="Demo Backpack Black",
            description="Backpack with multiple images",
            price=59.99,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Trail",
            source_image_url="https://images.example.com/demo-backpack-black-1.jpg",
            image_gallery_urls=[
                "https://images.example.com/demo-backpack-black-1.jpg",
                "https://images.example.com/demo-backpack-black-2.jpg",
            ],
            rating=4.5,
            review_count=32,
            tags=["backpack", "fashion"],
        )
        white_variant = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-backpack-white/-/A-3002",
            canonical_source_url="https://www.target.com/p/demo-backpack-white/-/A-3002",
            title="Demo Backpack White",
            description="Backpack sibling variant",
            price=61.99,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Trail",
            source_image_url="https://images.example.com/demo-backpack-white-1.jpg",
            image_gallery_urls=[
                "https://images.example.com/demo-backpack-white-1.jpg",
                "https://images.example.com/demo-backpack-white-2.jpg",
            ],
            rating=4.3,
            review_count=21,
            tags=["backpack", "fashion"],
        )
        product_ids = db_module.upsert_products(
            [black_variant, white_variant],
            {
                black_variant.source_image_url: {
                    "local_image_key": "img-backpack-black",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                white_variant.source_image_url: {
                    "local_image_key": "img-backpack-white",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        detail = db_module.get_product_with_reviews(product_ids[0])
        self.assertIsNotNone(detail)
        self.assertGreaterEqual(len(detail["imageGallery"]), 2)
        self.assertEqual(len(detail["variantOptions"]), 2)
        self.assertTrue(any(option["isCurrent"] for option in detail["variantOptions"]))

    def test_favorites_drive_recommendations_before_history(self):
        favorite_source = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-protein-bars/-/A-4001",
            canonical_source_url="https://www.target.com/p/demo-protein-bars/-/A-4001",
            title="Demo Protein Bars Chocolate",
            description="Favorite item for recommendation weighting",
            price=8.99,
            currency="USD",
            category_id="food",
            category="Food",
            brand="Fuel",
            source_image_url="https://images.example.com/demo-bars.jpg",
            rating=4.8,
            review_count=140,
            tags=["protein", "bars", "snack"],
        )
        recommended_food = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/demo-snack-mix/4002",
            canonical_source_url="https://www.walmart.com/ip/demo-snack-mix/4002",
            title="Demo Snack Mix Protein Bites",
            description="Food recommendation candidate",
            price=6.49,
            currency="USD",
            category_id="food",
            category="Food",
            brand="Fuel",
            source_image_url="https://images.example.com/demo-snack-mix.jpg",
            rating=4.7,
            review_count=90,
            tags=["protein", "snack", "mix"],
        )
        viewed_electronics = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-usb-cable/-/A-4003",
            canonical_source_url="https://www.target.com/p/demo-usb-cable/-/A-4003",
            title="Demo USB Cable",
            description="History-only electronics signal",
            price=12.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Wire",
            source_image_url="https://images.example.com/demo-cable.jpg",
            rating=4.1,
            review_count=20,
            tags=["usb", "cable"],
        )
        filler_electronics = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/demo-earbuds/4004",
            canonical_source_url="https://www.walmart.com/ip/demo-earbuds/4004",
            title="Demo Wireless Earbuds",
            description="Secondary electronics candidate",
            price=29.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Wire",
            source_image_url="https://images.example.com/demo-earbuds.jpg",
            rating=4.2,
            review_count=18,
            tags=["earbuds", "audio"],
        )
        product_ids = db_module.upsert_products(
            [favorite_source, recommended_food, viewed_electronics, filler_electronics],
            {
                favorite_source.source_image_url: {
                    "local_image_key": "img-bars",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                recommended_food.source_image_url: {
                    "local_image_key": "img-snack-mix",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                viewed_electronics.source_image_url: {
                    "local_image_key": "img-cable",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                filler_electronics.source_image_url: {
                    "local_image_key": "img-earbuds",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )
        auth = db_module.create_user("favorite-recs@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        db_module.add_user_favorite(user["id"], product_ids[0])
        db_module.record_user_event(
            user["id"],
            "product_view",
            product_id=product_ids[2],
            metadata={"originSurface": "catalog"},
            session_id="login-a",
        )

        recommendations = db_module.list_user_recommendations(user["id"], page=1, page_size=5, session_id="login-a")
        self.assertTrue(recommendations["items"])
        self.assertEqual(recommendations["items"][0]["categoryId"], "food")
        self.assertNotIn(product_ids[0], [item["id"] for item in recommendations["items"]])

    def test_daily_featured_offers_are_cached_and_regenerated_for_inactive_products(self):
        categories = ["electronics", "beauty", "home", "food", "fashion"]
        products: list[ProviderProduct] = []
        image_meta_by_url: dict[str, dict[str, object]] = {}
        for index in range(12):
            category_id = categories[index % len(categories)]
            product = ProviderProduct(
                provider="Target",
                source_url=f"https://www.target.com/p/daily-offer-{index}/-/A-{8000 + index}",
                canonical_source_url=f"https://www.target.com/p/daily-offer-{index}/-/A-{8000 + index}",
                title=f"Daily Offer Product {index}",
                description=f"Discounted product {index} for featured offers",
                price=49.99 + index,
                original_price=89.99 + (index * 2),
                currency="USD",
                category_id=category_id,
                category=db_module.category_name(category_id),
                brand=f"OfferBrand{index % 4}",
                source_image_url=f"https://images.example.com/daily-offer-{index}.jpg",
                rating=4.1 + ((index % 4) * 0.2),
                review_count=20 + (index * 5),
                tags=["offer", category_id, f"deal-{index}"],
            )
            products.append(product)
            image_meta_by_url[product.source_image_url] = {
                "local_image_key": f"img-daily-offer-{index}",
                "image_mime": "image/jpeg",
                "image_width": 640,
                "image_height": 640,
            }

        db_module.upsert_products(products, image_meta_by_url)

        fixed_now = datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc)
        with patch.object(db_module, "_now_datetime", return_value=fixed_now):
            first_payload = db_module.list_products(page=1, page_size=30)
            second_payload = db_module.list_products(page=1, page_size=30)

        first_offer_ids = [item["id"] for item in first_payload["offers"]]
        second_offer_ids = [item["id"] for item in second_payload["offers"]]

        self.assertEqual(first_offer_ids, second_offer_ids)
        self.assertLessEqual(len(first_offer_ids), 10)
        self.assertTrue(all(item["originalPrice"] and item["originalPrice"] > item["price"] for item in first_payload["offers"]))

        category_counts: dict[str, int] = {}
        for item in first_payload["offers"]:
            category_counts[item["categoryId"]] = category_counts.get(item["categoryId"], 0) + 1
        self.assertTrue(all(count <= 2 for count in category_counts.values()))

        with db_module.get_connection() as connection:
            snapshot_count = connection.execute("SELECT COUNT(*) AS count FROM featured_offer_snapshots").fetchone()["count"]
            self.assertEqual(snapshot_count, 1)
            connection.execute("UPDATE products SET is_active = 0 WHERE id = ?", (first_offer_ids[0],))
            connection.commit()

        with patch.object(db_module, "_now_datetime", return_value=fixed_now):
            refreshed_payload = db_module.list_products(page=1, page_size=30)

        refreshed_offer_ids = [item["id"] for item in refreshed_payload["offers"]]
        self.assertNotIn(first_offer_ids[0], refreshed_offer_ids)

    def test_history_trending_products_follow_recent_activity(self):
        viewed_blender = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-blender/-/A-8101",
            canonical_source_url="https://www.target.com/p/demo-blender/-/A-8101",
            title="Countertop Blender",
            description="Kitchen blender for smoothies and frozen drinks",
            price=89.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="BlendCo",
            source_image_url="https://images.example.com/demo-blender.jpg",
            rating=4.5,
            review_count=60,
            tags=["blender", "kitchen", "smoothie"],
        )
        top_recommendation = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/pro-performance-blender/-/A-8102",
            canonical_source_url="https://www.target.com/p/pro-performance-blender/-/A-8102",
            title="Pro Performance Blender",
            description="High-power kitchen blender with smoothie presets",
            price=129.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="BlendCo",
            source_image_url="https://images.example.com/pro-performance-blender.jpg",
            rating=4.9,
            review_count=180,
            tags=["blender", "kitchen", "smoothie"],
        )
        trending_candidate = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/blender-travel-cup/8103",
            canonical_source_url="https://www.walmart.com/ip/blender-travel-cup/8103",
            title="Blender Travel Cup Set",
            description="Portable smoothie cup set for blender owners",
            price=24.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="BlendCo",
            source_image_url="https://images.example.com/blender-travel-cup.jpg",
            rating=4.4,
            review_count=48,
            tags=["blender", "smoothie", "cup"],
        )
        unrelated_tennis_ball = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/tennis-ball-can/-/A-8104",
            canonical_source_url="https://www.target.com/p/tennis-ball-can/-/A-8104",
            title="Championship Tennis Ball Can",
            description="Pressurized tennis balls for match play",
            price=14.99,
            currency="USD",
            category_id="sports",
            category="Sports",
            brand="Ace",
            source_image_url="https://images.example.com/tennis-ball-can.jpg",
            rating=4.8,
            review_count=64,
            tags=["tennis", "ball", "sports"],
        )
        product_ids = db_module.upsert_products(
            [viewed_blender, top_recommendation, trending_candidate, unrelated_tennis_ball],
            {
                viewed_blender.source_image_url: {
                    "local_image_key": "img-demo-blender",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                top_recommendation.source_image_url: {
                    "local_image_key": "img-pro-performance-blender",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                trending_candidate.source_image_url: {
                    "local_image_key": "img-blender-travel-cup",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                unrelated_tennis_ball.source_image_url: {
                    "local_image_key": "img-tennis-ball-can-history",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        auth = db_module.create_user("history-trending@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None

        db_module.record_user_event(user["id"], "search", query_text="blender", session_id="login-a")
        db_module.record_user_event(
            user["id"],
            "product_view",
            product_id=product_ids[0],
            session_id="login-a",
            metadata={"originSurface": "catalog"},
        )

        recommendations = db_module.list_user_recommendations(user["id"], page=1, page_size=1, session_id="login-a")

        trending_ids = [item["id"] for item in recommendations["trending"]]
        self.assertTrue(trending_ids)
        self.assertIn(product_ids[2], trending_ids)
        self.assertNotIn(product_ids[0], trending_ids)
        self.assertNotIn(product_ids[3], trending_ids)

    def test_trending_products_are_empty_without_history_signal(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-candle/-/A-8110",
            canonical_source_url="https://www.target.com/p/demo-candle/-/A-8110",
            title="Demo Candle",
            description="Scented candle with no user activity yet",
            price=18.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="Glow",
            source_image_url="https://images.example.com/demo-candle.jpg",
            rating=4.5,
            review_count=25,
            tags=["candle", "home", "scented"],
        )
        db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-demo-candle",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )

        auth = db_module.create_user("empty-trending@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None

        recommendations = db_module.list_user_recommendations(user["id"], page=1, page_size=5)
        self.assertEqual(recommendations["trending"], [])

    def test_cached_search_requires_real_text_or_category_evidence(self):
        electronics = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-tv/-/A-1001",
            canonical_source_url="https://www.target.com/p/demo-tv/-/A-1001",
            title="Demo TV",
            description="A television for testing cached search",
            price=199.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="DemoBrand",
            source_image_url="https://images.example.com/demo-tv.jpg",
            rating=4.7,
            review_count=80,
            tags=["tv", "electronics"],
        )
        beauty = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-cream/-/A-1002",
            canonical_source_url="https://www.target.com/p/demo-cream/-/A-1002",
            title="Demo Cream",
            description="A beauty product for category search",
            price=19.99,
            currency="USD",
            category_id="beauty",
            category="Beauty",
            brand="Glow",
            source_image_url="https://images.example.com/demo-cream.jpg",
            rating=4.3,
            review_count=22,
            tags=["beauty", "cream"],
        )
        db_module.upsert_products(
            [electronics, beauty],
            {
                electronics.source_image_url: {
                    "local_image_key": "img-demo-tv",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                beauty.source_image_url: {
                    "local_image_key": "img-demo-cream",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        hoodie_in_electronics = db_module.search_cached_products("hoodie", page=1, page_size=10, category_id="electronics")
        tv_in_fashion = db_module.search_cached_products("tv", page=1, page_size=10, category_id="fashion")
        beauty_results = db_module.search_cached_products("beauty", page=1, page_size=10, category_id="beauty")

        self.assertEqual(hoodie_in_electronics["items"], [])
        self.assertEqual(tv_in_fashion["items"], [])
        self.assertEqual(len(beauty_results["items"]), 1)
        self.assertEqual(beauty_results["items"][0]["categoryId"], "beauty")

    def test_history_can_be_scoped_to_app_session(self):
        auth = db_module.create_user("session@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        self.assertIsNotNone(user)

        db_module.record_user_event(user["id"], "search", query_text="tv", session_id="session-a")
        db_module.record_user_event(user["id"], "search", query_text="watch", session_id="session-b")

        history_a = db_module.list_user_history(user["id"], page=1, page_size=10, session_id="session-a")
        history_b = db_module.list_user_history(user["id"], page=1, page_size=10, session_id="session-b")

        self.assertEqual(len(history_a["items"]), 1)
        self.assertEqual(history_a["items"][0]["queryText"], "tv")
        self.assertEqual(len(history_b["items"]), 1)
        self.assertEqual(history_b["items"][0]["queryText"], "watch")

    def test_visible_history_excludes_source_and_category_events(self):
        auth = db_module.create_user("history@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        self.assertIsNotNone(user)

        db_module.record_user_event(user["id"], "search", query_text="towel")
        db_module.record_user_event(user["id"], "source_open", source_url="https://www.target.com/p/demo")
        db_module.record_user_event(user["id"], "category_view", category_id="home")
        db_module.record_user_event(user["id"], "product_view", product_id="demo-product")

        history = db_module.list_user_history(user["id"], page=1, page_size=10)

        self.assertEqual([item["type"] for item in history["items"]], ["product_view", "search"])

    def test_session_scoped_recommendations_follow_current_login_interest(self):
        toy = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-toy/-/A-5001",
            canonical_source_url="https://www.target.com/p/demo-toy/-/A-5001",
            title="Plush Dog Toy",
            description="Soft dog toy for fetch and play",
            price=14.99,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Paws",
            source_image_url="https://images.example.com/demo-toy.jpg",
            rating=4.8,
            review_count=40,
            tags=["dog", "toy", "plush"],
        )
        beauty = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-serum/-/A-5002",
            canonical_source_url="https://www.target.com/p/demo-serum/-/A-5002",
            title="Glow Serum",
            description="Hydrating beauty serum",
            price=24.99,
            currency="USD",
            category_id="beauty",
            category="Beauty",
            brand="Glow",
            source_image_url="https://images.example.com/demo-serum.jpg",
            rating=4.6,
            review_count=30,
            tags=["beauty", "serum", "glow"],
        )
        product_ids = db_module.upsert_products(
            [toy, beauty],
            {
                toy.source_image_url: {
                    "local_image_key": "img-demo-toy",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                beauty.source_image_url: {
                    "local_image_key": "img-demo-serum",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        auth = db_module.create_user("recs@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None

        db_module.record_user_event(user["id"], "product_view", product_id=product_ids[0], session_id="login-a")
        db_module.record_user_event(user["id"], "product_view", product_id=product_ids[1], session_id="login-b")

        login_a = db_module.list_user_recommendations(user["id"], page=1, page_size=10, session_id="login-a")
        login_b = db_module.list_user_recommendations(user["id"], page=1, page_size=10, session_id="login-b")

        self.assertTrue(login_a["items"])
        self.assertTrue(login_b["items"])
        self.assertEqual(login_a["items"][0]["categoryId"], "toys")
        self.assertEqual(login_b["items"][0]["categoryId"], "beauty")

    def test_favorites_round_trip_and_favorite_flags(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-watch/-/A-3001",
            canonical_source_url="https://www.target.com/p/demo-watch/-/A-3001",
            title="Demo Watch",
            description="Smart watch for favorite testing",
            price=249.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/demo-watch.jpg",
            rating=4.5,
            review_count=40,
            tags=["watch", "electronics"],
        )
        product_id = db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-demo-watch",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )[0]
        auth = db_module.create_user("favorite@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None

        favorite = db_module.add_user_favorite(user["id"], product_id)
        favorites = db_module.list_user_favorites(user["id"], page=1, page_size=10)
        detail = db_module.get_product_with_reviews(product_id, user_id=user["id"])

        self.assertEqual(favorite["id"], product_id)
        self.assertTrue(favorites["items"][0]["isFavorite"])
        self.assertTrue(detail["isFavorite"])

        removed = db_module.remove_user_favorite(user["id"], product_id)
        self.assertTrue(removed)
        self.assertEqual(db_module.list_user_favorites(user["id"], page=1, page_size=10)["items"], [])

    def test_event_writes_do_not_refresh_recommendations(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-earbuds/-/A-3010",
            canonical_source_url="https://www.target.com/p/demo-earbuds/-/A-3010",
            title="Demo Earbuds",
            description="Wireless earbuds for event refresh testing",
            price=89.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/demo-earbuds.jpg",
            rating=4.4,
            review_count=20,
            tags=["earbuds", "audio"],
        )
        product_id = db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-demo-earbuds",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )[0]
        auth = db_module.create_user("event-refresh@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None

        with patch.object(db_module, "refresh_user_recommendations") as refresh_mock:
            db_module.record_user_event(
                user["id"],
                "product_view",
                product_id=product_id,
                session_id="session-a",
                metadata={"originSurface": "home"},
            )

        refresh_mock.assert_not_called()

    def test_favorite_mutations_invalidate_without_refresh(self):
        primary = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-watch-primary/-/A-3011",
            canonical_source_url="https://www.target.com/p/demo-watch-primary/-/A-3011",
            title="Demo Watch Primary",
            description="Primary favorite target",
            price=199.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/demo-watch-primary.jpg",
            rating=4.5,
            review_count=41,
            tags=["watch", "electronics"],
        )
        secondary = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-watch-secondary/-/A-3012",
            canonical_source_url="https://www.target.com/p/demo-watch-secondary/-/A-3012",
            title="Demo Watch Secondary",
            description="Secondary recommendation target",
            price=149.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/demo-watch-secondary.jpg",
            rating=4.1,
            review_count=18,
            tags=["watch", "wearable"],
        )
        product_ids = db_module.upsert_products(
            [primary, secondary],
            {
                primary.source_image_url: {
                    "local_image_key": "img-demo-watch-primary",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                secondary.source_image_url: {
                    "local_image_key": "img-demo-watch-secondary",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )
        auth = db_module.create_user("favorite-invalidate@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None

        with db_module.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO user_recommendations (user_id, product_id, score, reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user["id"], product_ids[1], 8.5, "Cached recommendation", db_module.now_iso()),
            )
            connection.commit()

        with patch.object(db_module, "refresh_user_recommendations") as refresh_mock:
            db_module.add_user_favorite(user["id"], product_ids[0])
        refresh_mock.assert_not_called()

        with db_module.get_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS count FROM user_recommendations WHERE user_id = ?", (user["id"],)).fetchone()["count"],
                0,
            )
            connection.execute(
                """
                INSERT INTO user_recommendations (user_id, product_id, score, reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user["id"], product_ids[1], 7.2, "Cached recommendation", db_module.now_iso()),
            )
            connection.commit()

        with patch.object(db_module, "refresh_user_recommendations") as refresh_mock:
            removed = db_module.remove_user_favorite(user["id"], product_ids[0])
        self.assertTrue(removed)
        refresh_mock.assert_not_called()

        with db_module.get_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS count FROM user_recommendations WHERE user_id = ?", (user["id"],)).fetchone()["count"],
                0,
            )

    def test_history_product_entries_include_snapshot(self):
        product = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-speaker/-/A-3002",
            canonical_source_url="https://www.target.com/p/demo-speaker/-/A-3002",
            title="Demo Speaker",
            description="Portable speaker for history snapshot testing",
            price=79.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Demo",
            source_image_url="https://images.example.com/demo-speaker.jpg",
            rating=4.2,
            review_count=18,
            tags=["speaker", "audio"],
        )
        product_id = db_module.upsert_products(
            [product],
            {
                product.source_image_url: {
                    "local_image_key": "img-demo-speaker",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )[0]
        auth = db_module.create_user("snapshot@example.com", "secret123")
        user = db_module.get_user_by_token(auth["token"])
        assert user is not None

        db_module.record_user_event(user["id"], "product_view", product_id=product_id)
        history = db_module.list_user_history(user["id"], page=1, page_size=10)

        self.assertEqual(history["items"][0]["productId"], product_id)
        self.assertEqual(history["items"][0]["canonicalSourceUrl"], product.canonical_source_url)
        self.assertEqual(history["items"][0]["productSnapshot"]["name"], "Demo Speaker")

    def test_related_products_prioritize_relevant_same_category_matches(self):
        anchor = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-dog-toy/-/A-7001",
            canonical_source_url="https://www.target.com/p/demo-dog-toy/-/A-7001",
            title="Dog Fetch Rope Toy",
            description="Durable rope toy for dogs to chew and fetch",
            price=12.99,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Paws",
            source_image_url="https://images.example.com/demo-dog-toy.jpg",
            rating=4.7,
            review_count=32,
            tags=["dog", "rope", "toy", "fetch"],
        )
        related_toy = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-fetch-toy/-/A-7002",
            canonical_source_url="https://www.target.com/p/demo-fetch-toy/-/A-7002",
            title="Dog Rope Fetch Toy",
            description="Rope toy for dog fetch games and chewing",
            price=10.99,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Paws",
            source_image_url="https://images.example.com/demo-fetch-toy.jpg",
            rating=4.5,
            review_count=21,
            tags=["rope", "toy", "fetch"],
        )
        unrelated_electronics = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-toy-camera/-/A-7003",
            canonical_source_url="https://www.target.com/p/demo-toy-camera/-/A-7003",
            title="Toy Camera Projector",
            description="Electronics projector with camera toy theme",
            price=39.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Spark",
            source_image_url="https://images.example.com/demo-toy-camera.jpg",
            rating=4.1,
            review_count=18,
            tags=["toy", "camera", "projector"],
        )
        product_ids = db_module.upsert_products(
            [anchor, related_toy, unrelated_electronics],
            {
                anchor.source_image_url: {
                    "local_image_key": "img-demo-dog-toy",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                related_toy.source_image_url: {
                    "local_image_key": "img-demo-fetch-toy",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                unrelated_electronics.source_image_url: {
                    "local_image_key": "img-demo-toy-camera",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        related = db_module.get_related_products(product_ids[0], page=1, page_size=10)

        assert related is not None
        self.assertEqual([item["id"] for item in related["items"]], [product_ids[1]])

    def test_related_products_for_others_require_real_family_overlap(self):
        anchor = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/portable-ping-pong-table/-/A-7101",
            canonical_source_url="https://www.target.com/p/portable-ping-pong-table/-/A-7101",
            title="Portable Ping Pong Table",
            description="Foldable table tennis table with paddles",
            price=133.99,
            currency="USD",
            category_id="others",
            category="Others",
            brand="Sporto",
            source_image_url="https://images.example.com/portable-ping-pong-table.jpg",
            rating=4.6,
            review_count=42,
            tags=["ping pong", "table tennis", "portable"],
        )
        related_table = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/blue-portable-table-tennis-table/-/A-7102",
            canonical_source_url="https://www.target.com/p/blue-portable-table-tennis-table/-/A-7102",
            title="Blue Portable Table Tennis Table",
            description="Tournament-style ping pong table for indoor games",
            price=342.82,
            currency="USD",
            category_id="others",
            category="Others",
            brand="Sporto",
            source_image_url="https://images.example.com/blue-table-tennis-table.jpg",
            rating=4.4,
            review_count=18,
            tags=["table tennis", "ping pong", "portable"],
        )
        unrelated_shoe = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/converse-kids-slip-on/-/A-7103",
            canonical_source_url="https://www.target.com/p/converse-kids-slip-on/-/A-7103",
            title="Converse Kids Slip-On Sneakers",
            description="Easy slip-on canvas shoes for kids",
            price=47.0,
            currency="USD",
            category_id="fashion",
            category="Fashion",
            brand="Converse",
            source_image_url="https://images.example.com/converse-kids-slip-on.jpg",
            rating=4.7,
            review_count=74,
            tags=["shoes", "sneakers", "kids"],
        )
        product_ids = db_module.upsert_products(
            [anchor, related_table, unrelated_shoe],
            {
                anchor.source_image_url: {
                    "local_image_key": "img-portable-ping-pong-table",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                related_table.source_image_url: {
                    "local_image_key": "img-blue-table-tennis-table",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                unrelated_shoe.source_image_url: {
                    "local_image_key": "img-converse-kids-slip-on",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        related = db_module.get_related_products(product_ids[0], page=1, page_size=10)

        assert related is not None
        self.assertEqual([item["id"] for item in related["items"]], [product_ids[1]])

    def test_stored_related_rows_are_revalidated_before_return(self):
        anchor = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/cap-neoprene-dumbbell-orange/-/A-7201",
            canonical_source_url="https://www.target.com/p/cap-neoprene-dumbbell-orange/-/A-7201",
            title="CAP Neoprene Dumbbell 3lbs - Orange",
            description="Neoprene dumbbell for strength training",
            price=14.99,
            currency="USD",
            category_id="sports",
            category="Sports",
            brand="Ace",
            source_image_url="https://images.example.com/cap-neoprene-dumbbell-orange.jpg",
            rating=4.8,
            review_count=64,
            tags=["cap", "dumbbell", "weights"],
        )
        related_ball = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/cap-neoprene-dumbbell-blue/-/A-7202",
            canonical_source_url="https://www.target.com/p/cap-neoprene-dumbbell-blue/-/A-7202",
            title="CAP Neoprene Dumbbell 5lbs - Blue",
            description="Neoprene dumbbell for strength training",
            price=19.99,
            currency="USD",
            category_id="sports",
            category="Sports",
            brand="Ace",
            source_image_url="https://images.example.com/cap-neoprene-dumbbell-blue.jpg",
            rating=4.3,
            review_count=28,
            tags=["cap", "dumbbell", "weights"],
        )
        unrelated_milk = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/whole-milk-gallon/-/A-7203",
            canonical_source_url="https://www.target.com/p/whole-milk-gallon/-/A-7203",
            title="Whole Milk Gallon",
            description="Fresh whole milk",
            price=4.99,
            currency="USD",
            category_id="food",
            category="Food",
            brand="Farm",
            source_image_url="https://images.example.com/whole-milk-gallon.jpg",
            rating=4.9,
            review_count=110,
            tags=["milk", "dairy", "grocery"],
        )
        product_ids = db_module.upsert_products(
            [anchor, related_ball, unrelated_milk],
            {
                anchor.source_image_url: {
                    "local_image_key": "img-cap-neoprene-dumbbell-orange",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                related_ball.source_image_url: {
                    "local_image_key": "img-cap-neoprene-dumbbell-blue",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                unrelated_milk.source_image_url: {
                    "local_image_key": "img-whole-milk-gallon",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        with db_module.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO related_products (product_id, related_product_id, score, reason)
                VALUES (?, ?, ?, ?)
                """,
                (product_ids[0], product_ids[2], 9.9, "Bad cached match"),
            )
            connection.execute(
                """
                INSERT INTO related_products (product_id, related_product_id, score, reason)
                VALUES (?, ?, ?, ?)
                """,
                (product_ids[0], product_ids[1], 8.1, "Good cached match"),
            )
            connection.commit()

        related = db_module.get_related_products(product_ids[0], page=1, page_size=10)

        assert related is not None
        self.assertEqual([item["id"] for item in related["items"]], [product_ids[1]])

    def test_search_driven_related_results_beat_cached_interest_graph_matches(self):
        anchor = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/rubiks-cube-classic/-/A-7301",
            canonical_source_url="https://www.target.com/p/rubiks-cube-classic/-/A-7301",
            title="Rubik's Puzzle Cube Toy",
            description="Classic Rubik cube puzzle toy for brain teaser fun",
            price=10.49,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Spin Master",
            source_image_url="https://images.example.com/rubiks-cube-classic.jpg",
            rating=4.8,
            review_count=120,
            tags=["rubik", "cube", "puzzle", "brain teaser"],
        )
        search_match = ProviderProduct(
            provider="Walmart",
            source_url="https://www.walmart.com/ip/rubiks-speed-cube/7302",
            canonical_source_url="https://www.walmart.com/ip/rubiks-speed-cube/7302",
            title="Rubik's Cube Brain Teaser Puzzle",
            description="Classic Rubik cube puzzle toy for brain teaser fun",
            price=14.99,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="Rubik's",
            source_image_url="https://images.example.com/rubiks-speed-cube.jpg",
            rating=4.6,
            review_count=58,
            tags=["rubik", "cube", "speed cube", "puzzle"],
        )
        cached_interest_graph_match = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/lego-batmobile/-/A-7303",
            canonical_source_url="https://www.target.com/p/lego-batmobile/-/A-7303",
            title="LEGO DC Batman Batmobile",
            description="Creative building toy for Batman fans",
            price=29.99,
            currency="USD",
            category_id="toys",
            category="Toys",
            brand="LEGO",
            source_image_url="https://images.example.com/lego-batmobile.jpg",
            rating=4.8,
            review_count=92,
            tags=["lego", "building toy", "batman"],
        )
        product_ids = db_module.upsert_products(
            [anchor, search_match, cached_interest_graph_match],
            {
                anchor.source_image_url: {
                    "local_image_key": "img-rubiks-cube-classic",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                search_match.source_image_url: {
                    "local_image_key": "img-rubiks-speed-cube",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                cached_interest_graph_match.source_image_url: {
                    "local_image_key": "img-lego-batmobile",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        with db_module.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO related_products (product_id, related_product_id, score, reason)
                VALUES (?, ?, ?, ?)
                """,
                (product_ids[0], product_ids[2], 9.7, "Interest graph toy match"),
            )
            connection.commit()

        related = db_module.get_related_products(product_ids[0], page=1, page_size=10)

        assert related is not None
        related_ids = [item["id"] for item in related["items"]]
        self.assertIn(product_ids[1], related_ids)
        self.assertEqual(related_ids[0], product_ids[1])

    def test_query_expansion_prefers_generated_titles(self):
        variants = expand_query_variants("iphone")
        self.assertGreater(len(variants), 1)
        self.assertEqual(variants[0], "apple iphone smartphone")
        self.assertIn("iphone", variants)

    def test_category_classification_covers_common_search_families(self):
        self.assertEqual(classify_category("microwave")["category_id"], "home")
        self.assertEqual(classify_category("microwave oven")["category_id"], "home")
        self.assertEqual(classify_category("clock")["category_id"], "home")
        self.assertEqual(classify_category("watch")["category_id"], "electronics")
        self.assertEqual(classify_category("protein bars")["category_id"], "food")
        self.assertEqual(classify_category("bed")["category_id"], "home")
        self.assertEqual(classify_category("mac")["category_id"], "electronics")

    def test_short_brand_query_does_not_match_substrings(self):
        microphone = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/microphone/-/A-2001",
            canonical_source_url="https://www.target.com/p/microphone/-/A-2001",
            title="USB Gaming Microphone for Mac",
            description="Streaming microphone for creators and PC gamers",
            price=129.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="HyperX",
            source_image_url="https://images.example.com/microphone.jpg",
            rating=4.1,
            review_count=14,
            tags=["microphone", "gaming", "mac"],
        )
        macbook = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/macbook/-/A-2002",
            canonical_source_url="https://www.target.com/p/macbook/-/A-2002",
            title="Apple MacBook Air Laptop",
            description="Apple laptop with M-series chip",
            price=999.99,
            currency="USD",
            category_id="electronics",
            category="Electronics",
            brand="Apple",
            source_image_url="https://images.example.com/macbook.jpg",
            rating=4.9,
            review_count=240,
            tags=["macbook", "laptop", "apple computer"],
        )
        db_module.upsert_products(
            [microphone, macbook],
            {
                microphone.source_image_url: {
                    "local_image_key": "img-microphone",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                macbook.source_image_url: {
                    "local_image_key": "img-macbook",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        results = db_module.search_cached_products("mac", page=1, page_size=10, category_id="electronics")

        self.assertGreaterEqual(len(results["items"]), 1)
        self.assertEqual(results["items"][0]["name"], "Apple MacBook Air Laptop")

    def test_rank_query_allows_strong_text_matches_from_others(self):
        dog_toy = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/dog-toy/-/A-3001",
            canonical_source_url="https://www.target.com/p/dog-toy/-/A-3001",
            title="Benebone Dog Chew Toy",
            description="Durable dog toy for aggressive chewers",
            price=19.99,
            currency="USD",
            category_id="others",
            category="Others",
            brand="Benebone",
            source_image_url="https://images.example.com/dog-toy.jpg",
            rating=4.8,
            review_count=120,
            tags=["dog", "toy", "pet"],
        )
        product_ids = db_module.upsert_products(
            [dog_toy],
            {
                dog_toy.source_image_url: {
                    "local_image_key": "img-dog-toy",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                }
            },
        )

        ranked_ids, exact_count, filtered_count = db_module.rank_product_ids_for_query(
            product_ids,
            "dog toy",
            category_id=None,
            strict_category=False,
        )

        self.assertEqual(filtered_count, 0)
        self.assertEqual(exact_count, 1)
        self.assertEqual(ranked_ids, product_ids)

    def test_rank_query_filters_unrelated_candidates_for_blender(self):
        blender = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-blender-core/-/A-8201",
            canonical_source_url="https://www.target.com/p/demo-blender-core/-/A-8201",
            title="Kitchen Blender",
            description="Countertop blender for smoothies and soups",
            price=79.99,
            currency="USD",
            category_id="home",
            category="Home",
            brand="BlendCo",
            source_image_url="https://images.example.com/demo-blender-core.jpg",
            rating=4.6,
            review_count=58,
            tags=["blender", "kitchen", "smoothie"],
        )
        tennis_ball = ProviderProduct(
            provider="Target",
            source_url="https://www.target.com/p/demo-tennis-ball/-/A-8202",
            canonical_source_url="https://www.target.com/p/demo-tennis-ball/-/A-8202",
            title="Championship Tennis Ball Can",
            description="Tennis balls for practice and tournament play",
            price=12.99,
            currency="USD",
            category_id="sports",
            category="Sports",
            brand="Ace",
            source_image_url="https://images.example.com/demo-tennis-ball.jpg",
            rating=4.8,
            review_count=64,
            tags=["tennis", "ball", "sports"],
        )
        product_ids = db_module.upsert_products(
            [blender, tennis_ball],
            {
                blender.source_image_url: {
                    "local_image_key": "img-demo-blender-core",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
                tennis_ball.source_image_url: {
                    "local_image_key": "img-demo-tennis-ball",
                    "image_mime": "image/jpeg",
                    "image_width": 640,
                    "image_height": 640,
                },
            },
        )

        ranked_ids, exact_count, filtered_count = db_module.rank_product_ids_for_query(product_ids, "blender")

        self.assertEqual(ranked_ids, [product_ids[0]])
        self.assertEqual(exact_count, 1)
        self.assertEqual(filtered_count, 1)


if __name__ == "__main__":
    unittest.main()
