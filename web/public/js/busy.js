// P150 (PROTOCOL §22) 「在办事项」: who is working in the background under the main Agent's watch. P154 追加 10 (owner,
// 2026-10-12): one original pixel robot per worker at the right of the reply card's header, 18 px high (native 18×18 art,
// assets/robots-18.png), dancing 12 steps; a new worker pops in, a finished one bows and floats away and the rest close
// up. The explainer dialog shows the same robots 24 px high (the 12×12 art at exactly 2×). 追加 11: a library of 16 robots
// (assets/robots.json → js/robots.js; colours + small shape differences); one task keeps one robot from start to end.
// Robots 0–8 wear the nine P150 colour pairs, so a CEO's host colour c is its robot; a clash (or a 10th worker) takes a
// free one of 9–15. Still under reduced motion. A tap opens the
// global centred square dialog: 「有 N 个 Agent 在后台干活，<Agent 名> 正在替你监控」 + who · what. Display only — nothing
// here starts or stops work. Host text (who / label) is plain text (textContent); colours are an index 0..8 (sprite row).
// P160: every row is a button — a tap turns the dialog into that task's own card (its robot at 24 px, who, the whole title,
// where it runs, how long) and asks the computer (§22 `busy_get`) for the read-only rest: the sub-agent's latest step and,
// for a dispatch, its report's path + first line; re-asked every DETAIL_POLL_MS while open. Codex / OpenCode sub-agents
// arrive in the same list as Claude's (host busy.py), so they are the same robots. 返回 goes back to the list.
import { t, onLang } from './t.js';
import { registerLayer } from './layers.js';
import { ROBOTS } from './robots.js';
import { busyLook, onTheme } from './theme.js';   // P169 (§23): the owner's theme may bring its own robots, or the dots
import { isReady, sendApp } from './session.js';
import { longPress } from './longpress.js';

export const MAX_DOTS = 6;
// 追加 11: how many robots the header shows side by side. Default 6; when the computer says how many background Agents it
// can run at once (§22 `cap` = running + `agentj busy capacity`'s can_start), a stronger machine may show more — up to 12
// on a wide screen. A phone (≤ 600 px) stays at 6 so the time is never covered. Beyond the limit: 「+N」.
export const MAX_WIDE = 12;
export function rowLimit(cap, wide) {
  const want = Number.isInteger(cap) && cap > MAX_DOTS ? Math.min(cap, MAX_WIDE) : MAX_DOTS;
  return wide ? want : MAX_DOTS;
}
let cap = null;
let heard = false;            // a busy message already painted: a robot that appears now is new and pops in (not the first list after a (re)load)
// P154 追加 4 「负荷分割线」: the line under the reply card's header (as wide as the Agent's name + time). A darker gradient
// glides along it; the computer's load (§22 `load`, 0..1) sets the speed — one way 6 s when idle … 1 s when every core is
// busy (linear), so the owner sees whether to send now or wait. Pure CSS animation (only its duration changes); still
// under reduced motion; no glide while disconnected or when the host sends no load.
export const LOAD_SLOW_S = 6, LOAD_FAST_S = 1;
export const loadDur = (v) => +(LOAD_SLOW_S - (LOAD_SLOW_S - LOAD_FAST_S) * Math.min(1, Math.max(0, v))).toFixed(2);
let load = null;
export const LABEL_MAX = 120;                 // P154: the host sends the whole title (≤ 120); rows ellipsise it
// P154 追加 10: the robot sheets — 13 frames (12 dance steps + the bow) across, one row per colour 0..8.
export const ROBOT_FRAMES = 12, ROBOT_BOW = 12, ROBOT_STEP_MS = 200;    // 2.4 s a dance, the old breath
export const ROBOT_BYE_MS = 1000, ROBOT_CLOSE_MS = 280;
const SHEETS = { 18: 'assets/robots-18.png', 24: 'assets/robots-12.png' };
const COLORS = 9;
export const ROBOT_N = ROBOTS.length;            // the factory library; a theme's own robots: robotN()
const robotN = () => busyLook().n || ROBOT_N;
const sheet = (h) => (busyLook().sheets || SHEETS)[h];
const dotsMode = () => busyLook().style === 'dots';
let tasks = [];
const assigned = new Map();   // task key → robot index, for as long as the task is on screen (CEOs: by name, kept longer)
let agentName = () => 'Agent J';
let prompt = async () => false;      // P181: relay.sayPreset — the 「定制主题」 message to the main Agent
let fetchDetail = null;       // P160: api.busyDetail (injected by app.js, so this module stays importable without a session)
export const DETAIL_POLL_MS = 4000;
let one = null;               // the key of the task whose card is open, or null (the list)
let oneData = null, oneTimer = 0, oneSeq = 0;
const $ = (id) => document.getElementById(id);

