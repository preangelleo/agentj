import { registerLayer } from './layers.js';
// Media out (PROTOCOL §13, F21, 0.15.2): files, pictures, audio, video, PDFs and generated HTML that the Agent's reply
// refers to, sent by the computer inside the Noise session — the reverse of blobs.js. A finished page carries `media`
// [{mid, name, mime, kind, bytes, sha256, ref}] and `media_skip` [{name, why}]; nothing is fetched from a path or a URL.
// Bytes are pulled lazily: media_get {mid, o} → ≤ 8 media_chunk {mid, o, d, last} (45 056 raw bytes each) per request, the
// next window asked for when one is in; media_err {mid, why}. P183: small files load by themselves when their card scrolls
// into view (mediawire.autoLoad: pictures / PDFs ≤ 5 MiB, the rest ≤ 2 MiB; never on data saver / cellular), bigger ones on
// a tap that shows the size. The SHA-256 of what arrived is checked against the page's before
// anything is shown. A small in-memory LRU (≤ 64 MiB of Blobs, never stored); object URLs belong to the page on screen and
// are revoked when another page is shown, and on unpair (forgetAll).
// Rendering (DOM nodes only): image → <img> from a Blob URL; audio → <audio controls>; video → <video controls playsinline>;
// every card: 预览 · 下载 · 分享 · 转发到邮箱 (P184's dialog via mailfwd.js) · 转发到 Telegram (host cap `tgfile`) icons.
// 预览 (also Space / Enter on a focused card) = the full-screen viewer: pictures zoom, players play, documents via
// preview.js (pdf.js, mammoth, SheetJS, md.js, highlight.js); html → <iframe sandbox="" srcdoc> (an EMPTY sandbox: no scripts, no same-origin, no forms,
// no popups; the page's CSP is inherited by srcdoc and the iframe's own `csp` adds default-src 'none' with only data:
// pictures and inline styles — so no remote picture, stylesheet, font or anything else in the HTML ever loads, and inline
// <style> applies only where the page's own CSP allows it too). Share only where the phone can share files (iOS:
// 「存储到文件」). SVG only ever as <img> (scripts in an <img> never run).
import { sendApp, gen, peer } from './session.js';
import { t } from './t.js';
import { human, toast } from './ui.js';
import { tgForwardFile } from './api.js';
import { canMailForward, mailForward } from './mailfwd.js';
import { renderDoc } from './preview.js';
import { glyph } from './icons.js';
import { unb64u } from '../proto/wire.js';

