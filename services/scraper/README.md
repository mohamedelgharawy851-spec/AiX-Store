# Scraper Runtime

This service is the live catalog runtime gateway for AIX Store.

## What it does

- stores products in `data/catalog.json`
- proxies the mobile app to the FastAPI backend
- decorates product payloads with runtime-served image URLs
- seeds roughly 100 real products on startup when the backend catalog is empty
- returns products, offers, categories, and product details over a lightweight local HTTP runtime
- scrapes more products when a search misses the current catalog

## Endpoints

- `GET /health`
- `GET /catalog/bootstrap?count=100`
- `GET /catalog/products?category=electronics&limit=100`
- `GET /catalog/search?q=tv&limit=10`
- `GET /catalog/products/:id`

## Run

```bash
node services/scraper/server.mjs
```

Or let the mobile script start it automatically:

```bash
/usr/bin/npm --workspace apps/mobile run android
```
