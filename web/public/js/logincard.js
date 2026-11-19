// P114 (P90 ADR-A194 §5): the sign-in QR card. The computer's own browser shows a site's sign-in QR code; the host crops ONLY that
// code and sends it here inside the end-to-end encrypted session, as this card — never a chat message, never stored (no
// localStorage / IndexedDB / history), gone when it ends. ≤ 120 s; 「刷新」 asks the computer for a fresh one. Success is decided
// by the computer's browser reaching the signed-in page — 「我扫好了」 only asks it to look now. Telegram: a configured bot
// gets a text reminder; the QR image goes there only after the owner taps 「也发到 Telegram」 here (Telegram can see it).
import { sendApp, gen } from './session.js';
import { t, onLang } from './t.js';
import { mk, toast } from './ui.js';
import { registerLayer, TOP_RANK } from './layers.js';

const cards = new Map();            // id → card (arrival order)
const ended = new Map();            // site → expired original card or a read-only request failure
const unsafeLegacy = new Set();     // a retired refresh has no correlation id on older hosts
const REASONS = ['browser_unavailable', 'page_not_ready', 'qr_not_found', 'capture_timeout', 'capture_blank'];
const OLD_RESULTS = ['no_qr', 'no_browser', 'no_device', 'busy', 'disabled', 'stopped', 'host_login', 'host_login_unavailable', 'already'];
let root = null, timer = 0, busy = false;

function retire(site, replaced = false) {
  const entry = ended.get(site);
  if (entry?.pending) {
    clearTimeout(entry.timer);
    if (!replaced) unsafeLegacy.add(site);
  }
  ended.delete(site);
}

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal dlgcard lqr');
  root.id = 'lqr'; root.hidden = true;
  // P142 (A16): a layer — ESC = the card's own quiet way out (「稍后」 while it waits, 「关闭」 once ended); shortcuts stay off
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, rank: TOP_RANK, fallback: () => document.getElementById('input') || document.getElementById('setBtn'),
    close: () => { const b = $('lqr-later'); (b && !b.hidden ? b : $('lqr-close'))?.click(); } });
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'lqr-title');
  const box = mk('div', 'box');
  const node = (tag, id, cls) => { const n = mk(tag, cls, undefined, cls?.split(/\s+/).includes('aj-action') ? 'aj-action' : 'aj-auth-card', 'dialog'); n.id = id; return n; };
  const frame = node('div', 'lqr-frame', 'lqr-frame');
  const img = node('img', 'lqr-img', 'lqr-img');
  img.decoding = 'sync'; img.draggable = true;
  frame.appendChild(img);
  const acts = mk('div', 'lqr-acts');
  const check = node('button', 'lqr-check', 'aj-action aj-action--primary'); check.type = 'button';
  const refresh = node('button', 'lqr-refresh', 'aj-action aj-action--primary'); refresh.type = 'button'; refresh.hidden = true;
  const close = node('button', 'lqr-close', 'aj-action'); close.type = 'button'; close.hidden = true;
  acts.append(check, refresh, close);
  const tg = node('button', 'lqr-tg', 'aj-action aj-action--quiet lqr-tg'); tg.type = 'button'; tg.hidden = true;
  const later = node('button', 'lqr-later', 'aj-action aj-action--quiet lqr-later'); later.type = 'button';
  box.append(node('p', 'lqr-kicker', 'elev-kicker'), node('h2', 'lqr-title'), frame, node('p', 'lqr-left', 'elev-left'),
    node('p', 'lqr-note', 'elev-note'), acts, node('p', 'lqr-tgnote', 'elev-note lqr-tgnote'), tg, later);
  root.appendChild(box);
  document.body.appendChild(root);
  check.addEventListener('click', () => act('login_qr_check'));
  later.addEventListener('click', () => act('login_qr_cancel'));
  refresh.addEventListener('click', () => doRefresh());
  close.addEventListener('click', () => {
    if (cards.size || !ended.size) return;
    retire(ended.keys().next().value);
    render();
  });
  tg.addEventListener('click', () => toggleTg());
  document.addEventListener?.('visibilitychange', () => { if (!document.hidden) render(); });
  onLang(() => render());
  return root;
}
const $ = (id) => root.querySelector('#' + id);
const ID = /^[0-9a-f]{32}$/;
const SITE = /^[a-z0-9][a-z0-9-]{0,47}$/;

