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
export const QUOTES_MAX = 5;           // P286: one say quotes ≤ 5 places (reply_to + quote_more ≤ 4)
export const CAP_MQ = 'mq5';           // P286: the host takes quote_more
export const FRAG_MAX_N = 128;
export const FRAG_MAX_TOTAL = 2 * 1024 * 1024;
export const BLOB_CHUNK = 45056;         // raw bytes per blob_chunk → 60 075 base64url chars → one 60 160-byte padded frame
export const BLOB_MAX = 25 * 1024 * 1024;
export const ASR_MAX = 44 + 630 * 16000 * 2;   // a purpose:"asr" take: ten minutes + decoder safety margin, ≤ 630 s of 16 kHz mono PCM16 WAV
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
/** P296: an `agentj mail send` allow that also puts the ticked recipients on the mail allowlist: approveMessage('allow'), plus
 *  one more line hex(SHA-256("allow:" + the ticked addresses in the card's offered order, newline-joined)). Mirrors
 *  approvals.allow_add_line; the host checks the picks against what it offered. */
export async function mailAllowMessage(channel, device, id, tool, summary, picks) {
  if (!Array.isArray(picks) || !picks.length) throw new Error('no picks');
  return concat(await approveMessage(channel, device, id, 'allow', tool, summary), enc.encode(`\n${hex(await sha256(enc.encode('allow:' + picks.join('\n'))))}`));
}
// ---------------------------------------------------------------- phone controls (PROTOCOL §8). Mirrors host/agentj/controls.py.
export const CONTROL_CONTEXT = 'agentjarvis-control-v1';
export const CONTROL_ACTIONS = ['heartbeat_write', 'security_begin','security_verify','security_retire', 'host_write', 'mem_rm', 'mem_undo', 'estop', 'resume', 'task_on', 'task_off',
  'fr_set', 'pg_set', 'pg_del', 'fr_add', 'fr_discoverable', 'fr_card',          // §17.7 agent friends (0.16)
  'fr_ctx', 'update', 'bots_write', 'setup_mark', 'pair_new',               // P153: pair_new = a paired remote opens a pairing (§3.3)
  'dev_name', 'dev_unbind', 'dev_add', 'dev_confirm',                     // P171 (§24): the phone's 遥控器管理 page                                                         // P115: first-run checklist card                                                                       // P73: a friend's 「补充设定」
  'agent_name',                                                             // P181 (§22.2): rename the Agent from the header
  'cards_get',                                                              // P203 (§29.1): the signed open-cards snapshot (a read)
  'mail_allow',                                                             // P207 (§25): add an address to the mail allowlist
  'reset', 'rescue_do',                                                    // P332 (§33): 紧急修复 ① reset connection · ② support help
  'approval_set',                                                           // P336: approval level / goal without the opening card
  'cmd_run',                                                                // P361 (§34): Settings 「执行」 — a whitelisted command name
  'term_open', 'term_line', 'term_sudo', 'term_forget',                    // P408 (§38): the web terminal
  'elev_stop', 'elev_watch',                                                // P406 (§11.2): Stop / show a running sudo command's output
  'vault_share',                                                           // P414 (§38.11): let this remote use the stored password
  'fr_gsay',                                                                // P426 #170 (§17.7): a message in 「我的设备」
  'fr_report_reply', 'fr_report_remark', 'files_write', 'skill_archive', 'skill_restore', 'wechat',   // P426 #181-3 (§39): 「归档」/「恢复」 on the 技能 page
  'seat_transfer', 'seat_transfer_cancel',                                  // P547: Settings → 转移席位 (object {} → "transfer" / "cancel")
  'forward_set',                                                            // P554: 转发去向, one host setting for every remote
  'task_migrate'];                                                          // P570c: 定时任务 → 重新迁移 (id \n tsha, like task_on)
