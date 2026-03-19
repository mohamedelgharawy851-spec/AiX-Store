# AIX Store Project Review

## Executive Summary

AIX Store is a hybrid commerce-discovery system. It is not a checkout-first marketplace and it is not a thin UI over static catalog data. Its core value is product discovery: finding real products from live retail sources, normalizing them into a unified catalog, enriching them with images and detail data, and presenting them through a mobile experience shaped by favorites, history, and recommendations.

The current stack is composed of three cooperating layers:

- an Expo React Native mobile application
- a lightweight Node runtime gateway
- a FastAPI backend responsible for data, scraping, personalization, and storage

Persistent application data is now backed by Supabase Postgres. Scraping and discovery remain server-side. The app brand has been updated to **AIX Store**.

## What This Project Is

At a product level, AIX Store is a discovery engine that lets a user:

- browse curated category feeds
- search for products using typed queries
- open a detailed product page with pricing, images, reviews, and related items
- save products into favorites
- revisit searches and product opens through history
- receive recommendations influenced primarily by favorites and secondarily by recent behavior
- jump to the original source listing when ready to buy

At a systems level, it is a pipeline that converts live web product signals into a stable in-app experience.

## Current Brand and Naming

The public-facing product name is now **AIX Store**.

The repository still contains some historical `ShopEase` identifiers in implementation details, including:

- environment variable prefixes such as `SHOPEASE_*`
- compatibility headers such as `X-ShopEase-Session`
- older helper and script names

Those are technical legacy names. The user-facing application identity is AIX Store, as reflected in the mobile app branding and public documentation.

## Repository Walkthrough

### `apps/mobile`

This is the Expo React Native client. It owns:

- navigation and screen composition
- Home, Catalog, Favorites, and Profile surfaces
- product detail presentation
- recommendation and related-product screens
- optimistic favorite toggling
- local session/UI state
- communication with the local runtime

The mobile app is intentionally thin on business logic compared with the backend. Most catalog, history, recommendation, and search behavior is server-driven.

### `services/scraper`

This is the Node runtime gateway. It exists to make the mobile development experience simple and consistent.

Its responsibilities are:

- expose a single local HTTP origin for the mobile app
- proxy requests to the FastAPI backend
- normalize image delivery through runtime-served image URLs
- keep mobile route contracts stable even when backend implementation changes

This runtime is not the system of record. It is a gateway and payload decorator.

### `services/scraper-python`

This is the operational core of the system.

It owns:

- catalog bootstrap
- category feeds
- typed search
- product detail enrichment
- related-product generation
- favorites persistence
- history/event recording
- recommendation generation
- discovery integration
- scraping provider orchestration
- persistence and migrations
- Supabase-backed auth integration

If the mobile app is the face of the product, the Python service is the brain and the hands.

### `docs`

This folder holds architectural and integration notes. It now includes this full review so the repository has a professional, high-level explanation alongside the implementation.

### `infra/searxng`

This contains the retained self-hosted search infrastructure for the hybrid discovery pipeline. Even though the live discovery flow is centered around Apify first-pass search plus SearXNG expansion, the infrastructure and config remain part of the system story.

## End-to-End Request Flow

### App startup

When the app starts in local development:

1. Expo launches the mobile client.
2. The Node runtime listens on port `8787`.
3. The FastAPI backend listens on port `8790`.
4. The mobile app talks only to the runtime.
5. The runtime proxies to FastAPI.
6. FastAPI reads and writes persistent state through Supabase Postgres.

### Category feed flow

For Home and category browsing:

1. The mobile app requests catalog/bootstrap or category feed data.
2. The runtime forwards the request to FastAPI.
3. FastAPI checks current coverage and query state.
4. If cached catalog data is sufficient, it returns immediately.
5. If coverage is weak, the job layer triggers provider-backed collection and persistence.
6. The runtime rewrites image URLs to local runtime image paths before returning payloads to the app.

### Typed search flow

For typed user search:

1. The user enters a query in the mobile app.
2. The app calls `/catalog/search`.
3. FastAPI normalizes the query and expands deterministic variants.
4. Cached accepted results are checked first.
5. Apify runs as the first live discovery layer.
6. SearXNG runs as a second-pass expansion layer when useful.
7. Discovery hits are normalized, ranked, grouped by provider, and filtered.
8. Supported provider extractors fetch concrete product data.
9. Accepted products are persisted and returned.

