import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { canonicalizeProductUrl, hashId, nowIso, slugify, uniqueBy } from "./utils.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_FILE = path.resolve(__dirname, "../data/catalog.json");

async function ensureDataFile() {
  await fs.mkdir(path.dirname(DATA_FILE), { recursive: true });
  try {
    await fs.access(DATA_FILE);
  } catch {
    await fs.writeFile(
      DATA_FILE,
      JSON.stringify({ version: 1, lastSeededAt: null, products: [] }, null, 2),
      "utf8",
    );
  }
}

function mergeProductPair(left, right) {
  const preferred =
    (right.reviews?.length || 0) > (left.reviews?.length || 0) ||
    (right.description?.length || 0) > (left.description?.length || 0)
      ? right
      : left;
  const fallback = preferred === right ? left : right;

  return {
    ...fallback,
    ...preferred,
    id: preferred.id || fallback.id,
    slug: preferred.slug || fallback.slug,
    categoryId: preferred.categoryId === "others" && fallback.categoryId ? fallback.categoryId : preferred.categoryId,
    category: preferred.category === "Others" && fallback.category ? fallback.category : preferred.category,
    description:
      (preferred.description?.length || 0) >= (fallback.description?.length || 0)
        ? preferred.description
        : fallback.description,
    originalPrice: Math.max(preferred.originalPrice || 0, fallback.originalPrice || 0) || null,
    rating: Math.max(preferred.rating || 0, fallback.rating || 0),
    imageUrl: preferred.imageUrl || fallback.imageUrl,
    imageAltText: preferred.imageAltText || fallback.imageAltText,
    reviewCount: Math.max(preferred.reviewCount || 0, fallback.reviewCount || 0),
    tags: uniqueBy([...(fallback.tags || []), ...(preferred.tags || [])], (item) => item),
    reviews:
      (preferred.reviews?.length || 0) >= (fallback.reviews?.length || 0)
        ? preferred.reviews || []
        : fallback.reviews || [],
    sourceUrl: preferred.sourceUrl || fallback.sourceUrl,
    createdAt: fallback.createdAt || preferred.createdAt || nowIso(),
  };
}

function normalizeProduct(product) {
  const sourceUrl = canonicalizeProductUrl(product?.sourceUrl || "") || product?.sourceUrl || "";
  const stableId = sourceUrl ? hashId(sourceUrl) : product?.id || "";

  return {
    ...product,
    id: stableId,
    slug:
      product?.slug && product.slug.includes(stableId)
        ? product.slug
        : slugify(`${product?.name || "product"}-${stableId}`),
    sourceUrl,
    imageUrl: product?.imageUrl || "",
    imageAltText: product?.imageAltText || product?.name || "",
    reviews: Array.isArray(product?.reviews) ? product.reviews : [],
    tags: Array.isArray(product?.tags) ? uniqueBy(product.tags.filter(Boolean), (item) => item) : [],
  };
}

function normalizeProducts(products) {
  const byKey = new Map();

  for (const product of products.map(normalizeProduct)) {
    const key = product.sourceUrl || product.id;
    const existing = byKey.get(key);
    byKey.set(key, existing ? mergeProductPair(existing, product) : product);
  }

  return Array.from(byKey.values());
}

export async function loadCatalogStore() {
  await ensureDataFile();
  const raw = await fs.readFile(DATA_FILE, "utf8");
  const payload = JSON.parse(raw);
  return {
    version: payload.version || 1,
    lastSeededAt: payload.lastSeededAt || null,
    products: normalizeProducts(Array.isArray(payload.products) ? payload.products : []),
  };
}

export async function saveCatalogStore(store) {
  await ensureDataFile();
  await fs.writeFile(
    DATA_FILE,
    JSON.stringify({ ...store, products: normalizeProducts(store.products || []) }, null, 2),
    "utf8",
  );
}

export async function mergeProducts(newProducts) {
  const store = await loadCatalogStore();
  const merged = normalizeProducts([...newProducts, ...store.products]);
  const nextStore = {
    ...store,
    lastSeededAt: store.lastSeededAt || nowIso(),
    products: merged,
  };
  await saveCatalogStore(nextStore);
  return nextStore;
}

export async function replaceCatalogStore(products) {
  const nextStore = {
    version: 1,
    lastSeededAt: nowIso(),
    products: normalizeProducts(products),
  };
  await saveCatalogStore(nextStore);
  return nextStore;
}
