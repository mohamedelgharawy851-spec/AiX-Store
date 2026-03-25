# ShopEase Project Guide

Generated on 2026-03-25. This document is intended to make the whole repository understandable at the folder, file, and function level.

## System Overview

ShopEase is a mobile-first shopping discovery app. The Expo React Native client talks to a hosted or local runtime, the runtime fronts a Python FastAPI backend, and the backend owns the catalog, persistence, search expansion, live discovery, personalization, and image caching. The project also keeps a Node runtime/proxy layer, design exports, migration scripts, and architecture notes in the same repository.

- Frontend: Expo + React Native in `apps/mobile`.
- Primary backend: FastAPI service in `services/scraper-python`.
- Node runtime/proxy: `services/scraper`.
- Primary production data/auth service: Supabase Postgres + Supabase Auth.
- Live search expansion/discovery: Apify.
- Optional/local AI assist: Ollama with TinyLlama-style prompts and schemas.
- Reference/legacy metasearch infrastructure: SearXNG in `infra/searxng`.

## Request Flow

- The mobile app resolves a runtime base URL in `apps/mobile/src/runtime/client.ts`.
- UI actions in `apps/mobile/App.tsx` call typed client wrappers under `apps/mobile/src/*/client.ts`.
- The request hits the Node runtime or directly the Python runtime, depending on deployment and environment.
- FastAPI routes in `services/scraper-python/app/main.py` validate auth/session context and delegate to `CatalogJobRunner` or direct storage helpers.
- `services/scraper-python/app/jobs.py` orchestrates bootstrap, search, live discovery, provider extraction, persistence, and pagination.
- `services/scraper-python/app/storage/db.py` stores products, queries, related rows, user events, favorites, recommendations, and cached AI/discovery state.
- Responses are normalized into mobile-friendly payloads and rendered by the React Native screens.

## Feature Logic Map

### Authentication and Session Logic

The login flow starts in `apps/mobile/App.tsx` through `AuthScreen`, `submitAuth`, `restoreSession`, and `handleLogout`. The mobile app talks to `apps/mobile/src/auth/client.ts`, persists the bearer token with `apps/mobile/src/auth/session.ts`, and relies on `apps/mobile/src/auth/types.ts` for response shapes. On the backend, `services/scraper-python/app/main.py` exposes `/auth/signup`, `/auth/login`, `/auth/logout`, `/me`, and event endpoints. `verify_supabase_jwt` validates Supabase-issued tokens, while the user/profile/session records live in `services/scraper-python/app/storage/db.py`.

### Home, Catalog, and Browse Logic

The home and category experience is orchestrated in `apps/mobile/App.tsx` via `HomeScreen`, catalog pagination handlers, offer rails, and product cards. The app calls `bootstrapCatalog` and `listCatalog` from `apps/mobile/src/catalog/client.ts`. The backend receives those requests in `services/scraper-python/app/main.py`, routes them into `CatalogJobRunner.ensure_bootstrap()` and `CatalogJobRunner.list_category()` in `services/scraper-python/app/jobs.py`, then persists/reads products through `services/scraper-python/app/storage/db.py`. Featured offers are derived from discountable products, and image URLs are normalized/cached before being sent back to the app.

### Search and Discovery Logic

Search starts with `SearchField` and `handleSearchSubmit` inside `apps/mobile/App.tsx`, then uses `searchCatalog` from `apps/mobile/src/catalog/client.ts`. The backend route `catalog_search` in `services/scraper-python/app/main.py` delegates to `CatalogJobRunner.search()` in `services/scraper-python/app/jobs.py`. That flow can query cached products first, expand the query through the AI layer, and run live discovery through Apify (`services/scraper-python/app/discovery/apify_client.py`). Discovery hits are normalized (`normalization.py`), ranked (`ranking.py`), converted into provider fetches, and finally upserted into the catalog DB.

### Product Detail and Related Logic

When the user opens a product, `openProduct`, `ProductDetailScreen`, and `openRelatedScreen` in `apps/mobile/App.tsx` coordinate the detail/related experience. The mobile client calls `getProductDetail` and `getRelatedProducts`. The backend answers through `catalog_product_detail` and `catalog_product_related` in `services/scraper-python/app/main.py`, with most of the work handled in `CatalogJobRunner.get_detail()` and `CatalogJobRunner.get_related()`. The storage layer builds image galleries, variant options, review payloads, family keys, cached related rows, and live fallback related matches.

### Favorites, History, and Recommendations Logic

The profile area in `apps/mobile/App.tsx` coordinates `FavoritesScreen`, `ProfileScreen`, recommendation pagination, and history pagination. It uses API wrappers from `apps/mobile/src/favorites/client.ts`, `apps/mobile/src/history/client.ts`, and `apps/mobile/src/recommendations/client.ts`. Backend user routes in `services/scraper-python/app/main.py` call storage-layer functions in `services/scraper-python/app/storage/db.py` that record events, dedupe visible history, compute interest signals, build personalized recommendations, and keep favorites synchronized.

### AI Assistance Logic

The AI layer is optional and controlled by feature flags in `services/scraper-python/app/ai/config.py`. Query rewrites are generated by `generate_rewrite_plan()` in `rewrite_service.py` using prompts from `prompts.py`, schemas from `schemas.py`, and model access through `OllamaModelManager` in `model_manager.py`. Ambiguous category decisions flow through `category_judge.py`. Cache and logging helpers record rewrite reuse and AI-run metadata. The project documentation in `docs/ai/` explains the intended prompt contracts and rollout strategy.

### Storage, Scoring, and Data Quality Techniques

Most business rules live in `services/scraper-python/app/storage/db.py`. Important techniques include category-rule classification, book-like product repair, family-key grouping, variant metadata extraction, image presence enforcement, query/result caching, related-product revalidation, session-scoped history and recommendations, and Postgres compatibility via `postgres_compat.py`. The service uses SQLite-style logic for local development/tests and a translated Postgres path for Supabase in production.

### External Services and Infrastructure

Supabase is the active auth and Postgres backend. Apify is the active live-search/discovery provider. Hugging Face Spaces appears in runtime URLs and is used as the hosted API/runtime target for mobile preview builds. Ollama/TinyLlama powers optional local AI rewrite/category tasks. SearXNG is still present as optional/local infrastructure and documentation, but the live path has moved to Apify. Neo4j variables exist in `.env.example` and a local Neo4j bundle exists in the workspace, but the tracked application code does not currently wire Neo4j into the active request path. Railway and Docker files define deployment paths for the backend services.

## Important Techniques Used in the Codebase

- Category classification combines rule-based heuristics, strong phrases, include/exclude terms, and optional AI assistance for ambiguous cases.
- Product quality filters reject items with no images, impossible pricing, or obviously wrong category assignments.
- Family keys and variant metadata are used to deduplicate variants while still exposing variant selection in the detail screen.
- Related products combine collection-group context, same-category matching, cached related rows, and live fallback discovery.
- User recommendations are computed from favorites, history, search activity, and session-scoped interest signals.
- A compatibility layer rewrites SQLite-shaped SQL so the same business logic can run against Supabase Postgres.
- Image URLs are fetched and cached locally so the app can serve stable image endpoints.
- Query results, discovery hits, and AI rewrites are cached to reduce repeated network/model cost.

## Folder and File Reference

### .

Repository-level files that describe the project, shared tooling, and workspace configuration.

#### .dockerignore

Purpose: Docker build ignore rules for the repository root.

Contains: Lists files and directories that should not be sent into Docker build contexts.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### .env.example

Purpose: Environment-variable template for local and hosted development.

Contains: Documents database, Supabase, Apify, AI, optional Neo4j, and other integration settings.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### .gitignore

Purpose: Git ignore rules for development artifacts.

Contains: Defines which generated files, caches, and secrets should stay out of version control.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### PLAN.md

Purpose: Planning and roadmap notes for the project.

Contains: Captures product/engineering planning context rather than executable code.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### README.md

Purpose: Top-level onboarding document for the repository.

Contains: Explains what the project is, how the runtimes fit together, and how to run common workflows.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### package-lock.json

Purpose: Locked npm dependency graph for the workspace root.

Contains: Pins package versions so installs are reproducible.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### package.json

Purpose: Workspace manifest for shared scripts.

Contains: Defines root npm scripts and top-level development dependencies.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### apps/mobile

Expo React Native application. This is the user-facing client with login, home, catalog, detail, favorites, profile, and related-product flows.

#### apps/mobile/App.tsx

Purpose: Single-file mobile application shell and screen coordinator.

Contains: Contains navigation state, screen components, UI helpers, pagination handlers, cache helpers, and feature orchestration for login, home, search, detail, favorites, history, related products, and profile.

