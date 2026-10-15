// F13 · P171 · Settings (the top-right gear, `s`, ⌘, / Ctrl+,): ONE full-screen page for everything — the ≡ menu is gone and
// its rows live here, grouped in section cards (wide screens: side navigation + cards in columns; narrow: one column).
// Sub-pages: the iPhone Add-to-Home-Screen guide and 遥控器管理 (js/remotes.js). Works on every view but the camera.
// Host-side values go through `pref_set` → `pref_res` → the normal `preferences` broadcast (PROTOCOL §10, contract C6 /
// F14): optional risk warnings and session mode are phone-editable too. Reply size is phone-local.
// PR1: nothing here leaves the page except inside the Noise session (pref_set / push_off), and no customer text is shown.
import { longPress } from './longpress.js';
import VERSION from '../version.js';
import { RelayMD } from './md.js';
import { t, fillText, onLang } from './t.js';
import { el, noticeSheet } from './ui.js';
import { ask1, isReady, peer, sendApp } from './session.js';
import * as push from './push.js';
import * as sfx from './sfx.js';
import * as shotclip from './shotclip.js';
import * as remotes from './remotes.js';
import * as mail from './mail.js';
import * as authrescue from './authrescue.js';   // P258
import * as contacts from './contacts.js';
import * as ctmanage from './ctmanage.js';   // P207 §3: Settings → 通讯录
import * as imsg from './imessage.js';      // P274: Settings → iMessage 助手 (Mac hosts only)

let H = { agentName: () => 'Agent J', paired: () => false, connected: () => false, unpair: () => {}, reload: () => location.reload(),
  view: () => document.body.dataset.view, forget: () => {} };
let prefs = null;                                    // the last `preferences` value from the host (null = none yet / older host)
let hostInfo = null;                                 // its `host` block (host ≥ 0.15: version, settable keys, paired phones)
let noPrefSet = false;                               // the host never answered a pref_set → show commands
let prefFail = null;                                 // P302: why the last setPref returned false (old / fail / offline + text)
let sub = 'main';                                    // 'main' | 'ios' | 'remotes' | 'mail' | 'contacts' | 'imessage'

