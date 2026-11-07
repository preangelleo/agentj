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
import { copyable } from './copycode.js';   // P292: the report path can be copied
import { ROBOTS } from './robots.js';
import { settleRobots } from './robot-color.js';   // P302: a robot never wears its ground's colour
import { VENDOR_SVGS } from './vendor-marks.js';
import { busyLook, onTheme } from './theme.js';   // P169 (§23): the owner's theme may bring its own robots, or the dots
import { isReady, sendApp } from './session.js';
import { lang } from './t.js';
import { longPress } from './longpress.js';
import { orderCard, orderedBusy, orderId } from './order-cards.js';
let orderMain = null, orderStopped = () => false;
import { SVG } from './icons.js';   // 0.26.9 #430: the @ glyph
import { sanitizeToday, todayText, useShort, useOf as useOfDetail } from './use.js';   // P204 (§29): today's spend on the load card, a run's on its robot

export const MAX_DOTS = 6;
// Legacy viewport/cap limit retained for consumers of rowLimit()/limit(). #557's
// header renderer measures its actual remaining width and no longer uses this cap.
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
const settle = (el) => settleRobots(el, !!busyLook().sheets);   // P302: a theme's own robots keep their colours
let tasks = [];
let liveProcesses = null;
let taskCount = null;
let unknownCount = null;
const assigned = new Map();   // task key → robot index, for as long as the task is on screen (CEOs: by name, kept longer)
let agentName = () => 'Agent J';
let prompt = async () => false;      // P181: relay.sayPreset — the 「定制主题」 message to the main Agent
let mentionHook = null;       // 0.26.9 #430: relay.insertText (injected by app.js) — a row's @ puts the task in the composer
const mentioned = new Map();  // text → {text, label}: the composer shows these as one chip (five-pages.js getMentions)
let fetchDetail = null;       // P160: api.busyDetail (injected by app.js, so this module stays importable without a session)
export const DETAIL_POLL_MS = 4000;
let view = 'tasks';
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
      robot: Number.isInteger(x.robot)&&x.robot>=0&&x.robot<16?x.robot:null,
      detected: x.detected && typeof x.detected === 'object' ? Object.fromEntries(['harness','model','effort','account','subscription'].map(k=>[k,str(x.detected[k],100)])) : null,
      reported: x.reported && typeof x.reported === 'object' ? {harness:str(x.reported.harness,32),model:str(x.reported.model,100)} : null,
      conflict: x.conflict === true,
      status: x.status === 'unknown' ? 'unknown' : 'running',
      harness: str(x.harness, 32), model: str(x.model, 100), workflow: str(x.workflow, 80),
      where: str(x.where, 40),                                          // 0.26.9 #444: the machine it runs on (empty = this computer)
      company: str(x.company, 80), role: str(x.role, 80), parent: str(x.parent, 80),
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
  for (const x of out) { if (x.robot!==null && x.robot!==undefined) {x.r=x.robot%N;used.add(x.r);assigned.set(x.rk,x.r);} }
  for (const x of out) { if(x.r!==undefined)continue;                       // keepers first: a task that already has a robot keeps it
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
// 0.26.9 #430 (owner 11-03: 「每一个任务都应该添加一个艾特icon……直接点击这一个icon就可以艾特……这个task」): the words the
// message carries for the Agent — who, what, and the task id the host listed (`agentj busy` shows the same id) — and the
// short chip the composer shows instead, like @CEO. Plain text only; the host's id / label are bounded already (sanitize).
const clip = (v, n) => (v.length > n ? v.slice(0, n - 1) + '…' : v);
export function mentionOf(x) {
  const name = clip(who(x), 24), job = clip(what(x).replace(/\s+/g, ' ').trim(), LABEL_MAX);
  const text = '@' + t('p0269w4.busy.mention', { who: name, what: job || name }) + (x.id ? t('p0269w4.busy.mentionId', { id: x.id }) : '');
  return { text, label: '@' + name + ' · ' + (job || name), taskId:x.id||'' };
}
/** The tasks put in the composer on this page (the composer turns each `text` back into its chip). */
export const mentions = () => [...mentioned.values()];
function mentionTask(x) {
  const m = mentionOf(x);
  mentioned.set(m.text, m);
  close();
  if (mentionHook) mentionHook(m.text + ' ');
}
function atButton(x) {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'busy-at'; b.setAttribute('data-aj-owner', 'aj-busy');
  const label = mentionOf(x).label;
  b.dataset.taskId=x.id||'';
  b.setAttribute('aria-label', label); b.title = label;
  const NS = SVG, svg = document.createElementNS(NS, 'svg');
  for (const [k, v] of [['viewBox', '0 0 24 24'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '1.9'], ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true']]) svg.setAttribute(k, v);
  for (const d of ['M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z', 'M15.5 8.5v4.75a2.25 2.25 0 0 0 4.5 0V12a8 8 0 1 0-3.13 6.35']) {
    const path = document.createElementNS(NS, 'path'); path.setAttribute('d', d); svg.append(path);
  }
  b.append(svg);
  b.addEventListener('click', (e) => { e.stopPropagation();const id=e.currentTarget.dataset.taskId;const rows=tasks.filter(t=>t.id===id&&t.who===x.who&&t.label===x.label);if(rows.length===1)mentionTask(rows[0]); });
  b.addEventListener('keydown', (e) => e.stopPropagation());   // Enter / Space press this button, not the row under it
  return b;
}

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
  if (dance) i.style.setProperty('--rbd', danceDelay(k) + 's');   // P207 追加 4: the dance offset, kept under the pop too
  return i;
}
/** P281 (owner, 2026-10-14): the reading area's wait figure — every 「已收到 / 排队 / 正在处理 / 切换中」 line starts with it, and
    a wait without words is this figure alone. The current theme's first robot (factory: 小蓝), dancing, about the body
    text's height (CSS .wbot: em-sized, so it follows the text size and the font step); the dots theme keeps its three
    blinking dots — the owner chose them. Decorative: the words beside it say what is going on. */
export function waitRobot() {
  if (dotsMode()) {
    const s = document.createElement('span');
    s.className = 'wbot wdots';
    s.setAttribute('aria-hidden', 'true');
    for (let k = 0; k < 3; k++) s.appendChild(document.createElement('i'));
    return s;
  }
  const i = document.createElement('i');
  i.className = 'wbot' + (still() ? ' still' : '');
  i.setAttribute('aria-hidden', 'true');
  i.dataset.r = '0';
  i.style.backgroundImage = `url(${sheet(24)})`;
  i.style.setProperty('--wbn', String(robotN()));
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
      if (!still() && heard) popIn(i);
    }
    const at = prev ? prev.nextElementSibling : box.firstElementChild;
    if (at !== i) box.insertBefore(i, at);
    prev = i;
  });
  for (const b of box.querySelectorAll('b')) b.remove();
}
/** A new robot pops in (dancing underneath already). .in is dropped on its animationend, or by a timer when that event never
    comes (P207 追加 4: popped in while the window was hidden / occluded) — and swept when the page shows again. */