/** The wire message → the list (bounded, plain strings, a valid colour each; garbage is dropped). */
export function sanitize(m) {
  const list = Array.isArray(m && m.tasks) ? m.tasks : [];
  const out = [];
  for (const x of list.slice(0, 32)) {
    if (!x || typeof x !== 'object') continue;
    const str = (v, n) => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, n) : '');
    out.push({
      id: str(x.id, 64), who: str(x.who, 24) || null, kind: x.kind === 'ceo' ? 'ceo' : 'temp', label: str(x.label, LABEL_MAX),
      lk: x.lk === 'schedule' || x.lk === 'dispatch' ? x.lk : null,
      since: Number.isInteger(x.since) && x.since > 0 ? x.since : null,      // P160: the card's 「已经干了 …」
      c: Number.isInteger(x.c) && x.c >= 0 && x.c < COLORS ? x.c : out.length % COLORS,
    });
  }
  // Neighbours never share a colour (the host already keeps them apart while ≤ 9 run).
  for (let i = 1; i < out.length; i++) if (out[i].c === out[i - 1].c) out[i].c = (out[i].c + 1) % COLORS;
  return out;
}
/** 追加 11: give every task its robot — kept for the task's whole life. A new task asks for robot c (its P150 colour); if
    a task on screen already has it, the first free robot after 8 (from a spot picked by the task's key), else any free
    one, else (more than 16 at once) c again. */
export function assign(list) {
  const rkey = (x) => (x.kind === 'ceo' && x.who ? 'ceo:' + x.who : 'id:' + x.key);
  const used = new Set(), N = robotN();
  const out = list.map((x) => ({ ...x, rk: rkey(x) }));
  for (const x of out) {                       // keepers first: a task that already has a robot keeps it
    const r = assigned.get(x.rk);
    if (r !== undefined && !used.has(r)) { x.r = r; used.add(r); }
  }
  for (const x of out) {
    if (x.r !== undefined) continue;
    let r = x.c < N && !used.has(x.c) ? x.c : -1;
    if (r < 0) {
      const extra = N - COLORS, h = [...x.rk].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
      for (let i = 0; i < extra && r < 0; i++) { const k = COLORS + ((h + i) % extra); if (!used.has(k)) r = k; }
      for (let k = 0; k < N && r < 0; k++) if (!used.has(k)) r = k;
      if (r < 0) r = x.c % N;
    }
    x.r = r; used.add(r); assigned.set(x.rk, r);
  }
  const live = new Set(out.map((x) => x.rk));
  for (const k of [...assigned.keys()]) if (!live.has(k) && !k.startsWith('ceo:')) assigned.delete(k);
  while (assigned.size > 64) assigned.delete(assigned.keys().next().value);
  return out;
}
export const robotName = (r) => {
  const x = (busyLook().names || ROBOTS)[r];
  return x ? (document.documentElement.lang === 'en' ? x.en : x.zh) : '';
};
export const who = (x) => x.who || t('busy.temp');
export const what = (x) => x.label || (x.lk ? t('busy.lk.' + x.lk) : '');

