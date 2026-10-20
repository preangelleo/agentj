// P408 (PROTOCOL §38, ADR-P408-terminal): the always-loaded part of the web terminal — the Settings → 安全 → 「网页终端」 row,
// the 「在终端里打开」 door next to P361's 「执行」, and the routing of the host's term_* messages. The terminal page itself
// (js/terminal.js + terminal.css) is loaded on the first open only, as a module script next to this file (never in the
// first-screen bundle). The phone can only end its own session and forget its own cached password — never switch the
// terminal on: the row only names the command to run at the computer.
import { signedWrite, whyText } from './controls.js';
import { myIdSync } from './api.js';
import { t, onLang } from './t.js';
import { el, toast } from './ui.js';

let info = null, mod = null, waiting = null, H = { setupFaceId() {}, openSettings() {}, openRescue() {}, changed() {} };
const MODES = ['prf', 'local', 'session'];
/** The host block `terminal` (absent on an older host → null; no row). */
function parse(x) {
  if (!x || typeof x !== 'object' || typeof x.on !== 'boolean' || typeof x.os !== 'boolean') return null;
  const minutes = Number.isInteger(x.minutes) && x.minutes >= 1 && x.minutes <= 60 ? x.minutes : 15;
  return { on: x.on, os: x.os, me: x.me === true, web_ok: x.web_ok !== false, sudo: MODES.includes(x.sudo) ? x.sudo : null,
    minutes, idle_s: Number.isInteger(x.idle_s) && x.idle_s > 0 ? x.idle_s : 300, open: x.open === true,
    until: Number.isInteger(x.until) ? x.until : null };
}
/** 'none' (no row) · 'off' · 'notme' · 'noweb' · 'ok'. */
export function state() {
  if (!info || !info.os) return 'none';
  if (!info.on) return 'off';
  if (!info.me) return 'notme';
  return info.web_ok ? 'ok' : 'noweb';
}
export const usable = () => state() === 'ok';
export const minutes = () => (info ? info.minutes : 15);
export const termInfo = () => info && { ...info };

export function configure(h) {
  H = { ...H, ...h };
  el('set-term-open')?.addEventListener('click', () => open(''));
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
  const cmd = s === 'off' ? 'agentj terminal enable' : s === 'notme' ? `agentj terminal allow ${myIdSync() || '<device>'}`
    : s === 'noweb' ? `agentj terminal allow ${myIdSync() || '<device>'} --allow-web` : '';
  el('set-term-state').textContent = s === 'off' ? t('p408.off') : s === 'notme' ? t('p408.notMe') : s === 'noweb' ? t('p408.notWeb')
    : t('p408.mode.' + (info.sudo || 'none'));
  el('set-term-cmdline').hidden = !cmd;
  el('set-term-cmd').textContent = cmd;
  el('set-term-acts').hidden = s !== 'ok';
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
  mod.configure({ setupFaceId: () => H.setupFaceId(), openRescue: () => H.openRescue(), closedLine, openSettings: backToRow });
  waiting?.ok(mod); waiting = null;
}
/** After the page closes: Settings, scrolled to the row (which now says why it closed). */
function backToRow() {
  H.openSettings();
  const row = el('set-term-row');
  if (row && !row.hidden) row.scrollIntoView({ block: 'center' });
}
/** Open the terminal page (the normal open flow when it is not open); `prefill` goes into the input line, never sent. */
export function open(prefill = '') {
  if (!usable()) return Promise.resolve(false);
  return load().then((m) => m.open(String(prefill || ''), termInfo()), () => { toast(t('p408.loadFail'), 4000, 'error'); return false; });
}
/** app.js: term_* and the `login` elev card (with its elev_done / elev_refused) — only a loaded page cares. */
export function msg(m) { if (mod) mod.onMsg(m); }
/** app.js onHostGone: the Noise session this terminal rode is gone (the host closes it: relay_lost). */
export function gone() { if (mod) mod.gone(); }
export const isOpen = () => !!(mod && mod.isOpen());
// tests
export const _debug = () => ({ info: info && { ...info }, loaded: !!mod, state: state() });
