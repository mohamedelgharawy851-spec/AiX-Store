# Apify Discovery Integration

AIXStore now uses Apify's managed Google Search Results Scraper as the live discovery provider for page-1 typed searches.

## Search flow

1. Search starts with deterministic local variants.
2. For discovery, AIXStore appends retailer-weighted Google queries such as `toy target`, `toy walmart`, and `toy amazon`.
3. If cached accepted results are already strong, AIXStore returns them immediately.
4. Otherwise, AIXStore sends the top variants to Apify's synchronous Google search actor.
5. Only allowlisted retailer domains are kept.
6. Result URLs are grouped by provider and extracted through the existing Target, Walmart, and Amazon parsers.
7. Accepted products are stored in the existing catalog/query cache and persistent backend storage.
8. If Apify is unavailable or produces nothing usable, AIXStore falls back to the direct-provider search path.

## Required environment

- `AIXSTORE_APIFY_ENABLED=true`
- `AIXSTORE_APIFY_TOKEN=<secret>`
- `AIXSTORE_APIFY_BASE_URL=https://api.apify.com/v2`
- `AIXSTORE_APIFY_ACTOR_ID=apify~google-search-scraper`
- `AIXSTORE_APIFY_TIMEOUT_MS=8000`
- `AIXSTORE_APIFY_RESULTS_PER_PAGE=10`
- `AIXSTORE_APIFY_MAX_PAGES_PER_QUERY=1`
- `AIXSTORE_APIFY_COUNTRY=US`
- `AIXSTORE_APIFY_LANGUAGE=en`
- `AIXSTORE_APIFY_DOMAIN=com`
- `AIXSTORE_APIFY_CACHE_TTL_SECONDS=21600`
- `AIXSTORE_APIFY_MAX_VARIANTS=3`
- `AIXSTORE_APIFY_MAX_URLS_PER_PROVIDER=6`
- `AIXSTORE_APIFY_TOTAL_BUDGET_MS=2000`
- `AIXSTORE_APIFY_PROVIDER_EXTRACTION_TIMEOUT_MS=2500`

## Phase-1 allowlist

- `amazon.com`
- `walmart.com`
- `target.com`

## Discovery metadata

`GET /catalog/search` returns an additive `discovery` block with:

- whether discovery was enabled or invoked
- which query variants were sent
- the active provider (`apify`)
- actor id and locale
- candidate and accepted domain/url counts
- latency and fallback reason

## Debug endpoints

- `GET /discovery/health`
- `POST /discovery/query`
- `GET /discovery/cache?contextKey=<value>`

## Notes

- Apify replaces SearXNG in the live search path.
- The AI model is no longer part of the live search pipeline.
- Category tabs do not use Apify.
- Discovery failures never fail the main search request.
