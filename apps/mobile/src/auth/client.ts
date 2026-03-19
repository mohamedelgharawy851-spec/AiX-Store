import { fetchRuntimeJson } from "../runtime/client";
import type { AuthResponse, MeResponse } from "./types";

export function signUp(email: string, password: string, signal?: AbortSignal) {
  return fetchRuntimeJson("/auth/signup", {
    method: "POST",
    body: { email, password },
    signal,
  }) as Promise<AuthResponse>;
}

export function signIn(email: string, password: string, signal?: AbortSignal) {
  return fetchRuntimeJson("/auth/login", {
    method: "POST",
    body: { email, password },
    signal,
  }) as Promise<AuthResponse>;
}

export function signOut(token: string, signal?: AbortSignal) {
  return fetchRuntimeJson("/auth/logout", {
    method: "POST",
    token,
    signal,
  }) as Promise<{ ok: true }>;
}

export function getMe(token: string, signal?: AbortSignal) {
  return fetchRuntimeJson("/me", { token, signal }) as Promise<MeResponse>;
}

export function postUserEvent(
  token: string,
  payload: {
    type: string;
    productId?: string | null;
    categoryId?: string | null;
    queryText?: string | null;
    sourceUrl?: string | null;
    metadata?: Record<string, unknown>;
  },
  signal?: AbortSignal,
) {
  return fetchRuntimeJson("/me/events", {
    method: "POST",
    token,
    body: payload,
    signal,
  }) as Promise<{ ok: true }>;
}
