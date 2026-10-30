import { fileDoubleTap, DOUBLE_MS } from './file-gestures.js';
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
import { t, onLang } from './t.js';
import { human, toast } from './ui.js';
import { tgForwardFile } from './api.js';
import { canMailForward, mailForward } from './mailfwd.js';
import { renderDoc } from './preview.js';
import { glyph } from './icons.js';
import { unb64u } from '../proto/wire.js';

import { CHUNK, WINDOW, EAGER, LRU_MAX, MAX_ITEMS, CAPS, WHYS, HTML_CSP, sanitize, sandboxAttrs, FRAME_NOBAR, normRef, matchSlot, placeSlots, blobType, autoLoad, previewKind, codeLang, PREVIEW } from './mediawire.js';
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
  tgStates.clear();
  for (const f of [...flights.values()]) f.fail('net');
  waiting.length = 0; cache.clear(); cacheBytes = 0;
  clearScope(reading, true); reading = scope();
  clearShown(true);
}
export const cacheSize = () => cacheBytes;

// ---------------------------------------------------------------- rendering
// Two scopes own object URLs: the page on screen (`main`) and the full-screen reader (`reader`, P59 / ADR-A164). Both take
// their Blobs from the one LRU above (fetchMedia), so opening the reader never fetches a file a second time.
const scope = () => ({ key: null, page: null, urls: [], io: null, cards: () => [] });
let shown = scope();
let reading = scope();
// P205: a card still on the page keeps its object URL (a re-render whose page lost its file list leaves the inline cards in
// place — their player and 下载 must not point at a revoked URL); it is revoked by a later sweep once the card is gone.
let kept = [];
function clearScope(sc, all = false) {
  const live = [];
  for (const x of [...kept, ...sc.urls]) { if (!all && x.c.isConnected) live.push(x); else URL.revokeObjectURL(x.u); }
  kept = live;
  if (sc.io) sc.io.disconnect();
}
function clearShown(all = false) {
  clearScope(shown, all);
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
export function skipText(s) {
  const at = s.line ? t('media.skip.at', { line: s.line, hint: s.hint || '' }) : '';   // P424 #142: 「（第 3 行：sk…）」
  return t('media.skip.' + s.why, { name: s.name, mb: mb(s.bytes || 0) }) + at;
}

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

// P205 (6): a [label](local file) the computer never sent with a finished page (a page written before the host scanned
// shared-session text, or a file it did not offer) kept its raw Markdown — a dead link. After UNSENT_MS (the file list
// follows the page's end) the slot shows the label and one line saying so; a list that arrives later still fills it.
export const UNSENT_MS = 6000;
function unsent(words, p) {
  if (!p || p.end === 'open') return;
  const late = p.ts && Date.now() / 1000 - p.ts > 600 ? 0 : UNSENT_MS;
  for (const s of words.querySelectorAll('.mslot[data-link="1"]')) {
    if (s.dataset.mid || s.dataset.skip || s.dataset.unsent) continue;
    s.dataset.unsent = 'wait';
    const show = () => {
      if (!s.isConnected || s.dataset.mid || s.dataset.skip) return;
      s.dataset.unsent = '1';
      s.replaceChildren(mk('span', 'mlabel', (s.dataset.label || '').trim() || String(s.dataset.ref || '').split(/[/\\]/).filter(Boolean).pop() || ''), mk('span', 'mskip', t('media.unsent')));
    };
    if (late) setTimeout(show, late); else show();
  }
}

/** After the page's words are on screen: fill its inline slots, the strip under the words and the skip notes.
 *  p = the page (snap.toPage: {id, media, mediaSkip}). Idempotent while the same page and list are shown. */
export function renderPage(words, p) {
  unsent(words, p);
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
  shown.cards = () => [...words.querySelectorAll('.mcard'), ...box.querySelectorAll('.mcard')];
  const { rest, restSkips } = fillSlots(words, items, skips, shown);
  for (const m of rest) box.appendChild(card(m, false, shown));
  for (const sk of restSkips) box.appendChild(mk('div', 'mskip', skipText(sk)));
  box.hidden = !box.childElementCount;
}

/** P59: the full-screen reader's words (relay.js fills them with md.js like the page's): the same slots filled the same way,
 *  from the same verified Blobs. P273: the slots, then what the reply did not place (.rdstrip at the end of the words). */
export function renderReader(words, p) {
  unsent(words, p);
  const items = (p && p.media) || [], skips = (p && p.mediaSkip) || [];
  const key = pageKey(p, items, skips);
  const fresh = [...words.querySelectorAll('.mslot')].some((s) => !s.dataset.mid && !s.dataset.skip);
  if (key === reading.key && (!key || !fresh)) return;
  clearReader();
  reading.key = key; reading.page = p && p.id !== undefined ? p.id : null;
  if (!key) return;
  reading.io = observer(reading);
  // P273 (long reading): what the reply did not place follows its words here too, in the reader's own strip (never #mstrip)
  reading.cards = () => [...words.querySelectorAll('.mcard')];   // the slots + .rdstrip: every file of this reply, once
  const { rest, restSkips } = fillSlots(words, items, skips, reading);
  words.querySelector(':scope>.rdstrip')?.remove();
  if (!rest.length && !restSkips.length) return;
  const box = mk('div', 'mstrip rdstrip');
  for (const m of rest) box.appendChild(card(m, false, reading));
  for (const sk of restSkips) box.appendChild(mk('div', 'mskip', skipText(sk)));
  words.appendChild(box);
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
  const bPv = iconBtn('pv', 'preview', t(m.kind === 'audio' ? 'media.act.preview' : 'media.act.fullscreen'));
  if (m.kind !== 'audio') bPv.append(mk('span', 'mfullscreen-label', t('media.act.fullscreen')));
  const bDl = iconBtn('dl', 'download', t('media.act.download'));
  const bSh = iconBtn('sh', 'share', t('media.act.share'));
  const bMail = iconBtn('mail', 'mail', t('media.act.mail'));
  const bTg = iconBtn('tg', 'tg', t('media.act.tg', { channel: CHANNEL }));
  bSh.hidden = !shareable(m);
  bMail.hidden = !canMailForward();
  bTg.hidden = !peer.tgfile;
  acts.append(bPv, bDl, bSh, bMail, bTg);
  foot.append(meta, acts);
  const tgNote = mk('div', 'mtg-status'); tgNote.hidden = true; tgNote.setAttribute('role', 'status');
  c.append(body, foot, tgNote);
  queueMicrotask(() => paintTg(m.mid));

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
  c._m = m;
  // P196: the last card asked for wins — flipping fast through big files never opens an older one late
  const preview = () => { want = c; return load().then((blob) => { if (want === c) openPreview(m, c, blob); }, () => { if (want === c && viewerOpen() && viewer.card === c) loadFailed(); }); };
  c._preview = preview;
  if (m.kind !== 'audio') fileDoubleTap(c, preview);   // @ix file.preview
  status.addEventListener('click', (e) => { e.stopPropagation(); load().then(() => { if (m.kind === 'audio' || m.kind === 'video') body.querySelector('audio,video')?.play?.().catch(() => {}); }, () => {}); });
  bPv.addEventListener('click', (e) => { e.stopPropagation(); preview(); });   // @ix file.preview
  bDl.addEventListener('click', (e) => { e.stopPropagation(); dlTap(m, c, load, bDl); });   // @ix file.download
  bSh.addEventListener('click', (e) => {   // @ix file.share
    e.stopPropagation();
    const ready = !!c._blob;
    load().then((blob) => {
      let file;
      try { file = new File([blob], m.name, { type: blob.type }); } catch { return; }
      navigator.share({ files: [file], title: m.name }).catch((err) => { if (!ready && err && err.name === 'NotAllowedError') toast(t('media.shareAgain'), 2600); });
    }, () => {});
  });
  bMail.addEventListener('click', (e) => {   // @ix file.mail
    e.stopPropagation();
    mailForward({ kind: 'file', mid: m.mid, name: m.name, mime: m.mime, bytes: m.bytes, page: sc.page ?? null, blob: load });
  });
  bTg.addEventListener('click', (e) => { e.stopPropagation(); tgTap(m, bTg); });   // @ix file.forward
  c.addEventListener('keydown', (e) => {
    if (e.target !== c || e.ctrlKey || e.metaKey || e.altKey || (e.key !== ' ' && e.key !== 'Enter')) return;
    e.preventDefault(); e.stopPropagation(); preview();
  });
  // a tap on the card's empty space (not a button, a player or a picture) previews a document
  c.addEventListener('click', (e) => {
    if (e.target.closest('button,a,audio,video,img,.mslot .mlabel')) return;
    if (m.kind !== 'audio' && m.kind !== 'video' && m.kind !== 'image') { e.stopPropagation(); preview(); }
  });
  // P273: bytes already in the LRU (the page showed them) cost nothing — draw them at once, wherever the card sits (the
  // reader opens mid-reply); the rest still wait until they scroll near the screen
  if (cache.has(m.mid)) queueMicrotask(() => c._load());
  else if (autoLoad(m, net())) {
    if (sc.io) sc.io.observe(c); else queueMicrotask(() => c._load());
  }
  return c;
}

// Status belongs to the media id, shared by the card and reader, and survives page turns.
const tgStates = new Map();
onLang(() => { for (const mid of tgStates.keys()) paintTg(mid); });
function paintTg(mid) {
  const st = tgStates.get(mid);
  for (const c of document.querySelectorAll('.mcard')) {
    if (c.dataset.mid !== mid) continue;
    const b = c.querySelector('.mact.tg'), note = c.querySelector('.mtg-status');
    if (!b || !note) continue;
    b.dataset.state = st?.state || 'idle'; b.disabled = st?.state === 'sending';
    note.textContent = st ? t(st.key, {channel: CHANNEL}) : ''; note.hidden = !st;
    if (st) { b.title = note.textContent; b.setAttribute('aria-label', note.textContent); }
  }
}
async function tgTap(m, b) {
  if (tgStates.get(m.mid)?.state === 'sending') return;
  const set = (state, key) => {
    tgStates.delete(m.mid); tgStates.set(m.mid, {state, key});
    for (const [id, st] of tgStates) { if (tgStates.size <= 128) break; if (st.state !== 'sending') tgStates.delete(id); }
    paintTg(m.mid);
  };
  if (!peer.tgfwd) { set('failed', 'media.tg.setup'); return; }
  set('sending', 'media.tg.sending');
  const request = tgStates.get(m.mid);
  let r;
  try { r = await tgForwardFile(m.mid); } catch { r = {ok: false, why: 'send_failed'}; }
  // Unpair clears the state; a stale request must not recreate it on another host.
  if (tgStates.get(m.mid) !== request) return;
  const sent = r.ok || r.why === 'recently_sent';
  const key = sent ? 'media.tg.sent' : r.why === 'not_configured' ? 'media.tg.setup' : r.why === 'too_big' ? 'media.tg.tooBig'
    : r.why === 'not_found' ? 'media.tg.gone' : r.why === 'rate_limited' ? 'r.tg.limited'
    : r.why === 'in_flight' ? 'r.tg.again' : r.why === 'timeout' ? 'media.tg.uncertain' : 'media.tg.failed';
  set(sent ? 'sent' : 'failed', key);
}

function urlOf(c, blob) {
  if (!c._url) { c._url = URL.createObjectURL(blob); c._sc.urls.push({ u: c._url, c }); }
  return c._url;
}
// P205 (Leo 10-13, Mac Chrome 0.20.1a1: the 下载 icon on 张教授的思念.mp3 did nothing he could see). Not reproduced in Chromium
// (headless and headed) or WebKit with the same reply, name and size (web/test/p205_dl.mjs), so the download no longer leans on
// anything that can go stale or silent: its own object URL of an application/octet-stream view (the card's player URL is
// revoked when the page re-renders; a typed audio/PDF Blob may open in place instead of saving), an anchor on <body> (no
// ancestor click handler), the URL kept a minute (Safari reads it after the click returns), a toast with the name (the owner
// sees that it went to the browser), and a fetch that fails says so on the icon. iPhone / iPad home-screen apps cannot save a
// blob link at all (WebKit opens nothing), so there the share sheet ("Save to Files") is the download.
const IOS = typeof navigator !== 'undefined' && (/iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1));
const standalone = () => navigator.standalone === true || (typeof matchMedia === 'function' && matchMedia('(display-mode: standalone)').matches);
export const DL_KEEP_MS = 60_000;
function download(m, c, blob) {
  if (IOS && standalone() && shareable(m)) {
    let file;
    try { file = new File([blob], m.name, { type: blob.type || blobType(m) }); } catch { file = null; }
    if (file) { navigator.share({ files: [file], title: m.name }).catch((err) => { if (err && err.name === 'NotAllowedError') toast(t('media.dlAgain'), 2600); }); return; }
  }
  const url = URL.createObjectURL(new Blob([blob], { type: 'application/octet-stream' }));
  const a = mk('a'); a.href = url; a.download = m.name; a.rel = 'noopener'; a.hidden = true;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), DL_KEEP_MS);
  toast(t('media.dlStarted', { name: m.name }), 2400);
}
/** P432: the Files card's download — the same §13 media_get pull (SHA-256 checked) and save as a reply's 下载 icon. */
export function save(m) {
  return fetchMedia(m).then((blob) => download(m, null, blob), (e) => toast(t(e && e.why === 'gone' ? 'media.expired' : e && e.why === 'sha' ? 'media.shaBad' : 'media.dlFailed'), 2600));
}
/** The 下载 icon: fetch the bytes if needed (the icon breathes meanwhile), then save; a failed fetch marks the icon and says so. */
function dlTap(m, c, load, b) {
  if (c._blob) { download(m, c, c._blob); return; }
  b.dataset.state = 'sending';
  load().then((blob) => { b.dataset.state = 'idle'; download(m, c, blob); }, (e) => {
    b.dataset.state = 'failed';
    setTimeout(() => { if (b.dataset.state === 'failed') b.dataset.state = 'idle'; }, 3000);
    toast(t(e && e.why === 'gone' ? 'media.expired' : e && e.why === 'sha' ? 'media.shaBad' : 'media.dlFailed'), 2600);
  });
}
function show(m, c, body, blob) {
  c._blob = blob;
  const url = urlOf(c, blob);
  c.dataset.state = 'ready';
  if (m.kind === 'image') {
    const img = mk('img'); img.alt = m.name; img.src = url; img.decoding = 'async';
    img.tabIndex = 0; img.setAttribute('role', 'button');
    img.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); want = c; viewImage(url, m.name, c, m); } });
    img.addEventListener('click', (e) => { e.stopPropagation(); want = c; viewImage(url, m.name, c, m); });
    body.replaceChildren(img);
  } else if (m.kind === 'audio' || m.kind === 'video') {
    const v = mk(m.kind); v.controls = true; v.preload = 'metadata'; v.src = url;
    if (m.kind === 'video') {
      v.playsInline = true; v.setAttribute('playsinline', '');
      let single = 0;
      c._cancelSingle = () => { clearTimeout(single); single = 0; };
      // An independent picture hit surface prevents UA shadow-controls from toggling
      // playback before page listeners on touch. The native bottom controls stay exposed.
      const surface = mk('div', 'mvideo-surface'); surface.setAttribute('aria-hidden', 'true');
      c._videoSurface = surface;
      surface.addEventListener('click', e => {
        e.preventDefault(); e.stopPropagation();
        c._cancelSingle();
        if (e.detail < 2) single = setTimeout(() => {
          single = 0;
          if (v.paused) v.play().catch(() => {}); else v.pause();
        }, DOUBLE_MS);
      });
    }
    body.replaceChildren(v);
    if (m.kind === 'video') body.appendChild(c._videoSurface);
  } else {
    body.replaceChildren();                              // a document: name, size and the icons say it all
  }
}
// P573 (#386): a file to read (Markdown / plain text / code) opens in the reply's own full-screen reader (relay.js registers it
// here): the same Noir page, the same md.js rendering, the same footer icons, select-to-quote — one reader, not two.
let docReader = null;
export function setDocReader(fn) { docReader = typeof fn === 'function' ? fn : null; }
const READ_KINDS = new Set(['markdown', 'text', 'code']);
async function readInReader(m, c, blob, kind) {
  const cut = blob.size > PREVIEW.TEXT_MAX;
  const text = new TextDecoder('utf-8', { fatal: false }).decode(await (cut ? blob.slice(0, PREVIEW.TEXT_MAX) : blob).arrayBuffer());
  if (want !== c) return;
  if (viewerOpen()) { const pushed = viewerPushed; closeViewer(); want = c; if (pushed) await afterPop(); }   // ← / → from the viewer: its Back lands first
  if (want !== c) return;
  want = null;
  docReader({ name: m.name, text, cut, kind, lang: codeLang(m.name), card: c });
}
// history.back() is asynchronous: the next layer pushes its own entry only after the pop (or 300 ms), never before it
export const afterPop = () => new Promise((r) => { const t = setTimeout(r, 300); addEventListener('popstate', () => { clearTimeout(t); setTimeout(r, 0); }, { once: true }); });
/** P573: ← / → in the file reader — the previous / next previewable file of the same reply (the viewer's own order). */
export function fileNeighbour(c, d) { const list = navList(c), i = list.indexOf(c); return i < 0 ? null : list[i + d] || null; }
export const readsInReader = (c) => READ_KINDS.has(c?.dataset?.pv);
/** Esc / × on the viewer gives the focus back to this card (the file last shown, as step() does). */
export function viewerOpener(c) { if (viewer?.layer && viewerOpen()) viewer.layer.opener = c; }
function openPreview(m, c, blob) {
  c._cancelSingle?.();
  const kind0 = previewKind(m);
  if (docReader && READ_KINDS.has(kind0)) return readInReader(m, c, blob, kind0);
  const url = urlOf(c, blob), kind = kind0;
  if (kind === 'image') return viewImage(url, m.name, c, m);
  if (kind === 'html') return blob.arrayBuffer().then((b) => viewHtml(new TextDecoder().decode(b), m, c));
  if (kind === 'audio' || kind === 'video') return viewPlayer(m, url, c);
  return viewDoc(kind, m, blob, c);
}