/** The text whose SHA-256 a control signature covers (the target, with the content hash the phone saw). */
export function controlObject(action, o) {
  if (action === 'fr_report_reply') return `${o.resend === true ? 'resend\n' : ''}${o.id}\n${o.text}`;
  if (action === 'fr_report_remark') return `${o.friend}\n${o.name}`;
  if (action === 'host_write' || action === 'heartbeat_write') return canonicalJson(o);
  if (action === 'bots_write') return canonicalJson(o.request);
  if (action === 'update') return 'latest';
  if (action === 'cards_get') return 'cards';
  if (action === 'pair_new') return 'start';
  if (action === 'setup_mark') return `${o.item}\n${o.choice}\n${o.rev}`;
  if (['security_begin','security_verify','security_retire'].includes(action)) return `${o.epoch}\n${[...o.keep].sort().join('\n')}`;
  if (action === 'dev_name') return `${o.device}\n${o.name}`;
  if (action === 'agent_name') return String(o.name);
  if (action === 'mail_allow') return `${o.addr}\n${o.role}`;   // P207: address as sent \n owner | guest
  if (action === 'reset') return String(o.what);                 // P332 (§33): object {"what":"connection"} → "connection"
  if (action === 'rescue_do') return String(o.action);           // P332 (§33): object {"action":<action>} → the action name
  if (action === 'cmd_run') return String(o.cmd);                // P361 (§34): object {"cmd":<name>} → the name
  if (action === 'term_open') return String(o.mode);                // P408 (§38): object {"mode":"line"} → "line"
  if (action === 'term_line') return `${o.id}\n${o.line}`;         // P408: terminal id \n the line (a confirmed dangerous line)
  if (action === 'term_sudo') return `${o.id}\n${o.rid}`;          // P408: terminal id \n the host's sudo request id
  if (action === 'term_forget') return 'forget';                   // P408: object {} (no Face ID)
  if (action === 'elev_stop') return String(o.id);                 // P406 (§11.2): the running sudo card's id
  if (action === 'elev_watch') return `${o.id}\n${o.on === true ? 'on' : 'off'}`;   // P406 F1: card id \n on | off
  if (action === 'vault_share') return 'share';                    // P414 (§37.11): object {}
  if (action === 'seat_transfer') return 'transfer';               // P547: object {} (Face ID: the host asks for it)
  if (action === 'seat_transfer_cancel') return 'cancel';          // P547: object {} (only narrows: no Face ID)
  if (action === 'forward_set') return `${o.dest}${o.migrate === true ? '\nmigrate' : ''}`;   // P554: share | wx | tg [\n migrate]
  if (action === 'files_write') return `${o.op}\n${o.path}\n${o.expires}`;
  if (action === 'skill_archive' || action === 'skill_restore') return `${o.op}\n${o.name}`;   // P426 #181-3 (§39): op \n name
  if (action === 'approval_set') return `${o.key}\n${typeof o.value === 'boolean' ? (o.value ? 'true' : 'false') : o.value}`;   // P336
  if (action === 'dev_unbind') return String(o.device);
  if (action === 'dev_add') return o.rv ? `rv\n${o.rv}` : 'new';   // P190 (§27): a reverse pairing names the scanned code's channel
  if (action === 'dev_confirm') return `${o.device}\n${o.code}`;
  if (action === 'mem_rm') return `${o.src}\n${o.file}\n${o.fsha}\n${o.iid}`;
  if (action === 'mem_undo') return String(o.id);
  if (action === 'estop' || action === 'resume') return 'all';
  if (action === 'task_on' || action === 'task_off' || action === 'task_migrate') return `${o.id}\n${o.tsha}`;
  if (action === 'fr_set') return `${o.friend}\n${o.op}\n${o.value ?? ''}`;
  if (action === 'pg_set') return canonicalJson(o.group);
  if (action === 'pg_del') return String(o.id);
  if (action === 'fr_add') return `${o.id}\n${o.note ?? ''}`;
  if (action === 'fr_discoverable') return o.on ? 'on' : 'off';
  if (action === 'fr_card') return `${o.owner ?? ''}\n${o.intro ?? ''}`;
  if (action === 'fr_ctx') return `${o.friend}\n${o.text ?? ''}`;
  if (action === 'fr_gsay') return `devices\n${o.text}`;          // P426 #170: the group is fixed, the text exactly as sent
  if (action === 'wechat') return `${o.op}\n${o.id}\n${o.code}`;
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
    if (typeof p === 'string') return !!p.trim() && !textProblem(p, 2000);
    if (!Array.isArray(p) || !p.length || !p.every(Number.isInteger)) return false;
    for (let i = 1; i < p.length; i++) if (p[i] <= p[i - 1]) return false;
    return p[0] >= 1 && p[p.length - 1] <= q.o.length && (q.m || p.length === 1);
  });
}
export async function questionMessage(channel, device, id, action, qs, picks) {
  if (action !== 'answer' && action !== 'cancel') throw new Error('bad action');
  if ((action === 'cancel') !== (picks == null)) throw new Error('picks only with answer');
  if (action === 'answer' && !checkPicks(qs, picks)) throw new Error('bad picks');
  const signedPicks = picks == null ? '-' : (await Promise.all(picks.map(async p => typeof p === 'string' ? 't:' + await h256(p) : p.join(',')))).join(';');
  return enc.encode(`${QUESTION_CONTEXT}\n${channel}\n${device}\n${id}\n${action}\n${await questionDigest(qs)}\n${signedPicks}`);
}

