import { Ionicons } from "@expo/vector-icons";
import type { ReactNode, RefObject } from "react";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  Easing,
  Image,
  LayoutChangeEvent,
  Linking,
  NativeScrollEvent,
  NativeSyntheticEvent,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { getMe, postUserEvent, signIn, signOut, signUp } from "./src/auth/client";
import { clearSessionToken, readSessionToken, saveSessionToken } from "./src/auth/session";
import type { AuthResponse, MeResponse } from "./src/auth/types";
import { bootstrapCatalog, getProductDetail, getRelatedProducts, listCatalog, searchCatalog } from "./src/catalog/client";
import type {
  CatalogListResponse,
  Category,
  EnrichmentState,
  Product,
  ProductDetail,
  ProductImageAsset,
  ProductReview,
  ProductVariantSummary,
  RelatedProductsResponse,
} from "./src/catalog/types";
import { getHistory } from "./src/history/client";
import type { HistoryEntry, HistoryResponse } from "./src/history/client";
import { addFavorite, getFavorites, removeFavorite } from "./src/favorites/client";
import type { FavoritesResponse } from "./src/favorites/client";
import { getRecommendations } from "./src/recommendations/client";
import type { RecommendationResponse } from "./src/recommendations/client";
import { runtimeBaseUrl } from "./src/runtime/client";
import { colors, spacing } from "./src/theme/tokens";

type TabKey = "home" | "catalog" | "favorites" | "profile";
type ViewMode = "home" | "catalog" | "favorites" | "profile" | "detail" | "related";
type AuthMode = "signIn" | "signUp";
type AuthState = "loading" | "signedOut" | "signedIn";
type RequestArea = "auth" | "bootstrap" | "catalog" | "detail" | "related" | "recommendations" | "history" | "favorites";
type CatalogContextType = "home" | "category" | "search";
type ProductOriginSurface = "home" | "catalog" | "favorites" | "history" | "profile" | "recommended" | "detail_related" | "unknown";
type CatalogContextState = {
  contextKey: string;
  contextType: CatalogContextType;
  query: string | null;
  categoryId: string | null;
  items: Product[];
  page: number;
  hasMore: boolean;
  loadingInitial: boolean;
  loadingMore: boolean;
  error: string | null;
  enrichment: EnrichmentState;
  loadedPages: number[];
};

const brandLogo = require("./assets/aix-store-brand.png");
const CATEGORY_FALLBACK_QUERIES: Record<string, string> = {
  electronics: "laptop",
  food: "snacks",
  fashion: "sneakers",
  beauty: "skincare",
  home: "air fryer",
  toys: "lego",
  sports: "dumbbells",
  others: "storage organizer",
};
const CATEGORY_PRIMARY_TIMEOUT_MS = 8000;
const SEARCH_REQUEST_TIMEOUT_MS = 90000;
const HOME_OFFER_LIMIT = 10;
const OFFER_CARD_WIDTH = 260;
const OFFER_RAIL_ITEM_WIDTH = OFFER_CARD_WIDTH + spacing.md;

const defaultEnrichmentState: EnrichmentState = {
  state: "idle",
  sourceProviders: [],
  lastUpdatedAt: null,
  message: null,
};

function iconName(name: string) {
  return name as keyof typeof Ionicons.glyphMap;
}

function uniqueProducts(products: Product[]) {
  const seen = new Set<string>();
  return products.filter((product) => {
    const identity = product?.familyKey && product?.provider ? `${product.provider.toLowerCase()}::${product.familyKey}` : product?.id;
    if (!identity || seen.has(identity)) {
      return false;
    }
    seen.add(identity);
    return true;
  });
}

function normalizeRuntimeAssetUrl(imageUrl: string | null | undefined) {
  const value = (imageUrl || "").trim();
  if (!value) {
    return "";
  }
  const runtimeBase = runtimeBaseUrl().replace(/\/+$/, "");
  if (value.startsWith("/")) {
    return `${runtimeBase}${value}`;
  }
  const runtimeHttpBase = runtimeBase.replace(/^https:/, "http:");
  if (value.startsWith(runtimeHttpBase)) {
    return `${runtimeBase}${value.slice(runtimeHttpBase.length)}`;
  }
  return value;
}

function firstNonEmptyUrl(...values: Array<string | null | undefined>) {
  for (const value of values) {
    const normalized = normalizeRuntimeAssetUrl(value);
    if (normalized) {
      return normalized;
    }
  }
  return "";
}

function uniqueImageUrls(urls: Array<string | null | undefined>) {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of urls) {
    const normalized = normalizeRuntimeAssetUrl(value);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string) {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  const timeout = new Promise<T>((_, reject) => {
    timeoutId = setTimeout(() => {
      reject(new Error(message));
    }, timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  });
}

function fallbackCategoryQuery(categoryId: string | null) {
  if (!categoryId) {
    return "";
  }
  return CATEGORY_FALLBACK_QUERIES[categoryId] || categoryId;
}

function imageGalleryForProduct(product: Product | ProductDetail | null | undefined) {
  const gallery =
    product?.imageGallery
      ?.map((item) =>
        item?.url
          ? {
              ...item,
              url: normalizeRuntimeAssetUrl(item.url),
            }
          : item,
      )
      .filter((item) => item?.url) ?? [];
  const galleryUrls = uniqueImageUrls([
    product?.imageUrl,
    ...gallery.map((item) => item.url),
    gallery.length ? null : product?.sourceImageUrl,
  ]);
  if (!galleryUrls.length || !product) {
    return [];
  }
  return galleryUrls.map((url, index) => {
    const existing = gallery.find((item) => item.url === url);
    return {
      id: `${product.id}:gallery:${index}`,
      url,
      altText: existing?.altText || product.imageAltText,
      variantLabel: existing?.variantLabel ?? product.variantLabel ?? null,
    };
  }) as ProductImageAsset[];
}

function productFromVariantSummary(
  baseProduct: Product,
  variant: ProductVariantSummary,
): Product {
  const firstImage = firstNonEmptyUrl(variant.imageUrl, variant.imageGallery[0]?.url, baseProduct.imageUrl, baseProduct.sourceImageUrl);
  const sourceImage = firstNonEmptyUrl(variant.imageGallery[0]?.url, baseProduct.sourceImageUrl, firstImage);
  return {
    ...baseProduct,
    id: variant.productId,
    price: variant.price,
    originalPrice: variant.originalPrice ?? null,
    imageUrl: firstImage,
    sourceImageUrl: sourceImage,
    imageAltText: baseProduct.imageAltText,
    familyKey: variant.familyKey,
    variantLabel: variant.label,
    variantAttributes: variant.attributes,
    imageGallery: variant.imageGallery,
  };
}

function offerDiscount(product: Product) {
  if (!product.originalPrice || product.originalPrice <= product.price || product.price <= 0) {
    return 0;
  }
  const discount = Math.round(((product.originalPrice - product.price) / product.originalPrice) * 100);
  return discount > 0 && discount < 95 ? discount : 0;
}

function pickOfferProducts(offers: Product[], catalogProducts: Product[]) {
  return uniqueProducts([
    ...offers.filter((product) => offerDiscount(product) > 0),
    ...catalogProducts.filter((product) => offerDiscount(product) > 0),
  ]).slice(0, HOME_OFFER_LIMIT);
}

function pickDiverseProducts(products: Product[], count: number) {
  const buckets = uniqueProducts(products).reduce<Map<string, Product[]>>((map, product) => {
    const existing = map.get(product.categoryId) || [];
    existing.push(product);
    map.set(product.categoryId, existing);
    return map;
  }, new Map());
  const result: Product[] = [];
  while (result.length < count && Array.from(buckets.values()).some((items) => items.length)) {
    for (const items of buckets.values()) {
      const next = items.shift();
      if (next) {
        result.push(next);
      }
      if (result.length >= count) {
        break;
      }
    }
  }
  return result;
}

function expectedCatalogContextKey(query: string, categoryId: string | null) {
  const normalizedQuery = query.trim().toLowerCase();
  if (normalizedQuery) {
    return `search::${categoryId || "all"}::${normalizedQuery}`;
  }
  if (categoryId) {
    return `category::${categoryId}`;
  }
  return "home";
}

function catalogCacheKey(contextKey: string, page: number) {
  return `${contextKey}:page:${page}`;
}

function recommendationCacheKey(userId: string, page: number) {
  return `recommendations:${userId}:page:${page}`;
}

function historyCacheKey(userId: string, page: number) {
  return `history:${userId}:page:${page}`;
}

function relatedCacheKey(productId: string, page: number) {
  return `related:${productId}:page:${page}`;
}

function favoritesCacheKey(userId: string, page: number) {
  return `favorites:${userId}:page:${page}`;
}

function collectCatalogItemsFromCache(
  cache: Map<string, CatalogListResponse>,
  contextKey: string,
  page: number,
  fallbackItems: Product[],
) {
  const priorItems: Product[] = [];
  for (let pageIndex = 1; pageIndex < page; pageIndex += 1) {
    const cachedPage = cache.get(catalogCacheKey(contextKey, pageIndex));
    if (cachedPage?.items?.length) {
      priorItems.push(...cachedPage.items);
    }
  }
  if (!priorItems.length) {
    priorItems.push(...fallbackItems);
  }
  return uniqueProducts(priorItems);
}

function buildCatalogContext(
  contextKey: string,
  contextType: CatalogContextType,
  query: string | null,
  categoryId: string | null,
): CatalogContextState {
  return {
    contextKey,
    contextType,
    query,
    categoryId,
    items: [],
    page: 1,
    hasMore: false,
    loadingInitial: false,
    loadingMore: false,
    error: null,
    enrichment: defaultEnrichmentState,
    loadedPages: [],
  };
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function interestLabel(value: string) {
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function snapshotToProduct(snapshot: Record<string, unknown> | null | undefined): Product | null {
  if (!snapshot || typeof snapshot !== "object") {
    return null;
  }
  const id = typeof snapshot.id === "string" ? snapshot.id : "";
  const name = typeof snapshot.name === "string" ? snapshot.name : "";
  const sourceUrl = typeof snapshot.sourceUrl === "string" ? snapshot.sourceUrl : "";
  if (!id || !name || !sourceUrl) {
    return null;
  }
  return {
    id,
    slug: typeof snapshot.slug === "string" ? snapshot.slug : id,
    provider: typeof snapshot.provider === "string" ? snapshot.provider : "Archived",
    name,
    categoryId: typeof snapshot.categoryId === "string" ? snapshot.categoryId : "others",
    category: typeof snapshot.category === "string" ? snapshot.category : "Others",
    description: typeof snapshot.description === "string" ? snapshot.description : name,
    price: typeof snapshot.price === "number" ? snapshot.price : 0,
    originalPrice: typeof snapshot.originalPrice === "number" ? snapshot.originalPrice : null,
    currency: typeof snapshot.currency === "string" ? snapshot.currency : "USD",
    rating: typeof snapshot.rating === "number" ? snapshot.rating : 0,
    imageUrl: typeof snapshot.imageUrl === "string" ? snapshot.imageUrl : "",
    imageAltText: typeof snapshot.imageAltText === "string" ? snapshot.imageAltText : name,
    reviewCount: typeof snapshot.reviewCount === "number" ? snapshot.reviewCount : 0,
    hasReviews: Boolean(snapshot.hasReviews),
    tags: Array.isArray(snapshot.tags) ? snapshot.tags.filter((item): item is string => typeof item === "string") : [],
    sourceSite: typeof snapshot.sourceSite === "string" ? snapshot.sourceSite : "Archived",
    sourceUrl,
    sourceImageUrl: typeof snapshot.sourceImageUrl === "string" ? snapshot.sourceImageUrl : undefined,
    imageGallery: Array.isArray(snapshot.imageGallery)
      ? snapshot.imageGallery.filter(
          (item): item is ProductImageAsset =>
            Boolean(item) && typeof item === "object" && typeof (item as ProductImageAsset).url === "string",
        )
      : undefined,
    createdAt: typeof snapshot.createdAt === "string" ? snapshot.createdAt : undefined,
    brand: typeof snapshot.brand === "string" ? snapshot.brand : null,
    isFavorite: Boolean(snapshot.isFavorite),
    familyKey: typeof snapshot.familyKey === "string" ? snapshot.familyKey : null,
    variantLabel: typeof snapshot.variantLabel === "string" ? snapshot.variantLabel : null,
    variantAttributes:
      snapshot.variantAttributes && typeof snapshot.variantAttributes === "object"
        ? Object.fromEntries(
            Object.entries(snapshot.variantAttributes as Record<string, unknown>).flatMap(([key, value]) =>
              typeof value === "string" ? [[key, value] as [string, string]] : [],
            ),
          )
        : undefined,
  };
}

function ScreenShell({
  children,
  header,
  scrollViewRef,
  title,
  subtitle,
}: {
  children: ReactNode;
  header?: ReactNode;
  scrollViewRef?: RefObject<ScrollView | null>;
  title?: string;
  subtitle?: string;
}) {
  return (
    <ScrollView contentContainerStyle={styles.screenContent} ref={scrollViewRef} showsVerticalScrollIndicator={false}>
      {header ? (
        header
      ) : (
        <>
          {title ? <Text style={styles.screenTitle}>{title}</Text> : null}
          {subtitle ? <Text style={styles.screenSubtitle}>{subtitle}</Text> : null}
        </>
      )}
      {children}
    </ScrollView>
  );
}

function BrandHero({
  caption,
  compact = false,
}: {
  caption: string;
  compact?: boolean;
}) {
  return (
    <View style={[styles.brandHero, compact && styles.brandHeroCompact]}>
      <Image resizeMode="contain" source={brandLogo} style={[styles.brandImage, compact && styles.brandImageCompact]} />
      <Text style={[styles.brandCaption, compact && styles.brandCaptionCompact]}>{caption}</Text>
    </View>
  );
}

function SearchField({
  value,
  placeholder,
  onChangeText,
  onSearch,
}: {
  value: string;
  placeholder: string;
  onChangeText: (value: string) => void;
  onSearch: () => void;
}) {
  return (
    <View style={styles.searchBox}>
      <Ionicons color={colors.textMuted} name="search-outline" size={18} />
      <TextInput
        autoCapitalize="none"
        onChangeText={onChangeText}
        onSubmitEditing={onSearch}
        placeholder={placeholder}
        placeholderTextColor={colors.textMuted}
        returnKeyType="search"
        style={styles.searchInput}
        value={value}
      />
      {value.trim() ? (
        <Pressable onPress={onSearch} style={styles.searchButton}>
          <Text style={styles.searchButtonText}>Search</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function PromoHeroCard({
  onPress,
}: {
  onPress: () => void;
}) {
  const drift = useRef(new Animated.Value(0)).current;
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const driftLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(drift, {
          duration: 5200,
          easing: Easing.inOut(Easing.quad),
          toValue: 1,
          useNativeDriver: true,
        }),
        Animated.timing(drift, {
          duration: 5200,
          easing: Easing.inOut(Easing.quad),
          toValue: 0,
          useNativeDriver: true,
        }),
      ]),
    );
    const pulseLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          duration: 3600,
          easing: Easing.inOut(Easing.sin),
          toValue: 1,
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          duration: 3600,
          easing: Easing.inOut(Easing.sin),
          toValue: 0,
          useNativeDriver: true,
        }),
      ]),
    );
    driftLoop.start();
    pulseLoop.start();
    return () => {
      driftLoop.stop();
      pulseLoop.stop();
    };
  }, [drift, pulse]);

  return (
    <View style={styles.aixHeroCard}>
      <View pointerEvents="none" style={styles.aixHeroBackdrop}>
        <Animated.View
          style={[
            styles.aixHeroWave,
            {
              transform: [
                { rotate: "-14deg" },
                { translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -18] }) },
                { translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, 10] }) },
                { scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.06] }) },
              ],
            },
          ]}
        />
        <Animated.View
          style={[
            styles.aixHeroOrb,
            styles.aixHeroOrbPrimary,
            {
              transform: [
                { translateX: pulse.interpolate({ inputRange: [0, 1], outputRange: [0, -10] }) },
                { translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -14] }) },
                { scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] }) },
              ],
            },
          ]}
        />
        <Animated.View
          style={[
            styles.aixHeroOrb,
            styles.aixHeroOrbSecondary,
            {
              transform: [
                { translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, 12] }) },
                { translateY: pulse.interpolate({ inputRange: [0, 1], outputRange: [0, -8] }) },
                { scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 0.94] }) },
              ],
            },
          ]}
        />
        <Animated.View
          style={[
            styles.aixHeroSpark,
            {
              opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.45, 0.85] }),
              transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.14] }) }],
            },
          ]}
        />
      </View>
      <Text style={styles.aixHeroEyebrow}>LIMITED TIME OFFER</Text>
      <Text style={styles.aixHeroTitle}>Mega Summer Sale</Text>
      <Text style={styles.aixHeroBody}>Up to 60% off on electronics, beauty, home, and more.</Text>
      <Pressable onPress={onPress} style={styles.aixHeroButton}>
        <Text style={styles.aixHeroButtonText}>Shop now</Text>
      </Pressable>
    </View>
  );
}

