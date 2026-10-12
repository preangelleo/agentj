// F13 · P171 · Settings (the top-right gear, `s`, ⌘, / Ctrl+,): ONE full-screen page for everything — the ≡ menu is gone and
// its rows live here, grouped in section cards (wide screens: side navigation + cards in columns; narrow: one column).
// Sub-pages: the iPhone Add-to-Home-Screen guide and 遥控器管理 (js/remotes.js). Works on every view but the camera.
// Host-side values go through `pref_set` → `pref_res` → the normal `preferences` broadcast (PROTOCOL §10, contract C6 /
// F14): optional risk warnings and session mode are phone-editable too. Reply size is phone-local.
// PR1: nothing here leaves the page except inside the Noise session (pref_set / push_off), and no customer text is shown.
import VERSION from '../version.js';
import { RelayMD } from './md.js';
import { t, fillText, onLang } from './t.js';
import { el } from './ui.js';
import { ask1, isReady, peer } from './session.js';
import * as push from './push.js';
import * as sfx from './sfx.js';
import * as shotclip from './shotclip.js';
import * as remotes from './remotes.js';
import * as mail from './mail.js';

let H = { agentName: () => 'Agent J', paired: () => false, connected: () => false, unpair: () => {}, reload: () => location.reload(),
  view: () => document.body.dataset.view, forget: () => {} };
let prefs = null;                                    // the last `preferences` value from the host (null = none yet / older host)
let hostInfo = null;                                 // its `host` block (host ≥ 0.15: version, settable keys, paired phones)
let noPrefSet = false;                               // the host never answered a pref_set → show commands
let sub = 'main';                                    // 'main' | 'ios' | 'remotes' | 'mail'

// ---------------------------------------------------------------- reply text size (phone-local)
export const FONT_STEPS = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.6];
const FONT_KEY = 'aj.fontScale';
let fontAt = FONT_STEPS.indexOf(1);
try { const v = Number(localStorage.getItem(FONT_KEY)); const i = FONT_STEPS.indexOf(v); if (i >= 0) fontAt = i; } catch { /* blocked storage: default */ }
function applyFont() { document.documentElement.style.setProperty('--rs', String(FONT_STEPS[fontAt])); }
applyFont();
export function fontScale() { return FONT_STEPS[fontAt]; }
export function setFontStep(i) {
  fontAt = Math.min(FONT_STEPS.length - 1, Math.max(0, i));
  applyFont();
  try { localStorage.setItem(FONT_KEY, String(FONT_STEPS[fontAt])); } catch { /* blocked storage: this visit only */ }
  renderFont();
}
// P157: Jev layer-2 threshold, 0.50–0.95 in steps of 0.05 (host validates the same range)
const JEV_MIN = 10, JEV_MAX = 19;        // in twentieths: 10/20 = 0.50 … 19/20 = 0.95
const jevAt = () => {
  const v = Number(pget('approval.jev_threshold', 0.7));
  return Math.min(JEV_MAX, Math.max(JEV_MIN, Math.round((Number.isFinite(v) ? v : 0.7) * 20)));
};
const jevStr = (n) => (n / 20).toFixed(2);
function renderJev() {
  const n = jevAt();
  el('set-jev-val').textContent = jevStr(n);
  el('set-jev-minus').disabled = n <= JEV_MIN;
  el('set-jev-plus').disabled = n >= JEV_MAX;
  el('set-jev-cmd').textContent = cmdFor('approval.jev_threshold', jevStr(Math.max(JEV_MIN, n - 1)));
}
function setJev(n) {
  n = Math.min(JEV_MAX, Math.max(JEV_MIN, n));
  el('set-jev-val').textContent = jevStr(n);
  setPref('approval.jev_threshold', Number(jevStr(n)));
}
function renderFont() {
  el('set-font-val').textContent = t('set.fontVal', { n: Math.round(FONT_STEPS[fontAt] * 100) });
  el('set-font-minus').disabled = fontAt === 0;
  el('set-font-plus').disabled = fontAt === FONT_STEPS.length - 1;
  el('set-font-reset').disabled = FONT_STEPS[fontAt] === 1;
}

