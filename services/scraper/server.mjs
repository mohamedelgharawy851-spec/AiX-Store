import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { RUNTIME_HOST, RUNTIME_PORT } from "./lib/config.mjs";

// ── Keep-alive: prevent Node from exiting when event loop is idle ────────────
const _keepAlive = setInterval(() => {}, 1 << 30);

// ── Global error visibility ───────────────────────────────────────────────────
process.on("uncaughtException", (err) => {
  console.error("Uncaught exception:", err);
});

process.on("unhandledRejection", (err) => {
  console.error("Unhandled rejection:", err);
});

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Load local .env only in non-cloud environments (safe – won't crash if missing)
if (!process.env.RAILWAY_ENVIRONMENT && !process.env.SPACE_ID) {
  try {
    const { loadAIXStoreEnv } = await import("../../scripts/load-env.mjs");
    loadAIXStoreEnv(path.resolve(__dirname, "../.."));
  } catch {
    // Running locally without a load-env helper – continue with process.env as-is
  }
}

// ── Upstream FastAPI URL ──────────────────────────────────────────────────────
// Set AIXSTORE_FASTAPI_URL=https://shadypro-aixstore-api.hf.space in HF Space secrets
const FASTAPI_URL = (process.env.AIXSTORE_FASTAPI_URL || "").trim().replace(/\/+$/, "");
const PYTHON_HOST = process.env.AIXSTORE_PYTHON_HOST || "127.0.0.1";
const PYTHON_PORT = Number(process.env.AIXSTORE_PYTHON_PORT || 8790);
const PYTHON_BASE_URL = FASTAPI_URL || `http://${PYTHON_HOST}:${PYTHON_PORT}`;
const UPSTREAM_STARTING_MESSAGE = "Backend is starting up, please try again in 10 seconds";
const HF_KEEP_ALIVE_MS = 4 * 60 * 1000;

console.log(`[config] upstream FastAPI  : ${PYTHON_BASE_URL}`);
console.log(`[config] runtime port      : ${RUNTIME_PORT}`);
console.log(`[config] runtime host      : ${RUNTIME_HOST}`);

function corsHeaders(extra = {}) {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
    "access-control-allow-headers": "Content-Type, Authorization, X-AIXStore-Session",
    ...extra,
  };
}

function sendJson(response, statusCode, payload) {
  response.writeHead(
    statusCode,
    corsHeaders({
      "content-type": "application/json; charset=utf-8",
    }),
  );
  response.end(JSON.stringify(payload));
}

function upstreamStartingPayload() {
  return { error: UPSTREAM_STARTING_MESSAGE };
}

function runtimeOrigin(request) {
  const forwardedProto = Array.isArray(request.headers["x-forwarded-proto"])
    ? request.headers["x-forwarded-proto"][0]
    : request.headers["x-forwarded-proto"];
  const protocol = (forwardedProto || "").split(",")[0].trim() || "http";
  return `${protocol}://${request.headers.host || `127.0.0.1:${RUNTIME_PORT}`}`;
}

function decorateProduct(request, product) {
  if (!product) {
    return product;
  }
  const imagePath = product.localImageKey ? `${runtimeOrigin(request)}/catalog/image/${product.localImageKey}` : "";
  return {
    ...product,
    imageUrl: imagePath,
    reviews: Array.isArray(product.reviews) ? product.reviews : undefined,
  };
}

function decorateCatalogPayload(request, payload) {
  return {
    ...payload,
    items: Array.isArray(payload.items) ? payload.items.map((product) => decorateProduct(request, product)) : [],
    offers: Array.isArray(payload.offers) ? payload.offers.map((product) => decorateProduct(request, product)) : [],
  };
}

function decorateProductDetail(request, payload) {
  return {
    ...decorateProduct(request, payload),
    relatedProducts: Array.isArray(payload.relatedProducts)
      ? payload.relatedProducts.map((product) => decorateProduct(request, product))
      : [],
    reviews: Array.isArray(payload.reviews) ? payload.reviews : [],
  };
}

