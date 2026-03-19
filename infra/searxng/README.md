# SearXNG

ShopEase uses SearXNG as a discovery layer for weak page-1 searches.

## Start locally

```bash
cd infra/searxng
docker compose up -d
```

The default endpoint is:

```text
http://127.0.0.1:8088
```

## Notes

- ShopEase only accepts discovery results from supported shopping domains.
- Phase 1 allowlist:
  - `amazon.com`
  - `walmart.com`
  - `target.com`
- If SearXNG is unavailable, ShopEase falls back to the existing direct-provider search path.

## Engine keys

The default config avoids paid engine keys. If you later add engines that require credentials, update `settings.yml` and the relevant environment variables for that deployment.