/** A card from the host (`login_qr`): one per site; a re-sent one (after a reconnect) replaces the old. */
export function add(m) {
  if (!ID.test(m.id || '') || !ID.test(m.n || '') || !SITE.test(m.site || '')) return;
  if (typeof m.title !== 'string' || m.title.length > 60 || typeof m.img !== 'string' || m.img.length > 700000) return;
  if (!/^[A-Za-z0-9+/=]+$/.test(m.img)) return;
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 120 ? m.ttl : 120;
  const deadline = Math.min(cards.get(m.id)?.deadline ?? Infinity, Date.now() + ttl * 1000);
  cards.set(m.id, { id: m.id, n: m.n, site: m.site, title: m.title, img: 'data:image/png;base64,' + m.img,
    deadline, tg: ['none', 'text', 'qr'].includes(m.tg) ? m.tg : 'none',
    channel: typeof m.channel === 'string' && /^[\w .-]{1,24}$/.test(m.channel) ? m.channel : '' });
  retire(m.site, true);
  busy = false;
  render();
}

/** `login_qr_done`: done (the browser confirmed the sign-in) · expired · cancelled · stopped · gone. */
export function done(m) {
  const c = cards.get(m.id);
  if (!c) return;
  cards.delete(m.id);
  busy = false;
  if (m.result === 'done') toast(t('lqr.signedIn', { site: c.title }), 3600);
  else if (m.result === 'expired') {
    retire(c.site);
    ended.set(c.site, { id: c.id, title: c.title });
  }
  render();
}

/** `login_qr_state`: still waiting (after 「我扫好了」) and / or the Telegram setting changed. */
export function state(m) {
  const c = cards.get(m.id);
  if (!c) return;
  if (['none', 'text', 'qr'].includes(m.tg)) c.tg = m.tg;
  if (busy && m.state === 'waiting') toast(t('lqr.waiting'), 3600);
  busy = false;
  render();
}

/** `login_qr_err`: the card is gone at the computer, or a refresh could not make a new one. */
export function failed(m) {
  if (!m.site && m.why === 'gone' && cards.has(m.id)) {
    done({ id: m.id, result: 'gone' });
    return;
  }
  const g = gen();
  if (!g || typeof m.site !== 'string' || !SITE.test(m.site)) return;
  const reason = REASONS.includes(m.reason) ? m.reason : '';
  const entry = ended.get(m.site);
  if (m.op === 'request') {
    // Only the host's direct reply to the originating phone can open this read-only state.
    if (m.id !== '' || !reason || entry?.pending || [...cards.values()].some(c => c.site === m.site)) return;
    ended.set(m.site, { reason });
  } else {
    if (!entry?.id || entry.pending !== g) return;
    const legacy = m.op === undefined && m.reason === undefined && m.id === '' && !unsafeLegacy.has(m.site);
    if (!legacy && !(m.op === 'refresh' && m.id === entry.id)) return;
    clearTimeout(entry.timer);
    entry.pending = null;
    if (!reason && m.why === 'already') {
      retire(m.site);
      toast(t('lqr.err.already'), 4800);
    } else if (!legacy && m.why === 'gone') {
      entry.closed = true;
      entry.reason = 'gone';
    } else {
      entry.reason = reason || (OLD_RESULTS.includes(m.why) ? m.why : 'failed');
    }
  }
  render();
}

/** A connection blip (the host re-sends open cards after ready) / unpair: forget everything. */
export function clear() {
  for (const entry of ended.values()) clearTimeout(entry.timer);
  cards.clear(); ended.clear(); unsafeLegacy.clear(); busy = false;
  if (root) { root.hidden = true; const i = $('lqr-img'); if (i) i.removeAttribute('src'); }
  clearTimeout(timer);
}
export const openCount = () => cards.size;
export const ids = () => [...cards.keys()];                                                          // P203 (§29.1)
export function prune(gone) { const now = Date.now(); for (const c of [...cards.values()]) if (gone(c.id)) done({ id: c.id, result: c.deadline <= now ? 'expired' : 'gone' }); }

