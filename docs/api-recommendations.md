# Data Strategy

## Current Strategy

- No external development API.
- No Django API.
- No separate recommendation service.
- The local scraper runtime is the live catalog source.

## Seed Behavior

- On startup, the runtime tries to collect around 100 real products.
- Products are stored in `services/scraper/data/catalog.json`.
- Home offers come from products that expose both current and original prices.

## Search Behavior

- Search first checks the local catalog.
- If there are not enough matching items, the runtime scrapes more products for that query.
- New products are merged into the local catalog and become available immediately.

## Redirect Model

- Product detail keeps the source URL and source site.
- The app opens the seller page directly instead of handling checkout itself.
