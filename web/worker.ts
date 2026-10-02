// agentjarvis web client Worker (alpha-web.agentjarvis.net). Serves public/ — public since 2026-10-02 (Cloudflare Access
// removed), never indexed (X-Robots-Tag noindex + robots.txt Disallow all). CSP connect-src = the relay only, and the client
// pins the relay itself. Wrong host, any method other than GET/HEAD, or a malformed RELAY_URL → bare 404.
// Self-contained (no imports): the same file builds from the public source tree. No logging of anything (PR1).

export interface WebEnv {
  ASSETS: { fetch(req: Request): Promise<Response> };
  WEB_HOST: string;           // alpha-web.agentjarvis.net
  RELAY_URL: string;          // wss://alpha-relay.agentjarvis.net — the only connect-src
}

export function notFound(): Response {
  return new Response("Not Found", { status: 404, headers: { "content-type": "text/plain", "x-robots-tag": "noindex, nofollow" } });
}

export function csp(relay: string): string {
  return "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; " +
    `connect-src ${relay}; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`;
}

export function webHeaders(relay: string): Record<string, string> {
  return {
    "content-security-policy": csp(relay),
    "permissions-policy": "camera=(self), microphone=(), geolocation=()",
    "x-robots-tag": "noindex, nofollow, noarchive",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "strict-transport-security": "max-age=31536000",
    "cache-control": "private, no-store",
  };
}

export async function handle(req: Request, env: WebEnv): Promise<Response> {
  if (new URL(req.url).hostname !== env.WEB_HOST) return notFound();
  if (req.method !== "GET" && req.method !== "HEAD") return notFound();
  if (!env.RELAY_URL || !/^wss:\/\/[a-z0-9.-]+$/.test(env.RELAY_URL)) return notFound();
  const res = await env.ASSETS.fetch(req);
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(webHeaders(env.RELAY_URL))) out.headers.set(k, v);
  return out;
}

export default {
  fetch(req: Request, env: WebEnv): Promise<Response> { return handle(req, env); },
};