function decorateRecommendations(request, payload) {
  return {
    ...payload,
    items: Array.isArray(payload.items) ? payload.items.map((product) => decorateProduct(request, product)) : [],
  };
}

function decorateFavorites(request, payload) {
  return {
    ...payload,
    items: Array.isArray(payload.items) ? payload.items.map((product) => decorateProduct(request, product)) : [],
  };
}

function decorateRelatedPayload(request, payload) {
  return {
    ...payload,
    items: Array.isArray(payload.items) ? payload.items.map((product) => decorateProduct(request, product)) : [],
  };
}

async function readRequestBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function upstreamPolicy(pathname, method = "GET") {
  const normalizedMethod = String(method || "GET").toUpperCase();
  if (pathname.startsWith("/me/favorites/") && (normalizedMethod === "PUT" || normalizedMethod === "DELETE")) {
    return { timeoutMs: 12000, retryCount: 0, kind: "favorite-mutation" };
  }
  if (pathname === "/me/events") {
    return { timeoutMs: 12000, retryCount: 0, kind: "event-write" };
  }
  if (pathname.startsWith("/auth/")) {
    return { timeoutMs: 20000, retryCount: 0, kind: "auth" };
  }
  return { timeoutMs: 25000, retryCount: 1, kind: "read" };
}

async function fetchUpstream(
  pathname,
  { search = "", method = "GET", body, authorization, sessionId, timeoutMs = 25000, retryCount = 1 } = {},
) {
  const url = `${PYTHON_BASE_URL}${pathname}${search}`;
  const headers = {
    accept: "application/json, image/*;q=0.9, */*;q=0.8",
  };
  if (authorization) {
    headers.authorization = authorization;
  }
  if (sessionId) {
    headers["x-aixstore-session"] = sessionId;
  }
  if (body) {
    headers["content-type"] = "application/json; charset=utf-8";
  }

  const options = {
    method,
    headers,
    body,
  };

  const makeRequest = async (attempt = 0) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      return response;
    } catch (err) {
      const isTimeout =
        err.name === "AbortError" || err.code === "UND_ERR_HEADERS_TIMEOUT" || (err.cause && err.cause.code === "UND_ERR_HEADERS_TIMEOUT");

      if (isTimeout && attempt < retryCount) {
        console.warn(
          `[upstream] timeout ${method} ${pathname} attempt=${attempt + 1} timeoutMs=${timeoutMs} retrying=1`,
        );
        await new Promise((r) => setTimeout(r, 3000));
        return makeRequest(attempt + 1);
      }
      console.error(
        `[upstream] failed ${method} ${pathname} attempt=${attempt + 1} timeoutMs=${timeoutMs} retryCount=${retryCount}`,
        err,
      );
      throw err;
    } finally {
      clearTimeout(timeoutId);
    }
  };

  return makeRequest();
}

async function wakeUpFastApi(reason = "warmup") {
  if (!FASTAPI_URL) {
    return;
  }
  try {
    await fetch(`${PYTHON_BASE_URL}/health`, {
      headers: { accept: "application/json" },
    });
    console.log(`[upstream] wake ping ok reason=${reason}`);
  } catch (err) {
    console.warn(`[upstream] wake ping failed reason=${reason}`, err);
  }
}

async function decodeJsonUpstream(upstream, pathname) {
  const contentType = (upstream.headers.get("content-type") || "").toLowerCase();
  const rawText = await upstream.text();
  const trimmed = rawText.trimStart();

  if (
    contentType.includes("text/html") ||
    trimmed.startsWith("<!DOCTYPE") ||
    trimmed.startsWith("<html")
  ) {
    console.warn(
      `[upstream] html startup page ${pathname} status=${upstream.status} contentType=${contentType || "unknown"}`,
    );
    return { isStartingUp: true, payload: null };
  }

  if (!rawText) {
    return { isStartingUp: false, payload: {} };
  }

  try {
    return { isStartingUp: false, payload: JSON.parse(rawText) };
  } catch (err) {
    console.error(
      `[upstream] invalid json ${pathname} status=${upstream.status} contentType=${contentType || "unknown"}`,
      err,
    );
    throw err;
  }
}

