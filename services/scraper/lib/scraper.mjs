import { execFile as execFileCallback } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { CATEGORY_CONFIG, SOURCE_CONFIG } from "./config.mjs";
import {
  canonicalizeProductUrl,
  hashId,
  normalizeWhitespace,
  nowIso,
  parseHostFromUrl,
  pickFirst,
  safeUrl,
  slugify,
  stripHtml,
  toArray,
  toNumber,
  tokenize,
  uniqueBy,
} from "./utils.mjs";

const execFile = promisify(execFileCallback);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PYTHON_FETCH_SCRIPT = path.resolve(__dirname, "../scripts/fetch_url.py");
const PYTHON_FETCH_TIMEOUT_MS = 30_000;
const WALMART_CATEGORY_BROWSE_URLS = {
  electronics: "https://www.walmart.com/browse/electronics/3944",
  fashion: "https://www.walmart.com/browse/clothing/5438",
  beauty: "https://www.walmart.com/browse/beauty/1085666",
  home: "https://www.walmart.com/browse/home/4044",
  toys: "https://www.walmart.com/browse/toys/4171_14521",
  sports: "https://www.walmart.com/browse/sports-outdoors/4125",
  others: "https://www.walmart.com/browse/home/4044",
};
const WALMART_QUERY_BROWSE_HINTS = [
  {
    keywords: ["tv", "television", "oled", "qled"],
    url: "https://www.walmart.com/browse/electronics/all-tvs/3944_1060825_447913",
  },
  {
    keywords: ["headphone", "headset", "earbud", "airpod"],
    url: "https://www.walmart.com/browse/electronics/headphones/3944_133251_1095191",
  },
  {
    keywords: ["lego", "building blocks", "plush", "doll", "toy"],
    url: "https://www.walmart.com/browse/toys/4171_14521",
  },
  {
    keywords: ["hoodie", "jacket", "dress", "sneaker", "shoe", "fashion"],
    url: "https://www.walmart.com/browse/clothing/5438",
  },
  {
    keywords: ["lamp", "chair", "desk", "air fryer", "coffee maker", "home", "kitchen"],
    url: "https://www.walmart.com/browse/home/4044",
  },
  {
    keywords: ["golf", "yoga", "dumbbell", "fitness", "sports"],
    url: "https://www.walmart.com/browse/sports-outdoors/4125",
  },
];
const SEARCH_HEADERS = {
  "user-agent": "Mozilla/5.0",
  "accept-language": "en-US,en;q=0.9",
};

function inferCategoryId(query = "") {
  const normalizedQuery = query.toLowerCase();
  for (const [categoryId, config] of Object.entries(CATEGORY_CONFIG)) {
    if (config.keywords.some((keyword) => normalizedQuery.includes(keyword))) {
      return categoryId;
    }
  }
  return "others";
}

function getCategoryName(categoryId) {
  return CATEGORY_CONFIG[categoryId]?.name || CATEGORY_CONFIG.others.name;
}

function inferSourceSite(url) {
  const host = parseHostFromUrl(url);
  return SOURCE_CONFIG.find((source) => source.domains.some((domain) => host.endsWith(domain)))?.siteName || host;
}

function queryScore(query, product, categoryIdHint) {
  const queryTokens = tokenize(query);
  if (!queryTokens.length) {
    return 0;
  }

  const normalizedQuery = normalizeWhitespace(query).toLowerCase();
  const nameTokens = new Set(tokenize(product.name, product.imageAltText));
  const metaTokens = new Set(tokenize(product.category, product.sourceSite));
  const descriptionTokens = new Set(tokenize(product.description));
  let score = 0;

  if (normalizedQuery.length > 2 && product.name.toLowerCase().includes(normalizedQuery)) {
    score += 20;
  }

  for (const token of queryTokens) {
    if (nameTokens.has(token)) {
      score += token.length <= 2 ? 18 : 12;
      continue;
    }

    if (metaTokens.has(token)) {
      score += 7;
      continue;
    }

    if (token.length > 2 && descriptionTokens.has(token)) {
      score += 3;
    }
  }

  if (categoryIdHint && product.categoryId === categoryIdHint) {
    score += 15;
  }

  return score + (product.rating || 0) + Math.min((product.reviewCount || 0) / 100, 5);
}

