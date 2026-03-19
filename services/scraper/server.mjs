import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadAIXStoreEnv } from "../../scripts/load-env.mjs";
import { RUNTIME_HOST, RUNTIME_PORT } from "./lib/config.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
loadAIXStoreEnv(path.resolve(__dirname, "../.."));

const FASTAPI_URL = (process.env.AIXSTORE_FASTAPI_URL || "").trim().replace(/\/+$/, "");
const PYTHON_HOST = process.env.AIXSTORE_PYTHON_HOST || "127.0.0.1";
const PYTHON_PORT = Number(process.env.AIXSTORE_PYTHON_PORT || 8790);
const PYTHON_BASE_URL = FASTAPI_URL || `http://${PYTHON_HOST}:${PYTHON_PORT}`;

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

function runtimeOrigin(request) {
  return `http://${request.headers.host || `127.0.0.1:${RUNTIME_PORT}`}`;
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

async function fetchUpstream(pathname, { search = "", method = "GET", body, authorization, sessionId } = {}) {
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
  return fetch(`${PYTHON_BASE_URL}${pathname}${search}`, {
    method,
    headers,
    body,
  });
}

async function proxyJson(request, response, pathname, decorate, search = "") {
  const body = request.method && !["GET", "HEAD"].includes(request.method) ? await readRequestBody(request) : null;
  const upstream = await fetchUpstream(pathname, {
    search,
    method: request.method || "GET",
    body: body && body.byteLength ? body : undefined,
    authorization: request.headers.authorization,
    sessionId: request.headers["x-aixstore-session"],
  });
  const payload = await upstream.json();
  sendJson(response, upstream.status, decorate ? decorate(request, payload) : payload);
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

  try {
    if (url.pathname === "/health") {
      const upstream = await fetchUpstream("/health");
      if (!upstream.ok) {
        sendJson(response, 503, { status: "error", service: "catalog-runtime", upstream: "unhealthy" });
        return;
      }
      const payload = await upstream.json();
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

    if (url.pathname === "/auth/signup") {
      await proxyJson(request, response, "/auth/signup");
      return;
    }

    if (url.pathname === "/auth/login") {
      await proxyJson(request, response, "/auth/login");
      return;
    }

    if (url.pathname === "/auth/logout") {
      await proxyJson(request, response, "/auth/logout");
      return;
    }

    if (url.pathname === "/me") {
      await proxyJson(request, response, "/me");
      return;
    }

    if (url.pathname === "/me/history") {
      await proxyJson(request, response, "/me/history", undefined, url.search);
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

    if (url.pathname === "/me/events") {
      await proxyJson(request, response, "/me/events");
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
    sendJson(response, 500, {
      error: "Runtime request failed",
      detail: error instanceof Error ? error.message : String(error),
    });
  }
});

server.listen(RUNTIME_PORT, RUNTIME_HOST, () => {
  console.log(`AIXStore catalog runtime listening on http://${RUNTIME_HOST}:${RUNTIME_PORT}`);
});