- `iconName()` - Implements icon name in the mobile app shell.
- `uniqueProducts()` - Implements unique products in the mobile app shell.
- `normalizeRuntimeAssetUrl()` - Implements normalize runtime asset url in the mobile app shell.
- `firstNonEmptyUrl()` - Implements first non empty url in the mobile app shell.
- `uniqueImageUrls()` - Implements unique image urls in the mobile app shell.
- `fallbackCategoryQuery()` - Implements fallback category query in the mobile app shell.
- `imageGalleryForProduct()` - Implements image gallery for product in the mobile app shell.
- `productFromVariantSummary()` - Implements product from variant summary in the mobile app shell.
- `offerDiscount()` - Implements offer discount in the mobile app shell.
- `pickOfferProducts()` - Selects  offer products from available options.
- `pickDiverseProducts()` - Selects  diverse products from available options.
- `expectedCatalogContextKey()` - Implements expected catalog context key in the mobile app shell.
- `catalogCacheKey()` - Implements catalog cache key in the mobile app shell.
- `recommendationCacheKey()` - Implements recommendation cache key in the mobile app shell.
- `historyCacheKey()` - Implements history cache key in the mobile app shell.
- `relatedCacheKey()` - Implements related cache key in the mobile app shell.
- `favoritesCacheKey()` - Implements favorites cache key in the mobile app shell.
- `collectCatalogItemsFromCache()` - Implements collect catalog items from cache in the mobile app shell.
- `buildCatalogContext()` - Implements build catalog context in the mobile app shell.
- `formatDate()` - Formats  date for display or transport.
- `interestLabel()` - Implements interest label in the mobile app shell.
- `snapshotToProduct()` - Implements snapshot to product in the mobile app shell.
- `ScreenShell()` - Implements screen shell in the mobile app shell.
- `BrandHero()` - Implements brand hero in the mobile app shell.
- `SearchField()` - Implements search field in the mobile app shell.
- `PromoHeroCard()` - Implements promo hero card in the mobile app shell.
- `SkeletonBlock()` - Implements skeleton block in the mobile app shell.
- `ProductImage()` - Implements product image in the mobile app shell.
- `OfferCardTile()` - Implements offer card tile in the mobile app shell.
- `AutoScrollingOfferRail()` - Implements auto scrolling offer rail in the mobile app shell.
- `run()` - Implements run in the mobile app shell.
- `pauseRail()` - Pauses  rail.
- `resumeRailSoon()` - Resumes  rail soon.
- `ProductCard()` - Implements product card in the mobile app shell.
- `ProductCardSkeleton()` - Implements product card skeleton in the mobile app shell.
- `HistoryRowSkeleton()` - Implements history row skeleton in the mobile app shell.
- `ShowMoreButton()` - Implements show more button in the mobile app shell.
- `EmptyState()` - Implements empty state in the mobile app shell.
- `HomeScreen()` - Implements home screen in the mobile app shell.
- `CatalogScreen()` - Implements catalog screen in the mobile app shell.
- `ProductDetailScreen()` - Implements product detail screen in the mobile app shell.
- `handleGalleryLayout()` - Handles the  gallery layout interaction or event.
- `handleGalleryScroll()` - Handles the  gallery scroll interaction or event.
- `RelatedScreen()` - Implements related screen in the mobile app shell.
- `ProfileScreen()` - Implements profile screen in the mobile app shell.
- `FavoritesScreen()` - Implements favorites screen in the mobile app shell.
- `AuthScreen()` - Implements auth screen in the mobile app shell.
- `BottomTab()` - Implements bottom tab in the mobile app shell.
- `isFavoriteProduct()` - Implements is favorite product in the mobile app shell.
- `mergeFavoriteIds()` - Implements merge favorite ids in the mobile app shell.
- `markProductsFavoriteState()` - Implements mark products favorite state in the mobile app shell.
- `applyFavoriteState()` - Implements apply favorite state in the mobile app shell.
- `abortArea()` - Implements abort area in the mobile app shell.
- `setCatalogContextState()` - Implements set catalog context state in the mobile app shell.
- `clearUserScopedCaches()` - Implements clear user scoped caches in the mobile app shell.
- `resetDetailNavigation()` - Implements reset detail navigation in the mobile app shell.
- `fireUserEvent()` - Sends  user event into the backend or local event pipeline.
- `loadRecommendationsPage()` - Implements load recommendations page in the mobile app shell.
- `loadHistoryPage()` - Implements load history page in the mobile app shell.
- `loadFavoritesPage()` - Implements load favorites page in the mobile app shell.
- `loadRelatedPage()` - Implements load related page in the mobile app shell.
- `loadCatalogPage()` - Implements load catalog page in the mobile app shell.
- `openProduct()` - Opens  product in the mobile app shell.
- `selectProductVariant()` - Implements select product variant in the mobile app shell.
- `toggleFavorite()` - Toggles  favorite on or off.
- `openProductSource()` - Opens  product source in the mobile app shell.
- `restoreSession()` - Restores  session from saved state.
- `submitAuth()` - Submits  auth to the backend or next workflow step.
- `handleLogout()` - Handles the  logout interaction or event.
- `openTab()` - Opens  tab in the mobile app shell.
- `handleSearchChange()` - Handles the  search change interaction or event.
- `handleSearchSubmit()` - Handles the  search submit interaction or event.
- `handleCategoryPress()` - Handles the  category press interaction or event.
- `handleClearCategory()` - Handles the  clear category interaction or event.
- `handleShowMoreCatalog()` - Handles the  show more catalog interaction or event.
- `handleShowMoreRecommendations()` - Handles the  show more recommendations interaction or event.
- `handleShowMoreHistory()` - Handles the  show more history interaction or event.
- `handleShowMoreFavorites()` - Handles the  show more favorites interaction or event.
- `openRelatedScreen()` - Opens  related screen in the mobile app shell.
- `handleGrabMoreRelated()` - Handles the  grab more related interaction or event.
- `openHistoryEntry()` - Opens  history entry in the mobile app shell.
- `renderContent()` - Renders  content for the current UI state.

#### apps/mobile/app.json

Purpose: Expo application manifest.

Contains: Stores app identity, icon/splash config, Android package name, and EAS metadata.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### apps/mobile/eas.json

Purpose: EAS build profile configuration.

Contains: Defines Expo Application Services build behavior, especially the preview APK profile and runtime URL injection.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### apps/mobile/index.js

Purpose: Expo entry point.

Contains: Registers the React Native root component.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### apps/mobile/package.json

Purpose: Mobile app package manifest.

Contains: Defines Expo/React Native dependencies and developer scripts for running the app.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### apps/mobile/tsconfig.json

Purpose: TypeScript compiler settings for the mobile app.

Contains: Configures TypeScript behavior for Expo/React Native development.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### apps/mobile/assets

Branding and launcher assets packaged into the mobile app.

#### apps/mobile/assets/aix-store-brand.png

Purpose: Mobile branding/image asset.

Contains: Stores launcher, brand, or mark artwork used by Expo builds and the app shell.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### apps/mobile/assets/aix-store-icon.png

Purpose: Mobile branding/image asset.

Contains: Stores launcher, brand, or mark artwork used by Expo builds and the app shell.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### apps/mobile/assets/aix-store-mark.png

Purpose: Mobile branding/image asset.

Contains: Stores launcher, brand, or mark artwork used by Expo builds and the app shell.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### apps/mobile/src/auth

Client-side authentication helpers, token persistence, and auth-related types.

#### apps/mobile/src/auth/client.ts

Purpose: HTTP client for auth endpoints.

Contains: Wraps sign up, sign in, sign out, profile lookup, and event posting against the backend API.

- `signUp()` - Implements sign up in the module.
- `signIn()` - Implements sign in in the module.
- `signOut()` - Implements sign out in the module.
- `getMe()` - Implements get me in the module.
- `postUserEvent()` - Implements post user event in the module.

#### apps/mobile/src/auth/session.ts

Purpose: Session token persistence helpers.

Contains: Reads, writes, and clears the auth token on the device.

- `saveSessionToken()` - Implements save session token in the module.
- `readSessionToken()` - Implements read session token in the module.
- `clearSessionToken()` - Implements clear session token in the module.

#### apps/mobile/src/auth/types.ts

Purpose: Auth-related TypeScript contracts.

Contains: Defines response shapes for the authenticated user and login/register flows.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### apps/mobile/src/catalog

Client-side catalog DTOs and HTTP wrappers for bootstrap, browsing, search, detail, and related results.

#### apps/mobile/src/catalog/client.ts

Purpose: HTTP client for catalog endpoints.

Contains: Wraps bootstrap, list, search, detail, and related-product requests.

- `bootstrapCatalog()` - Implements bootstrap catalog in the module.
- `listCatalog()` - Implements list catalog in the module.
- `searchCatalog()` - Implements search catalog in the module.
- `getProductDetail()` - Implements get product detail in the module.
- `getRelatedProducts()` - Implements get related products in the module.

#### apps/mobile/src/catalog/types.ts

Purpose: Catalog domain models for the mobile app.

Contains: Defines product, review, category, detail, list, AI metadata, and discovery metadata types used by the UI.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### apps/mobile/src/favorites

Client wrappers for favorite mutation and retrieval.

#### apps/mobile/src/favorites/client.ts

Purpose: Favorites API wrapper.

Contains: Fetches favorites and sends favorite add/remove mutations.

- `getFavorites()` - Implements get favorites in the module.
- `addFavorite()` - Implements add favorite in the module.
- `removeFavorite()` - Implements remove favorite in the module.

### apps/mobile/src/history

Client wrapper for user history retrieval.

#### apps/mobile/src/history/client.ts

Purpose: History API wrapper.

Contains: Loads paginated user history from the backend.

- `getHistory()` - Implements get history in the module.

### apps/mobile/src/recommendations

Client wrapper for personalized recommendations and trending items.

#### apps/mobile/src/recommendations/client.ts

Purpose: Recommendations API wrapper.

Contains: Fetches personalized recommendations and trending items for the current user/session.

- `getRecommendations()` - Implements get recommendations in the module.

### apps/mobile/src/runtime

Runtime URL selection and fetch helpers that let the mobile app talk to local or hosted backends.

#### apps/mobile/src/runtime/client.ts

Purpose: Runtime fetch and URL-resolution helper.

Contains: Chooses the correct backend base URL and performs JSON fetches with consistent behavior.

- `extractMetroHost()` - Implements extract metro host in the module.
- `runtimeBaseUrl()` - Implements runtime base url in the module.
- `fetchRuntimeJson()` - Implements fetch runtime json in the module.
- `abortListener()` - Implements abort listener in the module.

### apps/mobile/src/theme

Shared visual tokens used by the mobile UI.

#### apps/mobile/src/theme/tokens.ts

Purpose: Shared color and spacing tokens.

Contains: Defines the mobile app design primitives used across the UI.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### docs

Project notes that explain architecture, API decisions, search strategy, and review findings.

#### docs/api-recommendations.md

Purpose: High-level note describing the lightweight runtime-first API strategy.

Contains: Explains that the catalog runtime is the live source of truth and how search/redirect behavior works.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### docs/architecture.md

Purpose: Architecture overview document.

Contains: Describes the system shape, runtime boundaries, and major data flows.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### docs/project-review.md

Purpose: Project review and findings document.

Contains: Summarizes technical observations, risks, or review notes about the codebase.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### docs/ai

AI-specific notes, prompts, and rollout planning.

#### docs/ai/prompts.md

Purpose: Prompt contract reference for the AI layer.

Contains: Documents the rewrite and category-judge prompt formats and output contracts.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### docs/ai/tinyllama-phase1.md

Purpose: AI rollout note for TinyLlama phase 1.

Contains: Explains the first-stage plan for integrating local/assisted AI behavior.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### docs/search

Discovery/search integration notes, especially Apify and the legacy SearXNG path.

#### docs/search/apify-integration.md

Purpose: Documentation for the live Apify discovery path.

Contains: Explains how Apify-based search expansion/discovery is configured and intended to run.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### docs/search/searxng-integration.md

Purpose: Documentation for the legacy/optional SearXNG path.

Contains: Explains the older metasearch integration retained as reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### infra/searxng

Optional self-hosted SearXNG infrastructure kept as reference/local tooling.

#### infra/searxng/README.md

Purpose: Operational note for local SearXNG infrastructure.

Contains: Explains how the optional SearXNG stack is configured and started.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### infra/searxng/docker-compose.yml

Purpose: Docker Compose definition for SearXNG.

Contains: Starts the local SearXNG service and any supporting containers.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### infra/searxng/settings.yml

Purpose: SearXNG instance configuration.

Contains: Defines engines and service-level settings for the local metasearch instance.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### scripts

Root-level development scripts for loading env files and starting the full local mobile stack.

#### scripts/load-env.mjs

Purpose: Shared environment loader for Node-side tooling.

Contains: Parses `.env` and `.env.local` files and injects values into `process.env`.

- `parseValue()` - Implements parse value in the module.
- `parseEnvFile()` - Implements parse env file in the module.
- `loadAIXStoreEnv()` - Implements load a i x store env in the module.

#### scripts/start-mobile-live.mjs

Purpose: Full local development launcher.

Contains: Boots the Python backend, boots the Node runtime if needed, configures Expo, and handles Android port-reverse/LAN access.

- `wait()` - Implements wait in the module.
- `resolveAdbBinary()` - Implements resolve adb binary in the module.
- `ensureAndroidReversePort()` - Implements ensure android reverse port in the module.
- `resolveLanHost()` - Implements resolve lan host in the module.
- `findBundledChromium()` - Implements find bundled chromium in the module.
- `requestHealth()` - Implements request health in the module.
- `resolvePythonBinary()` - Implements resolve python binary in the module.
- `ensurePythonRuntime()` - Implements ensure python runtime in the module.
- `ensureNodeRuntime()` - Implements ensure node runtime in the module.
- `main()` - Implements main in the module.

### services/scraper

Node.js runtime/proxy layer that decorates responses for the mobile app and can front the Python backend.

#### services/scraper/.dockerignore

Purpose: Docker ignore rules for the Node runtime.

