// agentj wire helpers shared by the web client and the Node tests (spec: PROTOCOL.md). Mirrors host/agentj/wire.py.
import { concat, sha256, hmac } from './noise.js';

const enc = new TextEncoder();
const dec = new TextDecoder('utf-8', { fatal: true });

export const KIND = { PAIR_INIT: 1, RESUME_INIT: 2, HS_RESP: 3, DATA: 4 };
export const PAD = 256;
export const MAX_JSON = 16 * 1024;
export const MAX_TEXT = 4000;            // UTF-16 code units (String.length), the same count the host uses
// §10.0 (PROMPT-33): once BOTH ends announced capability "p33" (device hello `caps`, host approved / ready `caps`)
export const CAP_P33 = 'p33';
export const MAX_JSON_P33 = 60 * 1024;   // padded plaintext ≤ 61 696, relay payload ≤ 61 713 < 65 536
export const MAX_TEXT_P33 = 20000;       // UTF-16 code units per message; say text + excerpt together
export const EXCERPT_MAX = 2000;
export const FRAG_MAX_N = 128;
export const FRAG_MAX_TOTAL = 2 * 1024 * 1024;
export const BLOB_CHUNK = 45056;         // raw bytes per blob_chunk → 60 075 base64url chars → one 60 160-byte padded frame
export const BLOB_MAX = 25 * 1024 * 1024;
export const ASR_MAX = 44 + 120 * 16000 * 2;   // a purpose:"asr" take: ≤ 120 s of 16 kHz mono PCM16 WAV
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

/** JSON app message → padded plaintext (len u16 ‖ json ‖ zeros, total a multiple of 256).
 *  maxJson: MAX_JSON, or MAX_JSON_P33 once both ends announced p33 (PROTOCOL §10.0). */
export function padJson(obj, maxJson = MAX_JSON) {
  const j = enc.encode(JSON.stringify(obj));
  if (j.length > maxJson) throw new Error('message too large');
  const total = Math.ceil((j.length + 2) / PAD) * PAD;
  const out = new Uint8Array(total);
  new DataView(out.buffer).setUint16(0, j.length, false);
  out.set(j, 2);
  return out;
}

export function unpadJson(pt, maxJson = MAX_JSON) {
  if (pt.length < 2 || pt.length % PAD !== 0) throw new Error('bad padding');
  const n = new DataView(pt.buffer, pt.byteOffset, 2).getUint16(0, false);
  if (n > maxJson || n > pt.length - 2) throw new Error('bad length');
  for (let i = 2 + n; i < pt.length; i++) if (pt[i] !== 0) throw new Error('bad padding');
  const obj = JSON.parse(dec.decode(pt.subarray(2, 2 + n)));
  if (!obj || typeof obj !== 'object' || Array.isArray(obj) || typeof obj.t !== 'string') throw new Error('bad message');
  return obj;
}

/** Parse the QR / link payload (`…#p=<b64url json>` or the bare b64url). Throws on anything malformed or expired.
 *  allowRelay(url) → bool lets a client pin the relay it will talk to (a pasted link must not move it to someone else's). */
// ADR-A177 (F29): the compact pairing link. `#p=` + decimal digits of the big-endian number made of
// 0x02 ‖ channel(16) ‖ host_x25519_pub(32) ‖ pairing_id(16) ‖ psk(32) ‖ expiry(u32) ‖ relay — relay = "" for the default relay,
// else the wss URL without its scheme (a local ws test relay stays whole). Digits go into a QR numeric segment (3.3 bits each, vs 8 for
// a byte of base64url-inside-JSON-inside-base64url), so the QR drops from version 13 (69 modules) to 8 (49). The JSON v1 link
// (b64url, always starts "eyJ") still parses: pairing links from not-yet-updated hosts keep working.
export const PAIR_DEFAULT_RELAY = 'wss://relay.agentj.app';
const PAIR_V2_FIXED = 1 + 16 + 32 + 16 + 32 + 4;
function unpackPairingV2(digits) {
  if (digits.length > 400) throw new Error('bad pairing link');
  let h = BigInt(digits).toString(16);
  if (h.length % 2) h = '0' + h;
  const b = Uint8Array.from(h.match(/../g) || [], (x) => parseInt(x, 16));
  if (b.length < PAIR_V2_FIXED || b[0] !== 2) throw new Error('unsupported version');
  const rest = dec.decode(b.slice(PAIR_V2_FIXED));
  const r = rest === '' ? PAIR_DEFAULT_RELAY : rest.startsWith('ws:') ? rest : PAIR_DEFAULT_RELAY.slice(0, 6) + rest;
  const x = new DataView(b.buffer, b.byteOffset + 97, 4).getUint32(0, false);
  return { v: 1, r, c: b64u(b.slice(1, 17)), k: b64u(b.slice(17, 49)), i: b64u(b.slice(49, 65)), p: b64u(b.slice(65, 97)), x };
}