/** Robot k: its dance starts a little later than its left neighbour's, so a row never moves in lockstep. */
export const danceDelay = (k) => -((k * 0.37 * ROBOT_FRAMES * ROBOT_STEP_MS) % (ROBOT_FRAMES * ROBOT_STEP_MS)) / 1000;
const still = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
/** One robot `h` px high (18 header / list, 24 dialog); `dance` = false → frame 0 at rest. */
function robot(x, k, h, dance = true) {
  const i = document.createElement('i');
  i.className = 'rb rb' + h + (dance ? '' : ' still');
  i.dataset.c = String(x.c);
  i.dataset.r = String(x.r);
  i.dataset.id = x.key;
  i.title = robotName(x.r);
  i.style.backgroundImage = `url(${sheet(h)})`;
  i.style.backgroundSize = `${13 * h}px ${robotN() * h}px`;
  i.style.backgroundPositionY = -x.r * h + 'px';
  if (dance) i.style.animationDelay = danceDelay(k) + 's';
  return i;
}
/** P169 dots (the P150 look, theme preset `dots`): one round dot per item, its colour pair softly blinking (Heavy ⇄ Light);
    a theme with fewer than 9 pairs repeats them. No dance, no bow — a finished dot just goes. */
function dot(x, k, cls) {
  const i = document.createElement('i');
  i.className = 'bd' + (cls ? ' ' + cls : '') + (busyLook().blink === false ? ' still' : '');
  i.dataset.c = String(x.c % (busyLook().dots || COLORS));
  i.dataset.id = x.key || '';
  i.style.animationDelay = (-k * 0.8) + 's';      // a gentle wave, not a synchronised blink
  return i;
}
const keyed = (list) => assign(list.map((x, k) => ({ ...x, key: x.id || `${x.who || ''}|${x.label}|${k}` })));
/** The header row: keep the robots that are still working (they keep dancing), pop in the new ones, bow the finished ones
    out (then their slot closes). Reduced motion: no pop, no bow — a finished robot just goes. */
function fillRow(box, now, h) {     // `now`: already keyed + assigned (over the whole list, so a hidden one keeps its robot)
  const want = new Set(now.map((x) => x.key));
  const have = new Map([...box.querySelectorAll('i.rb:not(.bye)')].map((i) => [i.dataset.id, i]));
  for (const [key, i] of have) if (!want.has(key)) leave(i, box);
  let prev = null;
  now.forEach((x, k) => {
    let i = have.get(x.key);
    if (!i || i.dataset.r !== String(x.r)) {
      if (i) i.remove();
      i = robot(x, k, h);
      if (!still() && heard) { i.classList.add('in'); i.addEventListener('animationend', () => i.classList.remove('in'), { once: true }); }
    }
    const at = prev ? prev.nextElementSibling : box.firstElementChild;
    if (at !== i) box.insertBefore(i, at);
    prev = i;
  });
  for (const b of box.querySelectorAll('b')) b.remove();
}
function leave(i, box) {
  if (still()) { i.remove(); return; }
  i.classList.add('bye');
  i.style.backgroundPositionX = -ROBOT_BOW * (i.classList.contains('rb24') ? 24 : 18) + 'px';   // the bow frame
  setTimeout(() => {
    i.classList.add('gone');
    setTimeout(() => { i.remove(); if (!box.querySelector('i.rb') && !tasks.length) box.hidden = true; }, ROBOT_CLOSE_MS);
  }, ROBOT_BYE_MS);
}

const wide = () => typeof matchMedia === 'function' && !matchMedia('(max-width: 600px)').matches;
export const limit = () => rowLimit(cap, wide());
function paintDots() {
  const b = $('busyDots');
  if (!b) return;
  const lim = limit();
  const shown = keyed(tasks).slice(0, tasks.length > lim ? lim - 1 : lim);
  b.classList.toggle('dots', dotsMode());
  if (dotsMode()) b.replaceChildren(...shown.map((x, k) => dot(x, k)));
  else { for (const d of b.querySelectorAll('i.bd')) d.remove(); fillRow(b, shown, 18); }
  b.hidden = !tasks.length && !b.querySelector('i.rb');
  if (shown.length < tasks.length) { const more = document.createElement('b'); more.textContent = '+' + (tasks.length - shown.length); b.append(more); }
  const label = t('busy.aria', { n: tasks.length });
  b.setAttribute('aria-label', label); b.title = label;
  b.dataset.n = String(tasks.length);
}