/** §10.2 a `say` (validated like the host does; throws on anything the host would answer shape / too_long). */
export function sayMessage({ sid = newId22(), text = '', att, reply_to, excerpt, quote_more, ts = Date.now() } = {}) {
  const t = normalizeNewlines(text), ex = excerpt == null ? null : normalizeNewlines(excerpt);
  // P286 §10.6: quotes 2…5 after reply_to's — [{id[, ex]}]
  const more = quote_more == null ? null : quote_more.map((q) => (q.ex == null ? { id: q.id } : { id: q.id, ex: normalizeNewlines(q.ex) }));
  if (more && (reply_to == null || !more.length || more.length >= QUOTES_MAX || !more.every((q) => Number.isInteger(q.id)))) throw new Error('bad quote_more');
  const exs = [ex, ...(more || []).map((q) => q.ex ?? null)].filter((x) => x != null);
  const bad = textProblem(t) || exs.map((x) => textProblem(x, EXCERPT_MAX)).find(Boolean);
  if (bad) throw new Error(bad);
  if (t.length + exs.reduce((n, x) => n + x.length, 0) > MAX_TEXT_P33) throw new Error('too_long');
  if (ex != null && reply_to == null) throw new Error('excerpt only with reply_to');
  if (att != null && (!Array.isArray(att) || att.length > 10 || !att.every(isId22) || new Set(att).size !== att.length)) throw new Error('bad att');
  if (!t.trim() && !(att && att.length)) throw new Error('empty');
  const m = { t: 'say', sid, text: t, ts };
  if (att && att.length) m.att = att;
  if (reply_to != null) m.reply_to = reply_to;
  if (ex != null) m.excerpt = ex;
  if (more) m.quote_more = more;
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
  if (c.kind === 'secret') return [c.name, c.purpose, c.dest, c.verify || '', ...(c.auth ? ['kind','url','code','expires_at','round','rounds'].map(k => String(c.auth[k] ?? '')) : [])];
  if (c.kind === 'login') return [c.term];               // P408 (§38): the `r` of the pending term_open the password belongs to
  // P414 (M4, §38.11): the requesting remote's id, its passkey credential id, its pairing date (unix s), its flags (web, account)
  if (c.kind === 'vshare') return [c.dev, c.cred, String(c.paired), c.flags.join(',')];
  throw new Error('bad kind');
}
/** hex SHA-256 of `context\nkind\n` + one `sha256(field)` line per shown field. */
export async function elevateDigest(kind, fields) {
  if (!['sudo', 'secret', 'login', 'vshare'].includes(kind)) throw new Error('bad kind');
  const lines = [ELEVATE_CONTEXT, kind];
  for (const f of fields) lines.push(hex(await sha256(enc.encode(f))));
  return hex(await sha256(enc.encode(lines.join('\n'))));
}
/** The exact bytes a device signs for a card: decision allow | deny, the card's one-time nonce, ts (ms), the shown digest and
 *  hex SHA-256 of the sealed value ('-' for deny). `grant` (§11.1): the card's 10-minute class hash, only when the owner
 *  switched that toggle on — then one more line `grant:<hash>`. `save` (P414 M2, §37.10): hex SHA-256 of the `pct` a typed
 *  sudo answer adds to remember the password — then the last line `save:<hex>` (after any grant line). */