Contains: Keeps unnecessary local files out of the Node runtime image build context.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/.railwayignore

Purpose: Railway ignore rules for the Node runtime.

Contains: Excludes files that should not be uploaded during Railway deployment.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/Dockerfile

Purpose: Container definition for the Node runtime.

Contains: Builds the runtime image used to serve or proxy the catalog API.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/README.md

Purpose: Readme for the Node runtime service.

Contains: Explains what the runtime does and how it is expected to be used.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/package-lock.json

Purpose: Locked dependency tree for the Node runtime.

Contains: Pins runtime dependency versions.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/package.json

Purpose: Node runtime package manifest.

Contains: Defines the start script and runtime dependencies for the proxy/decorator service.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/railway.toml

Purpose: Railway deployment config for the Node runtime.

Contains: Describes how the runtime should be deployed on Railway.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/server.mjs

Purpose: HTTP server for the Node runtime/proxy.

Contains: Handles health checks, proxies catalog/auth requests, decorates payloads for the mobile app, and wakes the Python service when needed.

- `corsHeaders()` - Implements cors headers in the module.
- `sendJson()` - Implements send json in the module.
- `upstreamStartingPayload()` - Implements upstream starting payload in the module.
- `runtimeOrigin()` - Implements runtime origin in the module.
- `decorateProduct()` - Implements decorate product in the module.
- `decorateCatalogPayload()` - Implements decorate catalog payload in the module.
- `decorateProductDetail()` - Implements decorate product detail in the module.
- `decorateRecommendations()` - Implements decorate recommendations in the module.
- `decorateFavorites()` - Implements decorate favorites in the module.
- `decorateRelatedPayload()` - Implements decorate related payload in the module.
- `readRequestBody()` - Implements read request body in the module.
- `upstreamPolicy()` - Implements upstream policy in the module.
- `fetchUpstream()` - Implements fetch upstream in the module.
- `wakeUpFastApi()` - Implements wake up fast api in the module.
- `decodeJsonUpstream()` - Implements decode json upstream in the module.
- `proxyJson()` - Implements proxy json in the module.
- `proxyImage()` - Implements proxy image in the module.

### services/scraper/data

Placeholder directory for local runtime catalog data.

#### services/scraper/data/.gitkeep

Purpose: Placeholder data file for the Node runtime.

Contains: Keeps the data directory tracked in git.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### services/scraper/lib

Node runtime helpers for catalog shaping, scraping, config, utilities, and local persistence.

#### services/scraper/lib/catalog.mjs

Purpose: Catalog shaping and fallback logic for the Node runtime.

Contains: Builds list/search/detail payloads, filters products, interleaves results, and computes simple relevance/offer behavior.

- `inferCategoryId()` - Infers  category id from the available inputs.
- `productSearchScore()` - Implements product search score in the module.
- `relatedScore()` - Implements related score in the module.
- `nearestScore()` - Implements nearest score in the module.
- `interleaveProducts()` - Implements interleave products in the module.
- `getCategories()` - Implements get categories in the module.
- `getOffers()` - Implements get offers in the module.
- `filterProducts()` - Implements filter products in the module.
- `hasBootstrapCoverage()` - Implements has bootstrap coverage in the module.
- `ensureBootstrap()` - Implements ensure bootstrap in the module.
- `listCatalog()` - Implements list catalog in the module.
- `searchCatalog()` - Implements search catalog in the module.
- `getProductDetail()` - Implements get product detail in the module.
- `buildCatalogPayload()` - Implements build catalog payload in the module.

#### services/scraper/lib/config.mjs

Purpose: Static category/source config for the Node runtime.

Contains: Defines category metadata, source domains, and default limits.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper/lib/scraper.mjs

Purpose: Legacy Node-side scraping utilities.

Contains: Collects and normalizes product data from retailer pages when the Node runtime is used directly.

- `inferCategoryId()` - Infers  category id from the available inputs.
- `getCategoryName()` - Implements get category name in the module.
- `inferSourceSite()` - Infers  source site from the available inputs.
- `queryScore()` - Implements query score in the module.
- `buildFallbackQueries()` - Implements build fallback queries in the module.
- `fetchHtml()` - Implements fetch html in the module.
- `fetchHtmlViaPython()` - Implements fetch html via python in the module.
- `fetchWalmartHtml()` - Implements fetch walmart html in the module.
- `tryParseJson()` - Implements try parse json in the module.
- `collectProductNodes()` - Implements collect product nodes in the module.
- `extractJsonLdProducts()` - Implements extract json ld products in the module.
- `extractNextData()` - Implements extract next data in the module.
- `extractMetaContent()` - Implements extract meta content in the module.
- `extractOriginalPrice()` - Implements extract original price in the module.
- `normalizeOffer()` - Implements normalize offer in the module.
- `normalizeReviews()` - Implements normalize reviews in the module.
- `toProductFromStructuredData()` - Implements to product from structured data in the module.
- `buildFallbackProduct()` - Implements build fallback product in the module.
- `scrapeProductPage()` - Implements scrape product page in the module.
- `normalizeWalmartImage()` - Implements normalize walmart image in the module.
- `pickWalmartImage()` - Selects  walmart image from available options.
- `toProductFromWalmartSearchItem()` - Implements to product from walmart search item in the module.
- `extractWalmartItemsFromHtml()` - Implements extract walmart items from html in the module.
- `browseWalmartCategory()` - Implements browse walmart category in the module.
- `searchWalmart()` - Implements search walmart in the module.
- `extractWalmartItemId()` - Implements extract walmart item id in the module.
- `normalizeWalmartReviewItem()` - Implements normalize walmart review item in the module.
- `scrapeWalmartReviewsPage()` - Implements scrape walmart reviews page in the module.
- `scrapeProductsForQuery()` - Implements scrape products for query in the module.
- `bootstrapProducts()` - Implements bootstrap products in the module.
- `enrichProductFromSource()` - Implements enrich product from source in the module.

#### services/scraper/lib/store.mjs

Purpose: Local JSON catalog persistence helpers.

Contains: Loads, merges, normalizes, and saves catalog data for the Node runtime.

- `ensureDataFile()` - Implements ensure data file in the module.
- `mergeProductPair()` - Implements merge product pair in the module.
- `normalizeProduct()` - Implements normalize product in the module.
- `normalizeProducts()` - Implements normalize products in the module.
- `loadCatalogStore()` - Implements load catalog store in the module.
- `saveCatalogStore()` - Implements save catalog store in the module.
- `mergeProducts()` - Implements merge products in the module.
- `replaceCatalogStore()` - Implements replace catalog store in the module.

#### services/scraper/lib/utils.mjs

Purpose: General-purpose helpers for the Node runtime.

Contains: Provides normalization, hashing, URL cleanup, tokenization, and array utilities.

- `normalizeWhitespace()` - Implements normalize whitespace in the module.
- `slugify()` - Converts text into a stable slug.
- `toNumber()` - Implements to number in the module.
- `hashId()` - Implements hash id in the module.
- `decodeHtmlEntities()` - Implements decode html entities in the module.
- `stripHtml()` - Implements strip html in the module.
- `pickFirst()` - Selects  first from available options.
- `toArray()` - Implements to array in the module.
- `uniqueBy()` - Implements unique by in the module.
- `safeUrl()` - Implements safe url in the module.
- `canonicalizeProductUrl()` - Canonicalizes  product url.
- `parseHostFromUrl()` - Implements parse host from url in the module.
- `nowIso()` - Implements now iso in the module.
- `tokenize()` - Splits text into normalized tokens.

### services/scraper/scripts

Utility scripts used by the Node runtime.

#### services/scraper/scripts/fetch_url.py

Purpose: Utility fetch script used by scraping workflows.

Contains: Retrieves page content from Python when a scriptable fetch path is needed.

- `main()` - Implements main in the module.

### services/scraper-python

Primary backend service. It owns the catalog API, persistence, discovery, recommendations, AI hooks, and migration tooling.

#### services/scraper-python/.railwayignore

Purpose: Railway ignore rules for the Python backend.

Contains: Excludes local-only files from Python service deployment.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/Dockerfile

Purpose: Container definition for the Python backend.

Contains: Builds the FastAPI service image with its runtime dependencies.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/railway.toml

Purpose: Railway deployment config for the Python backend.

Contains: Describes how the Python API should run on Railway.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/requirements.txt

Purpose: Python dependency manifest.

Contains: Pins backend, scraping, image, DB, and AI-support libraries.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### services/scraper-python/app

Main Python application package: API, jobs, config, env loading, utilities, and business rules.

#### services/scraper-python/app/__init__.py

Purpose: Package marker for the Python app.

Contains: Makes the `app` directory importable.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/config.py

Purpose: Central backend configuration module.

Contains: Defines ports, paths, category metadata, thresholds, provider settings, and shared constants.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/env.py

Purpose: Environment loader for the Python service.

Contains: Parses repo-level env files before app startup so local development behaves consistently.

- `_repo_root()` - Implements repo root in the module.
- `_service_root()` - Implements service root in the module.
- `_parse_value()` - Parses value.
- `_parse_env_file()` - Parses env file.
- `load_env_files()` - Loads env files.

#### services/scraper-python/app/jobs.py

Purpose: High-level catalog orchestration layer.

Contains: Coordinates bootstrap, category listing, search, discovery enrichment, ranking, persistence, pagination, and related-product generation.

