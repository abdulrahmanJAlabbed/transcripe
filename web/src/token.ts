/** Studio token.
 *
 * Loopback studios need none. A studio opened to the network mints one and
 * hands it over in the launch URL (`?token=…`); we keep it and tidy the URL so
 * it doesn't linger in history or get pasted into a chat by accident.
 */
const KEY = "transcripe.token";

function adopt(): string {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    localStorage.setItem(KEY, fromUrl);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
    return fromUrl;
  }
  return localStorage.getItem(KEY) ?? "";
}

let token = adopt();

export function setToken(value: string) {
  token = value.trim();
  if (token) localStorage.setItem(KEY, token);
  else localStorage.removeItem(KEY);
}

export function hasToken() {
  return token.length > 0;
}

/** fetch() with the studio token attached, when we have one. */
export function api(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (token) headers.set("X-Transcripe-Token", token);
  return fetch(path, { ...init, headers });
}
