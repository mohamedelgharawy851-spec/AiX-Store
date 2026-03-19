from __future__ import annotations

import unittest
from pathlib import Path

from app.parsers.amazon_bs4 import parse_detail, parse_search_results
from app.providers.target_requests import _parse_products
from app.providers.walmart_requests import _parse_search


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_amazon_search_parser_extracts_product(self):
        html = (FIXTURES_DIR / "amazon_search.html").read_text("utf-8")
        products = parse_search_results(html, query="headphones", category_id="electronics")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].title, "Demo Headphones")
        self.assertEqual(products[0].source_image_url, "https://images.example.com/demo-headphones.jpg")

    def test_walmart_search_parser_extracts_product(self):
        html = (FIXTURES_DIR / "walmart_search.html").read_text("utf-8")
        products = _parse_search(html, query="tv", category_id="electronics")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].title, "Demo Walmart TV")
        self.assertEqual(products[0].review_count, 88)

    def test_target_search_parser_extracts_product(self):
        html = (FIXTURES_DIR / "target_search.html").read_text("utf-8")
        products = _parse_products(html, query="lamp", category_id="home")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].title, "Demo Target Lamp")
        self.assertEqual(products[0].source_image_url, "https://images.example.com/demo-target-lamp.jpg")
        self.assertEqual(products[0].review_count, 21)
        self.assertEqual(products[0].original_price, 49.99)

    def test_amazon_detail_parser_collects_dynamic_image_gallery(self):
        html = """
        <html>
          <body>
            <span id="productTitle">Demo Travel Backpack</span>
            <img
              id="landingImage"
              data-a-dynamic-image='{"https://images.example.com/backpack-1.jpg":[1200,1200],"https://images.example.com/backpack-2.jpg":[1200,1200]}'
            />
            <div id="corePriceDisplay_desktop_feature_div">
              <span class="a-offscreen">$59.99</span>
            </div>
            <span id="acrCustomerReviewText">18 ratings</span>
          </body>
        </html>
        """
        product = parse_detail(
            html,
            {
                "id": "demo-backpack",
                "sourceUrl": "https://www.amazon.com/dp/DEMO1234",
                "name": "Demo Travel Backpack",
                "description": "A travel backpack",
                "price": 59.99,
                "currency": "USD",
                "categoryId": "fashion",
                "category": "Fashion",
                "brand": "Demo",
                "sourceImageUrl": "https://images.example.com/backpack-1.jpg",
                "reviewCount": 18,
            },
        )
        self.assertIsNotNone(product)
        self.assertEqual(product.source_image_url, "https://images.example.com/backpack-1.jpg")
        self.assertGreaterEqual(len(product.image_gallery_urls), 2)


if __name__ == "__main__":
    unittest.main()
