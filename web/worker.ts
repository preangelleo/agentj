// agentj web client Worker (m.agentj.app; legacy alpha-web.agentjarvis.net for one version cycle — phones paired there
// keep their keys in that origin's storage; app.js sends a visitor with no pairing there on to m.agentj.app). Serves
// public/ — public since 2026-10-02 (Cloudflare Access removed), never indexed (X-Robots-Tag noindex + robots.txt
// Disallow all). CSP connect-src = our two relay hosts and exact version.json metadata only (relay.agentj.app + the legacy alpha-relay.agentjarvis.net that
// old pairing links carry), and the client pins the same two itself. Wrong host, any method other than GET/HEAD, or a
// malformed relay URL → bare 404. One POST endpoint: /.aj/rt (P122, PROTOCOL §12.1) — the phone's renewal-ticket cookie.
// Self-contained (no imports): the same file builds from the public source tree. No logging of anything (PR1).

export interface WebEnv {
  ASSETS: { fetch(req: Request): Promise<Response> };
  PREVIEW_CONFIG?: string;
  PREVIEW_NONCES?: { idFromName(name: string): unknown; get(id: unknown): { fetch(req: Request): Promise<Response> } };
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
  const fileSources=relay.split(' ').filter(source=>RELAY_RE.test(source)).map(source=>source.replace(/^wss:/,'https:')+'/v1/file/').join(' ');
  return "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; " +
    `connect-src ${relay}${fileSources ? " " + fileSources : ""}${versionSource ? " " + versionSource : ""}; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`;
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
/** path = where the cookie lives: /.aj/rt for the root page, /<seat id>/.aj/rt for a seat's page (P545: one ticket per seat). */
export function rtSetCookie(value: string, secure: boolean, path = RT_PATH): string {
  return `${rtCookieName(secure)}=${value}; Path=${path}; Max-Age=${value ? RT_MAX_AGE : 0}; ${secure ? "Secure; " : ""}HttpOnly; SameSite=Strict`;
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
export async function rtEndpoint(req: Request, secure = true, path = RT_PATH): Promise<Response> {
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
  if (m.op === "set" && typeof m.v === "string" && RT_VALUE.test(m.v)) return rtResponse(204, null, rtSetCookie(m.v, secure, path));
  if (m.op === "clear") return rtResponse(204, null, rtSetCookie("", secure, path));
  if (m.op === "has") return rtResponse(200, { has: rtRead(req, secure) !== null });   // a ready phone: is its cookie still there?
  if (m.op === "prove" && typeof m.c === "string" && RT_CHALLENGE.test(m.c)) {
    const v = rtRead(req, secure);
    if (!v) return rtResponse(200, {});
    const [, h, i, k] = v.split(".");
    return rtResponse(200, { h, i, p: b64uEncode(await rtProof(b64uDecode(k), b64uDecode(m.c))) });
  }
  return notFound();
}

/** The page's own two same-origin fetch targets, exact URLs (connect-src never says 'self'): version.json and /.aj/rt
 *  (a seat's page: its own /<seat id>/.aj/rt; version.json is one file for every page). */
export const connectSelf = (url: string) => {
  const s = seatRoute(new URL(url).pathname);
  return `${new URL("/version.json", url).href} ${new URL(s ? `/${s.seat}${RT_PATH}` : RT_PATH, url).href}`;
};

// ---------------------------------------------------------------- P545 (FEEDBACK #309, PROTOCOL §40): one address per seat
// m.agentj.app/<seat id>/ is the same page as m.agentj.app/, served under the seat's own path so that everything the browser
// keys by path follows the seat: the Home Screen app (manifest id / start_url / scope = /<seat id>/ — Android installs one app
// per seat and never offers to open another seat's), the service worker and its push subscription, and the renewal cookie
// (Path=/<seat id>/.aj/rt). Storage keyed by origin (localStorage, IndexedDB) is split by the page itself (js/seatscope.js).
// The seat id is 8 lower-case base32 letters (a one-way hash of the channel; host/agentj/seaturl.py); no top-level file or
// page route of public/ has that shape (static.test.mjs). /<seat id> without the slash → 308 to /<seat id>/ (a #p= pairing
// fragment survives a redirect).
export const SEAT_PATH = /^\/([a-z2-7]{8})(\/.*)?$/;
export function seatRoute(pathname: string): { seat: string; rest: string | null } | null {
  const m = SEAT_PATH.exec(pathname);
  return m ? { seat: m[1], rest: m[2] ?? null } : null;
}
const MANIFESTS = ["/manifest.webmanifest", "/manifest-dark.webmanifest", "/manifest-light.webmanifest"];
const CRED_RE = /^[A-Za-z0-9_-]{16,512}$/;
/** The seat's display name from the page (?n=): no control / format / private-use characters, one space, ≤ 32 code points. */
export function cleanSeatName(v: string | null): string {
  const s = String(v ?? "").slice(0, 256).replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]/gu, "").replace(/\s+/gu, " ").trim();
  return Array.from(s).slice(0, 32).join("").trim();
}
/** The installed-app identity of one seat: own id / start_url / scope, the seat's name on the icon ("Agent J · Father@TB"),
 *  and — when the page knows it — its own passkey's credential id in start_url's fragment (#c=: never sent on a launch), so
 *  the installed app asks for that passkey only (a credential id is a public identifier, not a secret). Icons and
 *  share_target stay relative (./…). */