function render() {
  build();
  clearTimeout(timer);
  const now = Date.now();
  for (const card of cards.values()) {
    if (card.deadline > now) continue;
    cards.delete(card.id);
    retire(card.site);
    ended.set(card.site, { id: card.id, title: card.title });
    busy = false;
  }
  const c = cards.values().next().value;
  const e = c ? null : ended.values().next().value;
  if (!c && !e) { root.hidden = true; $('lqr-img').removeAttribute('src'); document.body.dataset.lqr = '0'; return; }
  document.body.dataset.lqr = '1';
  root.hidden = false;
  const title = c ? c.title : e.title;
  $('lqr-kicker').textContent = t('lqr.kicker');
  $('lqr-title').textContent = c || e?.id ? t('lqr.title', { site: title }) : t('lqr.unavailable');
  $('lqr-frame').hidden = !c;
  if (c && $('lqr-img').getAttribute('src') !== c.img) { $('lqr-img').src = c.img; $('lqr-img').alt = t('lqr.alt', { site: title }); }
  if (!c) $('lqr-img').removeAttribute('src');
  $('lqr-note').textContent = c ? t('lqr.note') : e.reason ? t('lqr.err.' + e.reason) : t('lqr.expiredNote');
  $('lqr-check').textContent = t('lqr.check');
  $('lqr-check').hidden = !c;
  $('lqr-check').disabled = busy;
  $('lqr-refresh').textContent = t('lqr.refresh');
  $('lqr-refresh').hidden = !e?.id || !!e?.closed;
  $('lqr-refresh').disabled = !!e?.pending;
  $('lqr-close').textContent = t('lqr.close');
  $('lqr-close').hidden = !!c;
  $('lqr-later').textContent = t('lqr.later');
  $('lqr-later').hidden = !c;
  $('lqr-later').disabled = busy;
  const tg = c && c.channel ? c.tg : 'none';
  $('lqr-tg').hidden = tg === 'none';
  $('lqr-tgnote').hidden = tg === 'none';
  const channel = c && c.channel ? c.channel : '';
  $('lqr-tg').textContent = t(tg === 'qr' ? 'lqr.tgOff' : 'lqr.tgOn', { channel });
  $('lqr-tgnote').textContent = t(tg === 'qr' ? 'lqr.tgOnNote' : 'lqr.tgTextNote', { channel });
  $('lqr-left').hidden = !c && !e?.id;
  if ($('lqr-left').hidden) $('lqr-left').textContent = '';
  else paintLeft(c);
}
function paintLeft(c) {
  if (!c) { $('lqr-left').textContent = t('lqr.expired'); return; }
  const left = Math.max(0, Math.ceil((c.deadline - Date.now()) / 1000));
  $('lqr-left').textContent = left > 0 ? t('lqr.left', { m: Math.floor(left / 60), s: String(left % 60).padStart(2, '0') }) : t('lqr.expired');
  const nextExpiry = Math.min(...Array.from(cards.values(), card => card.deadline));
  timer = setTimeout(render, Math.max(0, Math.min(1000, nextExpiry - Date.now())));
}

async function act(kind) {
  const c = cards.values().next().value;
  if (!c || busy) return;
  if (c.deadline <= Date.now()) { render(); return; }
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  busy = true; render();
  try { await sendApp({ t: kind, id: c.id, n: c.n }, g); } catch { busy = false; render(); toast(t('elev.fail'), 3000); }
}
async function toggleTg() {
  const c = cards.values().next().value;
  if (!c || busy) return;
  if (c.deadline <= Date.now()) { render(); return; }
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  busy = true; render();
  try { await sendApp({ t: 'login_qr_tg', id: c.id, n: c.n, on: c.tg !== 'qr' }, g); } catch { busy = false; render(); toast(t('elev.fail'), 3000); }
}
async function doRefresh() {
  if (cards.size || !ended.size) return;
  const [site, entry] = ended.entries().next().value;
  if (!entry.id || entry.pending || entry.closed) return;
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  entry.pending = g;
  render();
  const current = () => gen() === g && ended.get(site) === entry && entry.pending === g;
  entry.timer = setTimeout(() => {
    if (!current()) return;
    unsafeLegacy.add(site);
    entry.pending = null;
    entry.closed = true;
    entry.reason = 'no_reply';
    render();
  }, 60000);
  try {
    await sendApp({ t: 'login_qr_refresh', site, id: entry.id }, g);
  } catch {
    if (!current()) return;
    clearTimeout(entry.timer);
    entry.pending = null;
    render();
    toast(t('elev.fail'), 3000);
  }
}
