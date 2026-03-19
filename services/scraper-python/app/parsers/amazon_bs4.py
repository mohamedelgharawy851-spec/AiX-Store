from __future__ import annotations

from bs4 import BeautifulSoup

from .common import build_product, build_review, pick_image_url, pick_image_urls, rating_from_text, review_count_from_text, soup_for
from ..providers.base import ProviderProduct
from ..utils import absolute_url, normalize_whitespace, parse_float, strip_html, tokenize

AMAZON_BASE_URL = "https://www.amazon.com"


def parse_search_results(html: str, query: str, category_id: str | None = None) -> list[ProviderProduct]:
    soup = soup_for(html)
    products: list[ProviderProduct] = []
    for card in soup.select("div.s-result-item[data-component-type='s-search-result'][data-asin]"):
        asin = normalize_whitespace(card.get("data-asin"))
        if not asin:
            continue
        title_node = card.select_one("h2 a.a-link-normal")
        title = normalize_whitespace(title_node.get_text(" ", strip=True) if title_node else "")
        source_url = absolute_url(AMAZON_BASE_URL, title_node.get("href") if title_node else "")
        image_gallery_urls = pick_image_urls(card.select_one("img.s-image"), AMAZON_BASE_URL)
        image_url = image_gallery_urls[0] if image_gallery_urls else ""
        price_text = normalize_whitespace(
            (card.select_one(".a-price .a-offscreen") or {}).get_text(" ", strip=True)
            if card.select_one(".a-price .a-offscreen")
            else ""
        )
        price = parse_float(price_text)
        rating = rating_from_text(card.select_one(".a-icon-alt").get_text(" ", strip=True) if card.select_one(".a-icon-alt") else "")
        review_count = review_count_from_text(
            card.select_one(".s-link-style .s-underline-text").get_text(" ", strip=True)
            if card.select_one(".s-link-style .s-underline-text")
            else ""
        )
        brand = title.split(" ")[0] if title else None
        product = build_product(
            provider="Amazon",
            source_url=source_url,
            title=title,
            description=title,
            price=price,
            currency="USD",
            category_id=category_id,
            brand=brand,
            source_image_url=image_url,
            image_gallery_urls=image_gallery_urls,
            rating=rating,
            review_count=review_count,
            tags=tokenize(title, query),
            raw_json={"asin": asin},
        )
        if product:
            products.append(product)
    return products


def parse_detail(html: str, product: dict) -> ProviderProduct | None:
    soup = soup_for(html)
    title = normalize_whitespace((soup.select_one("#productTitle") or soup.select_one("title")).get_text(" ", strip=True))
    image_gallery_urls = pick_image_urls(
        soup.select_one("#imgTagWrapperId img") or soup.select_one("#landingImage"),
        AMAZON_BASE_URL,
    )
    image_url = image_gallery_urls[0] if image_gallery_urls else pick_image_url(
        soup.select_one("#imgTagWrapperId img") or soup.select_one("#landingImage"),
        AMAZON_BASE_URL,
    )
    description_parts = [
        node.get_text(" ", strip=True)
        for node in soup.select("#feature-bullets li span.a-list-item, #productOverview_feature_div tr")
    ]
    description = normalize_whitespace(" ".join(part for part in description_parts if part))
    price = parse_float(
        normalize_whitespace(
            (soup.select_one("#corePriceDisplay_desktop_feature_div .a-offscreen")
             or soup.select_one(".a-price .a-offscreen")
             or {}).get_text(" ", strip=True)
            if soup.select_one("#corePriceDisplay_desktop_feature_div .a-offscreen") or soup.select_one(".a-price .a-offscreen")
            else ""
        )
    )
    original_price = parse_float(
        normalize_whitespace(
            (soup.select_one(".basisPrice .a-offscreen") or soup.select_one(".a-text-price .a-offscreen") or {}).get_text(" ", strip=True)
            if soup.select_one(".basisPrice .a-offscreen") or soup.select_one(".a-text-price .a-offscreen")
            else ""
        )
    )
    rating = rating_from_text(
        soup.select_one("#acrPopover .a-icon-alt").get_text(" ", strip=True)
        if soup.select_one("#acrPopover .a-icon-alt")
        else ""
    )
    review_count = review_count_from_text(
        soup.select_one("#acrCustomerReviewText").get_text(" ", strip=True)
        if soup.select_one("#acrCustomerReviewText")
        else ""
    )
    reviews = []
    for review_index, node in enumerate(soup.select("div[data-hook='review']")[:10]):
        review = build_review(
            review_id=f"amazon-{product['id']}-{review_index}",
            author_name=node.select_one("[data-hook='review-author']").get_text(" ", strip=True)
            if node.select_one("[data-hook='review-author']")
            else "",
            body=node.select_one("[data-hook='review-body']").get_text(" ", strip=True)
            if node.select_one("[data-hook='review-body']")
            else "",
            rating_text=node.select_one("[data-hook='review-star-rating']").get_text(" ", strip=True)
            if node.select_one("[data-hook='review-star-rating']")
            else "",
            published_at=node.select_one("[data-hook='review-date']").get_text(" ", strip=True)
            if node.select_one("[data-hook='review-date']")
            else "",
        )
        if review:
            reviews.append(review)

    return build_product(
        provider="Amazon",
        source_url=product["sourceUrl"],
        title=title or product["name"],
        description=description or product["description"] or title,
        price=price or product["price"],
        currency=product.get("currency") or "USD",
        category_id=product.get("categoryId"),
        brand=product.get("brand"),
        source_image_url=image_url or product["sourceImageUrl"],
        image_gallery_urls=image_gallery_urls or [product.get("sourceImageUrl", "")],
        original_price=original_price,
        rating=rating or product.get("rating", 0),
        review_count=review_count or product.get("reviewCount", 0),
        tags=tokenize(title, description, product.get("category")),
        raw_json={"source": "detail"},
        reviews=reviews,
    )
