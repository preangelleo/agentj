// F13 · Settings (the top-right gear, `s`, ⌘, / Ctrl+,): everything the owner changes often, in one panel.
// Host-side values go through `pref_set` → `pref_res` → the normal `preferences` broadcast (PROTOCOL §10, contract C6 /
// F14): optional risk warnings and session mode are phone-editable too. Reply size is phone-local.
// PR1: nothing here leaves the page except inside the Noise session (pref_set / push_off), and no customer text is shown.
import VERSION from '../version.js';
import { RelayMD } from './md.js';
import { t, fillText, onLang } from './t.js';
import { el } from './ui.js';
import { ask1, isReady } from './session.js';
import * as push from './push.js';

let H = { agentName: () => 'Agent J', paired: () => false, connected: () => false, unpair: () => {}, reload: () => location.reload() };
let prefs = null;                                    // the last `preferences` value from the host (null = none yet / older host)
let hostInfo = null;                                 // its `host` block (host ≥ 0.15: version, settable keys, paired phones)
let noPrefSet = false;                               // the host never answered a pref_set → show commands
let sub = 'main';                                    // 'main' | 'ios'

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
function renderFont() {
  el('set-font-val').textContent = t('set.fontVal', { n: Math.round(FONT_STEPS[fontAt] * 100) });
  el('set-font-minus').disabled = fontAt === 0;
  el('set-font-plus').disabled = fontAt === FONT_STEPS.length - 1;
  el('set-font-reset').disabled = FONT_STEPS[fontAt] === 1;
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
PREF_KEYS.push('updates.mode');
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
/** The page's own theme switch (brand/ui.js: auto → light → dark, saved as "aj.theme") stays the one place that persists
 *  it; this steps it to the wanted value so the ≡ menu label and the stored choice follow. */
function applyThemeLocal(want) {
  const sw = document.querySelector('[data-aj-theme-switch]');
  for (let i = 0; sw && i < 3 && curTheme() !== want; i++) sw.click();
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
  renderPush();
  renderA2hs();
  el('set-webver').textContent = t('set.webVer', { v: VERSION.version });
  const hv = hostInfo && typeof hostInfo.version === 'string' && /^[0-9A-Za-z.+-]{1,32}$/.test(hostInfo.version) ? hostInfo.version : null;
  el('set-hostver').textContent = hv ? t('set.hostVer', { v: hv }) : t('set.hostVerNone');
  renderPhones();
  const name = H.agentName();
  el('set-computer').textContent = !H.paired() ? t('set.computerNone') : t(H.connected() ? 'set.computerOn' : 'set.computerOff', { name });
  el('set-unpair').hidden = !H.paired();
  const risk = pget('agent.high_risk_warnings', false) !== false;
  tgl('set-risk', risk);
  el('set-risk-cmd').textContent = cmdFor('agent.high_risk_warnings', risk ? 'false' : 'true');
  const mode = pget('agent.session_mode', 'shared') === 'independent' ? 'independent' : 'shared';
  el('set-updates').textContent = t(pget('updates.mode', 'auto') === 'ask' ? 'set.updatesAsk' : 'set.updatesAuto');
  el('set-mode').textContent = t(mode === 'shared' ? 'set.modeShared' : 'set.modeIndependent');
  el('set-mode-cmd').textContent = cmdFor('agent.session_mode', mode === 'shared' ? 'independent' : 'shared');
  const iso = pget('agent.isolation', true) !== false, dock = pget('agent.allow_docker', false) === true;
  tgl('set-iso', iso);
  el('set-iso-cmd').textContent = cmdFor('agent.isolation', iso ? 'false' : 'true');
  tgl('set-docker', dock);
  el('set-docker-cmd').textContent = cmdFor('agent.allow_docker', dock ? 'false' : 'true');
}
/** The phones this computer has paired (host metadata: label, online now) — read-only; this one is marked. Labels are
 *  host data: textContent only, ≤ 64 characters. */
function renderPhones() {
  const list = el('set-phones'), devs = hostInfo && Array.isArray(hostInfo.devices) ? hostInfo.devices.slice(0, 20) : [];
  list.hidden = !devs.length;
  el('set-phones-h').hidden = !devs.length;
  el('set-phones-h').textContent = t('set.phones', { n: devs.length });
  const me = H.myId ? H.myId() : null;
  list.replaceChildren(...devs.map((d) => {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = String(d && d.name || '—').slice(0, 64);
    const tag = document.createElement('small');
    tag.textContent = [d && d.id && d.id === me ? t('set.phoneThis') : '', d && d.online ? t('set.phoneOn') : ''].filter(Boolean).join(' · ');
    li.append(name, tag);
    return li;
  }));
}
/** app.js: every `preferences` message from the host (value + the host ≥ 0.15 `host` block). */
export function setPrefs(value, host) {
  prefs = value && typeof value === 'object' ? value : null;
  hostInfo = host && typeof host === 'object' ? host : null;
  if (isOpen()) render();
}

// ---------------------------------------------------------------- open / close / sub-view
export const isOpen = () => !el('settings').hidden;
function showSub(v) {
  sub = v;
  el('set-main').hidden = v !== 'main';
  el('set-ios').hidden = v !== 'ios';
  el('set-back').hidden = v === 'main';
  el('set-title').textContent = t(v === 'ios' ? 'set.iosTitle' : 'set.title');
  (v === 'main' ? el('set-a2hs') : el('set-back')).focus();
}
export function open() {
  if (document.body.dataset.view !== 'chat') return;
  const m = el('aj-menu'); if (m) m.open = false;
  say('');
  if (!noPrefSet) hideCmd();
  render();
  const box = el('settings');
  box.hidden = false; box.dataset.modalOpen = '1';
  sub = 'main'; el('set-main').hidden = false; el('set-ios').hidden = true; el('set-back').hidden = true;
  el('set-title').textContent = t('set.title');
  el('setBtn').setAttribute('aria-expanded', 'true');
  el('settings').querySelector('.setbox').focus();
}
export function close() {
  const box = el('settings');
  if (box.hidden) return;
  box.hidden = true; delete box.dataset.modalOpen;
  el('setBtn').setAttribute('aria-expanded', 'false');
  if (document.activeElement && box.contains(document.activeElement)) document.activeElement.blur();
}

/** Update / refresh the app: ask the browser for a fresh service worker, unregister every one of ours, drop every Cache
 *  Storage entry, then reload (the page itself is `no-store`, so the reload fetches the new files). The page cannot
 *  fetch version.json (CSP connect-src = the relays only); the version line after the reload says what came in. */
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

/** hooks: { agentName(), paired(), connected(), myId(), unpair(), reload() } — app.js wires them. */
export function configure(hooks) {
  H = { ...H, ...hooks };
  el('setBtn').addEventListener('click', () => (isOpen() ? close() : open()));
  el('set-close').addEventListener('click', close);
  el('set-back').addEventListener('click', () => showSub('main'));
  el('settings').addEventListener('click', (e) => { if (e.target === el('settings')) close(); });
  el('keysBtn').addEventListener('click', close);  // relay.js opens the shortcut sheet on the same click (listener added later)
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
  for (const id of ['set-speak', 'set-wake', 'set-risk', 'set-iso', 'set-docker']) el(id).addEventListener('click', () => {
    const b = el(id), next = b.getAttribute('aria-checked') !== 'true';
    tgl(id, next);
    setPref(b.dataset.pref, next);
  });
  el('set-updates').addEventListener('click', () => setPref('updates.mode', pget('updates.mode', 'auto') === 'auto' ? 'ask' : 'auto'));
  el('set-mode').addEventListener('click', () => setPref('agent.session_mode', pget('agent.session_mode', 'shared') === 'shared' ? 'independent' : 'shared'));
  el('set-font-minus').addEventListener('click', () => setFontStep(fontAt - 1));
  el('set-font-plus').addEventListener('click', () => setFontStep(fontAt + 1));
  el('set-font-reset').addEventListener('click', () => setFontStep(FONT_STEPS.indexOf(1)));
  el('set-push').addEventListener('click', async () => {
    if (el('set-push').dataset.act === 'off') { if (await push.disablePush()) say(t('set.pushOffDone')); }
    else await push.enablePush();
    renderPush();
  });
  push.onChange(() => { if (isOpen()) renderPush(); });
  el('set-refresh').addEventListener('click', refreshApp);
  el('set-a2hs').addEventListener('click', onA2hs);
  el('set-unpair').addEventListener('click', () => { close(); H.unpair(); });
  for (const b of document.querySelectorAll('[data-copy]')) b.addEventListener('click', async () => {
    const ok = await RelayMD.copyText(el(b.dataset.copy).textContent);
    say(ok ? t('set.copied') : t('r.copy.fail'));
  });
  // Esc: the iOS guide → back; else close. Registered before relay's own keys (wire() runs before relay.init), so the
  // Escape that closes the panel never reaches relay's Esc-Esc (clear / interrupt).
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !isOpen() || !el('confirm').hidden) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (sub !== 'main') showSub('main'); else { close(); el('setBtn').focus(); }
  });
  onLang(() => { if (isOpen()) { render(); if (sub === 'ios') el('set-title').textContent = t('set.iosTitle'); } });
}

/** relay.js: the keyboard opens it (`s` outside a field, ⌘, / Ctrl+, anywhere on the chat screen). */
export function toggleFromKey() { if (isOpen()) close(); else open(); }
// for screens tests: what the panel would send
export const _debug = () => ({ prefs, hostInfo, noPrefSet, sub, font: FONT_STEPS[fontAt] });
