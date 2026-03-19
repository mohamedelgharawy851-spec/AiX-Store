import type { Product } from "../catalog/types";
import { fetchRuntimeJson } from "../runtime/client";

export type RecommendationResponse = {
  items: Product[];
  page: number;
  pageSize: number;
  hasMore: boolean;
  basedOn: string[];
};

export function getRecommendations(
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
  return fetchRuntimeJson(`/me/recommendations?${searchParams.toString()}`, {
    token,
    signal: params.signal,
  }) as Promise<RecommendationResponse>;
}
