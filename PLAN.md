# ShopEase Delivery Plan

## Current Direction

- No Django API.
- No recommendation microservice.
- No Postgres dependency for the current app flow.
- The scraper runtime is the live data source.

## Runtime Behavior

- On startup, seed roughly 100 live products into the local catalog.
- Use those products for home, offers, catalog, and product detail.
- When a search misses the current catalog, scrape around 10 more products for that query and merge them into the local store.
- Keep original product URLs so the app can redirect the user to the seller page.

## Immediate Next Actions

1. Run the mobile app through the live startup script.
2. Verify initial bootstrap fills the catalog with real products and images.
3. Verify a cache-miss search like `tv` scrapes and stores more products.
4. Tighten the source list and scraping quality based on the first live run.
