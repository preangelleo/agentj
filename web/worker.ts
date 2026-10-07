// agentj web client Worker (m.agentj.app; legacy alpha-web.agentjarvis.net for one version cycle — phones paired there
// keep their keys in that origin's storage; app.js sends a visitor with no pairing there on to m.agentj.app). Serves
// public/ — public since 2026-10-02 (Cloudflare Access removed), never indexed (X-Robots-Tag noindex + robots.txt
// Disallow all). CSP connect-src = our two relay hosts only (relay.agentj.app + the legacy alpha-relay.agentjarvis.net that
// old pairing links carry), and the client pins the same two itself. Wrong host, any method other than GET/HEAD, or a
// malformed relay URL → bare 404.
// Self-contained (no imports): the same file builds from the public source tree. No logging of anything (PR1).

export interface WebEnv {
  ASSETS: { fetch(req: Request): Promise<Response> };
  WEB_HOST: string;            // m.agentj.app
  LEGACY_WEB_HOST?: string;    // alpha-web.agentjarvis.net
  RELAY_URL: string;           // wss://relay.agentj.app
  LEGACY_RELAY_URL?: string;   // wss://alpha-relay.agentjarvis.net
}

const RELAY_RE = /^wss:\/\/[a-z0-9.-]+$/;

export function notFound(): Response {
  return new Response("Not Found", { status: 404, headers: { "content-type": "text/plain", "x-robots-tag": "noindex, nofollow", "cache-control": "no-store, no-transform" } });
}

/** relay = the connect-src source list (space-separated).
 *  style-src 'unsafe-inline' (0.15.2, P57): mermaid lays a diagram out in the live page — a <style> element and style=""
 *  attributes — before render.js turns it into an <img>. Scripts stay 'self' only (no inline script, no eval), and the page
 *  never parses Agent text as HTML, so no Agent text can become a style; img / font / connect stay 'self' / the relays. */
export function csp(relay: string): string {
  return "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; " +
    `connect-src ${relay}; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`;
}

export function webHeaders(relay: string): Record<string, string> {
  return {
    "content-security-policy": csp(relay),
    "permissions-policy": "camera=(self), microphone=(self), geolocation=()",
    "x-robots-tag": "noindex, nofollow, noarchive",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "strict-transport-security": "max-age=31536000",
    "cache-control": "private, no-store, no-transform",   // no-transform: Cloudflare never injects into it (G-A130)
  };
}

/** The connect-src list: RELAY_URL (required) + LEGACY_RELAY_URL (optional); null when either is malformed (fail closed). */
export function relaySources(env: WebEnv): string | null {
  if (!env.RELAY_URL || !RELAY_RE.test(env.RELAY_URL)) return null;
  if (env.LEGACY_RELAY_URL === undefined || env.LEGACY_RELAY_URL === "") return env.RELAY_URL;
  if (!RELAY_RE.test(env.LEGACY_RELAY_URL)) return null;
  return `${env.RELAY_URL} ${env.LEGACY_RELAY_URL}`;
}

/** Page routes of the single-page client (0.16, §17.7): served as index.html; app.js reads location.pathname. Exact
 *  matches only — "/friends/" would move every relative asset URL, so it stays a 404. */
export const SPA_PATHS = ["/friends"];
export function assetRequest(req: Request): Request {
  const url = new URL(req.url);
  if (!SPA_PATHS.includes(url.pathname)) return req;
  url.pathname = "/";
  return new Request(url.toString(), { method: req.method, headers: req.headers });
}

export async function handle(req: Request, env: WebEnv): Promise<Response> {
  const host = new URL(req.url).hostname;
  if (!env.WEB_HOST || (host !== env.WEB_HOST && !(env.LEGACY_WEB_HOST && host === env.LEGACY_WEB_HOST))) return notFound();
  if (req.method !== "GET" && req.method !== "HEAD") return notFound();
  const relays = relaySources(env);
  if (!relays) return notFound();
  const res = await env.ASSETS.fetch(assetRequest(req));
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(webHeaders(relays))) out.headers.set(k, v);
  return out;
}

export default {
  fetch(req: Request, env: WebEnv): Promise<Response> { return handle(req, env); },
};
