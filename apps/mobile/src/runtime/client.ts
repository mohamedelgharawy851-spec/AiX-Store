import { NativeModules } from "react-native";

const DEFAULT_RUNTIME_PORT = 8787;
const DEFAULT_PRODUCTION_RUNTIME_URL = "https://aix-store-production.up.railway.app";
const runtimeOverride = (process.env.EXPO_PUBLIC_AIXSTORE_RUNTIME_URL ?? "").trim();

function extractMetroHost() {
  const scriptURL = NativeModules.SourceCode?.scriptURL ?? "";
  const match = scriptURL.match(/^https?:\/\/([^/:]+)(?::\d+)?\//);
  return match?.[1] || "127.0.0.1";
}

export function runtimeBaseUrl() {
  if (runtimeOverride) {
    return runtimeOverride.replace(/\/+$/, "");
  }
  if (!__DEV__) {
    return DEFAULT_PRODUCTION_RUNTIME_URL;
  }
  return `http://${extractMetroHost()}:${DEFAULT_RUNTIME_PORT}`;
}

export async function fetchRuntimeJson(
  path: string,
  options: {
    method?: "GET" | "POST" | "PUT" | "DELETE";
    body?: unknown;
    token?: string | null;
    sessionId?: string | null;
    signal?: AbortSignal;
  } = {},
) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  if (options.sessionId) {
    headers["X-AIXStore-Session"] = options.sessionId;
  }

  const response = await fetch(`${runtimeBaseUrl()}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: string }).detail)
        : `Runtime request failed with ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}