This is a hybrid discovery architecture: discovery breadth first, extractor-backed reliability second.

### Product detail flow

When a user opens a product:

1. The mobile app requests `/catalog/products/{id}`.
2. FastAPI loads stored product detail.
3. When available, provider enrichment is used to upgrade detail quality.
4. The response may include reviews, gallery images, related items, and variant metadata.
5. The runtime decorates image fields for mobile delivery.
6. The detail page renders the product, image gallery, pricing, favorite state, and related entry points.

The system prefers graceful degradation. If live enrichment fails, the stored product can still open.

## Mobile Experience Review

### Home

Home is the first branded experience and now reflects AIX Store rather than the previous ShopEase identity. It functions as a personalized discovery surface rather than a plain category menu.

Its key responsibilities are:

- surface recommendations
- expose category entry points
- present curated sections
- route the user into search or product detail quickly

### Catalog

Catalog is the structured browsing and search surface.

It supports:

- section/category browsing
- typed search
- product-card browsing
- product open actions
- favorites toggling

The catalog layer depends on backend pagination and result shaping. The mobile client intentionally avoids embedding search logic locally.

### Product Detail

The detail page is where the catalog becomes useful. It combines:

- primary and alternate product imagery
- pricing and discount presentation
- favorite save/remove action
- reviews
- source link
- related-product entry points

The gallery work is important because it moves the app from a simple listing shell to a real product-evaluation surface.

### Favorites

Favorites are not cosmetic. In the current architecture they are a primary personalization signal.

The user can:

- save from cards
- save from product detail
- revisit favorites later

The backend uses favorites as the strongest signal for recommendation quality.

### Profile and History

Profile exposes user-facing history while the backend preserves additional event telemetry for personalization.

Visible history is intentionally narrower than the raw event stream. The goal is to present meaningful user history without dumping every background signal into the UI.

## Backend Review

### API Layer

The FastAPI application in `app/main.py` exposes the main service interface:

- `/catalog/*`
- `/auth/*`
- `/me/*`
- `/images/*`
- `/ai/*`
- `/discovery/*`

This API surface is what the Node runtime proxies and what the mobile app depends on indirectly.

### Job Layer

The `CatalogJobRunner` in `app/jobs.py` is the orchestration layer of the system.

It coordinates:

- provider selection
- category bootstrapping
- search execution
- detail retrieval
- related-product expansion
- enrichment persistence
- fallback behavior when a provider fails

This class is where the platform stops being a CRUD app and becomes an adaptive collection system.

### Storage Layer

The storage layer in `app/storage/db.py` acts as the persistence contract for the whole backend.

It owns:

- products
- reviews
- query contexts
- discovery caches
- user favorites
- user history events
- user affinities
- recommendations
- auth/profile lookups

The recent Supabase migration changed the physical database but intentionally preserved this layer as the public interface for the rest of the backend. That was the correct architectural choice because it minimized route-level churn.

## Discovery and Scraping Review

### Why discovery exists

The project does not rely on a fixed imported catalog. It needs to discover products dynamically from the web and convert them into stable app items.

### Current discovery model

The current search model is hybrid:

- Apify provides the primary first-pass discovery
- SearXNG provides expansion breadth
- provider-specific extractors produce final visible products

This separation matters:

- discovery is allowed to be broad
- extraction is required to be precise

That is why the system can consider more domains than it can directly ingest as final products.

### Provider integrations

The backend contains explicit provider integrations for major retail sources, including:

- Amazon requests
- Amazon Playwright fallback
- Walmart requests
- Target requests

Each provider layer is responsible for:

- search or URL-based extraction
- HTML or payload parsing
- normalized product output
- detail enrichment

The project is therefore not “scraping in general.” It is a curated extractor system with discovery support.

### Reliability model

The search and scraping pipeline includes defensive behavior:

- retries
- fallbacks
- cached response reuse
- graceful degradation when live enrichment fails

This is critical because upstream retail pages are unstable by nature.

## Personalization Review