import { CHUNK, WINDOW, EAGER, LRU_MAX, MAX_ITEMS, CAPS, WHYS, HTML_CSP, sanitize, sandboxAttrs, normRef, matchSlot, placeSlots, blobType, autoLoad, previewKind } from './mediawire.js';
export { CHUNK, WINDOW, EAGER, LRU_MAX, MAX_ITEMS, CAPS, WHYS, HTML_CSP, sanitize, sandboxAttrs, normRef, matchSlot, placeSlots, blobType };
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
// Two scopes own object URLs: the page on screen (`main`) and the full-screen reader (`reader`, P59 / ADR-A164). Both take
// their Blobs from the one LRU above (fetchMedia), so opening the reader never fetches a file a second time.
const scope = () => ({ key: null, page: null, urls: [], io: null });
let shown = scope();
let reading = scope();
function clearScope(sc) {
  for (const u of sc.urls) URL.revokeObjectURL(u);
  if (sc.io) sc.io.disconnect();
}
function clearShown() {
  clearScope(shown);
  shown = scope();
  closeViewer();
}
/** The reader closed (or shows other words): its object URLs go; the Blobs stay in the LRU. */
export function clearReader() {
  clearScope(reading);
  reading = scope();
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
const pageKey = (p, items, skips) => (p && p.id !== null && (items.length || skips.length)
  ? p.id + '|' + items.map((m) => m.mid).join(',') + '|' + skips.length : null);

/** Fill a words element's slots: the item's card (image inline, audio / video players, pdf / html / file cards), a
 *  refusal's one-line note, or nothing (the source text stays). → what no slot showed: {rest, restSkips}. */
function fillSlots(words, items, skips, sc) {
  const slots = [...words.querySelectorAll('.mslot')];
  const { at, rest, restSkips } = placeSlots(slots.map((s) => s.dataset.ref), items, skips);
  slots.forEach((s, i) => {
    const a = at[i];
    if (!a) return;
    const label = s.dataset.link === '1' ? (s.dataset.label || '').trim() : '';
    if (a.mid) {
      const m = items.find((x) => x.mid === a.mid);
      s.dataset.mid = m.mid;
      const c = card(m, true, sc);
      if (label && label !== m.name) s.replaceChildren(mk('span', 'mlabel', label), c); else s.replaceChildren(c);
    } else {
      s.dataset.skip = '1';
      s.replaceChildren(mk('span', 'mskip', skipText(skips[a.skip])));
    }
  });
  return { rest, restSkips };
}
const observer = (sc) => (typeof IntersectionObserver === 'function' ? new IntersectionObserver((es) => {
  for (const e of es) if (e.isIntersecting) { sc.io.unobserve(e.target); e.target._load?.(); }
}, { rootMargin: '200px 0px' }) : null);

/** After the page's words are on screen: fill its inline slots, the strip under the words and the skip notes.
 *  p = the page (snap.toPage: {id, media, mediaSkip}). Idempotent while the same page and list are shown. */
export function renderPage(words, p) {
  const box = strip(words);
  const items = (p && p.media) || [], skips = (p && p.mediaSkip) || [];
  const key = pageKey(p, items, skips);
  const fresh = [...words.querySelectorAll('.mslot')].some((s) => !s.dataset.mid && !s.dataset.skip);
  if (key === shown.key && (!key || !fresh)) return;
  clearShown();
  shown.key = key; shown.page = p && p.id !== undefined ? p.id : null;
  box.replaceChildren();
  box.hidden = !key;
  if (!key) return;
  shown.io = observer(shown);
  const { rest, restSkips } = fillSlots(words, items, skips, shown);
  for (const m of rest) box.appendChild(card(m, false, shown));
  for (const sk of restSkips) box.appendChild(mk('div', 'mskip', skipText(sk)));
  box.hidden = !box.childElementCount;
}

/** P59: the full-screen reader's words (relay.js fills them with md.js like the page's): the same slots filled the same way,
 *  from the same verified Blobs. Only the slots — what the reply did not place stays in the strip under the page. */
export function renderReader(words, p) {
  const items = (p && p.media) || [], skips = (p && p.mediaSkip) || [];
  const key = pageKey(p, items, skips);
  const fresh = [...words.querySelectorAll('.mslot')].some((s) => !s.dataset.mid && !s.dataset.skip);
  if (key === reading.key && (!key || !fresh)) return;
  clearReader();
  reading.key = key; reading.page = p && p.id !== undefined ? p.id : null;
  if (!key) return;
  reading.io = observer(reading);
  fillSlots(words, items, skips, reading);
}

// ---------------------------------------------------------------- P183: the card
// A dashed outline on no fill (the composer's four tool buttons), the file's name and size, and one row of icons drawn like
// the reply card's own (.rmbar): 预览 · 下载 · 分享 · 转发到邮箱 · 转发到 Telegram. Pictures, players and the body's
// 「点一下加载 · 12 MB」 sit above. Small files (mediawire.autoLoad: pictures / PDFs ≤ 5 MiB, the rest ≤ 2 MiB) load by
// themselves when the card scrolls into view — never on data saver or a cellular link; bigger ones on a tap. Every icon
// that needs the bytes loads them first. The card itself is focusable: Space (or Enter) = 预览, like Quick Look.
const CHANNEL = 'Telegram';                // a channel's name is data (like logincard's {channel}), never dictionary text
function iconBtn(cls, name, label) {
  const b = mk('button', 'mact ' + cls); b.type = 'button';
  b.setAttribute('aria-label', label); b.title = label; b.dataset.state = 'idle';
  b.append(glyph(name), glyph('ok', 'g gok'), mk('i', 'bang', '!'));
  b.lastChild.setAttribute('aria-hidden', 'true');
  return b;
}
const net = () => { const c = navigator.connection || {}; return { saveData: c.saveData === true, type: c.type, effectiveType: c.effectiveType }; };
function shareable(m) {
  if (!navigator.share || !navigator.canShare) return false;
  try { return navigator.canShare({ files: [new File([new Uint8Array(1)], m.name, { type: blobType(m) })] }); } catch { return false; }
}
if (typeof document !== 'undefined') document.addEventListener('aj:mailfwd', () => {
  for (const b of document.querySelectorAll('.mcard .mact.mail')) b.hidden = !canMailForward();
});

function card(m, inline, sc) {
  const pv = previewKind(m);
  const c = mk('div', 'mcard' + (inline ? ' inline' : ''));
  c._sc = sc;
  c.dataset.kind = m.kind; c.dataset.state = 'idle'; c.dataset.mid = m.mid; c.dataset.pv = pv;
  c.tabIndex = 0; c.setAttribute('role', 'group'); c.setAttribute('aria-label', m.name);
  const body = mk('div', 'mbody');
  const status = mk('button', 'mtap', t('media.tapSize', { size: human(m.bytes) }));
  status.type = 'button';
  body.appendChild(status);
  const foot = mk('div', 'mfoot');
  const meta = mk('div', 'mmeta');
  const icon = glyph('file', 'mico');
  meta.append(icon, mk('span', 'mname', m.name), mk('span', 'msize', human(m.bytes)));
  const acts = mk('div', 'macts'); acts.setAttribute('role', 'toolbar'); acts.setAttribute('aria-label', m.name);
  const bPv = iconBtn('pv', 'preview', t('media.act.preview'));
  const bDl = iconBtn('dl', 'download', t('media.act.download'));
  const bSh = iconBtn('sh', 'share', t('media.act.share'));
  const bMail = iconBtn('mail', 'mail', t('media.act.mail'));
  const bTg = iconBtn('tg', 'tg', t('media.act.tg', { channel: CHANNEL }));
  bSh.hidden = !shareable(m);
  bMail.hidden = !canMailForward();
  bTg.hidden = !peer.tgfile;
  acts.append(bPv, bDl, bSh, bMail, bTg);
  foot.append(meta, acts);
  c.append(body, foot);

  let flight = null;
  const load = () => {
    if (c._blob) return Promise.resolve(c._blob);
    if (flight) return flight;
    c.dataset.state = 'loading'; status.textContent = t('media.loading'); status.disabled = true;
    flight = fetchMedia(m).then((blob) => {
      if (c.isConnected) show(m, c, body, blob);
      return blob;
    }, (e) => {
      c.dataset.state = 'error'; status.disabled = e.why === 'gone' || e.why === 'sha';
      status.textContent = e.why === 'gone' ? t('media.expired') : e.why === 'sha' ? t('media.shaBad') : t('media.retry');
      throw e;
    }).finally(() => { flight = null; });
    return flight;
  };
  c._load = () => load().catch(() => {});
  const preview = () => load().then((blob) => openPreview(m, c, blob), () => {});
  status.addEventListener('click', (e) => { e.stopPropagation(); load().then(() => { if (m.kind === 'audio' || m.kind === 'video') body.querySelector('audio,video')?.play?.().catch(() => {}); }, () => {}); });
  bPv.addEventListener('click', (e) => { e.stopPropagation(); preview(); });
  bDl.addEventListener('click', (e) => { e.stopPropagation(); load().then((blob) => download(m, c, blob), () => {}); });
  bSh.addEventListener('click', (e) => {
    e.stopPropagation();
    const ready = !!c._blob;
    load().then((blob) => {
      let file;
      try { file = new File([blob], m.name, { type: blob.type }); } catch { return; }
      navigator.share({ files: [file], title: m.name }).catch((err) => { if (!ready && err && err.name === 'NotAllowedError') toast(t('media.shareAgain'), 2600); });
    }, () => {});
  });
  bMail.addEventListener('click', (e) => {
    e.stopPropagation();
    mailForward({ kind: 'file', mid: m.mid, name: m.name, mime: m.mime, bytes: m.bytes, page: sc.page ?? null, blob: load });
  });
  bTg.addEventListener('click', (e) => { e.stopPropagation(); tgTap(m, bTg); });
  c.addEventListener('keydown', (e) => {
    if (e.target !== c || e.ctrlKey || e.metaKey || e.altKey || (e.key !== ' ' && e.key !== 'Enter')) return;
    e.preventDefault(); e.stopPropagation(); preview();
  });
  // a tap on the card's empty space (not a button, a player or a picture) previews a document
  c.addEventListener('click', (e) => {
    if (e.target.closest('button,a,audio,video,img,.mslot .mlabel')) return;
    if (m.kind !== 'audio' && m.kind !== 'video' && m.kind !== 'image') { e.stopPropagation(); preview(); }
  });
  if (autoLoad(m, net())) {
    if (sc.io) sc.io.observe(c); else queueMicrotask(() => c._load());
  }
  return c;
}

const tgBusy = new Set();
async function tgTap(m, b) {
  if (!peer.tgfwd) { toast(t('media.tg.setup', { channel: CHANNEL }), 3200); return; }
  if (tgBusy.has(m.mid)) return;
  tgBusy.add(m.mid); b.dataset.state = 'sending'; b.disabled = true;
  let r;
  try { r = await tgForwardFile(m.mid); } catch { r = { ok: false, why: 'send_failed' }; }
  tgBusy.delete(m.mid); b.disabled = false;
  const st = r.ok ? 'sent' : (r.why === 'recently_sent' || r.why === 'in_flight' || r.why === 'rate_limited') ? 'idle' : 'failed';
  b.dataset.state = st;
  if (st !== 'idle') setTimeout(() => { if (b.dataset.state === st) b.dataset.state = 'idle'; }, r.ok ? 2000 : 3000);
  const key = r.ok ? 'media.tg.sent' : r.why === 'not_configured' ? 'media.tg.setup' : r.why === 'too_big' ? 'media.tg.tooBig'
    : r.why === 'not_found' ? 'media.tg.gone' : r.why === 'rate_limited' ? 'r.tg.limited'
    : (r.why === 'recently_sent' || r.why === 'in_flight') ? 'r.tg.again' : 'media.tg.failed';
  if (r.why === 'not_configured') peer.tgfwd = false;
  toast(t(key, { channel: CHANNEL }), 2600);
}

function urlOf(c, blob) {
  if (!c._url) { c._url = URL.createObjectURL(blob); c._sc.urls.push(c._url); }
  return c._url;
}
function download(m, c, blob) {
  const a = mk('a'); a.href = urlOf(c, blob); a.download = m.name; a.rel = 'noopener'; a.hidden = true;
  c.appendChild(a); a.click(); a.remove();
}
function show(m, c, body, blob) {
  c._blob = blob;
  const url = urlOf(c, blob);
  c.dataset.state = 'ready';
  if (m.kind === 'image') {
    const img = mk('img'); img.alt = m.name; img.src = url; img.decoding = 'async';
    img.tabIndex = 0; img.setAttribute('role', 'button');
    img.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); viewImage(url, m.name, c, m); } });
    img.addEventListener('click', (e) => { e.stopPropagation(); viewImage(url, m.name, c, m); });
    body.replaceChildren(img);
  } else if (m.kind === 'audio' || m.kind === 'video') {
    const v = mk(m.kind); v.controls = true; v.preload = 'metadata'; v.src = url;
    if (m.kind === 'video') { v.playsInline = true; v.setAttribute('playsinline', ''); }
    body.replaceChildren(v);
  } else {
    body.replaceChildren();                              // a document: name, size and the icons say it all
  }
}
function openPreview(m, c, blob) {
  const url = urlOf(c, blob), kind = previewKind(m);
  if (kind === 'image') return viewImage(url, m.name, c, m);
  if (kind === 'html') return blob.arrayBuffer().then((b) => viewHtml(new TextDecoder().decode(b), m, c));
  if (kind === 'audio' || kind === 'video') return viewPlayer(m, url, c);
  return viewDoc(kind, m, blob, c);
}