export function parsePairing(input, nowSec = Math.floor(Date.now() / 1000), allowRelay = null) {
  let s = String(input).trim();
  const i = s.indexOf('#p=');
  if (i >= 0) s = s.slice(i + 3);
  const p = /^[0-9]+$/.test(s) ? unpackPairingV2(s) : JSON.parse(new TextDecoder().decode(unb64u(s)));
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

// ---------------------------------------------------------------- approvals (PROTOCOL §8). Mirrors host/agentj/approvals.py.
export const APPROVE_CONTEXT = 'agentjarvis-approve-v1';
const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
/** hex SHA-256 of what the phone shows for a request: tool, newline, summary (UTF-8). */
export async function shownDigest(tool, summary) { return hex(await sha256(enc.encode(`${tool}\n${summary}`))); }
/** The exact bytes a device signs (Ed25519) to answer a request. decision: 'allow' | 'deny' | 'allow_batch'.
 *  allow_batch (batch approval of the same low-risk kind for the rest of the turn) also signs hex SHA-256 of the scope text
 *  exactly as shown, on one more line, so the host can tell the human agreed to THAT scope and no wider one. */
export async function approveMessage(channel, device, id, decision, tool, summary, scope) {
  if (decision !== 'allow' && decision !== 'deny' && decision !== 'allow_batch') throw new Error('bad decision');
  if ((decision === 'allow_batch') !== (typeof scope === 'string' && scope.length > 0)) throw new Error('scope only with allow_batch');
  const base = `${APPROVE_CONTEXT}\n${channel}\n${device}\n${id}\n${decision}\n${await shownDigest(tool, summary)}`;
  return enc.encode(decision === 'allow_batch' ? `${base}\n${hex(await sha256(enc.encode(scope)))}` : base);
}
// ---------------------------------------------------------------- phone controls (PROTOCOL §8). Mirrors host/agentj/controls.py.
export const CONTROL_CONTEXT = 'agentjarvis-control-v1';
export const CONTROL_ACTIONS = ['mem_rm', 'mem_undo', 'estop', 'resume', 'task_on', 'task_off',
  'fr_set', 'pg_set', 'pg_del', 'fr_add', 'fr_discoverable', 'fr_card',          // §17.7 agent friends (0.16)
  'fr_ctx'];                                                                       // P73: a friend's 「补充设定」
/** The text whose SHA-256 a control signature covers (the target, with the content hash the phone saw). */
export function controlObject(action, o) {
  if (action === 'mem_rm') return `${o.src}\n${o.file}\n${o.fsha}\n${o.iid}`;
  if (action === 'mem_undo') return String(o.id);
  if (action === 'estop' || action === 'resume') return 'all';
  if (action === 'task_on' || action === 'task_off') return `${o.id}\n${o.tsha}`;
  if (action === 'fr_set') return `${o.friend}\n${o.op}\n${o.value ?? ''}`;
  if (action === 'pg_set') return canonicalJson(o.group);
  if (action === 'pg_del') return String(o.id);
  if (action === 'fr_add') return `${o.id}\n${o.note ?? ''}`;
  if (action === 'fr_discoverable') return o.on ? 'on' : 'off';
  if (action === 'fr_card') return `${o.owner ?? ''}\n${o.intro ?? ''}`;
  if (action === 'fr_ctx') return `${o.friend}\n${o.text ?? ''}`;
  throw new Error('bad action');
}
/** The exact bytes a device signs for a write command: delete / undo a memory item, stop everything, resume, enable / disable a
 *  scheduled task. nonce = 32 hex, ts = ms since the epoch (the host accepts ±120 s, each nonce once). */
export async function controlMessage(channel, device, action, nonce, ts, o) {
  if (!CONTROL_ACTIONS.includes(action)) throw new Error('bad action');
  if (!/^[0-9a-f]{32}$/.test(nonce) || !Number.isInteger(ts)) throw new Error('bad nonce / ts');
  const d = hex(await sha256(enc.encode(controlObject(action, o))));
  return enc.encode(`${CONTROL_CONTEXT}\n${channel}\n${device}\n${action}\n${nonce}\n${ts}\n${d}`);
}
/** The device's Ed25519 approval key: private half non-extractable (it can sign, never leave WebCrypto); public half raw 32 B. */
export async function generateSigningKeypair() {
  const kp = await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']);
  return { priv: kp.privateKey, pub: new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey)) };
}