function buildFallbackQueries(query, categoryId) {
  const queryTokens = tokenize(query);
  const fallbackQueries = [];

  for (const hint of WALMART_QUERY_BROWSE_HINTS) {
    if (hint.keywords.some((keyword) => query.toLowerCase().includes(keyword))) {
      fallbackQueries.push(...hint.keywords.slice(0, 2));
    }
  }

  if (categoryId && CATEGORY_CONFIG[categoryId]) {
    fallbackQueries.push(...CATEGORY_CONFIG[categoryId].seedQueries);
    fallbackQueries.push(...CATEGORY_CONFIG[categoryId].keywords);
  }

  fallbackQueries.push(...queryTokens);
  return uniqueBy(
    fallbackQueries
      .map((value) => normalizeWhitespace(value))
      .filter((value) => value && value.toLowerCase() !== query.toLowerCase()),
    (value) => value.toLowerCase(),
  );
}

async function fetchHtml(url) {
  const response = await fetch(url, { headers: SEARCH_HEADERS, redirect: "follow" });
  if (!response.ok) {
    throw new Error(`Request failed for ${url}: ${response.status}`);
  }
  return response.text();
}

async function fetchHtmlViaPython(url) {
  const { stdout } = await execFile("python3", [PYTHON_FETCH_SCRIPT, url], {
    maxBuffer: 10 * 1024 * 1024,
    timeout: PYTHON_FETCH_TIMEOUT_MS,
  });
  return stdout;
}

async function fetchWalmartHtml(url) {
  try {
    return await fetchHtmlViaPython(url);
  } catch {
    return fetchHtml(url);
  }
}