function paintDialog() {
  const d = $('busyDlg');
  if (!d || d.hidden) return;
  if (!tasks.length && !one) { close(); return; }
  $('busyTitle').textContent = tasks.length === 1 ? t('busy.title1') : t('busy.title', { n: tasks.length });
  $('busySub').textContent = t('busy.sub', { a: agentName() });
  const big = keyed(tasks).slice(0, MAX_WIDE);
  const fig = (x, k, cls) => (dotsMode() ? dot(x, k, cls) : robot(x, k, 24));
  $('busyBig').replaceChildren(...big.map((x, k) => fig(x, k)));
  $('busyList').replaceChildren(...keyed(tasks).map((x, k) => {
    const li = document.createElement('li');
    const b = document.createElement('b'); b.textContent = who(x);
    const s = document.createElement('span'); s.textContent = what(x); s.title = what(x);
    li.append(fig(x, k, 'row'), b, s);   // 追加 11: every task, its own robot at 24 px (P169 dots: its dot)
    li.setAttribute('role', 'button'); li.tabIndex = 0;     // P160: a tap → this task's card
    li.setAttribute('aria-label', t('busy.detail.open', { who: who(x), what: what(x) }));
    const go = (e) => { e.stopPropagation(); openOne(x.key); };
    li.addEventListener('click', go);
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); } });
    return li;
  }));
  paintOne();
  d.querySelector('.sq').dataset.n = String(Math.min(tasks.length, 9));
  d.querySelector('.sq').dataset.big = String(big.length);
}

