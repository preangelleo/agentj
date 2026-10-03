// The end-to-end session (PROTOCOL §2–§4, §8, §10.0 / §10.1 / §10.13). One live session at a time.
// phase: wait-host → hs → (pair) approval | (resume) ready-wait → ready. Frames are handled strictly in order (async
// crypto must not interleave); transport sends are serialized (CipherState nonces = socket order) and paced to the
// relay's per-socket budget. Everything the page sends or receives travels inside Noise transport messages.
// Crypto generations (P33-X01): every completed handshake opens a new generation {send, live, chain}; a reset (host down,
// close, a new session) ends it. A send is bound to the generation it was queued on and re-checks it after pacing and
// after encryption, so nothing queued before a reset can ever be encrypted under — or reach the socket of — the next
// session; `hello` is always the first message of a generation; app messages go only on a `ready` generation.
import { IK, IKPSK2, Handshake } from '../proto/noise.js';
import { KIND, MAX_JSON, MAX_JSON_P33, MAX_TEXT_P33, padJson, unpadJson, Defrag, pairPrologue, resumePrologue, safetyCode, frame, b64u } from '../proto/wire.js';
import { deviceKey, signKey } from './store.js';

const enc = new TextEncoder();
const EMPTY = new Uint8Array(0);

// §10.0: once both ends announced "p33", app-message JSON ≤ 61 440 bytes (60 KiB) and text ≤ 20 000 code units.
// Padding (padJson / unpadJson) and frag reassembly (Defrag, §10.1: one open id, i = 0 … N−1, N fixed, ≤ 2 MiB, any other
// message drops the partial) are protocol/wire.js's — the same code the host's tests run against.
export const P33_JSON = MAX_JSON_P33;
export const P33_TEXT = MAX_TEXT_P33;
export const OLD_TEXT = 4000;

class ProtocolError extends Error {}

// ---------------------------------------------------------------- pacing (§10.3 / §10.13)
// ≤ 50 frames per 10 s from this socket, or — once the relay said {"t":"rate","n":240,"w":10} — ≤ 220 per 10 s and
// ≤ 22 per second (headroom under the relay's 240 / 10 s, so a burst never costs the socket).
export class Pacer {
  constructor(now = () => performance.now()) { this.now = now; this.sent = []; this.boost = false; }
  limits() { return this.boost ? { w: 10000, n: 220, s: 22 } : { w: 10000, n: 50, s: 50 }; }
  /** ms to wait before the next frame may go (0 = now). */
  wait() {
    const t = this.now(), L = this.limits();
    while (this.sent.length && t - this.sent[0] >= L.w) this.sent.shift();
    let w = 0;
    if (this.sent.length >= L.n) w = this.sent[this.sent.length - L.n] + L.w - t;
    const lastSec = this.sent.filter((x) => t - x < 1000);
    if (lastSec.length >= L.s) w = Math.max(w, lastSec[lastSec.length - L.s] + 1000 - t);
    return Math.max(0, Math.ceil(w));
  }
  mark() { this.sent.push(this.now()); }
}

// ---------------------------------------------------------------- the session
let H = null;                                   // hooks from app.js
let sess = null;
let reconnectTimer = null;
let backoff = 1000;
export const peer = { p33: false, asr: 'off', hist: 'off' };
export function configure(hooks) { H = hooks; }
export const current = () => sess;
export const isReady = () => !!sess && sess.phase === 'ready';
/** The current ready generation (null when not ready): a caller that must not send to a later session pins it. */
export const gen = () => (isReady() && sess.gen && sess.gen.live ? sess.gen : null);
export const channel = () => (sess ? sess.ctx.channel : null);
/** Which computer this session talks to (channel + host key); uploads are bound to it. */
export const hostId = () => (sess ? sess.ctx.channel + ':' + b64u(sess.ctx.hostPub) : null);
export const jsonMax = () => (peer.p33 ? P33_JSON : MAX_JSON);
export const textMax = () => (peer.p33 ? P33_TEXT : OLD_TEXT);

export function closeSession() {
  clearTimeout(reconnectTimer); reconnectTimer = null;
  const s = sess; sess = null;
  if (s) { endGen(s); s.closedByUs = true; try { s.ws.close(1000); } catch { /* already closed */ } }
}

export function openSession(mode, ctx) {
  closeSession();
  const ws = new WebSocket(ctx.relay + '/v1/dev/' + ctx.channel);
  ws.binaryType = 'arraybuffer';
  const s = { ws, mode, ctx, phase: 'wait-host', hs: null, gen: null, recv: null, chain: Promise.resolve(),
    closedByUs: false, frag: new Defrag(), pacer: new Pacer() };
  sess = s;
  H.setStatus('connecting', 'st.connecting');
  ws.onopen = () => { if (sess === s && s.phase === 'wait-host') H.setStatus('waiting-host', 'st.waitingHost'); };
  ws.onmessage = (ev) => { s.chain = s.chain.then(() => onFrame(s, ev.data)).catch((e) => protocolFail(s, e)); };
  ws.onclose = (ev) => { s.chain = s.chain.then(() => onClose(s, ev)); };
  ws.onerror = () => { /* a close event always follows */ };
  return s;
}

