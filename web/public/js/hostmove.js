// P547 (PASSKEY-SEAT-MODEL §3 ④): the seat moved to a new Agent J host. The old host verified the new host's arrival with
// the transfer code it alone holds, then tells every remote `{"t":"host_moved","relay","channel","hostPub","name"}` over
// this end-to-end session (`at` = unix s of the move, optional). The page checks the shape before it touches the stored pairing: our own relay only (the same
// allowRelay as a pairing link), channel = 16 bytes, host key = 32 bytes, both base64url. Pure — p547_hostmove.test.mjs.
import { unb64u } from '../proto/wire.js';

const B64U = /^[A-Za-z0-9_-]+$/;
function bytes(v, n) {
  if (typeof v !== 'string' || !B64U.test(v) || v.length !== Math.ceil(n * 4 / 3)) return null;
  try { const b = unb64u(v); return b.length === n ? b : null; } catch { return null; }
}
const cleanName = (v) => (typeof v === 'string' ? v.slice(0, 256).replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Co}]/gu, '').replace(/\s+/gu, ' ').trim().slice(0, 32) : '');

/** → {relay, channel, hostPub (Uint8Array 32), name} or null. allowRelay(url) → boolean (may throw on a non-URL). */
export function parseHostMoved(m, allowRelay) {
  if (!m || m.t !== 'host_moved' || typeof m.relay !== 'string' || m.relay.length > 200) return null;
  let ok = false;
  try { ok = !!allowRelay(m.relay); } catch { ok = false; }
  if (!ok) return null;
  if (!bytes(m.channel, 16)) return null;
  const hostPub = bytes(m.hostPub, 32);
  if (!hostPub) return null;
  // `at` (unix s, when the old host saw the move) is shown on the confirm sheet; absent / implausible → 0 (the page uses now)
  const at = Number.isSafeInteger(m.at) && m.at > 1_600_000_000 && m.at < 4_000_000_000 ? m.at : 0;
  return { relay: m.relay.replace(/\/+$/, ''), channel: m.channel, hostPub, name: cleanName(m.name), at };
}

/** The stored pairing after the move: same device keys (dk stays), the new computer; no passkey there yet, the renewal
 *  ticket of the old computer is void (rtAt dropped → the new host issues one after ready). */
export function movedRecord(h, mv) {
  const keep = { ...(h || {}) };
  for (const k of ['rtAt', 'plat', 'boot', 'removed']) delete keep[k];
  return { ...keep, relay: mv.relay, channel: mv.channel, hostPub: mv.hostPub, approved: true, pk: false, pkAsked: false,
    ...(mv.name ? { name: mv.name } : {}) };
}

/** 「HH:MM」 of the moment the move arrived, 24 h, local clock (the confirm sheet). */
export function movedAt(ms) {
  const d = new Date(Number.isFinite(ms) ? ms : Date.now());
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
