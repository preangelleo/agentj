// agentj web client Worker (m.agentj.app; legacy alpha-web.agentjarvis.net for one version cycle — phones paired there
// keep their keys in that origin's storage; app.js sends a visitor with no pairing there on to m.agentj.app). Serves
// public/ — public since 2026-10-02 (Cloudflare Access removed), never indexed (X-Robots-Tag noindex + robots.txt
// Disallow all). CSP connect-src = our two relay hosts and exact version.json metadata only (relay.agentj.app + the legacy alpha-relay.agentjarvis.net that
// old pairing links carry), and the client pins the same two itself. Wrong host, any method other than GET/HEAD, or a
// malformed relay URL → bare 404. One POST endpoint: /.aj/rt (P122, PROTOCOL §12.1) — the phone's renewal-ticket cookie.
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
export function csp(relay: string, versionSource = ""): string {
  return "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; " +
    `connect-src ${relay}${versionSource ? " " + versionSource : ""}; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`;
}

export function webHeaders(relay: string, versionSource = ""): Record<string, string> {
  return {
    "content-security-policy": csp(relay, versionSource),
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
export const SPA_PATHS = ["/friends", "/bots"];
export function assetRequest(req: Request): Request {
  const url = new URL(req.url);
  if (!SPA_PATHS.includes(url.pathname)) return req;
  url.pathname = "/";
  return new Request(url.toString(), { method: req.method, headers: req.headers });
}

// ---------------------------------------------------------------- P122 renewal ticket (PROTOCOL §12.1)
// The page hands the host's ticket here once; it lives ONLY as a first-party cookie: HttpOnly (page script cannot read it),
// Secure, SameSite=Strict, Path=/.aj/rt (sent with no other request), ~400 days. Safari's 7-day eviction of script-writable
// storage (ITP) does not touch a server-set cookie of the site itself. A page that lost its keys sends a 32-byte challenge
// (made over its NEW keys) and gets {h, i, p}: the user handle, the ticket id and HMAC-SHA256(secret, label ‖ challenge) —
// never the secret. Stateless: nothing stored, nothing logged. Same-origin JSON POST only (Origin + Sec-Fetch-Site), so
// another site can neither read a proof nor plant its own ticket (a planted ticket would point this phone at its computer).
export const RT_PATH = "/.aj/rt";
export const RT_MAX_AGE = 400 * 86400;                       // the longest any browser keeps a cookie (Chrome caps at 400 d)
const RT_VALUE = /^1\.[A-Za-z0-9_-]{67}\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}$/;   // 1.<handle 50 B>.<id 16 B>.<secret 32 B>
const RT_CHALLENGE = /^[A-Za-z0-9_-]{43}$/;                 // 32 bytes, b64url
const RT_PROOF_LABEL = "agentjarvis/rt/proof/v1\n";

export const rtCookieName = (secure: boolean) => (secure ? "__Secure-aj_rt" : "aj_rt");
export function rtSetCookie(value: string, secure: boolean): string {
  return `${rtCookieName(secure)}=${value}; Path=${RT_PATH}; Max-Age=${value ? RT_MAX_AGE : 0}; ${secure ? "Secure; " : ""}HttpOnly; SameSite=Strict`;
}
function rtRead(req: Request, secure: boolean): string | null {
  const name = rtCookieName(secure) + "=";
  for (const part of (req.headers.get("cookie") || "").split(";")) {
    const c = part.trim();
    if (c.startsWith(name) && RT_VALUE.test(c.slice(name.length))) return c.slice(name.length);
  }
  return null;
}
const b64uDecode = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4)), (c) => c.charCodeAt(0));
const b64uEncode = (b: Uint8Array) => btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
/** HMAC-SHA256(secret, label ‖ challenge) — host/agentj/renewal.py proof(). */
export async function rtProof(secret: Uint8Array, challenge: Uint8Array): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", secret, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const label = new TextEncoder().encode(RT_PROOF_LABEL);
  const msg = new Uint8Array(label.length + challenge.length);
  msg.set(label); msg.set(challenge, label.length);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, msg));
}
function rtResponse(status: number, body: unknown = null, cookie?: string): Response {
  const h: Record<string, string> = { "cache-control": "no-store", "x-robots-tag": "noindex, nofollow", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer" };
  if (body !== null) h["content-type"] = "application/json";
  if (cookie !== undefined) h["set-cookie"] = cookie;
  return new Response(body === null ? null : JSON.stringify(body), { status, headers: h });
}
/** POST /.aj/rt {op:"set",v} | {op:"prove",c} | {op:"has"} | {op:"clear"}. secure=false only for the loopback test server (http). */
export async function rtEndpoint(req: Request, secure = true): Promise<Response> {
  if (req.method !== "POST") return notFound();
  const origin = new URL(req.url).origin;
  const site = req.headers.get("sec-fetch-site");
  if (req.headers.get("origin") !== origin || (site !== null && site !== "same-origin")) return notFound();
  if (!/^application\/json\b/i.test(req.headers.get("content-type") || "")) return notFound();
  const text = await req.text();
  if (text.length > 512) return notFound();
  let m: { op?: unknown; v?: unknown; c?: unknown };
  try { m = JSON.parse(text); } catch { return notFound(); }
  if (!m || typeof m !== "object") return notFound();
  if (m.op === "set" && typeof m.v === "string" && RT_VALUE.test(m.v)) return rtResponse(204, null, rtSetCookie(m.v, secure));
  if (m.op === "clear") return rtResponse(204, null, rtSetCookie("", secure));
  if (m.op === "has") return rtResponse(200, { has: rtRead(req, secure) !== null });   // a ready phone: is its cookie still there?
  if (m.op === "prove" && typeof m.c === "string" && RT_CHALLENGE.test(m.c)) {
    const v = rtRead(req, secure);
    if (!v) return rtResponse(200, {});
    const [, h, i, k] = v.split(".");
    return rtResponse(200, { h, i, p: b64uEncode(await rtProof(b64uDecode(k), b64uDecode(m.c))) });
  }
  return notFound();
}

/** The page's own two same-origin fetch targets, exact URLs (connect-src never says 'self'): version.json and /.aj/rt. */
export const connectSelf = (url: string) => `${new URL("/version.json", url).href} ${new URL(RT_PATH, url).href}`;

export async function handle(req: Request, env: WebEnv): Promise<Response> {
  const host = new URL(req.url).hostname;
  if (!env.WEB_HOST || (host !== env.WEB_HOST && !(env.LEGACY_WEB_HOST && host === env.LEGACY_WEB_HOST))) return notFound();
  if (new URL(req.url).pathname === RT_PATH) return rtEndpoint(req, true);
  if (req.method !== "GET" && req.method !== "HEAD") return notFound();
  const relays = relaySources(env);
  if (!relays) return notFound();
  const res = await env.ASSETS.fetch(assetRequest(req));
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(webHeaders(relays, connectSelf(req.url)))) out.headers.set(k, v);
  return out;
}

export default {
  fetch(req: Request, env: WebEnv): Promise<Response> { return handle(req, env); },
};
