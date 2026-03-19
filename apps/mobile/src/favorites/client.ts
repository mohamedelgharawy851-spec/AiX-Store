import { fetchRuntimeJson } from "../runtime/client";
import type { Product } from "../catalog/types";

export type FavoritesResponse = {
  items: Product[];
  page: number;
  pageSize: number;
  hasMore: boolean;
};

export function getFavorites(
  token: string,
  params: {
    page?: number;
    pageSize?: number;
    signal?: AbortSignal;
  } = {},
) {
  const searchParams = new URLSearchParams();
  searchParams.set("page", String(params.page || 1));
  searchParams.set("pageSize", String(params.pageSize || 20));
  return fetchRuntimeJson(`/me/favorites?${searchParams.toString()}`, {
    token,
    signal: params.signal,
  }) as Promise<FavoritesResponse>;
}

export function addFavorite(token: string, productId: string, signal?: AbortSignal) {
  return fetchRuntimeJson(`/me/favorites/${productId}`, {
    method: "PUT",
    token,
    signal,
  }) as Promise<Product>;
}

export function removeFavorite(token: string, productId: string, signal?: AbortSignal) {
  return fetchRuntimeJson(`/me/favorites/${productId}`, {
    method: "DELETE",
    token,
    signal,
  }) as Promise<{ ok: boolean }>;
}