- `_decode_json_value()` - Decodes json value.
- `_decode_string_list()` - Decodes string list.
- `CatalogJobRunner` - Main class/object for this file.
- `CatalogJobRunner.__init__()` - Initializes the object and wires in its dependencies/configuration.
- `CatalogJobRunner._provider_for_product()` - Implements provider for product in the catalog job runner.
- `CatalogJobRunner._related_search_queries()` - Implements related search queries in the catalog job runner.
- `CatalogJobRunner._related_search_query()` - Implements related search query in the catalog job runner.
- `CatalogJobRunner._build_related_payload()` - Builds related payload.
- `CatalogJobRunner._search_related_products()` - Implements search related products in the catalog job runner.
- `CatalogJobRunner._provider_sequence()` - Implements provider sequence in the catalog job runner.
- `CatalogJobRunner._safe_provider_search()` - Implements safe provider search in the catalog job runner.
- `CatalogJobRunner._safe_provider_search_by_urls()` - Implements safe provider search by urls in the catalog job runner.
- `CatalogJobRunner._persist_products()` - Implements persist products in the catalog job runner.
- `CatalogJobRunner._prepare_candidates()` - Prepares candidates for the next step.
- `CatalogJobRunner._rank_and_persist_products()` - Ranks and persist products by relevance or score.
- `CatalogJobRunner._category_context_key()` - Implements category context key in the catalog job runner.
- `CatalogJobRunner._search_context_key()` - Implements search context key in the catalog job runner.
- `CatalogJobRunner._category_variants()` - Implements category variants in the catalog job runner.
- `CatalogJobRunner._is_product_detail_url()` - Checks whether product detail url.
- `CatalogJobRunner._empty_cursor()` - Implements empty cursor in the catalog job runner.
- `CatalogJobRunner._normalize_discovery_pagination()` - Normalizes discovery pagination.
- `CatalogJobRunner._build_discovery_pagination()` - Builds discovery pagination.
- `CatalogJobRunner._load_cursor()` - Loads cursor.
- `CatalogJobRunner._discovery_cursor_has_more()` - Implements discovery cursor has more in the catalog job runner.
- `CatalogJobRunner._cursor_has_more()` - Implements cursor has more in the catalog job runner.
- `CatalogJobRunner._next_cursor_index()` - Implements next cursor index in the catalog job runner.
- `CatalogJobRunner._next_discovery_seed()` - Implements next discovery seed in the catalog job runner.
- `CatalogJobRunner._merge_engines()` - Merges engines.
- `CatalogJobRunner._related_family_tokens()` - Implements related family tokens in the catalog job runner.
- `CatalogJobRunner._product_related_tokens()` - Implements product related tokens in the catalog job runner.
- `CatalogJobRunner._matches_related_family()` - Implements matches related family in the catalog job runner.
- `CatalogJobRunner._list_context_items()` - Lists context items.
- `CatalogJobRunner._run_term_search()` - Implements run term search in the catalog job runner.
- `CatalogJobRunner._fetch_searxng_hits()` - Implements fetch searxng hits in the catalog job runner.
- `CatalogJobRunner._extract_discovery_products()` - Extracts discovery products.
- `CatalogJobRunner._run_discovery_search()` - Implements run discovery search in the catalog job runner.
- `CatalogJobRunner._ensure_context_results()` - Ensures context results is ready before the flow continues.
- `CatalogJobRunner._ensure_search_show_more_results()` - Ensures search show more results is ready before the flow continues.
- `CatalogJobRunner._ensure_related_show_more_results()` - Ensures related show more results is ready before the flow continues.
- `CatalogJobRunner._seed_term()` - Implements seed term in the catalog job runner.
- `CatalogJobRunner.ensure_bootstrap()` - Ensures bootstrap is ready before the flow continues.
- `CatalogJobRunner._baseline_queries_for_category()` - Implements baseline queries for category in the catalog job runner.
- `CatalogJobRunner._reseed_category_baseline()` - Implements reseed category baseline in the catalog job runner.
- `CatalogJobRunner.reseed_full_catalog_baseline()` - Implements reseed full catalog baseline in the catalog job runner.
- `CatalogJobRunner.backfill_product_galleries()` - Implements backfill product galleries in the catalog job runner.
- `CatalogJobRunner.list_category()` - Lists category.
- `CatalogJobRunner.search()` - Runs the main search flow for this component.
- `CatalogJobRunner.get_detail()` - Returns detail.
- `CatalogJobRunner.get_related()` - Returns related.

#### services/scraper-python/app/main.py

Purpose: FastAPI application entry point.

Contains: Defines lifecycle startup, auth verification, API routes, image serving, and error handling.

- `_dotenv_path()` - Implements dotenv path in the API layer.
- `lifespan()` - Implements lifespan in the API layer.
- `_extract_token()` - Extracts token.
- `verify_supabase_jwt()` - Implements verify supabase jwt in the API layer.
- `_require_user_id()` - Implements require user id in the API layer.
- `_require_auth_context()` - Implements require auth context in the API layer.
- `_extract_client_session_id()` - Extracts client session id.
- `_effective_session_id()` - Implements effective session id in the API layer.
- `health()` - Reports whether the underlying integration/service is healthy.
- `health_db()` - Implements health db in the API layer.
- `ai_health()` - Implements ai health in the API layer.
- `discovery_health()` - Implements discovery health in the API layer.
- `bootstrap_catalog()` - Implements bootstrap catalog in the API layer.
- `catalog_products()` - Implements catalog products in the API layer.
- `catalog_search()` - Implements catalog search in the API layer.
- `discovery_query()` - Implements discovery query in the API layer.
- `discovery_cache()` - Implements discovery cache in the API layer.
- `ai_rewrite()` - Implements ai rewrite in the API layer.
- `ai_judge_category()` - Implements ai judge category in the API layer.
- `catalog_product_detail()` - Implements catalog product detail in the API layer.
- `catalog_product_related()` - Implements catalog product related in the API layer.
- `auth_signup()` - Implements auth signup in the API layer.
- `auth_login()` - Implements auth login in the API layer.
- `auth_logout()` - Implements auth logout in the API layer.
- `me()` - Implements me in the API layer.
- `me_history()` - Implements me history in the API layer.
- `me_favorites()` - Implements me favorites in the API layer.
- `me_favorite_put()` - Implements me favorite put in the API layer.
- `me_favorite_delete()` - Implements me favorite delete in the API layer.
- `me_recommendations()` - Implements me recommendations in the API layer.
- `me_events()` - Implements me events in the API layer.
- `cached_image()` - Implements cached image in the API layer.
- `unhandled_exception_handler()` - Implements unhandled exception handler in the API layer.

#### services/scraper-python/app/utils.py

Purpose: Shared backend utility functions.

Contains: Provides text cleanup, URL handling, category inference, query expansion, price normalization, ID generation, HTML cleanup, and retry helpers.

- `now_iso()` - Implements now iso in the module.
- `normalize_whitespace()` - Normalizes whitespace.
- `slugify()` - Converts text into a stable slug.
- `hash_text()` - Implements hash text in the module.
- `canonicalize_url()` - Canonicalizes  url.
- `absolute_url()` - Implements absolute url in the module.
- `parse_float()` - Parses float.
- `to_cents()` - Implements to cents in the module.
- `from_cents()` - Implements from cents in the module.
- `normalize_offer_prices()` - Normalizes offer prices.
- `extract_first_number()` - Extracts first number.
- `tokenize()` - Splits text into normalized tokens.
- `singularize_token()` - Implements singularize token in the module.
- `looks_like_book_product()` - Implements looks like book product in the module.
- `infer_category_id()` - Infers  category id from the available inputs.
- `classify_category()` - Implements classify category in the module.
- `expand_query_variants()` - Implements expand query variants in the module.
- `expand_discovery_variants()` - Implements expand discovery variants in the module.
- `category_name()` - Implements category name in the module.
- `product_id_for_url()` - Implements product id for url in the module.
- `json_dumps()` - Implements json dumps in the module.
- `decode_srcset()` - Decodes srcset.
- `strip_html()` - Implements strip html in the module.
- `ensure_unique_by_key()` - Ensures unique by key is ready before the flow continues.
- `has_bot_block()` - Checks whether bot block is available.
- `retry_async()` - Retries an operation with the module-defined retry policy.

### services/scraper-python/app/ai

AI support layer for query rewriting and ambiguous category review using Ollama/TinyLlama-style models.

#### services/scraper-python/app/ai/__init__.py

Purpose: Package marker for AI helpers.

Contains: Makes the AI subpackage importable.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/ai/cache.py

Purpose: Cache helpers for AI rewrites.

Contains: Stores and retrieves rewrite results so repeated searches avoid redundant model work.

- `_get_connection()` - Returns connection.
- `build_rewrite_cache_key()` - Builds rewrite cache key.
- `get_cached_rewrite()` - Returns cached rewrite.
- `save_rewrite_cache()` - Saves rewrite cache.

#### services/scraper-python/app/ai/category_judge.py

Purpose: Ambiguous-category review logic.

Contains: Decides when the AI model should be asked to confirm or relabel uncertain category assignments.

- `_should_judge()` - Decides whether udge should happen.
- `judge_ambiguous_category()` - Implements judge ambiguous category in the AI layer.

#### services/scraper-python/app/ai/config.py

Purpose: AI feature flags and mode settings.

Contains: Reads env flags that control whether AI is off, shadow-only, or assist mode.

- `_env_flag()` - Implements env flag in the AI layer.
- `_env_mode()` - Implements env mode in the AI layer.
- `ai_is_active()` - Implements ai is active in the AI layer.
- `ai_is_assist_mode()` - Implements ai is assist mode in the AI layer.
- `ai_pipeline_is_enabled()` - Implements ai pipeline is enabled in the AI layer.

#### services/scraper-python/app/ai/logging.py

Purpose: Persistence helpers for AI runs.

Contains: Records start/end metadata for model invocations in the database.

- `_get_connection()` - Returns connection.
- `start_ai_run()` - Implements start ai run in the AI layer.
- `finish_ai_run()` - Implements finish ai run in the AI layer.

#### services/scraper-python/app/ai/model_manager.py

Purpose: Ollama model runtime wrapper.

Contains: Loads the configured model, checks availability, and sends generation requests.

- `ModelUnavailableError` - Data/error/schema class declared by this file.
- `OllamaModelManager` - Main class/object for this file.
- `OllamaModelManager.__init__()` - Initializes the object and wires in its dependencies/configuration.
- `OllamaModelManager.status()` - Returns the current status for this object or integration.
- `OllamaModelManager.ensure_loaded()` - Ensures loaded is ready before the flow continues.
- `OllamaModelManager.generate()` - Performs the main generation step for this component.

#### services/scraper-python/app/ai/prompts.py

Purpose: Prompt builders for AI tasks.

Contains: Creates the exact prompt strings for rewrite generation and category judgment.

- `build_rewrite_prompt()` - Builds rewrite prompt.
- `build_category_judge_prompt()` - Builds category judge prompt.

#### services/scraper-python/app/ai/rewrite_service.py

Purpose: Query rewrite service entry point.

Contains: Executes the model-driven rewrite flow and returns normalized rewrite plans.

- `generate_rewrite_plan()` - Implements generate rewrite plan in the AI layer.

#### services/scraper-python/app/ai/schemas.py

Purpose: AI schema parsing/serialization helpers.

Contains: Defines dataclasses and parsing logic for strict JSON contracts returned by the model.

- `_extract_json_fragment()` - Extracts json fragment.
- `_normalize_terms()` - Normalizes terms.
- `parse_rewrite_plan()` - Parses rewrite plan.
- `parse_category_judgment()` - Parses category judgment.
- `rewrite_plan_to_json()` - Implements rewrite plan to json in the AI layer.
- `category_judgment_to_json()` - Implements category judgment to json in the AI layer.
- `AIParseError` - Data/error/schema class declared by this file.
- `RewriteCandidate` - Data/error/schema class declared by this file.
- `RewritePlan` - Data/error/schema class declared by this file.
- `CategoryJudgment` - Data/error/schema class declared by this file.

### services/scraper-python/app/discovery

Live web discovery integrations, normalization, ranking, caching, and schema objects.

#### services/scraper-python/app/discovery/__init__.py

Purpose: Package marker for discovery helpers.

Contains: Makes the discovery subpackage importable.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/discovery/apify_client.py

Purpose: Apify integration client.

Contains: Builds Apify requests, reads actor datasets, and converts them into internal search results.

- `_sanitize_query()` - Implements sanitize query in the discovery layer.
- `_build_request_payload()` - Builds request payload.
- `ApifyClient` - Main class/object for this file.
- `ApifyClient._request_dataset_items()` - Implements request dataset items in the discovery layer.
- `ApifyClient.health()` - Reports whether the underlying integration/service is healthy.
- `ApifyClient.search()` - Runs the main search flow for this component.

#### services/scraper-python/app/discovery/apify_schemas.py

Purpose: Apify result schema objects.

Contains: Defines typed containers for Apify query and search results.

- `ApifyQueryResult` - Main class/object for this file.
- `ApifyQueryResult.to_json()` - Serializes the object into a JSON-ready shape.
- `ApifySearchResult` - Main class/object for this file.
- `ApifySearchResult.to_json()` - Serializes the object into a JSON-ready shape.

#### services/scraper-python/app/discovery/cache.py

Purpose: Discovery cache helpers.

Contains: Stores and retrieves discovery responses so repeated live lookups can be reused.