export const ROBOT_POP_MS = 420;
function popIn(i) {
  i.classList.add('in');
  const done = (e) => { if (!e || e.animationName === 'aj-busy-rb-pop') i.classList.remove('in'); };
  i.addEventListener('animationend', done);
  setTimeout(done, ROBOT_POP_MS + 200);
}
/** Back from the background: no robot stays half-popped, and one whose bow timers were throttled leaves now. */
export function sweep(box = $('busyDots')) {
  if (!box) return;
  for (const i of box.querySelectorAll('i.rb.in')) i.classList.remove('in');
  const now = Date.now();
  for (const i of box.querySelectorAll('i.rb.bye')) if (now - (+i.dataset.bye || now) > ROBOT_BYE_MS + ROBOT_CLOSE_MS + 500) i.remove();
  if (!box.querySelector('i.rb') && !tasks.length) box.hidden = true;
}
function leave(i, box) {
  if (still()) { i.remove(); return; }
  i.classList.add('bye'); i.dataset.bye = String(Date.now());
  i.style.backgroundPositionX = -ROBOT_BOW * (i.classList.contains('rb24') ? 24 : 18) + 'px';   // the bow frame
  setTimeout(() => {
    i.classList.add('gone');
    setTimeout(() => { i.remove(); if (!box.querySelector('i.rb') && !tasks.length) box.hidden = true; }, ROBOT_CLOSE_MS);
  }, ROBOT_BYE_MS);
}