### Favorites-first recommendations

One of the more important product decisions in the codebase is that recommendations are no longer random or purely session-based. The system now gives favorites the highest weight.

Recommendation inputs include:

- favorite categories
- favorite brands
- favorite tags
- favorite provider/site patterns
- recent history and events as a secondary signal

This produces recommendations that are more stable and more intentional than simple co-click logic.

### History

History is backed by event storage, but the visible history UX is curated:

- searches and product opens are surfaced
- internal telemetry can remain broader than the visible feed

This separation improves the user experience without throwing away useful behavioral data.

### Related products

Related items are generated through a mix of:

- local related scoring
- family/category matching
- expansion when the user explicitly asks for more

The system has been tightened to reduce category drift. That matters because irrelevant “related” items damage user trust quickly.

## Auth and Supabase Review

The project originally relied more heavily on local database storage patterns. It now uses Supabase-backed Postgres for deployed persistence while keeping the backend API contract stable.

That migration introduced three important properties:

1. persistence is now externalized and production-friendly
2. the backend still owns business logic rather than leaking data access into the mobile app
3. the system retains a local fallback path in code for development or emergency use

This is a mature compromise: the application becomes more deployable without turning into a thin client over direct database calls.

## AI Layer Review

The AI layer is intentionally auxiliary rather than foundational.

It supports:

- rewrite planning
- ambiguous category judging
- optional search assistance

This is the correct use of AI in this architecture. Core catalog correctness still comes from deterministic logic, provider extraction, and persistence rules.

## Design and Brand Evolution

The repository shows an obvious evolution:

- an earlier ShopEase-branded concept
- a stronger AIX Store visual direction
- Stitch design sources for key surfaces
- newer mobile assets and splash branding aligned to AIX Store

This is visible in:

- `apps/mobile/app.json`
- the updated mobile assets
- the `stitch_home_screen` source set

The project has effectively moved from a functional prototype identity into a clearer product brand.

## How Everything Happened

From the repository itself, the project evolution is clear:

1. It began as a scraper-driven catalog idea with a local runtime and locally stored product data.
2. The mobile app matured into a more complete browsing experience with product detail, favorites, history, and recommendations.
3. The backend evolved from simple storage and scraping toward a real orchestration layer with discovery, extraction, ranking, and enrichment.
4. Search became hybrid, combining managed discovery with secondary expansion.
5. Personalization became more deliberate, especially through favorites-first recommendations.
6. Persistence moved from local-only SQLite into Supabase-backed Postgres.
7. The public brand moved from ShopEase to AIX Store.

That sequence is important because it explains the current shape of the code:

- some legacy names still exist
- the architecture is layered instead of fully rewritten from scratch
- the project has both prototype-era artifacts and production-oriented improvements

## Strengths of the Project

- Strong separation between mobile UI, gateway runtime, and backend logic
- Scraping and discovery kept server-side rather than pushed into the client
- Good preservation of API contracts while storage migrated underneath
- Clear provider abstraction for scraping
- Practical personalization model centered on favorites and history
- Operational tooling for reset, audit, migration, and maintenance
- Local development flow remains simple despite backend complexity

## Areas That Still Carry Legacy Weight

- Historical `ShopEase` identifiers remain in internal names
- The repository still contains prototype and design artifact folders that are not runtime-critical
- The Node runtime exists mainly as a compatibility/development gateway rather than a deeply featured application server
- Some documentation previously described an older local-data model and needed branding cleanup

These are not structural failures, but they are the visible marks of a project that has evolved quickly.

## Final Assessment

AIX Store is no longer just a mobile UI or a simple scraper. It is a layered product-discovery platform with:

- a branded client
- a stable local runtime gateway
- a backend that understands search, product quality, personalization, and enrichment
- a migration path toward a more production-ready storage model

Its strongest quality is that it keeps the complex parts in the backend where they belong:

- scraping
- discovery
- ranking
- persistence
- personalization

Its strongest product decision is that it treats favorites and behavior as first-class signals rather than superficial UI state.

Its clearest architectural story is this:

**AIX Store discovers live products, turns them into a normalized catalog, and delivers them through a mobile experience that becomes smarter as the user interacts with it.**