class StaleSend extends Error {}
function endGen(s) { if (s.gen) { s.gen.live = false; s.gen = null; } }
function resetCrypto(s) { s.hs = null; endGen(s); s.recv = null; s.frag = new Defrag(); }

async function onFrame(s, data) {
  if (s !== sess) return;
  if (typeof data === 'string') {                     // relay status; anything else from the relay is ignored
    let m; try { m = JSON.parse(data); } catch { return; }
    if (m && m.t === 'host' && typeof m.up === 'boolean') return m.up ? hostUp(s) : hostDown(s);
    if (m && m.t === 'rate' && m.n === 240 && m.w === 10) { s.pacer.boost = true; H.onRate?.(true); }
    return;
  }
  const b = new Uint8Array(data);
  if (b.length < 1) throw new ProtocolError('empty frame');
  const kind = b[0], body = b.subarray(1);
  if (kind === KIND.HS_RESP) {
    if (s.phase !== 'hs') throw new ProtocolError('unexpected HS_RESP');
    const payload = await s.hs.readMessage(body);
    if (payload.length !== 0) throw new ProtocolError('msg2 payload must be empty');
    const { send, recv, h } = await s.hs.split();
    endGen(s);
    s.hs = null; s.recv = recv;
    const g = s.gen = { send, live: true, chain: Promise.resolve() };
    // hello is the first thing on the new generation's chain: nothing else can be queued on it before this line
    if (s.mode === 'pair') {
      s.phase = 'approval';
      await sendOn(s, g, { t: 'hello', caps: ['p33'] }, MAX_JSON);
      H.onSas(await safetyCode(h));
    } else {
      s.phase = 'ready-wait';
      // the host replays what this page has not seen: §8 since (old hosts), §10.5 hist (p33 hosts)
      await sendOn(s, g, { t: 'hello', caps: ['p33'], ...H.helloExtra() }, MAX_JSON);
    }
    return;
  }
  if (kind === KIND.DATA) {
    if (!s.recv) throw new ProtocolError('DATA before handshake');
    const m = unpadJson(await s.recv.decrypt(EMPTY, body), P33_JSON);
    const whole = s.frag.feed(m);
    if (whole) return onApp(s, whole);
    return;
  }
  throw new ProtocolError('bad kind');
}

function readCaps(m) {
  peer.p33 = Array.isArray(m.caps) && m.caps.includes('p33');
  peer.asr = ['ready', 'not_installed', 'off', 'broken'].includes(m.asr) ? m.asr : (peer.p33 ? 'not_installed' : 'off');
  peer.hist = m.hist === 'on' ? 'on' : 'off';
}

async function onApp(s, m) {
  if (m.t === 'approved') {
    if (s.mode !== 'pair' || s.phase !== 'approval') throw new ProtocolError('unexpected approved');
    readCaps(m);
    const host = await H.onApproved(s.ctx);
    s.mode = 'resume'; s.ctx = host;                  // drop the PSK; a later host restart resumes on this socket
    return enterReady(s);
  }
  if (m.t === 'ready') {
    if (s.mode !== 'resume' || s.phase !== 'ready-wait') throw new ProtocolError('unexpected ready');
    readCaps(m);
    return enterReady(s);
  }
  if (s.phase !== 'ready') {
    if (m.t === 'msg') throw new ProtocolError('msg before ready');
    return;
  }
  if (m.t === 'msg' && (typeof m.text !== 'string' || m.text.length > textMax())) throw new ProtocolError('bad msg');
  // A bug in the page's own rendering must cost a screen update, never the session (it is not the peer's fault).
  try {
    if (typeof m.r === 'string' && pending.has(m.r)) return pending.get(m.r)(m);
    return await H.onApp(m);
  } catch (e) { H.onUiError?.(e, m.t); }
}

function enterReady(s) {
  s.phase = 'ready';
  backoff = 1000;
  H.onReady();
  if (document.visibilityState !== 'visible') sendApp({ t: 'vis', fg: false }).catch(() => {});
}