async function proxyJson(request, response, pathname, decorate, search = "") {
  const body = request.method && !["GET", "HEAD"].includes(request.method) ? await readRequestBody(request) : null;
  const policy = upstreamPolicy(pathname, request.method || "GET");
  console.log(
    `[proxy] ${request.method || "GET"} ${pathname} policy=${policy.kind} timeoutMs=${policy.timeoutMs} retries=${policy.retryCount}`,
  );
  const upstream = await fetchUpstream(pathname, {
    search,
    method: request.method || "GET",
    body: body && body.byteLength ? body : undefined,
    authorization: request.headers.authorization,
    sessionId: request.headers["x-aixstore-session"],
    timeoutMs: policy.timeoutMs,
    retryCount: policy.retryCount,
  });
  const decoded = await decodeJsonUpstream(upstream, pathname);
  if (decoded.isStartingUp) {
    sendJson(response, 503, upstreamStartingPayload());
    return;
  }
  const payload = decoded.payload;
  sendJson(response, upstream.status, decorate ? decorate(request, payload) : payload);
}

if (FASTAPI_URL) {
  void wakeUpFastApi("startup");
  setInterval(() => {
    void wakeUpFastApi("keepalive");
  }, HF_KEEP_ALIVE_MS);
}

async function proxyImage(response, imageKey) {
  const upstream = await fetch(`${PYTHON_BASE_URL}/images/${imageKey}`);
  if (!upstream.ok) {
    sendJson(response, upstream.status, { error: "Image not found" });
    return;
  }

  const contentType = upstream.headers.get("content-type") || "image/jpeg";
  const buffer = Buffer.from(await upstream.arrayBuffer());
  response.writeHead(
    200,
    corsHeaders({
      "content-type": contentType,
      "content-length": String(buffer.byteLength),
      "cache-control": "public, max-age=86400",
    }),
  );
  response.end(buffer);
}