- `build_discovery_cache_key()` - Builds discovery cache key.
- `get_cached_discovery()` - Returns cached discovery.
- `save_discovery_cache()` - Saves discovery cache.

#### services/scraper-python/app/discovery/config.py

Purpose: Discovery configuration and feature gating.

Contains: Reads env flags for Apify/SearXNG behavior, allowlists, budgets, and activation status.

- `_dotenv_path()` - Implements dotenv path in the discovery layer.
- `_env_flag()` - Implements env flag in the discovery layer.
- `_env_csv()` - Implements env csv in the discovery layer.
- `_first_env()` - Implements first env in the discovery layer.
- `apify_configuration_error()` - Implements apify configuration error in the discovery layer.
- `apify_is_active()` - Implements apify is active in the discovery layer.
- `discovery_is_active()` - Implements discovery is active in the discovery layer.

#### services/scraper-python/app/discovery/normalization.py

Purpose: Discovery result normalization rules.

Contains: Cleans domains/URLs, filters noise, and converts raw discovery entries into internal candidates.

- `normalize_domain()` - Normalizes domain.
- `domain_from_url()` - Implements domain from url in the discovery layer.
- `provider_for_url()` - Implements provider for url in the discovery layer.
- `is_noise_domain()` - Checks whether noise domain.
- `is_allowed_url()` - Checks whether allowed url.
- `normalize_result()` - Normalizes result.
- `normalize_apify_entry()` - Normalizes apify entry.

#### services/scraper-python/app/discovery/ranking.py

Purpose: Discovery ranking logic.

Contains: Scores and orders discovery hits before provider extraction runs.

- `_token_set()` - Implements token set in the discovery layer.
- `_category_conflicts()` - Implements category conflicts in the discovery layer.
- `score_hit()` - Calculates a score for hit.
- `rank_hits()` - Ranks hits by relevance or score.

#### services/scraper-python/app/discovery/schemas.py

Purpose: Core discovery schema objects.

Contains: Defines typed containers for normalized discovery hits and query results.

- `DiscoveryHit` - Main class/object for this file.
- `DiscoveryHit.to_json()` - Serializes the object into a JSON-ready shape.
- `DiscoveryQueryResult` - Main class/object for this file.
- `DiscoveryQueryResult.to_json()` - Serializes the object into a JSON-ready shape.

#### services/scraper-python/app/discovery/searxng_client.py

Purpose: Legacy/optional SearXNG client.

Contains: Calls a SearXNG instance and converts its results into the internal discovery shape.

- `_sanitize_query()` - Implements sanitize query in the discovery layer.
- `SearXNGClient` - Main class/object for this file.
- `SearXNGClient._request_search_payload()` - Implements request search payload in the discovery layer.
- `SearXNGClient.health()` - Reports whether the underlying integration/service is healthy.
- `SearXNGClient.search()` - Runs the main search flow for this component.

#### services/scraper-python/app/discovery/searxng_expansion.py

Purpose: Seed-generation logic for discovery queries.

Contains: Builds cleaner search seeds and related-product expansion queries from titles and families.

- `_field()` - Implements field in the discovery layer.
- `retailer_for_domain()` - Implements retailer for domain in the discovery layer.
- `is_noise_domain()` - Checks whether noise domain.
- `_is_seed_candidate()` - Checks whether seed candidate.
- `clean_seed_title()` - Implements clean seed title in the discovery layer.
- `dedupe_seed_queries()` - Implements dedupe seed queries in the discovery layer.
- `_family_tokens()` - Implements family tokens in the discovery layer.
- `build_search_seed_queries()` - Builds search seed queries.
- `build_related_seed_queries()` - Builds related seed queries.
- `build_related_family_query()` - Builds related family query.

#### services/scraper-python/app/discovery/searxng_schemas.py

Purpose: SearXNG schema objects.

Contains: Defines typed containers for raw and normalized SearXNG results.

- `SearXNGQueryResult` - Main class/object for this file.
- `SearXNGQueryResult.to_json()` - Serializes the object into a JSON-ready shape.
- `SearXNGSearchResult` - Main class/object for this file.
- `SearXNGSearchResult.to_json()` - Serializes the object into a JSON-ready shape.

### services/scraper-python/app/parsers

HTML parsing helpers for retailer pages.

#### services/scraper-python/app/parsers/__init__.py

Purpose: Package marker for parser helpers.

Contains: Makes the parsers subpackage importable.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/parsers/amazon_bs4.py

Purpose: Amazon HTML parser.

Contains: Extracts search results and product detail data from Amazon HTML using BeautifulSoup.

- `parse_search_results()` - Parses search results.
- `parse_detail()` - Parses detail.

#### services/scraper-python/app/parsers/common.py

Purpose: Shared parser helper library.

Contains: Provides soup creation, image extraction, model construction, and text/rating parsing helpers.

- `soup_for()` - Implements soup for in the module.
- `_dedupe_urls()` - Implements dedupe urls in the module.
- `pick_image_urls()` - Selects  image urls from available options.
- `pick_image_url()` - Selects  image url from available options.
- `build_product()` - Builds product.
- `build_review()` - Builds review.
- `rating_from_text()` - Implements rating from text in the module.
- `review_count_from_text()` - Implements review count from text in the module.

### services/scraper-python/app/providers

Retailer-specific search/detail providers and shared HTTP utilities.

#### services/scraper-python/app/providers/__init__.py

Purpose: Package marker for providers.

Contains: Makes retailer providers importable as a group.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/providers/amazon_playwright.py

Purpose: Amazon provider using Playwright.

Contains: Searches Amazon through browser automation and enriches product details when scripted browsing is required.

- `AmazonPlaywrightProvider` - Main class/object for this file.
- `AmazonPlaywrightProvider.supports_url()` - Checks whether this component can handle a given URL.
- `AmazonPlaywrightProvider._fetch_html()` - Implements fetch html in the provider layer.
- `AmazonPlaywrightProvider.search()` - Runs the main search flow for this component.
- `AmazonPlaywrightProvider.search_by_urls()` - Processes an explicit set of URLs instead of starting from a plain-text query.
- `AmazonPlaywrightProvider.enrich_product()` - Fetches additional detail for a product that was already discovered.

#### services/scraper-python/app/providers/amazon_requests.py

Purpose: Amazon provider using direct HTTP requests.

Contains: Searches Amazon and enriches products without browser automation when possible.

- `AmazonRequestsProvider` - Main class/object for this file.
- `AmazonRequestsProvider.supports_url()` - Checks whether this component can handle a given URL.
- `AmazonRequestsProvider.search()` - Runs the main search flow for this component.
- `AmazonRequestsProvider.search_by_urls()` - Processes an explicit set of URLs instead of starting from a plain-text query.
- `AmazonRequestsProvider.enrich_product()` - Fetches additional detail for a product that was already discovered.

#### services/scraper-python/app/providers/base.py

Purpose: Shared provider models and interface.

Contains: Defines provider dataclasses and the abstract contract all retailer providers follow.

- `ProviderReview` - Data/error/schema class declared by this file.
- `ProviderProduct` - Data/error/schema class declared by this file.
- `ProviderSearchResult` - Data/error/schema class declared by this file.
- `BaseProvider` - Main class/object for this file.
- `BaseProvider.search()` - Runs the main search flow for this component.
- `BaseProvider.supports_url()` - Checks whether this component can handle a given URL.
- `BaseProvider.search_by_urls()` - Processes an explicit set of URLs instead of starting from a plain-text query.
- `BaseProvider.enrich_product()` - Fetches additional detail for a product that was already discovered.

#### services/scraper-python/app/providers/http.py

Purpose: Shared HTTP utility layer for providers.

Contains: Creates resilient clients/headers and provides fetch helpers with proxy and fallback behavior.

- `build_client()` - Builds client.
- `build_headers()` - Builds headers.
- `_fetch_text_via_httpx()` - Implements fetch text via httpx in the provider layer.
- `fetch_text_via_urllib()` - Implements fetch text via urllib in the provider layer.
- `fetch_text_resilient()` - Implements fetch text resilient in the provider layer.

#### services/scraper-python/app/providers/target_requests.py

Purpose: Target provider using direct HTTP requests.

Contains: Parses Target search/detail responses, builds product objects, and fetches review data.

- `_clean_target_text()` - Implements clean target text in the provider layer.
- `_extract_target_data()` - Extracts target data.
- `_extract_api_key()` - Extracts api key.
- `_fallback_page_path()` - Implements fallback page path in the provider layer.
- `_extract_search_context()` - Extracts search context.
- `_find_search_response()` - Implements find search response in the provider layer.
- `_pick_image_url()` - Implements pick image url in the provider layer.
- `_extract_image_urls()` - Extracts image urls.
- `_product_description_text()` - Implements product description text in the provider layer.
- `_review_count()` - Implements review count in the provider layer.
- `_build_target_product()` - Builds target product.
- `_parse_products()` - Parses products.
- `_find_detail_product()` - Implements find detail product in the provider layer.
- `_build_reviews()` - Builds reviews.
- `_parse_detail()` - Parses detail.
- `TargetRequestsProvider` - Main class/object for this file.
- `TargetRequestsProvider.__init__()` - Initializes the object and wires in its dependencies/configuration.
- `TargetRequestsProvider._fetch_search_html()` - Implements fetch search html in the provider layer.
- `TargetRequestsProvider._fetch_product_html()` - Implements fetch product html in the provider layer.
- `TargetRequestsProvider.supports_url()` - Checks whether this component can handle a given URL.
- `TargetRequestsProvider.search()` - Runs the main search flow for this component.
- `TargetRequestsProvider.enrich_product()` - Fetches additional detail for a product that was already discovered.
- `TargetRequestsProvider.search_by_urls()` - Processes an explicit set of URLs instead of starting from a plain-text query.

#### services/scraper-python/app/providers/walmart_requests.py

Purpose: Walmart provider using direct HTTP requests.

Contains: Parses Walmart search/detail payloads and converts them into normalized products.

- `_extract_next_data()` - Extracts next data.
- `_pick_image()` - Implements pick image in the provider layer.
- `_extract_images()` - Extracts images.
- `_parse_search()` - Parses search.
- `_extract_item_id()` - Extracts item id.
- `_parse_detail()` - Parses detail.
- `WalmartRequestsProvider` - Main class/object for this file.
- `WalmartRequestsProvider.supports_url()` - Checks whether this component can handle a given URL.
- `WalmartRequestsProvider.search()` - Runs the main search flow for this component.
- `WalmartRequestsProvider.search_by_urls()` - Processes an explicit set of URLs instead of starting from a plain-text query.
- `WalmartRequestsProvider.enrich_product()` - Fetches additional detail for a product that was already discovered.

### services/scraper-python/app/storage

Database, image cache, schema, and Postgres compatibility logic.

#### services/scraper-python/app/storage/__init__.py

Purpose: Package marker for storage helpers.

Contains: Makes storage modules importable.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/storage/db.py

Purpose: Main persistence and business-rule layer.

Contains: Creates and migrates schema, upserts products, powers catalog/search/detail/related/favorites/history/recommendations flows, and maintains caches/repair jobs.