async function hostUp(s) {
  if (s.phase !== 'wait-host') return;                // duplicate "up"
  const dev = await deviceKey();
  let msg1;
  if (s.mode === 'pair') {
    const p = s.ctx;
    if (p.expires < Math.floor(Date.now() / 1000)) { closeSession(); return H.onPairFailed(); }
    s.hs = await new Handshake({ protocol: IKPSK2, initiator: true, prologue: pairPrologue(p.channel, p.pairingId), s: dev, rs: p.hostPub, psk: p.psk }).init();
    const sk = await signKey();
    const info = sk ? { v: 1, name: H.deviceLabel(), sk: b64u(sk.pub) } : { v: 1, name: H.deviceLabel() };
    msg1 = frame(KIND.PAIR_INIT, p.pairingId, await s.hs.writeMessage(enc.encode(JSON.stringify(info))));
    H.setStatus('pairing', 'st.pairing');
  } else {
    s.hs = await new Handshake({ protocol: IK, initiator: true, prologue: resumePrologue(s.ctx.channel), s: dev, rs: s.ctx.hostPub }).init();
    const sk = await signKey();                       // a device paired before L1 registers its approval key here (host keeps the first)
    msg1 = frame(KIND.RESUME_INIT, await s.hs.writeMessage(enc.encode(JSON.stringify(sk ? { v: 1, sk: b64u(sk.pub) } : { v: 1 }))));
    H.setStatus('connecting', 'st.resuming');
  }
  if (s !== sess || s.ws.readyState !== WebSocket.OPEN) return;
  s.phase = 'hs';
  s.ws.send(msg1);
  s.pacer.mark();
}

function hostDown(s) {
  if (s.mode === 'pair' && s.phase !== 'wait-host') {  // host restarted mid-pairing: the pairing is gone
    closeSession();
    return H.onPairFailed();
  }
  resetCrypto(s);                                     // the host lost all session state; redo RESUME on its next "up"
  s.phase = 'wait-host';
  H.onHostDown();
  H.setStatus('waiting-host', 'st.hostDown');
}

function onClose(s, ev) {
  if (s !== sess) return;                             // superseded or closed by us
  sess = null; endGen(s);
  if (ev.code === 4010) return H.onRevoked();
  if (s.mode === 'pair') return H.onPairFailed();
  // An approved device whose RESUME is answered by a close (not a network drop) is no longer on the allowlist.
  if ((s.phase === 'hs' || s.phase === 'ready-wait') && ev.code !== 1006) return H.onRevoked();
  H.onHostDown();
  scheduleReconnect();
}

function protocolFail(s, e) {
  if (s !== sess) return;
  const wasPair = s.mode === 'pair';
  closeSession();
  H.onProtocolFail(wasPair, e);
}

export function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  const wait = backoff;
  backoff = Math.min(backoff * 2, 30000);
  H.setStatus('connecting', 'st.retryIn', { n: Math.round(wait / 1000) });
  reconnectTimer = setTimeout(() => { reconnectTimer = null; H.resume(); }, wait);
}
export function reconnectNow() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; backoff = 1000; H.resume(); }
}

/** Send one app message on the ready session; resolves when it is on the socket. `g` pins a generation (gen()): when the
 *  session was reset or replaced since, the send is refused — it is never re-sent on its own (the caller re-offers it). */
export function sendApp(obj, g = gen()) {
  const s = sess;
  if (!s || !g || s.gen !== g || s.phase !== 'ready') return Promise.reject(new StaleSend('no session'));
  return sendOn(s, g, obj, jsonMax());
}
function sendOn(s, g, obj, max) {
  const alive = () => g.live && s.gen === g && sess === s;
  const p = g.chain.then(async () => {
    if (!alive()) throw new StaleSend('session reset');
    const pt = padJson(obj, max);
    for (let w = s.pacer.wait(); w > 0; w = s.pacer.wait()) {
      await new Promise((r) => setTimeout(r, w));
      if (!alive()) throw new StaleSend('session reset');
    }
    const ct = await g.send.encrypt(EMPTY, pt);     // g.send: this generation's CipherState, never a later one
    if (!alive()) throw new StaleSend('session reset');
    if (s.ws.readyState !== WebSocket.OPEN) throw new Error('socket closed');
    s.ws.send(frame(KIND.DATA, ct));
    s.pacer.mark();
  });
  g.chain = p.catch(() => {});
  return p;
}

// ---------------------------------------------------------------- requests with an id (r) echoed on every answer
const pending = new Map();
export function randHex(bytes) { return Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, '0')).join(''); }
/** obj + a fresh r → every answer carrying that r goes to onMsg until done(r). → r, or null when not ready. */
export function request(obj, onMsg, g = gen()) {
  if (!isReady() || !g) return null;
  const r = randHex(8);
  pending.set(r, onMsg);
  sendApp({ ...obj, r }, g).catch(() => pending.delete(r));
  return r;
}
export function done(r) { pending.delete(r); }
/** One request → the first answer of type `want` (or {t:'timeout'} / {t:'offline'}). */
export function ask1(obj, want, ms = 20000) {
  return new Promise((resolve) => {
    const r = request(obj, (m) => { if (m.t === want) { pending.delete(r); resolve(m); } });
    if (!r) { resolve({ t: 'offline' }); return; }
    setTimeout(() => { if (pending.delete(r)) resolve({ t: 'timeout' }); }, ms);
  });
}
export const pendingCount = () => pending.size;