function SkeletonBlock({
  height,
  width = "100%",
  style,
}: {
  height: number;
  width?: number | string;
  style?: any;
}) {
  return <View style={[styles.skeletonBlock, { height, width }, style]} />;
}

function ProductImage({
  imageAltText,
  imageUrl,
  sourceImageUrl,
  style,
}: {
  imageAltText: string;
  imageUrl: string;
  sourceImageUrl?: string | null;
  style: any;
}) {
  const candidateUrls = uniqueImageUrls([imageUrl, sourceImageUrl]);
  const [activeIndex, setActiveIndex] = useState(0);
  const resolvedImageUrl = candidateUrls[activeIndex] || "";

  useEffect(() => {
    setActiveIndex(0);
  }, [candidateUrls.join("|")]);

  if (!resolvedImageUrl) {
    return (
      <View style={[style, styles.imageFallback]}>
        <Ionicons color={colors.textMuted} name="image-outline" size={22} />
        <Text style={styles.imageFallbackText}>Image unavailable</Text>
      </View>
    );
  }

  return (
    <Image
      accessibilityLabel={imageAltText}
      onError={() => {
        if (activeIndex < candidateUrls.length - 1) {
          setActiveIndex((current) => current + 1);
          return;
        }
        setActiveIndex(candidateUrls.length);
      }}
      resizeMode="cover"
      source={{ uri: resolvedImageUrl }}
      style={style}
    />
  );
}

function OfferCardTile({
  product,
  onPress,
  onToggleFavorite,
}: {
  product: Product;
  onPress: () => void;
  onToggleFavorite: () => void;
}) {
  const discount = offerDiscount(product);

  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.offerCard, pressed && styles.pressedCard]}>
      {discount > 0 ? (
        <View style={styles.offerDiscountBadge}>
          <Text style={styles.productDiscountText}>{discount}% OFF</Text>
        </View>
      ) : null}
      <Pressable
        onPress={(event) => {
          event.stopPropagation();
          onToggleFavorite();
        }}
        style={[styles.favoriteButton, styles.favoriteButtonFloating, product.isFavorite && styles.favoriteButtonActive]}
      >
        <Ionicons
          color={product.isFavorite ? colors.onPrimary : colors.textMuted}
          name={product.isFavorite ? "heart" : "heart-outline"}
          size={16}
        />
      </Pressable>
      <ProductImage
        imageAltText={product.imageAltText}
        imageUrl={product.imageUrl}
        sourceImageUrl={product.sourceImageUrl}
        style={styles.offerImage}
      />
      <View style={styles.offerBody}>
        <Text numberOfLines={1} style={styles.offerTitle}>
          {product.name}
        </Text>
        <View style={styles.offerPriceRow}>
          <Text style={styles.offerPrice}>${product.price.toFixed(2)}</Text>
          {discount > 0 && product.originalPrice ? (
            <Text style={styles.offerOriginalPrice}>${product.originalPrice.toFixed(2)}</Text>
          ) : null}
        </View>
      </View>
    </Pressable>
  );
}

function AutoScrollingOfferRail({
  offers,
  onProductPress,
  onToggleFavorite,
}: {
  offers: Product[];
  onProductPress: (product: Product) => void;
  onToggleFavorite: (product: Product) => void;
}) {
  const translateX = useRef(new Animated.Value(0)).current;
  const animationRef = useRef<Animated.CompositeAnimation | null>(null);
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentTranslateRef = useRef(0);
  const pausedRef = useRef(false);
  const [paused, setPaused] = useState(false);
  const trackWidth = offers.length * OFFER_RAIL_ITEM_WIDTH;
  const duplicatedOffers = offers.length > 1 ? [...offers, ...offers] : offers;

  useEffect(() => {
    const listenerId = translateX.addListener(({ value }) => {
      currentTranslateRef.current = value;
    });
    return () => {
      translateX.removeListener(listenerId);
    };
  }, [translateX]);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    return () => {
      animationRef.current?.stop();
      if (resumeTimerRef.current) {
        clearTimeout(resumeTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    animationRef.current?.stop();
    if (offers.length <= 1 || trackWidth <= 0) {
      translateX.setValue(0);
      currentTranslateRef.current = 0;
      return;
    }
    if (paused) {
      return;
    }

    const run = () => {
      if (pausedRef.current) {
        return;
      }
      const currentValue =
        currentTranslateRef.current <= -trackWidth || currentTranslateRef.current > 0 ? 0 : currentTranslateRef.current;
      const remainingDistance = Math.max(trackWidth + currentValue, 1);
      translateX.setValue(currentValue);
      animationRef.current = Animated.timing(translateX, {
        duration: Math.max(16000, Math.round((remainingDistance / trackWidth) * 26000)),
        easing: Easing.linear,
        toValue: -trackWidth,
        useNativeDriver: true,
      });
      animationRef.current.start(({ finished }) => {
        if (!finished || pausedRef.current) {
          return;
        }
        currentTranslateRef.current = 0;
        translateX.setValue(0);
        run();
      });
    };

    run();
    return () => {
      animationRef.current?.stop();
    };
  }, [offers.length, paused, trackWidth, translateX]);

  function pauseRail() {
    if (resumeTimerRef.current) {
      clearTimeout(resumeTimerRef.current);
      resumeTimerRef.current = null;
    }
    setPaused(true);
  }

  function resumeRailSoon() {
    if (resumeTimerRef.current) {
      clearTimeout(resumeTimerRef.current);
    }
    resumeTimerRef.current = setTimeout(() => {
      setPaused(false);
    }, 1200);
  }

  return (
    <View
      onTouchCancel={resumeRailSoon}
      onTouchEnd={resumeRailSoon}
      onTouchStart={pauseRail}
      style={styles.offerRailViewport}
    >
      <Animated.View style={[styles.offerRailTrack, { transform: [{ translateX }] }]}>
        {duplicatedOffers.map((product, index) => (
          <View key={`${product.id}:${index}`} style={styles.offerRailItem}>
            <OfferCardTile
              onPress={() => onProductPress(product)}
              onToggleFavorite={() => onToggleFavorite(product)}
              product={product}
            />
          </View>
        ))}
      </Animated.View>
    </View>
  );
}

function ProductCard({
  product,
  onPress,
  onToggleFavorite,
}: {
  product: Product;
  onPress: () => void;
  onToggleFavorite?: () => void;
}) {
  const discount = offerDiscount(product);

  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.productCard, pressed && styles.pressedCard]}>
      {onToggleFavorite ? (
        <Pressable
          onPress={(event) => {
            event.stopPropagation();
            onToggleFavorite();
          }}
          style={[styles.favoriteButton, product.isFavorite && styles.favoriteButtonActive]}
        >
          <Ionicons
            color={product.isFavorite ? colors.onPrimary : colors.textMuted}
            name={product.isFavorite ? "heart" : "heart-outline"}
            size={16}
          />
        </Pressable>
      ) : null}
      {discount > 0 ? (
        <View style={styles.productDiscountBadge}>
          <Text style={styles.productDiscountText}>{discount}% OFF</Text>
        </View>
      ) : null}
      <View style={styles.productImageWrap}>
        <ProductImage
          imageAltText={product.imageAltText}
          imageUrl={product.imageUrl}
          sourceImageUrl={product.sourceImageUrl}
          style={styles.productImage}
        />
      </View>
      <Text style={styles.productCategory}>{product.category}</Text>
      <Text numberOfLines={1} style={styles.productName}>
        {product.name}
      </Text>
      <Text numberOfLines={2} style={styles.productDescription}>
        {product.description}
      </Text>
      <View style={styles.productMeta}>
        <View style={styles.productPriceGroup}>
          <Text style={styles.productPrice}>${product.price.toFixed(2)}</Text>
          {discount > 0 && product.originalPrice ? (
            <Text style={styles.productOriginalPrice}>${product.originalPrice.toFixed(2)}</Text>
          ) : null}
        </View>
        <View style={styles.ratingGroup}>
          <Ionicons color={colors.accent} name="star" size={14} />
          <Text style={styles.productRating}>{product.rating.toFixed(1)}</Text>
        </View>
      </View>
    </Pressable>
  );
}

function ProductCardSkeleton() {
  return (
    <View style={styles.productCard}>
      <SkeletonBlock height={160} style={styles.productImage} />
      <SkeletonBlock height={12} width="45%" />
      <SkeletonBlock height={18} width="78%" />
      <SkeletonBlock height={14} width="94%" />
      <SkeletonBlock height={14} width="82%" />
      <View style={styles.productMeta}>
        <SkeletonBlock height={16} width="32%" />
        <SkeletonBlock height={16} width="18%" />
      </View>
    </View>
  );
}

function HistoryRowSkeleton() {
  return (
    <View style={styles.historyCard}>
      <SkeletonBlock height={16} width="58%" />
      <SkeletonBlock height={13} width="84%" />
      <SkeletonBlock height={12} width="26%" />
    </View>
  );
}

function ShowMoreButton({
  visible = true,
  loading = false,
  onPress,
  label = "Show more",
}: {
  visible?: boolean;
  loading?: boolean;
  onPress: () => void;
  label?: string;
}) {
  if (!visible) {
    return null;
  }
  return (
    <Pressable disabled={loading} onPress={onPress} style={styles.showMoreButton}>
      <Text style={styles.showMoreText}>{loading ? "Loading more..." : label}</Text>
    </Pressable>
  );
}

function EmptyState({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <View style={styles.emptyCard}>
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyBody}>{body}</Text>
    </View>
  );
}