- `get_connection()` - Returns connection.
- `_is_locked_error()` - Checks whether locked error.
- `_write_connection()` - Implements write connection in the storage layer.
- `initialize_database()` - Implements initialize database in the storage layer.
- `_purge_products_by_ids()` - Implements purge products by ids in the storage layer.
- `_purge_products_without_images()` - Implements purge products without images in the storage layer.
- `_invalidate_related_derived_caches()` - Implements invalidate related derived caches in the storage layer.
- `_clear_image_cache_dir()` - Clears image cache dir.
- `_count_table_rows()` - Counts table rows.
- `reset_product_linked_state()` - Implements reset product linked state in the storage layer.
- `_table_columns()` - Implements table columns in the storage layer.
- `_ensure_postgres_schema_migrations()` - Ensures postgres schema migrations is ready before the flow continues.
- `_ensure_schema_migrations()` - Ensures schema migrations is ready before the flow continues.
- `_reclassify_existing_products()` - Reclassifies existing products.
- `_populate_variant_metadata()` - Implements populate variant metadata in the storage layer.
- `_repair_invalid_product_pricing()` - Repairs invalid product pricing.
- `_repair_book_like_product_categories()` - Repairs book like product categories.
- `_now_datetime()` - Implements now datetime in the storage layer.
- `_parse_iso()` - Parses iso.
- `_decode_json_array()` - Decodes json array.
- `_normalize_lookup_key()` - Normalizes lookup key.
- `_dedupe_strings()` - Implements dedupe strings in the storage layer.
- `_merge_image_gallery_urls()` - Merges image gallery urls.
- `_decode_image_gallery()` - Decodes image gallery.
- `_has_any_image_urls()` - Checks whether any image urls is available.
- `_resolved_primary_image_url()` - Implements resolved primary image url in the storage layer.
- `_record_gallery_urls()` - Records gallery urls.
- `_record_has_any_image()` - Records has any image.
- `_row_has_any_image()` - Implements row has any image in the storage layer.
- `_filter_rows_with_images()` - Filters rows with images.
- `_find_nested_value()` - Implements find nested value in the storage layer.
- `_extract_variant_attributes()` - Extracts variant attributes.
- `_build_variant_label()` - Builds variant label.
- `_build_family_key()` - Builds family key.
- `_row_identity_key()` - Implements row identity key in the storage layer.
- `_row_sort_key()` - Implements row sort key in the storage layer.
- `_dedupe_rows()` - Implements dedupe rows in the storage layer.
- `_product_identity_key()` - Implements product identity key in the storage layer.
- `_product_sort_key()` - Implements product sort key in the storage layer.
- `_dedupe_product_list()` - Implements dedupe product list in the storage layer.
- `_classification_debug_payload()` - Implements classification debug payload in the storage layer.
- `_candidate_looks_like_book()` - Implements candidate looks like book in the storage layer.
- `_product_to_row()` - Implements product to row in the storage layer.
- `_fallback_image_meta()` - Implements fallback image meta in the storage layer.
- `_runtime_image_path()` - Implements runtime image path in the storage layer.
- `_row_to_product()` - Implements row to product in the storage layer.
- `_decode_json_object()` - Decodes json object.
- `_snapshot_from_product()` - Implements snapshot from product in the storage layer.
- `_snapshot_from_row()` - Implements snapshot from row in the storage layer.
- `_restore_snapshot()` - Implements restore snapshot in the storage layer.
- `prepare_product_candidates()` - Prepares product candidates for the next step.
- `rank_product_candidates_for_query()` - Ranks product candidates for query by relevance or score.
- `resolve_batch_category()` - Resolves batch category.
- `_apply_batch_category_to_candidate()` - Implements apply batch category to candidate in the storage layer.
- `apply_batch_category_to_candidates()` - Implements apply batch category to candidates in the storage layer.
- `_purge_existing_products_by_canonical_urls()` - Implements purge existing products by canonical urls in the storage layer.
- `_preserve_existing_category_fields()` - Implements preserve existing category fields in the storage layer.
- `_apply_existing_category_precedence()` - Implements apply existing category precedence in the storage layer.
- `upsert_prepared_product_candidates()` - Implements upsert prepared product candidates in the storage layer.
- `favorite_product_ids_for_user()` - Implements favorite product ids for user in the storage layer.
- `annotate_products_with_favorites()` - Implements annotate products with favorites in the storage layer.
- `_build_categories()` - Builds categories.
- `_offer_snapshot_period_key()` - Implements offer snapshot period key in the storage layer.
- `_discount_percentage_from_row()` - Implements discount percentage from row in the storage layer.
- `_offer_recency_score()` - Implements offer recency score in the storage layer.
- `_offer_daily_bias()` - Implements offer daily bias in the storage layer.
- `_offer_snapshot_expiry()` - Implements offer snapshot expiry in the storage layer.
- `_discounted_product_rows()` - Implements discounted product rows in the storage layer.
- `_load_featured_offer_rows_from_ids()` - Loads featured offer rows from ids.
- `_generate_featured_offer_product_ids()` - Implements generate featured offer product ids in the storage layer.
- `_store_featured_offer_snapshot()` - Implements store featured offer snapshot in the storage layer.
- `_build_offers()` - Builds offers.
- `_interleaved_products()` - Implements interleaved products in the storage layer.
- `_catalog_response()` - Implements catalog response in the storage layer.
- `upsert_products()` - Implements upsert products in the storage layer.
- `replace_reviews()` - Implements replace reviews in the storage layer.
- `_generate_collection_code()` - Implements generate collection code in the storage layer.
- `_resolve_collection_group_category_id()` - Resolves collection group category id.
- `_assign_primary_collection_code()` - Implements assign primary collection code in the storage layer.
- `_store_collection_group_page()` - Implements store collection group page in the storage layer.
- `_next_append_positions()` - Implements next append positions in the storage layer.
- `save_query_results()` - Saves query results.
- `append_query_results()` - Implements append query results in the storage layer.
- `clear_query_results()` - Clears query results.
- `set_query_status()` - Implements set query status in the storage layer.
- `get_query_metadata()` - Returns query metadata.
- `cache_discovery_response()` - Implements cache discovery response in the storage layer.
- `get_cached_discovery_response()` - Returns cached discovery response.
- `store_discovery_hits()` - Implements store discovery hits in the storage layer.
- `list_discovery_hits()` - Lists discovery hits.
- `mark_discovery_failure()` - Implements mark discovery failure in the storage layer.
- `get_suppressed_discovery_urls()` - Returns suppressed discovery urls.
- `count_query_results()` - Counts query results.
- `count_products()` - Counts products.
- `category_counts()` - Implements category counts in the storage layer.
- `list_active_product_ids()` - Lists active product ids.
- `has_bootstrap_coverage()` - Checks whether bootstrap coverage is available.
- `list_products()` - Lists products.
- `list_query_products()` - Lists query products.
- `_search_record_tags()` - Implements search record tags in the storage layer.
- `_build_search_haystack_parts()` - Builds search haystack parts.
- `_build_search_token_set()` - Builds search token set.
- `_matches_phrase_or_token()` - Implements matches phrase or token in the storage layer.
- `_record_value()` - Records value.
- `_related_record_tags()` - Implements related record tags in the storage layer.
- `_build_related_token_set()` - Builds related token set.
- `_related_overlap_metrics()` - Implements related overlap metrics in the storage layer.
- `_related_candidate_passes_threshold()` - Implements related candidate passes threshold in the storage layer.
- `filter_related_product_candidates()` - Filters related product candidates.
- `_general_related_search_query()` - Implements general related search query in the storage layer.
- `_related_search_queries()` - Implements related search queries in the storage layer.
- `_keyword_terms()` - Implements keyword terms in the storage layer.
- `_keyword_term_is_meaningful()` - Implements keyword term is meaningful in the storage layer.
- `_compressed_keyword_terms()` - Implements compressed keyword terms in the storage layer.
- `_append_unique_keyword()` - Implements append unique keyword in the storage layer.
- `_join_keyword_terms()` - Implements join keyword terms in the storage layer.
- `extract_product_keywords()` - Extracts product keywords.
- `_related_fulltext_query_text()` - Implements related fulltext query text in the storage layer.
- `_search_products_by_keyword_for_related()` - Implements search products by keyword for related in the storage layer.
- `_fulltext_search_related_rows()` - Implements fulltext search related rows in the storage layer.
- `_candidate_title_keyword_hit_count()` - Implements candidate title keyword hit count in the storage layer.
- `_price_similarity_bonus()` - Implements price similarity bonus in the storage layer.
- `_score_related_candidate()` - Calculates a score for related candidate.
- `_compute_related_scores()` - Implements compute related scores in the storage layer.
- `find_related_products_hybrid()` - Implements find related products hybrid in the storage layer.
- `_collection_group_related_candidates()` - Implements collection group related candidates in the storage layer.
- `_shared_query_count_lookup()` - Implements shared query count lookup in the storage layer.
- `_query_specific_adjustment()` - Implements query specific adjustment in the storage layer.
- `_normalize_search_score()` - Normalizes search score.
- `search_cached_products()` - Implements search cached products in the storage layer.
- `rank_product_ids_for_query()` - Ranks product ids for query by relevance or score.
- `get_product()` - Returns product.
- `get_source_image_url()` - Returns source image url.
- `get_related_products()` - Returns related products.
- `_variant_summary_from_row()` - Implements variant summary from row in the storage layer.
- `get_product_with_reviews()` - Returns product with reviews.
- `_normalize_email()` - Normalizes email.
- `_password_hash()` - Implements password hash in the storage layer.
- `_hash_token()` - Implements hash token in the storage layer.
- `_session_expiry()` - Implements session expiry in the storage layer.
- `_supabase_auth_enabled()` - Implements supabase auth enabled in the storage layer.
- `_supabase_request()` - Implements supabase request in the storage layer.
- `_decode_token_claims()` - Decodes token claims.
- `_upsert_profile_for_supabase_user()` - Implements upsert profile for supabase user in the storage layer.
- `_public_user_payload()` - Implements public user payload in the storage layer.
- `create_user()` - Implements create user in the storage layer.
- `authenticate_user()` - Implements authenticate user in the storage layer.
- `get_auth_context_by_token()` - Returns auth context by token.
- `get_user_by_token()` - Returns user by token.
- `get_user_id_by_token()` - Returns user id by token.
- `logout_user()` - Implements logout user in the storage layer.
- `_decayed_score()` - Implements decayed score in the storage layer.
- `_bump_affinity()` - Implements bump affinity in the storage layer.
- `get_top_interests()` - Returns top interests.
- `_event_filter_clause()` - Implements event filter clause in the storage layer.
- `_dominant_category_id()` - Implements dominant category id in the storage layer.
- `_build_event_affinities()` - Builds event affinities.
- `_build_favorite_affinities()` - Builds favorite affinities.
- `_favorite_signal_strength()` - Implements favorite signal strength in the storage layer.
- `_build_history_trending_products()` - Builds history trending products.
- `record_user_event()` - Records user event.
- `invalidate_user_recommendations()` - Implements invalidate user recommendations in the storage layer.
- `add_user_favorite()` - Implements add user favorite in the storage layer.
- `remove_user_favorite()` - Implements remove user favorite in the storage layer.
- `get_user_favorite()` - Returns user favorite.
- `list_user_favorites()` - Lists user favorites.
- `refresh_user_recommendations()` - Implements refresh user recommendations in the storage layer.
- `list_user_recommendations()` - Lists user recommendations.
- `list_user_history()` - Lists user history.

#### services/scraper-python/app/storage/images.py

Purpose: Image cache manager.

Contains: Downloads product images, validates them, stores them locally, and resolves cached image paths.