// Current hosts own all counts. Pre-P508 hosts supplied only running rows.
const runningCount = () => taskCount ?? tasks.length;
export const currentCounts = () => ({task_count: runningCount(), unknown_count: unknownCount, live_process_count: liveProcesses});
const wide = () => typeof matchMedia === 'function' && !matchMedia('(max-width: 600px)').matches;
export const limit = () => rowLimit(cap, wide());
function paintDots() {
  const b = $('busyDots');
  if (!b) return;
  b.classList.toggle('dots', dotsMode());
  b.hidden = !tasks.length && !b.querySelector('i.rb');
  // #557: the name/time have already claimed their flex space. Measure this remainder,
  // including the actual +K font width, rather than guessing from the viewport.
  const style = getComputedStyle(b), gap = parseFloat(style.columnGap) || 0;
  const width = Math.max(0, b.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
  const probe = document.createElement('b');
  probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none';
  b.append(probe);
  const figure = document.createElement('i');
  figure.className = dotsMode() ? 'bd' : 'rb rb18';
  figure.style.cssText = 'position:absolute;visibility:hidden;animation:none';
  b.append(figure);
  const unit = figure.getBoundingClientRect().width;
  figure.remove();
  const count = tasks.length;
  let n = count;
  for (; n >= 0; n--) {
    const extra = tasks.length - n;
    probe.textContent = extra ? '+' + extra : '';
    const needed = n * unit + Math.max(0, n - 1) * gap + (extra ? probe.getBoundingClientRect().width + (n ? gap : 0) : 0);
    if (needed <= width) break;
  }
  probe.remove();
  const shown = keyed(tasks).slice(0, Math.max(0, n));
  b.classList.toggle('dots', dotsMode());
  if (dotsMode()) b.replaceChildren(...shown.map((x, k) => dot(x, k)));
  else {
    for (const d of b.querySelectorAll('i.bd')) d.remove();
    fillRow(b, shown, 18);
    // A hidden/finished robot may bow only when its slot fits. Resizing must free
    // obsolete slots immediately; otherwise the bow itself could cover the time.
    const activeWidth = shown.length * unit + Math.max(0, shown.length - 1) * gap;
    if (shown.length < tasks.length || b.scrollWidth > b.clientWidth || activeWidth >= width)
      for (const i of b.querySelectorAll('i.bye')) i.remove();
    settle(b);
  }
  b.hidden = !tasks.length && !b.querySelector('i.rb');
  if (shown.length < tasks.length) { const more = document.createElement('b'); more.textContent = '+' + (tasks.length - shown.length); b.append(more); }
  const label = t('busy.aria', { n: runningCount() });
  b.setAttribute('aria-label', label); b.title = label;
  b.dataset.n = String(runningCount());
}

function paintDialog() {
  const d = $('busyDlg');
  if (!d || d.hidden) return;
  if (!tasks.length && !one) { close(); return; }
  $('busyTitle').textContent = runningCount() === 1 ? t('busy.title1') : t('busy.title', { n: runningCount() });
  $('busySub').textContent = t('busy.sub', { a: agentName() }) + (liveProcesses === null ? '' : ' · '+t('p508.processes',{n:liveProcesses}));
  const big = keyed(tasks).slice(0, MAX_WIDE);
  const fig = (x, k, cls) => (dotsMode() ? dot(x, k, cls) : robot(x, k, 24));
  $('busyBig').replaceChildren(...big.map((x, k) => fig(x, k)));
  $('busyList').replaceChildren(...orderedBusy(keyed(tasks)).map((x, k) => {
    const li = document.createElement('li');li.dataset.taskId=x.id||'';
    const oid = orderId(x); if (oid) li.dataset.order = oid;
    const b = document.createElement('b'); b.textContent = who(x);
    const s = document.createElement('span'); s.textContent = what(x); s.title = what(x);
    const identity = document.createElement('div'); identity.className = 'busy-identity';
    const summary = document.createElement('small'); summary.setAttribute('data-aj-owner','aj-busy');
    summary.textContent = [identityText(x), x.since ? t('p515.running', {d: dur(nowS()-x.since)}) : ''].filter(Boolean).join(' · ');
    identity.append(summary);
    if (x.conflict && x.reported && (x.reported.harness || x.reported.model)) {
      const claim = document.createElement('small'); claim.setAttribute('data-aj-owner','aj-busy'); claim.className = 'error';
      claim.textContent = t('p515.conflict', {x: [shortHarness(x.reported.harness), x.reported.model].filter(Boolean).join(' · ')});
      identity.append(claim);
    }
    li.append(fig(x, k, 'row'), b, s, identity);
    if (mentionHook) li.append(atButton(x));             // 0.26.9 #430: @ = mention this task in the composer
    li.setAttribute('role', 'button'); li.setAttribute('data-aj-owner', 'aj-busy'); li.tabIndex = 0;     // P160: a tap → this task's card
    li.setAttribute('aria-label', t('busy.detail.open', { who: who(x), what: what(x) }));
    const go = (e) => { e.stopPropagation(); openOne(x.key); };
    li.addEventListener('click', go);
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); } });
    return li;
  }));
  paintWorkflow();
  paintOne();
  d.querySelector('.sq').dataset.n = String(Math.min(tasks.length, 9));
  d.querySelector('.sq').dataset.big = String(big.length);
  settle(d);
}

