import * as SecureStore from "expo-secure-store";

const SESSION_TOKEN_KEY = "aixstore.session.token";

export function saveSessionToken(token: string) {
  return SecureStore.setItemAsync(SESSION_TOKEN_KEY, token);
}

export function readSessionToken() {
  return SecureStore.getItemAsync(SESSION_TOKEN_KEY);
}

export function clearSessionToken() {
  return SecureStore.deleteItemAsync(SESSION_TOKEN_KEY);
}
