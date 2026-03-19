# Architecture

## Runtime Split

- `apps/mobile`
  - Expo client.
  - Owns UI, local cart state, search input, and source-page redirects.
- `services/scraper`
  - Local catalog runtime.
  - Owns scraping, catalog storage, bootstrap seeding, cache-miss search enrichment, offers, and related product generation.

## Data Flow

1. The mobile app starts.
2. The local scraper runtime starts on the same machine.
3. The runtime seeds real products into `services/scraper/data/catalog.json` when needed.
4. The mobile app loads products, offers, categories, and detail data from the runtime.
5. If a search misses the existing catalog, the runtime scrapes more products for that query and merges them into the local store.
6. The product detail screen exposes the original seller link so the user can open the source page directly.

## Why This Direction

- It removes the dead backend complexity from the current build.
- It keeps the app focused on discovery and redirect traffic instead of checkout.
- It makes search enrichment possible without rebuilding a full server stack first.
