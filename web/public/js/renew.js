// P122 (0.17.4, PROTOCOL §12.1): the renewal ticket — "pair once, stay signed in". The host gives this phone one ticket
// (id + secret) inside the Noise session; this module hands it straight to the web origin, which keeps it ONLY as a
// first-party HttpOnly + Secure + SameSite=Strict cookie on /.aj/rt (worker.ts). Script-writable storage is what Safari's
// 7-day rule (ITP) deletes; such a cookie is not. When this page finds no pairing, it makes new keys, computes the restore
// challenge over them and asks the origin for a proof: HMAC(secret, label ‖ challenge) + the user handle + the ticket id —
// the secret itself never comes back to page script. The host recomputes the proof from THIS session's keys.
// One exact same-origin fetch (static.test.mjs pins it); no credentials leave the origin, nothing is stored here.
import { sha256, concat } from '../proto/noise.js';
import { b64u, unb64u } from '../proto/wire.js';
import { encodeHandle } from './faceid.js';

export const RESTORE_LABEL = 'agentjarvis/rt/restore/v1\n';
const ENDPOINT = '/.aj/rt';

async function call(body) {
  return fetch(new URL(ENDPOINT, location.origin), { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    referrerPolicy: 'no-referrer', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(10000) });
}

/** SHA-256(label ‖ new device X25519 pub ‖ its Ed25519 approval pub ‖ ts u64 BE (ms) ‖ nonce(16)) — host/agentj/renewal.py. */
export async function restoreChallenge(x25519Pub, ed25519Pub, ts, nonce) {
  if (x25519Pub.length !== 32 || ed25519Pub.length !== 32 || nonce.length !== 16) throw new Error('bad challenge parts');
  const t = new Uint8Array(8);
  new DataView(t.buffer).setBigUint64(0, BigInt(ts));
  return sha256(concat(new TextEncoder().encode(RESTORE_LABEL), x25519Pub, ed25519Pub, t, nonce));
}

/** The cookie value the origin keeps: 1.<user handle (§12, 50 B)>.<ticket id>.<secret>. */
export function ticketValue(handle, i, k) {
  if (!/^[A-Za-z0-9_-]{22}$/.test(i) || !/^[A-Za-z0-9_-]{43}$/.test(k)) throw new Error('bad ticket');
  return '1.' + b64u(encodeHandle(handle)) + '.' + i + '.' + k;
}

/** Keep the host's ticket {i, k} for {relayIndex, channel, hostPub}. → true once the origin set the cookie. */
export async function save(handle, i, k) {
  try { return (await call({ op: 'set', v: ticketValue(handle, i, k) })).status === 204; } catch { return false; }
}

/** Is this phone's ticket cookie still there? (A browser can drop cookies but keep the page's storage.) null = unknown. */
export async function has() {
  try { const r = await call({ op: 'has' }); return r.status === 200 ? (await r.json()).has === true : null; } catch { return null; }
}

/** Unpair / removed: the cookie goes too. Never throws. */
export async function clear() {
  try { await call({ op: 'clear' }); } catch { /* offline: the host refuses the dead ticket anyway */ }
}

/** A proof for this page's NEW keys, or null (no ticket here), or throws (offline / origin unreachable). */
export async function prove(x25519Pub, ed25519Pub, now = Date.now()) {
  const nonce = crypto.getRandomValues(new Uint8Array(16));
  const challenge = await restoreChallenge(x25519Pub, ed25519Pub, now, nonce);
  const r = await call({ op: 'prove', c: b64u(challenge) });
  if (r.status !== 200) throw new Error('rt ' + r.status);
  const j = await r.json();
  if (!j || typeof j.h !== 'string' || typeof j.i !== 'string' || typeof j.p !== 'string') return null;
  return { uh: unb64u(j.h), i: j.i, p: j.p, ts: now, nonce: b64u(nonce) };
}