// ---- P160: one task's card
/** Seconds → 「不到 1 分钟」 / 「12 分钟」 / 「2 小时 5 分钟」. */
export function dur(sec) {
  const m = Math.floor(Math.max(0, sec) / 60);
  if (m < 1) return t('busy.dur.s');
  return m < 60 ? t('busy.dur.m', { n: m }) : t('busy.dur.h', { h: Math.floor(m / 60), m: m % 60 });
}
const SRC = ['claude', 'codex', 'opencode', 'unit', 'file', 'workflow'];
/** The computer's detail (§22 busy_info.item) → bounded plain strings; null when absent / garbage. */
export function sanitizeDetail(d) {
  if (!d || typeof d !== 'object') return null;
  const str = (v, n) => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, n) : '');
  const num = (v) => (Number.isInteger(v) && v > 0 ? v : null);
  const out = { src: SRC.includes(d.src) ? d.src : null, since: num(d.since) };
  if (d.last && typeof d.last === 'object' && str(d.last.text, 200)) out.last = { text: str(d.last.text, 200), at: num(d.last.at) };
  if (d.report && typeof d.report === 'object' && str(d.report.path, 400))
    out.report = { path: str(d.report.path, 400), head: str(d.report.head, 240) || null, at: num(d.report.at) };
  return out;
}
const nowS = () => Math.floor(Date.now() / 1000);
function paintOne() {
  const d = $('busyDlg'), box = $('busyOne');
  if (!d || d.hidden || !box) return;
  const sq = d.querySelector('.sq');
  if (!one) { box.hidden = true; box.replaceChildren(); $('busyBack').hidden = true; delete sq.dataset.view; return; }
  sq.dataset.view = 'one';
  box.hidden = false; $('busyBack').hidden = false;
  const all = keyed(tasks);
  const k = all.findIndex((x) => x.key === one);
  const x = k >= 0 ? all[k] : null;
  const info = oneData && oneData.key === one ? oneData.d : null;
  const kids = [];
  if (x) {
    $('busyBig').replaceChildren(robot(x, k, 24));     // its robot above the name, like the list's big row (P154 24 px)
    $('busyTitle').textContent = who(x);
    const w = document.createElement('div'); w.className = 'bo-what'; w.textContent = what(x); kids.push(w);
    const since = (info && info.since) || x.since;
    const meta = [info && info.src ? t('busy.src.' + info.src) : '', Number.isInteger(since) ? t('busy.detail.since', { d: dur(nowS() - since) }) : '']
      .filter(Boolean).join(' · ');
    if (meta) { const m = document.createElement('div'); m.className = 'bo-meta'; m.textContent = meta; kids.push(m); }
    const dl = document.createElement('dl');
    const row = (label, ...vals) => { const dt = document.createElement('dt'); dt.textContent = label; dl.append(dt); for (const v of vals) dl.append(v); };
    const dd = (text, code = false) => { const e = document.createElement('dd'); if (code) { const c = document.createElement('code'); c.textContent = text; e.append(c); } else e.textContent = text; return e; };
    if (oneData === 'old') { /* an older computer: what the list says is all there is */ }
    else if (!info) row(t('busy.detail.last'), dd(t('busy.detail.loading')));
    else {
      const agent = !['unit', 'file', 'workflow'].includes(info.src);  // a dispatch / command has no steps of ours, only a report
      if (info.last || agent || !info.report) row(t('busy.detail.last'), dd(info.last ? info.last.text : t('busy.detail.lastNone')));
      if (info.report) row(t('busy.detail.report'), dd(info.report.path, true), dd(info.report.head || t('busy.detail.reportNone')));
    }
    if (dl.childElementCount) kids.push(dl);
    if (oneData === 'old') { const n = document.createElement('div'); n.className = 'bo-note'; n.textContent = t('busy.detail.old'); kids.push(n); }
  } else {
    $('busyBig').replaceChildren();
    $('busyTitle').textContent = t('busy.detail.goneTitle');
    const n = document.createElement('div'); n.className = 'bo-note'; n.textContent = t('busy.detail.gone'); kids.push(n);
  }
  box.replaceChildren(...kids);
}
async function askOne() {
  clearTimeout(oneTimer);
  const key = one, seq = ++oneSeq;
  const x = key && tasks.find((v) => v.id === key);
  if (!x || !fetchDetail) { if (key && !fetchDetail) { oneData = 'old'; paintOne(); } return; }
  let r = null;
  try { r = await fetchDetail(x.id); } catch { r = null; }
  if (seq !== oneSeq || one !== key) return;
  if (r && r.ok) oneData = { key, d: sanitizeDetail(r.item) || { src: null, since: null } };
  else if (r && r.why === 'unsupported') oneData = 'old';
  paintOne();
  if (oneData !== 'old') oneTimer = setTimeout(askOne, DETAIL_POLL_MS);
}
/** A row tapped: the dialog becomes that task's card (read-only). */
export function openOne(key) {
  one = key; oneData = null;
  paintOne();
  askOne();
  $('busyBack').focus({ preventScroll: true });
}
/** 返回: back to the list (or closed, when nothing runs any more). */
export function backToList() {
  one = null; oneData = null; oneSeq++; clearTimeout(oneTimer);
  if (!tasks.length) { close(); return; }
  paintOne(); paintDialog();
  $('busyClose').focus({ preventScroll: true });
}

export function open() {
  if (!tasks.length) return;
  closeLoad();
  $('busyDlg').hidden = false;
  paintDialog();
  $('busyClose').focus({ preventScroll: true });
}
export function close() {
  const d = $('busyDlg');
  one = null; oneData = null; oneSeq++; clearTimeout(oneTimer);
  if (d) { d.hidden = true; delete d.querySelector('.sq').dataset.view; const b = $('busyOne'); if (b) b.hidden = true; if ($('busyBack')) $('busyBack').hidden = true; }
}

/** The line's width: 追加 6 — fixed at half the card's content width, whatever the name (re-measured on resize). */
function sizeLoad() {
  const line = $('loadLine'), rd = $('read');
  if (!line || !rd) return;
  const half = Math.round(rd.clientWidth / 2);
  if (half > 0) line.style.width = half + 'px';
}
function paintLoad() {
  const line = $('loadLine');
  if (!line) return;
  if (load === null) { delete line.dataset.load; line.style.removeProperty('--ll-dur'); return; }
  line.dataset.load = String(load);
  line.style.setProperty('--ll-dur', loadDur(load) + 's');
}
/** 追加 5: a tap on the line → the explainer (what it is, the load now, three sample speeds) in the global dialog. */
export function openLoad() {
  close();
  paintLoadNow();
  $('loadDlg').hidden = false;
  $('loadClose').focus({ preventScroll: true });
  watch(true);
}
export function closeLoad() { const d = $('loadDlg'); if (d && !d.hidden) { d.hidden = true; watch(false); } }