- `prepare_image_cache_dir()` - Prepares image cache dir for the next step.
- `_extension_for_format()` - Implements extension for format in the module.
- `_extension_for_content_type()` - Implements extension for content type in the module.
- `cache_image()` - Implements cache image in the module.
- `resolve_image_path()` - Resolves image path.

#### services/scraper-python/app/storage/postgres_compat.py

Purpose: SQLite-to-Postgres compatibility adapter.

Contains: Lets the codebase keep a mostly SQLite-shaped query style while running against Supabase Postgres.

- `postgres_enabled()` - Implements postgres enabled in the module.
- `_replace_placeholders()` - Implements replace placeholders in the module.
- `_replace_named_placeholders()` - Implements replace named placeholders in the module.
- `_escape_literal_percents()` - Implements escape literal percents in the module.
- `_rewrite_insert_or_replace()` - Implements rewrite insert or replace in the module.
- `translate_sql()` - Implements translate sql in the module.
- `_reset_connection()` - Implements reset connection in the module.
- `_connection_is_unusable()` - Implements connection is unusable in the module.
- `_ensure_connection()` - Ensures connection is ready before the flow continues.
- `get_connection()` - Returns connection.
- `BufferedResult` - Main class/object for this file.
- `BufferedResult.__init__()` - Initializes the object and wires in its dependencies/configuration.
- `BufferedResult.fetchone()` - Implements fetchone in the module.
- `BufferedResult.fetchall()` - Implements fetchall in the module.
- `PostgresConnectionWrapper` - Main class/object for this file.
- `PostgresConnectionWrapper.__init__()` - Initializes the object and wires in its dependencies/configuration.
- `PostgresConnectionWrapper.execute()` - Implements execute in the module.
- `PostgresConnectionWrapper.executescript()` - Implements executescript in the module.
- `PostgresConnectionWrapper.commit()` - Implements commit in the module.
- `PostgresConnectionWrapper.rollback()` - Implements rollback in the module.

#### services/scraper-python/app/storage/postgres_schema.sql

Purpose: Postgres schema definition.

Contains: Creates the tables and indexes used when the backend runs on Supabase/Postgres.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/app/storage/schema.sql

Purpose: SQLite schema definition.

Contains: Creates the local development/test database structure.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### services/scraper-python/data

Placeholder directory for local Python service data files.

#### services/scraper-python/data/.gitkeep

Purpose: Placeholder file for the Python data directory.

Contains: Keeps the otherwise-empty data folder in version control.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### services/scraper-python/scripts

Maintenance and migration scripts for SQLite, Supabase, catalog reset, and recovery actions.

#### services/scraper-python/scripts/_supabase_utils.py

Purpose: Shared helpers for Supabase migration scripts.

Contains: Provides env handling, Postgres connections, SQL execution, JSON normalization, and admin API requests.

- `env_value()` - Implements env value in the module.
- `connect_postgres()` - Implements connect postgres in the module.
- `execute_sql_script()` - Implements execute sql script in the module.
- `json_ready()` - Implements json ready in the module.
- `supabase_admin_request()` - Implements supabase admin request in the module.

#### services/scraper-python/scripts/audit_sqlite_for_supabase.py

Purpose: SQLite audit/export tool.

Contains: Inspects the local SQLite database and produces migration-audit outputs for a Supabase move.

- `parse_args()` - Parses args.
- `sqlite_tables()` - Implements sqlite tables in the module.
- `write_json()` - Implements write json in the module.
- `write_jsonl()` - Implements write jsonl in the module.
- `build_schema_summary()` - Builds schema summary.
- `main()` - Implements main in the module.

#### services/scraper-python/scripts/check_supabase_connection.py

Purpose: Supabase connectivity smoke test.

Contains: Verifies that the configured Postgres/Supabase connection is working.

- `parse_args()` - Parses args.
- `check_http()` - Implements check http in the module.
- `main()` - Implements main in the module.

#### services/scraper-python/scripts/export_ai_training_jsonl.py

Purpose: AI-training export tool.

Contains: Exports project data into JSONL suitable for AI evaluation or fine-tuning workflows.

- `main()` - Implements main in the module.

#### services/scraper-python/scripts/migrate_sqlite_to_supabase.py

Purpose: SQLite-to-Supabase migration script.

Contains: Copies local catalog/auth data into Supabase/Postgres and can trigger password recovery for migrated users.

- `parse_args()` - Parses args.
- `sqlite_connection()` - Implements sqlite connection in the module.
- `sqlite_rows()` - Implements sqlite rows in the module.
- `prepare_row()` - Prepares row for the next step.
- `build_exists_sql()` - Builds exists sql.
- `build_upsert_sql()` - Builds upsert sql.
- `existing_key_set()` - Implements existing key set in the module.
- `list_supabase_users()` - Lists supabase users.
- `ensure_supabase_user()` - Ensures supabase user is ready before the flow continues.
- `trigger_password_recovery()` - Implements trigger password recovery in the module.
- `migrate_profiles_and_auth()` - Implements migrate profiles and auth in the module.
- `migrate_table()` - Implements migrate table in the module.
- `apply_schema()` - Implements apply schema in the module.
- `default_report_path()` - Implements default report path in the module.
- `main()` - Implements main in the module.

#### services/scraper-python/scripts/reset_catalog.py

Purpose: Offline catalog reset and reseed script.

Contains: Deletes product-linked state, optionally reseeds the catalog baseline, and validates the resulting counts.

- `_health_ok()` - Implements health ok in the module.
- `_warn_if_runtime_active()` - Implements warn if runtime active in the module.
- `_build_parser()` - Builds parser.
- `_run()` - Implements run in the module.
- `_validate_thresholds()` - Validates thresholds.
- `main()` - Implements main in the module.

#### services/scraper-python/scripts/trigger_supabase_password_resets.py

Purpose: Bulk recovery-email trigger.

Contains: Sends Supabase password reset emails to migrated profile users.

- `parse_args()` - Parses args.
- `trigger_recovery()` - Implements trigger recovery in the module.
- `main()` - Implements main in the module.

### services/scraper-python/tests

Automated test suite covering AI parsing, DB logic, discovery, and HTML parsers.

#### services/scraper-python/tests/test_ai.py

Purpose: AI test suite.

Contains: Checks rewrite parsing, prompt contracts, category judgment behavior, and AI integration edge cases.

- `AITests` - Main class/object for this file.
- `AITests.setUp()` - Test helper used by this test module.
- `AITests.tearDown()` - Test helper used by this test module.
- `AITests._seed_product()` - Test helper used by this test module.
- `AITests.test_generate_rewrite_plan_uses_cache()` - Validates that generate rewrite plan uses cache.
- `AITests.test_search_pipeline_reports_ai_disabled()` - Validates that search pipeline reports ai disabled.
- `AITests.test_search_uses_retailer_variants_for_discovery_only()` - Validates that search uses retailer variants for discovery only.
- `AITests.test_category_judge_falls_back_on_model_error()` - Validates that category judge falls back on model error.

#### services/scraper-python/tests/test_db.py

Purpose: Database and catalog logic test suite.

Contains: Exercises product persistence, category repair, related-product ranking, history, favorites, recommendations, resets, and many edge cases.

- `DatabaseTests` - Main class/object for this file.
- `DatabaseTests.setUp()` - Test helper used by this test module.
- `DatabaseTests.tearDown()` - Test helper used by this test module.
- `DatabaseTests._make_product()` - Test helper used by this test module.
- `DatabaseTests._image_meta()` - Test helper used by this test module.
- `DatabaseTests._prepare_candidates()` - Test helper used by this test module.
- `DatabaseTests._upsert()` - Test helper used by this test module.
- `DatabaseTests.test_upsert_and_list_products()` - Validates that upsert and list products.
- `DatabaseTests.test_upsert_persists_products_when_image_prefetch_fails()` - Validates that upsert persists products when image prefetch fails.
- `DatabaseTests.test_upsert_skips_products_without_any_image()` - Validates that upsert skips products without any image.
- `DatabaseTests.test_initialize_database_purges_existing_products_without_images()` - Validates that initialize database purges existing products without images.
- `DatabaseTests.test_extract_product_keywords_keeps_core_title_phrases()` - Validates that extract product keywords keeps core title phrases.
- `DatabaseTests.test_find_related_products_hybrid_limits_results_to_same_category()` - Validates that find related products hybrid limits results to same category.
- `DatabaseTests.test_postgres_related_fulltext_query_omits_null_category_parameter_clause()` - Validates that postgres related fulltext query omits null category parameter clause.
- `DatabaseTests.test_batch_requested_category_forces_all_ranked_candidates_into_same_category()` - Validates that batch requested category forces all ranked candidates into same category.
- `DatabaseTests.test_batch_resolution_uses_dominant_non_other_category_without_request()` - Validates that batch resolution uses dominant non other category without request.
- `DatabaseTests.test_batch_resolution_keeps_all_other_batches_in_others()` - Validates that batch resolution keeps all other batches in others.
- `DatabaseTests.test_ranked_batch_persistence_does_not_store_rejected_candidates()` - Validates that ranked batch persistence does not store rejected candidates.
- `DatabaseTests.test_upsert_prepared_candidates_does_not_downgrade_non_other_category()` - Validates that upsert prepared candidates does not downgrade non other category.
- `DatabaseTests.test_query_results_and_append_share_one_collection_code()` - Validates that query results and append share one collection code.
- `DatabaseTests.test_related_products_prioritize_same_collection_before_category_fallback()` - Validates that related products prioritize same collection before category fallback.
- `DatabaseTests.test_related_product_fallback_excludes_cross_category_matches()` - Validates that related product fallback excludes cross category matches.
- `DatabaseTests.test_reset_product_linked_state_preserves_users_sessions_and_search_history()` - Validates that reset product linked state preserves users sessions and search history.
- `DatabaseTests.test_initialize_database_clears_stale_related_caches_only()` - Validates that initialize database clears stale related caches only.
- `DatabaseTests.test_upsert_skips_zero_price_and_sanitizes_impossible_discounts()` - Validates that upsert skips zero price and sanitizes impossible discounts.
- `DatabaseTests.test_list_query_products_excludes_inactive_products()` - Validates that list query products excludes inactive products.
- `DatabaseTests.test_auth_events_and_recommendations()` - Validates that auth events and recommendations.
- `DatabaseTests.test_history_only_tracks_allowed_product_origins_and_dedupes()` - Validates that history only tracks allowed product origins and dedupes.
- `DatabaseTests.test_family_dedupe_keeps_retailers_distinct()` - Validates that family dedupe keeps retailers distinct.
- `DatabaseTests.test_product_detail_includes_gallery_and_variants()` - Validates that product detail includes gallery and variants.
- `DatabaseTests.test_favorites_drive_recommendations_before_history()` - Validates that favorites drive recommendations before history.
- `DatabaseTests.test_daily_featured_offers_are_cached_and_regenerated_for_inactive_products()` - Validates that daily featured offers are cached and regenerated for inactive products.
- `DatabaseTests.test_history_trending_products_follow_recent_activity()` - Validates that history trending products follow recent activity.
- `DatabaseTests.test_trending_products_are_empty_without_history_signal()` - Validates that trending products are empty without history signal.
- `DatabaseTests.test_cached_search_requires_real_text_or_category_evidence()` - Validates that cached search requires real text or category evidence.
- `DatabaseTests.test_history_can_be_scoped_to_app_session()` - Validates that history can be scoped to app session.
- `DatabaseTests.test_visible_history_excludes_source_and_category_events()` - Validates that visible history excludes source and category events.
- `DatabaseTests.test_session_scoped_recommendations_follow_current_login_interest()` - Validates that session scoped recommendations follow current login interest.
- `DatabaseTests.test_favorites_round_trip_and_favorite_flags()` - Validates that favorites round trip and favorite flags.
- `DatabaseTests.test_event_writes_do_not_refresh_recommendations()` - Validates that event writes do not refresh recommendations.
- `DatabaseTests.test_favorite_mutations_invalidate_without_refresh()` - Validates that favorite mutations invalidate without refresh.
- `DatabaseTests.test_history_product_entries_include_snapshot()` - Validates that history product entries include snapshot.
- `DatabaseTests.test_related_products_prioritize_relevant_same_category_matches()` - Validates that related products prioritize relevant same category matches.
- `DatabaseTests.test_related_products_for_others_require_real_family_overlap()` - Validates that related products for others require real family overlap.
- `DatabaseTests.test_stored_related_rows_are_revalidated_before_return()` - Validates that stored related rows are revalidated before return.
- `DatabaseTests.test_search_driven_related_results_beat_cached_interest_graph_matches()` - Validates that search driven related results beat cached interest graph matches.
- `DatabaseTests.test_query_expansion_prefers_generated_titles()` - Validates that query expansion prefers generated titles.
- `DatabaseTests.test_category_classification_covers_common_search_families()` - Validates that category classification covers common search families.
- `DatabaseTests.test_book_like_product_stays_out_of_requested_electronics_category()` - Validates that book like product stays out of requested electronics category.
- `DatabaseTests.test_initialize_database_repairs_book_like_electronics_rows()` - Validates that initialize database repairs book like electronics rows.
- `DatabaseTests.test_short_brand_query_does_not_match_substrings()` - Validates that short brand query does not match substrings.
- `DatabaseTests.test_rank_query_allows_strong_text_matches_from_others()` - Validates that rank query allows strong text matches from others.
- `DatabaseTests.test_rank_query_filters_unrelated_candidates_for_blender()` - Validates that rank query filters unrelated candidates for blender.

