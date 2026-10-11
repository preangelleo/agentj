// P150 (PROTOCOL §22) 「在办事项」: who is working in the background under the main Agent's watch. One dot per worker at the
// right of the reply card's header (like a window's corner dots), each a fixed colour pair from Leo's palette (Heavy ⇄
// Light, slowly alternating = working); one fewer when one finishes; none = hidden. A tap opens the global centred square
// dialog: 「有 N 个 Agent 在后台干活，<Agent 名> 正在替你监控」 + who · what. Display only — nothing here starts or stops work.
// Host text (who / label) is plain text (textContent); colours are an index 0..8 into app.css [data-c].
import { t, onLang } from './t.js';
import { registerLayer } from './layers.js';

export const MAX_DOTS = 6;
const COLORS = 9;
let tasks = [];
let agentName = () => 'Agent J';
const $ = (id) => document.getElementById(id);

/** The wire message → the list (bounded, plain strings, a valid colour each; garbage is dropped). */
export function sanitize(m) {
  const list = Array.isArray(m && m.tasks) ? m.tasks : [];
  const out = [];
  for (const x of list.slice(0, 12)) {
    if (!x || typeof x !== 'object') continue;
    const str = (v, n) => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, n) : '');
    out.push({
      id: str(x.id, 64), who: str(x.who, 24) || null, kind: x.kind === 'ceo' ? 'ceo' : 'temp', label: str(x.label, 40),
      lk: x.lk === 'schedule' || x.lk === 'dispatch' ? x.lk : null,
      c: Number.isInteger(x.c) && x.c >= 0 && x.c < COLORS ? x.c : out.length % COLORS,
    });
  }
  // Neighbours never share a colour (the host already keeps them apart while ≤ 9 run).
  for (let i = 1; i < out.length; i++) if (out[i].c === out[i - 1].c) out[i].c = (out[i].c + 1) % COLORS;
  return out;
}
export const who = (x) => x.who || t('busy.temp');
export const what = (x) => x.label || (x.lk ? t('busy.lk.' + x.lk) : '');

function dot(x, k, cls) {
  const i = document.createElement('i');
  i.dataset.c = String(x.c);
  if (cls) i.className = cls;
  i.style.animationDelay = (-k * 0.8) + 's';      // a gentle wave, not a synchronised blink
  return i;
}

function paintDots() {
  const b = $('busyDots');
  if (!b) return;
  b.hidden = !tasks.length;
  const shown = tasks.slice(0, tasks.length > MAX_DOTS ? MAX_DOTS - 1 : MAX_DOTS);
  b.replaceChildren(...shown.map((x, k) => dot(x, k)));
  if (shown.length < tasks.length) { const more = document.createElement('b'); more.textContent = '+' + (tasks.length - shown.length); b.append(more); }
  const label = t('busy.aria', { n: tasks.length });
  b.setAttribute('aria-label', label); b.title = label;
  b.dataset.n = String(tasks.length);
}

function paintDialog() {
  const d = $('busyDlg');
  if (!d || d.hidden) return;
  if (!tasks.length) { close(); return; }
  $('busyTitle').textContent = tasks.length === 1 ? t('busy.title1') : t('busy.title', { n: tasks.length });
  $('busySub').textContent = t('busy.sub', { a: agentName() });
  $('busyBig').replaceChildren(...tasks.slice(0, MAX_DOTS).map((x, k) => dot(x, k)));
  $('busyList').replaceChildren(...tasks.map((x) => {
    const li = document.createElement('li');
    const b = document.createElement('b'); b.textContent = who(x);
    const s = document.createElement('span'); s.textContent = what(x);
    li.append(dot(x, 0, 'still'), b, s);
    return li;
  }));
  d.querySelector('.sq').dataset.n = String(Math.min(tasks.length, 9));
}

export function open() {
  if (!tasks.length) return;
  $('busyDlg').hidden = false;
  paintDialog();
  $('busyClose').focus({ preventScroll: true });
}
export function close() { const d = $('busyDlg'); if (d) d.hidden = true; }

/** `{"t":"busy","tasks":[…],"by":…}` from the computer. */
export function setBusy(m) { tasks = sanitize(m); paintDots(); paintDialog(); }
export const current = () => tasks.slice();

export function init({ name } = {}) {
  if (name) agentName = name;
  $('busyDots').addEventListener('click', (e) => { e.stopPropagation(); open(); });
  $('busyClose').addEventListener('click', close);
  $('busyDlg').addEventListener('click', (e) => { if (e.target === e.currentTarget) close(); });   // outside the square
  registerLayer({ element: () => $('busyDlg'), open: () => !$('busyDlg').hidden, close, fallback: () => $('busyDots'), rank: 5 });
  onLang(() => { paintDots(); paintDialog(); });
  paintDots();
}
