// P411 (#172 + #76): the screen-help card. Browser automation on the computer stopped at a sign-in QR code, a captcha or a
// "are you human" page; the Agent (`agentj qr push` / `agentj screen ask`) or the owner's own script sent the picture here inside
// the end-to-end encrypted session. Like P114's sign-in card it is never chat history and never stored (no localStorage /
// IndexedDB): it lives in memory until it ends. The owner scans the code (long-press saves the picture), taps 「已完成」 /
// 「我扫好了」, or types a short answer (a verification code) that goes back to the caller; 「先不处理」 cancels.
// Host → help_card {id, n, kind qr|screen, mime, img, text, reply done|text, by agent|auto, from, ttl} · help_end {id, result}
// · help_err {id, why}. Phone → help_done / help_cancel {id, n} · help_text {id, n, text ≤ 200}.
import { sendApp, gen } from './session.js';
import { t, onLang } from './t.js';
import { mk, toast } from './ui.js';
import { registerLayer } from './layers.js';

const cards = new Map();            // id → card (arrival order)
let root = null, timer = 0, busy = false;
const ID = /^[0-9a-f]{32}$/;
const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const MIMES = ['image/png', 'image/jpeg', 'image/webp'];
export const IMG_MAX_B64 = 1_900_000;
export const ANSWER_MAX = 200;

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal dlgcard hlp');
  root.id = 'hlp'; root.hidden = true;
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, rank: 55,
    fallback: () => document.getElementById('input') || document.getElementById('setBtn'),
    close: () => { if (root.classList.contains('zoom')) { root.classList.remove('zoom'); return; } $('hlp-later')?.click(); } });
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'hlp-title');
  const box = mk('div', 'box');
  const node = (tag, id, cls) => { const n = mk(tag, cls); n.id = id; return n; };
  const frame = node('div', 'hlp-frame', 'hlp-frame');
  const img = node('img', 'hlp-img', 'hlp-img');
  img.decoding = 'async'; img.draggable = true;
  frame.appendChild(img);
  frame.addEventListener('click', () => { if (root.dataset.kind === 'screen' || root.classList.contains('zoom')) root.classList.toggle('zoom'); });
  const form = node('form', 'hlp-form', 'hlp-form');
  const label = node('label', 'hlp-label', 'hlp-label');
  const input = node('input', 'hlp-input', 'aj-input hlp-input');
  input.type = 'text'; input.maxLength = ANSWER_MAX; input.autocomplete = 'one-time-code'; input.spellcheck = false;
  input.setAttribute('autocapitalize', 'off'); label.htmlFor = 'hlp-input';
  const send = node('button', 'hlp-send', 'aj-btn aj-btn--primary'); send.type = 'submit';
  form.append(label, input, send);
  form.addEventListener('submit', (e) => { e.preventDefault(); answer(); });
  input.addEventListener('input', () => { send.disabled = busy || !input.value.trim(); });
  const acts = mk('div', 'hlp-acts');
  const done = node('button', 'hlp-done', 'aj-btn aj-btn--primary'); done.type = 'button';
  acts.append(done);
  const later = node('button', 'hlp-later', 'aj-btn aj-btn--quiet hlp-later'); later.type = 'button';
  box.append(node('p', 'hlp-kicker', 'elev-kicker'), node('h2', 'hlp-title'), node('p', 'hlp-text', 'hlp-text'), frame,
    node('p', 'hlp-left', 'elev-left'), node('p', 'hlp-note', 'elev-note'), form, acts, node('p', 'hlp-privacy', 'elev-note hlp-privacy'),
    later, node('p', 'hlp-more', 'elev-note hlp-more'));
  root.appendChild(box);
  document.body.appendChild(root);
  done.addEventListener('click', () => act('help_done'));
  later.addEventListener('click', () => act('help_cancel'));
  onLang(() => render());
  return root;
}
const $ = (id) => root.querySelector('#' + id);

/** `help_card`: one card; a re-sent one (after a reconnect) replaces the old with the same id. */
export function add(m) {
  if (!ID.test(m.id || '') || !ID.test(m.n || '') || !['qr', 'screen'].includes(m.kind) || !MIMES.includes(m.mime)) return;
  if (typeof m.img !== 'string' || !m.img || m.img.length > IMG_MAX_B64 || !/^[A-Za-z0-9+/=]+$/.test(m.img)) return;
  const text = typeof m.text === 'string' ? m.text.slice(0, 120) : '';
  const ttl = Number.isInteger(m.ttl) && m.ttl >= 0 && m.ttl <= 900 ? m.ttl : 120;
  const prev = cards.get(m.id);
  cards.set(m.id, { id: m.id, n: m.n, kind: m.kind, img: `data:${m.mime};base64,${m.img}`, text,
    reply: m.reply === 'text' ? 'text' : 'done', by: m.by === 'agent' ? 'agent' : 'auto',
    from: typeof m.from === 'string' && NAME.test(m.from) ? m.from : '', deadline: Date.now() + ttl * 1000, draft: prev ? prev.draft : '' });
  busy = false;
  render();
}