// ---------------------------------------------------------------- P154 send-sound volume (phone-local, js/sfx.js)
function renderSfx() {
  const v = sfx.volume();
  el('set-sfx').value = String(v);
  el('set-sfx-val').textContent = v ? v + '%' : t('set.sfxOff');
  el('set-sfx-mute').setAttribute('aria-pressed', String(v === 0));
  el('set-sfx-test').disabled = v === 0;
}
function setSfx(v, hear) {
  const now = sfx.setVolume(v);
  renderSfx();
  if (hear && now > 0) sfx.preview();
}

// P154 截图自动进附件: only where a page can read the clipboard (computer Chromium); hidden elsewhere.
async function renderShot() {
  const ok = await shotclip.supported();
  el('set-shot-row').hidden = !ok;
  if (ok) tgl('set-shot', shotclip.enabled());
}

// ---------------------------------------------------------------- Add to Home Screen (Android prompt / iOS guide)
let installEvt = null;
addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installEvt = e; if (isOpen()) renderA2hs(); });
addEventListener('appinstalled', () => { installEvt = null; if (isOpen()) { say(t('set.a2hsAccepted')); renderA2hs(); } });
function a2hsKind() {
  if (push.standalone()) return 'done';
  if (installEvt) return 'prompt';
  if (push.isIOS()) return 'ios';
  return 'menu';
}
function renderA2hs() {
  const k = a2hsKind(), b = el('set-a2hs');
  el('set-a2hs-row').hidden = k === 'done';                       // already opened from the Home Screen: nothing to add
  b.hidden = k === 'menu' || k === 'done';
  b.textContent = t(k === 'ios' ? 'set.a2hsHow' : 'set.a2hsBtn');
  b.dataset.kind = k;
  fillText(el('set-a2hs-text'), t(k === 'menu' ? 'set.a2hsDesktop' : 'set.a2hsNote'));
}
async function onA2hs() {
  const k = el('set-a2hs').dataset.kind;
  if (k === 'ios') return showSub('ios');
  if (k !== 'prompt' || !installEvt) return;
  const e = installEvt;
  installEvt = null;
  try {
    await e.prompt();
    const c = await e.userChoice;
    if (c && c.outcome === 'accepted') say(t('set.a2hsAccepted'));
  } catch { /* the browser refused to show it twice */ }
  renderA2hs();
}

// ---------------------------------------------------------------- host preferences (pref_set)
export const PREF_KEYS = ['appearance.language', 'appearance.theme', 'voice.speak_replies', 'voice.wake_enabled', 'agent.high_risk_warnings', 'agent.session_mode', 'agent.isolation', 'agent.allow_docker'];
PREF_KEYS.push('updates.mode', 'heartbeat.enabled', 'heartbeat.interval_minutes', 'approval.jev_threshold', 'approval.goal_auto');
// P156: the heartbeat's idle interval steps through these (minutes); a value set elsewhere (agentj heartbeat set 45m) is shown as is
export const HB_STEPS = [15, 30, 60, 120, 240, 480, 1440];
export function hbNext(n) { return HB_STEPS.find((x) => x > n) || HB_STEPS[0]; }
function hbText(n) { return n % 60 === 0 ? t('set.heartbeatHour', { n: n / 60 }) : t('set.heartbeatMin', { n }); }
const pget = (key, dflt) => {
  let v = prefs;
  for (const p of key.split('.')) v = v && typeof v === 'object' ? v[p] : undefined;
  return v === undefined || v === null ? dflt : v;
};
const cmdFor = (key, value) => `agentj config set ${key} ${value}`;
function problemText(p) {
  const s = typeof p === 'string' ? p : p && typeof p === 'object' ? (p.error || p.message || p.code || '') : '';
  return String(s || '?').slice(0, 200);
}
/** → true when the host saved it. The `preferences` broadcast that follows re-renders the panel. */
export async function setPref(key, value) {
  if (!PREF_KEYS.includes(key)) return false;
  if (!isReady()) { say(t('set.offline')); return false; }
  // a host that sent preferences without the `host` block (before 0.15), or one that does not list this key: no round
  // trip to wait for — show the command at once
  if ((prefs && !hostInfo) || (hostInfo && Array.isArray(hostInfo.settable) && !hostInfo.settable.includes(key))) {
    noPrefSet = true; say(''); showCmd(key, value); render(); return false;
  }
  say(t('set.saving'));
  const m = await ask1({ t: 'pref_set', key, value }, 'pref_res', 8000);
  if (m.t === 'pref_res' && m.ok === true) { noPrefSet = false; hideCmd(); say(t('set.saved')); return true; }
  if (m.t === 'pref_res') say(t('set.saveFail', { why: problemText(m.problem) }));
  else if (m.t === 'timeout') { noPrefSet = true; say(''); showCmd(key, value); }
  else say(t('set.offline'));
  render();
  return false;
}
function showCmd(key, value) {
  const box = el(key === 'appearance.language' ? 'set-lang-cmd' : 'set-pref-cmd');
  box.querySelector('code').textContent = cmdFor(key, value);
  box.hidden = false;
}
function hideCmd() { el('set-lang-cmd').hidden = true; el('set-pref-cmd').hidden = true; }

