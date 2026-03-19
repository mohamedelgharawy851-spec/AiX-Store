# TinyLlama Phase 1

AIXStore phase 1 integrates `tinyllama:latest` through the local Ollama runtime as a backend-only search fallback and category judge.

Scope:
- prompt-only integration
- no fine-tuning
- no multi-agent orchestration
- no mobile-side inference

Execution model:
- Expo mobile app keeps calling the Node runtime on `:8787`
- Node proxies to the Python FastAPI service on `:8790`
- the Python service invokes TinyLlama only on weak or empty searches, and on ambiguous category classification

Rollout modes:
- `off`: AI code exists but never affects search
- `shadow`: AI runs and logs outputs, but search results stay deterministic
- `assist`: AI can add rewrite variants for weak searches and can override ambiguous category classification when confidence is high enough

Environment variables:
- `AIXSTORE_AI_ENABLED`
- `AIXSTORE_AI_MODE`
- `AIXSTORE_AI_MODEL`
- `AIXSTORE_OLLAMA_BASE_URL`
- `AIXSTORE_AI_MAX_NEW_TOKENS`
- `AIXSTORE_AI_TEMPERATURE`
- `AIXSTORE_AI_TOP_P`
- `AIXSTORE_AI_TIMEOUT_MS`
- `AIXSTORE_AI_COLD_START_TIMEOUT_MS`
- `AIXSTORE_AI_REWRITE_LIMIT`
- `AIXSTORE_AI_CACHE_TTL_SECONDS`

Debug endpoints:
- `GET /ai/health`
- `POST /ai/rewrite`
- `POST /ai/judge-category`

Storage:
- AI rewrite cache is stored in `ai_query_cache`
- AI invocations are logged in `ai_runs`
- product rows now include `category_source` and optional AI category metadata

Dataset export:
- `services/scraper-python/scripts/export_ai_training_jsonl.py`
