import { CATEGORY_CONFIG, DEFAULT_SEARCH_LIMIT } from "./config.mjs";
import { bootstrapProducts, enrichProductFromSource, scrapeProductsForQuery } from "./scraper.mjs";
import { loadCatalogStore, mergeProducts, replaceCatalogStore } from "./store.mjs";
import { normalizeWhitespace, nowIso, tokenize } from "./utils.mjs";

const CORE_CATEGORY_IDS = ["electronics", "fashion", "beauty", "home", "toys"];

function inferCategoryId(query = "") {
  const normalizedQuery = query.toLowerCase();
  for (const [categoryId, config] of Object.entries(CATEGORY_CONFIG)) {
    if (config.keywords.some((keyword) => normalizedQuery.includes(keyword))) {
      return categoryId;
    }
  }
  return null;
}

function productSearchScore(query, product, categoryIdHint = null) {
  const queryTokens = tokenize(query);
  if (!queryTokens.length) {
    return 0;
  }

  const normalizedQuery = normalizeWhitespace(query).toLowerCase();
  const nameTokens = new Set(tokenize(product.name, product.imageAltText));
  const metaTokens = new Set(tokenize(product.category, product.sourceSite));
  const descriptionTokens = new Set(tokenize(product.description));
  let overlap = 0;

  if (normalizedQuery.length > 2 && product.name.toLowerCase().includes(normalizedQuery)) {
    overlap += 20;
  }

  for (const token of queryTokens) {
    if (nameTokens.has(token)) {
      overlap += token.length <= 2 ? 18 : 12;
      continue;
    }

    if (metaTokens.has(token)) {
      overlap += 7;
      continue;
    }

    if (token.length > 2 && descriptionTokens.has(token)) {
      overlap += 3;
    }
  }

  if (!overlap) {
    return 0;
  }

  if (categoryIdHint && product.categoryId === categoryIdHint) {
    overlap += 15;
  }

  return overlap + (product.rating || 0) + Math.min((product.reviewCount || 0) / 100, 5);
}

function relatedScore(source, candidate) {
  const sourceTokens = new Set(tokenize(source.name, source.category, ...(source.tags || [])));
  const candidateTokens = new Set(tokenize(candidate.name, candidate.category, ...(candidate.tags || [])));
  let overlap = 0;
  for (const token of sourceTokens) {
    if (candidateTokens.has(token)) {
      overlap += 1;
    }
  }
  if (source.categoryId === candidate.categoryId) {
    overlap += 2;
  }
  return overlap + (candidate.rating || 0);
}

function nearestScore(query, product, categoryIdHint) {
  let score = productSearchScore(query, product, categoryIdHint);
  if (categoryIdHint && product.categoryId === categoryIdHint) {
    score += 10;
  }
  score += Math.min((product.reviewCount || 0) / 50, 6);
  if ((product.originalPrice || 0) > product.price) {
    score += 2;
  }
  return score;
}

function interleaveProducts(products, limit) {
  const queues = Array.from(
    products.reduce((accumulator, product) => {
      const existing = accumulator.get(product.categoryId) || [];
      existing.push(product);
      accumulator.set(product.categoryId, existing);
      return accumulator;
    }, new Map()).values(),
  );
  const picked = [];

  while (picked.length < limit && queues.some((queue) => queue.length > 0)) {
    for (const queue of queues) {
      const nextProduct = queue.shift();
      if (nextProduct) {
        picked.push(nextProduct);
      }
      if (picked.length >= limit) {
        break;
      }
    }
  }

  return picked;
}

function getCategories(products) {
  return Object.entries(CATEGORY_CONFIG)
    .map(([categoryId, config]) => ({
      id: categoryId,
      name: config.name,
      shortLabel: config.name.slice(0, 2).toUpperCase(),
      color: config.color,
      icon: config.icon,
      count: products.filter((product) => product.categoryId === categoryId).length,
    }))
    .filter((category) => category.count > 0);
}

function getOffers(products, limit = 5) {
  return products
    .filter((product) => product.originalPrice && product.originalPrice > product.price)
    .sort((left, right) => {
      const leftDiscount = (left.originalPrice || left.price) - left.price;
      const rightDiscount = (right.originalPrice || right.price) - right.price;
      return rightDiscount - leftDiscount || right.rating - left.rating;
    })
    .slice(0, limit);
}

function filterProducts(products, { categoryId, limit } = {}) {
  const filtered = categoryId ? products.filter((product) => product.categoryId === categoryId) : products;
  return typeof limit === "number" ? filtered.slice(0, limit) : filtered;
}