// ---------------------------------------------------------------- reply text size (phone-local)
const CHAN = 'Telegram';
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
PREF_KEYS.push('updates.mode', 'heartbeat.enabled', 'heartbeat.interval_minutes', 'approval.jev_threshold', 'approval.goal_auto', 'quota.auto_switch');
PREF_KEYS.push('updates.auto_install');                 // P203: half of the 升级方式 (自动 = install when idle) choice
PREF_KEYS.push(...imsg.PREF_KEYS);       // P274: the iMessage card's parameters (host lists them only on a Mac)
PREF_KEYS.push('heartbeat.daily_at');   // P207 §3: 「每天固定时间唤醒」 "HH:MM" | "" (host ≥ 0.20.2 lists it in settable)
PREF_KEYS.push('ui.card_hot_action');   // P285: the reply card's bottom-left hot key (hotkey.js writes it from its own sheet)
// P207 §3 (was P156's ⇄ stepper): the idle interval is a dropdown of these (minutes) + a custom number 5–1440; a value set
// elsewhere (agentj heartbeat set 45m) shows as 「自定义」 with that number
export const HB_STEPS = [30, 60, 120, 180, 240, 360, 480, 720, 1440];
export const HB_MIN = 5, HB_MAX = 1440;
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
function hbText(n) { return n % 60 === 0 ? t('set.heartbeatHour', { n: n / 60 }) : t('set.heartbeatMin', { n }); }
const pget = (key, dflt) => {
  let v = prefs;
  for (const p of key.split('.')) v = v && typeof v === 'object' ? v[p] : undefined;
  return v === undefined || v === null ? dflt : v;
};
const cmdFor = (key, value) => `agentj config set ${key} ${value}`;
// P203: the one 「空闲时自动更新」 switch = updates.auto_install (night / idle install) + updates.mode (upgrade notices)
const autoUpdates = () => pget('updates.auto_install', true) !== false && pget('updates.mode', 'auto') !== 'ask';
function problemText(p) {
  const s = typeof p === 'string' ? p : p && typeof p === 'object' ? (p.error || p.message || p.code || '') : '';
  return String(s || '?').slice(0, 200);
}
/** → true when the host saved it. The `preferences` broadcast that follows re-renders the panel. */
export async function setPref(key, value) {
  prefFail = null;
  if (!PREF_KEYS.includes(key)) return false;
  if (!isReady()) { say(t('set.offline')); prefFail = { kind: 'offline' }; return false; }
  // a host that sent preferences without the `host` block (before 0.15), or one that does not list this key: no round
  // trip to wait for — show the command at once
  if ((prefs && !hostInfo) || (hostInfo && Array.isArray(hostInfo.settable) && !hostInfo.settable.includes(key))) {
    noPrefSet = true; say(''); showCmd(key, value); render(); prefFail = { kind: 'old', cmd: cmdFor(key, value) }; return false;
  }
  say(t('set.saving'), true);
  const m = await ask1({ t: 'pref_set', key, value }, 'pref_res', 8000);
  if (m.t === 'pref_res' && m.ok === true) { noPrefSet = false; hideCmd(); say(t('set.saved')); return true; }
  if (m.t === 'pref_res') { say(t('set.saveFail', { why: problemText(m.problem) })); prefFail = { kind: 'fail', why: problemText(m.problem), cmd: cmdFor(key, value) }; }
  else if (m.t === 'timeout') { noPrefSet = true; say(''); showCmd(key, value); prefFail = { kind: 'old', cmd: cmdFor(key, value) }; }
  else { say(t('set.offline')); prefFail = { kind: 'offline' }; }
  render();
  return false;
}
/** P285: does the connected host take this key from the phone? (an older one lists no such key in `settable`) */
export const prefSettable = (key) => !!hostInfo && Array.isArray(hostInfo.settable) && hostInfo.settable.includes(key);
/** P302 (Leo 10-20: 「点了没反应」): a switch that did not change says why in a centred card, not only in a line far up the page. */
export function explainPrefFail() {
  const f = prefFail;
  prefFail = null;
  if (!f) return;
  const text = f.kind === 'offline' ? t('p302.prefOffline') : t(f.kind === 'old' ? 'p302.prefOld' : 'p302.prefFail', { cmd: f.cmd, why: f.why || '' });
  noticeSheet(t('p302.prefTitle'), text, t('p302.ok'));
}
function showCmd(key, value) {
  const box = el(key === 'appearance.language' ? 'set-lang-cmd' : 'set-pref-cmd');
  box.querySelector('code').textContent = cmdFor(key, value === '' ? '""' : value);
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
// P207 §3: the notice bar under the header closes with its ✕ and fades by itself after ~4 s (it stayed forever before);
// only 「正在保存…」 waits for its answer.
export const SAY_MS = 4000;
let sayTimer = 0;
function say(s, keep = false) {
  clearTimeout(sayTimer);
  el('set-status').textContent = s || '';
  el('set-status-bar').hidden = !s;
  el('set-status-x').hidden = !s;
  if (s && !keep) sayTimer = setTimeout(() => say(''), SAY_MS);
}
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
/** P207 §3: a two-way switch — aria-checked = the RIGHT option; its two labels say which side is which (bold = current). */
function twoWay(id, right) {
  const sw = el(id);
  sw.setAttribute('aria-checked', String(!!right));
  for (const l of sw.parentElement.querySelectorAll('.tw-lab')) l.setAttribute('aria-current', String((l.dataset.side === 'right') === !!right));
}
/** P294: say where isolation and Docker apply. Shared mode keeps isolation live (scheduled tasks, the OpenCode serve
 *  Agent J starts), so it only gets a note; Docker means nothing without isolation, so it greys out until isolation is on. */
function isoScope(iso, mode) {
  el('set-iso-shared').hidden = mode !== 'shared';
  el('set-docker-off').hidden = iso;
  el('set-docker').disabled = !iso;
  el('set-docker').closest('.setrow').classList.toggle('setoff', !iso);
}
/** P207 §3: the heartbeat interval dropdown (+ custom minutes) and the daily wake-up time. */
function renderHb(on, n) {
  const sel = el('set-hb-every');
  for (const o of sel.options) if (o.value !== 'custom') o.textContent = hbText(Number(o.value));
  const preset = HB_STEPS.includes(n);
  if (document.activeElement !== sel && !(sel.value === 'custom' && !el('set-hb-custom').hidden && !preset)) sel.value = preset ? String(n) : 'custom';
  el('set-hb-custom').hidden = sel.value !== 'custom';
  if (document.activeElement !== el('set-hb-custom-n')) el('set-hb-custom-n').value = String(n);
  sel.disabled = !on; el('set-hb-custom-n').disabled = !on;
  const dailyKnown = !!(hostInfo && Array.isArray(hostInfo.settable) && hostInfo.settable.includes('heartbeat.daily_at'));
  el('set-hb-daily-row').hidden = !dailyKnown;
  const at = String(pget('heartbeat.daily_at', '') || '');
  const daily = HHMM.test(at) || dailyOn;
  tgl('set-hb-daily', daily);
  el('set-hb-daily').disabled = !on;
  el('set-hb-time').hidden = !daily;
  el('set-hb-time').disabled = !on;
  if (HHMM.test(at) && document.activeElement !== el('set-hb-time')) el('set-hb-time').value = at;
}
let dailyOn = false;           // the daily switch turned on, time not picked / saved yet
function hbInterval(v) {
  const n = Math.round(Number(v));
  if (!Number.isFinite(n) || n < HB_MIN || n > HB_MAX) { say(t('p207set.hb.range', { min: HB_MIN, max: HB_MAX })); return; }
  setPref('heartbeat.interval_minutes', n);
}
export function render() {
  const lang = window.AJLang ? window.AJLang.get() : 'zh';
  for (const b of document.querySelectorAll('[data-set-lang]')) b.setAttribute('aria-pressed', String(b.dataset.setLang === lang));
  twoWay('set-lang', lang === 'en');                                    // P207: left 中文 · right English
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
  // P283: the owner's Telegram is re-paired with a one-time code; the channel's name is data, not dictionary copy
  el('set-chan').textContent = CHAN;
  el('set-tgpair').textContent = t('set.chanPair', { name: CHAN });
  fillText(el('set-chan-note'), t('set.chanNote', { name: CHAN }) + ' ' + t('set.chanGuests', { name: CHAN }));   // P297: the same bot as customer service
  const risk = pget('agent.high_risk_warnings', false) !== false;
  tgl('set-risk', risk);
  const goalAuto = pget('approval.goal_auto', true) !== false;          // P158: goal-mode 10-second countdown
  tgl('set-goal', goalAuto);
  el('set-goal-cmd').textContent = cmdFor('approval.goal_auto', goalAuto ? 'false' : 'true');
  const qSwitch = pget('quota.auto_switch', false) === true;            // P254: quota alarm → new tasks to another AI account
  tgl('set-qswitch', qSwitch);
  el('set-qswitch-cmd').textContent = cmdFor('quota.auto_switch', qSwitch ? 'false' : 'true');
  el('set-risk-cmd').textContent = cmdFor('agent.high_risk_warnings', risk ? 'false' : 'true');
  renderJev();
  const mode = pget('agent.session_mode', 'independent') === 'shared' ? 'shared' : 'independent';   // P174: independent by default
  twoWay('set-updates', !autoUpdates());     // P207 two-way (left 自动 · right 先问我) over P203's two keys: 自动 = install when idle
  twoWay('set-mode', mode === 'shared');                                // P207: left 独立 · right 共享
  el('set-mode-cmd').textContent = cmdFor('agent.session_mode', mode === 'shared' ? 'independent' : 'shared');
  const iso = pget('agent.isolation', true) !== false, dock = pget('agent.allow_docker', false) === true;
  tgl('set-iso', iso);
  el('set-iso-cmd').textContent = cmdFor('agent.isolation', iso ? 'false' : 'true');
  tgl('set-docker', dock);
  el('set-docker-cmd').textContent = cmdFor('agent.allow_docker', dock ? 'false' : 'true');
  isoScope(iso, mode);
  // P156: a host before 0.20 has no heartbeat: the row stays hidden
  const hbKnown = !!(hostInfo && Array.isArray(hostInfo.settable) && hostInfo.settable.includes('heartbeat.enabled'));
  el('set-hb-row').hidden = !hbKnown;
  const hbOn = pget('heartbeat.enabled', true) !== false, hbN = Number(pget('heartbeat.interval_minutes', 60)) || 60;
  tgl('set-hb', hbOn);
  renderHb(hbOn, hbN);
  el('set-owner-nav').hidden = !contacts.forwardAvailable();   // P194: slot 0 needs a host with the address book
  el('set-contacts-nav').hidden = !contacts.forwardAvailable(); // P207: the address book page, same host support
  el('set-imsg-nav').hidden = !imsg.available();               // P274: only when the computer is a Mac
  if (sub === 'imessage') { if (imsg.available()) imsg.render(); else showSub('main'); }
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
const SUB_TITLE = { main: 'set.title', ios: 'set.iosTitle', remotes: 'rm.title', mail: 'mail.nav', contacts: 'p207set.ct.nav', auth: 'ar.nav', imessage: 'imsg.nav' };
function showSub(v, add = false) {
  const from = sub;
  sub = v;
  el('set-main').hidden = v !== 'main';
  el('set-ios').hidden = v !== 'ios';
  el('set-remotes').hidden = v !== 'remotes';
  el('set-mail').hidden = v !== 'mail';
  el('set-contacts').hidden = v !== 'contacts';
  el('set-auth').hidden = v !== 'auth';
  el('set-imessage').hidden = v !== 'imessage';
  el('set-back').hidden = v === 'main';
  el('set-title').textContent = t(SUB_TITLE[v]);
  el('settings').dataset.sub = v;
  if (from === 'remotes' && v !== 'remotes') remotes.close();
  if (from === 'contacts' && v !== 'contacts') ctmanage.close();
  if (from === 'auth' && v !== 'auth') authrescue.close();
  if (v === 'remotes') { remotes.open({add}); return; }
  const back = { ios: 'set-a2hs', remotes: 'set-remotes-open', mail: 'set-mail-nav', contacts: 'set-contacts-nav', auth: 'set-auth-nav', imessage: 'set-imsg-nav' }[from];
  (v === 'main' ? el(back || 'set-close') : el('set-back')).focus({ preventScroll: v === 'main' && !back });
  if (v === 'main') renderRemotesRow();
  if (v === 'mail') mail.load();   // P170
  if (v === 'auth') authrescue.load();   // P258
  if (v === 'contacts') ctmanage.open();   // P207
  if (v === 'imessage') imsg.load();       // P274
}
/** Opens Settings (any view but the camera); `page` = 'remotes' lands straight on 遥控器管理. */
export function open(page = 'main', add = false) {
  if (document.body.dataset.view === 'scan') return;
  say('');
  if (!noPrefSet) hideCmd();
  render();
  const box = el('settings');
  box.hidden = false; box.dataset.modalOpen = '1';
  for (const id of ['scanBtn', 'srchBtn', 'setBtn']) el('set-actions').append(el(id));
  sub = 'main'; el('set-main').hidden = false; el('set-ios').hidden = true; el('set-remotes').hidden = true; el('set-mail').hidden = true; el('set-auth').hidden = true; el('set-back').hidden = true;
  el('set-contacts').hidden = true;
  el('set-imessage').hidden = true;
  box.dataset.sub = 'main';
  el('set-title').textContent = t('set.title');
  el('setBtn').setAttribute('aria-expanded', 'true');
  el('srchBtn').setAttribute('aria-label', t('p264.search')); el('srchBtn').title = t('p264.search');
  el('set-main').scrollTop = 0;
  markSection(null);
  el('settings').querySelector('.setbox').focus();
  if (page === 'remotes' && H.paired()) showSub('remotes', add);
  else if (H.paired() && isReady()) remotes.refresh().then(renderRemotesRow);   // 已用 N / 10 from the computer itself
}
export function back() { if (sub !== 'main') showSub('main'); else close(); }
/** P184: 「转发到邮箱」 with no mailbox yet → Settings → 邮件通道. */
export function openMail() { open(); if (isOpen()) showSub('mail'); }
/** P258: an expired-login alert → Settings → AI 登录救援. */
export function openAuth() { open(); if (isOpen()) showSub('auth'); }
export function close() {
  const box = el('settings');
  if (box.hidden) return;
  if (sub === 'remotes') remotes.close();
  if (sub === 'contacts') ctmanage.close();
  sub = 'main';
  say('');
  closeSearch();
  for (const id of ['scanBtn', 'srchBtn', 'setBtn']) document.querySelector('header').append(el(id));
  box.hidden = true; delete box.dataset.modalOpen;
  el('setBtn').setAttribute('aria-expanded', 'false');
  el('srchBtn').setAttribute('aria-label', t('r.find.btn')); el('srchBtn').title = t('r.find.btnTitle');
  if (document.activeElement && box.contains(document.activeElement)) document.activeElement.blur();
}

/** Update / refresh the app: ask the browser for a fresh service worker, unregister every one of ours, drop every Cache
 *  Storage entry, then reload (the page itself is `no-store`, so the reload fetches the new files). The metadata-only
 *  version notice checks version.json; this action remains an explicit owner choice. */
export async function refreshApp({silent=false, beforeReload=null} = {}) {
  if (!silent) say(t('set.refreshGo'));
  try {
    if ('serviceWorker' in navigator) {
      for (const r of await navigator.serviceWorker.getRegistrations()) {
        try { await r.update(); } catch { /* offline: unregistering is still right */ }
        await r.unregister();
      }
    }
    if (globalThis.caches) for (const k of await caches.keys()) await caches.delete(k);
  } catch {
    if (!silent) say(t('set.refreshFail'));
  }
  if (beforeReload && !await beforeReload()) return false;
  H.reload();
  return true;
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

function pickLang(l) {
  if (window.AJLang && window.AJLang.get() !== l) window.AJLang.set(l);   // the page follows at once
  render();
  setPref('appearance.language', l);
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
  longPress(el('setBtn'), () => H.screenshot?.()); // @ix app.screenshot
  el('setBtn').addEventListener('contextmenu', e => {
    e.preventDefault();
    // Touch browsers may emit contextmenu during a hold: the gear's hold is exclusively capture.
    if (e.pointerType === 'touch' || (!e.pointerType && matchMedia('(pointer:coarse)').matches)) return;
    H.gearHelp?.();
  });
  el('set-back').addEventListener('click', () => showSub('main'));
  el('settings').addEventListener('click', (e) => { if (e.target === el('settings')) close(); });
  // keysBtn: relay.js opens the shortcut page ABOVE Settings (P187: its back / Esc returns here)
  for (const b of document.querySelectorAll('[data-set-lang]')) b.addEventListener('click', () => pickLang(b.dataset.setLang));
  for (const b of document.querySelectorAll('[data-set-theme]')) b.addEventListener('click', () => {
    applyThemeLocal(b.dataset.setTheme);
    render();
    setPref('appearance.theme', b.dataset.setTheme);
  });
  for (const id of ['set-speak', 'set-wake', 'set-risk', 'set-goal', 'set-qswitch', 'set-iso', 'set-docker', 'set-hb']) el(id).addEventListener('click', () => {
    const b = el(id), next = b.getAttribute('aria-checked') !== 'true';
    tgl(id, next);
    if (id === 'set-iso') isoScope(next, pget('agent.session_mode', 'independent') === 'shared' ? 'shared' : 'independent');
    setPref(b.dataset.pref, next).then((ok) => { if (!ok) explainPrefFail(); });
  });
  // P207 §3: two-way switches — the switch flips, a side label picks that side (a no-op when it is already chosen)
  const TW = {
    'set-lang': [() => (window.AJLang ? window.AJLang.get() : 'zh') === 'en', (right) => pickLang(right ? 'en' : 'zh')],
    // P203 + P207: 自动 (left) = updates.auto_install true + mode auto (install when idle); 先问我 (right) = both off
    'set-updates': [() => !autoUpdates(), async (right) => { if (await setPref('updates.auto_install', !right)) await setPref('updates.mode', right ? 'ask' : 'auto'); }],
    'set-mode': [() => pget('agent.session_mode', 'independent') === 'shared', (right) => setPref('agent.session_mode', right ? 'shared' : 'independent')],
  };
  for (const [id, [isRight, pick]] of Object.entries(TW)) {
    el(id).addEventListener('click', () => { const next = !isRight(); twoWay(id, next); pick(next); });
    for (const l of el(id).parentElement.querySelectorAll('.tw-lab[data-tw]')) l.addEventListener('click', () => {
      const right = l.dataset.side === 'right';
      if (right !== isRight()) { twoWay(id, right); pick(right); }
    });
  }
  el('set-hb-every').addEventListener('change', () => {
    const v = el('set-hb-every').value;
    el('set-hb-custom').hidden = v !== 'custom';
    if (v === 'custom') { el('set-hb-custom-n').focus({ preventScroll: true }); return; }
    hbInterval(v);
  });
  el('set-hb-custom-n').addEventListener('change', () => hbInterval(el('set-hb-custom-n').value));
  el('set-hb-daily').addEventListener('click', () => {
    const next = el('set-hb-daily').getAttribute('aria-checked') !== 'true';
    tgl('set-hb-daily', next);
    el('set-hb-time').hidden = !next;
    dailyOn = next;
    if (next) { el('set-hb-time').focus({ preventScroll: true }); if (HHMM.test(el('set-hb-time').value)) setPref('heartbeat.daily_at', el('set-hb-time').value); }
    else setPref('heartbeat.daily_at', '');
  });
  el('set-hb-time').addEventListener('change', () => { const v = el('set-hb-time').value; if (HHMM.test(v)) setPref('heartbeat.daily_at', v); });
  el('set-status-x').addEventListener('click', () => say(''));
  el('set-hostupg').addEventListener('click', () => { if (H.upgradeHost) H.upgradeHost(); });   // P203: impact → card → /update @ix app.upgrade_host
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
  el('set-refresh').addEventListener('click', () => refreshApp());   // @ix app.refresh_web
  el('set-a2hs').addEventListener('click', onA2hs);
  el('set-mail-nav').addEventListener('click', () => showSub('mail'));   // P170
  el('set-auth-nav').addEventListener('click', () => showSub('auth'));   // P258
  el('set-owner-nav').addEventListener('click', () => contacts.openOwner());   // P194: 我的联系方式 (slot 0)
  el('set-contacts-nav').addEventListener('click', () => showSub('contacts'));   // P207: 通讯录
  mail.configure();
  el('set-imsg-nav').addEventListener('click', () => showSub('imessage'));   // P274
  imsg.configure({ pget, setPref, hostInfo: () => hostInfo, assist: (text) => { close(); if (H.assist) H.assist(text); } });
  // P207 §3: copy = an icon button; ✓ for 1.6 s on success (plus the fading notice for screen readers)
  // P283: 「重新绑定 Telegram」 — the host answers /telegram-pair with a one-time code card in the chat (no Agent, no model)
  el('set-tgpair').addEventListener('click', async () => {
    if (!isReady()) { say(t('set.chanOffline')); return; }
    try { await sendApp({ t: 'slash', cmd: 'telegram-pair', arg: '' }); } catch { say(t('set.chanOffline')); return; }
    say(t('set.chanSent'));
    close();
  });
  for (const b of document.querySelectorAll('[data-copy]')) b.addEventListener('click', async () => {
    const ok = await RelayMD.copyText(el(b.dataset.copy).textContent);
    say(ok ? t('set.copied') : t('r.copy.fail'));
    if (!ok) return;
    b.dataset.done = '1'; clearTimeout(b._done);
    b._done = setTimeout(() => { delete b.dataset.done; }, 1600);
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

/** P265/P264 integration seam: the shortcut and gear hold invoke the same configured capture callback. */
export function screenshotFromKey() { if (H.screenshot) return H.screenshot(); say(t('ixw.capturePending')); }

/** P265: the same configured owner action as the upgrade button, without a synthetic click. */
export function upgradeFromKey() { return H.upgradeHost?.(); }

/** P265 / P241 pairing key: reuse the existing signed Add flow and show its QR, never toggle it away. */
export function pairFromKey() { if (sub === 'remotes' && isOpen()) return remotes.open({add: true}); return open('remotes', true); }
// P264: only public labels / descriptions, never input values or secret cards, form the local search index.
export function closeSearch() { el('set-find').hidden = true; el('set-find-input').value = ''; el('set-find-results').replaceChildren(); }
export function toggleSearch() {
  if (!isOpen()) return;
  if (!el('set-find').hidden) { closeSearch(); return; }
  if (sub !== 'main') showSub('main');
  el('set-find').hidden = false; el('set-find-input').focus();
}
export function searchItems(query) {
  const q = query.trim().toLocaleLowerCase(); if (!q) return [];
  return [...el('set-main').querySelectorAll('.setrow, .setnav, .setlinks')].filter(n => n.getClientRects().length &&
    [...n.querySelectorAll('.setlab, .setnote, b, small, a')].map(x => x.textContent).join(' ').concat(' ', n.dataset.keywords || '', n.closest('.setcard')?.querySelector('h3')?.textContent || '').toLocaleLowerCase().includes(q));
}
function renderSearch() {
  const box = el('set-find-results'); box.replaceChildren();
  const found = searchItems(el('set-find-input').value);
  for (const row of found) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'setnav';
    b.textContent = row.querySelector('.setlab, b, a')?.textContent || row.textContent;
    b.addEventListener('click', () => { closeSearch(); row.scrollIntoView({block: 'center'}); row.classList.remove('set-found'); void row.offsetWidth; row.classList.add('set-found'); row.tabIndex = -1; row.focus({preventScroll:true}); setTimeout(() => row.classList.remove('set-found'), 2400); });
    box.append(b);
  }
  if (!found.length && el('set-find-input').value.trim()) box.textContent = t('p264.noResults');
}
el('set-find-input').addEventListener('input', renderSearch);
