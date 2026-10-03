// Files, photos and voice, end to end (PROTOCOL §10.3): blob_open → blob_ack {next} → blob_chunk {o, d} … → blob_end →
// blob_done. The bytes travel only inside Noise transport messages, 45 056 raw bytes (base64url) per chunk, ≤ 8 chunks
// unacknowledged, paced by session.js. SHA-256 + size are checked by the host at blob_end; a mismatch retries once from 0.
// A partial upload survives a reconnect: the same blob_open (same bid / size / sha256 / mime) resumes at the host's next.
// Bound (P33-X01 / X02): an open upload sends only on the session generation its blob_open went on (a reset loses the
// window → 'net' → resumed after `ready` by a fresh blob_open), and only to the computer it was first opened to; unpair,
// re-pair and revoke call forgetAll(): every upload is dropped and its bytes released, nothing is re-sent anywhere.
import { sendApp, isReady, gen, hostId } from './session.js';
import { b64u } from '../proto/wire.js';

export const CHUNK = 45056;
export const WINDOW = 8;
export const MAX_BYTES = 26214400;          // 25 MiB
export const ASR_MAX_BYTES = 3840044;        // 120 s of 16 kHz mono PCM16 + header
export const MAX_ATT = 10;
export const MAX_OPEN = 2;                   // ≤ 2 open uploads per device (host limit)
const STALL_MS = 45000;

/** MIME allowlist (= relay inbox.ALLOWED; the host stores the extension from the MIME, never from the name). */
export const TYPES = {
  'image/jpeg': '.jpg .jpeg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif',
  'image/heic': '.heic', 'image/heif': '.heif', 'application/pdf': '.pdf', 'text/plain': '.txt',
  'text/markdown': '.md .markdown', 'text/csv': '.csv', 'application/json': '.json',
  'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mpeg': '.mp3', 'audio/mp4': '.m4a',
  'audio/aac': '.aac', 'audio/wav': '.wav', 'audio/x-wav': '.wav' };

export function newId() {
  return b64u(crypto.getRandomValues(new Uint8Array(16)));          // 128 bits → 22 base64url characters
}
const hexOf = (b) => Array.from(new Uint8Array(b), (x) => x.toString(16).padStart(2, '0')).join('');

const live = new Map();                      // bid → Upload
const queue = [];                            // uploads waiting for one of the MAX_OPEN slots

