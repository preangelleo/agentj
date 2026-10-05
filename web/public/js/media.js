// Media out (PROTOCOL §13, F21, 0.15.2): files, pictures, audio, video, PDFs and generated HTML that the Agent's reply
// refers to, sent by the computer inside the Noise session — the reverse of blobs.js. A finished page carries `media`
// [{mid, name, mime, kind, bytes, sha256, ref}] and `media_skip` [{name, why}]; nothing is fetched from a path or a URL.
// Bytes are pulled lazily: media_get {mid, o} → ≤ 8 media_chunk {mid, o, d, last} (45 056 raw bytes each) per request, the
// next window asked for when one is in; media_err {mid, why}. Pictures ≤ 1 MiB load as soon as the page shows, bigger ones
// when they scroll into view, everything else on a tap. The SHA-256 of what arrived is checked against the page's before
// anything is shown. A small in-memory LRU (≤ 64 MiB of Blobs, never stored); object URLs belong to the page on screen and
// are revoked when another page is shown, and on unpair (forgetAll).
// Rendering (DOM nodes only): image → <img> from a Blob URL (tap = the viewer: pinch / double-tap zoom, Esc / × closes);
// audio → <audio controls>; video → <video controls playsinline>; pdf → open in a new tab (the phone's PDF viewer) /
// download / share; html → a preview in <iframe sandbox="" srcdoc> (an EMPTY sandbox: no scripts, no same-origin, no forms,
// no popups; the page's CSP is inherited by srcdoc and the iframe's own `csp` adds default-src 'none' with only data:
// pictures and inline styles — so no remote picture, stylesheet, font or anything else in the HTML ever loads, and inline
// <style> applies only where the page's own CSP allows it too); anything else → a file card with download and,
// where the phone can share files (iOS: 「存储到文件」), share. SVG only ever as <img> (scripts in an <img> never run).
import { sendApp, gen } from './session.js';
import { t } from './t.js';
import { human } from './ui.js';
import { unb64u } from '../proto/wire.js';

import { CHUNK, WINDOW, EAGER, LRU_MAX, MAX_ITEMS, CAPS, WHYS, HTML_CSP, sanitize, sandboxAttrs, normRef, matchSlot, blobType } from './mediawire.js';
export { CHUNK, WINDOW, EAGER, LRU_MAX, MAX_ITEMS, CAPS, WHYS, HTML_CSP, sanitize, sandboxAttrs, normRef, matchSlot, blobType };
const STALL_MS = 20000;
const PARALLEL = 2;                          // fetches at once (the computer allows 3 per session)
const MIB = 1024 * 1024;

// ---------------------------------------------------------------- fetching
const cache = new Map();                      // mid → Blob (LRU: Map order, newest last)
let cacheBytes = 0;
const flights = new Map();                    // mid → Flight
const waiting = [];                           // flights not yet started (≤ PARALLEL run at once)
const hexOf = (b) => Array.from(new Uint8Array(b), (x) => x.toString(16).padStart(2, '0')).join('');