// ---------------------------------------------------------------- relay parity (PROTOCOL §10, PROMPT-33). Mirrors host/agentj/wire.py.
/** UTF-16 code units (what String.length counts). */
export const units = (s) => s.length;
const LONE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
const C0 = /[\u0000-\u0008\u000B-\u001F]/;
/** §10.0 text rule: null when valid, else 'shape' (not a string, a lone surrogate, a C0 control other than tab / newline —
 *  CR included: a sender turns CR LF / CR into LF first, see normalizeNewlines) or 'too_long' (> limit UTF-16 units). */
export function textProblem(s, limit = MAX_TEXT_P33) {
  if (typeof s !== 'string' || LONE.test(s) || C0.test(s)) return 'shape';
  return s.length > limit ? 'too_long' : null;
}
export const normalizeNewlines = (s) => s.replace(/\r\n?/g, '\n');
const ID22 = /^[A-Za-z0-9_-]{22}$/;
const RID = /^[A-Za-z0-9_-]{1,32}$/;
export const isId22 = (v) => typeof v === 'string' && ID22.test(v);
export const isRid = (v) => typeof v === 'string' && RID.test(v);
/** A device-chosen id (`sid` / `bid`): 128 random bits as 22 base64url characters. */
export const newId22 = () => b64u(crypto.getRandomValues(new Uint8Array(16)));

/** §10.1 the receiver side of `frag`: feed every app message; get back the message to handle (a reassembled one, or the
 *  message itself), or null while a frag is open / after a broken one (one open `f` at a time, ≤ 128 slices, ≤ 2 MiB). */
export class Defrag {
  constructor() { this.f = null; }
  feed(m) {
    if (m.t !== 'frag') { this.f = null; return m; }
    const { f, i, n, d } = m;
    if (typeof f !== 'string' || !Number.isInteger(i) || !Number.isInteger(n) || typeof d !== 'string' || !(i >= 0 && i < n && n <= FRAG_MAX_N)) { this.f = null; return null; }
    if (i === 0) { this.f = f; this.n = n; this.parts = []; this.size = 0; }
    if (this.f !== f || this.n !== n || this.parts.length !== i) { this.f = null; return null; }
    this.parts.push(d);
    this.size += enc.encode(d).length;
    if (this.size > FRAG_MAX_TOTAL) { this.f = null; return null; }
    if (this.parts.length < n) return null;
    this.f = null;
    let inner;
    try { inner = JSON.parse(this.parts.join('')); } catch { return null; }
    if (!inner || typeof inner !== 'object' || Array.isArray(inner) || typeof inner.t !== 'string' || inner.t === 'frag') return null;
    return inner;
  }
}
/** §10.1 the sender side (the host's; here for tests and stand-in hosts): [obj] when it fits, else N `frag` messages. */
export function fragSplit(obj, fid, maxJson = MAX_JSON_P33) {
  const text = JSON.stringify(obj);
  const total = enc.encode(text).length;
  if (total <= maxJson) return [obj];
  if (total > FRAG_MAX_TOTAL) throw new Error('message too large even for frag');
  const budget = maxJson - 120, slices = [];
  let cur = '', size = 0;
  for (const ch of text) {
    const n = enc.encode(JSON.stringify(ch)).length - 2;
    if (size + n > budget) { slices.push(cur); cur = ''; size = 0; }
    cur += ch; size += n;
  }
  if (cur) slices.push(cur);
  if (slices.length > FRAG_MAX_N) throw new Error('message too large even for frag');
  return slices.map((d, i) => ({ t: 'frag', f: fid, i, n: slices.length, d }));
}

