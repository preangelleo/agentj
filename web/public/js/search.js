// P188 搜索聊天记录 (PROTOCOL §28, ADR-P188). The header's 🔍 (or ⌘K / Ctrl+K) opens a dark centred card: a Google-like box over
// every page of this session — the owner's input, quotes, attachment names and the Agent's replies. Pages already on the phone are
// searched at once; when older pages exist only on the computer, the computer's `hist_find` adds them a moment later. Ranking
// (proto/search.js, the same on both ends) or newest first. A hit → the card closes, the deck turns to that page and it flashes.
//
// Every search is one cached result page (≤ PAGES_MAX pages, ≤ BYTES_MAX in total, least recently used goes first); 🔍 again
// returns to the last one with its scroll position; ← → / a sideways swipe / the ‹ › buttons step through the earlier ones
// (「第 N/共 M 次搜索：关键词」). The cache is sealed in IndexedDB like the drafts (store.js), for this computer and this session
// only: a /clear (a new epoch), unpairing or a revocation empties it.
import { terms, fieldsOf, scoreFields, recency, snippet, marks, Q_MAX } from '../proto/search.js';
import { t } from './t.js';
import { el, toast } from './ui.js';
import { getSealed, putSealed } from './store.js';

export const PAGES_MAX = 20;
export const BYTES_MAX = 2 * 1024 * 1024;     // estimated as JSON length × 2 (UTF-16)
const ROWS_MAX = 120;                         // rows drawn per page
const LOCAL_MS = 120, HOST_MS = 450;          // typing → local results / → the computer's search
const SWIPE_PX = 56;
const FLASH_MS = 1600;

let X = null;                                 // ctx from relay.js
let pages = [];                               // [{id, q, sort, epoch, hits, host, hostN, more, scroll, used}]
let at = -1;                                  // the page on screen
let sealed = true;                            // the page on screen is finished: typing starts a new one
let serial = 0, localTimer = 0, hostTimer = 0, saveTimer = 0, restored = null, restoring = null;
let sel = -1;
const now = () => Date.now() / 1000;

const box = () => el('srch');
export const isOpen = () => !!box() && !box().hidden;

function sizeOf(p) { try { return JSON.stringify(p).length * 2; } catch { return 0; } }
function evict() {
  const keep = pages[at];
  while (pages.length > PAGES_MAX || (pages.length > 1 && pages.reduce((n, p) => n + sizeOf(p), 0) > BYTES_MAX)) {
    let lru = -1;
    for (let i = 0; i < pages.length; i++) if (pages[i] !== keep && (lru < 0 || pages[i].used < pages[lru].used)) lru = i;
    if (lru < 0) break;
    pages.splice(lru, 1);
  }
  at = pages.indexOf(keep);
}
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const host = X.hostId();
    if (!host) return;
    putSealed("srch", { v: 1, host, epoch: X.epoch(), at, pages: pages.filter((p) => p.q.trim()) }).catch(() => {});
  }, 400);
}
/** The sealed cache of this computer and this session, once (the epoch is known only after the first hist_meta). */
async function restore() {
  if (restored !== null) return;
  if (!restoring) restoring = getSealed('srch').then((r) => { restored = r || false; }).catch(() => { restored = false; });
  await restoring;
  const r = restored;
  if (!r || r.v !== 1 || r.host !== X.hostId() || r.epoch !== X.epoch() || !Array.isArray(r.pages) || pages.length) return;
  pages = r.pages.filter((p) => p && typeof p.q === 'string' && Array.isArray(p.hits)).slice(-PAGES_MAX)
    .map((p) => ({ ...p, id: ++serial, host: p.host === 'wait' ? 'fail' : p.host }));
  at = Math.min(pages.length - 1, Number.isInteger(r.at) ? r.at : pages.length - 1);
  evict();
}

/** Empty the cache (a /clear on any harness, unpair, revoke). */
export function clearAll(persist = true) {
  pages = []; at = -1; sealed = true; sel = -1; restored = false;
  clearTimeout(localTimer); clearTimeout(hostTimer);
  if (persist) putSealed("srch", { v: 1, host: null, epoch: 0, at: -1, pages: [] }).catch(() => {});
  if (isOpen()) paint();
}
/** The history moved to another epoch: a cache of the old one goes. */
export function onEpoch(ep) {
  if (pages.some((p) => p.epoch && p.epoch !== ep)) clearAll();
}