const server = http.createServer(async (request, response) => {
  if (!request.url) {
    sendJson(response, 404, { error: "Not found" });
    return;
  }

  if (request.method === "OPTIONS") {
    response.writeHead(204, corsHeaders());
    response.end();
    return;
  }

  if (!["GET", "POST", "PUT", "DELETE"].includes(request.method || "")) {
    sendJson(response, 405, { error: "Method not allowed" });
    return;
  }

  const url = new URL(request.url, runtimeOrigin(request));

  if (url.pathname === "/") {
    sendJson(response, 200, {
      status: "ok",
      service: "catalog-runtime",
      message: "AIXStore runtime is running. FastApi upstream: " + PYTHON_BASE_URL,
    });
    return;
  }

  try {
    if (url.pathname === "/health") {
      const upstream = await fetchUpstream("/health");
      if (!upstream.ok) {
        sendJson(response, 503, { status: "error", service: "catalog-runtime", upstream: "unhealthy" });
        return;
      }
      const decoded = await decodeJsonUpstream(upstream, "/health");
      if (decoded.isStartingUp) {
        sendJson(response, 503, { status: "error", service: "catalog-runtime", upstream: upstreamStartingPayload() });
        return;
      }
      const payload = decoded.payload;
      sendJson(response, 200, { status: "ok", service: "catalog-runtime", upstream: payload });
      return;
    }

    if (url.pathname === "/catalog/bootstrap") {
      await proxyJson(request, response, "/catalog/bootstrap", decorateCatalogPayload, url.search);
      return;
    }

    if (url.pathname === "/catalog/products") {
      await proxyJson(request, response, "/catalog/products", decorateCatalogPayload, url.search);
      return;
    }

    if (url.pathname === "/catalog/search") {
      await proxyJson(request, response, "/catalog/search", decorateCatalogPayload, url.search);
      return;
    }

    if (url.pathname === "/discovery/health") {
      await proxyJson(request, response, "/discovery/health");
      return;
    }

    if (url.pathname === "/discovery/query") {
      await proxyJson(request, response, "/discovery/query");
      return;
    }

    if (url.pathname === "/discovery/cache") {
      await proxyJson(request, response, "/discovery/cache", undefined, url.search);
      return;
    }

    if (url.pathname === "/ai/health") {
      await proxyJson(request, response, "/ai/health");
      return;
    }

    if (url.pathname === "/ai/rewrite") {
      await proxyJson(request, response, "/ai/rewrite");
      return;
    }

    if (url.pathname === "/ai/judge-category") {
      await proxyJson(request, response, "/ai/judge-category");
      return;
    }

    if (url.pathname.startsWith("/auth/")) {
      await proxyJson(request, response, url.pathname, undefined, url.search);
      return;
    }

    if (url.pathname.startsWith("/me/favorites/")) {
      const decorate = request.method === "PUT" ? decorateProduct : undefined;
      await proxyJson(request, response, url.pathname, decorate, url.search);
      return;
    }

    if (url.pathname === "/me/favorites") {
      await proxyJson(request, response, "/me/favorites", decorateFavorites, url.search);
      return;
    }

    if (url.pathname === "/me/recommendations") {
      await proxyJson(request, response, "/me/recommendations", decorateRecommendations, url.search);
      return;
    }

    if (url.pathname.startsWith("/me")) {
      await proxyJson(request, response, url.pathname, undefined, url.search);
      return;
    }

    const favoriteMatch = url.pathname.match(/^\/me\/favorites\/([^/]+)$/);
    if (favoriteMatch) {
      const decorate = request.method === "PUT" ? decorateProduct : undefined;
      await proxyJson(request, response, `/me/favorites/${favoriteMatch[1]}`, decorate);
      return;
    }

    const imageMatch = url.pathname.match(/^\/catalog\/image\/([a-f0-9]{40})$/);
    if (imageMatch) {
      await proxyImage(response, imageMatch[1]);
      return;
    }

    const relatedMatch = url.pathname.match(/^\/catalog\/products\/([^/]+)\/related$/);
    if (relatedMatch) {
      await proxyJson(request, response, `/catalog/products/${relatedMatch[1]}/related`, decorateRelatedPayload, url.search);
      return;
    }

    const productMatch = url.pathname.match(/^\/catalog\/products\/([^/]+)$/);
    if (productMatch) {
      await proxyJson(request, response, `/catalog/products/${productMatch[1]}`, decorateProductDetail);
      return;
    }

    sendJson(response, 404, { error: "Not found" });
  } catch (error) {
    const isTimeout =
      error.name === "AbortError" ||
      error.code === "UND_ERR_HEADERS_TIMEOUT" ||
      (error.cause && error.cause.code === "UND_ERR_HEADERS_TIMEOUT");

    if (isTimeout) {
      console.error("Upstream timeout persistent:", error);
      sendJson(response, 503, {
        error: "Backend is waking up, please try again in 10 seconds",
      });
      return;
    }

    console.error("Server error handling request:", error);
    sendJson(response, 500, {
      error: "Runtime request failed",
      detail: error instanceof Error ? error.message : String(error),
      cause: error.cause instanceof Error ? error.cause.message : String(error.cause),
    });
  }
});

// Surface server-level errors (e.g. EADDRINUSE)
server.on("error", (err) => {
  console.error("Server error:", err);
});

server.listen(RUNTIME_PORT, RUNTIME_HOST, () => {
  console.log(`AIXStore catalog runtime listening on http://${RUNTIME_HOST}:${RUNTIME_PORT}`);
});