function HomeScreen({
  categories,
  offers,
  recommendations,
  trendingProducts,
  recommendationsLabel,
  recommendationsHasMore,
  loadingRecommendations,
  onCategoryPress,
  onProductPress,
  onToggleFavorite,
  onOpenCatalog,
  onShowMoreRecommendations,
}: {
  categories: Category[];
  offers: Product[];
  recommendations: Product[];
  trendingProducts: Product[];
  recommendationsLabel: string[];
  recommendationsHasMore: boolean;
  loadingRecommendations: boolean;
  onCategoryPress: (categoryId: string) => void;
  onProductPress: (product: Product) => void;
  onToggleFavorite: (product: Product) => void;
  onOpenCatalog: () => void;
  onShowMoreRecommendations: () => void;
}) {
  return (
    <ScreenShell>
      <View style={styles.aixHeader}>
        <View style={styles.aixBrandWrap}>
          <View style={styles.aixBrandIcon}>
            <Ionicons color={colors.onPrimary} name="bag-handle" size={20} />
          </View>
          <View>
            <Text style={styles.aixBrandTitle}>AIXStore</Text>
            <Text style={styles.aixBrandSubtitle}>Fresh picks across every aisle</Text>
          </View>
        </View>
      </View>

      <PromoHeroCard onPress={onOpenCatalog} />

      <ScrollView horizontal contentContainerStyle={styles.rowGap} showsHorizontalScrollIndicator={false}>
        {categories.map((category) => (
          <Pressable
            key={category.id}
            onPress={() => onCategoryPress(category.id)}
            style={({ pressed }) => [styles.categoryCard, pressed && styles.pressedCard]}
          >
            <View style={[styles.categoryBadge, { backgroundColor: category.color }]}>
              <Ionicons color={colors.onPrimary} name={iconName(category.icon)} size={24} />
            </View>
            <Text style={styles.categoryLabel}>{category.name}</Text>
          </Pressable>
        ))}
      </ScrollView>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Offers</Text>
        <Pressable onPress={onOpenCatalog}>
          <Text style={styles.sectionAction}>Browse all</Text>
        </Pressable>
      </View>

      <AutoScrollingOfferRail offers={offers} onProductPress={onProductPress} onToggleFavorite={onToggleFavorite} />

      <View style={styles.sectionHeader}>
        <View>
          <Text style={styles.sectionTitle}>Recommended for you</Text>
          {recommendationsLabel.length ? (
            <Text style={styles.sectionSubtext}>Based on {recommendationsLabel.map(interestLabel).join(", ")}</Text>
          ) : null}
        </View>
      </View>

      {loadingRecommendations && !recommendations.length ? (
        <View style={styles.grid}>
          {Array.from({ length: 4 }).map((_, index) => (
            <ProductCardSkeleton key={`home-skeleton-${index}`} />
          ))}
        </View>
      ) : recommendations.length ? (
        <>
          <View style={styles.grid}>
            {recommendations.map((product) => (
              <ProductCard
                key={product.id}
                onPress={() => onProductPress(product)}
                onToggleFavorite={() => onToggleFavorite(product)}
                product={product}
              />
            ))}
          </View>
          <ShowMoreButton onPress={onShowMoreRecommendations} visible={recommendationsHasMore} />
        </>
      ) : (
        <EmptyState
          body="Search or open a few items to personalize this page."
          title="Recommendations will appear here"
        />
      )}

      {trendingProducts.length ? (
        <>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Inspired by your recent activity</Text>
          </View>
          <View style={styles.grid}>
            {trendingProducts.map((product) => (
              <ProductCard
                key={`trend-${product.id}`}
                onPress={() => onProductPress(product)}
                onToggleFavorite={() => onToggleFavorite(product)}
                product={product}
              />
            ))}
          </View>
        </>
      ) : null}
    </ScreenShell>
  );
}