#### services/scraper-python/tests/test_discovery.py

Purpose: Discovery pipeline test suite.

Contains: Verifies discovery caching, normalization, ranking, and query behavior.

- `DiscoveryTests` - Main class/object for this file.
- `DiscoveryTests.setUp()` - Test helper used by this test module.
- `DiscoveryTests.tearDown()` - Test helper used by this test module.
- `DiscoveryTests._seed_product()` - Test helper used by this test module.
- `DiscoveryTests.test_normalize_result_maps_allowed_provider()` - Validates that normalize result maps allowed provider.
- `DiscoveryTests.test_normalize_result_preserves_unsupported_domain_as_candidate()` - Validates that normalize result preserves unsupported domain as candidate.
- `DiscoveryTests.test_normalize_result_rejects_noise_domain()` - Validates that normalize result rejects noise domain.
- `DiscoveryTests.test_discovery_cache_round_trip()` - Validates that discovery cache round trip.
- `DiscoveryTests.test_normalize_apify_entry_maps_organic_results()` - Validates that normalize apify entry maps organic results.
- `DiscoveryTests.test_apify_token_alias_is_accepted()` - Validates that apify token alias is accepted.
- `DiscoveryTests.test_build_search_seed_queries_uses_cleaned_apify_titles()` - Validates that build search seed queries uses cleaned apify titles.
- `DiscoveryTests.test_build_search_seed_queries_uses_supported_then_extra_domain_titles()` - Validates that build search seed queries uses supported then extra domain titles.
- `DiscoveryTests.test_build_related_seed_queries_prefers_brand_title_then_related_titles()` - Validates that build related seed queries prefers brand title then related titles.
- `DiscoveryTests.test_build_related_family_query_keeps_product_family_terms()` - Validates that build related family query keeps product family terms.
- `DiscoveryTests.test_apify_client_falls_back_to_single_query_requests()` - Validates that apify client falls back to single query requests.
- `DiscoveryTests.test_searxng_client_falls_back_to_single_engine_requests()` - Validates that searxng client falls back to single engine requests.
- `DiscoveryTests.test_search_uses_discovery_results_for_weak_first_page()` - Validates that search uses discovery results for weak first page.
- `DiscoveryTests.test_run_term_search_skips_provider_exceptions()` - Validates that run term search skips provider exceptions.
- `DiscoveryTests.test_backfill_product_galleries_enriches_existing_products()` - Validates that backfill product galleries enriches existing products.
- `DiscoveryTests.test_search_page_one_merges_searxng_after_apify()` - Validates that search page one merges searxng after apify.
- `DiscoveryTests.test_search_page_two_uses_searxng_only_pagination()` - Validates that search page two uses searxng only pagination.
- `DiscoveryTests.test_related_page_two_uses_hybrid_pagination()` - Validates that related page two uses hybrid pagination.
- `DiscoveryTests.test_get_related_returns_hybrid_same_category_results()` - Validates that get related returns hybrid same category results.
- `DiscoveryTests.test_get_related_uses_hybrid_keyword_extraction_for_broad_title_match()` - Validates that get related uses hybrid keyword extraction for broad title match.
- `DiscoveryTests.test_get_detail_populates_related_products_from_hybrid_lookup()` - Validates that get detail populates related products from hybrid lookup.
- `DiscoveryTests.test_related_page_ignores_old_related_cache_rows_when_hybrid_finds_better_match()` - Validates that related page ignores old related cache rows when hybrid finds better match.
- `DiscoveryTests.test_run_term_search_forces_requested_batch_category_for_all_accepted_products()` - Validates that run term search forces requested batch category for all accepted products.
- `DiscoveryTests.test_search_show_more_reuses_active_collection_code_for_appended_results()` - Validates that search show more reuses active collection code for appended results.
- `DiscoveryTests.test_reseed_category_baseline_assigns_collection_code_to_query_batch()` - Validates that reseed category baseline assigns collection code to query batch.
- `DiscoveryTests.test_get_related_prefers_same_collection_results_before_fallback()` - Validates that get related prefers same collection results before fallback.
- `DiscoveryTests.test_search_falls_back_cleanly_when_discovery_fails()` - Validates that search falls back cleanly when discovery fails.

#### services/scraper-python/tests/test_parsers.py

Purpose: Parser test suite.

Contains: Validates retailer HTML parsing using saved fixture pages.

- `ParserTests` - Main class/object for this file.
- `ParserTests.test_amazon_search_parser_extracts_product()` - Validates that amazon search parser extracts product.
- `ParserTests.test_walmart_search_parser_extracts_product()` - Validates that walmart search parser extracts product.
- `ParserTests.test_target_search_parser_extracts_product()` - Validates that target search parser extracts product.
- `ParserTests.test_amazon_detail_parser_collects_dynamic_image_gallery()` - Validates that amazon detail parser collects dynamic image gallery.

### services/scraper-python/tests/fixtures

Captured retailer HTML used by parser tests.

#### services/scraper-python/tests/fixtures/amazon_search.html

Purpose: Saved HTML fixture for parser tests.

Contains: Contains a captured Amazon page used to validate parsing logic.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/tests/fixtures/target_search.html

Purpose: Saved HTML fixture for parser tests.

Contains: Contains a captured Target page used to validate parsing logic.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### services/scraper-python/tests/fixtures/walmart_search.html

Purpose: Saved HTML fixture for parser tests.

Contains: Contains a captured Walmart page used to validate parsing logic.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/enhanced_checkout

Files grouped under `stitch/enhanced_checkout`.

#### stitch/enhanced_checkout/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/enhanced_checkout/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/enhanced_home_screen

Files grouped under `stitch/enhanced_home_screen`.

#### stitch/enhanced_home_screen/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/enhanced_home_screen/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/enhanced_login

Files grouped under `stitch/enhanced_login`.

#### stitch/enhanced_login/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/enhanced_login/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/enhanced_product_detail

Files grouped under `stitch/enhanced_product_detail`.

#### stitch/enhanced_product_detail/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/enhanced_product_detail/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/enhanced_product_listing

Files grouped under `stitch/enhanced_product_listing`.

#### stitch/enhanced_product_listing/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/enhanced_product_listing/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/enhanced_shopping_cart

Files grouped under `stitch/enhanced_shopping_cart`.

#### stitch/enhanced_shopping_cart/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/enhanced_shopping_cart/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/sign_up

Files grouped under `stitch/sign_up`.

#### stitch/sign_up/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/sign_up/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch/user_profile

Files grouped under `stitch/user_profile`.

#### stitch/user_profile/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch/user_profile/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch_home_screen/aix_store_home_enhanced

Files grouped under `stitch_home_screen/aix_store_home_enhanced`.

#### stitch_home_screen/aix_store_home_enhanced/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch_home_screen/aix_store_home_enhanced/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch_home_screen/enhanced_login

Files grouped under `stitch_home_screen/enhanced_login`.

#### stitch_home_screen/enhanced_login/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch_home_screen/enhanced_login/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch_home_screen/enhanced_product_detail

Files grouped under `stitch_home_screen/enhanced_product_detail`.

#### stitch_home_screen/enhanced_product_detail/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch_home_screen/enhanced_product_detail/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch_home_screen/enhanced_product_listing

Files grouped under `stitch_home_screen/enhanced_product_listing`.

#### stitch_home_screen/enhanced_product_listing/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch_home_screen/enhanced_product_listing/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch_home_screen/sign_up

Files grouped under `stitch_home_screen/sign_up`.

#### stitch_home_screen/sign_up/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch_home_screen/sign_up/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

### stitch_home_screen/user_profile

Files grouped under `stitch_home_screen/user_profile`.

#### stitch_home_screen/user_profile/code.html

Purpose: Static prototype screen export.

Contains: Contains HTML exported from a design/prototyping workflow for visual reference.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

#### stitch_home_screen/user_profile/screen.png

Purpose: Prototype screenshot.

Contains: Contains a rendered screenshot of the corresponding design concept.

Functions/classes: This file does not define executable functions or classes; it is a configuration, schema, asset, documentation, or placeholder file.

## Notes About Services Mentioned in Configuration

- Supabase: actively used for authentication and Postgres storage.
- Apify: actively used for the live discovery/search expansion path.
- Hugging Face Spaces: used as a hosted runtime/API target for mobile builds and previews.
- Ollama/TinyLlama: optional local AI runtime for query rewrite and category-judge tasks.
- SearXNG: still documented and configured as optional/local infrastructure, but no longer the primary live discovery path.
- Neo4j: environment placeholders exist, but the active tracked code does not currently call Neo4j in production request flows.
- Stripe/Shopify/Google Web Client ID: present as configuration placeholders, not as active integrations in the tracked application flow.

## How to Read the Code Efficiently

- Start with `README.md`, then `docs/architecture.md`, then `services/scraper-python/app/main.py`.
- Read `apps/mobile/App.tsx` after that to understand the user-facing flow and screen state transitions.
- Use `services/scraper-python/app/jobs.py` to understand orchestration and `services/scraper-python/app/storage/db.py` to understand the real business rules.
- Read `services/scraper-python/app/providers/*` and `services/scraper-python/app/discovery/*` only after the request flow is clear.
- Use the tests in `services/scraper-python/tests/` as executable examples of expected behavior and edge-case handling.