export function seatManifest(base: Record<string, unknown>, seat: string, q: URLSearchParams): Record<string, unknown> {
  const n = cleanSeatName(q.get("n"));
  const c = q.get("c");
  const out: Record<string, unknown> = { ...base, id: `/${seat}/`, start_url: CRED_RE.test(c || "") ? `./#c=${c}` : "./", scope: "./" };
  if (n) { out.name = `Agent J · ${n}`; out.short_name = n; }
  return out;
}

async function seatAsset(req: Request, env: WebEnv, seat: string, rest: string): Promise<Response> {
  const url = new URL(req.url);
  url.pathname = rest;
  const inner = assetRequest(new Request(url.toString(), { method: req.method, headers: req.headers }));
  const res = await env.ASSETS.fetch(inner);
  if (!MANIFESTS.includes(rest) || !res.ok || req.method !== "GET") return res;
  let base: Record<string, unknown>;
  try { base = await res.json(); } catch { return notFound(); }
  return new Response(JSON.stringify(seatManifest(base, seat, url.searchParams)), { status: 200, headers: { "content-type": "application/manifest+json" } });
}

export async function handle(req: Request, env: WebEnv): Promise<Response> {
  const host = new URL(req.url).hostname;
  if (!env.WEB_HOST || (host !== env.WEB_HOST && !(env.LEGACY_WEB_HOST && host === env.LEGACY_WEB_HOST))) return notFound();
  const path = new URL(req.url).pathname;
  // Internal immutable bundles are never public, including encoded and seat-prefixed variants.
  if (previewInternalPath(path)) return notFound();
  if (path === RT_PATH) return rtEndpoint(req, true);
  const seat = seatRoute(path);
  if (seat && seat.rest === RT_PATH) return rtEndpoint(req, true, `/${seat.seat}${RT_PATH}`);
  if (seat && seat.rest?.startsWith(PREVIEW_PATH + "/")) return previewEndpoint(req, env, seat.seat, seat.rest);
  if (req.method !== "GET" && req.method !== "HEAD") return notFound();
  const relays = relaySources(env);
  if (!relays) return notFound();
  if (seat && seat.rest === null) {
    return new Response(null, { status: 308, headers: { location: `/${seat.seat}/${new URL(req.url).search}`, "cache-control": "no-store", "x-robots-tag": "noindex, nofollow" } });
  }
  const selected = seat && seat.rest ? await previewAsset(req, env, seat.seat, seat.rest) : null;
  const res = selected || (seat ? await seatAsset(req, env, seat.seat, seat.rest as string) : await env.ASSETS.fetch(assetRequest(req)));
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(webHeaders(relays, connectSelf(req.url)))) out.headers.set(k, v);
  if (selected) {
    out.headers.set("vary", "Cookie");
    out.headers.set("content-security-policy", csp(relays, connectSelf(req.url) + " " + new URL(`/${seat!.seat}${PREVIEW_PATH}/auth`, req.url).href));
  }
  return out;
}