// P250: original licensed assets stay byte-identical; the UI uses only approved palette fills.
const vendorPaletteSvg = new Map();
function paletteVendor(src) {
  if (!vendorPaletteSvg.has(src)) vendorPaletteSvg.set(src, Promise.resolve().then(() => {
    const text=VENDOR_SVGS[src.split('/').pop()];
    if (!text) throw new Error('vendor_asset_unavailable');
    const svg = new DOMParser().parseFromString(text, 'image/svg+xml').documentElement;
    if (svg.localName !== 'svg') throw new Error('vendor_asset_invalid');
    const tags = new Set(['svg','g','path','rect','polygon','polyline','circle','ellipse','line','defs','clipPath','title']);
    const fills = {'#211e1e':'var(--c-noir)', '#656363':'var(--c-black-mana)', '#cfcecd':'var(--c-lotion)', '#4d6bfe':'var(--c-lotion)'};
    for (const node of [svg, ...svg.querySelectorAll('*')]) {
      if (!tags.has(node.localName)) { node.remove(); continue; }
      for (const attr of [...node.attributes]) if (/^on/i.test(attr.name) || /href$/i.test(attr.name) || attr.name === 'style') node.removeAttribute(attr.name);
      for (const prop of ['fill','stroke']) if (node.hasAttribute(prop)) {
        const color = node.getAttribute(prop).toLowerCase();
        if (color !== 'none') node.style[prop] = fills[color] || 'var(--c-lotion)';
      }
    }
    svg.style.fill = 'var(--c-lotion)';
    svg.classList.add('busy-palette-mark');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
    return svg;
  }));
  return vendorPaletteSvg.get(src).then(svg => svg.cloneNode(true));
}

function vendorMark(value, isModel=false) {
  const wrap=document.createElement('span');wrap.className='busy-vendor';
  const v=(value||'').toLowerCase();
  const vendor=v.includes('deepseek')?'deepseek':!isModel&&v==='opencode'?'opencode':null;
  if(vendor){const img=document.createElement('img');img.alt=vendor==='deepseek'?'DeepSeek':'OpenCode';
    // The working dialog always uses the dark Noir surface, in both page themes.
    img.src='assets/vendors/'+(vendor==='deepseek'?'deepseek.svg':'opencode-dark.svg');
    const fallback=document.createElement('span');fallback.className='busy-vendor-fallback';fallback.textContent=img.alt;
    wrap.append(img,fallback);paletteVendor(img.src).then(svg=>{wrap.append(svg);wrap.dataset.palette='ready';}).catch(e=>{wrap.dataset.palette='error';wrap.dataset.paletteError=e.name+': '+e.message;});}
  else if(isModel){wrap.textContent=v.includes('claude')?'Anthropic':v.includes('gemini')?'Google':/gpt|o[134]-/.test(v)?'OpenAI':'';}
  return wrap;
}
const shortHarness = (h) => harnessName(h).split(' · ')[0];
export function identityText(x) {
  const d = x.detected || {harness:x.harness,model:x.model};
  const parts = [shortHarness(d.harness), d.model];
  if (d.effort) parts.push(['minimal','low','medium','high','xhigh','max'].includes(d.effort) ? t('p515.effort.'+d.effort) : d.effort);
  // Account fingerprints stay on the wire for equality only, never in owner-facing copy.
  const accounts = [...new Set(tasks.filter(v=>v.detected?.harness===d.harness).map(v=>v.detected?.account).filter(Boolean))].sort();
  if (d.account && accounts.length > 1) parts.push(t('p515.account',{h:shortHarness(d.harness),n:accounts.indexOf(d.account)+1}));
  if (x.status === 'unknown') parts.unshift(t('p508.state_unknown'));
  { const w = whereText(x); if (w) parts.push(w); }
  return parts.filter(Boolean).join(' · ');
}
/** 0.26.9 #444 (chairman 11-03 22:5x: 「我看不到它们是在 TensorBook 跑的，还是在 Olares 上的」): 「在 Olares 上跑」 for a task the
    host saw sent to another machine; 「在本机跑」 for the rest only while some task on the list runs elsewhere. */
