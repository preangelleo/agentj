// agentjarvis wire helpers shared by the web client and the Node tests (spec: PROTOCOL.md). Mirrors host/jarvis_host/wire.py.
import { concat, sha256, hmac } from './noise.js';

const enc = new TextEncoder();
const dec = new TextDecoder('utf-8', { fatal: true });

export const KIND = { PAIR_INIT: 1, RESUME_INIT: 2, HS_RESP: 3, DATA: 4 };
export const PAD = 256;
export const MAX_JSON = 16 * 1024;
export const MAX_TEXT = 4000;            // UTF-16 code units (String.length), the same count the host uses
export const PAIR_PROLOGUE = 'agentjarvis/v1/pair\n';
export const RESUME_PROLOGUE = 'agentjarvis/v1/resume\n';

export function b64u(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function unb64u(s) {
  if (typeof s !== 'string' || !/^[A-Za-z0-9_-]*$/.test(s)) throw new Error('bad base64url');
  const b = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
}

export const pairPrologue = (channel, pairingId) => concat(enc.encode(PAIR_PROLOGUE + channel + '\n'), pairingId);
export const resumePrologue = (channel) => enc.encode(RESUME_PROLOGUE + channel);

export async function deviceId(pub) { return b64u((await sha256(concat(enc.encode('agentjarvis/device/v1'), pub))).slice(0, 12)); }
export async function channelId(edPub) { return b64u((await sha256(concat(enc.encode('agentjarvis/channel/v1'), edPub))).slice(0, 16)); }

/** 6-digit safety code from the final handshake hash. */
export async function safetyCode(h) {
  const m = await hmac(h, enc.encode('agentjarvis-sas-v1'));
  const v = new DataView(m.buffer, m.byteOffset, 4).getUint32(0, false);
  return String(v % 1_000_000).padStart(6, '0');
}

/** JSON app message → padded plaintext (len u16 ‖ json ‖ zeros, total a multiple of 256). */
export function padJson(obj) {
  const j = enc.encode(JSON.stringify(obj));
  if (j.length > MAX_JSON) throw new Error('message too large');
  const total = Math.ceil((j.length + 2) / PAD) * PAD;
  const out = new Uint8Array(total);
  new DataView(out.buffer).setUint16(0, j.length, false);
  out.set(j, 2);
  return out;
}

export function unpadJson(pt) {
  if (pt.length < 2 || pt.length % PAD !== 0) throw new Error('bad padding');
  const n = new DataView(pt.buffer, pt.byteOffset, 2).getUint16(0, false);
  if (n > MAX_JSON || n > pt.length - 2) throw new Error('bad length');
  for (let i = 2 + n; i < pt.length; i++) if (pt[i] !== 0) throw new Error('bad padding');
  const obj = JSON.parse(dec.decode(pt.subarray(2, 2 + n)));
  if (!obj || typeof obj !== 'object' || Array.isArray(obj) || typeof obj.t !== 'string') throw new Error('bad message');
  return obj;
}

/** Parse the QR / link payload (`…#p=<b64url json>` or the bare b64url). Throws on anything malformed or expired.
 *  allowRelay(url) → bool lets a client pin the relay it will talk to (a pasted link must not move it to someone else's). */
export function parsePairing(input, nowSec = Math.floor(Date.now() / 1000), allowRelay = null) {
  let s = String(input).trim();
  const i = s.indexOf('#p=');
  if (i >= 0) s = s.slice(i + 3);
  const p = JSON.parse(new TextDecoder().decode(unb64u(s)));
  if (p.v !== 1) throw new Error('unsupported version');
  if (typeof p.r !== 'string' || !/^wss:\/\/[a-z0-9.-]+(:\d+)?$/.test(p.r) && !/^ws:\/\/127\.0\.0\.1:\d+$/.test(p.r)) throw new Error('bad relay url');
  if (allowRelay && !allowRelay(p.r)) throw new Error('relay not allowed');
  if (typeof p.c !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(p.c)) throw new Error('bad channel');
  const k = unb64u(p.k), id = unb64u(p.i), psk = unb64u(p.p);
  if (k.length !== 32 || id.length !== 16 || psk.length !== 32) throw new Error('bad key sizes');
  if (!Number.isInteger(p.x) || p.x < nowSec) throw new Error('pairing code expired');
  return { relay: p.r, channel: p.c, hostPub: k, pairingId: id, psk, expires: p.x };
}

export function frame(kind, ...parts) { return concat(Uint8Array.of(kind), ...parts); }

// ---------------------------------------------------------------- approvals (PROTOCOL §8). Mirrors host/jarvis_host/approvals.py.
export const APPROVE_CONTEXT = 'agentjarvis-approve-v1';
const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
/** hex SHA-256 of what the phone shows for a request: tool, newline, summary (UTF-8). */
export async function shownDigest(tool, summary) { return hex(await sha256(enc.encode(`${tool}\n${summary}`))); }
/** The exact bytes a device signs (Ed25519) to answer a request. decision: 'allow' | 'deny'. */
export async function approveMessage(channel, device, id, decision, tool, summary) {
  if (decision !== 'allow' && decision !== 'deny') throw new Error('bad decision');
  return enc.encode(`${APPROVE_CONTEXT}\n${channel}\n${device}\n${id}\n${decision}\n${await shownDigest(tool, summary)}`);
}
/** The device's Ed25519 approval key: private half non-extractable (it can sign, never leave WebCrypto); public half raw 32 B. */
export async function generateSigningKeypair() {
  const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']);
  return { priv: kp.privateKey, pub: new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey)) };
}
