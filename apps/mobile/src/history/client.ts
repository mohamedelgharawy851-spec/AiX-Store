import { fetchRuntimeJson } from "../runtime/client";

export type HistoryEntry = {
  id: string;
  type: "search" | "product_view" | "source_open" | "category_view" | string;
  title: string;
  subtitle: string;
  productId?: string | null;
  categoryId?: string | null;
  queryText?: string | null;
  sourceUrl?: string | null;
  canonicalSourceUrl?: string | null;
  productSnapshot?: Record<string, unknown> | null;
  createdAt: string;
};

export type HistoryResponse = {
  items: HistoryEntry[];
  page: number;
  pageSize: number;
  hasMore: boolean;
};

export function getHistory(
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
  return fetchRuntimeJson(`/me/history?${searchParams.toString()}`, {
    token,
    signal: params.signal,
  }) as Promise<HistoryResponse>;
}