// ---------------------------------------------------------------- searching
function localHits(ts) {
  const out = [];
  for (const p of X.pages()) {
    if (p.id === null || p.id === undefined) continue;
    const f = fieldsOf(p.source, p.reply);
    const r = scoreFields(f, ts);
    if (!r) continue;
    const snip = {};
    for (const k of r.where) snip[k] = snippet(f[k], ts);
    const s = p.source || {};
    out.push({ id: p.id, ts: p.ts, score: r.score, where: r.where, snip, local: true,
      src: { k: s.k || 'sys', dev: s.dev || null, name: s.name || '', local: s.local === true, ...(s.via ? { via: s.via } : {}) } });
  }
  return out;
}
function ranked(p) {
  const n = now();
  const hs = p.hits.slice();
  if (p.sort === 'time') hs.sort((a, b) => b.id - a.id);
  else hs.sort((a, b) => (b.score + recency(b.ts, n)) - (a.score + recency(a.ts, n)) || b.id - a.id);
  return hs;
}
function runLocal(p) {
  const ts = terms(p.q);
  const mine = ts.length ? localHits(ts) : [];
  const ids = new Set(mine.map((h) => h.id));
  p.hits = mine.concat(p.hits.filter((h) => !h.local && !ids.has(h.id)));
}
async function runHost(p) {
  const ts = terms(p.q);
  if (!ts.length) { p.host = 'local'; return; }
  if (!X.olderLeft()) { p.host = 'local'; p.hits = p.hits.filter((h) => h.local); return; }
  if (!X.canFind()) { p.host = 'old'; return; }
  p.host = 'wait';
  const q = p.q, sort = p.sort, ep = X.epoch();
  if (isOpen() && pages[at] === p) paintStatus(p);
  const r = await X.find(q, sort);
  if (!pages.includes(p) || p.q !== q || p.sort !== sort) return;          // typed on / re-sorted since
  if (!r || r.error || (r.epoch && r.epoch !== ep)) { p.host = 'fail'; }
  else {
    const mine = new Set(p.hits.filter((h) => h.local).map((h) => h.id));
    p.hits = p.hits.filter((h) => h.local).concat(r.hits.filter((h) => !mine.has(h.id)).map((h) => ({ ...h, local: false })));
    p.host = 'done'; p.hostN = r.n; p.more = r.more;
  }
  evict(); save();
  if (isOpen() && pages[at] === p) paint(true);
}

function current() {
  if (at >= 0 && pages[at]) return pages[at];
  return null;
}
function newPage(q) {
  const p = { id: ++serial, q, sort: 'score', epoch: X.epoch(), hits: [], host: 'local', hostN: 0, more: false, scroll: 0, used: Date.now() };
  pages.push(p); at = pages.length - 1; sealed = false;
  return p;
}
/** Finish the page on screen: an empty one goes; an earlier page with the same words goes (this one is the newer). */
function seal() {
  const p = current();
  sealed = true;
  if (!p) return;
  const list = box() && el('srchList');
  if (list) p.scroll = list.scrollTop;
  p.used = Date.now();
  if (!terms(p.q).length) { pages.splice(at, 1); at = pages.length - 1; save(); return; }
  const key = terms(p.q).join(' ');
  const dup = pages.findIndex((x) => x !== p && terms(x.q).join(' ') === key);
  if (dup >= 0) { pages.splice(dup, 1); at = pages.indexOf(p); }
  evict(); save();
}
function onType() {
  const q = el('srchInput').value.slice(0, Q_MAX);
  let p = current();
  if (!p || sealed) {
    if (p && q === p.q) return;
    if (p) seal();
    p = newPage(q);
  }
  p.q = q; p.used = Date.now(); p.scroll = 0; sel = -1;
  clearTimeout(localTimer); clearTimeout(hostTimer);
  localTimer = setTimeout(() => { runLocal(p); p.host = terms(q).length && X.olderLeft() ? (X.canFind() ? 'wait' : 'old') : 'local'; paint(true); }, LOCAL_MS);
  hostTimer = setTimeout(() => { runHost(p); }, HOST_MS);
  paintHead();
}
function setSort(sort) {
  const p = current();
  if (!p || p.sort === sort) return;
  p.sort = sort; p.scroll = 0; sel = -1;
  if (p.host === 'done' && p.more) runHost(p);     // the computer only sent its best 50 — ask it again in the new order
  save(); paint(true);
}