function CatalogScreen({
  categories,
  products,
  searchQuery,
  selectedCategoryId,
  enrichment,
  loading,
  loadingMore,
  scrollToTopSignal,
  error,
  onSearchChange,
  onSearchSubmit,
  onCategoryPress,
  onClearCategory,
  onProductPress,
  onToggleFavorite,
  onShowMore,
  hasMore,
}: {
  categories: Category[];
  products: Product[];
  searchQuery: string;
  selectedCategoryId: string | null;
  enrichment: EnrichmentState;
  loading: boolean;
  loadingMore: boolean;
  scrollToTopSignal: number;
  error: string | null;
  onSearchChange: (value: string) => void;
  onSearchSubmit: () => void;
  onCategoryPress: (categoryId: string) => void;
  onClearCategory: () => void;
  onProductPress: (product: Product) => void;
  onToggleFavorite: (product: Product) => void;
  onShowMore: () => void;
  hasMore: boolean;
}) {
  const scrollViewRef = useRef<ScrollView | null>(null);
  const emptyTitle = searchQuery.trim()
    ? enrichment.state === "running"
      ? "Finding the closest matches from live stores..."
      : "No products matched this search yet."
    : "We’re fetching more in this section.";

  useEffect(() => {
    if (scrollToTopSignal <= 0) {
      return;
    }
    scrollViewRef.current?.scrollTo({ animated: true, y: 0 });
  }, [scrollToTopSignal]);

  return (
    <ScreenShell
      scrollViewRef={scrollViewRef}
      title="Marketplace"
      subtitle="Fast sections, live search fallback, and one-tap seller redirects."
    >
      <SearchField
        onChangeText={onSearchChange}
        onSearch={onSearchSubmit}
        placeholder="Search catalog..."
        value={searchQuery}
      />

      <ScrollView horizontal contentContainerStyle={styles.rowGap} showsHorizontalScrollIndicator={false}>
        <Pressable onPress={onClearCategory} style={[styles.filterChip, !selectedCategoryId && styles.filterChipActive]}>
          <Text style={[styles.filterChipText, !selectedCategoryId && styles.filterChipTextActive]}>All</Text>
        </Pressable>
        {categories.map((category) => {
          const active = selectedCategoryId === category.id;
          return (
            <Pressable
              key={category.id}
              onPress={() => onCategoryPress(category.id)}
              style={[styles.filterChip, active && styles.filterChipActive]}
            >
              <Text style={[styles.filterChipText, active && styles.filterChipTextActive]}>{category.name}</Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {searchQuery.trim() ? (
        <View style={styles.infoCard}>
          <Text style={styles.cardTitle}>Search status</Text>
          <Text style={styles.infoBody}>
            {enrichment.state === "running"
              ? "Refreshing results from live stores while cached matches stay visible."
              : enrichment.state === "error"
                ? enrichment.message || "Search enrichment failed."
                : "Showing the fastest cached and live-ranked results."}
          </Text>
        </View>
      ) : null}

      {/* Error hidden: masks backend wake-up delays with persistent loading state */}
      
      {(loading || error) && !products.length ? (
        <View style={styles.grid}>
          {Array.from({ length: 6 }).map((_, index) => (
            <ProductCardSkeleton key={`catalog-skeleton-${index}`} />
          ))}
        </View>
      ) : products.length ? (
        <>
          <View style={styles.grid}>
            {products.map((product) => (
              <ProductCard
                key={product.id}
                onPress={() => onProductPress(product)}
                onToggleFavorite={() => onToggleFavorite(product)}
                product={product}
              />
            ))}
          </View>
          <ShowMoreButton loading={loadingMore} onPress={onShowMore} visible={hasMore} />
        </>
      ) : (
        <EmptyState body={emptyTitle} title="No visible products yet" />
      )}
    </ScreenShell>
  );
}

function ProductDetailScreen({
  product,
  reviews,
  relatedProducts,
  variantOptions,
  loadingExtras,
  onBack,
  onOpenSource,
  onProductPress,
  onOpenRelated,
  onSelectVariant,
  onToggleFavorite,
}: {
  product: Product;
  reviews: ProductReview[];
  relatedProducts: Product[];
  variantOptions: ProductVariantSummary[];
  loadingExtras: boolean;
  onBack: () => void;
  onOpenSource: (product: Product) => void;
  onProductPress: (product: Product) => void;
  onOpenRelated: () => void;
  onSelectVariant: (variant: ProductVariantSummary) => void;
  onToggleFavorite: (product: Product) => void;
}) {
  const gallery = imageGalleryForProduct(product);
  const [galleryWidth, setGalleryWidth] = useState(0);
  const [activeImageIndex, setActiveImageIndex] = useState(0);

  useEffect(() => {
    setActiveImageIndex(0);
  }, [product.id, gallery.length]);

  function handleGalleryLayout(event: LayoutChangeEvent) {
    setGalleryWidth(Math.max(1, Math.round(event.nativeEvent.layout.width)));
  }

  function handleGalleryScroll(event: NativeSyntheticEvent<NativeScrollEvent>) {
    if (!galleryWidth) {
      return;
    }
    const nextIndex = Math.round(event.nativeEvent.contentOffset.x / galleryWidth);
    setActiveImageIndex(Math.max(0, Math.min(nextIndex, Math.max(gallery.length - 1, 0))));
  }

  return (
    <ScrollView contentContainerStyle={styles.screenContent} showsVerticalScrollIndicator={false}>
      <Pressable onPress={onBack} style={styles.backButton}>
        <Ionicons color={colors.text} name="arrow-back" size={20} />
        <Text style={styles.backButtonText}>Back</Text>
      </Pressable>

      <View onLayout={handleGalleryLayout} style={styles.detailImageWrap}>
        <ScrollView
          horizontal
          onMomentumScrollEnd={handleGalleryScroll}
          pagingEnabled
          showsHorizontalScrollIndicator={false}
        >
          {gallery.map((image) => (
            <ProductImage
              key={image.id}
              imageAltText={image.altText}
              imageUrl={image.url}
              style={[styles.detailImage, galleryWidth ? { width: galleryWidth } : null]}
            />
          ))}
        </ScrollView>
        <Pressable
          onPress={() => onToggleFavorite(product)}
          style={[styles.detailFavoriteButton, product.isFavorite && styles.favoriteButtonActive]}
        >
          <Ionicons
            color={product.isFavorite ? colors.onPrimary : colors.textMuted}
            name={product.isFavorite ? "heart" : "heart-outline"}
            size={18}
          />
        </Pressable>
      </View>
      {gallery.length > 1 ? (
        <View style={styles.galleryDots}>
          {gallery.map((image, index) => (
            <View
              key={`${image.id}-dot`}
              style={[styles.galleryDot, index === activeImageIndex && styles.galleryDotActive]}
            />
          ))}
        </View>
      ) : null}
      <Text style={styles.detailCategory}>{product.category}</Text>
      <Text style={styles.detailTitle}>{product.name}</Text>

      <View style={styles.detailMetaRow}>
        <View style={styles.detailPriceWrap}>
          <Text style={styles.detailPrice}>${product.price.toFixed(2)}</Text>
          {offerDiscount(product) > 0 && product.originalPrice ? (
            <>
              <Text style={styles.detailOriginalPrice}>${product.originalPrice.toFixed(2)}</Text>
              <View style={styles.detailDiscountPill}>
                <Text style={styles.productDiscountText}>{offerDiscount(product)}% OFF</Text>
              </View>
            </>
          ) : null}
        </View>
        <View style={styles.ratingGroup}>
          <Ionicons color={colors.accent} name="star" size={16} />
          <Text style={styles.productRating}>{product.rating.toFixed(1)}</Text>
        </View>
      </View>

      <Text style={styles.detailDescription}>{product.description}</Text>

      <View style={styles.infoCard}>
        <Text style={styles.cardTitle}>Original listing</Text>
        <Text style={styles.infoBody}>Sold on {product.sourceSite}</Text>
        <Text numberOfLines={2} style={styles.sourceUrl}>
          {product.sourceUrl}
        </Text>
      </View>

      <Pressable onPress={() => onOpenSource(product)} style={styles.primaryButton}>
        <View style={styles.buttonInline}>
          <Text style={styles.primaryButtonText}>Open on {product.sourceSite}</Text>
          <Ionicons color={colors.onPrimary} name="open-outline" size={18} />
        </View>
      </Pressable>

      <View style={styles.detailSection}>
        <Text style={styles.sectionTitle}>Customer comments</Text>
        {loadingExtras && !reviews.length ? (
          <>
            {Array.from({ length: 3 }).map((_, index) => (
              <View key={`review-skeleton-${index}`} style={styles.reviewCard}>
                <SkeletonBlock height={16} width="44%" />
                <SkeletonBlock height={14} width="100%" />
                <SkeletonBlock height={14} width="86%" />
              </View>
            ))}
          </>
        ) : reviews.length ? (
          reviews.map((review) => (
            <View key={review.id} style={styles.reviewCard}>
              <View style={styles.reviewHeader}>
                <Text style={styles.reviewAuthor}>{review.authorName}</Text>
                <View style={styles.ratingGroup}>
                  <Ionicons color={colors.accent} name="star" size={14} />
                  <Text style={styles.productRating}>{review.rating.toFixed(1)}</Text>
                </View>
              </View>
              <Text style={styles.reviewBody}>{review.body}</Text>
              <Text style={styles.reviewDate}>{review.publishedAt ? formatDate(review.publishedAt) : "Recent"}</Text>
            </View>
          ))
        ) : (
          <EmptyState body="Comments will appear here after the live detail scrape finishes." title="No comments yet" />
        )}
      </View>

      <View style={styles.detailSection}>
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Related products</Text>
          <Pressable onPress={onOpenRelated}>
            <Text style={styles.sectionAction}>Show more</Text>
          </Pressable>
        </View>
        {loadingExtras && !relatedProducts.length ? (
          <ScrollView horizontal contentContainerStyle={styles.rowGap} showsHorizontalScrollIndicator={false}>
            {Array.from({ length: 3 }).map((_, index) => (
              <View key={`related-skeleton-${index}`} style={styles.relatedCard}>
                <SkeletonBlock height={120} width="100%" />
                <SkeletonBlock height={14} width="80%" />
                <SkeletonBlock height={14} width="36%" />
              </View>
            ))}
          </ScrollView>
        ) : (
          <ScrollView horizontal contentContainerStyle={styles.rowGap} showsHorizontalScrollIndicator={false}>
            {relatedProducts.map((relatedProduct) => (
              <Pressable
                key={relatedProduct.id}
                onPress={() => onProductPress(relatedProduct)}
                style={({ pressed }) => [styles.relatedCard, pressed && styles.pressedCard]}
              >
                {offerDiscount(relatedProduct) > 0 ? (
                  <View style={styles.relatedDiscountBadge}>
                    <Text style={styles.productDiscountText}>{offerDiscount(relatedProduct)}% OFF</Text>
                  </View>
                ) : null}
                <Pressable
                  onPress={(event) => {
                    event.stopPropagation();
                    onToggleFavorite(relatedProduct);
                  }}
                  style={[styles.favoriteButton, styles.favoriteButtonFloating, relatedProduct.isFavorite && styles.favoriteButtonActive]}
                >
                  <Ionicons
                    color={relatedProduct.isFavorite ? colors.onPrimary : colors.textMuted}
                    name={relatedProduct.isFavorite ? "heart" : "heart-outline"}
                    size={16}
                  />
                </Pressable>
                <ProductImage
                  imageAltText={relatedProduct.imageAltText}
                  imageUrl={relatedProduct.imageUrl}
                  sourceImageUrl={relatedProduct.sourceImageUrl}
                  style={styles.relatedImage}
                />
                <Text numberOfLines={1} style={styles.relatedTitle}>
                  {relatedProduct.name}
                </Text>
                <View style={styles.relatedPriceRow}>
                  <Text style={styles.relatedPrice}>${relatedProduct.price.toFixed(2)}</Text>
                  {offerDiscount(relatedProduct) > 0 && relatedProduct.originalPrice ? (
                    <Text style={styles.relatedOriginalPrice}>${relatedProduct.originalPrice.toFixed(2)}</Text>
                  ) : null}
                </View>
              </Pressable>
            ))}
          </ScrollView>
        )}
      </View>
    </ScrollView>
  );
}

function RelatedScreen({
  products,
  loading,
  loadingMore,
  onBack,
  onGrabMore,
  onProductPress,
  onToggleFavorite,
  hasMore,
}: {
  products: Product[];
  loading: boolean;
  loadingMore: boolean;
  onBack: () => void;
  onGrabMore: () => void;
  onProductPress: (product: Product) => void;
  onToggleFavorite: (product: Product) => void;
  hasMore: boolean;
}) {
  return (
    <ScreenShell
      title="Recommended"
      subtitle="Explore more products related to the item you opened."
      header={
        <View style={styles.relatedHeader}>
          <Pressable onPress={onBack} style={styles.backButton}>
            <Ionicons color={colors.text} name="arrow-back" size={20} />
            <Text style={styles.backButtonText}>Back</Text>
          </Pressable>
          <Text style={styles.screenTitle}>Recommended</Text>
          <Text style={styles.screenSubtitle}>More products from the same interest graph.</Text>
        </View>
      }
    >
      {loading && !products.length ? (
        <View style={styles.grid}>
          {Array.from({ length: 6 }).map((_, index) => (
            <ProductCardSkeleton key={`related-grid-skeleton-${index}`} />
          ))}
        </View>
      ) : products.length ? (
        <>
          <View style={styles.grid}>
            {products.map((product) => (
              <ProductCard
                key={product.id}
                onPress={() => onProductPress(product)}
                onToggleFavorite={() => onToggleFavorite(product)}
                product={product}
              />
            ))}
          </View>
          {hasMore ? (
            <View style={styles.grabMoreRow}>
              <Pressable
                disabled={loadingMore}
                onPress={onGrabMore}
                style={[styles.grabMoreButton, loadingMore && styles.grabMoreButtonDisabled]}
              >
                {loadingMore ? <ActivityIndicator color={colors.primary} size="small" /> : null}
                <Text style={styles.grabMoreText}>{loadingMore ? "Grabbing more..." : "Grab more"}</Text>
              </Pressable>
            </View>
          ) : null}
        </>
      ) : (
        <EmptyState body="We’re fetching more related products." title="Related products are loading" />
      )}
    </ScreenShell>
  );
}

function ProfileScreen({
  user,
  history,
  recommendations,
  loadingHistory,
  loadingRecommendations,
  onOpenCatalog,
  onOpenProduct,
  onToggleFavorite,
  onOpenHistoryEntry,
  onShowMoreHistory,
  onShowMoreRecommendations,
  historyHasMore,
  recommendationsHasMore,
  onLogout,
}: {
  user: MeResponse;
  history: HistoryEntry[];
  recommendations: Product[];
  loadingHistory: boolean;
  loadingRecommendations: boolean;
  onOpenCatalog: () => void;
  onOpenProduct: (product: Product) => void;
  onToggleFavorite: (product: Product) => void;
  onOpenHistoryEntry: (entry: HistoryEntry) => void;
  onShowMoreHistory: () => void;
  onShowMoreRecommendations: () => void;
  historyHasMore: boolean;
  recommendationsHasMore: boolean;
  onLogout: () => void;
}) {
  return (
    <ScreenShell title="Profile" subtitle="Your interests, history, and personalized recommendations.">
      <View style={styles.profileHero}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{user.email.slice(0, 1).toUpperCase()}</Text>
        </View>
        <Text style={styles.profileName}>{user.email}</Text>
        <Text style={styles.profileEmail}>Member since {formatDate(user.createdAt)}</Text>
      </View>

      <View style={styles.infoCard}>
        <Text style={styles.cardTitle}>Top interests</Text>
        <View style={styles.preferenceWrap}>
          {user.topInterests.length ? (
            user.topInterests.map((interest) => (
              <View key={`${interest.type}-${interest.key}`} style={styles.preferencePill}>
                <Text style={styles.preferenceText}>{interestLabel(interest.key)}</Text>
              </View>
            ))
          ) : (
            <Text style={styles.infoBody}>Search or open a few items to personalize this profile.</Text>
          )}
        </View>
      </View>

      <View style={styles.infoCard}>
        <View style={styles.sectionHeader}>
          <Text style={styles.cardTitle}>Recent history</Text>
        </View>
        {loadingHistory && !history.length ? (
          <>
            {Array.from({ length: 3 }).map((_, index) => (
              <HistoryRowSkeleton key={`history-skeleton-${index}`} />
            ))}
          </>
        ) : history.length ? (
          <>
            {history.map((entry) => (
              <Pressable key={entry.id} onPress={() => onOpenHistoryEntry(entry)} style={styles.historyCard}>
                <Text style={styles.historyTitle}>{entry.title}</Text>
                <Text style={styles.historySubtitle}>{entry.subtitle}</Text>
                <Text style={styles.historyDate}>{formatDate(entry.createdAt)}</Text>
              </Pressable>
            ))}
            <ShowMoreButton onPress={onShowMoreHistory} visible={historyHasMore} />
          </>
        ) : (
          <EmptyState body="Your searches and item opens will show up here." title="No history yet" />
        )}
      </View>

      <View style={styles.infoCard}>
        <View style={styles.sectionHeader}>
          <Text style={styles.cardTitle}>Continue exploring</Text>
        </View>
        {loadingRecommendations && !recommendations.length ? (
          <ScrollView horizontal contentContainerStyle={styles.rowGap} showsHorizontalScrollIndicator={false}>
            {Array.from({ length: 3 }).map((_, index) => (
              <View key={`profile-rec-skeleton-${index}`} style={styles.relatedCard}>
                <SkeletonBlock height={120} width="100%" />
                <SkeletonBlock height={14} width="80%" />
                <SkeletonBlock height={14} width="36%" />
              </View>
            ))}
          </ScrollView>
        ) : recommendations.length ? (
          <>
            <ScrollView horizontal contentContainerStyle={styles.rowGap} showsHorizontalScrollIndicator={false}>
              {recommendations.map((product) => (
                <Pressable
                  key={`profile-rec-${product.id}`}
                  onPress={() => onOpenProduct(product)}
                  style={({ pressed }) => [styles.relatedCard, pressed && styles.pressedCard]}
                >
                  {offerDiscount(product) > 0 ? (
                    <View style={styles.relatedDiscountBadge}>
                      <Text style={styles.productDiscountText}>{offerDiscount(product)}% OFF</Text>
                    </View>
                  ) : null}
                  <Pressable
                    onPress={(event) => {
                      event.stopPropagation();
                      onToggleFavorite(product);
                    }}
                    style={[styles.favoriteButton, styles.favoriteButtonFloating, product.isFavorite && styles.favoriteButtonActive]}
                  >
                    <Ionicons
                      color={product.isFavorite ? colors.onPrimary : colors.textMuted}
                      name={product.isFavorite ? "heart" : "heart-outline"}
                      size={16}
                    />
                  </Pressable>
                  <ProductImage
                    imageAltText={product.imageAltText}
                    imageUrl={product.imageUrl}
                    sourceImageUrl={product.sourceImageUrl}
                    style={styles.relatedImage}
                  />
                  <Text numberOfLines={1} style={styles.relatedTitle}>
                    {product.name}
                  </Text>
                  <View style={styles.relatedPriceRow}>
                    <Text style={styles.relatedPrice}>${product.price.toFixed(2)}</Text>
                    {offerDiscount(product) > 0 && product.originalPrice ? (
                      <Text style={styles.relatedOriginalPrice}>${product.originalPrice.toFixed(2)}</Text>
                    ) : null}
                  </View>
                </Pressable>
              ))}
            </ScrollView>
            <ShowMoreButton onPress={onShowMoreRecommendations} visible={recommendationsHasMore} />
          </>
        ) : (
          <EmptyState body="Recommendations will get better as you browse." title="No profile recommendations yet" />
        )}
      </View>

      <Pressable onPress={onOpenCatalog} style={styles.secondaryButton}>
        <Text style={styles.secondaryButtonText}>Open full catalog</Text>
      </Pressable>
      <Pressable onPress={onLogout} style={styles.ghostButton}>
        <Text style={styles.ghostButtonText}>Log out</Text>
      </Pressable>
    </ScreenShell>
  );
}

function FavoritesScreen({
  items,
  loading,
  loadingMore,
  hasMore,
  onProductPress,
  onToggleFavorite,
  onShowMore,
}: {
  items: Product[];
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  onProductPress: (product: Product) => void;
  onToggleFavorite: (product: Product) => void;
  onShowMore: () => void;
}) {
  return (
    <ScreenShell>
      <View style={styles.aixHeader}>
        <View style={styles.aixBrandWrap}>
          <View style={styles.aixBrandIcon}>
            <Ionicons color={colors.onPrimary} name="heart" size={18} />
          </View>
          <View>
            <Text style={styles.aixBrandTitle}>AIXStore</Text>
            <Text style={styles.aixBrandSubtitle}>Your saved favorites</Text>
          </View>
        </View>
      </View>

      {loading && !items.length ? (
        <View style={styles.grid}>
          {Array.from({ length: 4 }).map((_, index) => (
            <ProductCardSkeleton key={`favorite-skeleton-${index}`} />
          ))}
        </View>
      ) : items.length ? (
        <>
          <View style={styles.grid}>
            {items.map((product) => (
              <ProductCard
                key={`favorite-${product.id}`}
                onPress={() => onProductPress(product)}
                onToggleFavorite={() => onToggleFavorite(product)}
                product={product}
              />
            ))}
          </View>
          <ShowMoreButton loading={loadingMore} onPress={onShowMore} visible={hasMore} />
        </>
      ) : (
        <EmptyState body="Tap the heart on any product to save it here." title="No favorites yet" />
      )}
    </ScreenShell>
  );
}

function AuthScreen({
  mode,
  email,
  password,
  loading,
  error,
  onModeChange,
  onEmailChange,
  onPasswordChange,
  onSubmit,
}: {
  mode: AuthMode;
  email: string;
  password: string;
  loading: boolean;
  error: string | null;
  onModeChange: (mode: AuthMode) => void;
  onEmailChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <ScreenShell header={<BrandHero caption="Sign in to keep your own search history, recommendations, and seller redirects." />}>
      <View style={styles.authModeRow}>
        <Pressable
          onPress={() => onModeChange("signIn")}
          style={[styles.authModeChip, mode === "signIn" && styles.authModeChipActive]}
        >
          <Text style={[styles.authModeText, mode === "signIn" && styles.authModeTextActive]}>Sign In</Text>
        </Pressable>
        <Pressable
          onPress={() => onModeChange("signUp")}
          style={[styles.authModeChip, mode === "signUp" && styles.authModeChipActive]}
        >
          <Text style={[styles.authModeText, mode === "signUp" && styles.authModeTextActive]}>Create Account</Text>
        </Pressable>
      </View>

      <View style={styles.formCard}>
        <Text style={styles.inputLabel}>Email</Text>
        <TextInput
          autoCapitalize="none"
          keyboardType="email-address"
          onChangeText={onEmailChange}
          placeholder="name@example.com"
          placeholderTextColor={colors.textMuted}
          style={styles.textInput}
          value={email}
        />
        <Text style={styles.inputLabel}>Password</Text>
        <TextInput
          onChangeText={onPasswordChange}
          placeholder="Minimum 6 characters"
          placeholderTextColor={colors.textMuted}
          secureTextEntry
          style={styles.textInput}
          value={password}
        />
        {error ? <Text style={styles.formError}>{error}</Text> : null}
        <Pressable disabled={loading} onPress={onSubmit} style={styles.primaryButton}>
          <Text style={styles.primaryButtonText}>{loading ? "Working..." : mode === "signIn" ? "Sign In" : "Create Account"}</Text>
        </Pressable>
      </View>
    </ScreenShell>
  );
}

function BottomTab({
  active,
  icon,
  label,
  onPress,
}: {
  active: boolean;
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable onPress={onPress} style={styles.tabButton}>
      <Ionicons color={active ? colors.primary : colors.textMuted} name={icon} size={20} />
      <Text style={[styles.tabLabel, active && styles.tabLabelActive]}>{label}</Text>
    </Pressable>
  );
}

export default function App() {
  const catalogPageSize = 20;
  const historyPageSize = 10;
  const recommendationPageSize = 8;
  const relatedPageSize = 12;
  const favoritesPageSize = 20;
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [authMode, setAuthMode] = useState<AuthMode>("signIn");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<MeResponse | null>(null);
  const [currentTab, setCurrentTab] = useState<TabKey>("home");
  const [viewMode, setViewMode] = useState<ViewMode>("home");
  const [catalogHydrated, setCatalogHydrated] = useState(false);
  const [categories, setCategories] = useState<Category[]>([]);
  const [offerProducts, setOfferProducts] = useState<Product[]>([]);
  const [catalogContexts, setCatalogContexts] = useState<Record<string, CatalogContextState>>({
    home: buildCatalogContext("home", "home", null, null),
  });
  const [activeCatalogContextKey, setActiveCatalogContextKey] = useState("home");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchInputValue, setSearchInputValue] = useState("");
  const [searchRefreshSignal, setSearchRefreshSignal] = useState(0);
  const [selectedCategoryId, setSelectedCategoryId] = useState<string | null>(null);
  const [catalogScrollToTopSignal, setCatalogScrollToTopSignal] = useState(0);
  const [recommendations, setRecommendations] = useState<Product[]>([]);
  const [homeTrendingProducts, setHomeTrendingProducts] = useState<Product[]>([]);
  const [recommendationPage, setRecommendationPage] = useState(1);
  const [recommendationHasMore, setRecommendationHasMore] = useState(false);
  const [recommendationBasedOn, setRecommendationBasedOn] = useState<string[]>([]);
  const [loadingRecommendations, setLoadingRecommendations] = useState(false);
  const [loadingMoreRecommendations, setLoadingMoreRecommendations] = useState(false);
  const [historyItems, setHistoryItems] = useState<HistoryEntry[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingMoreHistory, setLoadingMoreHistory] = useState(false);
  const [favorites, setFavorites] = useState<Product[]>([]);
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(new Set());
  const [favoritesPage, setFavoritesPage] = useState(1);
  const [favoritesHasMore, setFavoritesHasMore] = useState(false);
  const [loadingFavorites, setLoadingFavorites] = useState(false);
  const [loadingMoreFavorites, setLoadingMoreFavorites] = useState(false);
  const [selectedProductSummary, setSelectedProductSummary] = useState<Product | null>(null);
  const [selectedProductDetail, setSelectedProductDetail] = useState<ProductDetail | null>(null);
  const [loadingProductDetail, setLoadingProductDetail] = useState(false);
  const [relatedProducts, setRelatedProducts] = useState<Product[]>([]);
  const [relatedPage, setRelatedPage] = useState(1);
  const [relatedHasMore, setRelatedHasMore] = useState(false);
  const [loadingRelated, setLoadingRelated] = useState(false);
  const [loadingMoreRelated, setLoadingMoreRelated] = useState(false);
  const catalogCacheRef = useRef(new Map<string, CatalogListResponse>());
  const historyCacheRef = useRef(new Map<string, HistoryResponse>());
  const favoritesCacheRef = useRef(new Map<string, FavoritesResponse>());
  const recommendationsCacheRef = useRef(new Map<string, RecommendationResponse>());
  const relatedCacheRef = useRef(new Map<string, RelatedProductsResponse>());
  const detailCacheRef = useRef(new Map<string, ProductDetail>());
  const controllersRef = useRef<Partial<Record<RequestArea, AbortController>>>({});
  const requestIdsRef = useRef<Record<RequestArea, number>>({
    auth: 0,
    bootstrap: 0,
    catalog: 0,
    detail: 0,
    related: 0,
    recommendations: 0,
    history: 0,
    favorites: 0,
  });
  const lastLoggedSearchRef = useRef("");
  const historyNeedsRefreshRef = useRef(false);

  const activeCatalogContext =
    catalogContexts[activeCatalogContextKey] ??
    buildCatalogContext(
      activeCatalogContextKey,
      activeCatalogContextKey === "home" ? "home" : searchQuery.trim() ? "search" : "category",
      searchQuery.trim() || null,
      selectedCategoryId,
    );
  const selectedProduct = selectedProductDetail ?? selectedProductSummary;
  const catalogVisibleProducts =
    activeCatalogContext.contextKey === "home"
      ? pickDiverseProducts(activeCatalogContext.items, activeCatalogContext.items.length)
      : activeCatalogContext.items;
  const homeRecommendations = recommendations.slice(0, 6);
  const trendingProducts = homeTrendingProducts;

  function isFavoriteProduct(product: Product | null | undefined) {
    if (!product?.id) {
      return false;
    }
    return favoriteIds.has(product.id) || Boolean(product.isFavorite);
  }

  function mergeFavoriteIds(products: Product[]) {
    const nextIds = new Set<string>();
    for (const product of products) {
      if (product?.id && (product.isFavorite || favoriteIds.has(product.id))) {
        nextIds.add(product.id);
      }
    }
    setFavoriteIds((current) => {
      const merged = new Set(current);
      nextIds.forEach((item) => merged.add(item));
      return merged;
    });
  }

  function markProductsFavoriteState(products: Product[], isFavorite: boolean, productId: string) {
    return products.map((product) =>
      product.id === productId ? { ...product, isFavorite } : product
    );
  }

  function applyFavoriteState(productId: string, isFavorite: boolean) {
    setFavoriteIds((current) => {
      const next = new Set(current);
      if (isFavorite) {
        next.add(productId);
      } else {
        next.delete(productId);
      }
      return next;
    });
    setFavorites((current) =>
      isFavorite ? current : current.filter((product) => product.id !== productId)
    );
    setRecommendations((current) => markProductsFavoriteState(current, isFavorite, productId));
    setHomeTrendingProducts((current) => markProductsFavoriteState(current, isFavorite, productId));
    setRelatedProducts((current) => markProductsFavoriteState(current, isFavorite, productId));
    setOfferProducts((current) => markProductsFavoriteState(current, isFavorite, productId));
    setSelectedProductSummary((current) => (current?.id === productId ? { ...current, isFavorite } : current));
    setSelectedProductDetail((current) =>
      current?.id === productId
        ? { ...current, isFavorite, relatedProducts: markProductsFavoriteState(current.relatedProducts, isFavorite, productId) }
        : current
    );
    setCatalogContexts((current) => {
      const next: Record<string, CatalogContextState> = {};
      Object.entries(current).forEach(([contextKey, context]) => {
        next[contextKey] = {
          ...context,
          items: markProductsFavoriteState(context.items, isFavorite, productId),
        };
      });
      return next;
    });
    detailCacheRef.current.forEach((detail, key) => {
      const nextDetail: ProductDetail = {
        ...detail,
        isFavorite: key === productId ? isFavorite : detail.isFavorite,
        relatedProducts: markProductsFavoriteState(detail.relatedProducts, isFavorite, productId),
      };
      detailCacheRef.current.set(key, nextDetail);
    });
  }

  function abortArea(area: RequestArea) {
    controllersRef.current[area]?.abort();
  }

  function setCatalogContextState(
    contextKey: string,
    updater: (current: CatalogContextState) => CatalogContextState,
  ) {
    setCatalogContexts((current) => {
      const existing =
        current[contextKey] ??
        buildCatalogContext(
          contextKey,
          contextKey === "home" ? "home" : contextKey.startsWith("category::") ? "category" : "search",
          contextKey.startsWith("search::") ? contextKey.split("::").slice(2).join("::") : null,
          contextKey.startsWith("category::") ? contextKey.split("::")[1] : null,
        );
      return { ...current, [contextKey]: updater(existing) };
    });
  }

  function clearUserScopedCaches() {
    recommendationsCacheRef.current.clear();
    historyCacheRef.current.clear();
    favoritesCacheRef.current.clear();
    relatedCacheRef.current.clear();
    detailCacheRef.current.clear();
    setRecommendations([]);
    setHomeTrendingProducts([]);
    setRecommendationPage(1);
    setRecommendationHasMore(false);
    setRecommendationBasedOn([]);
    setHistoryItems([]);
    setHistoryPage(1);
    setHistoryHasMore(false);
    setFavorites([]);
    setFavoriteIds(new Set());
    setFavoritesPage(1);
    setFavoritesHasMore(false);
    setRelatedProducts([]);
    setRelatedPage(1);
    setRelatedHasMore(false);
    historyNeedsRefreshRef.current = false;
  }

  function resetDetailNavigation(nextView: ViewMode = currentTab) {
    setSelectedProductSummary(null);
    setSelectedProductDetail(null);
    setRelatedProducts([]);
    setRelatedPage(1);
    setRelatedHasMore(false);
    setViewMode(nextView);
  }

  async function fireUserEvent(payload: {
    type: string;
    productId?: string | null;
    categoryId?: string | null;
    queryText?: string | null;
    sourceUrl?: string | null;
    metadata?: Record<string, unknown>;
  }) {
    if (!sessionToken) {
      return;
    }
    try {
      await postUserEvent(sessionToken, payload);
      if (payload.type === "search" || payload.type === "product_view") {
        historyNeedsRefreshRef.current = true;
        historyCacheRef.current.clear();
        void loadHistoryPage(1);
      }
      if (payload.type === "search" || payload.type === "product_view" || payload.type === "category_view" || payload.type === "source_open") {
        recommendationsCacheRef.current.clear();
        void loadRecommendationsPage(1);
      }
    } catch {
      // Ignore non-critical telemetry failures.
    }
  }

  async function loadRecommendationsPage(page: number, append = false) {
    if (!currentUser || !sessionToken) {
      return;
    }
    const key = recommendationCacheKey(currentUser.id, page);
    const cached = recommendationsCacheRef.current.get(key);
    if (page === 1 && cached) {
      mergeFavoriteIds([...cached.items, ...(cached.trending || [])]);
      setRecommendations(cached.items);
      setHomeTrendingProducts(cached.trending || []);
      setRecommendationPage(cached.page);
      setRecommendationHasMore(cached.hasMore);
      setRecommendationBasedOn(cached.basedOn);
      setLoadingRecommendations(false);
    }

    const area: RequestArea = "recommendations";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    if (append) {
      setLoadingMoreRecommendations(true);
    } else if (!cached) {
      setLoadingRecommendations(true);
    }

    try {
      const payload = await getRecommendations(sessionToken, {
        page,
        pageSize: recommendationPageSize,
        signal: controller.signal,
      });
      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      recommendationsCacheRef.current.set(key, payload);
      mergeFavoriteIds([...payload.items, ...(payload.trending || [])]);
      setRecommendationBasedOn(payload.basedOn);
      setRecommendationHasMore(payload.hasMore);
      setRecommendationPage(payload.page);
      if (!append) {
        setHomeTrendingProducts(payload.trending || []);
      }
      setRecommendations((current) => (append ? uniqueProducts([...current, ...payload.items]) : payload.items));
    } catch (error) {
      if (!(error instanceof Error) || error.name !== "AbortError") {
        setRecommendationHasMore(false);
      }
    } finally {
      if (requestIdsRef.current[area] === requestId) {
        setLoadingRecommendations(false);
        setLoadingMoreRecommendations(false);
      }
    }
  }

  async function loadHistoryPage(page: number, append = false) {
    if (!currentUser || !sessionToken) {
      return;
    }
    const key = historyCacheKey(currentUser.id, page);
    const cached = historyCacheRef.current.get(key);
    if (page === 1 && cached) {
      setHistoryItems(cached.items);
      setHistoryPage(cached.page);
      setHistoryHasMore(cached.hasMore);
      setLoadingHistory(false);
    }

    const area: RequestArea = "history";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    if (append) {
      setLoadingMoreHistory(true);
    } else if (!cached) {
      setLoadingHistory(true);
    }

    try {
      const payload = await getHistory(sessionToken, {
        page,
        pageSize: historyPageSize,
        signal: controller.signal,
      });
      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      historyCacheRef.current.set(key, payload);
      setHistoryPage(payload.page);
      setHistoryHasMore(payload.hasMore);
      setHistoryItems((current) => (append ? [...current, ...payload.items] : payload.items));
      if (page === 1) {
        historyNeedsRefreshRef.current = false;
      }
    } catch (error) {
      if (!(error instanceof Error) || error.name !== "AbortError") {
        setHistoryHasMore(false);
      }
    } finally {
      if (requestIdsRef.current[area] === requestId) {
        setLoadingHistory(false);
        setLoadingMoreHistory(false);
      }
    }
  }

  async function loadFavoritesPage(page: number, append = false) {
    if (!currentUser || !sessionToken) {
      return;
    }
    const key = favoritesCacheKey(currentUser.id, page);
    const cached = favoritesCacheRef.current.get(key);
    if (page === 1 && cached) {
      setFavorites(cached.items);
      setFavoritesPage(cached.page);
      setFavoritesHasMore(cached.hasMore);
      mergeFavoriteIds(cached.items);
      setLoadingFavorites(false);
    }

    const area: RequestArea = "favorites";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    if (append) {
      setLoadingMoreFavorites(true);
    } else if (!cached) {
      setLoadingFavorites(true);
    }

    try {
      const payload = await getFavorites(sessionToken, {
        page,
        pageSize: favoritesPageSize,
        signal: controller.signal,
      });
      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      favoritesCacheRef.current.set(key, payload);
      mergeFavoriteIds(payload.items);
      setFavoritesPage(payload.page);
      setFavoritesHasMore(payload.hasMore);
      setFavorites((current) => (append ? uniqueProducts([...current, ...payload.items]) : payload.items));
    } catch (error) {
      if (!(error instanceof Error) || error.name !== "AbortError") {
        setFavoritesHasMore(false);
      }
    } finally {
      if (requestIdsRef.current[area] === requestId) {
        setLoadingFavorites(false);
        setLoadingMoreFavorites(false);
      }
    }
  }

  async function loadRelatedPage(productId: string, page: number, append = false) {
    const area: RequestArea = "related";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    if (append) {
      setLoadingMoreRelated(true);
    } else {
      setRelatedProducts([]);
      setRelatedPage(1);
      setRelatedHasMore(false);
      setLoadingRelated(true);
    }

    try {
      const payload = await getRelatedProducts(productId, {
        token: sessionToken,
        page,
        pageSize: relatedPageSize,
        signal: controller.signal,
      });
      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      mergeFavoriteIds(payload.items);
      setRelatedPage(payload.page);
      setRelatedHasMore(payload.hasMore);
      setRelatedProducts((current) => (append ? uniqueProducts([...current, ...payload.items]) : payload.items));
    } catch (error) {
      if (!(error instanceof Error) || error.name !== "AbortError") {
        setRelatedHasMore(false);
      }
    } finally {
      if (requestIdsRef.current[area] === requestId) {
        setLoadingRelated(false);
        setLoadingMoreRelated(false);
      }
    }
  }

  async function loadCatalogPage(query: string, categoryId: string | null, page: number, append = false) {
    const normalizedQuery = query.trim();
    const expectedContextKey = expectedCatalogContextKey(normalizedQuery, categoryId);
    const key = catalogCacheKey(expectedContextKey, page);
    const cached = catalogCacheRef.current.get(key);
    const contextType: CatalogContextType = normalizedQuery ? "search" : categoryId ? "category" : "home";

    if (page === 1 && cached) {
      mergeFavoriteIds([...cached.items, ...cached.offers]);
      setCategories(cached.categories);
      setOfferProducts(pickOfferProducts(cached.offers, cached.items));
      setCatalogContextState(expectedContextKey, (current) => ({
        ...current,
        contextKey: cached.contextKey,
        contextType: cached.contextType,
        query: normalizedQuery || null,
        categoryId,
        items: cached.items,
        page: cached.page,
        hasMore: cached.hasMore,
        loadingInitial: false,
        loadingMore: false,
        error: null,
        enrichment: cached.enrichment,
        loadedPages: [cached.page],
      }));
      if (contextType === "search") {
        setCatalogScrollToTopSignal((current) => current + 1);
      }
    }

    setCatalogContextState(expectedContextKey, (current) => ({
      ...current,
      contextKey: expectedContextKey,
      contextType,
      query: normalizedQuery || null,
      categoryId,
      loadingInitial: append ? current.loadingInitial : !cached,
      loadingMore: append,
      error: null,
    }));

    const area: RequestArea = "catalog";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    const fallbackQuery = !normalizedQuery ? fallbackCategoryQuery(categoryId) : "";

    try {
      const primaryRequest = normalizedQuery
        ? searchCatalog(normalizedQuery, {
            categoryId,
            page,
            pageSize: catalogPageSize,
            token: sessionToken,
            signal: controller.signal,
            timeoutMs: SEARCH_REQUEST_TIMEOUT_MS,
          })
        : listCatalog({
            categoryId,
            page,
            pageSize: catalogPageSize,
            token: sessionToken,
            signal: controller.signal,
          });
      const payload =
        fallbackQuery && !append
          ? await withTimeout(primaryRequest, CATEGORY_PRIMARY_TIMEOUT_MS, "Category request timed out")
          : await primaryRequest;

      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      if (payload.contextKey !== expectedContextKey) {
        return;
      }

      catalogCacheRef.current.set(key, payload);
      mergeFavoriteIds([...payload.items, ...payload.offers]);
      setCategories(payload.categories);
      const baseItems = append
        ? collectCatalogItemsFromCache(
            catalogCacheRef.current,
            expectedContextKey,
            page,
            catalogContexts[expectedContextKey]?.items ?? [],
          )
        : [];
      const nextCatalogItems = append ? uniqueProducts([...baseItems, ...payload.items]) : payload.items;
      setOfferProducts(pickOfferProducts(payload.offers, nextCatalogItems));
      setCatalogContextState(expectedContextKey, (current) => {
        return {
          ...current,
          contextKey: payload.contextKey,
          contextType: payload.contextType,
          query: payload.appliedQuery,
          categoryId: payload.appliedCategoryId,
          items: nextCatalogItems,
          page: payload.page,
          hasMore: payload.hasMore,
          loadingInitial: false,
          loadingMore: false,
          error: null,
          enrichment: payload.enrichment,
          loadedPages: append ? Array.from(new Set([...current.loadedPages, payload.page])) : [payload.page],
        };
      });
      if (!append && payload.contextType === "search") {
        setCatalogScrollToTopSignal((current) => current + 1);
      }

      if (normalizedQuery && sessionToken && page === 1 && lastLoggedSearchRef.current !== normalizedQuery.toLowerCase()) {
        lastLoggedSearchRef.current = normalizedQuery.toLowerCase();
        void fireUserEvent({ type: "search", categoryId, queryText: normalizedQuery });
      }
    } catch (error) {
      if (!(error instanceof Error) || error.name !== "AbortError") {
        if (fallbackQuery) {
          try {
            const payload = await searchCatalog(fallbackQuery, {
              categoryId,
              page,
              pageSize: catalogPageSize,
              token: sessionToken,
              signal: controller.signal,
            });
            if (requestIdsRef.current[area] !== requestId) {
              return;
            }
            catalogCacheRef.current.set(key, payload);
            mergeFavoriteIds([...payload.items, ...payload.offers]);
            setCategories(payload.categories);
            const baseItems = append
              ? collectCatalogItemsFromCache(
                  catalogCacheRef.current,
                  expectedContextKey,
                  page,
                  catalogContexts[expectedContextKey]?.items ?? [],
                )
              : [];
            const nextCatalogItems = append ? uniqueProducts([...baseItems, ...payload.items]) : payload.items;
            setOfferProducts(pickOfferProducts(payload.offers, nextCatalogItems));
            setCatalogContextState(expectedContextKey, (current) => ({
              ...current,
              contextKey: expectedContextKey,
              contextType: "category",
              query: null,
              categoryId,
              items: nextCatalogItems,
              page: payload.page,
              hasMore: payload.hasMore,
              loadingInitial: false,
              loadingMore: false,
              error: null,
              enrichment: {
                ...(payload.enrichment || defaultEnrichmentState),
                state: "error",
                message: `Using fallback ${fallbackQuery} results while the live category feed recovers.`,
              },
              loadedPages: append ? Array.from(new Set([...current.loadedPages, payload.page])) : [payload.page],
            }));
            return;
          } catch {
            // Fall through to the generic live request error below.
          }
        }
        setCatalogContextState(expectedContextKey, (current) => ({
          ...current,
          loadingInitial: false,
          loadingMore: false,
          error: "The live catalog request failed. Check that the scraper runtime is still running.",
        }));
      }
    } finally {
      if (requestIdsRef.current[area] === requestId) {
        setCatalogContextState(expectedContextKey, (current) => ({
          ...current,
          loadingInitial: false,
          loadingMore: false,
        }));
      }
    }
  }

  async function openProduct(product: Product, originSurface: ProductOriginSurface = "unknown") {
    const summaryProduct = {
      ...product,
      isFavorite: isFavoriteProduct(product),
      imageGallery: imageGalleryForProduct(product),
    };
    setRelatedProducts([]);
    setRelatedPage(1);
    setRelatedHasMore(false);
    setSelectedProductSummary(summaryProduct);
    const cachedDetail = detailCacheRef.current.get(product.id) ?? null;
    setSelectedProductDetail(cachedDetail ? { ...cachedDetail, relatedProducts: [] } : null);
    setViewMode("detail");
    setLoadingProductDetail(true);
    if (originSurface === "home" || originSurface === "catalog" || originSurface === "favorites") {
      void fireUserEvent({
        type: "product_view",
        productId: product.id,
        categoryId: product.categoryId,
        metadata: { sourceSite: product.sourceSite, originSurface },
      });
    }
    if (cachedDetail) {
      mergeFavoriteIds([cachedDetail]);
    }

    const area: RequestArea = "detail";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    try {
      const payload = await getProductDetail(product.id, {
        token: sessionToken,
        signal: controller.signal,
      });
      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      detailCacheRef.current.set(product.id, { ...payload, relatedProducts: [] });
      setSelectedProductDetail(payload);
      mergeFavoriteIds([payload, ...(payload.relatedProducts || [])]);
    } catch {
      // Keep the summary detail view visible if live enrichment fails.
    } finally {
      if (requestIdsRef.current[area] === requestId) {
        setLoadingProductDetail(false);
      }
    }
  }

  async function selectProductVariant(variant: ProductVariantSummary) {
    const currentProduct = selectedProductDetail ?? selectedProductSummary;
    if (!currentProduct) {
      return;
    }
    const immediateProduct = productFromVariantSummary(currentProduct, variant);
    setSelectedProductSummary(immediateProduct);
    setSelectedProductDetail((current) =>
      current
        ? {
            ...current,
            ...immediateProduct,
            imageGallery: variant.imageGallery,
            variantOptions: current.variantOptions.map((option) => ({
              ...option,
              isCurrent: option.productId === variant.productId,
            })),
          }
        : null,
    );

    const cachedDetail = detailCacheRef.current.get(variant.productId);
    if (cachedDetail) {
      setSelectedProductDetail({ ...cachedDetail, relatedProducts: [] });
      setSelectedProductSummary(cachedDetail);
      mergeFavoriteIds([cachedDetail]);
    }

    const area: RequestArea = "detail";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;
    setLoadingProductDetail(true);

    try {
      const payload = await getProductDetail(variant.productId, {
        token: sessionToken,
        signal: controller.signal,
      });
      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      detailCacheRef.current.set(variant.productId, { ...payload, relatedProducts: [] });
      setSelectedProductSummary(payload);
      setSelectedProductDetail(payload);
      mergeFavoriteIds([payload, ...(payload.relatedProducts || [])]);
    } catch {
      // Keep the currently displayed variant summary visible if live enrichment fails.
    } finally {
      if (requestIdsRef.current[area] === requestId) {
        setLoadingProductDetail(false);
      }
    }
  }

  async function toggleFavorite(product: Product) {
    if (!sessionToken) {
      Alert.alert("Sign in required", "Sign in to save favorites.");
      return;
    }
    const nextState = !isFavoriteProduct(product);
    applyFavoriteState(product.id, nextState);
    try {
      if (nextState) {
        const saved = await addFavorite(sessionToken, product.id);
        applyFavoriteState(product.id, true);
        setFavorites((current) => uniqueProducts([saved, ...current]));
      } else {
        await removeFavorite(sessionToken, product.id);
      }
      favoritesCacheRef.current.clear();
      if (currentTab === "favorites" || viewMode === "favorites") {
        void loadFavoritesPage(1);
      }
    } catch (error) {
      applyFavoriteState(product.id, !nextState);
      Alert.alert("Favorite update failed", error instanceof Error ? error.message : "Try again in a moment.");
    }
  }

  async function openProductSource(product: Product) {
    if (!product.sourceUrl) {
      Alert.alert("Missing source", "This product does not have a source link yet.");
      return;
    }

    try {
      const canOpen = await Linking.canOpenURL(product.sourceUrl);
      if (!canOpen) {
        Alert.alert("Cannot open link", "This product link could not be opened on this device.");
        return;
      }
      void fireUserEvent({
        type: "source_open",
        productId: product.id,
        categoryId: product.categoryId,
        sourceUrl: product.sourceUrl,
        metadata: { sourceSite: product.sourceSite },
      });
      await Linking.openURL(product.sourceUrl);
    } catch {
      Alert.alert("Open failed", "The store page could not be opened. Try again in a moment.");
    }
  }

  async function restoreSession() {
    const area: RequestArea = "auth";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    try {
      const storedToken = await readSessionToken();
      if (!storedToken) {
        setAuthState("signedOut");
        return;
      }
      const user = await getMe(storedToken, controller.signal);
      if (requestIdsRef.current[area] !== requestId) {
        return;
      }
      setSessionToken(storedToken);
      setCurrentUser(user);
      setAuthState("signedIn");
      setCurrentTab("home");
      setViewMode("home");
    } catch {
      await clearSessionToken();
      setSessionToken(null);
      setCurrentUser(null);
      setAuthState("signedOut");
    }
  }

  async function submitAuth() {
    if (!email.trim() || !password.trim()) {
      setAuthError("Enter both an email and password.");
      return;
    }
    setAuthSubmitting(true);
    setAuthError(null);
    try {
      const response: AuthResponse =
        authMode === "signIn" ? await signIn(email.trim(), password) : await signUp(email.trim(), password);
      await saveSessionToken(response.token);
      setSessionToken(response.token);
      setCurrentUser(response.user);
      setAuthState("signedIn");
      setCurrentTab("home");
      setViewMode("home");
      setPassword("");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Authentication failed.");
    } finally {
      setAuthSubmitting(false);
    }
  }

  async function handleLogout() {
    try {
      if (sessionToken) {
        await signOut(sessionToken);
      }
    } catch {
      // Continue logout even if the remote session call fails.
    }
    await clearSessionToken();
    setSessionToken(null);
    setCurrentUser(null);
    setAuthState("signedOut");
    setCurrentTab("home");
    setViewMode("home");
    setCatalogHydrated(false);
    setSearchQuery("");
    setSelectedCategoryId(null);
    clearUserScopedCaches();
    resetDetailNavigation("home");
  }

  useEffect(() => {
    void restoreSession();
    return () => {
      (Object.keys(controllersRef.current) as RequestArea[]).forEach((area) => abortArea(area));
    };
  }, []);

  useEffect(() => {
    if (authState !== "signedIn") {
      return;
    }
    const contextKey = "home";
    const key = catalogCacheKey(contextKey, 1);
    const cached = catalogCacheRef.current.get(key);
    if (cached) {
      setCategories(cached.categories);
      setOfferProducts(pickOfferProducts(cached.offers, cached.items));
      setCatalogContextState(contextKey, (current) => ({
        ...current,
        contextKey: cached.contextKey,
        contextType: cached.contextType,
        query: null,
        categoryId: null,
        items: cached.items,
        page: cached.page,
        hasMore: cached.hasMore,
        loadingInitial: false,
        loadingMore: false,
        error: null,
        enrichment: cached.enrichment,
        loadedPages: [cached.page],
      }));
      setCatalogHydrated(true);
    }

    const area: RequestArea = "bootstrap";
    const requestId = ++requestIdsRef.current[area];
    abortArea(area);
    const controller = new AbortController();
    controllersRef.current[area] = controller;

    if (!cached) {
      setCatalogContextState(contextKey, (current) => ({ ...current, loadingInitial: true, error: null }));
    }

    bootstrapCatalog(100, { token: sessionToken, signal: controller.signal })
      .then((payload) => {
        if (requestIdsRef.current[area] !== requestId) {
          return;
        }
        catalogCacheRef.current.set(key, payload);
        mergeFavoriteIds([...payload.items, ...payload.offers]);
        setCategories(payload.categories);
        setOfferProducts(pickOfferProducts(payload.offers, payload.items));
        setCatalogContextState(contextKey, (current) => ({
          ...current,
          contextKey: payload.contextKey,
          contextType: payload.contextType,
          query: null,
          categoryId: null,
          items: payload.items,
          page: payload.page,
          hasMore: payload.hasMore,
          loadingInitial: false,
          loadingMore: false,
          error: null,
          enrichment: payload.enrichment,
          loadedPages: [payload.page],
        }));
        setCatalogHydrated(true);
      })
      .catch((error) => {
        if (!(error instanceof Error) || error.name !== "AbortError") {
          setCatalogContextState(contextKey, (current) => ({
            ...current,
            loadingInitial: false,
            error: "Live products could not be loaded. Start the app with the scraper runtime and try again.",
          }));
          setCatalogHydrated(true);
        }
      })
      .finally(() => {
        if (requestIdsRef.current[area] === requestId) {
          setCatalogContextState(contextKey, (current) => ({ ...current, loadingInitial: false }));
        }
      });
  }, [authState]);

  useEffect(() => {
    if (authState !== "signedIn" || !catalogHydrated) {
      return;
    }
    const normalizedQuery = searchQuery.trim();
    const nextContextKey = expectedCatalogContextKey(normalizedQuery, selectedCategoryId);
    setActiveCatalogContextKey(nextContextKey);
    void loadCatalogPage(normalizedQuery, selectedCategoryId, 1);
  }, [authState, catalogHydrated, searchQuery, searchRefreshSignal, selectedCategoryId]);

  useEffect(() => {
    if (authState !== "signedIn" || !currentUser) {
      return;
    }
    void loadRecommendationsPage(1);
    void loadHistoryPage(1);
    void loadFavoritesPage(1);
  }, [authState, currentUser?.id]);

  function openTab(nextTab: TabKey) {
    setCurrentTab(nextTab);
    if (nextTab === "home") {
      setActiveCatalogContextKey("home");
    } else if (nextTab === "catalog") {
      setActiveCatalogContextKey(expectedCatalogContextKey(searchQuery, selectedCategoryId));
    } else if (nextTab === "favorites" && currentUser && !favorites.length && !loadingFavorites) {
      void loadFavoritesPage(1);
    } else if (nextTab === "profile" && currentUser && !loadingHistory && (historyNeedsRefreshRef.current || !historyItems.length)) {
      void loadHistoryPage(1);
    }
    resetDetailNavigation(nextTab);
  }

  function handleSearchChange(value: string) {
    setSearchInputValue(value);
  }

  function handleSearchSubmit() {
    console.log("[AIXStore Search] handleSearchSubmit", {
      searchInputValue,
      searchQuery,
      selectedCategoryId,
      authState,
      catalogHydrated,
    });
    const nextQuery = searchInputValue.trim();
    setSearchRefreshSignal((current) => current + 1);
    setSearchQuery(nextQuery);
    if (!nextQuery) {
      setActiveCatalogContextKey(expectedCatalogContextKey("", selectedCategoryId));
      return;
    }
    setSelectedCategoryId(null);
    setActiveCatalogContextKey(expectedCatalogContextKey(nextQuery, null));
    setCurrentTab("catalog");
    resetDetailNavigation("catalog");
  }

  function handleCategoryPress(categoryId: string) {
    setSelectedCategoryId(categoryId);
    setSearchQuery("");
    setActiveCatalogContextKey(expectedCatalogContextKey("", categoryId));
    setCurrentTab("catalog");
    resetDetailNavigation("catalog");
    void fireUserEvent({ type: "category_view", categoryId });
  }

  function handleClearCategory() {
    setSelectedCategoryId(null);
    setSearchQuery("");
    setActiveCatalogContextKey("home");
    setCurrentTab("catalog");
    resetDetailNavigation("catalog");
  }

  function handleShowMoreCatalog() {
    if (
      activeCatalogContext.contextType === "search"
      || activeCatalogContext.loadingInitial
      || activeCatalogContext.loadingMore
      || !activeCatalogContext.hasMore
    ) {
      return;
    }
    const nextPage = activeCatalogContext.page + 1;
    void loadCatalogPage(activeCatalogContext.query || "", activeCatalogContext.categoryId, nextPage, true);
  }

  function handleShowMoreRecommendations() {
    if ((viewMode === "home" || viewMode === "profile") && recommendationHasMore) {
      const nextPage = recommendationPage + 1;
      void loadRecommendationsPage(nextPage, true);
    }
  }

  function handleShowMoreHistory() {
    if (!historyHasMore) {
      return;
    }
    const nextPage = historyPage + 1;
    void loadHistoryPage(nextPage, true);
  }

  function handleShowMoreFavorites() {
    if (!favoritesHasMore) {
      return;
    }
    const nextPage = favoritesPage + 1;
    void loadFavoritesPage(nextPage, true);
  }

  function openRelatedScreen() {
    if (!selectedProduct) {
      return;
    }
    setViewMode("related");
    void loadRelatedPage(selectedProduct.id, 1);
  }

  function handleGrabMoreRelated() {
    if (!selectedProduct || !relatedHasMore || loadingMoreRelated) {
      return;
    }
    const nextPage = relatedPage + 1;
    void loadRelatedPage(selectedProduct.id, nextPage, true);
  }

  function openHistoryEntry(entry: HistoryEntry) {
    if (entry.type === "search" && entry.queryText) {
      setSelectedCategoryId(null);
      setSearchQuery(entry.queryText);
      setCurrentTab("catalog");
      resetDetailNavigation("catalog");
      return;
    }
    if (entry.type === "product_view") {
      const snapshotProduct = snapshotToProduct(
        entry.productSnapshot && typeof entry.productSnapshot === "object" ? entry.productSnapshot : null,
      );
      if (snapshotProduct) {
        void openProduct(snapshotProduct, "history");
        return;
      }
      if (entry.productId) {
        void openProduct({
          id: entry.productId,
          slug: entry.productId,
          provider: "Archived",
          name: entry.title,
          categoryId: entry.categoryId || "others",
          category: entry.subtitle || "Product",
          description: entry.title,
          price: 0,
          originalPrice: null,
          currency: "USD",
          rating: 0,
          imageUrl: "",
          imageAltText: entry.title,
          reviewCount: 0,
          hasReviews: false,
          tags: [],
          sourceSite: "Archived",
          sourceUrl: entry.sourceUrl || entry.canonicalSourceUrl || "",
        }, "history");
      }
    }
  }

  const renderContent = () => {
    if (authState === "loading") {
      return (
        <ScreenShell header={<BrandHero caption="Restoring your AIXStore session." compact />}>
          <View style={styles.loadingCard}>
            <SkeletonBlock height={20} width="55%" />
            <SkeletonBlock height={16} width="82%" />
            <SkeletonBlock height={16} width="70%" />
          </View>
        </ScreenShell>
      );
    }

    if (authState === "signedOut") {
      return (
        <AuthScreen
          email={email}
          error={authError}
          loading={authSubmitting}
          mode={authMode}
          onEmailChange={setEmail}
          onModeChange={setAuthMode}
          onPasswordChange={setPassword}
          onSubmit={submitAuth}
          password={password}
        />
      );
    }

    if (selectedProduct && viewMode === "detail") {
      return (
        <ProductDetailScreen
          loadingExtras={loadingProductDetail}
          onBack={() => resetDetailNavigation(currentTab)}
          onOpenRelated={openRelatedScreen}
          onOpenSource={openProductSource}
          onProductPress={(product) => void openProduct(product, "detail_related")}
          onSelectVariant={selectProductVariant}
          onToggleFavorite={toggleFavorite}
          product={selectedProduct}
          relatedProducts={(selectedProductDetail?.relatedProducts ?? []).slice(0, 6)}
          reviews={selectedProductDetail?.reviews ?? []}
          variantOptions={selectedProductDetail?.variantOptions ?? []}
        />
      );
    }

    if (selectedProduct && viewMode === "related") {
      return (
        <RelatedScreen
          hasMore={relatedHasMore}
          loading={loadingRelated}
          loadingMore={loadingMoreRelated}
          onBack={() => setViewMode("detail")}
          onGrabMore={handleGrabMoreRelated}
          onProductPress={(product) => void openProduct(product, "recommended")}
          onToggleFavorite={toggleFavorite}
          products={relatedProducts}
        />
      );
    }

    if (viewMode === "catalog") {
      return (
        <CatalogScreen
          categories={categories}
          enrichment={activeCatalogContext.enrichment}
          error={activeCatalogContext.error}
          hasMore={activeCatalogContext.contextType === "search" ? false : activeCatalogContext.hasMore}
          loading={activeCatalogContext.loadingInitial}
          loadingMore={activeCatalogContext.loadingMore}
          onCategoryPress={handleCategoryPress}
          onClearCategory={handleClearCategory}
          onProductPress={(product) => void openProduct(product, "catalog")}
          onSearchChange={handleSearchChange}
          onSearchSubmit={handleSearchSubmit}
          onShowMore={handleShowMoreCatalog}
          onToggleFavorite={toggleFavorite}
          products={catalogVisibleProducts}
          scrollToTopSignal={catalogScrollToTopSignal}
          searchQuery={searchInputValue}
          selectedCategoryId={selectedCategoryId}
        />
      );
    }

    if (viewMode === "favorites") {
      return (
        <FavoritesScreen
          hasMore={favoritesHasMore}
          items={favorites}
          loading={loadingFavorites}
          loadingMore={loadingMoreFavorites}
          onProductPress={(product) => void openProduct(product, "favorites")}
          onShowMore={handleShowMoreFavorites}
          onToggleFavorite={toggleFavorite}
        />
      );
    }

    if (viewMode === "profile" && currentUser) {
      return (
        <ProfileScreen
          history={historyItems}
          historyHasMore={historyHasMore}
          loadingHistory={loadingHistory}
          loadingRecommendations={loadingRecommendations}
          onLogout={() => void handleLogout()}
          onOpenCatalog={() => openTab("catalog")}
          onOpenHistoryEntry={openHistoryEntry}
          onOpenProduct={(product) => void openProduct(product, "profile")}
          onShowMoreHistory={handleShowMoreHistory}
          onShowMoreRecommendations={handleShowMoreRecommendations}
          onToggleFavorite={toggleFavorite}
          recommendations={recommendations.slice(0, 6)}
          recommendationsHasMore={recommendationHasMore}
          user={currentUser}
        />
      );
    }

    return (
      <HomeScreen
        categories={categories}
        loadingRecommendations={loadingRecommendations}
        offers={offerProducts}
        onCategoryPress={handleCategoryPress}
        onOpenCatalog={() => openTab("catalog")}
        onProductPress={(product) => void openProduct(product, "home")}
        onShowMoreRecommendations={handleShowMoreRecommendations}
        onToggleFavorite={toggleFavorite}
        recommendations={homeRecommendations}
        recommendationsHasMore={recommendationHasMore}
        recommendationsLabel={recommendationBasedOn}
        trendingProducts={trendingProducts}
      />
    );
  };

  return (
    <SafeAreaView style={styles.appShell}>
      <StatusBar barStyle="dark-content" />
      <View style={styles.contentArea}>{renderContent()}</View>
      {authState === "signedIn" ? (
        <View style={styles.tabBar}>
          <BottomTab active={currentTab === "home"} icon="home" label="Home" onPress={() => openTab("home")} />
          <BottomTab active={currentTab === "catalog"} icon="grid" label="Catalog" onPress={() => openTab("catalog")} />
          <BottomTab active={currentTab === "favorites"} icon="heart" label="Favorites" onPress={() => openTab("favorites")} />
          <BottomTab active={currentTab === "profile"} icon="person" label="Profile" onPress={() => openTab("profile")} />
        </View>
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  appShell: {
    flex: 1,
    backgroundColor: colors.background,
  },
  contentArea: {
    flex: 1,
  },
  screenContent: {
    gap: spacing.md,
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  screenTitle: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "800",
  },
  screenSubtitle: {
    color: colors.textMuted,
    fontSize: 15,
    lineHeight: 22,
  },
  brandHero: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 32,
    borderWidth: 1,
    marginBottom: spacing.xs,
    overflow: "hidden",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.lg,
  },
  brandHeroCompact: {
    paddingVertical: spacing.md,
  },
  brandImage: {
    alignSelf: "center",
    height: 96,
    marginBottom: spacing.md,
    width: "92%",
  },
  brandImageCompact: {
    height: 78,
    marginBottom: spacing.xs,
  },
  brandCaption: {
    color: colors.textMuted,
    fontSize: 15,
    lineHeight: 22,
  },
  brandCaptionCompact: {
    fontSize: 13,
    lineHeight: 19,
  },
  searchBox: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 20,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  searchButton: {
    backgroundColor: colors.primary,
    borderRadius: 14,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  searchButtonText: {
    color: colors.onPrimary,
    fontSize: 14,
    fontWeight: "700",
  },
  searchInput: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
  },
  rowGap: {
    gap: spacing.md,
  },
  categoryCard: {
    alignItems: "center",
    width: 84,
  },
  categoryBadge: {
    alignItems: "center",
    borderRadius: 22,
    height: 60,
    justifyContent: "center",
    marginBottom: spacing.xs,
    width: 60,
  },
  categoryLabel: {
    color: colors.textMuted,
    fontSize: 12,
    fontWeight: "600",
    textAlign: "center",
  },
  sectionHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "800",
  },
  sectionSubtext: {
    color: colors.textMuted,
    fontSize: 13,
    marginTop: 4,
  },
  sectionAction: {
    color: colors.primary,
    fontSize: 13,
    fontWeight: "800",
  },
  offerCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 24,
    borderWidth: 1,
    overflow: "hidden",
    width: OFFER_CARD_WIDTH,
  },
  offerImage: {
    backgroundColor: colors.surfaceAlt,
    height: 160,
    width: "100%",
  },
  offerRailViewport: {
    marginHorizontal: -spacing.lg,
    overflow: "hidden",
    paddingHorizontal: spacing.lg,
  },
  offerRailTrack: {
    flexDirection: "row",
  },
  offerRailItem: {
    marginRight: spacing.md,
    width: OFFER_CARD_WIDTH,
  },
  offerBody: {
    padding: spacing.md,
  },
  offerDiscount: {
    color: "#0F7A5D",
    fontSize: 12,
    fontWeight: "800",
    marginBottom: 4,
  },
  offerDiscountBadge: {
    backgroundColor: "#2563EB",
    borderRadius: 999,
    left: spacing.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    position: "absolute",
    top: spacing.sm,
    zIndex: 2,
  },
  offerTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800",
    marginBottom: 6,
  },
  offerPrice: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700",
  },
  offerPriceRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: spacing.xs,
  },
  offerOriginalPrice: {
    color: colors.textMuted,
    fontSize: 13,
    fontWeight: "600",
    textDecorationLine: "line-through",
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.md,
  },
  productCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    overflow: "visible",
    padding: spacing.md,
    position: "relative",
    width: "47%",
  },
  productImage: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 18,
    height: 160,
    width: "100%",
  },
  productImageWrap: {
    marginBottom: spacing.sm,
    position: "relative",
    zIndex: 0,
  },
  productDiscountBadge: {
    backgroundColor: "#0F7A5D",
    borderRadius: 999,
    elevation: 3,
    height: 30,
    justifyContent: "center",
    left: spacing.sm,
    paddingHorizontal: 10,
    position: "absolute",
    top: spacing.sm,
    zIndex: 3,
  },
  productDiscountText: {
    color: colors.onPrimary,
    fontSize: 10,
    fontWeight: "800",
  },
  productCategory: {
    color: colors.textMuted,
    fontSize: 11,
    fontWeight: "800",
    marginBottom: 4,
    textTransform: "uppercase",
  },
  productName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800",
    marginBottom: 6,
  },
  productDescription: {
    color: colors.textMuted,
    fontSize: 12,
    lineHeight: 18,
    marginBottom: spacing.sm,
  },
  productMeta: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  productPriceGroup: {
    gap: 4,
  },
  ratingGroup: {
    alignItems: "center",
    flexDirection: "row",
    gap: 4,
  },
  productPrice: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800",
  },
  productRating: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
  },
  productOriginalPrice: {
    color: colors.textMuted,
    fontSize: 12,
    fontWeight: "600",
    textDecorationLine: "line-through",
  },
  skeletonBlock: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 14,
  },
  imageFallback: {
    alignItems: "center",
    justifyContent: "center",
  },
  imageFallbackText: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: spacing.xs,
  },
  filterChip: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  filterChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  filterChipText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
  },
  filterChipTextActive: {
    color: colors.onPrimary,
  },
  infoCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    padding: spacing.md,
  },
  cardTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800",
    marginBottom: spacing.sm,
  },
  infoBody: {
    color: colors.textMuted,
    fontSize: 14,
    lineHeight: 21,
  },
  emptyCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    padding: spacing.lg,
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "800",
    marginBottom: spacing.xs,
  },
  emptyBody: {
    color: colors.textMuted,
    fontSize: 14,
    lineHeight: 22,
  },
  showMoreButton: {
    alignSelf: "flex-end",
    paddingVertical: spacing.xs,
  },
  showMoreText: {
    color: colors.primary,
    fontSize: 13,
    fontWeight: "800",
  },
  backButton: {
    alignItems: "center",
    alignSelf: "flex-start",
    flexDirection: "row",
    gap: spacing.xs,
  },
  backButtonText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700",
  },
  detailImage: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 28,
    height: 320,
    width: "100%",
  },
  detailImageWrap: {
    overflow: "hidden",
    position: "relative",
    width: "100%",
  },
  galleryDots: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    marginTop: spacing.sm,
  },
  galleryDot: {
    backgroundColor: colors.border,
    borderRadius: 999,
    height: 8,
    width: 8,
  },
  galleryDotActive: {
    backgroundColor: colors.primary,
    width: 18,
  },
  variantRow: {
    gap: spacing.sm,
    paddingVertical: spacing.xs,
  },
  variantChip: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  variantChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  variantChipText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
  },
  variantChipTextActive: {
    color: colors.onPrimary,
  },
  detailCategory: {
    color: colors.textMuted,
    fontSize: 13,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  detailTitle: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "800",
  },
  detailMetaRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  detailPriceWrap: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
  },
  detailPrice: {
    color: colors.primary,
    fontSize: 24,
    fontWeight: "800",
  },
  detailOriginalPrice: {
    color: colors.textMuted,
    fontSize: 14,
    fontWeight: "700",
    textDecorationLine: "line-through",
  },
  detailDiscountPill: {
    backgroundColor: "#0F7A5D",
    borderRadius: 999,
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
  },
  detailDescription: {
    color: colors.textMuted,
    fontSize: 15,
    lineHeight: 23,
  },
  sourceUrl: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: spacing.xs,
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.primary,
    borderRadius: 18,
    paddingVertical: spacing.md,
  },
  primaryButtonText: {
    color: colors.onPrimary,
    fontSize: 16,
    fontWeight: "800",
  },
  buttonInline: {
    alignItems: "center",
    flexDirection: "row",
    gap: spacing.sm,
  },
  detailSection: {
    gap: spacing.sm,
  },
  reviewCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 18,
    borderWidth: 1,
    padding: spacing.md,
  },
  reviewHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: spacing.xs,
  },
  reviewAuthor: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
  },
  reviewBody: {
    color: colors.textMuted,
    fontSize: 14,
    lineHeight: 21,
  },
  reviewDate: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: spacing.sm,
  },
  relatedCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 18,
    borderWidth: 1,
    padding: spacing.sm,
    width: 170,
  },
  relatedImage: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 14,
    height: 120,
    marginBottom: spacing.sm,
    width: "100%",
  },
  relatedTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
    marginBottom: 4,
  },
  relatedPriceRow: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
  },
  relatedPrice: {
    color: colors.primary,
    fontSize: 14,
    fontWeight: "800",
  },
  relatedOriginalPrice: {
    color: colors.textMuted,
    fontSize: 12,
    fontWeight: "600",
    textDecorationLine: "line-through",
  },
  relatedDiscountBadge: {
    backgroundColor: "#0F7A5D",
    borderRadius: 999,
    left: spacing.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    position: "absolute",
    top: spacing.sm,
    zIndex: 2,
  },
  relatedHeader: {
    gap: spacing.sm,
  },
  grabMoreRow: {
    alignItems: "flex-start",
    width: "100%",
  },
  grabMoreButton: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 16,
    borderWidth: 1,
    flexDirection: "row",
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  grabMoreButtonDisabled: {
    opacity: 0.75,
  },
  grabMoreText: {
    color: colors.primary,
    fontSize: 13,
    fontWeight: "800",
  },
  profileHero: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 24,
    borderWidth: 1,
    padding: spacing.lg,
  },
  avatar: {
    alignItems: "center",
    backgroundColor: colors.primary,
    borderRadius: 999,
    height: 88,
    justifyContent: "center",
    marginBottom: spacing.md,
    width: 88,
  },
  avatarText: {
    color: colors.onPrimary,
    fontSize: 32,
    fontWeight: "800",
  },
  profileName: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "800",
  },
  profileEmail: {
    color: colors.textMuted,
    fontSize: 14,
    marginTop: 4,
  },
  preferenceWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
  },
  preferencePill: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 999,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  preferenceText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
  },
  historyCard: {
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    paddingVertical: spacing.sm,
  },
  historyTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800",
    marginBottom: 4,
  },
  historySubtitle: {
    color: colors.textMuted,
    fontSize: 13,
    lineHeight: 19,
  },
  historyDate: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: spacing.xs,
  },
  secondaryButton: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 18,
    borderWidth: 1,
    paddingVertical: spacing.md,
  },
  secondaryButtonText: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800",
  },
  ghostButton: {
    alignItems: "center",
    paddingVertical: spacing.sm,
  },
  ghostButtonText: {
    color: colors.textMuted,
    fontSize: 14,
    fontWeight: "700",
  },
  authModeRow: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 999,
    flexDirection: "row",
    padding: 4,
  },
  authModeChip: {
    alignItems: "center",
    borderRadius: 999,
    flex: 1,
    paddingVertical: spacing.sm,
  },
  authModeChipActive: {
    backgroundColor: colors.surface,
  },
  authModeText: {
    color: colors.textMuted,
    fontSize: 14,
    fontWeight: "700",
  },
  authModeTextActive: {
    color: colors.text,
  },
  formCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 24,
    borderWidth: 1,
    padding: spacing.lg,
  },
  inputLabel: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
    marginBottom: 8,
    marginTop: spacing.sm,
  },
  textInput: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: 16,
    color: colors.text,
    marginBottom: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  formError: {
    color: "#DC2626",
    fontSize: 13,
    marginBottom: spacing.sm,
  },
  loadingCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    gap: spacing.sm,
    padding: spacing.lg,
  },
  favoriteButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.92)",
    borderRadius: 999,
    height: 30,
    justifyContent: "center",
    position: "absolute",
    right: spacing.sm,
    top: spacing.sm,
    width: 30,
    zIndex: 2,
  },
  favoriteButtonActive: {
    backgroundColor: "#EF4444",
  },
  favoriteButtonFloating: {
    right: spacing.sm,
    top: spacing.sm,
  },
  detailFavoriteButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.94)",
    borderRadius: 999,
    height: 38,
    justifyContent: "center",
    position: "absolute",
    right: spacing.md,
    top: spacing.md,
    width: 38,
    zIndex: 2,
  },
  aixHeader: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 26,
    borderWidth: 1,
    padding: spacing.lg,
  },
  aixBrandWrap: {
    alignItems: "center",
    flexDirection: "row",
    gap: spacing.md,
  },
  aixBrandIcon: {
    alignItems: "center",
    backgroundColor: colors.primary,
    borderRadius: 18,
    height: 42,
    justifyContent: "center",
    width: 42,
  },
  aixBrandTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900",
  },
  aixBrandSubtitle: {
    color: colors.textMuted,
    fontSize: 13,
    marginTop: 2,
  },
  aixHeroCard: {
    backgroundColor: "#1D4ED8",
    borderRadius: 32,
    borderWidth: 1,
    borderColor: "rgba(219,234,254,0.18)",
    gap: spacing.sm,
    overflow: "hidden",
    padding: spacing.lg,
    position: "relative",
  },
  aixHeroBackdrop: {
    ...StyleSheet.absoluteFillObject,
  },
  aixHeroWave: {
    backgroundColor: "rgba(147,197,253,0.28)",
    borderRadius: 48,
    height: 220,
    position: "absolute",
    right: -36,
    top: -62,
    width: 260,
  },
  aixHeroOrb: {
    borderRadius: 999,
    position: "absolute",
  },
  aixHeroOrbPrimary: {
    backgroundColor: "rgba(239,246,255,0.2)",
    height: 128,
    left: -18,
    top: 112,
    width: 128,
  },
  aixHeroOrbSecondary: {
    backgroundColor: "rgba(96,165,250,0.2)",
    height: 86,
    right: 36,
    top: 118,
    width: 86,
  },
  aixHeroSpark: {
    backgroundColor: "rgba(219,234,254,0.72)",
    borderRadius: 999,
    height: 22,
    position: "absolute",
    right: 92,
    top: 34,
    width: 22,
  },
  aixHeroEyebrow: {
    color: "rgba(219,234,254,0.82)",
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.1,
    zIndex: 1,
  },
  aixHeroTitle: {
    color: colors.onPrimary,
    fontSize: 28,
    fontWeight: "900",
    zIndex: 1,
  },
  aixHeroBody: {
    color: "rgba(239,246,255,0.9)",
    fontSize: 14,
    lineHeight: 20,
    maxWidth: "76%",
    zIndex: 1,
  },
  aixHeroButton: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: "#EFF6FF",
    borderRadius: 16,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    zIndex: 1,
  },
  aixHeroButtonText: {
    color: "#1D4ED8",
    fontSize: 14,
    fontWeight: "800",
  },
  tabBar: {
    backgroundColor: colors.surface,
    borderTopColor: colors.border,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-around",
    paddingBottom: spacing.md,
    paddingTop: spacing.sm,
  },
  tabButton: {
    alignItems: "center",
    gap: 4,
    paddingVertical: spacing.xs,
  },
  tabLabel: {
    color: colors.textMuted,
    fontSize: 12,
    fontWeight: "700",
  },
  tabLabelActive: {
    color: colors.primary,
  },
  pressedCard: {
    opacity: 0.9,
    transform: [{ scale: 0.99 }],
  },
});
