import { NativeModules } from "react-native";

const DEFAULT_RUNTIME_PORT = 8787;
const DEFAULT_PRODUCTION_RUNTIME_URL = "https://aix-store-production.up.railway.app";
const DEFAULT_REQUEST_TIMEOUT_MS = 10000;
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
    timeoutMs?: number;
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

  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const timeoutId = setTimeout(() => {
    controller.abort();
  }, timeoutMs);
  const abortListener = () => controller.abort();
  options.signal?.addEventListener("abort", abortListener);

  let response: Response;
  try {
    response = await fetch(`${runtimeBaseUrl()}${path}`, {
      method: options.method || "GET",
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted && !options.signal?.aborted) {
      throw new Error("Request timed out after 10 seconds. Try again in a moment.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
    options.signal?.removeEventListener("abort", abortListener);
  }

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
