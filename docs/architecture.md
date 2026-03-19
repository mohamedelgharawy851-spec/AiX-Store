# AIX Store Architecture

## Runtime Split

- `apps/mobile`
  - Expo React Native client
  - Owns UI, navigation, favorites, history presentation, recommendations UI, and product detail rendering
- `services/scraper`
  - Local Node runtime gateway
  - Proxies requests to FastAPI and decorates catalog payloads for mobile-safe image delivery
- `services/scraper-python`
  - FastAPI backend
  - Owns scraping, discovery, search, product enrichment, related products, history, favorites, recommendations, and persistence

## Data Flow

1. The mobile app starts.
2. The local runtime starts on the same machine.
3. The runtime forwards mobile requests to the Python API.
4. The Python API reads and writes application state through Supabase-backed storage.
5. Search and bootstrap requests trigger discovery, extractor-backed scraping, ranking, and persistence.
6. The runtime returns mobile-friendly payloads with runtime-served image URLs.
7. The product detail screen exposes the original seller link when the user wants to open the source page.

## System Direction

AIX Store is designed as a product-discovery system rather than a checkout-first marketplace:

- the mobile client stays focused on browsing and interaction
- scraping and discovery stay server-side
- personalization is driven by favorites, history, and backend ranking logic
- storage is externalized through Supabase while the backend API contract remains stable

For the full system walkthrough, see [Project Review](/home/shadymayez/ShopEase/docs/project-review.md).
