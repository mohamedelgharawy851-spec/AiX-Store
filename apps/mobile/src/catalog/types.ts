export type Category = {
  id: string;
  name: string;
  shortLabel: string;
  color: string;
  icon: string;
  count?: number;
};

export type ProductReview = {
  id: string;
  authorName: string;
  rating: number;
  body: string;
  publishedAt: string | null;
};

export type ProductImageAsset = {
  id: string;
  url: string;
  altText: string;
  variantLabel?: string | null;
};

export type ProductVariantSummary = {
  productId: string;
  familyKey: string;
  label: string;
  attributes: Record<string, string>;
  price: number;
  originalPrice?: number | null;
  imageUrl: string;
  imageGallery: ProductImageAsset[];
  isCurrent: boolean;
};

export type Product = {
  id: string;
  slug: string;
  provider: string;
  name: string;
  categoryId: string;
  category: string;
  description: string;
  price: number;
  originalPrice?: number | null;
  currency: string;
  rating: number;
  imageUrl: string;
  imageAltText: string;
  reviewCount: number;
  hasReviews: boolean;
  tags: string[];
  sourceSite: string;
  sourceUrl: string;
  sourceImageUrl?: string;
  reviews?: ProductReview[];
  createdAt?: string;
  brand?: string | null;
  isFavorite?: boolean;
  favoritedAt?: string;
  familyKey?: string | null;
  variantLabel?: string | null;
  variantAttributes?: Record<string, string>;
  imageGallery?: ProductImageAsset[];
};

export type ProductDetail = Product & {
  reviews: ProductReview[];
  relatedProducts: Product[];
  variantOptions: ProductVariantSummary[];
};

export type EnrichmentState = {
  state: "idle" | "running" | "error";
  sourceProviders: string[];
  lastUpdatedAt: string | null;
  message: string | null;
};

export type CatalogListResponse = {
  contextKey: string;
  contextType: "home" | "category" | "search";
  appliedQuery: string | null;
  appliedCategoryId: string | null;
  strictCategory: boolean;
  items: Product[];
  offers: Product[];
  categories: Category[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
  queryVariants: string[];
  matching: {
    source: "exact" | "expanded" | "category_feed" | "cached_fallback" | "home";
    exactMatchCount: number;
    filteredOutCount: number;
  };
  enrichment: EnrichmentState;
  ai?: {
    enabled: boolean;
    mode: "off" | "shadow" | "assist";
    invoked: boolean;
    triggerReason: string | null;
    queryVariants: string[];
    selectedVariant: string | null;
    categoryJudgeUsed: boolean;
    modelId: string | null;
    latencyMs: number | null;
    fallbackReason: string | null;
  };
  discovery?: {
    enabled: boolean;
    invoked: boolean;
    provider: "apify" | "searxng" | null;
    engines: string[];
    queriedVariants: string[];
    selectedVariant: string | null;
    domainsConsidered: string[];
    domainsAccepted: string[];
    candidateUrlCount: number;
    acceptedUrlCount: number;
    latencyMs: number | null;
    fallbackReason: string | null;
    actorId?: string | null;
    locale?: {
      country: string;
      language: string;
      domain: string | null;
    } | null;
  };
};

export type RelatedProductsResponse = {
  items: Product[];
  page: number;
  pageSize: number;
  hasMore: boolean;
  total: number;
};