// ---- P181 「负载卡五项」 (PROTOCOL §22.1): CPU · memory · GPU · disk · network while the card is open --------------------
// The card asks the computer to sample (res_watch on, renewed every RES_RENEW_MS); closing it — or the page going away —
// stops it, so nothing is measured while nobody looks. Unknown = 「—」, never a guessed number.
export const RES_RENEW_MS = 10000;
let res = null, resTimer = 0;
function watch(on) {
  clearInterval(resTimer); resTimer = 0;
  if (on) { res = null; paintRes(); resTimer = setInterval(() => send(true), RES_RENEW_MS); }
  send(on);
}
function send(on) { if (isReady()) sendApp({ t: 'res_watch', on }).catch(() => {}); }
const share = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : null);
/** §22.1 `res` → {cpu, mem, gpu:{pct,name,sleep}, disk:{busy,free,free_gb}, net:{rx,tx}} with every bad field null. */
export function sanitizeRes(m) {
  const o = (v) => (v && typeof v === 'object' ? v : {});
  const g = o(m && m.gpu), d = o(m && m.disk), n = o(m && m.net);
  const rate = (v) => (Number.isFinite(v) && v >= 0 ? Math.min(v, 1e12) : null);
  const gb = Number.isFinite(d.free_gb) && d.free_gb >= 0 ? Math.min(d.free_gb, 1e6) : null;
  return {
    cpu: share(m && m.cpu), mem: share(m && m.mem),
    gpu: m && m.gpu ? { pct: share(g.pct), sleep: g.sleep === true, name: typeof g.name === 'string' ? g.name.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 40) : '' } : null,
    disk: m && m.disk ? { busy: share(d.busy), free: share(d.free), free_gb: gb } : null,
    net: m && m.net && rate(n.rx) !== null && rate(n.tx) !== null ? { rx: rate(n.rx), tx: rate(n.tx) } : null,
  };
}
/** bytes per second → 「1.2 MB/s」 / 「340 KB/s」. */
export function rateText(bps) {
  if (bps >= 1e6) return (bps / 1e6).toFixed(bps >= 1e8 ? 0 : 1) + ' MB/s';
  return Math.round(bps / 1e3) + ' KB/s';
}
const pct = (v) => Math.round(v * 100) + '%';
function row(k, value, fill, sub) {
  const li = $('resList') && $('resList').querySelector(`li[data-k="${k}"]`);
  if (!li) return;
  li.querySelector('.v').textContent = value;
  const bar = li.querySelector('.resbar');
  bar.hidden = fill === undefined;
  bar.firstElementChild.style.width = fill == null ? '0%' : Math.round(fill * 100) + '%';
  li.dataset.known = fill == null && (value === '—' || value === '…' || value === t('p181.res.none')) ? '0' : '1';
  li.dataset.hot = fill != null && fill >= 0.85 ? '1' : '0';
  const s = li.querySelector('small');
  s.textContent = sub || '';
  s.hidden = !sub;
}
function paintRes() {
  if (!$('resList')) return;
  const off = document.body.dataset.conn === 'off';
  const r = res || {};
  const dash = res ? '—' : (off ? '—' : '…');
  row('cpu', r.cpu != null ? pct(r.cpu) : dash, r.cpu);
  row('mem', r.mem != null ? pct(r.mem) : dash, r.mem);
  if (r.gpu && r.gpu.sleep) row('gpu', t('p181.res.gpuSleep'), 0, r.gpu.name);
  else if (r.gpu && r.gpu.pct != null) row('gpu', pct(r.gpu.pct), r.gpu.pct, r.gpu.name);
  else row('gpu', res ? t('p181.res.none') : dash, null);
  const d = r.disk;
  const free = d && d.free != null ? t('p181.res.free', { gb: d.free_gb != null ? (d.free_gb >= 100 ? Math.round(d.free_gb) : d.free_gb) : '—', pct: pct(d.free) }) : '';
  row('disk', d && d.busy != null ? pct(d.busy) : dash, d ? d.busy : null, free);
  row('net', r.net ? `↓ ${rateText(r.net.rx)}  ↑ ${rateText(r.net.tx)}` : dash, undefined);
}
/** `{"t":"res",…}` from the computer (only while this card watches). */
export function setRes(m) { res = sanitizeRes(m); if (typeof m.load === 'number') { load = sanitizeLoad(m); paintLoad(); paintLoadNow(); } paintRes(); }
export const currentRes = () => res;
function paintLoadNow() {
  const p = $('loadNow');
  if (!p) return;
  const off = document.body.dataset.conn === 'off';
  if (off || load === null) { p.textContent = t(off ? 'load.nowOff' : 'load.nowNone'); return; }
  const lvl = load < 0.35 ? 'idle' : load < 0.75 ? 'normal' : 'busy';
  p.textContent = t('load.now', { pct: Math.round(load * 100), lvl: t('load.' + lvl), s: loadDur(load) });
}
/** §22 `load` → 0..1, or null when absent / not a number. */
export function sanitizeLoad(m) {
  const v = m && m.load;
  return typeof v === 'number' && Number.isFinite(v) ? Math.min(1, Math.max(0, Math.round(v * 100) / 100)) : null;
}