// ---------------------------------------------------------------- the viewer: full screen, a bar (‹ 3 / 14 › · name · 下载 · ×), Esc / Space close
// P196: while it is open it is the preview layer — the top layer (Esc closes it) that takes every key before the chat's own
// shortcuts and the deck's card swipe ever see it. ← / → (and a sideways swipe; on a picture only while it is not zoomed —
// zoomed, one finger pans) step to the previous / next file of the SAME reply that can be previewed, in reading order;
// no wrap-around at either end (a short note says so). A player is paused before it goes.
let viewer = null;
let viewerPushed = false, viewerSkipPop = false;
let want = null;                              // the card whose preview was asked for last
const SWIPE_PX = 60, FLING_PX = 30, FLING_V = 0.5, SWIPE_TAN = Math.tan(30 * Math.PI / 180), CLICK_SLOP = 10;
function viewerEl() {
  if (viewer) return viewer;
  const v = mk('div', 'mview'); v.hidden = true;
  v.setAttribute('role', 'dialog'); v.setAttribute('aria-modal', 'true');
  const bar = mk('div', 'mvbar');
  const title = mk('span', 'mvtitle');
  const prev = mk('button', 'mvact mvprev', '‹'); prev.type = 'button';
  const next = mk('button', 'mvact mvnext', '›'); next.type = 'button';
  const count = mk('span', 'mvcount'); count.hidden = true;
  const dl = mk('button', 'mvact mvdl'); dl.type = 'button'; dl.append(glyph('download'));
  const x = mk('button', 'mview-x', '×'); x.type = 'button';
  bar.append(prev, count, next, title, dl, x);
  const stage = mk('div', 'mstage');
  const note = mk('div', 'mnote');
  const hint = mk('div', 'mvhint'); hint.setAttribute('role', 'status'); hint.setAttribute('aria-live', 'polite');
  v.append(bar, stage, note, hint);
  x.addEventListener('click', closeViewer);   // @ix viewer.close (+ Space / Esc, a tap outside the file)
  prev.addEventListener('click', () => step(-1));   // @ix viewer.step (+ swipeable below, ← → in viewerKey)
  next.addEventListener('click', () => step(1));
  dl.addEventListener('click', viewerDownload);   // @ix viewer.download (+ d in viewerKey)
  v.addEventListener('click', (e) => {
    if (performance.now() - viewer.openedAt < 400) return;
    if (performance.now() - viewer.dragAt < 400) return;            // the end of a drag is not a tap on the backdrop
    // Pointer capture retargets image taps to the stage: they belong to zoomable.
    if (e.target === v || (e.target === stage && v.dataset.mode !== 'image')) closeViewer();
  });
  v.addEventListener('click', e => { if (performance.now() - viewer.openedAt < 400 && !e.target.closest('button,a,input,textarea,select,[role=link]')) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
  swipeable(v, stage);
  document.body.appendChild(v);
  viewer = { v, x, dl, title, stage, note, hint, prev, next, count, card: null, m: null, cleanup: null, dragAt: 0, hintT: 0 };
  viewer.layer = registerLayer({element:()=>v,open:()=>!v.hidden,close:closeViewer,fallback:()=>document.getElementById(document.body.dataset.reader==='1'?'rdReadBtn':'readBtn'),rank:90});
  return viewer;
}
/** The preview layer's keys (capture, before the chat screen's shortcuts and the layers' own handler): ← / → step,
 *  Space closes (Quick Look) unless a player or a field has it; Esc and Tab go on to the layers (close, focus trap);
 *  every other key reaches only the preview itself (its default action: scroll the document, press the button). */
function viewerDownload() { const c = viewer && viewer.card; if (c && c._blob && viewer.m) download(viewer.m, c, c._blob); }
function viewerKey(e) {
  if (!viewerOpen() || e.key === 'Escape' || e.key === 'Tab' || e.isComposing) return;
  const tg = e.target && e.target.closest ? e.target : null;
  const mods = e.ctrlKey || e.metaKey || e.altKey;
  if (!mods && !e.shiftKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !tg?.closest('input,textarea,select,[contenteditable]')) {
    e.preventDefault(); e.stopImmediatePropagation();
    if (!e.repeat) step(e.key === 'ArrowLeft' ? -1 : 1);
    return;
  }
  // P207: d = 下载 the file being previewed (the bar's download icon)
  if (e.key === 'd' && !mods && !e.shiftKey && !e.repeat && !tg?.closest('input,textarea,select,[contenteditable]')) {
    e.preventDefault(); e.stopImmediatePropagation(); viewerDownload();
    return;
  }
  if (e.key === ' ' && !mods && !tg?.closest('audio,video,input,textarea,select,.mpvtabs')) {
    e.preventDefault(); e.stopImmediatePropagation(); closeViewer();
    return;
  }
  e.stopImmediatePropagation();
}
if (typeof document !== 'undefined') {
  document.addEventListener('keydown', viewerKey, true);
  window.addEventListener('popstate', e => {
    if (viewerSkipPop) {
      viewerSkipPop = false;
      // A new file can open while the preceding close waits for asynchronous Back.
      // Push its entry only after that pop; otherwise Back would consume the new one.
      if (viewerOpen()) { try { history.pushState({ ...history.state, ajFile: 1 }, ''); viewerPushed = true; } catch { viewerPushed = false; } }
      e.stopImmediatePropagation(); return;
    }
    if (viewerOpen()) { viewerPushed = false; closeViewer(true); e.stopImmediatePropagation(); }
  }, true);
}

/** The files of the reply the viewer's card belongs to, in reading order: those that can be previewed (+ the one shown). */
function navList(c) {
  const all = c && c._sc ? c._sc.cards() : [];
  return all.filter((x) => x === c || (x.isConnected && x.dataset.pv !== 'none'));
}
function paintNav() {
  const V = viewer, list = navList(V.card), i = list.indexOf(V.card), n = list.length;
  const on = n > 1 && i >= 0;
  V.prev.hidden = V.next.hidden = V.count.hidden = !on;
  V.v.dataset.nav = on ? '1' : '';
  if (!on) return;
  V.count.textContent = `${i + 1} / ${n}`;
  V.count.setAttribute('aria-label', t('media.pv.pos', { n: i + 1, of: n }));
  V.prev.disabled = i === 0; V.next.disabled = i === n - 1;
  V.prev.setAttribute('aria-label', t('media.pv.prev')); V.prev.title = t('media.pv.prev');
  V.next.setAttribute('aria-label', t('media.pv.next')); V.next.title = t('media.pv.next');
}
function pausePlayers(V) { for (const p of V.stage.querySelectorAll('audio,video')) { try { p.pause(); } catch { /* gone */ } } }
/** ← / → / a swipe: the previous / next file of the same reply. → true when it moved. */
export function step(d) {
  const V = viewer;
  if (!V || V.v.hidden || !V.card) return false;
  const list = navList(V.card), i = list.indexOf(V.card), c = list[i + d];
  if (i < 0 || !c) { edge(d); return false; }
  pausePlayers(V);
  const m = c._m;
  if (!c._blob && m) {                                     // not here yet: say so in the viewer while it loads
    const W = openViewer(m.name, c, 'doc', m);
    W.stage.replaceChildren(mk('p', 'mpvnote mvloading', t('media.loading')));
  }
  c._preview?.();
  if (V.layer) V.layer.opener = c;                         // Esc / × gives the focus back to the file last shown
  if (c.isConnected) c.scrollIntoView?.({ block: 'nearest' });
  return true;
}
function edge(d) {
  const V = viewer;
  V.hint.textContent = t(d < 0 ? 'media.pv.first' : 'media.pv.last');
  V.hint.classList.add('on');
  clearTimeout(V.hintT);
  V.hintT = setTimeout(() => V.hint.classList.remove('on'), 1400);
  V.stage.animate?.([{ transform: 'translateX(0)' }, { transform: `translateX(${d < 0 ? 18 : -18}px)` }, { transform: 'translateX(0)' }], { duration: 240, easing: 'ease-out' });
}
function loadFailed() { viewer.stage.replaceChildren(mk('p', 'mpvnote', t('media.pv.fail'))); }
/** A sideways swipe anywhere in a document / player preview (pictures: zoomable() owns their pointer). Not on a player's
 *  own controls, nor where the content itself scrolls sideways that way (a wide table, long code lines). */
function hScrolls(el, root, dx) {
  for (let n = el; n && n !== root; n = n.parentElement) {
    if (n.scrollWidth > n.clientWidth + 1) {
      const ox = getComputedStyle(n).overflowX;
      if ((ox === 'auto' || ox === 'scroll') && (dx > 0 ? n.scrollLeft > 0 : n.scrollLeft + n.clientWidth < n.scrollWidth - 1)) return true;
    }
  }
  return false;
}
function swipeable(v, stage) {
  let g = null;
  v.addEventListener('touchstart', (e) => {
    g = null;
    if (e.touches.length !== 1 || v.dataset.mode === 'image' || e.target.closest('audio,video,input,select,textarea,.mvbar')) return;
    const p = e.touches[0];
    g = { x: p.clientX, y: p.clientY, lx: p.clientX, lt: performance.now(), v: 0, mode: null, dx: 0, el: e.target };
  }, { passive: true });
  v.addEventListener('touchmove', (e) => {
    if (!g) return;
    if (e.touches.length !== 1) { reset(); return; }
    const p = e.touches[0], dx = p.clientX - g.x, dy = p.clientY - g.y;
    if (g.mode === null) {
      if (Math.abs(dx) + Math.abs(dy) < CLICK_SLOP) return;
      g.mode = Math.abs(dy) <= Math.abs(dx) * SWIPE_TAN && !hScrolls(g.el, v, dx) ? 'nav' : 'scroll';
    }
    if (g.mode !== 'nav') return;
    if (e.cancelable) e.preventDefault();               // ours: never the browser's overscroll back / forward navigation
    const now = performance.now();
    g.v = (p.clientX - g.lx) / Math.max(1, now - g.lt); g.lx = p.clientX; g.lt = now; g.dx = dx;
    stage.style.transition = 'none';
    stage.style.transform = `translateX(${dx * 0.5}px)`;
  }, { passive: false });
  const reset = () => { stage.style.transition = ''; stage.style.transform = ''; g = null; };
  v.addEventListener('touchend', () => {
    if (!g || g.mode !== 'nav') { g = null; return; }
    const { dx, v: speed } = g;
    viewer.dragAt = performance.now();
    g = null;
    if ((Math.abs(dx) >= SWIPE_PX || (Math.abs(dx) >= FLING_PX && Math.abs(speed) >= FLING_V)) && step(dx < 0 ? 1 : -1)) {
      // the next file comes in from the side the finger went to, not back from where the old one was dragged
      stage.style.transition = 'none';
      stage.style.transform = `translateX(${dx < 0 ? 32 : -32}px)`;
      void stage.offsetWidth;
    }
    stage.style.transition = ''; stage.style.transform = '';
  });
  v.addEventListener('touchcancel', reset);
}
function openViewer(label, c, mode = 'image', m = null) {
  const V = viewerEl();
  pausePlayers(V);
  if (V.cleanup) { try { V.cleanup(); } catch { /* gone */ } V.cleanup = null; }
  V.stage.style.transition = ''; V.stage.style.transform = '';
  V.card = c || null; V.m = m;
  if (mode !== 'image') V.stage.onpointerdown = V.stage.onpointermove = V.stage.onpointerup = V.stage.onpointercancel = null;
  V.v.dataset.mode = mode;
  V.v.setAttribute('aria-label', label || t('media.viewer'));
  V.title.textContent = label || '';
  V.x.setAttribute('aria-label', t('media.close')); V.x.title = t('media.close');
  V.dl.setAttribute('aria-label', t('media.act.download')); V.dl.title = t('media.act.download');
  V.dl.hidden = !(c && c._blob);
  V.note.textContent = ''; V.note.hidden = true;
  const was = !V.v.hidden;
  V.openedAt = performance.now();
  if (!was && !viewerSkipPop) { try { history.pushState({ ...history.state, ajFile: 1 }, ''); viewerPushed = true; } catch { viewerPushed = false; } }
  V.v.hidden = false; V.v.dataset.modalOpen = '1';
  paintNav();
  if (!was || !V.v.contains(document.activeElement)) V.x.focus({ preventScroll: true });
  return V;
}
export function closeViewer(fromPop = false) {
  if (!viewer || viewer.v.hidden) return;
  if (viewer.cleanup) { try { viewer.cleanup(); } catch { /* gone */ } viewer.cleanup = null; }
  pausePlayers(viewer); want = null;
  if (fromPop !== true && viewerPushed && history.state?.ajFile) { viewerSkipPop = true; history.back(); }
  viewerPushed = false;
  viewer.hint.classList.remove('on');
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
  // Move a loaded inline video: no decoder restart, pause, duplicate audio or lost position.
  const inline = m.kind === 'video' ? c?.querySelector('.mbody video') : null;
  const inlineHeight = inline?.getBoundingClientRect().height;
  const v = inline || mk(m.kind, 'mplayer');
  v.classList.add('mplayer'); v.controls = true;
  if (!inline) { v.src = url; v.preload = 'metadata'; }
  if (inline) {
    const body = c.querySelector('.mbody'), height = body.style.minHeight;
    // Keep the card's geometry while its player is in the overlay (no scroll clamping).
    body.style.minHeight = `${inlineHeight}px`;
    const placeholder = mk('span'); placeholder.setAttribute('aria-hidden', 'true'); body.appendChild(placeholder);
    V.cleanup = () => { v.classList.remove('mplayer'); body.style.minHeight = height; if (c.isConnected) body.replaceChildren(v, c._videoSurface); else v.pause(); };
  }
  if (m.kind === 'video') { v.playsInline = true; v.setAttribute('playsinline', ''); }
  V.stage.replaceChildren(v);
  if (!inline) v.play?.().catch(() => {});
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
/** Two-finger pinch, one-finger pan when zoomed, double-tap / double-click toggles 2.5×. P196: not zoomed, a one-finger
 *  sideways drag follows the finger and, past SWIPE_PX (or a fling), steps to the previous / next file; a gesture that
 *  started zoomed, or had two fingers at any point, only pans / zooms. */
function zoomable(stage, img) {
  let s = 1, x = 0, y = 0;
  const pts = new Map();
  let start = null, lastTap = 0, g = null;
  const paint = () => { img.style.transform = `translate(${x}px, ${y}px) scale(${s})`; };
  const dist = () => { const [a, b] = [...pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y); };
  stage.onpointerdown = (e) => {
    if (!pts.size) g = { x: e.clientX, y: e.clientY, s, multi: false, moved: false, lx: e.clientX, lt: performance.now(), v: 0 };
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size > 1 && g) g.multi = true;
    start = { s, x, y, d: pts.size === 2 ? dist() : 0, px: e.clientX, py: e.clientY };
    img.style.transition = 'none';
    stage.setPointerCapture?.(e.pointerId);
  };
  stage.onpointermove = (e) => {
    if (!pts.has(e.pointerId) || !start) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (g && Math.hypot(e.clientX - g.x, e.clientY - g.y) > CLICK_SLOP) g.moved = true;
    if (pts.size === 2 && start.d) s = Math.min(5, Math.max(1, start.s * dist() / start.d));
    else if (pts.size === 1 && s > 1) { x = start.x + e.clientX - start.px; y = start.y + e.clientY - start.py; }
    if (s === 1) {
      y = 0;
      x = pts.size === 1 && g && !g.multi && g.s === 1 ? e.clientX - g.x : 0;     // the picture follows the finger sideways
      if (g) { const now = performance.now(); g.v = (e.clientX - g.lx) / Math.max(1, now - g.lt); g.lx = e.clientX; g.lt = now; }
    }
    paint();
  };
  const up = (e) => {
    pts.delete(e.pointerId);
    start = pts.size ? { s, x, y, d: pts.size === 2 ? dist() : 0, px: [...pts.values()][0].x, py: [...pts.values()][0].y } : null;
  };
  stage.onpointerup = (e) => {
    const now = Date.now();
    const last = pts.size === 1, gg = g;
    if (last && gg && gg.moved && viewer) viewer.dragAt = performance.now();
    if (last && gg && !gg.multi && gg.s === 1 && s === 1 && gg.moved) {
      const dx = e.clientX - gg.x, dy = e.clientY - gg.y;
      const side = Math.abs(dy) <= Math.abs(dx) * SWIPE_TAN && (Math.abs(dx) >= SWIPE_PX || (Math.abs(dx) >= FLING_PX && Math.abs(gg.v) >= FLING_V));
      up(e); g = null;
      img.style.transition = ''; x = 0; y = 0; paint();
      if (side) step(dx < 0 ? 1 : -1);
      lastTap = 0;
      return;
    }
    if (last && !(gg && gg.moved) && now - lastTap < 300) { s = s > 1 ? 1 : 2.5; x = 0; y = 0; paint(); lastTap = 0; }
    else if (last) lastTap = gg && gg.moved ? 0 : now;
    img.style.transition = '';
    up(e);
    if (!pts.size) g = null;
  };
  stage.onpointercancel = (e) => { up(e); if (!pts.size) { g = null; if (s === 1) { x = 0; y = 0; img.style.transition = ''; paint(); } } };
  paint();
}

function viewHtml(html, m, c) {
  const name = m.name;
  const V = openViewer(name, c, 'doc', m);
  const f = document.createElement('iframe');
  for (const [k, v] of Object.entries(sandboxAttrs())) f.setAttribute(k, v);
  f.className = 'mframe';
  f.title = name;
  f.srcdoc = html + FRAME_NOBAR;                     // P273: appended, so a doctype stays first (no quirks mode)
  V.stage.replaceChildren(f);
  V.note.textContent = t('media.htmlNote'); V.note.hidden = false;
}