export default {
  fetch(req: Request, env: WebEnv): Promise<Response> { return handle(req, env); },
};

// #547: publisher-selected seat preview. Both proof domains are deliberately distinct from approvals.
export const PREVIEW_PATH = "/.aj/preview";
export const PREVIEW_DEVICE_LABEL = "agentj/web-preview/device/v1\n";
export const PREVIEW_HOST_LABEL = "agentj/web-preview/host/v1\n";
const PREVIEW_COOKIE = "__Secure-aj_preview";
const PREVIEW_SEEN = "__Secure-aj_preview_seen";
const HEX256 = /^[a-f0-9]{64}$/;
const B64KEY = /^[A-Za-z0-9_-]{43}$/;
const utf8 = new TextEncoder();
const previewCache = new Map<string, Promise<PreviewBundle | null>>();
interface PreviewPointer { bundleSha256: string; manifestSha256: string; combined: string; hostPublicKey: string }
interface PreviewBundle { v: number; manifest: { combined: string; files: Record<string, string>; [key: string]: unknown }; files: Record<string, string> }
interface PreviewProof { message: string; deviceSignature: string; hostSignature: string }
interface PreviewMessage { v: number; origin: string; seat: string; manifestSha256: string; devicePublicKey: string; nonce: string; expires: number }
export const previewDigest = async (data: BufferSource) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", data)), b => b.toString(16).padStart(2, "0")).join("");
export function previewInternalPath(path: string): boolean {
  try {
    let value = path;
    for (let i = 0; i < 4; i++) {
      if (value.replace(/\\/g, "/").split("/").includes(".preview")) return true;
      const next = decodeURIComponent(value); if (next === value) return false; value = next;
    }
    return true;
  } catch { return true; }
}
export function previewPointer(env: WebEnv, seat: string): PreviewPointer | null {
  try {
    const cfg = JSON.parse(env.PREVIEW_CONFIG || "null");
    const p = cfg?.v === 1 && cfg.seats && Object.hasOwn(cfg.seats, seat) ? cfg.seats[seat] : null;
    return p && HEX256.test(p.bundleSha256) && HEX256.test(p.manifestSha256) && HEX256.test(p.combined) && B64KEY.test(p.hostPublicKey) ? p : null;
  } catch { return null; }
}
const previewFile = (path: string) => path.length < 256 && !path.startsWith("/") && !path.includes("\\") && !path.includes("%") && path.split("/").every(p => p && p !== "." && p !== ".." && p !== ".preview");
/** One immutable bundle: a single subrequest, bounded memory; index is withheld until EVERY file verifies. */
export async function previewBundle(req: Request, env: WebEnv, pin: PreviewPointer): Promise<PreviewBundle | null> {
  const key = pin.bundleSha256 + ":" + pin.manifestSha256 + ":" + pin.combined;
  if (!previewCache.has(key)) previewCache.set(key, (async () => {
    try {
      const url = new URL(req.url); url.pathname = `/.preview/${pin.bundleSha256}.json`; url.search = "";
      const response = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
      if (!response.ok || Number(response.headers.get("content-length") || 0) > 24 * 1024 * 1024) return null;
      const raw = await response.arrayBuffer();
      if (raw.byteLength > 24 * 1024 * 1024 || await previewDigest(raw) !== pin.bundleSha256) return null;
      const bundle = JSON.parse(new TextDecoder().decode(raw)) as PreviewBundle;
      if (bundle.v !== 1 || !bundle.files || !bundle.manifest || bundle.manifest.combined !== pin.combined) return null;
      const paths = Object.keys(bundle.manifest.files).sort();
      if (paths.length < 1 || paths.length > 1000 || Object.keys(bundle.files).length !== paths.length + 2) return null;
      const listing = paths.map(path => `${bundle.manifest.files[path]}  ${path}\n`).join("");
      if (await previewDigest(utf8.encode(listing)) !== pin.combined) return null;
      for (const path of [...paths, "version.json", "version.js"]) {
        if (!previewFile(path) || !Object.hasOwn(bundle.files, path) || typeof bundle.files[path] !== "string") return null;
        const bytes = b64uDecode(bundle.files[path]);
        if (path === "version.json") {
          if (await previewDigest(bytes) !== pin.manifestSha256 || JSON.stringify(JSON.parse(new TextDecoder().decode(bytes))) !== JSON.stringify(bundle.manifest)) return null;
        } else if (path === "version.js") {
          if (new TextDecoder().decode(bytes) !== "// generated by build.mjs — do not edit\nexport default " + new TextDecoder().decode(b64uDecode(bundle.files["version.json"])).trimEnd() + ";\n") return null;
        } else if (!HEX256.test(bundle.manifest.files[path]) || await previewDigest(bytes) !== bundle.manifest.files[path]) return null;
      }
      if (!Object.hasOwn(bundle.files, "index.html") || !Object.hasOwn(bundle.files, "app.js")) return null;
      if ((bundle.manifest.preview as { channel?: string } | undefined)?.channel !== "preview" || !Object.hasOwn(bundle.files, ".aj/preview/bootstrap.js") || new TextDecoder().decode(b64uDecode(bundle.files[".aj/preview/bootstrap.js"])) !== PREVIEW_BOOTSTRAP) return null;
      return bundle;
    } catch { return null; }
  })());
  const bundle = await previewCache.get(key)!;
  if (!bundle) previewCache.delete(key); // an unavailable candidate is never a persistent PASS or permanent failure cache.
  return bundle;
}
function previewMessage(text: string, req: Request, seat: string, pin: PreviewPointer): PreviewMessage | null {
  try {
    if (text.length > 1024) return null;
    const m = JSON.parse(text) as PreviewMessage;
    const now = Math.floor(Date.now() / 1000);
    if (JSON.stringify(m) !== text || Object.keys(m).join(",") !== "v,origin,seat,manifestSha256,devicePublicKey,nonce,expires") return null;
    return m.v === 1 && m.origin === new URL(req.url).origin && m.origin === "https://m.agentj.app" && m.seat === seat && m.manifestSha256 === pin.manifestSha256 && B64KEY.test(m.devicePublicKey) && B64KEY.test(m.nonce) && Number.isSafeInteger(m.expires) && m.expires > now && m.expires <= now + 60 ? m : null;
  } catch { return null; }
}
async function previewSignature(pub: string, signature: string, message: string): Promise<boolean> {
  if (!/^[A-Za-z0-9_-]{86}$/.test(signature)) return false;
  try { const key = await crypto.subtle.importKey("raw", b64uDecode(pub), { name: "Ed25519" }, false, ["verify"]); return crypto.subtle.verify("Ed25519", key, b64uDecode(signature), utf8.encode(message)); } catch { return false; }
}
export async function previewVerify(proof: PreviewProof, req: Request, seat: string, pin: PreviewPointer): Promise<PreviewMessage | null> {
  if (!proof || typeof proof.message !== "string" || typeof proof.deviceSignature !== "string" || typeof proof.hostSignature !== "string") return null;
  const m = previewMessage(proof.message, req, seat, pin);
  if (!m || !await previewSignature(m.devicePublicKey, proof.deviceSignature, PREVIEW_DEVICE_LABEL + proof.message) || !await previewSignature(pin.hostPublicKey, proof.hostSignature, PREVIEW_HOST_LABEL + proof.message)) return null;
  return m;
}
const previewCookie = (proof: PreviewProof, seat: string, expires: number) => `${PREVIEW_COOKIE}=${b64uEncode(utf8.encode(JSON.stringify(proof)))}; Path=/${seat}/; Max-Age=${Math.max(0, expires - Math.floor(Date.now() / 1000))}; Secure; HttpOnly; SameSite=Strict`;
function previewRead(req: Request): PreviewProof | null {
  const values = (req.headers.get("cookie") || "").split(";").map(v => v.trim()).filter(v => v.startsWith(PREVIEW_COOKIE + "="));
  if (values.length !== 1 || values[0].length > 4096) return null;
  try { return JSON.parse(new TextDecoder().decode(b64uDecode(values[0].slice(PREVIEW_COOKIE.length + 1)))); } catch { return null; }
}
const previewReply = (body: unknown, status = 200, cookie?: string) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "private, no-store, no-transform", "x-content-type-options": "nosniff", "x-robots-tag": "noindex, nofollow", "referrer-policy": "no-referrer", "vary": "Cookie", ...(cookie ? { "set-cookie": cookie } : {}) } });
/** The durable object's only state is a short-lived nonce and fixed metadata. Atomic consume prevents concurrent replays. */
export class PreviewNonces {
  state: any;
  constructor(state: any) { this.state = state; }
  async fetch(req: Request): Promise<Response> {
    const m = await req.json() as { op: string; message: string };
    if (m.op === "issue") {
      const f = JSON.parse(m.message); const key = f.nonce;
      const accepted = await this.state.storage.transaction(async (tx: any) => {
        const now = Math.floor(Date.now() / 1000);
        const outstanding = ((await tx.get("outstanding")) || []).filter((n: any) => n.expires > now);
        if (outstanding.length >= 64) return false;
        for (const n of ((await tx.get("outstanding")) || [])) if (n.expires <= now) await tx.delete(n.nonce);
        outstanding.push({ nonce: key, expires: f.expires });
        await tx.put(key, m.message); await tx.put("outstanding", outstanding); return true;
      });
      if (!accepted) return previewReply({ ok: false }, 429);
      await this.state.storage.setAlarm((f.expires + 5) * 1000);
      return previewReply({ ok: true });
    }
    if (m.op === "consume") {
      const f = JSON.parse(m.message);
      const ok = await this.state.storage.transaction(async tx => {
        if (f.expires <= Math.floor(Date.now() / 1000) || await tx.get(f.nonce) !== m.message) return false;
        await tx.delete(f.nonce);
        await tx.put("outstanding", ((await tx.get("outstanding")) || []).filter((n: any) => n.nonce !== f.nonce)); return true;
      });
      return previewReply({ ok }, ok ? 200 : 409);
    }
    return notFound();
  }
  async alarm(): Promise<void> { await this.state.storage.deleteAll(); }
}
async function previewNonce(env: WebEnv, seat: string, op: string, message: string): Promise<Response> {
  if (!env.PREVIEW_NONCES) return notFound();
  return env.PREVIEW_NONCES.get(env.PREVIEW_NONCES.idFromName(seat)).fetch(new Request("https://nonce.internal/", { method: "POST", body: JSON.stringify({ op, message }) }));
}
async function previewBody(req: Request): Promise<string | null> {
  if (!req.body || Number(req.headers.get("content-length") || 0) > 4096) return null;
  const reader = req.body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      size += value.byteLength; if (size > 4096) { await reader.cancel(); return null; }
      chunks.push(value);
    }
  } catch { return null; }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(bytes);
}
export async function previewEndpoint(req: Request, env: WebEnv, seat: string, rest: string): Promise<Response> {
  const pin = previewPointer(env, seat);
  if (!pin || new URL(req.url).origin !== "https://m.agentj.app" || !relaySources(env)) return notFound();
  if (rest === PREVIEW_PATH + "/bootstrap.js" && req.method === "GET") return new Response(PREVIEW_BOOTSTRAP, { headers: { "content-type": "text/javascript", ...webHeaders(relaySources(env)!), "vary": "Cookie" } });
  if (rest !== PREVIEW_PATH + "/auth" || req.method !== "POST" || req.headers.get("origin") !== new URL(req.url).origin || !/^application\/json\b/i.test(req.headers.get("content-type") || "") || (req.headers.has("sec-fetch-site") && req.headers.get("sec-fetch-site") !== "same-origin")) return notFound();
  const raw = await previewBody(req); if (raw === null) return notFound();
  let body; try { body = JSON.parse(raw); } catch { return notFound(); }
  if (body?.op === "clear") {
    const out = previewReply({ ok: true });
    for (const name of [PREVIEW_COOKIE, PREVIEW_SEEN]) out.headers.append("set-cookie", `${name}=; Path=/${seat}/; Max-Age=0; Secure; HttpOnly; SameSite=Strict`);
    return out;
  }
  if (!body || !await previewBundle(req, env, pin)) return previewReply({ ok: false }, 503);
  if (body.op === "challenge" && B64KEY.test(body.devicePublicKey)) {
    const message = JSON.stringify({ v: 1, origin: new URL(req.url).origin, seat, manifestSha256: pin.manifestSha256, devicePublicKey: body.devicePublicKey, nonce: b64uEncode(crypto.getRandomValues(new Uint8Array(32))), expires: Math.floor(Date.now() / 1000) + 60 });
    try { const r = await previewNonce(env, seat, "issue", message); return r.ok ? previewReply({ ok: true, message }) : previewReply({ ok: false }, 503); } catch { return previewReply({ ok: false }, 503); }
  }
  if (body.op === "authorize") {
    const m = await previewVerify(body.proof, req, seat, pin);
    if (!m) return previewReply({ ok: false }, 403);
    try { const r = await previewNonce(env, seat, "consume", body.proof.message); if (!r.ok) return previewReply({ ok: false }, 409); const out = previewReply({ ok: true, expires: m.expires }, 200, previewCookie(body.proof, seat, m.expires)); out.headers.append("set-cookie", `${PREVIEW_SEEN}=1; Path=/${seat}/; Max-Age=86400; Secure; HttpOnly; SameSite=Strict`); return out; } catch { return previewReply({ ok: false }, 503); }
  }
  return notFound();
}
function previewType(path: string): string {
  if (path.endsWith(".js")) return "text/javascript";
  if (path.endsWith(".css")) return "text/css";
  if (path.endsWith(".html")) return "text/html; charset=utf-8";
  if (path.endsWith(".json")) return "application/json";
  if (path.endsWith(".webmanifest")) return "application/manifest+json";
  if (path.endsWith(".svg")) return "image/svg+xml";
  return ({ png: "image/png", ico: "image/x-icon", jpg: "image/jpeg", jpeg: "image/jpeg", woff2: "font/woff2", mp3: "audio/mpeg", wav: "audio/wav", webp: "image/webp", wasm: "application/wasm" } as Record<string, string>)[path.split(".").pop() || ""] || "application/octet-stream";
}
export async function previewInject(html: string, seat: string): Promise<string> {
  const integrity = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.digest("SHA-256", utf8.encode(PREVIEW_BOOTSTRAP)))));
  return html.replace("</body>", `<script type="module" src="/${seat}${PREVIEW_PATH}/bootstrap.js" integrity="sha256-${integrity}" crossorigin="anonymous"></script></body>`);
}
export async function previewAsset(req: Request, env: WebEnv, seat: string, rest: string): Promise<Response | null> {
  if (new URL(req.url).origin !== "https://m.agentj.app") return null;
  const page = rest === "/" || SPA_PATHS.includes(rest) || rest === "/index.html";
  const pin = previewPointer(env, seat);
  // No client cookie may change a nonlisted seat, including its errors and response bytes.
  if (!pin) return null;
  const bundle = await previewBundle(req, env, pin);
  if (!bundle) return null; // missing/corrupt candidate always falls back to the exact formal collection.
  const proof = previewRead(req);
  const authorized = proof && await previewVerify(proof, req, seat, pin);
  if (!authorized) {
    if (!page || req.method !== "GET") {
      // An expired candidate document must never import formal code into its live module graph.
      if ((req.headers.get("cookie") || "").split(";").some(v => v.trim() === PREVIEW_SEEN + "=1")) return new Response("Preview lease expired; refresh", { status: 409 });
      return null;
    }
    const formal = await seatAsset(req, env, seat, rest);
    if (!formal.ok) return formal;
    const fallback = new Response(await previewInject(await formal.text(), seat), formal);
    fallback.headers.append("set-cookie", `${PREVIEW_SEEN}=; Path=/${seat}/; Max-Age=0; Secure; HttpOnly; SameSite=Strict`);
    return fallback;
  }
  const path = page ? "index.html" : rest.slice(1);
  if (!previewFile(path) || !Object.hasOwn(bundle.files, path)) return notFound();
  let body: BodyInit = b64uDecode(bundle.files[path]);
  if (MANIFESTS.includes(rest)) body = JSON.stringify(seatManifest(JSON.parse(new TextDecoder().decode(body as Uint8Array)), seat, new URL(req.url).searchParams));
  // Candidate HTML already contains the fixed bootstrap + SRI before manifest hashing; serve its exact bytes.
  return new Response(req.method === "HEAD" ? null : body, { headers: { "content-type": previewType(path), "x-agentj-web-channel": "preview", "x-agentj-web-hash": pin.combined, "x-agentj-preview-manifest": pin.manifestSha256, "vary": "Cookie" } });
}

