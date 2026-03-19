import crypto from "node:crypto";

export function normalizeWhitespace(value = "") {
  return String(value).replace(/\s+/g, " ").trim();
}

export function slugify(value = "") {
  return normalizeWhitespace(value)
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function toNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (!value) {
    return null;
  }

  const normalized = String(value).replace(/,/g, "").match(/\d+(?:\.\d+)?/);
  if (!normalized) {
    return null;
  }

  const parsed = Number(normalized[0]);
  return Number.isFinite(parsed) ? parsed : null;
}

export function hashId(value) {
  return crypto.createHash("sha1").update(String(value)).digest("hex").slice(0, 16);
}

export function decodeHtmlEntities(value = "") {
  return String(value)
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#x2F;/g, "/");
}

export function stripHtml(value = "") {
  return decodeHtmlEntities(String(value).replace(/<[^>]+>/g, " "));
}

export function pickFirst(items) {
  for (const item of items) {
    if (item) {
      return item;
    }
  }
  return "";
}

export function toArray(value) {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}

export function uniqueBy(items, getKey) {
  const seen = new Set();
  return items.filter((item) => {
    const key = getKey(item);
    if (!key || seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

export function safeUrl(value) {
  try {
    return new URL(value).toString();
  } catch {
    return "";
  }
}

export function canonicalizeProductUrl(value = "") {
  try {
    const parsed = new URL(value);
    parsed.hash = "";
    const hostname = parsed.hostname.replace(/^www\./, "");

    if (hostname.endsWith("walmart.com")) {
      return `https://www.walmart.com${parsed.pathname}`;
    }

    return parsed.toString();
  } catch {
    return "";
  }
}

export function parseHostFromUrl(value = "") {
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function nowIso() {
  return new Date().toISOString();
}

export function tokenize(...parts) {
  const tokens = parts
    .flatMap((part) => normalizeWhitespace(part).toLowerCase().match(/[a-z0-9]+/g) ?? [])
    .filter((token) => token.length > 1);

  const expanded = tokens.flatMap((token) => {
    if (token.endsWith("ies") && token.length > 4) {
      return [token, `${token.slice(0, -3)}y`];
    }

    if (token.endsWith("s") && token.length > 4 && !token.endsWith("ss")) {
      return [token, token.slice(0, -1)];
    }

    return [token];
  });

  return Array.from(new Set(expanded));
}