function cachePut(mid, blob) {
  if (blob.size > LRU_MAX) return;
  cache.set(mid, blob); cacheBytes += blob.size;
  for (const [k, b] of cache) { if (cacheBytes <= LRU_MAX) break; cache.delete(k); cacheBytes -= b.size; }
}
class Flight {
  constructor(item) {
    this.item = item; this.parts = []; this.got = 0; this.win = 0; this.g = null; this.timer = 0; this.started = false;
    this.promise = new Promise((res, rej) => { this.res = res; this.rej = rej; });
  }
  start() { this.started = true; this.g = gen(); if (!this.g) return this.fail('net'); this.ask(); }
  ask() {
    this.win = this.got;
    this.arm();
    sendApp({ t: 'media_get', mid: this.item.mid, o: this.got }, this.g).catch(() => this.fail('net'));
  }
  arm() { clearTimeout(this.timer); this.timer = setTimeout(() => this.fail('net'), STALL_MS); }
  chunk(m) {
    if (m.o !== this.got || typeof m.d !== 'string') return;          // a stale window (an earlier request) — ignore
    let d;
    try { d = unb64u(m.d); } catch { return this.fail('shape'); }
    if (!d.length || d.length > CHUNK || this.got + d.length > this.item.bytes) return this.fail('size');
    this.parts.push(d); this.got += d.length; this.arm();
    if (m.last === true || this.got === this.item.bytes) return this.finish();
    if (this.got - this.win >= WINDOW * CHUNK) this.ask();
  }
  async finish() {
    clearTimeout(this.timer);
    if (this.got !== this.item.bytes) return this.fail('size');
    const all = new Uint8Array(this.got);
    let o = 0;
    for (const p of this.parts) { all.set(p, o); o += p.length; }
    this.parts = [];
    const sha = hexOf(await crypto.subtle.digest('SHA-256', all));
    if (sha !== this.item.sha256) return this.fail('sha');
    const blob = new Blob([all], { type: blobType(this.item) });
    cachePut(this.item.mid, blob);
    this.end();
    this.res(blob);
  }
  fail(why) { clearTimeout(this.timer); this.parts = []; this.end(); this.rej(Object.assign(new Error(why), { why })); }
  end() { if (flights.get(this.item.mid) === this) flights.delete(this.item.mid); pump(); }
}
function pump() {
  let running = [...flights.values()].filter((f) => f.started).length;
  while (running < PARALLEL && waiting.length) {
    const f = waiting.shift();
    if (flights.get(f.item.mid) !== f) continue;
    running++; f.start();
  }
}
/** → Promise<Blob> (verified), or a rejection with .why = 'gone' | 'sha' | 'net' | 'busy' | 'size' | 'shape'. */
export function fetchMedia(item) {
  const hit = cache.get(item.mid);
  if (hit) { cache.delete(item.mid); cache.set(item.mid, hit); return Promise.resolve(hit); }
  const f0 = flights.get(item.mid);
  if (f0) return f0.promise;
  const f = new Flight(item);
  flights.set(item.mid, f); waiting.push(f); pump();
  return f.promise;
}
/** Route a computer → phone media message. → true when it was one. */
export function handle(m) {
  if ((m.t !== 'media_chunk' && m.t !== 'media_err') || typeof m.mid !== 'string') return false;
  const f = flights.get(m.mid);
  if (!f || !f.started) return true;
  if (m.t === 'media_chunk') f.chunk(m);
  else f.fail(typeof m.why === 'string' ? m.why.slice(0, 16) : 'gone');
  return true;
}
/** Unpair / re-pair / revoke: every Blob and every fetch goes. */
export function forgetAll() {
  for (const f of [...flights.values()]) f.fail('net');
  waiting.length = 0; cache.clear(); cacheBytes = 0;
  clearShown();
}
export const cacheSize = () => cacheBytes;

// ---------------------------------------------------------------- rendering
let shown = { key: null, urls: [], io: null };
function clearShown() {
  for (const u of shown.urls) URL.revokeObjectURL(u);
  if (shown.io) shown.io.disconnect();
  shown = { key: null, urls: [], io: null };
  closeViewer();
}
const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const mb = (n) => (Math.round(n / MIB * 10) / 10).toFixed(1);

/** The note a refused file leaves on its page. */
export function skipText(s) { return t('media.skip.' + s.why, { name: s.name, mb: mb(s.bytes || 0) }); }

function strip(words) {
  let s = document.getElementById('mstrip');
  if (!s) { s = mk('div', 'mstrip'); s.id = 'mstrip'; s.hidden = true; }
  if (s.previousElementSibling !== words) words.after(s);
  return s;
}

/** After the page's words are on screen: fill its inline slots, the strip under the words and the skip notes.
 *  p = the page (snap.toPage: {id, media, mediaSkip}). Idempotent while the same page and list are shown. */
export function renderPage(words, p) {
  const box = strip(words);
  const items = (p && p.media) || [], skips = (p && p.mediaSkip) || [];
  const key = p && p.id !== null && (items.length || skips.length) ? p.id + '|' + items.map((m) => m.mid).join(',') + '|' + skips.length : null;
  const slots = [...words.querySelectorAll('.mslot')];
  const fresh = slots.some((s) => !s.dataset.mid);
  if (key === shown.key && (!key || !fresh)) return;
  clearShown();
  shown.key = key;
  box.replaceChildren();
  box.hidden = !key;
  if (!key) return;
  shown.io = typeof IntersectionObserver === 'function' ? new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) { shown.io.unobserve(e.target); e.target._load?.(); }
  }) : null;
  const used = new Set();
  for (const s of slots) {
    const m = matchSlot(s.dataset.ref, items, used);
    if (!m) continue;
    used.add(m.mid);
    s.dataset.mid = m.mid;
    s.replaceChildren(card(m, true));
  }
  for (const m of items) if (!used.has(m.mid)) box.appendChild(card(m, false));
  for (const sk of skips) box.appendChild(mk('div', 'mskip', skipText(sk)));
}

