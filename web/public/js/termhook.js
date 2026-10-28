// P408 (PROTOCOL §38, ADR-P408-terminal): the always-loaded part of the web terminal — the Settings → 安全 → 「网页终端」 row,
// the 「在终端里打开」 door next to P361's 「执行」, and the routing of the host's term_* messages. The terminal page itself
// (js/terminal.js + terminal.css) is loaded on the first open only, as a module script next to this file (never in the
// first-screen bundle). P417 #185 (Leo 10-21): the terminal is used from a COMPUTER browser only. There the row can switch it
// on for this browser (`term_open` mode "enable": Face ID + the Agent J host's login password, then it opens in this window);
// the copyable `agentj terminal enable` stays as the other way. On a phone the row only says why not — no buttons. This
// judgement is UX routing only; the host refuses a remote that paired as a phone by itself (terminal.py).
import { signedWrite, whyText } from './controls.js';
import { myIdSync } from './api.js';
import { t, onLang } from './t.js';
import { el, toast } from './ui.js';
import { scrollToPkRow } from './pkassist.js';   // P561 追加 14

let info = null, mod = null, waiting = null, H = { setupFaceId() {}, openSettings() {}, openRescue() {}, changed() {}, attach() {}, insert() {}, goChat() {} };
const MODES = ['prf', 'local', 'session'];
/** The host block `terminal` (absent on an older host → null; no row). */
function parse(x) {
  if (!x || typeof x !== 'object' || typeof x.on !== 'boolean' || typeof x.os !== 'boolean') return null;
  const minutes = Number.isInteger(x.minutes) && x.minutes >= 1 && x.minutes <= 60 ? x.minutes : 15;
  return { on: x.on, os: x.os, me: x.me === true, web_ok: x.web_ok !== false, sudo: MODES.includes(x.sudo) ? x.sudo : null,
    minutes, idle_s: Number.isInteger(x.idle_s) && x.idle_s > 0 ? x.idle_s : 300, open: x.open === true,
    until: Number.isInteger(x.until) ? x.until : null, pk: x.pk !== false,   // P417 #185: pk absent (older host) → no Face ID line
    kind: ['phone', 'desktop', 'unknown'].includes(x.kind) ? x.kind : null };   // P417 #185: null = an older host (no remote enable)
}
/** P417 #185: is this a computer browser? A combined judgement — a phone / tablet user agent, an iPad asking for the desktop
 *  site, a finger as the main pointer, a touch screen without a mouse / trackpad, or a phone-sized touch screen all say
 *  "phone". A narrow window alone does not (a computer browser can be narrow; a test browser may report no pointer). UX routing only: the host's own check is the device kind recorded at pairing. */
export function computerBrowser(env = globalThis) {
  const nav = env.navigator || {}, ua = String(nav.userAgent || '');
  const mm = (q) => { try { return !!env.matchMedia?.(q).matches; } catch { return false; } };
  if (/Android|iPhone|iPad|iPod|Mobile|HarmonyOS|OpenHarmony/i.test(ua)) return false;
  if (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1) return false;
  if (mm('(pointer: coarse)')) return false;                       // the main pointer is a finger
  const touch = (nav.maxTouchPoints || 0) > 0, sc = env.screen || {};
  if (touch && !mm('(pointer: fine)')) return false;               // a touch screen and no mouse / trackpad
  return !(touch && Math.min(sc.width || 9999, sc.height || 9999) < 600);   // a phone-sized touch screen
}
/** 'none' (older host: no row) · 'os' (macOS / Windows host: not yet) · 'phone' · 'off' · 'notme' · 'noweb' · 'ok'. P417 #185:
 *  the row stays in 安全 whenever the host knows the feature, so the owner finds it without reading the docs. */
export function state() {
  if (!info) return 'none';
  if (!info.os) return 'os';
  if (info.kind === 'phone' || !computerBrowser()) return 'phone';
  if (!info.on) return 'off';
  if (!info.me) return 'notme';
  return info.web_ok ? 'ok' : 'noweb';
}
export const usable = () => state() === 'ok';
export const minutes = () => (info ? info.minutes : 15);
export const termInfo = () => info && { ...info };
/** P417 #185: the row may switch the terminal on for this computer browser (the host knows it paired as a computer). */
export const canEnable = () => !!info && info.kind === 'desktop' && ['off', 'notme', 'noweb'].includes(state());

