// Offline queue on the phone (0.15.2, PROTOCOL §14). A message sent while the page is not connected — no network,
// still connecting, or the computer away — is not refused: it waits here, sealed in IndexedDB (record "outbox", the same
// AES-256-GCM `local` key as the drafts, §10.14), and goes out by itself once the session is `ready` again: strictly in
// order, one `say` at a time, each waiting for its `say_res`. No wire change.
// Dedupe rests on one fact: the message's `sid` is chosen when it is queued and never changes. A say whose say_res was
// lost (the connection dropped after the host took it) is sent again with the SAME sid, and the host answers `dup`
// (compose.Sends: every sid a device used, newest 1 024 per device, for the life of the host process) — `dup` means "it
// already arrived", so it simply leaves the queue. What that does not cover is written in PROTOCOL §14.
// Bound to one computer: the sealed record carries the paired computer (channel + host key); a record for another
// computer is deleted unread, a drain never runs on a session to another computer, and unpair / re-pair / revoke delete
// the record with the drafts (store.wipeLocal) and empty the memory copy (wipe()).
import { getSealed, putSealed, dbGet, dbDel } from './store.js';
import { b64u } from '../proto/wire.js';

export const MAX = 50;              // queued messages; the 51st send is refused with one line
export const RETRY_MS = 5000;       // too_many (16 of this phone's sends still wait for the Agent) / no say_res in 30 s
const SID_RE = /^[A-Za-z0-9_-]{22}$/;
const TEXT_MAX = 20000;

/** What one say_res means for the queue: sent (ok, or dup = it had already arrived) · later (keep it first, retry in
 *  RETRY_MS) · stop (the connection went: keep everything, the next `ready` drains) · drop (refused for good). */
export function verdict(r) {
  if (r && r.ok === true) return 'sent';
  const w = r && r.why;
  if (w === 'dup') return 'sent';
  if (w === 'too_many' || w === 'att_open' || w === 'timeout') return 'later';
  if (w === 'offline' || w === 'lost') return 'stop';
  return 'drop';
}

/** The computer this phone is paired with, as session.hostId() names it (channel + ":" + host key), or null. */
export async function pairedHost() {
  const h = await dbGet('host');
  return h && h.approved && h.channel && h.hostPub ? h.channel + ':' + b64u(new Uint8Array(h.hostPub)) : null;
}

function valid(e) {
  if (!e || !SID_RE.test(e.sid) || typeof e.text !== 'string' || e.text.length > TEXT_MAX || !Number.isInteger(e.ts)) return false;
  if (e.rt != null && !(Number.isInteger(e.rt.id) && (e.rt.excerpt == null || typeof e.rt.excerpt === 'string'))) return false;
  if (e.att != null && !(Array.isArray(e.att) && e.att.length <= 10 && e.att.every((a) => a && typeof a.name === 'string'))) return false;
  return !!e.text.trim() || !!(e.att && e.att.length);
}

