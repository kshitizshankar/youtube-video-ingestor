/** Client-side auth helpers. Stores a base64(user:pass) token in
 * localStorage; wraps fetch + lets us tack the token onto SSE / WebSocket
 * URLs (which can't carry custom headers in the browser). */

const KEY = "vvi.authToken";

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(user: string, pass: string): void {
  // base64 encode user:pass. Handles unicode via utf-8 TextEncoder.
  const bytes = new TextEncoder().encode(`${user}:${pass}`);
  const bin = Array.from(bytes, (b) => String.fromCharCode(b)).join("");
  localStorage.setItem(KEY, btoa(bin));
}

export function clearToken(): void {
  localStorage.removeItem(KEY);
}

export async function authFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(opts.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Basic ${token}`);
  }
  return fetch(url, { ...opts, headers });
}

/** Append `?token=...` (or `&token=...`) so that EventSource and WebSocket
 * URLs can carry credentials. */
export function withAuthQuery(url: string): string {
  const token = getToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}