export function configure(h) {
  H = { ...H, ...h };
  el('set-term-open')?.addEventListener('click', () => open(''));
  el('set-term-enable')?.addEventListener('click', enable);   // P417 #185
  el('set-term-faceid')?.addEventListener('click', () => scrollToPkRow());   // P417 #185 · P561 追加 14: the one entry is Settings' top row
  el('set-term-forget')?.addEventListener('click', forget);
  onLang(paint);
}
/** settings.js render(): the host block decides the row. */
export function render(hostInfo) {
  const next = parse(hostInfo && hostInfo.terminal);
  const was = JSON.stringify(info);
  info = next;
  paint();
  if (was !== JSON.stringify(info)) H.changed();
}
let last = '';   // the last 「终端已关闭（…）」 line, shown under the row until the next open (memory only)
export function closedLine(text) { last = text || ''; paint(); }
export function paint() {
  const row = el('set-term-row');
  if (!row) return;
  const s = state();
  row.hidden = s === 'none';
  if (s === 'none') return;
  const en = canEnable();
  const cmd = s === 'off' ? 'agentj terminal enable' : s === 'notme' ? `agentj terminal allow ${myIdSync() || '<device>'}`
    : s === 'noweb' ? `agentj terminal allow ${myIdSync() || '<device>'} --allow-web` : '';
  el('set-term-state').textContent = s === 'os' ? t('p417.os') : s === 'phone' ? t('p417.phone')
    : en ? t(s === 'off' ? 'p417.off' : 'p417.notMe')
      : s === 'off' ? t('p408.off') : s === 'notme' ? t('p408.notMe') : s === 'noweb' ? t('p408.notWeb')
        : t('p408.mode.' + (info.sudo || 'none'));
  // RESUME 9 ④: a remote paired before 0.25.1a1 has no recorded kind — the command, plus how to get the button
  if (info.kind === 'unknown' && ['off', 'notme', 'noweb'].includes(s)) el('set-term-state').textContent += ' ' + t('p417.repair');
  el('set-term-cmdline').hidden = !cmd;
  el('set-term-cmd').textContent = en ? 'agentj terminal enable' : cmd;   // P417: enable from here, or this line at the host
  const nopk = s !== 'os' && s !== 'phone' && !info.pk;   // P417 #185: no passkey on this device yet → 「设置 Face ID」 first
  el('set-term-acts').hidden = s !== 'ok' || nopk;
  el('set-term-en').hidden = !en || nopk;
  el('set-term-or').hidden = !en;
  el('set-term-pk').hidden = !nopk;
  el('set-term-forget').hidden = !(info.sudo === 'prf' || info.sudo === 'local');
  el('set-term-note').textContent = t('p408.rowNote', { n: info.minutes });
  el('set-term-last').textContent = last;
  el('set-term-last').hidden = !last;
}
/** 「清除已记住的电脑密码」: signed term_forget (no Face ID) → ctl_res. */
async function forget() {
  const b = el('set-term-forget');
  b.disabled = true;
  const res = await signedWrite('term_forget', {}, { t: 'term_forget' });
  b.disabled = false;
  if (res.ok) { if (info) info.sudo = null; paint(); toast(t('p408.forgot'), 3200); }
  else toast(t('p408.forgetFail', { why: whyText(res.why) || res.why || '' }), 4000, 'error');
}

// ---------------------------------------------------------------- the lazily loaded page
function load() {
  if (mod) return Promise.resolve(mod);
  if (waiting) return waiting.p;
  let ok, no;
  waiting = { p: new Promise((a, b) => { ok = a; no = b; }) };
  waiting.ok = ok;
  const s = document.createElement('script');
  s.type = 'module'; s.src = new URL('./terminal.js', import.meta.url).href;
  s.onerror = () => { waiting = null; s.remove(); no(new Error('load')); };
  document.head.appendChild(s);
  return waiting.p;
}
/** terminal.js calls this once it has loaded. */
export function register(api) {
  mod = api;
  mod.configure({ setupFaceId: () => H.setupFaceId(), openRescue: () => H.openRescue(), closedLine, openSettings: backToRow,
    attach: (f) => H.attach(f), insert: (x) => H.insert(x), goChat: () => H.goChat() });   // P414
  waiting?.ok(mod); waiting = null;
}
/** After the page closes: Settings, scrolled to the row (which now says why it closed). */
function backToRow() {
  H.openSettings();
  const row = el('set-term-row');
  if (row && !row.hidden) row.scrollIntoView({ block: 'center' });
}
/** P417 #185: 「开启并打开网页终端」 — the open flow with mode "enable" (Face ID, then the host's login password card). */
export function enable() {
  if (!canEnable() || !info.pk) return Promise.resolve(false);
  return load().then((m) => m.open('', termInfo(), { enable: true }), () => { toast(t('p408.loadFail'), 4000, 'error'); return false; });
}
/** Open the terminal page (the normal open flow when it is not open); `prefill` goes into the input line, never sent. */
export function open(prefill = '') {
  if (!usable()) return Promise.resolve(false);
  return load().then((m) => m.open(String(prefill || ''), termInfo()), () => { toast(t('p408.loadFail'), 4000, 'error'); return false; });
}
/** P414 (§37.12): a 「!」 line from the message box → a promise of true (sent, or the open flow started — clear the field). */
export function bang(line, sid) {
  if (!usable()) return Promise.resolve(false);
  return load().then((m) => m.bang(line, sid, termInfo()), () => { toast(t('p408.loadFail'), 4000, 'error'); return false; });
}
/** app.js: term_* and the `login` elev card (with its elev_done / elev_refused) — only a loaded page cares. P414 (§37.13):
 *  `term_go` (the Agent opens the terminal, maybe with a line to fill in) loads the page first; never on a remote that may
 *  not use it. */
export function msg(m) {
  if (m && m.t === 'term_go') { if (usable()) load().then((x) => x.go(m, termInfo()), () => {}); return; }
  if (mod) mod.onMsg(m);
}
/** app.js onHostGone: the Noise session this terminal rode is gone (the host closes it: relay_lost). */
export function gone() { if (mod) mod.gone(); }
export const isOpen = () => !!(mod && mod.isOpen());
// tests
export const _debug = () => ({ info: info && { ...info }, loaded: !!mod, state: state() });