function hasBootstrapCoverage(products) {
  return CORE_CATEGORY_IDS.every((categoryId) => products.some((product) => product.categoryId === categoryId));
}

export async function ensureBootstrap(targetCount = 100) {
  const store = await loadCatalogStore();
  if (store.products.length >= targetCount && hasBootstrapCoverage(store.products)) {
    return buildCatalogPayload(store.products);
  }

  const scrapedProducts = await bootstrapProducts(Math.max(targetCount, 120));
  const nextStore =
    store.products.length === 0 ? await replaceCatalogStore(scrapedProducts) : await mergeProducts(scrapedProducts);

  return buildCatalogPayload(nextStore.products);
}

export async function listCatalog({ categoryId, limit = 100 } = {}) {
  const store = await loadCatalogStore();
  const filteredProducts = filterProducts(store.products, { categoryId });
  const orderedProducts = categoryId ? filteredProducts : interleaveProducts(filteredProducts, filteredProducts.length);

  return {
    ...buildCatalogPayload(typeof limit === "number" ? orderedProducts.slice(0, limit) : orderedProducts),
    total: store.products.length,
  };
}

export async function searchCatalog(query, { categoryId, limit = DEFAULT_SEARCH_LIMIT } = {}) {
  const store = await loadCatalogStore();
  const categoryIdHint = categoryId || inferCategoryId(query);
  const initialMatches = filterProducts(store.products, { categoryId })
    .map((product) => ({ product, score: productSearchScore(query, product, categoryIdHint) }))
    .filter((entry) => entry.score > 0)
    .sort((left, right) => right.score - left.score)
    .map((entry) => entry.product);

  let enriched = false;
  let mergedProducts = store.products;

  if (initialMatches.length < limit) {
    const scrapedProducts = await scrapeProductsForQuery(query, {
      categoryId,
      limit: Math.max(limit, 10),
    });
    if (scrapedProducts.length) {
      enriched = true;
      const mergedStore = await mergeProducts(scrapedProducts.map((product) => ({ ...product, createdAt: nowIso() })));
      mergedProducts = mergedStore.products;
    }
  }

  const finalMatches = filterProducts(mergedProducts, { categoryId })
    .map((product) => ({ product, score: productSearchScore(query, product, categoryIdHint) }))
    .filter((entry) => entry.score > 0)
    .sort((left, right) => right.score - left.score)
    .map((entry) => entry.product)
    .slice(0, limit);

  if (finalMatches.length < limit) {
    const seenIds = new Set(finalMatches.map((product) => product.id));
    const nearestMatches = mergedProducts
      .filter((product) => !seenIds.has(product.id))
      .filter((product) => !categoryIdHint || product.categoryId === categoryIdHint)
      .map((product) => ({ product, score: nearestScore(query, product, categoryIdHint) }))
      .filter((entry) => entry.score > 0)
      .sort((left, right) => right.score - left.score)
      .map((entry) => entry.product)
      .slice(0, limit - finalMatches.length);

    finalMatches.push(...nearestMatches);
  }

  return {
    items: finalMatches,
    offers: getOffers(mergedProducts),
    categories: getCategories(mergedProducts),
    total: mergedProducts.length,
    enriched,
  };
}

export async function getProductDetail(productId) {
  const store = await loadCatalogStore();
  const product = store.products.find((item) => item.id === productId);
  if (!product) {
    return null;
  }

  const enrichedProduct =
    !product.reviews?.length || !product.description || product.description === product.name
      ? await enrichProductFromSource(product)
      : product;

  const mergedStore =
    enrichedProduct !== product ? await mergeProducts([{ ...enrichedProduct, createdAt: product.createdAt || nowIso() }]) : store;
  const sourceProduct = mergedStore.products.find((item) => item.id === enrichedProduct.id) || enrichedProduct;

  const relatedProducts = mergedStore.products
    .filter((candidate) => candidate.id !== sourceProduct.id)
    .map((candidate) => ({ candidate, score: relatedScore(sourceProduct, candidate) }))
    .filter((entry) => entry.score > (entry.candidate.rating || 0))
    .sort((left, right) => right.score - left.score)
    .map((entry) => entry.candidate)
    .slice(0, 6);

  return {
    ...sourceProduct,
    reviews: Array.isArray(sourceProduct.reviews) ? sourceProduct.reviews : [],
    relatedProducts,
  };
}

function buildCatalogPayload(products) {
  return {
    items: products,
    offers: getOffers(products),
    categories: getCategories(products),
    total: products.length,
  };
}