/** §10.7 questions: what an answer (or a cancel) signs. qs = the card's [{q, h, m, o: [{l, d}]}] exactly as shown. */
export const QUESTION_CONTEXT = 'agentjarvis-question-v1';
const h256 = async (s) => hex(await sha256(enc.encode(s)));
export async function questionDigest(qs) {
  const lines = [];
  for (const q of qs) {
    lines.push(`q ${await h256(q.h ?? '')} ${await h256(q.q)} ${q.m ? 'm' : 's'}`);
    for (const o of q.o) lines.push(`o ${await h256(o.l)} ${await h256(o.d ?? '')}`);
  }
  return h256(lines.join('\n'));
}
/** picks: [[1,3],[2]] (1-based, ascending; exactly one for a single-choice question) → "1,3;2"; null (cancel) → "-". */
export const picksText = (picks) => (picks == null ? '-' : picks.map((p) => p.join(',')).join(';'));
export function checkPicks(qs, picks) {
  if (!Array.isArray(picks) || picks.length !== qs.length) return false;
  return qs.every((q, k) => {
    const p = picks[k];
    if (!Array.isArray(p) || !p.length || !p.every(Number.isInteger)) return false;
    for (let i = 1; i < p.length; i++) if (p[i] <= p[i - 1]) return false;
    return p[0] >= 1 && p[p.length - 1] <= q.o.length && (q.m || p.length === 1);
  });
}
export async function questionMessage(channel, device, id, action, qs, picks) {
  if (action !== 'answer' && action !== 'cancel') throw new Error('bad action');
  if ((action === 'cancel') !== (picks == null)) throw new Error('picks only with answer');
  if (action === 'answer' && !checkPicks(qs, picks)) throw new Error('bad picks');
  return enc.encode(`${QUESTION_CONTEXT}\n${channel}\n${device}\n${id}\n${action}\n${await questionDigest(qs)}\n${picksText(picks)}`);
}

/** §10.2 a `say` (validated like the host does; throws on anything the host would answer shape / too_long). */
export function sayMessage({ sid = newId22(), text = '', att, reply_to, excerpt, ts = Date.now() } = {}) {
  const t = normalizeNewlines(text), ex = excerpt == null ? null : normalizeNewlines(excerpt);
  const bad = textProblem(t) || (ex != null ? textProblem(ex, EXCERPT_MAX) : null);
  if (bad) throw new Error(bad);
  if (t.length + (ex ? ex.length : 0) > MAX_TEXT_P33) throw new Error('too_long');
  if (ex != null && reply_to == null) throw new Error('excerpt only with reply_to');
  if (att != null && (!Array.isArray(att) || att.length > 10 || !att.every(isId22) || new Set(att).size !== att.length)) throw new Error('bad att');
  if (!t.trim() && !(att && att.length)) throw new Error('empty');
  const m = { t: 'say', sid, text: t, ts };
  if (att && att.length) m.att = att;
  if (reply_to != null) m.reply_to = reply_to;
  if (ex != null) m.excerpt = ex;
  return m;
}
/** §10.3 blob messages. */
export const blobOpen = ({ bid = newId22(), purpose = 'att', name, mime, size, sha256: s, origin = 'file', secs }) =>
  ({ t: 'blob_open', bid, purpose, name, mime, size, sha256: s, origin, ...(secs != null ? { secs } : {}) });
export const blobChunk = (bid, o, bytes) => ({ t: 'blob_chunk', bid, o, d: b64u(bytes) });
export const blobEnd = (bid) => ({ t: 'blob_end', bid });
export const blobDrop = (bid) => ({ t: 'blob_drop', bid });
export async function sha256hex(bytes) { return hex(await sha256(bytes)); }
/** 16 kHz mono PCM16 little-endian WAV (44-byte header) from samples in [-1, 1] — what purpose:"asr" accepts (§10.9). */
export function wav16k(samples) {
  const n = samples.length, out = new Uint8Array(44 + 2 * n), v = new DataView(out.buffer);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) out[o + i] = s.charCodeAt(i); };
  str(0, 'RIFF'); v.setUint32(4, 36 + 2 * n, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, 'data'); v.setUint32(40, 2 * n, true);
  for (let i = 0; i < n; i++) { const x = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + 2 * i, x < 0 ? x * 0x8000 : x * 0x7fff, true); }
  return out;
}
/** §10.5 / §10.11 / §10.12 requests (answered to the asking session only). */
export const histGet = (r, { before, after, limit } = {}) => ({ t: 'hist_get', r, ...(before != null ? { before } : {}), ...(after != null ? { after } : {}), ...(limit != null ? { limit } : {}) });
export const modelSet = (r, { model = null, effort = null, def = false } = {}) => (def ? { t: 'model_set', r, default: true } : { t: 'model_set', r, model, effort });
export const menuGet = (r) => ({ t: 'menu_get', r });