export class Upload {
  /** opts: {purpose:'att'|'asr', origin, name, mime, secs, onProgress(frac)} */
  constructor(file, opts) {
    this.file = file; this.opts = opts;
    this.bid = newId();
    this.size = file.size; this.mime = opts.mime || file.type || 'application/octet-stream';
    this.name = String(opts.name || file.name || 'upload').slice(0, 128);
    this.state = 'idle';                     // idle | queued | open | sending | ending | done | failed | held | dropped
    this.next = 0; this.sent = 0; this.retried = false;
    this.result = null; this.waiters = [];
    this.buf = null; this.sha = null;
    this.host = null; this.g = null;         // the computer (first blob_open) and the session generation it sends on
  }
  /** → Promise of {ok:true, kind, bytes} | {ok:false, why} (why '' = held/dropped by the page). */
  start() {
    if (this.state === 'done') return Promise.resolve(this.result);
    if (this.state === 'dropped' || !this.file && !this.buf) return Promise.resolve({ ok: false, why: '' });
    const p = new Promise((res) => this.waiters.push(res));
    if (['idle', 'held', 'failed'].includes(this.state)) {
      this.result = null; this.state = 'queued';
      queue.push(this); pump();
    }
    return p;
  }
  finish(r) {
    this.result = r; clearTimeout(this.stall);
    if (r.ok) this.state = 'done';
    else if (this.state !== 'held' && this.state !== 'dropped') this.state = 'failed';
    live.delete(this.bid);
    const ws = this.waiters; this.waiters = [];
    for (const w of ws) w(r);
    pump();
  }
  /** Stop sending but keep the host's partial (a cancelled send; resumed by the next start()). */
  hold() {
    if (this.state === 'done' || this.state === 'dropped') return;
    const i = queue.indexOf(this); if (i >= 0) queue.splice(i, 1);
    this.state = 'held';
    this.finish({ ok: false, why: '' });
  }
  /** Unpair / re-pair / revoke: gone without a word to any computer; its bytes are released. */
  forget() {
    const i = queue.indexOf(this); if (i >= 0) queue.splice(i, 1);
    this.state = 'dropped';
    this.buf = null; this.file = null;
    this.finish({ ok: false, why: '' });
  }
  /** The tray's ×: the host deletes it (also an open upload). */
  drop() {
    const i = queue.indexOf(this); if (i >= 0) queue.splice(i, 1);
    const was = this.state;
    this.state = 'dropped';
    if (was !== 'idle' && isReady() && this.host === hostId()) sendApp({ t: 'blob_drop', bid: this.bid }).catch(() => {});
    this.finish({ ok: false, why: '' });
  }
  async open() {
    this.state = 'open';
    live.set(this.bid, this);
    this.host ??= hostId();
    this.g = gen();
    if (!this.g || this.host !== hostId()) return this.finish({ ok: false, why: 'net' });
    try {
      if (!this.buf) {
        this.buf = new Uint8Array(await this.file.arrayBuffer());
        if (this.buf.length !== this.size) { this.size = this.buf.length; }
        this.sha = hexOf(await crypto.subtle.digest('SHA-256', this.buf));
      }
    } catch { return this.finish({ ok: false, why: 'read' }); }
    if (this.state !== 'open') return;
    if (this.g !== gen()) return this.netLost();     // reset while the file was read
    if (!this.size) return this.finish({ ok: false, why: 'empty' });
    const m = { t: 'blob_open', bid: this.bid, purpose: this.opts.purpose || 'att', name: this.name, mime: this.mime,
      size: this.size, sha256: this.sha, origin: this.opts.origin || 'file' };
    if (typeof this.opts.secs === 'number') m.secs = Math.round(this.opts.secs * 10) / 10;
    this.armStall();
    sendApp(m, this.g).catch(() => this.netLost());
  }
  armStall() { clearTimeout(this.stall); this.stall = setTimeout(() => { if (['open', 'sending', 'ending'].includes(this.state)) this.netLost(); }, STALL_MS); }
  netLost() { if (['open', 'sending', 'ending'].includes(this.state)) this.finish({ ok: false, why: 'net' }); }
  onAck(next) {
    if (!['open', 'sending', 'ending'].includes(this.state)) return;
    if (!Number.isInteger(next) || next < 0 || next > this.size) return;
    this.armStall();
    if (this.state === 'open' || next < this.next || next > this.sent) this.sent = next;   // first ack, or a resync
    this.next = next;
    this.opts.onProgress?.(this.size ? this.next / this.size : 0);
    if (this.state === 'ending') return;
    this.state = 'sending';
    this.pumpChunks();
  }
  pumpChunks() {
    while (this.state === 'sending' && this.sent < this.size && (this.sent - this.next) / CHUNK < WINDOW) {
      const o = this.sent, end = Math.min(this.size, o + CHUNK);
      this.sent = end;
      sendApp({ t: 'blob_chunk', bid: this.bid, o, d: b64u(this.buf.subarray(o, end)) }, this.g).catch(() => this.netLost());
    }
    if (this.state === 'sending' && this.sent >= this.size) {
      this.state = 'ending';
      sendApp({ t: 'blob_end', bid: this.bid }, this.g).catch(() => this.netLost());
    }
  }
  onDone(m) {
    if (m.ok === true) {
      this.opts.onProgress?.(1);
      if ((this.opts.purpose || 'att') === 'asr') { this.state = 'transcribing'; this.armStall(); return; }
      return this.finish({ ok: true, kind: m.kind, bytes: m.bytes });
    }
    return this.fail(m.why);
  }
  fail(why) {
    if (why === 'sha_mismatch' && !this.retried) {            // once from 0 (the host deleted the partial)
      this.retried = true; this.next = 0; this.sent = 0;
      this.state = 'open';
      return this.open();
    }
    this.finish({ ok: false, why: typeof why === 'string' ? why : 'unknown' });
  }
}

function pump() {
  const open = [...live.values()].filter((u) => ['open', 'sending', 'ending'].includes(u.state)).length;
  let slots = MAX_OPEN - open;
  while (slots > 0 && queue.length) {
    const u = queue.shift();
    if (u.state !== 'queued') continue;
    if (!isReady()) { u.finish({ ok: false, why: 'net' }); continue; }
    if (u.host && u.host !== hostId()) { u.forget(); continue; }
    slots--; u.open();
  }
}

/** Route a host → device blob message (blob_ack / blob_err / blob_done / asr_res). → true when it was one. */
export function handle(m) {
  if (!['blob_ack', 'blob_err', 'blob_done', 'asr_res'].includes(m.t) || typeof m.bid !== 'string') return false;
  const u = live.get(m.bid);
  if (!u) return true;
  if (m.t === 'blob_ack') u.onAck(m.next);
  else if (m.t === 'blob_err') { if (m.why !== 'dropped') u.fail(m.why); }
  else if (m.t === 'blob_done') u.onDone(m);
  else if (m.t === 'asr_res') u.finish(m.ok === true ? { ok: true, text: typeof m.text === 'string' ? m.text : '', engine: m.engine } : { ok: false, why: m.why || 'unknown' });
  return true;
}
/** After a reconnect: an upload still waiting for acks of the OLD session (its window went with that socket — e.g. the
 *  host restarted while chunks were in flight) is lost now, not after the 45 s stall; the page resumes it with the same
 *  blob_open (same bid → the host's next). `list`: uploads that already failed with 'net' to start again. */
export function onReconnect(list) {
  for (const u of [...live.values()]) u.netLost();
  for (const u of list) if (u && u.state === 'failed') u.start();
}
export const liveCount = () => live.size;
/** Unpair / re-pair / revoke (P33-X02): every upload, open or waiting, is dropped here and never sent again. */
export function forgetAll() {
  const all = new Set([...live.values(), ...queue]);
  queue.length = 0;
  for (const u of all) u.forget();
}