// ---------------------------------------------------------------- the viewer: full screen, a bar (name · 下载 · ×), Esc / Space close
let viewer = null;
function viewerEl() {
  if (viewer) return viewer;
  const v = mk('div', 'mview'); v.hidden = true;
  v.setAttribute('role', 'dialog'); v.setAttribute('aria-modal', 'true');
  const bar = mk('div', 'mvbar');
  const title = mk('span', 'mvtitle');
  const dl = mk('button', 'mvact mvdl'); dl.type = 'button'; dl.append(glyph('download'));
  const x = mk('button', 'mview-x', '×'); x.type = 'button';
  bar.append(title, dl, x);
  const stage = mk('div', 'mstage');
  const note = mk('div', 'mnote');
  v.append(bar, stage, note);
  x.addEventListener('click', closeViewer);
  dl.addEventListener('click', () => { const c = viewer.card; if (c && c._blob && viewer.m) download(viewer.m, c, c._blob); });
  v.addEventListener('click', (e) => { if (e.target === v || e.target === stage) closeViewer(); });
  // Space closes the preview again (Quick Look), unless a player or a field has the key
  v.addEventListener('keydown', (e) => {
    if (e.key === ' ' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.target.closest('audio,video,input,textarea,select,.mpvtabs')) {
      e.preventDefault(); e.stopPropagation(); closeViewer();
    } else if (e.key !== 'Escape' && e.key !== 'Tab') e.stopPropagation();   // arrows / PageDown scroll the preview, not the cards behind it
  });
  document.body.appendChild(v);
  viewer = { v, x, dl, title, stage, note, card: null, m: null, cleanup: null };
  registerLayer({element:()=>v,open:()=>!v.hidden,close:closeViewer,fallback:()=>document.getElementById(document.body.dataset.reader==='1'?'rdClose':'readBtn'),rank:70});
  return viewer;
}
function openViewer(label, c, mode = 'image', m = null) {
  const V = viewerEl();
  if (V.cleanup) { try { V.cleanup(); } catch { /* gone */ } V.cleanup = null; }
  V.card = c || null; V.m = m;
  if (mode !== 'image') V.stage.onpointerdown = V.stage.onpointermove = V.stage.onpointerup = V.stage.onpointercancel = null;
  V.v.dataset.mode = mode;
  V.v.setAttribute('aria-label', label || t('media.viewer'));
  V.title.textContent = label || '';
  V.x.setAttribute('aria-label', t('media.close')); V.x.title = t('media.close');
  V.dl.setAttribute('aria-label', t('media.act.download')); V.dl.title = t('media.act.download');
  V.dl.hidden = !(c && c._blob);
  V.note.textContent = ''; V.note.hidden = true;
  V.v.hidden = false; V.v.dataset.modalOpen = '1';
  V.x.focus({ preventScroll: true });
  return V;
}
export function closeViewer() {
  if (!viewer || viewer.v.hidden) return;
  if (viewer.cleanup) { try { viewer.cleanup(); } catch { /* gone */ } viewer.cleanup = null; }
  viewer.v.hidden = true; delete viewer.v.dataset.modalOpen;
  viewer.stage.replaceChildren(); viewer.note.textContent = ''; viewer.note.hidden = true; viewer.card = null; viewer.m = null;
}
export const viewerOpen = () => !!viewer && !viewer.v.hidden;

