# ShopEase

ShopEase is a scraper-driven product discovery app with an Expo React Native mobile client, a local runtime layer, and a FastAPI backend backed by Supabase.

## What is in this repo

- `apps/mobile`
  - Expo React Native client
  - Owns navigation, product browsing, favorites, history UI, recommendations UI, and product detail presentation
- `services/scraper`
  - Node runtime gateway
  - Proxies requests from the mobile app to FastAPI and decorates payloads for mobile-friendly image URLs
- `services/scraper-python`
  - FastAPI backend
  - Owns catalog bootstrapping, category feeds, typed search, product detail, related products, favorites, history, recommendations, discovery, scraping, and persistence
- `docs`
  - architecture and integration notes
- `scripts`
  - shared startup and environment-loading utilities
- `infra/searxng`
  - self-hosted discovery reference configuration retained for the hybrid search pipeline

## Current Runtime Model

The system runs as a layered stack:

1. The mobile app calls the local runtime on port `8787`.
2. The runtime forwards requests to the Python API on port `8790`.
3. The Python API executes catalog, search, scraping, recommendation, and auth logic.
4. Persistent application data is stored in Supabase Postgres.
5. Product scraping and enrichment are performed server-side through provider integrations and discovery services.

The public app name is now **AIX Store**.

Some internal identifiers still use the historical `ShopEase` prefix for compatibility, mainly in:

- environment variable names
- request headers such as `X-ShopEase-Session`
- some script/helper names

Those legacy identifiers are implementation details, not the product brand.

## Main User Capabilities

- browse category feeds
- run typed product search
- open detailed product pages
- save favorites
- view history
- receive personalized recommendations
- open the original seller/source page

## Start the App

```bash
/usr/bin/npm --workspace apps/mobile run android
```

That startup path launches:

- the Node runtime
- Expo for the mobile app

## Operations

Useful root scripts:

- `npm run catalog:runtime`
- `npm run catalog:reset`
- `npm run catalog:audit-sqlite`
- `npm run catalog:check-supabase`
- `npm run catalog:migrate-supabase`
- `npm run catalog:trigger-password-resets`

## Notes for GitHub

- Local runtime data under `services/scraper/data/` and `services/scraper-python/data/` is intentionally ignored and regenerated when needed.
- `.env` remains local only and must not be committed.
- The repository is prepared to be published without local product or user data.
