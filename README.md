# AIX Store
AIX Store is a scraper-driven product discovery app with an Expo React Native mobile client, a local runtime layer, and a FastAPI backend backed by Supabase.

## What is in this repo

- `apps/mobile`
  - Expo React Native app for home, catalog, product detail, source redirects, and local cart state.
- `services/scraper`
  - Local catalog runtime.
  - Scrapes real product pages through web search, seeds the app on startup, and enriches searches on cache misses.
  - Local cache files under `services/scraper/data/` are generated at runtime and are not committed.
- `services/scraper-python`
  - FastAPI backend and scraper pipeline.
  - Local files under `services/scraper-python/data/` are generated at runtime for fallback/caching only and are not committed.
- `scripts/start-mobile-live.mjs`
  - Starts the scraper runtime and Expo together so the mobile app loads live products immediately.

## Current product flow

1. Start the mobile app.
2. The local scraper runtime boots and seeds around 100 live products if the catalog is empty.
3. The mobile app loads home, offers, catalog, product detail, images, and reviews from that runtime.
4. If a user searches for something missing, the runtime scrapes more products for that query and stores only regenerated local cache files when needed.
5. When the user opens a product, the app can redirect them to the original store page.

## Run

```bash
/usr/bin/npm --workspace apps/mobile run android
```

The workspace startup script now launches both:

- the local catalog runtime on port `8787`
- Expo for the mobile app