export class Outbox {
  /** io: {load, save, del, hostKey} (defaults: the sealed IndexedDB record "outbox" and the paired computer);
   *  h: {say(e) → say_res-like, live() → may send now, hostNow() → the computer of the ready session, done(e, r, kind),
   *  changed()} — relay.js fills it in; the unit tests pass fakes. */
  constructor(io = {}, h = {}) {
    this.io = { load: () => getSealed('outbox'), save: (v) => putSealed("outbox", v), del: () => dbDel('outbox'), hostKey: pairedHost, ...io };
    this.h = { say: async () => ({ ok: false, why: 'offline' }), live: () => false, hostNow: () => null, done: () => {}, changed: () => {}, ...h };
    this.items = [];                // {sid, text, ts, rt: {id, excerpt} | null, att: [{name, kind}] | null}, oldest first
    this.host = undefined;          // undefined = not known yet; null = not paired (nothing is ever sent)
    this.epoch = 0;                 // bumped by wipe(): nothing started before it writes or sends after it
    this.ready = Promise.resolve();
    this.wq = Promise.resolve();    // writes in call order, each one the state at its turn (the last write wins)
    this.busy = null; this.cut = null; this.timer = 0;
  }
  /** Load the sealed queue (a page reload / an app kill comes back to the same pending messages). */
  open() {
    const ep = this.epoch;
    return (this.ready = (async () => {
      let host = null, rec = null;
      try { host = await this.io.hostKey(); } catch { /* no storage: not paired */ }
      try { rec = await this.io.load(); } catch { /* unreadable = nothing queued */ }
      if (ep !== this.epoch) return;
      const kept = rec && rec.v === 1 && host && rec.host === host && Array.isArray(rec.items) ? rec.items.filter(valid) : [];
      const early = this.items;     // sent in the moment before the record was read: they come after it
      this.host = host;
      this.items = kept.concat(early).slice(0, MAX);
      if ((rec && !kept.length) || early.length) this.persist();   // a queue for another computer is never kept
      this.h.changed();
    })());
  }
  /** Queue one message (its sid already chosen). → "ok" | "full". Written to IndexedDB before anything is sent. */
  add(e) {
    if (this.items.length >= MAX) return 'full';
    this.items.push(e);
    this.persist();
    this.h.changed();
    return 'ok';
  }
  persist() {
    const ep = this.epoch;
    const p = this.wq.then(async () => {
      await this.bind();
      if (ep !== this.epoch) return;
      if (!this.items.length || !this.host) await this.io.del();
      else await this.io.save({ v: 1, host: this.host, items: this.items.slice() });
    });
    this.wq = p.catch(() => {});
    return this.wq;
  }
  /** After a wipe (a new pairing) the computer is read again lazily, from the host record written after it. */
  async bind() {
    await this.ready;
    if (this.host === undefined) {
      const ep = this.epoch, h = await this.io.hostKey().catch(() => null);
      if (ep === this.epoch && this.host === undefined) this.host = h;
    }
  }
  /** Send what waits, oldest first, one say at a time. Single-flight; safe to call on every `ready`. */
  drain() {
    if (this.busy) return this.busy;
    return (this.busy = this.run().finally(() => { this.busy = null; }));
  }
  async run() {
    const ep = this.epoch;
    await this.bind();
    clearTimeout(this.timer);
    while (this.items.length) {
      if (ep !== this.epoch) return 'wiped';
      // never to another computer: the ready session must be the one this queue was written for
      if (!this.host || !this.h.live() || this.h.hostNow() !== this.host) return 'stop';
      const e = this.items[0];
      let r;
      try {
        r = await Promise.race([this.h.say(e), new Promise((res) => { this.cut = () => res({ ok: false, why: 'lost' }); })]);
      } catch { r = { ok: false, why: 'other' }; }
      this.cut = null;
      if (ep !== this.epoch) return 'wiped';
      const k = verdict(r);
      if (k === 'stop') return 'stop';
      if (k === 'later') { this.later(); return 'later'; }
      if (this.items[0] === e) this.items.shift();
      await this.persist();
      if (ep !== this.epoch) return 'wiped';
      this.h.done(e, r, k);
      this.h.changed();
    }
    return 'empty';
  }
  later() { clearTimeout(this.timer); this.timer = setTimeout(() => this.drain(), RETRY_MS); }
  /** The connection went: the say in flight stops waiting (it stays first in the queue; the next ready sends it again
   *  with the same sid — `dup` if it had arrived). */
  lost() { if (this.cut) this.cut(); }
  /** Unpair / re-pair / revoke: forget everything queued (store.wipeLocal deletes the record itself). */
  wipe() {
    this.epoch++;
    this.lost();
    clearTimeout(this.timer);
    this.items = [];
    this.host = undefined;
    this.ready = Promise.resolve();
    this.h.changed();
  }
}

/** The pending bubbles above the field: each queued message on one line (textContent only), a note on one whose files
 *  were lost in a reload, and the one line 「网络恢复后自动发送」 while not connected. */
export function paint(box, items, { line, lostNote, isLost, showLine }) {
  if (!box) return;
  const out = [];
  for (const e of items) {
    const d = document.createElement('div');
    d.className = 'obi'; d.setAttribute('role', 'listitem'); d.dataset.sid = e.sid;
    const s = document.createElement('span');
    s.className = 'obt';
    s.textContent = e.text.replace(/\s+/g, ' ').trim() || (e.att || []).map((a) => a.name).join(', ');
    d.append(s);
    if (e.att && e.att.length) d.dataset.att = String(e.att.length);
    if (isLost(e)) {
      d.dataset.lost = '1';
      const n = document.createElement('span');
      n.className = 'obn'; n.textContent = lostNote;
      d.append(n);
    }
    out.push(d);
  }
  if (showLine) {
    const l = document.createElement('div');
    l.className = 'obl'; l.textContent = line;
    out.push(l);
  }
  box.replaceChildren(...out);
  box.hidden = !out.length;
}