function viewImage(url, name, c, m = null) {
  const V = openViewer(name, c, 'image', m);
  const img = mk('img', 'mzoom'); img.alt = name; img.src = url; img.draggable = false;
  V.stage.replaceChildren(img);
  zoomable(V.stage, img);
}
function viewPlayer(m, url, c) {
  const V = openViewer(m.name, c, 'player', m);
  const v = mk(m.kind, 'mplayer'); v.controls = true; v.src = url; v.preload = 'metadata';
  if (m.kind === 'video') { v.playsInline = true; v.setAttribute('playsinline', ''); }
  V.stage.replaceChildren(v);
  v.play?.().catch(() => {});
}
async function viewDoc(kind, m, blob, c) {
  const V = openViewer(m.name, c, 'doc', m);
  const box = mk('div', 'mdoc'); box.tabIndex = 0;
  V.stage.replaceChildren(box);
  if (kind === 'none') { box.dataset.pv = 'none'; box.replaceChildren(unsupported(m, c, blob)); return; }
  try {
    const done = await renderDoc(kind, m, blob, box);
    if (typeof done === 'function') { if (V.card === c && !V.v.hidden) V.cleanup = done; else done(); }
  } catch {
    if (V.card !== c || V.v.hidden) return;
    box.dataset.pv = 'none';
    box.replaceChildren(unsupported(m, c, blob, kind === 'pdf' ? 'media.pv.pdfFail' : 'media.pv.fail'));
  }
}
function unsupported(m, c, blob, key = 'media.pv.none') {
  const w = mk('div', 'mpvnone');
  const ic = glyph('file', 'mpvbig');
  const b = mk('button', 'mtap', t('media.act.download')); b.type = 'button';
  b.addEventListener('click', () => download(m, c, blob));
  w.append(ic, mk('p', 'mpvname', m.name), mk('p', 'mpvnote', t(key)), b);
  if (m.kind === 'pdf') {                               // pdf.js could not draw it: the phone's own PDF viewer still can
    const a = mk('a', 'mtap', t('media.open')); a.href = urlOf(c, blob); a.target = '_blank'; a.rel = 'noopener noreferrer';
    w.appendChild(a);
  }
  return w;
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

function viewHtml(html, m, c) {
  const name = m.name;
  const V = openViewer(name, c, 'doc', m);
  const f = document.createElement('iframe');
  for (const [k, v] of Object.entries(sandboxAttrs())) f.setAttribute(k, v);
  f.className = 'mframe';
  f.title = name;
  f.srcdoc = html;
  V.stage.replaceChildren(f);
  V.note.textContent = t('media.htmlNote'); V.note.hidden = false;
}
