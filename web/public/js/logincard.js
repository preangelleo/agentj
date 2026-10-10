// P114 (P90 ADR-A194 §5): the sign-in QR card. The computer's own browser shows a site's sign-in QR code; the host crops ONLY that
// code and sends it here inside the end-to-end encrypted session, as this card — never a chat message, never stored (no
// localStorage / IndexedDB / history), gone when it ends. ≤ 120 s; 「刷新」 asks the computer for a fresh one. Success is decided
// by the computer's browser reaching the signed-in page — 「我扫好了」 only asks it to look now. Telegram: a configured bot
// gets a text reminder; the QR image goes there only after the owner taps 「也发到 Telegram」 here (Telegram can see it).
import { sendApp, gen } from './session.js';
import { t, onLang } from './t.js';
import { mk, toast } from './ui.js';

const cards = new Map();            // id → card (arrival order)
const ended = new Map();            // site → {title, result} of the last card that expired (offer 「刷新」)
let root = null, timer = 0, busy = false;

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal lqr');
  root.id = 'lqr'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'lqr-title');
  const box = mk('div', 'box');
  const node = (tag, id, cls) => { const n = mk(tag, cls); n.id = id; return n; };
  const frame = node('div', 'lqr-frame', 'lqr-frame');
  const img = node('img', 'lqr-img', 'lqr-img');
  img.decoding = 'sync'; img.draggable = true;
  frame.appendChild(img);
  const acts = mk('div', 'lqr-acts');
  const check = node('button', 'lqr-check', 'aj-btn aj-btn--primary'); check.type = 'button';
  const refresh = node('button', 'lqr-refresh', 'aj-btn aj-btn--primary'); refresh.type = 'button'; refresh.hidden = true;
  const close = node('button', 'lqr-close', 'aj-btn'); close.type = 'button'; close.hidden = true;
  acts.append(check, refresh, close);
  const tg = node('button', 'lqr-tg', 'aj-btn aj-btn--quiet lqr-tg'); tg.type = 'button'; tg.hidden = true;
  const later = node('button', 'lqr-later', 'aj-btn aj-btn--quiet lqr-later'); later.type = 'button';
  box.append(node('p', 'lqr-kicker', 'elev-kicker'), node('h2', 'lqr-title'), frame, node('p', 'lqr-left', 'elev-left'),
    node('p', 'lqr-note', 'elev-note'), acts, node('p', 'lqr-tgnote', 'elev-note lqr-tgnote'), tg, later);
  root.appendChild(box);
  document.body.appendChild(root);
  check.addEventListener('click', () => act('login_qr_check'));
  later.addEventListener('click', () => act('login_qr_cancel'));
  refresh.addEventListener('click', () => doRefresh());
  close.addEventListener('click', () => { ended.clear(); render(); });
  tg.addEventListener('click', () => toggleTg());
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
  cards.set(m.id, { id: m.id, n: m.n, site: m.site, title: m.title, img: 'data:image/png;base64,' + m.img,
    deadline: Date.now() + ttl * 1000, tg: ['none', 'text', 'qr'].includes(m.tg) ? m.tg : 'none',
    channel: typeof m.channel === 'string' && /^[\w .-]{1,24}$/.test(m.channel) ? m.channel : '' });
  ended.delete(m.site);
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
  else if (m.result === 'expired') ended.set(c.site, { title: c.title, result: 'expired' });
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
  busy = false;
  if (m.id && cards.has(m.id)) cards.delete(m.id);
  if (m.site && m.why) {
    ended.delete(m.site);
    toast(t(`lqr.err.${['no_qr', 'no_browser', 'no_device', 'busy', 'disabled', 'stopped', 'host_login', 'host_login_unavailable', 'already'].includes(m.why) ? m.why : 'failed'}`), 4800);
  }
  render();
}

/** A connection blip (the host re-sends open cards after ready) / unpair: forget everything. */
export function clear() {
  cards.clear(); ended.clear(); busy = false;
  if (root) { root.hidden = true; const i = $('lqr-img'); if (i) i.removeAttribute('src'); }
  clearTimeout(timer);
}
export const openCount = () => cards.size;

function render() {
  build();
  clearTimeout(timer);
  const c = cards.values().next().value;
  const e = c ? null : ended.entries().next().value;
  if (!c && !e) { root.hidden = true; $('lqr-img').removeAttribute('src'); document.body.dataset.lqr = '0'; return; }
  document.body.dataset.lqr = '1';
  root.hidden = false;
  const title = c ? c.title : e[1].title;
  $('lqr-kicker').textContent = t('lqr.kicker');
  $('lqr-title').textContent = t('lqr.title', { site: title });
  $('lqr-frame').hidden = !c;
  if (c && $('lqr-img').getAttribute('src') !== c.img) { $('lqr-img').src = c.img; $('lqr-img').alt = t('lqr.alt', { site: title }); }
  if (!c) $('lqr-img').removeAttribute('src');
  $('lqr-note').textContent = c ? t('lqr.note') : t('lqr.expiredNote');
  $('lqr-check').textContent = t('lqr.check');
  $('lqr-check').hidden = !c;
  $('lqr-check').disabled = busy;
  $('lqr-refresh').textContent = t('lqr.refresh');
  $('lqr-refresh').hidden = !!c;
  $('lqr-refresh').disabled = busy;
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
  paintLeft(c);
}
function paintLeft(c) {
  if (!c) { $('lqr-left').textContent = t('lqr.expired'); return; }
  const left = Math.max(0, Math.ceil((c.deadline - Date.now()) / 1000));
  $('lqr-left').textContent = left > 0 ? t('lqr.left', { m: Math.floor(left / 60), s: String(left % 60).padStart(2, '0') }) : t('lqr.expired');
  if (left > 0) timer = setTimeout(() => { if (cards.get(c.id) === c) paintLeft(c); }, 1000);
}

async function act(kind) {
  const c = cards.values().next().value;
  if (!c || busy) return;
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  busy = true; render();
  try { await sendApp({ t: kind, id: c.id, n: c.n }, g); } catch { busy = false; render(); toast(t('elev.fail'), 3000); }
}
async function toggleTg() {
  const c = cards.values().next().value;
  if (!c || busy) return;
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  busy = true; render();
  try { await sendApp({ t: 'login_qr_tg', id: c.id, n: c.n, on: c.tg !== 'qr' }, g); } catch { busy = false; render(); toast(t('elev.fail'), 3000); }
}
async function doRefresh() {
  const e = ended.entries().next().value;
  if (!e || busy) return;
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  busy = true; render();
  try { await sendApp({ t: 'login_qr_refresh', site: e[0] }, g); } catch { busy = false; render(); toast(t('elev.fail'), 3000); }
}