const curTheme = () => { const v = document.documentElement.getAttribute('data-theme'); return v === 'light' || v === 'dark' ? v : 'system'; };
/** Same storage as brand/ui.js's theme switch and brand/lang.js's pre-apply in <head> (localStorage "aj.theme", no flash). */
export function applyThemeLocal(want) {
  const de = document.documentElement;
  if (want === 'light' || want === 'dark') de.setAttribute('data-theme', want); else de.removeAttribute('data-theme');
  try { if (want === 'light' || want === 'dark') localStorage.setItem('aj.theme', want); else localStorage.removeItem('aj.theme'); } catch { /* blocked storage */ }
}

// ---------------------------------------------------------------- render
function say(s) { el('set-status').textContent = s; }
const tgl = (id, on) => { el(id).setAttribute('aria-checked', String(!!on)); };
function renderPush() {
  const st = push.state(), b = el('set-push');
  let key;
  if (!push.pushSupported()) key = push.isIOS() && !push.standalone() ? 'set.pushStateIos' : 'set.pushStateNo';
  else key = { on: 'set.pushStateOn', offer: 'set.pushStateOff', ios: 'set.pushStateIos', denied: 'set.pushStateDenied', failed: 'push.failed' }[st] || 'set.pushStateWait';
  fillText(el('set-push-text'), t(key));
  b.hidden = !['on', 'offer', 'failed'].includes(st) || !push.pushSupported();
  b.textContent = t(st === 'on' ? 'set.pushDisable' : 'set.pushEnable');
  b.dataset.act = st === 'on' ? 'off' : 'on';
}
export function render() {
  const lang = window.AJLang ? window.AJLang.get() : 'zh';
  for (const b of document.querySelectorAll('[data-set-lang]')) b.setAttribute('aria-pressed', String(b.dataset.setLang === lang));
  const theme = curTheme();
  for (const b of document.querySelectorAll('[data-set-theme]')) b.setAttribute('aria-pressed', String(b.dataset.setTheme === theme));
  tgl('set-speak', pget('voice.speak_replies', false) === true);
  tgl('set-wake', pget('voice.wake_enabled', false) === true);
  renderFont();
  renderSfx();
  renderShot();
  renderPush();
  renderA2hs();
  el('set-webver').textContent = t('set.webVer', { v: VERSION.version });
  const hv = hostInfo && typeof hostInfo.version === 'string' && /^[0-9A-Za-z.+-]{1,32}$/.test(hostInfo.version) ? hostInfo.version : null;
  el('set-hostver').textContent = hv ? t('set.hostVer', { v: hv }) : t('set.hostVerNone');
  const paired = H.paired();
  for (const n of el('settings').querySelectorAll('[data-paired]')) n.hidden = !paired;
  el('keysBtn').hidden = !paired || H.view() !== 'chat';
  renderRemotesRow();
  const name = H.agentName();
  el('set-computer').textContent = !H.paired() ? t('set.computerNone') : t(H.connected() ? 'set.computerOn' : 'set.computerOff', { name });
  const risk = pget('agent.high_risk_warnings', false) !== false;
  tgl('set-risk', risk);
  const goalAuto = pget('approval.goal_auto', true) !== false;          // P158: goal-mode 10-second countdown
  tgl('set-goal', goalAuto);
  el('set-goal-cmd').textContent = cmdFor('approval.goal_auto', goalAuto ? 'false' : 'true');
  el('set-risk-cmd').textContent = cmdFor('agent.high_risk_warnings', risk ? 'false' : 'true');
  renderJev();
  const mode = pget('agent.session_mode', 'independent') === 'shared' ? 'shared' : 'independent';   // P174: independent by default
  el('set-updates').textContent = t(pget('updates.mode', 'auto') === 'ask' ? 'set.updatesAsk' : 'set.updatesAuto');
  el('set-mode').textContent = t(mode === 'shared' ? 'set.modeShared' : 'set.modeIndependent');
  el('set-mode-cmd').textContent = cmdFor('agent.session_mode', mode === 'shared' ? 'independent' : 'shared');
  const iso = pget('agent.isolation', true) !== false, dock = pget('agent.allow_docker', false) === true;
  tgl('set-iso', iso);
  el('set-iso-cmd').textContent = cmdFor('agent.isolation', iso ? 'false' : 'true');
  tgl('set-docker', dock);
  el('set-docker-cmd').textContent = cmdFor('agent.allow_docker', dock ? 'false' : 'true');
  // P156: a host before 0.20 has no heartbeat: the row stays hidden
  const hbKnown = !!(hostInfo && Array.isArray(hostInfo.settable) && hostInfo.settable.includes('heartbeat.enabled'));
  el('set-hb-row').hidden = !hbKnown;
  const hbOn = pget('heartbeat.enabled', true) !== false, hbN = Number(pget('heartbeat.interval_minutes', 60)) || 60;
  tgl('set-hb', hbOn);
  el('set-hb-every').textContent = hbText(hbN);
  el('set-hb-every').disabled = !hbOn;
}
/** 「遥控器管理」 row: 已用 N / 10 (from the last dev_list / devs, else the preferences host block). */
function renderRemotesRow() {
  const c = remotes.count();
  el('set-remotes-count').textContent = c ? t('set.remotesNote', { n: c.n, max: c.max }) : '';
}
/** P179: one paired remote as the host last described it ({id, name, label, named_by, self_name, …}), or null. */
export function deviceInfo(id) {
  if (!id || !hostInfo || !Array.isArray(hostInfo.devices)) return null;
  const d = hostInfo.devices.find((x) => x && x.id === id);
  if (!d) return null;
  const s = (v) => (typeof v === 'string' ? v.slice(0, 64) : '');
  return { id, name: s(d.name), label: s(d.label) || s(d.name), named_by: d.named_by === 'owner' ? 'owner' : 'device', self_name: s(d.self_name) };
}
/** app.js: every `preferences` message from the host (value + the host ≥ 0.15 `host` block). */
export function setPrefs(value, host) {
  prefs = value && typeof value === 'object' ? value : null;
  hostInfo = host && typeof host === 'object' ? host : null;
  if (isOpen()) render();
}