// Runs only on publisher-listed seat HTML. Reads existing keys; never pairs, creates a database/key, or touches approvals.
export const PREVIEW_BOOTSTRAP = `import * as session from '../../js/session.js';
const seat=location.origin==='https://m.agentj.app'?/^\\/([a-z2-7]{8})\\//.exec(location.pathname)?.[1]:null;
const enc=new TextEncoder();
const b64=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
async function records(){
 if(!seat)return null;
 return new Promise(resolve=>{
  const q=indexedDB.open('agentjarvis@'+seat);
  q.onupgradeneeded=()=>q.transaction.abort();
  q.onerror=e=>{e.preventDefault();resolve(null)};
  q.onsuccess=()=>{const db=q.result;if(!db.objectStoreNames.contains('kv')){db.close();resolve(null);return;}
   const tx=db.transaction('kv','readonly'),s=tx.objectStore('kv'),h=s.get('host'),k=s.get('sign');
   tx.oncomplete=()=>{db.close();resolve(h.result?.approved&&k.result?.priv&&k.result?.pub?{host:h.result,key:k.result}:null)};
   tx.onerror=tx.onabort=()=>{db.close();resolve(null)};
  };
 });
}
async function authorize(){
 const r=await records();if(!r)return false;
 if(!await session.whenReady(20000))return false;
 const endpoint='/'+seat+'/.aj/preview/auth';
 const post=async body=>{const r=await fetch(endpoint,{method:'POST',headers:{'content-type':'application/json'},credentials:'same-origin',cache:'no-store',body:JSON.stringify(body)});if(!r.ok)throw Error('preview unavailable');return r.json()};
 const c=await post({op:'challenge',devicePublicKey:b64(r.key.pub)});
 const answer=await session.ask1({t:'web_preview_attest',message:c.message},'web_preview_proof',15000);
 if(!answer.ok||typeof answer.signature!=='string')return false;
 const signature=await crypto.subtle.sign('Ed25519',r.key.priv,enc.encode('agentj/web-preview/device/v1\\n'+c.message));
 const a=await post({op:'authorize',proof:{message:c.message,deviceSignature:b64(signature),hostSignature:answer.signature}});
 return !!a.ok;
}
try {
 const active=document.querySelector('meta[name="agentj-web-preview"]');
 if(await authorize()){
  if(!active)location.reload();
  else {
   let flight=null;
   const fallback=async()=>{try{await fetch('/'+seat+'/.aj/preview/auth',{method:'POST',headers:{'content-type':'application/json'},credentials:'same-origin',cache:'no-store',body:JSON.stringify({op:'clear'})})}catch{}location.reload()};
   const renew=()=>flight||(flight=authorize().then(ok=>{if(!ok)return fallback()}).catch(fallback).finally(()=>{flight=null}));
   setInterval(renew,40000);
   document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')renew()});
  }
 }
} catch { /* candidate unavailable or unpaired: the current formal page keeps working */ }
`;