export function elevateMessage(channel, device, id, kind, decision, nonce, ts, digest, ctSha, grant, save) {
  if (decision !== 'allow' && decision !== 'deny') throw new Error('bad decision');
  if (!Number.isInteger(ts)) throw new Error('bad ts');
  const lines = [ELEVATE_CONTEXT, channel, device, id, kind, decision, nonce, String(ts), digest, ctSha || '-'];
  if (grant) lines.push('grant:' + grant);
  if (save) lines.push('save:' + save);
  return enc.encode(lines.join('\n'));
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
 *  line hex(SHA-256("group:" + group)), so the host knows the owner picked THAT group. peer_question allow may sign group:colleague; ctx stays friend_request-only. */
export const GROUP_ID_RE = /^[a-z0-9-]{1,32}$/;
/** Which of group / ctx a friend answer signs (null = no line): allow only; group on friend_request / peer_question; ctx on
 *  friend_request only. Shared by friendAnswerMessage and friendAnswer so the signed lines and the sent fields cannot part. */
function friendLines(tool, decision, group, ctx) {
  const on = ['friend_request', 'peer_question'].includes(tool) && decision === 'allow';
  const g = on && group != null && group !== '' ? group : null;
  if (g !== null && (!GROUP_ID_RE.test(g) || (tool === 'peer_question' && !['colleague', 'main'].includes(g)))) throw new Error('bad group');
  const c = on && tool === 'friend_request' && typeof ctx === 'string' && ctx !== '' ? ctx : null;
  if (c !== null && [...c].length > FRIEND_CTX_MAX) throw new Error('ctx too long');
  return { group: g, ctx: c };
}
export async function friendAnswerMessage(channel, device, id, decision, tool, summary, group, ctx) {
  let out = await approveMessage(channel, device, id, decision, tool, summary);
  const l = friendLines(tool, decision, group, ctx);
  if (l.group !== null) out = concat(out, enc.encode(`\n${hex(await sha256(enc.encode('group:' + l.group)))}`));
  // P73 (ADR-A176): 「同意」 may carry this friend's 「补充设定」 — one more line hex(SHA-256("ctx:" + ctx)), after the group line
  if (l.ctx !== null) out = concat(out, enc.encode(`\n${hex(await sha256(enc.encode('ctx:' + l.ctx)))}`));
  return out;
}
/** P424 (#152): the whole `answer` for a friend card (friend_request / peer_question), signed by `sign(bytes) → Uint8Array`.
 *  It sends group / ctx exactly when their line is signed and throws on a field that would travel unsigned — 0.24.0a1 sent
 *  peer_question `group:colleague` but signed without that line, so the host refused it on every device. `dg` = the first 16
 *  hex of the digest this phone signed: the host only logs whether it equals its own (diagnosis; never trusted). */
export async function friendAnswer(channel, device, card, ok, { group = null, ctx = null } = {}, sign) {
  const decision = ok === true ? 'allow' : 'deny';
  const l = friendLines(card.tool, decision, group, ctx);
  if ((group != null && group !== '' && l.group === null) || (typeof ctx === 'string' && ctx !== '' && l.ctx === null)) throw new Error('unsigned field');
  const msg = await friendAnswerMessage(channel, device, card.id, decision, card.tool, card.summary, l.group, l.ctx);
  const ans = { t: 'answer', id: card.id, ok: ok === true, sig: b64u(await sign(msg)), dg: (await shownDigest(card.tool, card.summary)).slice(0, 16) };
  if (l.group !== null) ans.group = l.group;
  if (l.ctx !== null) ans.ctx = l.ctx;
  return ans;
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

// ---------------------------------------------------------------- long tasks (0.18, ADR-A193). Mirrors host/agentj/capability.py.
export const PREFLIGHT_DOMAIN = 'agentj.preflight.v1';
export const BRIEF_DOMAIN = 'agentj.brief.v1';
/** The bytes a device signs to answer an opening card (submit / cancel): canonical JSON of every bound field. */
export function preflightBytes(channel, device, card, action, picks) {
  return enc.encode(canonicalJson({ domain: PREFLIGHT_DOMAIN, channel, device, card_id: card.card_id, task_id: card.task_id,
    revision: card.revision, brief_digest: card.brief_digest, registry_revision: card.registry_revision, nonce: card.nonce,
    expires_at: card.expires_at, action, picks: action === 'submit' ? (picks || {}) : {} }));
}
/** The bytes a device signs to confirm a task brief (a new workflow / a schedule): scope digest + the card's binding. */
export function briefBytes(channel, device, card, scopeDigest, action) {
  return enc.encode(canonicalJson({ domain: BRIEF_DOMAIN, channel, device, card_id: card.card_id, task_id: card.task_id,
    revision: card.revision, brief_digest: card.brief_digest, scope_digest: scopeDigest, nonce: card.nonce,
    expires_at: card.expires_at, action }));
}

// ---------------------------------------------------------------- P190 reverse pairing (PROTOCOL §27). The web client only.
// A new, unpaired browser shows `https://m.agentj.app/#j=` + b64url(0x01 ‖ channel(16) ‖ x25519 pub(32) ‖ nonce(16) ‖
// expiry u32 BE ‖ relay) — relay = "" for the default relay, else the relay URL without its scheme (a local ws test relay
// whole), as in the compact pairing link. `channel` is a throw-away rendezvous channel (channelId of a fresh Ed25519 key the page authenticates to the relay
// with, exactly like a host), not a computer. A paired remote that scans it gets a pairing link from its own computer (§24
// dev_add with `rv`) and hands it to that browser sealed to the code's x25519 key: only the browser that drew the code can
// open it. The relay sees one more channel and two frames of padded ciphertext.
export const RV_PREFIX = '#j=';
export const RV_LINK_BASE = 'https://m.agentj.app/#j=';
export const RV_TTL = 300;                     // seconds a code is shown before it is redrawn
export const RV_MAX_AHEAD = 600;               // a code claiming to live longer than this is forged
export const RV_OFFER = 0x10;                  // remote → new browser: 0x10 ‖ e_pub(32) ‖ iv(12) ‖ AES-GCM
export const RV_ANSWER = 0x11;                 // new browser → remote: 0x11 ‖ iv(12) ‖ AES-GCM
export const RV_MAX_FRAME = 2048;
const RV_FIXED = 1 + 16 + 32 + 16 + 4;
const RV_INFO = 'agentjarvis/rv/v1\n';

/** The code's link. relay: the wss / test ws URL the page waits on. */
export function reverseLink(channel, pub, nonce, expires, relay = PAIR_DEFAULT_RELAY, base = RV_LINK_BASE) {
  const ch = unb64u(channel);
  if (ch.length !== 16 || pub.length !== 32 || nonce.length !== 16) throw new Error('bad sizes');
  const WSS = PAIR_DEFAULT_RELAY.slice(0, 6);
  const r = relay === PAIR_DEFAULT_RELAY ? '' : relay.startsWith(WSS) ? relay.slice(6) : relay;
  const x = new Uint8Array(4);
  new DataView(x.buffer).setUint32(0, expires, false);
  return base + b64u(concat(Uint8Array.of(1), ch, pub, nonce, x, enc.encode(r)));
}
/** Parse a scanned / opened reverse code. Throws on anything malformed, expired or too far in the future (a forged code). */
export function parseReverse(input, nowSec = Math.floor(Date.now() / 1000), allowRelay = null) {
  const s = String(input).trim();
  const i = s.indexOf(RV_PREFIX);
  const body = i >= 0 ? s.slice(i + RV_PREFIX.length) : s;
  if (!/^[A-Za-z0-9_-]{92,200}$/.test(body)) throw new Error('bad reverse code');
  const b = unb64u(body);
  if (b.length < RV_FIXED || b[0] !== 1) throw new Error('unsupported version');
  const rest = dec.decode(b.slice(RV_FIXED));
  const relay = rest === '' ? PAIR_DEFAULT_RELAY : rest.startsWith('ws:') ? rest : PAIR_DEFAULT_RELAY.slice(0, 6) + rest;
  if (!/^wss:\/\/[a-z0-9.-]+(:\d+)?$/.test(relay) && !/^ws:\/\/127\.0\.0\.1:\d+$/.test(relay)) throw new Error('bad relay url');
  if (allowRelay && !allowRelay(relay)) throw new Error('relay not allowed');
  const expires = new DataView(b.buffer, b.byteOffset + 65, 4).getUint32(0, false);
  if (expires < nowSec) throw new Error('code expired');
  if (expires > nowSec + RV_MAX_AHEAD) throw new Error('bad expiry');
  return { channel: b64u(b.slice(1, 17)), pub: b.slice(17, 49), nonce: b.slice(49, 65), expires, relay };
}
/** What a scanned text is: 'reverse' (§27) · 'pair' (a computer's §4 link) · 'friend' (§17.1 share link) · 'data'. */
export function classifyScan(text) {
  const s = String(text ?? '');
  if (/^https:\/\/m\.agentj\.app\/(?:\?[^#]*)?#j=/.test(s) || /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/(?:\?[^#]*)?#j=/.test(s)) return 'reverse';
  // P545: a pairing link may open the seat's own page (m.agentj.app/<seat id>/#p=…)
  if (/^https:\/\/m\.agentj\.app\/(?:[a-z2-7]{8}\/)?(?:\?[^#]*)?#p=/.test(s) || /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/(?:[a-z2-7]{8}\/)?(?:\?[^#]*)?#p=/.test(s)) return 'pair';
  if (s.startsWith(FRIEND_LINK_BASE)) return 'friend';
  return 'data';
}
async function rvKey(priv, peerPub, channel, nonce) {
  const k = await crypto.subtle.importKey('raw', peerPub, { name: 'X25519' }, false, []);
  const ikm = await crypto.subtle.deriveBits({ name: 'X25519', public: k }, priv, 256);
  const base = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: nonce, info: enc.encode(RV_INFO + channel) }, base,
    { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}
const rvAad = (what, channel) => enc.encode(`agentjarvis/rv/${what}\n${channel}`);
/** The scanning remote: seal the pairing link to the code's key. → { frame, key } (key opens the answer). */
export async function rvOffer(code, link) {
  const e = await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
  const ePub = new Uint8Array(await crypto.subtle.exportKey('raw', e.publicKey));
  const key = await rvKey(e.privateKey, code.pub, code.channel, code.nonce);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const pt = padJson({ t: 'rv', v: 1, n: b64u(code.nonce), l: link }, 1536);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: rvAad('offer', code.channel) }, key, pt));
  return { frame: concat(Uint8Array.of(RV_OFFER), ePub, iv, ct), key };
}
/** The new browser: open an offer with its code's private key. → { link, key } or throws (not for this code). */
export async function rvOpenOffer(frameBytes, priv, channel, nonce) {
  if (frameBytes.length > RV_MAX_FRAME || frameBytes.length < 1 + 32 + 12 + 16 || frameBytes[0] !== RV_OFFER) throw new Error('bad offer');
  const key = await rvKey(priv, frameBytes.slice(1, 33), channel, nonce);
  const pt = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: frameBytes.slice(33, 45), additionalData: rvAad('offer', channel) },
    key, frameBytes.slice(45)));
  const m = unpadJson(pt, 1536);
  if (m.t !== 'rv' || m.v !== 1 || m.n !== b64u(nonce) || typeof m.l !== 'string' || m.l.length > 1200) throw new Error('bad offer');
  return { link: m.l, key };
}
/** Either side: the new browser's answer ({ok:true} | {ok:false, why}). */
export async function rvAnswer(key, channel, obj) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: rvAad('answer', channel) }, key,
    padJson({ t: 'rv_ok', ok: obj.ok === true, ...(obj.why ? { why: String(obj.why).slice(0, 16) } : {}) }, 256)));
  return concat(Uint8Array.of(RV_ANSWER), iv, ct);
}
export async function rvOpenAnswer(frameBytes, key, channel) {
  if (frameBytes.length > RV_MAX_FRAME || frameBytes.length < 1 + 12 + 16 || frameBytes[0] !== RV_ANSWER) throw new Error('bad answer');
  const pt = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: frameBytes.slice(1, 13), additionalData: rvAad('answer', channel) },
    key, frameBytes.slice(13)));
  const m = unpadJson(pt, 256);
  if (m.t !== 'rv_ok') throw new Error('bad answer');
  return { ok: m.ok === true, why: typeof m.why === 'string' ? m.why : null };
}
/** Relay host auth (§2) for the rendezvous channel: the bytes the page's throw-away Ed25519 key signs. */
export const relayAuthMessage = (channel, n) => enc.encode(`agentjarvis-relay-auth-v1\n${channel}\n${n}`);

/** P498: exact host challenge, signed in an independent domain; never rebuild from a task's display name. */
export function workflowResumeMessage(o) {
  const keys = ['action','channel','device','expires','id','issued','nonce','run_id','run_sha','tsha'];
  if (!o || typeof o !== 'object' || Object.keys(o).sort().join(',') !== keys.join(',') || o.action !== 'wf_resume'
      || !Number.isSafeInteger(o.issued) || !Number.isSafeInteger(o.expires) || o.expires - o.issued !== 120
      || !['channel','device','id','nonce','run_id','run_sha','tsha'].every(k => typeof o[k] === 'string' && o[k].length > 0 && o[k].length <= 256)) throw new Error('shape');
  return enc.encode('agentjarvis-wf-resume-v1\n' + canonicalJson(o));
}