// ---------------------------------------------------------------- open / close / sub-view
export const isOpen = () => !el('settings').hidden;
const SUB_TITLE = { main: 'set.title', ios: 'set.iosTitle', remotes: 'rm.title', mail: 'mail.nav' };
function showSub(v) {
  const from = sub;
  sub = v;
  el('set-main').hidden = v !== 'main';
  el('set-ios').hidden = v !== 'ios';
  el('set-remotes').hidden = v !== 'remotes';
  el('set-mail').hidden = v !== 'mail';
  el('set-back').hidden = v === 'main';
  el('set-title').textContent = t(SUB_TITLE[v]);
  el('settings').dataset.sub = v;
  if (from === 'remotes' && v !== 'remotes') remotes.close();
  if (v === 'remotes') { remotes.open(); return; }
  const back = { ios: 'set-a2hs', remotes: 'set-remotes-open', mail: 'set-mail-nav' }[from];
  (v === 'main' ? el(back || 'set-close') : el('set-back')).focus({ preventScroll: v === 'main' && !back });
  if (v === 'main') renderRemotesRow();
  if (v === 'mail') mail.load();   // P170
}
/** Opens Settings (any view but the camera); `page` = 'remotes' lands straight on 遥控器管理. */
export function open(page = 'main') {
  if (document.body.dataset.view === 'scan') return;
  say('');
  if (!noPrefSet) hideCmd();
  render();
  const box = el('settings');
  box.hidden = false; box.dataset.modalOpen = '1';
  sub = 'main'; el('set-main').hidden = false; el('set-ios').hidden = true; el('set-remotes').hidden = true; el('set-mail').hidden = true; el('set-back').hidden = true;
  box.dataset.sub = 'main';
  el('set-title').textContent = t('set.title');
  el('setBtn').setAttribute('aria-expanded', 'true');
  el('set-main').scrollTop = 0;
  markSection(null);
  el('settings').querySelector('.setbox').focus();
  if (page === 'remotes' && H.paired()) showSub('remotes');
  else if (H.paired() && isReady()) remotes.refresh().then(renderRemotesRow);   // 已用 N / 10 from the computer itself
}
export function back() { if (sub !== 'main') showSub('main'); else close(); }
/** P184: 「转发到邮箱」 with no mailbox yet → Settings → 邮件通道. */
export function openMail() { open(); if (isOpen()) showSub('mail'); }
export function close() {
  const box = el('settings');
  if (box.hidden) return;
  if (sub === 'remotes') remotes.close();
  sub = 'main';
  box.hidden = true; delete box.dataset.modalOpen;
  el('setBtn').setAttribute('aria-expanded', 'false');
  if (document.activeElement && box.contains(document.activeElement)) document.activeElement.blur();
}