function tryParseJson(value) {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function collectProductNodes(input, nodes = []) {
  if (!input) {
    return nodes;
  }

  if (Array.isArray(input)) {
    for (const item of input) {
      collectProductNodes(item, nodes);
    }
    return nodes;
  }

  if (typeof input !== "object") {
    return nodes;
  }

  const typeList = toArray(input["@type"]).map((item) => String(item).toLowerCase());
  if (typeList.includes("product")) {
    nodes.push(input);
  }

  collectProductNodes(input["@graph"], nodes);
  collectProductNodes(input.mainEntity, nodes);
  collectProductNodes(input.itemListElement, nodes);
  return nodes;
}

function extractJsonLdProducts(html) {
  const products = [];
  const pattern = /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  for (const match of html.matchAll(pattern)) {
    const payload = tryParseJson(match[1].trim());
    if (!payload) {
      continue;
    }
    products.push(...collectProductNodes(payload));
  }
  return products;
}

function extractNextData(html) {
  const match = html.match(/<script[^>]*id=["']__NEXT_DATA__["'][^>]*>([\s\S]*?)<\/script>/i);
  return match ? tryParseJson(match[1].trim()) : null;
}

function extractMetaContent(html, key) {
  const patterns = [
    new RegExp(`<meta[^>]+property=["']${key}["'][^>]+content=["']([^"']+)["'][^>]*>`, "i"),
    new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]+property=["']${key}["'][^>]*>`, "i"),
    new RegExp(`<meta[^>]+name=["']${key}["'][^>]+content=["']([^"']+)["'][^>]*>`, "i"),
    new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]+name=["']${key}["'][^>]*>`, "i"),
  ];

  for (const pattern of patterns) {
    const match = html.match(pattern);
    if (match?.[1]) {
      return decodeHtmlEntities(match[1]);
    }
  }
  return "";
}

function extractOriginalPrice(html) {
  const patterns = [
    /"listPrice"\s*:\s*\{[^}]*"price"\s*:\s*"?(?<price>\d+(?:\.\d+)?)"?/i,
    /"originalPrice"\s*:\s*"?(?<price>\d+(?:\.\d+)?)"?/i,
    /"priceBeforeDiscount"\s*:\s*"?(?<price>\d+(?:\.\d+)?)"?/i,
    /"wasPrice"\s*:\s*"?(?<price>\d+(?:\.\d+)?)"?/i,
    /"strikeThroughPrice"\s*:\s*"?(?<price>\d+(?:\.\d+)?)"?/i,
  ];

  for (const pattern of patterns) {
    const match = html.match(pattern);
    const parsed = toNumber(match?.groups?.price);
    if (parsed) {
      return parsed;
    }
  }
  return null;
}

function normalizeOffer(offers) {
  const offerList = toArray(offers).filter(Boolean);
  for (const offer of offerList) {
    const price = toNumber(offer?.price ?? offer?.lowPrice);
    if (price) {
      return {
        price,
        currency: offer?.priceCurrency || "USD",
        originalPrice:
          toNumber(offer?.priceSpecification?.price) ||
          toNumber(offer?.highPrice) ||
          toNumber(offer?.priceBeforeDiscount) ||
          null,
      };
    }
  }
  return { price: null, currency: "USD", originalPrice: null };
}

function normalizeReviews(review) {
  return toArray(review)
    .map((item, index) => {
      const authorName =
        normalizeWhitespace(item?.author?.name || item?.author || item?.name || "") || "Verified customer";
      const body = normalizeWhitespace(item?.reviewBody || item?.description || "");
      if (!body) {
        return null;
      }

      return {
        id: `${index}-${slugify(authorName) || "review"}`,
        authorName,
        rating: toNumber(item?.reviewRating?.ratingValue || item?.reviewRating || item?.ratingValue) || 0,
        body,
        publishedAt: item?.datePublished || null,
      };
    })
    .filter(Boolean);
}

function toProductFromStructuredData(structuredProduct, url, query, categoryIdHint) {
  const offer = normalizeOffer(structuredProduct.offers);
  const name = normalizeWhitespace(structuredProduct.name);
  const image = pickFirst(
    toArray(structuredProduct.image).map((item) => safeUrl(typeof item === "string" ? item : item?.url)),
  );
  const description = normalizeWhitespace(structuredProduct.description);
  const rating = toNumber(
    structuredProduct.aggregateRating?.ratingValue ||
      structuredProduct.aggregateRating?.ratingValue?.["@value"],
  );
  const reviewCount =
    toNumber(structuredProduct.aggregateRating?.reviewCount) ||
    toNumber(structuredProduct.aggregateRating?.ratingCount) ||
    normalizeReviews(structuredProduct.review).length;

  if (!name || !image || !offer.price) {
    return null;
  }

  const categoryId = categoryIdHint || inferCategoryId(`${query} ${structuredProduct.category || ""}`);
  const reviews = normalizeReviews(structuredProduct.review);

  return {
    id: hashId(url),
    slug: slugify(`${name}-${hashId(url)}`),
    name,
    categoryId,
    category: getCategoryName(categoryId),
    description,
    price: offer.price,
    originalPrice: offer.originalPrice,
    currency: offer.currency || "USD",
    rating: rating || 0,
    imageUrl: image,
    imageAltText: name,
    reviewCount,
    tags: tokenize(name, description, structuredProduct.brand?.name || structuredProduct.brand || ""),
    sourceSite: inferSourceSite(url),
    sourceUrl: url,
    reviews,
    createdAt: nowIso(),
  };
}

function buildFallbackProduct(url, html, query, categoryIdHint) {
  const name = normalizeWhitespace(
    pickFirst([
      extractMetaContent(html, "og:title"),
      html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ? stripHtml(html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)[1]) : "",
    ]),
  );
  const imageUrl = safeUrl(extractMetaContent(html, "og:image"));
  const description = normalizeWhitespace(
    pickFirst([extractMetaContent(html, "description"), extractMetaContent(html, "og:description")]),
  );
  const price =
    toNumber(extractMetaContent(html, "product:price:amount")) ||
    toNumber(html.match(/"price"\s*:\s*"?(?<price>\d+(?:\.\d+)?)"?/i)?.groups?.price);

  if (!name || !imageUrl || !price) {
    return null;
  }

  const categoryId = categoryIdHint || inferCategoryId(query);
  return {
    id: hashId(url),
    slug: slugify(`${name}-${hashId(url)}`),
    name,
    categoryId,
    category: getCategoryName(categoryId),
    description,
    price,
    originalPrice: extractOriginalPrice(html),
    currency: extractMetaContent(html, "product:price:currency") || "USD",
    rating: toNumber(html.match(/"ratingValue"\s*:\s*"?(?<rating>\d+(?:\.\d+)?)"?/i)?.groups?.rating) || 0,
    imageUrl,
    imageAltText: name,
    reviewCount: toNumber(html.match(/"reviewCount"\s*:\s*"?(?<count>\d+)"?/i)?.groups?.count) || 0,
    tags: tokenize(name, description),
    sourceSite: inferSourceSite(url),
    sourceUrl: url,
    reviews: [],
    createdAt: nowIso(),
  };
}

async function scrapeProductPage(url, query, categoryIdHint) {
  try {
    const html = await fetchHtml(url);
    const structuredProducts = extractJsonLdProducts(html);
    for (const structuredProduct of structuredProducts) {
      const product = toProductFromStructuredData(structuredProduct, url, query, categoryIdHint);
      if (product) {
        if (!product.originalPrice) {
          product.originalPrice = extractOriginalPrice(html);
        }
        if (product.originalPrice && product.originalPrice <= product.price) {
          product.originalPrice = null;
        }
        return product;
      }
    }

    return buildFallbackProduct(url, html, query, categoryIdHint);
  } catch {
    return null;
  }
}

function normalizeWalmartImage(imageUrl = "") {
  if (!imageUrl) {
    return "";
  }

  const [baseUrl] = imageUrl.split("?");
  return `${baseUrl}?odnHeight=600&odnWidth=600&odnBg=FFFFFF`;
}

function pickWalmartImage(imageInfo) {
  return normalizeWalmartImage(
    imageInfo?.thumbnailUrl ||
      imageInfo?.allImages?.[0]?.url ||
      imageInfo?.allImages?.[0]?.imageUrl ||
      imageInfo?.allImages?.[0],
  );
}

function toProductFromWalmartSearchItem(item, query, categoryIdHint) {
  const url = canonicalizeProductUrl(`https://www.walmart.com${item.canonicalUrl || ""}`);
  const name = normalizeWhitespace(item.name);
  const imageUrl = pickWalmartImage(item.imageInfo);
  const linePrice = toNumber(item.priceInfo?.linePrice);
  const currentPrice = toNumber(item.priceInfo?.currentPrice?.price);
  const itemPrice = toNumber(item.priceInfo?.itemPrice);
  const price = currentPrice || linePrice || itemPrice;

  if (!url || !name || !imageUrl || !price) {
    return null;
  }

  const categoryId = categoryIdHint || inferCategoryId(`${query} ${item.catalogProductType || ""}`);
  const description = normalizeWhitespace(stripHtml(item.shortDescription || item.catalogProductType || name));
  const originalPrice =
    toNumber(item.priceInfo?.wasPrice || item.priceInfo?.strikeThroughPrice) ||
    (itemPrice && linePrice && itemPrice > linePrice ? itemPrice : null);
  const sellerName = normalizeWhitespace(item.brand || item.manufacturerName || item.sellerName || "Walmart");

  return {
    id: hashId(url),
    slug: slugify(`${name}-${hashId(url)}`),
    name,
    categoryId,
    category: getCategoryName(categoryId),
    description,
    price,
    originalPrice: originalPrice && originalPrice > price ? originalPrice : null,
    currency: "USD",
    rating: toNumber(item.averageRating) || 0,
    imageUrl,
    imageAltText: name,
    reviewCount: toNumber(item.numberOfReviews) || 0,
    tags: tokenize(name, description, item.catalogProductType || "", sellerName),
    sourceSite: "Walmart",
    sourceUrl: url,
    reviews: [],
    createdAt: nowIso(),
  };
}

function extractWalmartItemsFromHtml(html) {
  const payload = extractNextData(html);
  return payload?.props?.pageProps?.initialData?.searchResult?.itemStacks?.[0]?.items || [];
}

async function browseWalmartCategory(categoryId, limit) {
  const browseUrl = WALMART_CATEGORY_BROWSE_URLS[categoryId];
  if (!browseUrl) {
    return [];
  }

  try {
    const html = await fetchWalmartHtml(browseUrl);
    const items = extractWalmartItemsFromHtml(html);
    return uniqueBy(
      items
        .map((item) => toProductFromWalmartSearchItem(item, CATEGORY_CONFIG[categoryId]?.name || categoryId, categoryId))
        .filter((item) => Boolean(item)),
      (item) => item.sourceUrl || item.id,
    ).slice(0, limit);
  } catch {
    return [];
  }
}

async function searchWalmart(query, { categoryId, limit }) {
  try {
    const searchUrl = `https://www.walmart.com/search?q=${encodeURIComponent(query)}`;
    const html = await fetchWalmartHtml(searchUrl);
    const items = extractWalmartItemsFromHtml(html);

    const products = uniqueBy(
      items
        .map((item) => toProductFromWalmartSearchItem(item, query, categoryId))
        .filter((item) => Boolean(item)),
      (item) => item.sourceUrl || item.id,
    );

    if (products.length) {
      return products.slice(0, limit);
    }
  } catch {
    // Fall through to browse-page fallback when direct search is challenged.
  }

  try {
    const browseUrls = uniqueBy(
      [
        ...WALMART_QUERY_BROWSE_HINTS.filter((entry) =>
          entry.keywords.some((keyword) => query.toLowerCase().includes(keyword)),
        ).map((entry) => entry.url),
        WALMART_CATEGORY_BROWSE_URLS[categoryId || inferCategoryId(query)],
      ].filter(Boolean),
      (url) => url,
    );

    const products = [];
    for (const url of browseUrls) {
      const html = await fetchWalmartHtml(url);
      const items = extractWalmartItemsFromHtml(html);
      products.push(
        ...items
          .map((item) => toProductFromWalmartSearchItem(item, query, categoryId))
          .filter((item) => Boolean(item)),
      );
    }

    return uniqueBy(products, (item) => item.sourceUrl || item.id)
      .map((product) => ({ product, score: queryScore(query, product, categoryId || inferCategoryId(query)) }))
      .filter((entry) => entry.score > 0)
      .sort((left, right) => right.score - left.score)
      .map((entry) => entry.product)
      .slice(0, limit);
  } catch {
    return [];
  }
}

function extractWalmartItemId(url) {
  try {
    const segments = new URL(url).pathname.split("/").filter(Boolean);
    const numericSegment = [...segments].reverse().find((segment) => /^\d+$/.test(segment));
    return numericSegment || "";
  } catch {
    return "";
  }
}

function normalizeWalmartReviewItem(review, index) {
  const authorName = normalizeWhitespace(review?.userNickname || review?.author || "Verified customer");
  const body = normalizeWhitespace(review?.reviewText || "");
  if (!body) {
    return null;
  }

  return {
    id: review?.reviewId ? String(review.reviewId) : `${index}-${slugify(authorName) || "review"}`,
    authorName,
    rating: toNumber(review?.rating) || 0,
    body,
    publishedAt: normalizeWhitespace(review?.reviewSubmissionTime || "") || null,
  };
}

async function scrapeWalmartReviewsPage(product) {
  const itemId = extractWalmartItemId(product?.sourceUrl || "");
  if (!itemId) {
    return null;
  }

  try {
    const html = await fetchWalmartHtml(`https://www.walmart.com/reviews/product/${itemId}`);
    const payload = extractNextData(html);
    const data = payload?.props?.pageProps?.initialData?.data;
    const productData = data?.product;
    const reviewsData = data?.reviews;

    if (!productData && !reviewsData) {
      return null;
    }

    const categoryTrail = toArray(productData?.category?.path)
      .map((node) => normalizeWhitespace(node?.name))
      .filter(Boolean);
    const summary = normalizeWhitespace(
      reviewsData?.reviewSummary?.summary || reviewsData?.topPositiveReview?.reviewText || "",
    );
    const rating =
      toNumber(reviewsData?.roundedAverageOverallRating) ||
      toNumber(reviewsData?.averageOverallRating) ||
      product.rating ||
      0;
    const reviewCount =
      toNumber(reviewsData?.totalReviewCount) || toNumber(reviewsData?.filteredReviewsCount) || product.reviewCount || 0;
    const reviews = toArray(reviewsData?.customerReviews)
      .map((review, index) => normalizeWalmartReviewItem(review, index))
      .filter(Boolean)
      .slice(0, 10);
    const derivedCategoryId =
      product.categoryId ||
      inferCategoryId(`${productData?.type || ""} ${categoryTrail.join(" ")} ${product.name || productData?.name || ""}`);
    const price =
      toNumber(productData?.priceInfo?.currentPrice?.price) ||
      toNumber(productData?.priceInfo?.currentPrice?.priceString) ||
      product.price;
    const originalPrice =
      toNumber(productData?.priceInfo?.wasPrice?.price) ||
      toNumber(productData?.priceInfo?.wasPrice?.priceString) ||
      product.originalPrice ||
      null;

    return {
      ...product,
      name: normalizeWhitespace(productData?.name || product.name),
      categoryId: derivedCategoryId,
      category: getCategoryName(derivedCategoryId),
      description: summary || product.description,
      price,
      originalPrice: originalPrice && originalPrice > price ? originalPrice : null,
      rating,
      imageUrl: pickWalmartImage(productData?.imageInfo) || product.imageUrl,
      imageAltText: normalizeWhitespace(productData?.name || product.imageAltText || product.name),
      reviewCount,
      sourceUrl: canonicalizeProductUrl(`https://www.walmart.com${productData?.canonicalUrl || ""}`) || product.sourceUrl,
      reviews,
      tags: uniqueBy(
        [
          ...(product.tags || []),
          ...tokenize(
            productData?.type || "",
            productData?.sellerName || "",
            summary,
            ...categoryTrail,
            ...toArray(reviewsData?.aspects).map((aspect) => aspect?.name || ""),
          ),
        ],
        (item) => item,
      ),
    };
  } catch {
    return null;
  }
}

export async function scrapeProductsForQuery(query, { categoryId, limit = 10 } = {}) {
  const resolvedCategoryId = categoryId || inferCategoryId(query);
  const products = await searchWalmart(query, { categoryId: resolvedCategoryId, limit });
  if (products.length >= limit) {
    return products.slice(0, limit);
  }

  const fallbackQueries = buildFallbackQueries(query, resolvedCategoryId);
  for (const fallbackQuery of fallbackQueries) {
    if (products.length >= limit) {
      break;
    }

    const fallbackProducts = await searchWalmart(fallbackQuery, {
      categoryId: resolvedCategoryId,
      limit: Math.max(limit, 10),
    });
    products.push(...fallbackProducts);
  }

  return uniqueBy(products, (item) => item.sourceUrl || item.id)
    .map((product) => ({ product, score: queryScore(query, product, resolvedCategoryId) }))
    .filter((entry) => entry.score > 0)
    .sort((left, right) => right.score - left.score)
    .map((entry) => entry.product)
    .slice(0, limit);
}

export async function bootstrapProducts(targetCount = 100) {
  const products = [];
  const categoryIds = Object.keys(CATEGORY_CONFIG);
  const minimumPerCategory = Math.max(8, Math.floor(targetCount / Math.max(categoryIds.length, 1)));

  for (const categoryId of categoryIds) {
    const needed = targetCount - products.length;
    if (needed <= 0) {
      break;
    }

    const scraped = await browseWalmartCategory(categoryId, Math.min(minimumPerCategory, needed));
    products.push(...scraped);
  }

  const queries = Object.entries(CATEGORY_CONFIG).flatMap(([categoryId, config]) =>
    config.seedQueries.map((query) => ({ query, categoryId })),
  );

  for (const entry of queries) {
    const needed = targetCount - products.length;
    if (needed <= 0) {
      break;
    }

    const scraped = await scrapeProductsForQuery(entry.query, {
      categoryId: entry.categoryId,
      limit: Math.min(6, needed),
    });
    products.push(...scraped);
  }

  return uniqueBy(products, (item) => item.sourceUrl || item.id).slice(0, targetCount);
}

export async function enrichProductFromSource(product) {
  if (!product?.sourceUrl) {
    return product;
  }

  if (product.sourceSite === "Walmart" || parseHostFromUrl(product.sourceUrl).endsWith("walmart.com")) {
    const enriched = await scrapeWalmartReviewsPage(product);
    if (enriched) {
      return enriched;
    }
  }

  const enriched = await scrapeProductPage(product.sourceUrl, product.name, product.categoryId);
  if (!enriched) {
    return product;
  }

  return {
    ...product,
    description: enriched.description || product.description,
    imageUrl: enriched.imageUrl || product.imageUrl,
    originalPrice: enriched.originalPrice ?? product.originalPrice ?? null,
    rating: enriched.rating || product.rating,
    reviewCount: enriched.reviewCount || product.reviewCount,
    reviews: enriched.reviews?.length ? enriched.reviews : product.reviews || [],
    tags: uniqueBy([...(product.tags || []), ...(enriched.tags || [])], (item) => item),
  };
}
