// P545 (FEEDBACK #309, PROTOCOL §40): one address per seat — m.agentj.app/<seat id>. The seat id is public: the first
// 40 bits of SHA-256("agentj/seat-url/v1\n" ‖ channel) as 8 lower-case base32 letters (host/agentj/seaturl.py,
// dashboard src/seaturl.ts; vectors protocol/vectors/p545-seat-url.json). js/seatscope.js (first script in <head>) has
// already split this page's localStorage by seat; this module holds the rest: the id, the seat's own paths, the list of
// seats this browser has opened (kept on the origin's own localStorage so the root page can offer 「选择要连接的席位」),
// and the seat page's Home Screen name / passkey hint.
import { sha256 } from '../proto/noise.js';

const S = globalThis.AJSeat || { id: '', base: '/', root: null, db: (n) => n };
export const SEAT = S.id;                 // '' = the root page (m.agentj.app/)
export const BASE = S.base;               // '/' | '/<seat id>/'
export const SEAT_RE = /^[a-z2-7]{8}$/;
const ALPHABET = 'abcdefghijklmnopqrstuvwxyz234567';
const LABEL = 'agentj/seat-url/v1\n';

/** channel (b64url, 22) → seat id (8 letters). */
export async function seatId(channel) {
  if (!/^[A-Za-z0-9_-]{22}$/.test(String(channel))) throw new Error('bad channel');
  const d = await sha256(new TextEncoder().encode(LABEL + channel));
  let n = 0n;
  for (let i = 0; i < 5; i++) n = (n << 8n) | BigInt(d[i]);
  let out = '';
  for (let i = 0; i < 8; i++) out += ALPHABET[Number((n >> BigInt(35 - 5 * i)) & 31n)];
  return out;
}
/** The address to open / install: <this origin>/<seat id>/ (the page's own origin, so tests stay on loopback). */
export const seatHref = (id) => location.origin + '/' + id + '/';
/** The same, as shown to the owner: m.agentj.app/<seat id>. */
export const seatShown = (id) => location.host + '/' + id;
/** A page route of this page ('/friends', '/bots', '/') under this seat's path. */
export const routePath = (p) => BASE + String(p).replace(/^\/+/, '');
/** location.pathname without this seat's prefix ('/<seat id>/friends' → '/friends'). */
export function routeOf(pathname = location.pathname) {
  return SEAT && pathname.startsWith(BASE) ? '/' + pathname.slice(BASE.length) : pathname;
}

// ---------------------------------------------------------------- the seats this browser has opened (root localStorage "aj.seats")
// [{s: seat id, n: the Agent's name (display text), t: last ready (ms)}], newest first, ≤ 16. Names only — no channel, no key.
const LIST_KEY = 'aj.seats';
const MAX = 16;
function readList() {
  try {
    const v = JSON.parse((S.root && S.root.getItem(LIST_KEY)) || '[]');
    return Array.isArray(v) ? v.filter((e) => e && SEAT_RE.test(e.s) && typeof e.n === 'string' && Number.isFinite(e.t)).slice(0, MAX) : [];
  } catch { return []; }
}
function writeList(list) { try { S.root && S.root.setItem(LIST_KEY, JSON.stringify(list.slice(0, MAX))); } catch { /* blocked: the list is only a convenience */ } }
export const knownSeats = () => readList();
/** This seat's page is paired and ready (name = the Agent's display name): it goes to the top of the list. */
export function remember(id, name) {
  if (!SEAT_RE.test(id)) return;
  const n = String(name || '').slice(0, 64);
  writeList([{ s: id, n, t: Date.now() }, ...readList().filter((e) => e.s !== id)]);
}
/** Unpaired / removed on that seat's page: off the list. */
export function forgetSeat(id) { writeList(readList().filter((e) => e.s !== id)); }

// ---------------------------------------------------------------- this seat page's Home Screen identity
/** The Agent's name on the icon ("Agent J · Father@TB") and this seat's passkey (credential id) for the installed app. */
export function setInstallHints({ name, pk } = {}) {
  if (!SEAT) return;
  try {
    if (name !== undefined) { if (name) localStorage.setItem(NAME_KEY, String(name).slice(0, 64)); else localStorage.removeItem(NAME_KEY); }
    if (pk !== undefined) { if (pk) localStorage.setItem(PK_KEY, pk); else localStorage.removeItem(PK_KEY); }
  } catch { /* blocked storage: the icon says Agent J */ }
  if (typeof globalThis.__ajManifest === 'function') globalThis.__ajManifest();
}
const NAME_KEY = 'aj.seatName';           // scoped by seatscope.js → "aj.seatName@<seat id>"
const PK_KEY = 'aj.seatPk';
// The installed seat app starts at ./#c=<credential id> (worker.ts seatManifest): taken here, before app.js reads the hash.
let launchPk = null;
if (SEAT && /^#c=[A-Za-z0-9_-]{16,512}$/.test(location.hash)) {
  launchPk = location.hash.slice(3);
  history.replaceState(history.state, '', location.pathname + location.search);
}
/** This seat's own passkey(s) for a restore: the installed app's #c= and the page's last known one. [] = ask for any. */
export function passkeyHint() {
  if (!SEAT) return [];
  const out = [];
  if (launchPk) out.push(launchPk);
  try { const v = localStorage.getItem(PK_KEY); if (v && /^[A-Za-z0-9_-]{16,512}$/.test(v) && !out.includes(v)) out.push(v); } catch { /* blocked */ }
  return out;
}

/** The root page's pairing markers follow a pairing moved to its seat's page (install id: the computer sees the same phone). */
export function adoptRootMarkers() {
  if (!SEAT || !S.root) return;
  for (const k of ['aj.iid', 'aj.paired']) {
    try { const v = S.root.getItem(k); if (v !== null && localStorage.getItem(k) === null) localStorage.setItem(k, v); } catch { /* blocked */ }
  }
}