/** Update / refresh the app: ask the browser for a fresh service worker, unregister every one of ours, drop every Cache
 *  Storage entry, then reload (the page itself is `no-store`, so the reload fetches the new files). The metadata-only
 *  version notice checks version.json; this action remains an explicit owner choice. */
export async function refreshApp() {
  say(t('set.refreshGo'));
  try {
    if ('serviceWorker' in navigator) {
      for (const r of await navigator.serviceWorker.getRegistrations()) {
        try { await r.update(); } catch { /* offline: unregistering is still right */ }
        await r.unregister();
      }
    }
    if (globalThis.caches) for (const k of await caches.keys()) await caches.delete(k);
  } catch {
    say(t('set.refreshFail'));
  }
  H.reload();
}

// ---------------------------------------------------------------- side navigation (wide screens)
function markSection(id) {
  for (const b of document.querySelectorAll('[data-set-jump]')) b.setAttribute('aria-current', String(b.dataset.setJump === id));
}
function jump(id) {
  const target = el(id);
  if (!target || target.hidden) return;
  target.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  markSection(id);
  jumpedAt = performance.now();       // the smooth scroll that follows must not re-mark another section
  const h = target.querySelector('h3');
  if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
}
let jumpedAt = -1e9;
function followScroll() {          // the side navigation marks the section at the top of the scrolled area
  if (performance.now() - jumpedAt < 1200) return;
  const main = el('set-main');
  const top = main.getBoundingClientRect().top + 24;
  let cur = null;
  for (const c of main.querySelectorAll('.setcard')) {
    if (c.hidden) continue;
    if (c.getBoundingClientRect().top <= top) cur = c.id; else if (!cur) cur = c.id;
  }
  markSection(cur);
}