function card(m, inline) {
  const c = mk('div', 'mcard' + (inline ? ' inline' : ''));
  c.dataset.kind = m.kind; c.dataset.state = 'idle'; c.dataset.mid = m.mid;
  const body = mk('div', 'mbody');
  const meta = mk('div', 'mmeta');
  meta.append(mk('span', 'mname', m.name), mk('span', 'msize', human(m.bytes)));
  const acts = mk('div', 'macts');
  const status = mk('button', 'mtap', t(m.kind === 'html' ? 'media.preview' : 'media.tap'));
  status.type = 'button';
  body.appendChild(status);
  c.append(body, meta, acts);
  let busy = false;
  const load = async (open) => {
    if (busy || c.dataset.state === 'ready' && !open) return;
    if (c.dataset.state === 'ready') return opened(m, c, open);
    busy = true; c.dataset.state = 'loading'; status.textContent = t('media.loading'); status.disabled = true;
    try {
      const blob = await fetchMedia(m);
      if (!c.isConnected) return;
      show(m, c, body, acts, blob, open);
    } catch (e) {
      c.dataset.state = 'error'; status.disabled = e.why === 'gone' || e.why === 'sha';
      status.textContent = e.why === 'gone' ? t('media.expired') : e.why === 'sha' ? t('media.shaBad') : t('media.retry');
    } finally { busy = false; }
  };
  status.addEventListener('click', (e) => { e.stopPropagation(); load(true); });
  c._load = () => load(false);
  if (m.kind === 'image') {
    if (m.bytes <= EAGER || !shown.io) queueMicrotask(() => load(false));
    else shown.io.observe(c);
  }
  return c;
}

function urlOf(c, blob) {
  if (!c._url) { c._url = URL.createObjectURL(blob); shown.urls.push(c._url); }
  return c._url;
}
function show(m, c, body, acts, blob, open) {
  c._blob = blob;
  const url = urlOf(c, blob);
  c.dataset.state = 'ready';
  if (m.kind === 'image') {
    const img = mk('img'); img.alt = m.name; img.src = url; img.decoding = 'async';
    img.addEventListener('click', (e) => { e.stopPropagation(); viewImage(url, m.name); });
    body.replaceChildren(img);
  } else if (m.kind === 'audio' || m.kind === 'video') {
    const v = mk(m.kind); v.controls = true; v.preload = 'metadata'; v.src = url;
    if (m.kind === 'video') { v.playsInline = true; v.setAttribute('playsinline', ''); }
    body.replaceChildren(v);
    if (open) v.play?.().catch(() => {});
  } else {
    const tap = mk('button', 'mtap', t(m.kind === 'html' ? 'media.preview' : m.kind === 'pdf' ? 'media.open' : 'media.download'));
    tap.type = 'button';
    tap.addEventListener('click', (e) => { e.stopPropagation(); opened(m, c, true); });
    body.replaceChildren(tap);
  }
  actions(m, c, acts, blob, url);
  if (open && m.kind !== 'image' && m.kind !== 'audio' && m.kind !== 'video') opened(m, c, true);
}
function actions(m, c, acts, blob, url) {
  acts.replaceChildren();
  if (m.kind === 'pdf') {
    const a = mk('a', 'mact', t('media.open')); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    acts.appendChild(a);
  }
  const d = mk('a', 'mact', t('media.download')); d.href = url; d.download = m.name; d.rel = 'noopener';
  acts.appendChild(d);
  let file = null;
  try { file = new File([blob], m.name, { type: blob.type }); } catch { file = null; }
  if (file && navigator.canShare && navigator.share) {
    let can = false;
    try { can = navigator.canShare({ files: [file] }); } catch { can = false; }
    if (can) {
      const s = mk('button', 'mact', t('media.share')); s.type = 'button';
      s.addEventListener('click', (e) => { e.stopPropagation(); navigator.share({ files: [file], title: m.name }).catch(() => {}); });
      acts.appendChild(s);
    }
  }
}
function opened(m, c, open) {
  if (!open || !c._blob) return;
  if (m.kind === 'image') return viewImage(c._url, m.name);
  if (m.kind === 'html') return c._blob.arrayBuffer().then((b) => viewHtml(new TextDecoder().decode(b), m.name));
  if (m.kind === 'pdf') { c.querySelector('.macts a[target]')?.click(); return; }
  if (m.kind === 'file') { c.querySelector('.macts a[download]')?.click(); }
}