export function whereText(x) {
  if (x.where) return t('p0269w4.where', { h: x.where === 'ssh' ? t('p0269w4.remote') : x.where });
  return tasks.some((v) => v.where) ? t('p0269w4.whereLocal') : '';
}
export function harnessName(h) {
  return ({claude:'Claude Code · Anthropic', codex:'Codex · OpenAI', opencode:'OpenCode', gemini:'Gemini · Google',
    deepseek:'DeepSeek'})[h] || h || '';
}
function paintWorkflow() {
  if (!$('busyWorkflow')) return;
  $('busyTasksTab').textContent = t('p244.busy46');
  $('busyWorkflowTab').textContent = t('p244.busy47');
  for (const [id, selected] of [['busyTasksTab',view==='tasks'], ['busyWorkflowTab',view==='workflow']]) {
    $(id).setAttribute('aria-selected', selected); $(id).tabIndex = selected ? 0 : -1;
  }
  $('busyList').hidden = view !== 'tasks'; $('busyWorkflow').hidden = view !== 'workflow';
  const box = $('busyWorkflow'); box.replaceChildren();
  const groups = new Map();
  for (const x of keyed(tasks)) {
    const key = x.workflow || '';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(x);
  }
  for (const [folder, workers] of groups) {
    const section = document.createElement('section'); section.className = 'busy-org';
    const head = document.createElement('h3'); head.setAttribute('data-aj-owner','aj-busy'); head.textContent = folder ? workers[0].company || folder : t('p244.busy48');
    section.append(head);
    if (folder) {
      const root = document.createElement('p'); root.className = 'busy-org-root'; root.textContent = t('p244.busy49') + (workers.find(x=>x.role==='CEO')?.who || workers[0].parent || 'CEO');
      section.append(root);
    }
    workers.forEach((x,k)=>{
      const row=document.createElement('button'); row.setAttribute('data-aj-owner', 'aj-busy'); row.type='button'; row.className='busy-org-person';
      const words=document.createElement('span'); words.textContent=who(x)+' · '+(x.role||t('p244.busy50'));
      const meta=document.createElement('small'); meta.setAttribute('data-aj-owner','aj-busy'); meta.textContent=folder ? (x.role==='CEO' ? t('p244.busy51') : t('p244.busy52')+(x.parent||'CEO')) : harnessName(x.harness);
      row.append(robot(x,k,24),words);if(meta.textContent)row.append(meta); row.onclick=()=>{view='tasks';paintDialog();openOne(x.key);}; section.append(row);
    }); box.append(section);
  }
}
function switchView(v) { backToList(); view=v; paintDialog(); }

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
  if (d.use && typeof d.use === 'object') {                 // P204 (§29): a workflow's last run — 「12 s · ≈$0.04」
    const s = useShort(useOfDetail(d.use));
    if (s) out.use = s;
  }
  if (d.report && typeof d.report === 'object' && str(d.report.path, 400))
    out.report = { path: str(d.report.path, 400), head: str(d.report.head, 240) || null, at: num(d.report.at) };
  if (str(d.cmd, 400)) out.cmd = str(d.cmd, 400);     // P200: a dispatch's raw command line — here only, never on the list
  if (d.order && typeof d.order.id === 'string' && /^o\d{8}-\d{6}-[0-9a-f]{4}$/.test(d.order.id)) out.order = d.order;
  return out;
}
const nowS = () => Math.floor(Date.now() / 1000);
function paintOne() {
  const d = $('busyDlg'), box = $('busyOne');
  if (!d || d.hidden || !box) return;
  const sq = d.querySelector('.sq');
  if (!one) { box.hidden = true; box.replaceChildren(); $('busyBack').hidden = true; delete sq.dataset.view; return; }
  sq.dataset.view = 'one';
  if ($('busyWorkflow')) $('busyWorkflow').hidden = true;
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
    const provenance = document.createElement('div'); provenance.className='busy-provenance'; provenance.textContent=identityText(x); if(provenance.textContent) kids.push(provenance);
    const since = (info && info.since) || x.since;
    const meta = [info && info.src ? t('busy.src.' + info.src) : '', Number.isInteger(since) ? t('busy.detail.since', { d: dur(nowS() - since) }) : '']
      .filter(Boolean).join(' · ');
    if (info?.order && orderMain) kids.push(orderCard(info.order, { t, toMain: orderMain, input: async label => window.prompt(label), stopped: orderStopped() }));
    if (meta) { const m = document.createElement('div'); m.className = 'bo-meta'; m.textContent = meta; kids.push(m); }
    const dl = document.createElement('dl');
    const row = (label, ...vals) => { const dt = document.createElement('dt'); dt.textContent = label; dl.append(dt); for (const v of vals) dl.append(v); };
    const dd = (text, code = false) => { const e = document.createElement('dd'); if (code) { const c = document.createElement('code'); c.setAttribute('data-aj-owner','aj-busy'); c.textContent = text; e.append(copyable(c)); } else e.textContent = text; return e; };
    if (oneData === 'old') { /* an older computer: what the list says is all there is */ }
    else if (!info) row(t('busy.detail.last'), dd(t('busy.detail.loading')));
    else {
      const agent = !['unit', 'file', 'workflow'].includes(info.src);  // a dispatch / command has no steps of ours, only a report
      if (info.last || agent || !info.report) row(t('busy.detail.last'), dd(info.last ? info.last.text : t('busy.detail.lastNone')));
      if (info.report) row(t('busy.detail.report'), dd(info.report.path, true), dd(info.report.head || t('busy.detail.reportNone')));
      if (info.cmd) row(t('busy.detail.cmd'), dd(info.cmd, true));
    }
    if (dl.childElementCount) kids.push(dl);
    if (oneData === 'old') { const n = document.createElement('div'); n.className = 'bo-note'; n.textContent = t('busy.detail.old'); kids.push(n); }
  } else {
    $('busyBig').replaceChildren();
    $('busyTitle').textContent = t('busy.detail.goneTitle');
    const n = document.createElement('div'); n.className = 'bo-note'; n.textContent = t('busy.detail.gone'); kids.push(n);
  }
  box.replaceChildren(...kids);
  settle($('busyDlg'));
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
  view = 'tasks';
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
// #553: share the input-message card's measured width; keep following it on rotation / resize.
function sizeLoadDialog() {
  const sq = $('loadDlg')?.querySelector('.sq'), om = $('om'), deck = $('deck');
  if (!sq) return;
  let width = om && !om.hidden ? om.getBoundingClientRect().width : 0;
  if (!width && deck) {
    const style = getComputedStyle(deck);
    width = deck.clientWidth - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0) - 24;
  }
  if (width > 0) sq.style.setProperty('--load-card-width', Math.round(width) + 'px');
  else sq.style.removeProperty('--load-card-width');
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
  sizeLoadDialog();
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
    disk: m && m.disk ? { busy: share(d.busy), free: share(d.free), free_gb: gb, scope: d.scope === 'workdir' ? 'workdir' : 'home', total_gb: Number.isFinite(d.total_gb) && d.total_gb > 0 ? Math.min(d.total_gb, 1e6) : null } : null,
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
  const diskInfo = d ? [t(d.scope === 'workdir' ? 'p553.disk.workdir' : 'p553.disk.home'), d.total_gb != null ? t('p553.disk.total', { gb: Math.round(d.total_gb) }) : '', free].filter(Boolean).join(' · ') : '';
  row('disk', d && d.busy != null ? pct(d.busy) : dash, d ? d.busy : null, diskInfo);
  row('net', r.net ? `↓ ${rateText(r.net.rx)}  ↑ ${rateText(r.net.tx)}` : dash, undefined);
}
/** `{"t":"res",…}` from the computer (only while this card watches). */
// P204: `use` absent (an older computer) → the 今天花费 row hides
export function setRes(m) { res = sanitizeRes(m); paintUseToday(sanitizeToday(m && m.use)); if (typeof m.load === 'number') { load = sanitizeLoad(m); paintLoad(); paintLoadNow(); } paintRes(); }
export const currentRes = () => res;
let today;
function paintUseToday(d) {
  if (d !== undefined) today = d;
  const v = $('loadUseV');
  if (!v) return;
  $('loadUse').hidden = today === null;
  v.textContent = today === undefined ? '…' : todayText(today);
}
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
export function setBusy(m) { taskCount = Number.isInteger(m?.task_count)&&m.task_count>=0?m.task_count:null; unknownCount = Number.isInteger(m?.unknown_count)&&m.unknown_count>=0?m.unknown_count:null; liveProcesses = Number.isInteger(m?.live_process_count)&&m.live_process_count>=0?m.live_process_count:null; tasks = sanitize(m); load = sanitizeLoad(m); cap = sanitizeCap(m); paintDots(); heard = true; paintDialog(); paintLoad(); if ($('loadDlg') && !$('loadDlg').hidden) paintLoadNow(); }
export const current = () => tasks.slice();