// ---------------------------------------------------------------- painting
const pad = (n) => String(n).padStart(2, '0');
function when(ts) {
  if (typeof ts !== 'number') return '';
  const d = new Date(ts * 1000), today = new Date();
  const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
  if (d.toDateString() === today.toDateString()) return hm;
  return (d.getFullYear() === today.getFullYear() ? '' : d.getFullYear() + '-') + (d.getMonth() + 1) + '-' + d.getDate() + ' ' + hm;
}
function hl(node, text, ts) {
  for (const [s, on] of marks(text, ts)) {
    if (on) { const m = document.createElement('mark'); m.textContent = s; node.appendChild(m); }
    else node.appendChild(document.createTextNode(s));
  }
}
function row(h, ts) {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'srhit'; b.dataset.id = String(h.id); b.dataset.local = h.local ? '1' : '0';
  b.setAttribute('role', 'option');
  const meta = document.createElement('span'); meta.className = 'srmeta';
  const who = X.sourceOf(h.src);
  const parts = [who.label];
  if (h.src.k === 'phone' && h.src.name && who.label !== h.src.name) parts.push(h.src.name);
  meta.textContent = parts.join(' · ') + (h.ts ? ' · ' + when(h.ts) : '');
  b.appendChild(meta);
  const order = ['in', 'quote', 'att', 'reply'].filter((f) => h.where.includes(f) && h.snip[f]);
  for (const f of order.slice(0, 2)) {
    const line = document.createElement('span'); line.className = 'srsnip'; line.dataset.f = f;
    const tag = document.createElement('b'); tag.textContent = t('r.find.f.' + f);
    line.appendChild(tag);
    const tx = document.createElement('span'); hl(tx, h.snip[f], ts); line.appendChild(tx);
    b.appendChild(line);
  }
  return b;
}
function statusText(p, n) {
  const k = p.hits.filter((h) => !h.local).length;
  if (!terms(p.q).length) return '';
  if (p.host === 'wait') return t('r.find.waiting', { n });
  if (p.host === 'fail') return t('r.find.fail', { n });
  if (p.host === 'old') return t('r.find.old', { n });
  if (p.host === 'done' && p.more) return t('r.find.more', { n, total: Math.max(n, p.hostN) });
  if (k) return t('r.find.withHost', { n, k });
  return n ? t('r.find.count', { n }) : '';
}
function paintStatus(p) {
  el('srchStatus').textContent = statusText(p, p.hits.length);
}
function paintHead() {
  const p = current(), m = pages.filter((x) => terms(x.q).length).length;
  const n = p ? pages.slice(0, at + 1).filter((x) => terms(x.q).length).length : 0;
  const named = p && terms(p.q).length;
  el('srchPage').textContent = named ? t('r.find.page', { n, m, q: p.q.trim() }) : (m ? t('r.find.pageNew', { m }) : t('r.find.title'));
  el('srchPrev').disabled = !(at > 0);
  el('srchNext').disabled = !(at >= 0 && at < pages.length - 1);
  for (const b of el('srchSort').querySelectorAll('button')) b.setAttribute('aria-pressed', String(!!p && p.sort === b.dataset.sort || (!p && b.dataset.sort === 'score')));
}
function paint(keepScroll = false) {
  const p = current();
  const list = el('srchList');
  const input = el('srchInput');
  if (document.activeElement !== input || !p || input.value !== p.q) input.value = p ? p.q : '';
  paintHead();
  const ts = p ? terms(p.q) : [];
  const hs = p ? ranked(p) : [];
  const nodes = [];
  if (!ts.length) {
    const e = document.createElement('p'); e.className = 'srempty';
    e.textContent = t('r.find.hint');
    nodes.push(e);
    if (pages.some((x) => terms(x.q).length)) { const s = document.createElement('p'); s.className = 'srempty'; s.textContent = t('r.find.hintSwipe'); nodes.push(s); }
  } else if (!hs.length && p.host !== 'wait') {
    const e = document.createElement('p'); e.className = 'srempty'; e.textContent = t('r.find.none'); nodes.push(e);
  }
  for (const h of hs.slice(0, ROWS_MAX)) nodes.push(row(h, ts));
  const top = list.scrollTop;
  list.replaceChildren(...nodes);
  list.scrollTop = keepScroll ? top : (p ? p.scroll : 0);
  if (p) paintStatus(p); else el('srchStatus').textContent = '';
  if (sel >= 0) focusRow(sel, false);
}

// ---------------------------------------------------------------- open / close / move
export async function open() {
  if (!X) return;
  X.beforeOpen();
  await restore();
  box().hidden = false;
  document.body.dataset.srch = '1';
  sealed = true; sel = -1;
  paint();
  const input = el('srchInput');
  if (!current() || !matchMedia('(pointer:coarse)').matches) { input.focus({ preventScroll: true }); input.select(); }
  else el('srchList').focus({ preventScroll: true });
}
export function close() {
  if (!isOpen()) return;
  seal();
  clearTimeout(localTimer);
  box().hidden = true;
  delete document.body.dataset.srch;
}
export function toggle() { if (isOpen()) close(); else open(); }
function step(d) {
  const cur = current();
  seal();                                     // an empty page being typed into goes; its neighbours stay where they were
  const i = cur && pages.includes(cur) ? pages.indexOf(cur) + d : (d < 0 ? pages.length - 1 : pages.length);
  if (i < 0 || i >= pages.length) { paint(); return; }
  at = i; sealed = true; sel = -1;
  pages[at].used = Date.now();
  paint();
  save();
}
function rows() { return [...el('srchList').querySelectorAll('.srhit')]; }
function focusRow(i, move = true) {
  const rs = rows();
  if (!rs.length) { sel = -1; return; }
  sel = Math.max(0, Math.min(rs.length - 1, i));
  rs.forEach((r, k) => r.setAttribute('aria-selected', String(k === sel)));
  if (move) rs[sel].focus({ preventScroll: false });
}
async function pick(id) {
  const p = current();
  const h = p && p.hits.find((x) => x.id === id);
  close();
  const ok = await X.show(id);
  if (!ok) { toast(t('r.find.gone'), 2400); return; }
  X.flash(id, p ? terms(p.q) : [], h ? h.where : []);
}

