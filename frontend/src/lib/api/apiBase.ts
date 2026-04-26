/**
 * Base URL for Tanishi REST + WS (no trailing slash).
 * Priority: <meta name="tanishi-api-origin" content="http://host:port"> → VITE_API_BASE → same origin.
 */
export function getApiOrigin(): string {
  if (typeof document !== "undefined") {
    const meta = document.querySelector('meta[name="tanishi-api-origin"]');
    const fromMeta = meta?.getAttribute("content")?.trim();
    if (fromMeta) {
      return fromMeta.replace(/\/$/, "");
    }
  }
  const fromEnv = import.meta.env.VITE_API_BASE?.trim();
  if (fromEnv) {
    return fromEnv.replace(/\/$/, "");
  }
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return "";
}

export function apiUrl(path: string): string {
  const base = getApiOrigin();
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}

/** WebSocket URL for Tanishi `/ws`, aligned with `getApiOrigin()`. */
export function getWsUrl(): string {
  const base = getApiOrigin();
  if (!base) {
    const proto = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = typeof window !== "undefined" ? window.location.host : "";
    return `${proto}//${host}/ws`;
  }
  const u = new URL(`${base}/`);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.pathname = "/ws";
  u.search = "";
  u.hash = "";
  return u.toString().replace(/\/$/, "");
}