/** §22 `cap` (追加 11) → how many background Agents the computer can run at once, or null. */
export function sanitizeCap(m) {
  const v = m && m.cap;
  return Number.isInteger(v) && v >= 1 && v <= 64 ? v : null;
}

/** `{"t":"busy","tasks":[…],"by":…,"load"?:0..1,"cap"?:n}` from the computer. */
export function setBusy(m) { tasks = sanitize(m); load = sanitizeLoad(m); cap = sanitizeCap(m); paintDots(); heard = true; paintDialog(); paintLoad(); if ($('loadDlg') && !$('loadDlg').hidden) paintLoadNow(); }
export const current = () => tasks.slice();

export function init(fns = {}) {
  if (fns.name) agentName = fns.name;
  if (typeof fns.detail === 'function') fetchDetail = fns.detail;
  if ($('busyBack')) $('busyBack').addEventListener('click', (e) => { e.stopPropagation(); backToList(); });
  $('busyDots').addEventListener('click', (e) => { e.stopPropagation(); open(); });
  $('busyClose').addEventListener('click', close);
  if (fns.prompt) prompt = fns.prompt;
  // P181: 「换个样子」 under the robots, and a long press on the robots themselves (a tap still opens this card)
  if ($('busyTheme')) $('busyTheme').addEventListener('click', () => { close(); prompt('theme'); });
  longPress($('busyDots'), () => { close(); prompt('theme'); });
  $('busyDlg').addEventListener('click', (e) => { if (e.target === e.currentTarget) close(); });   // outside the square
  registerLayer({ element: () => $('busyDlg'), open: () => !$('busyDlg').hidden, close, fallback: () => $('busyDots'), rank: 5 });
  if ($('loadHit')) {
    $('loadHit').addEventListener('click', (e) => { e.stopPropagation(); openLoad(); });
    $('loadClose').addEventListener('click', closeLoad);
    $('loadDlg').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeLoad(); });
    registerLayer({ element: () => $('loadDlg'), open: () => !$('loadDlg').hidden, close: closeLoad, fallback: () => $('loadHit'), rank: 5 });
  }
  onTheme(() => {                // P169: another theme — every figure is drawn again with the new sheets / dots
    assigned.clear();
    const b = $('busyDots');
    if (b) for (const i of b.querySelectorAll('i.rb, i.bd')) i.remove();
    paintDots(); paintDialog();
  });
  onLang(() => { paintDots(); paintDialog(); sizeLoad(); if ($('loadDlg') && !$('loadDlg').hidden) { paintLoadNow(); paintRes(); } });
  addEventListener('pagehide', () => { if ($('loadDlg') && !$('loadDlg').hidden) send(false); });
  try { const ro = new ResizeObserver(sizeLoad); for (const id of ['read']) if ($(id)) ro.observe($(id)); }
  catch (_) { addEventListener('resize', sizeLoad); }
  paintDots(); paintLoad(); sizeLoad();
}