// ---------------------------------------------------------------- sudo / secret cards (PROTOCOL §11, F17). Mirrors host/agentj/elevate.py.
export const ELEVATE_CONTEXT = 'agentjarvis-elevate-v1';
export const SEAL_CONTEXT = 'agentjarvis-seal-v1';
/** The texts the card shows, in the order the host hashes them. sudo: cmd, why, effect · secret: name, purpose, dest, verify. */
export function elevateFields(c) {
  if (c.kind === 'sudo') return [c.cmd, c.why, c.effect || ''];
  if (c.kind === 'secret') return [c.name, c.purpose, c.dest, c.verify || ''];
  throw new Error('bad kind');
}
/** hex SHA-256 of `context\nkind\n` + one `sha256(field)` line per shown field. */
export async function elevateDigest(kind, fields) {
  if (kind !== 'sudo' && kind !== 'secret') throw new Error('bad kind');
  const lines = [ELEVATE_CONTEXT, kind];
  for (const f of fields) lines.push(hex(await sha256(enc.encode(f))));
  return hex(await sha256(enc.encode(lines.join('\n'))));
}
/** The exact bytes a device signs for a card: decision allow | deny, the card's one-time nonce, ts (ms), the shown digest and
 *  hex SHA-256 of the sealed value ('-' for deny). */
export function elevateMessage(channel, device, id, kind, decision, nonce, ts, digest, ctSha) {
  if (decision !== 'allow' && decision !== 'deny') throw new Error('bad decision');
  if (!Number.isInteger(ts)) throw new Error('bad ts');
  return enc.encode([ELEVATE_CONTEXT, channel, device, id, kind, decision, nonce, String(ts), digest, ctSha || '-'].join('\n'));
}
/** Seal a password / secret to the card's one-time host key: ephemeral X25519, HKDF-SHA256(salt = host_epk ‖ epk,
 *  info = SEAL_CONTEXT), AES-256-GCM with AAD = SEAL_CONTEXT\nchannel\ndevice\nid\nkind\nnonce\ndigest. → { epk, ct = iv ‖
 *  ciphertext, ctSha }. The caller zeroes `value` afterwards. */
export async function sealValue(hostEpk, value, channel, device, id, kind, nonce, digest) {
  const subtle = crypto.subtle;
  const kp = await subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
  const epk = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  const hk = await subtle.importKey('raw', hostEpk, { name: 'X25519' }, false, []);
  const shared = new Uint8Array(await subtle.deriveBits({ name: 'X25519', public: hk }, kp.privateKey, 256));
  const ikm = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  shared.fill(0);
  const key = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: concat(hostEpk, epk), info: enc.encode(SEAL_CONTEXT) },
    ikm, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const aad = enc.encode([SEAL_CONTEXT, channel, device, id, kind, nonce, digest].join('\n'));
  const ct = concat(iv, new Uint8Array(await subtle.encrypt({ name: 'AES-GCM', iv, additionalData: aad }, key, value)));
  return { epk, ct, ctSha: hex(await sha256(ct)) };
}

// ---------------------------------------------------------------- agent friends (PROTOCOL §17, 0.16). Mirrors host/agentj/peer.py.
/** Sorted keys, no spaces, non-ASCII as is (Python: json.dumps(o, sort_keys=True, separators=(',', ':'), ensure_ascii=False)). */
export function canonicalJson(o) {
  if (Array.isArray(o)) return `[${o.map(canonicalJson).join(',')}]`;
  if (o && typeof o === 'object') return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canonicalJson(o[k])}`).join(',')}}`;
  return JSON.stringify(o === undefined ? null : o);
}
/** §17.7 the bytes a device signs to answer a friend_request card: approveMessage, plus — for allow with a group — one more
 *  line hex(SHA-256("group:" + group)), so the host knows the owner picked THAT group. Every other ask = approveMessage. */