/** `help_end`: answered here or on another remote, expired, the page moved on, withdrawn, stopped. */
export function end(m) {
  const c = cards.get(m.id);
  if (!c) return;
  cards.delete(m.id);
  busy = false;
  if (m.result === 'page_changed') toast(t('p411.pageChanged'), 3600);
  else if (m.result === 'expired') toast(t('p411.expiredToast'), 3600);
  render();
}

/** `help_err`: the answer was refused (looks like a secret / bad shape) or the card is gone at the computer. */
export function failed(m) {
  busy = false;
  const why = ['secret', 'gone', 'shape'].includes(m.why) ? m.why : 'shape';
  if (why === 'gone' && m.id) cards.delete(m.id);
  toast(t('p411.err.' + why), 4800);
  render();
}

export function clear() {
  cards.clear(); busy = false;
  if (root) { root.hidden = true; root.classList.remove('zoom'); const i = $('hlp-img'); if (i) i.removeAttribute('src'); const f = $('hlp-input'); if (f) f.value = ''; }
  clearTimeout(timer);
}
export const openCount = () => cards.size;
export const ids = () => [...cards.keys()];                                                          // P203 (§29.1)
export function prune(gone) { const now = Date.now(); for (const c of [...cards.values()]) if (gone(c.id)) end({ id: c.id, result: c.deadline <= now ? 'expired' : 'gone' }); }

let shown = null;
function render() {
  build();
  clearTimeout(timer);
  const c = cards.values().next().value;
  if (!c) { root.hidden = true; root.classList.remove('zoom'); $('hlp-img').removeAttribute('src'); document.body.dataset.hlp = '0'; shown = null; return; }
  if (shown && shown !== c.id) { const old = cards.get(shown); if (old) old.draft = $('hlp-input').value; }
  const fresh = shown !== c.id;
  shown = c.id;
  document.body.dataset.hlp = '1';
  root.hidden = false;
  root.dataset.kind = c.kind;
  if (fresh) root.classList.remove('zoom');
  $('hlp-kicker').textContent = t(c.kind === 'qr' ? 'p411.kickerQr' : 'p411.kickerScreen') + ' · '
    + (c.by === 'agent' ? t('p411.fromAgent') : t('p411.fromAuto', { name: c.from || '—' }));
  $('hlp-title').textContent = t(c.kind === 'qr' ? 'p411.titleQr' : 'p411.titleScreen');
  $('hlp-text').textContent = c.text;
  $('hlp-text').hidden = !c.text;
  if ($('hlp-img').getAttribute('src') !== c.img) { $('hlp-img').src = c.img; }
  $('hlp-img').alt = t(c.kind === 'qr' ? 'p411.altQr' : 'p411.altScreen');
  $('hlp-note').textContent = t(c.kind === 'qr' ? 'p411.noteQr' : (c.reply === 'text' ? 'p411.noteScreen' : 'p411.noteScreenDone'));
  const typed = c.reply === 'text';
  $('hlp-form').hidden = !typed;
  $('hlp-label').textContent = t('p411.answerLabel');
  $('hlp-input').placeholder = t('p411.answerHint');
  if (fresh) $('hlp-input').value = c.draft || '';
  $('hlp-send').textContent = t('p411.send');
  $('hlp-send').disabled = busy || !$('hlp-input').value.trim();
  $('hlp-input').disabled = busy;
  $('hlp-done').textContent = t(c.kind === 'qr' ? 'p411.doneQr' : 'p411.done');
  $('hlp-done').disabled = busy;
  $('hlp-done').classList.toggle('aj-btn--primary', !typed);
  $('hlp-later').textContent = t('p411.later');
  $('hlp-later').disabled = busy;
  $('hlp-privacy').textContent = t('p411.privacy');
  $('hlp-more').textContent = cards.size > 1 ? t('p411.more', { n: cards.size - 1 }) : '';
  $('hlp-more').hidden = cards.size < 2;
  paintLeft(c);
}
function paintLeft(c) {
  const left = Math.max(0, Math.ceil((c.deadline - Date.now()) / 1000));
  $('hlp-left').textContent = left > 0 ? t('p411.left', { m: Math.floor(left / 60), s: String(left % 60).padStart(2, '0') }) : t('p411.expired');
  $('hlp-left').classList.toggle('low', left > 0 && left <= 20);
  if (left > 0) timer = setTimeout(() => { if (cards.get(c.id) === c) paintLeft(c); }, 1000);
}

async function send(obj) {
  const g = gen();
  if (!g) { toast(t('elev.net'), 3000); return; }
  busy = true; render();
  try { await sendApp(obj, g); } catch { busy = false; render(); toast(t('elev.fail'), 3000); }
}
function act(kind) {
  const c = cards.values().next().value;
  if (!c || busy) return;
  return send({ t: kind, id: c.id, n: c.n });
}
function answer() {
  const c = cards.values().next().value;
  const text = ($('hlp-input').value || '').trim();
  if (!c || busy || !text || c.reply !== 'text') return;
  return send({ t: 'help_text', id: c.id, n: c.n, text: text.slice(0, ANSWER_MAX) });
}