/** hooks: { agentName(), paired(), connected(), myId(), unpair(), reload(), view(), forget(), hostDevices() } — app.js wires them. */
export function configure(hooks) {
  H = { ...H, ...hooks };
  remotes.configure({ myId: () => (H.myId ? H.myId() : null), hostDevices: () => hostInfo, forget: () => { close(); H.forget(); },
    changed: renderRemotesRow });
  for (const b of document.querySelectorAll('[data-set-jump]')) b.addEventListener('click', () => jump(b.dataset.setJump));
  let raf = 0;
  el('set-main').addEventListener('scroll', () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(followScroll); }, { passive: true });
  el('set-remotes-open').addEventListener('click', () => showSub('remotes'));
  el('setBtn').addEventListener('click', () => (isOpen() ? close() : open()));
  el('set-close').addEventListener('click', close);
  el('set-back').addEventListener('click', () => showSub('main'));
  el('settings').addEventListener('click', (e) => { if (e.target === el('settings')) close(); });
  // keysBtn: relay.js opens the shortcut page ABOVE Settings (P187: its back / Esc returns here)
  for (const b of document.querySelectorAll('[data-set-lang]')) b.addEventListener('click', () => {
    const l = b.dataset.setLang;
    if (window.AJLang && window.AJLang.get() !== l) window.AJLang.set(l);   // the page follows at once
    render();
    setPref('appearance.language', l);
  });
  for (const b of document.querySelectorAll('[data-set-theme]')) b.addEventListener('click', () => {
    applyThemeLocal(b.dataset.setTheme);
    render();
    setPref('appearance.theme', b.dataset.setTheme);
  });
  for (const id of ['set-speak', 'set-wake', 'set-risk', 'set-goal', 'set-iso', 'set-docker', 'set-hb']) el(id).addEventListener('click', () => {
    const b = el(id), next = b.getAttribute('aria-checked') !== 'true';
    tgl(id, next);
    setPref(b.dataset.pref, next);
  });
  el('set-updates').addEventListener('click', () => setPref('updates.mode', pget('updates.mode', 'auto') === 'auto' ? 'ask' : 'auto'));
  el('set-hb-every').addEventListener('click', () => setPref('heartbeat.interval_minutes', hbNext(Number(pget('heartbeat.interval_minutes', 60)) || 60)));
  el('set-mode').addEventListener('click', () => setPref('agent.session_mode', pget('agent.session_mode', 'independent') === 'shared' ? 'independent' : 'shared'));
  el('set-jev-minus').addEventListener('click', () => setJev(jevAt() - 1));
  el('set-jev-plus').addEventListener('click', () => setJev(jevAt() + 1));
  el('set-font-minus').addEventListener('click', () => setFontStep(fontAt - 1));
  el('set-font-plus').addEventListener('click', () => setFontStep(fontAt + 1));
  el('set-font-reset').addEventListener('click', () => setFontStep(FONT_STEPS.indexOf(1)));
  el('set-sfx').addEventListener('input', () => setSfx(el('set-sfx').value, false));
  el('set-sfx').addEventListener('change', () => setSfx(el('set-sfx').value, true));   // let go = hear it once
  el('set-sfx-mute').addEventListener('click', () => setSfx(sfx.volume() ? 0 : sfx.lastLevel(), true));
  el('set-sfx-test').addEventListener('click', () => sfx.preview());
  el('set-shot').addEventListener('click', async () => {
    const on = await shotclip.setEnabled(el('set-shot').getAttribute('aria-checked') !== 'true');
    tgl('set-shot', on);
    if (on) say(t('set.shotOn'));
  });
  el('set-push').addEventListener('click', async () => {
    if (el('set-push').dataset.act === 'off') { if (await push.disablePush()) say(t('set.pushOffDone')); }
    else await push.enablePush();
    renderPush();
  });
  push.onChange(() => { if (isOpen()) renderPush(); });
  el('set-refresh').addEventListener('click', refreshApp);
  el('set-a2hs').addEventListener('click', onA2hs);
  el('set-mail-nav').addEventListener('click', () => showSub('mail'));   // P170
  mail.configure();
  for (const b of document.querySelectorAll('[data-copy]')) b.addEventListener('click', async () => {
    const ok = await RelayMD.copyText(el(b.dataset.copy).textContent);
    say(ok ? t('set.copied') : t('r.copy.fail'));
  });
  onLang(() => { if (isOpen()) { render(); el('set-title').textContent = t(SUB_TITLE[sub]); } });
}

/** relay.js: the keyboard opens it (`s` outside a field, ⌘, / Ctrl+, anywhere on the chat screen). */
export function toggleFromKey() { if (isOpen()) close(); else open(); }
// for screens tests: what the panel would send
export const _debug = () => ({ prefs, hostInfo, noPrefSet, sub, font: FONT_STEPS[fontAt], sfx: sfx._debug(), remotes: remotes._debug() });
/** app.js: host → device §24 messages (devs without r, dev_pending, dev_pair_end). */
export function onRemotes(m) {
  if (m.t === 'devs') { remotes.onDevs(m); renderRemotesRow(); }
  else if (m.t === 'dev_pending') remotes.onPending(m);
  else if (m.t === 'dev_pair_end') remotes.onPairEnd(m);
}
/** app.js: `pair_ev` (P153 `pair_new`). P165: 添加遥控器 lives on the P171 遥控器管理 page (`dev_add`), whose own messages drive
 *  it; the P153 confirmation card still comes to every remote. Kept so an older host's `pair_ev` is ignored, not an error. */
export function onPairEv() {}
/** app.js (P165): a P153 pair card arrived — keep this remote's pending add alive while Settings steps aside. */
export function onPairCard() { remotes.onPairCard(); }