export function init(fns = {}) {
  if (typeof fns.orderMain === 'function') orderMain = fns.orderMain;
  if (typeof fns.orderStopped === 'function') orderStopped = fns.orderStopped;
  if (fns.name) agentName = fns.name;
  if (typeof fns.detail === 'function') fetchDetail = fns.detail;
  if (typeof fns.mention === 'function') mentionHook = fns.mention;
  if ($('busyBack')) $('busyBack').addEventListener('click', (e) => { e.stopPropagation(); backToList(); });
  $('busyDots').addEventListener('click', (e) => { e.stopPropagation(); open(); });   // @ix app.busy_list
  $('busyClose').addEventListener('click', close);
  for (const [id,v] of [['busyTasksTab','tasks'],['busyWorkflowTab','workflow']]) if ($(id)) {
    $(id).onclick=()=>switchView(v);
    $(id).onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'tasks':e.key==='End'?'workflow':view==='tasks'?'workflow':'tasks';switchView(next);$(next==='tasks'?'busyTasksTab':'busyWorkflowTab').focus();}};
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sweep(); });   // P207 追加 4
  if (fns.prompt) prompt = fns.prompt;
  // P181: 「换个样子」 under the robots, and a long press on the robots themselves (a tap still opens this card)
  if ($('busyTheme')) $('busyTheme').addEventListener('click', () => { close(); prompt('theme'); });
  longPress($('busyDots'), () => { close(); prompt('theme'); });   // @ix app.robots_theme
  $('busyDlg').addEventListener('click', (e) => { if (e.target === e.currentTarget) close(); });   // outside the square
  registerLayer({ element: () => $('busyDlg'), open: () => !$('busyDlg').hidden, close, fallback: () => $('busyDots'), rank: 5 });
  if ($('loadHit')) {
    $('loadHit').addEventListener('click', (e) => { e.stopPropagation(); openLoad(); });   // @ix app.load
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
  try { const ro = new ResizeObserver(() => { sizeLoad(); sizeLoadDialog(); }); for (const id of ['read', 'om', 'deck']) if ($(id)) ro.observe($(id)); }
  catch (_) { addEventListener('resize', () => { sizeLoad(); sizeLoadDialog(); }); }
  // Observe the row and its text as both resizing and font changes affect the budget.
  let frame = 0;
  const refit = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; paintDots(); }); };
  try {
    const ro = new ResizeObserver(refit);
    for (const el of [$('busyDots'), $('busyDots')?.parentElement, $('agent'), $('rmTime'), $('rmPart')]) if (el) ro.observe(el);
  } catch (_) { addEventListener('resize', refit); }
  document.fonts?.ready.then(refit);
  document.fonts?.addEventListener('loadingdone', refit);
  paintDots(); paintLoad(); sizeLoad();
}
