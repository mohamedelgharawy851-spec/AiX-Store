import { fetchRuntimeJson } from "../runtime/client";
import type { CatalogListResponse, ProductDetail, RelatedProductsResponse } from "./types";

export function bootstrapCatalog(
  count = 100,
  options: {
    token?: string | null;
    signal?: AbortSignal;
  } = {},
) {
  return fetchRuntimeJson(`/catalog/bootstrap?count=${count}`, {
    token: options.token,
    signal: options.signal,
  }) as Promise<CatalogListResponse>;
}

export function listCatalog(
  params: {
    categoryId?: string | null;
    page?: number;
    pageSize?: number;
    token?: string | null;
    signal?: AbortSignal;
  } = {},
) {
  const searchParams = new URLSearchParams();
  if (params.categoryId) {
    searchParams.set("category", params.categoryId);
  }
  searchParams.set("page", String(params.page || 1));
  searchParams.set("pageSize", String(params.pageSize || 20));
  return fetchRuntimeJson(`/catalog/products?${searchParams.toString()}`, {
    token: params.token,
    signal: params.signal,
  }) as Promise<CatalogListResponse>;
}

export function searchCatalog(
  query: string,
  params: {
    categoryId?: string | null;
    page?: number;
    pageSize?: number;
    token?: string | null;
    signal?: AbortSignal;
    timeoutMs?: number;
  } = {},
) {
  const searchParams = new URLSearchParams({ q: query });
  if (params.categoryId) {
    searchParams.set("category", params.categoryId);
  }
  searchParams.set("page", String(params.page || 1));
  searchParams.set("pageSize", String(params.pageSize || 20));
  return fetchRuntimeJson(`/catalog/search?${searchParams.toString()}`, {
    token: params.token,
    signal: params.signal,
    timeoutMs: params.timeoutMs,
  }).then((payload) => {
    const typedPayload = payload as CatalogListResponse;
    if (__DEV__ && typedPayload.ai) {
      console.log("[AIXStore AI]", typedPayload.ai);
    }
    if (__DEV__ && typedPayload.discovery) {
      console.log("[AIXStore Discovery]", typedPayload.discovery);
    }
    return typedPayload;
  });
}

export function getProductDetail(
  productId: string,
  options: { token?: string | null; signal?: AbortSignal } = {},
) {
  return fetchRuntimeJson(`/catalog/products/${productId}`, {
    token: options.token,
    signal: options.signal,
  }) as Promise<ProductDetail>;
}

export function getRelatedProducts(
  productId: string,
  params: { token?: string | null; page?: number; pageSize?: number; signal?: AbortSignal } = {},
) {
  const searchParams = new URLSearchParams();
  searchParams.set("page", String(params.page || 1));
  searchParams.set("pageSize", String(params.pageSize || 12));
  return fetchRuntimeJson(`/catalog/products/${productId}/related?${searchParams.toString()}`, {
    token: params.token,
    signal: params.signal,
  }) as Promise<RelatedProductsResponse>;
}