/** The deck's page: flash the two cards and mark the first hit in the words (cleared on the next render). */
export function flashCards(ts, where) {
  for (const id of ['om', 'rm']) {
    const n = el(id);
    if (!n) continue;
    n.classList.remove('srflash');
    void n.offsetWidth;
    n.classList.add('srflash');
    setTimeout(() => n.classList.remove('srflash'), FLASH_MS);
  }
  const target = where.includes('reply') || !where.length ? el('words') : (where.includes('in') ? el('omText') : null);
  if (!target || !ts.length) return;
  const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const seg = marks(n.nodeValue, ts);
    let off = 0;
    for (const [s, on] of seg) {
      if (on) {
        const r = document.createRange();
        r.setStart(n, off); r.setEnd(n, off + s.length);
        const m = document.createElement('mark'); m.className = 'srmark';
        try { r.surroundContents(m); } catch { return; }
        m.scrollIntoView({ block: 'center', behavior: 'smooth' });
        setTimeout(() => { if (m.isConnected) m.replaceWith(...m.childNodes); }, FLASH_MS * 2);
        return;
      }
      off += s.length;
    }
  }
}

// ---------------------------------------------------------------- wiring
export function init(ctx) {
  X = ctx;
  const input = el('srchInput'), list = el('srchList');
  el('srchBtn').addEventListener('click', toggle);   // @ix app.search
  el('srchClose').addEventListener('click', close);   // @ix search.close
  el('srchPrev').addEventListener('click', () => step(-1));   // @ix search.history (+ ← → and the swipe below)
  el('srchNext').addEventListener('click', () => step(1));
  box().addEventListener('click', (e) => { if (e.target === box()) close(); });
  input.addEventListener('input', onType);
  input.addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter') { e.preventDefault(); const rs = rows(); if (rs.length) pick(+rs[Math.max(0, sel)].dataset.id); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); focusRow(0); }
  });
  for (const b of el('srchSort').querySelectorAll('button')) b.addEventListener('click', () => setSort(b.dataset.sort));
  list.addEventListener('click', (e) => {   // @ix search.results (+ ↑ ↓ Enter)
    const b = e.target.closest && e.target.closest('.srhit'); if (b) pick(+b.dataset.id); });
  list.addEventListener('scroll', () => { const p = current(); if (p) p.scroll = list.scrollTop; }, { passive: true });
  box().addEventListener('keydown', (e) => {
    if (e.target === input || e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); step(e.key === 'ArrowLeft' ? -1 : 1); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (e.key === 'ArrowUp' && sel <= 0) { sel = -1; rows().forEach((r) => r.setAttribute('aria-selected', 'false')); input.focus(); return; }
      focusRow(sel + (e.key === 'ArrowDown' ? 1 : -1));
      return;
    }
    if (e.key === 'Enter' && e.target.closest && e.target.closest('.srhit')) { e.preventDefault(); pick(+e.target.closest('.srhit').dataset.id); }
  });
  // a sideways swipe on the results = the previous / next search (vertical = scroll the results)
  let sw = null;
  const card = el('srchCard');
  card.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') sw = { x: e.clientX, y: e.clientY, id: e.pointerId }; }, { passive: true });
  card.addEventListener('pointercancel', () => { sw = null; });
  card.addEventListener('pointerup', (e) => {
    if (!sw || e.pointerId !== sw.id) return;
    const dx = e.clientX - sw.x, dy = e.clientY - sw.y;
    sw = null;
    if (Math.abs(dx) >= SWIPE_PX && Math.abs(dy) < Math.abs(dx) * 0.58) step(dx > 0 ? -1 : 1);   // within 30°; right = the earlier search
  }, { passive: true });
  card.addEventListener('touchmove', (e) => { if (sw && e.touches.length === 1) { const dx = e.touches[0].clientX - sw.x, dy = e.touches[0].clientY - sw.y; if (Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy) * 1.7) e.preventDefault(); } }, { passive: false });
}
/** For the test: the cache as it stands. */
export function debug() { return { at, sealed, pages: pages.map((p) => ({ q: p.q, sort: p.sort, n: p.hits.length, host: p.host, ids: ranked(p).map((h) => h.id), scroll: p.scroll, bytes: sizeOf(p) })) }; }