export const GROUP_ID_RE = /^[a-z0-9-]{1,32}$/;
export async function friendAnswerMessage(channel, device, id, decision, tool, summary, group, ctx) {
  const base = await approveMessage(channel, device, id, decision, tool, summary);
  if (tool !== 'friend_request' || decision !== 'allow') return base;
  let out = base;
  if (group != null && group !== '') {
    if (!GROUP_ID_RE.test(group)) throw new Error('bad group');
    out = concat(out, enc.encode(`\n${hex(await sha256(enc.encode('group:' + group)))}`));
  }
  // P73 (ADR-A176): 「同意」 may carry this friend's 「补充设定」 — one more line hex(SHA-256("ctx:" + ctx)), after the group line
  if (typeof ctx === 'string' && ctx !== '') {
    if ([...ctx].length > FRIEND_CTX_MAX) throw new Error('ctx too long');
    out = concat(out, enc.encode(`\n${hex(await sha256(enc.encode('ctx:' + ctx)))}`));
  }
  return out;
}
/** P73: the longest 「补充设定」 for one friend, in characters (Unicode code points; the host counts the same way). */
export const FRIEND_CTX_MAX = 4000;
/** §17.1 Agent ID: `AJ-` + 15 Crockford base32 characters of the first 75 bits of h + 1 check character, 4 groups of 4. */
export const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
const checkChar = (v) => CROCKFORD[v.reduce((n, x, i) => n + (i + 1) * x, 0) % 31];
const showId = (s16) => `AJ-${s16.slice(0, 4)}-${s16.slice(4, 8)}-${s16.slice(8, 12)}-${s16.slice(12)}`;
/** Whatever a person typed or pasted → the canonical `AJ-XXXX-XXXX-XXXX-XXXX`, or null (wrong length / letter / check). */
export function parseAgentId(input) {
  if (typeof input !== 'string' || input.length > 64) return null;
  let s = input.replace(/[\s-]/g, '').toUpperCase();
  if (s.length === 18 && s.startsWith('AJ')) s = s.slice(2);
  s = s.replace(/[IL]/g, '1').replace(/O/g, '0');
  if (s.length !== 16) return null;
  const v = [...s].map((c) => CROCKFORD.indexOf(c));
  if (v.some((x) => x < 0) || checkChar(v.slice(0, 15)) !== s[15]) return null;
  return showId(s);
}
/** id_raw (10 bytes: id75 << 5) of a canonical or typed ID; null when invalid. */
export function agentIdRaw(id) {
  const p = parseAgentId(id);
  if (!p) return null;
  let n = 0n;
  for (const c of p.replace(/^AJ-|-/g, '').slice(0, 15)) n = (n << 5n) | BigInt(CROCKFORD.indexOf(c));
  n <<= 5n;
  const out = new Uint8Array(10);
  for (let i = 9; i >= 0; i--) { out[i] = Number(n & 255n); n >>= 8n; }
  return out;
}
/** The Agent ID of a peer key pair (x25519 public ‖ ed25519 public, 32 bytes each). */
export async function agentIdOf(x25519Pub, ed25519Pub) {
  const h = await sha256(concat(enc.encode('agentj/peer-id/v1\n'), x25519Pub, ed25519Pub));
  let n = 0n;
  for (const b of h.slice(0, 10)) n = (n << 8n) | BigInt(b);
  n >>= 5n;                                           // 80 → 75 bits
  const v = [];
  for (let i = 14; i >= 0; i--) v.push(Number((n >> BigInt(5 * i)) & 31n));
  return showId(v.map((x) => CROCKFORD[x]).join('') + checkChar(v));
}
/** mbox = b64url(SHA-256("agentj/mbox/v1\n" ‖ id_raw)[0:16]) — what the relay sees instead of the ID. */
export async function mboxOfId(id) {
  const raw = agentIdRaw(id);
  if (!raw) throw new Error('bad id');
  return b64u((await sha256(concat(enc.encode('agentj/mbox/v1\n'), raw))).slice(0, 16));
}
/** The share link (fragment only: never reaches a server). */
export const FRIEND_LINK_BASE = 'https://m.agentj.app/friends#add=';
export const friendLink = (id) => FRIEND_LINK_BASE + parseAgentId(id);
