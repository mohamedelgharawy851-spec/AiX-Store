# AIX Store

AIX Store is a mobile-first product discovery platform built with Expo React Native, a lightweight Node runtime, and a FastAPI scraping and personalization backend backed by Supabase.

This repository contains the full application stack:

- a branded mobile app
- a local runtime gateway used by the app during development
- a Python API that owns search, scraping, product enrichment, personalization, and storage
- operational scripts for reset, reseed, migration, and Supabase maintenance

## Documentation Map

Start here if you want to understand the project quickly:

- [Project Guide](docs/ShopEase_Project_Guide.md)
- [Project Guide (Word)](docs/ShopEase_Project_Guide.docx)
- [Architecture Notes](docs/architecture.md)
- [Project Review](docs/project-review.md)
- [Apify Integration](docs/search/apify-integration.md)
- [AI Prompt Notes](docs/ai/prompts.md)

## Project Review

For the full professional project review, architecture walkthrough, and system evolution, see:

- [Project Review](docs/project-review.md)

## Repository Structure

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

## Logic Map

If you want to read the code by feature instead of by folder, use this order:

1. Login and session logic
   - `apps/mobile/App.tsx`
   - `apps/mobile/src/auth/*`
   - `services/scraper-python/app/main.py`
   - `services/scraper-python/app/storage/db.py`
2. Home, category feeds, and offers
   - `apps/mobile/App.tsx`
   - `apps/mobile/src/catalog/*`
   - `services/scraper-python/app/jobs.py`
   - `services/scraper-python/app/storage/db.py`
3. Search and discovery
   - `services/scraper-python/app/discovery/*`
   - `services/scraper-python/app/providers/*`
   - `services/scraper-python/app/ai/*`
4. Product detail, reviews, variants, and related products
   - `apps/mobile/App.tsx`
   - `services/scraper-python/app/jobs.py`
   - `services/scraper-python/app/storage/db.py`
5. Favorites, history, and recommendations
   - `apps/mobile/src/favorites/*`
   - `apps/mobile/src/history/*`
   - `apps/mobile/src/recommendations/*`
   - `services/scraper-python/app/storage/db.py`

## Core Services and Infrastructure

- Supabase
  - active database and authentication backend
- Apify
  - active live discovery/search expansion provider
- Hugging Face Spaces
  - hosted runtime target used by preview/mobile builds
- Ollama / TinyLlama
  - optional local AI rewrite and category-judge path
- SearXNG
  - legacy or optional metasearch infrastructure kept in `infra/searxng`
- Railway
  - deployment target for the backend services
- Neo4j
  - environment placeholders exist, but the tracked application flow does not currently depend on Neo4j at runtime

## Current Runtime Model

The system runs as a layered stack:

1. The mobile app calls the local runtime on port `8787`.
2. The runtime forwards requests to the Python API on port `8790`.
3. The Python API executes catalog, search, scraping, recommendation, and auth logic.
4. Persistent application data is stored in Supabase Postgres.
5. Product scraping and enrichment are performed server-side through provider integrations and discovery services.

The public app name is **AIXStore**.

Core runtime and configuration identifiers use the `AIXSTORE_*` prefix and the `X-AIXStore-Session` request header.

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

## Recommended Reading Order

1. Read this file.
2. Read [`docs/architecture.md`](docs/architecture.md).
3. Read [`services/scraper-python/app/main.py`](services/scraper-python/app/main.py).
4. Read [`apps/mobile/App.tsx`](apps/mobile/App.tsx).
5. Read [`services/scraper-python/app/jobs.py`](services/scraper-python/app/jobs.py).
6. Read [`services/scraper-python/app/storage/db.py`](services/scraper-python/app/storage/db.py).
7. Use [`docs/ShopEase_Project_Guide.md`](docs/ShopEase_Project_Guide.md) as the file-by-file reference.

## Notes for GitHub

- Local runtime data under `services/scraper/data/` and `services/scraper-python/data/` is intentionally ignored and regenerated when needed.
- `.env` remains local only and must not be committed.
- The repository is prepared to be published without local product or user data.