// ---------------------------------------------------------------- the viewer (pictures and the HTML preview)
let viewer = null;
function viewerEl() {
  if (viewer) return viewer;
  const v = mk('div', 'mview'); v.hidden = true;
  v.setAttribute('role', 'dialog'); v.setAttribute('aria-modal', 'true');
  const x = mk('button', 'mview-x', '×'); x.type = 'button';
  const stage = mk('div', 'mstage');
  const note = mk('div', 'mnote');
  v.append(stage, note, x);
  x.addEventListener('click', closeViewer);
  v.addEventListener('click', (e) => { if (e.target === v || e.target === stage) closeViewer(); });
  document.body.appendChild(v);
  viewer = { v, x, stage, note };
  return viewer;
}
function onKey(e) {
  if (e.key !== 'Escape') return;
  e.preventDefault(); e.stopImmediatePropagation();
  closeViewer();
}
function openViewer(label) {
  const V = viewerEl();
  V.v.setAttribute('aria-label', label || t('media.viewer'));
  V.x.setAttribute('aria-label', t('media.close'));
  V.v.hidden = false; V.v.dataset.modalOpen = '1';
  window.addEventListener('keydown', onKey, true);
  V.x.focus({ preventScroll: true });
  return V;
}
export function closeViewer() {
  if (!viewer || viewer.v.hidden) return;
  viewer.v.hidden = true; delete viewer.v.dataset.modalOpen;
  viewer.stage.replaceChildren(); viewer.note.textContent = ''; viewer.note.hidden = true;
  window.removeEventListener('keydown', onKey, true);
}
export const viewerOpen = () => !!viewer && !viewer.v.hidden;

function viewImage(url, name) {
  const V = openViewer(name);
  const img = mk('img', 'mzoom'); img.alt = name; img.src = url; img.draggable = false;
  V.stage.replaceChildren(img); V.note.hidden = true;
  zoomable(V.stage, img);
}
/** Two-finger pinch, one-finger pan when zoomed, double-tap / double-click toggles 2.5×. */
function zoomable(stage, img) {
  let s = 1, x = 0, y = 0;
  const pts = new Map();
  let start = null, lastTap = 0;
  const paint = () => { img.style.transform = `translate(${x}px, ${y}px) scale(${s})`; };
  const dist = () => { const [a, b] = [...pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };
  stage.onpointerdown = (e) => {
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    start = { s, x, y, d: pts.size === 2 ? dist() : 0, px: e.clientX, py: e.clientY };
    stage.setPointerCapture?.(e.pointerId);
  };
  stage.onpointermove = (e) => {
    if (!pts.has(e.pointerId) || !start) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2 && start.d) s = Math.min(5, Math.max(1, start.s * dist() / start.d));
    else if (pts.size === 1 && s > 1) { x = start.x + e.clientX - start.px; y = start.y + e.clientY - start.py; }
    if (s === 1) { x = 0; y = 0; }
    paint();
  };
  const up = (e) => {
    pts.delete(e.pointerId);
    start = pts.size ? { s, x, y, d: pts.size === 2 ? dist() : 0, px: [...pts.values()][0].x, py: [...pts.values()][0].y } : null;
  };
  stage.onpointerup = (e) => {
    const now = Date.now();
    if (pts.size === 1 && now - lastTap < 300) { s = s > 1 ? 1 : 2.5; x = 0; y = 0; paint(); lastTap = 0; }
    else lastTap = now;
    up(e);
  };
  stage.onpointercancel = up;
  paint();
}

function viewHtml(html, name) {
  const V = openViewer(name);
  const f = document.createElement('iframe');
  for (const [k, v] of Object.entries(sandboxAttrs())) f.setAttribute(k, v);
  f.className = 'mframe';
  f.title = name;
  f.srcdoc = html;
  V.stage.replaceChildren(f);
  V.note.textContent = t('media.htmlNote'); V.note.hidden = false;
}
