// Web Push (PROTOCOL §9): the subscription goes to the host only (end to end), pushes carry no content; and the
// 「添加到主屏幕」 hint (phones only, not when installed, dismissed once — localStorage "aj.a2hs").
import { sendApp, isReady } from './session.js';
import { t, lang, fillText } from './t.js';
import { el } from './ui.js';

let pushKey = null;
// Explicit off is local to this browser; permission alone is not consent to turn alerts back on.
const OFF_KEY = 'aj.pushExplicitOff';
let localOff = false;
const explicitlyOff = () => { try { return localOff || localStorage.getItem(OFF_KEY) === '1'; } catch { return true; } };
// A write failure may be temporary while reads still work. Mirror the revocation in IndexedDB and
// check both stores before any automatic subscribe. A failure of both stores is never reported as success.
function offDb(value) {
  const name = globalThis.AJSeat?.db?.('aj-push-intent');
  if (!name) return Promise.reject(new Error('seat scope unavailable'));
  return new Promise((resolve, reject) => {
    let db, settled = false;
    const timer = setTimeout(() => finish(false, new Error('push intent store timeout')), 2000);
    const finish = (ok, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { db?.close(); } catch { /* closed */ }
      ok ? resolve(result) : reject(result);
    };
    try {
      const req = indexedDB.open(name, 1);
      req.onupgradeneeded = () => { if (!req.result.objectStoreNames.contains('flags')) req.result.createObjectStore('flags'); };
      req.onerror = () => finish(false, req.error);
      req.onblocked = () => finish(false, new Error('push intent store blocked'));
      req.onsuccess = () => {
        db = req.result;
        try {
          const tx = db.transaction('flags', value === undefined ? 'readonly' : 'readwrite');
          const r = value === undefined ? tx.objectStore('flags').get('off') : tx.objectStore('flags').put(value, 'off');
          r.onsuccess = () => { if (value === undefined) finish(true, r.result === true); };
          r.onerror = () => finish(false, r.error);
          tx.oncomplete = () => { if (value !== undefined) finish(true, true); };
          tx.onerror = () => finish(false, tx.error);
          tx.onabort = () => finish(false, tx.error);
        } catch (e) { finish(false, e); }
      };
    } catch (e) { finish(false, e); }
  });
}
async function storedOff() {
  let ls;
  try { ls = localStorage.getItem(OFF_KEY) === '1'; } catch { return true; }
  if (ls) return true;
  try { return localOff || await offDb(); } catch { return true; }
}
async function markOff(off) {
  localOff = off;
  let ls = false, db = false;
  try { localStorage.setItem(OFF_KEY, off ? '1' : '0'); ls = true; } catch { /* IndexedDB fallback */ }
  try { db = await offDb(off); } catch { /* localStorage fallback */ }
  if (off || !ls || !db) localOff = true;
  else localOff = false;
  // A conflicting old marker is unsafe: never declare enable successful until both stores agree.
  return off ? ls || db : ls && db;
}
let hostEnabled = null; // The host's per-device record, not a guess from browser permission.
let lastKey = null;
let intent = 0;
let offSend = null, pendingOff = null, hostOnSerial = 0;
// A socket write is not a Host acknowledgement. Bind the reply to this request so stale on:false
// from a prior connection cannot be mistaken for successful revocation.
function retryOff() {
  if (!explicitlyOff() || !isReady() || offSend) return offSend;
  const r = crypto.randomUUID(), at = hostOnSerial;
  offSend = new Promise(resolve => {
    const timer = setTimeout(() => finish(false), 3000);
    const finish = ok => {
      if (pendingOff?.r !== r) return;
      pendingOff = null;
      clearTimeout(timer);
      resolve(ok);
    };
    pendingOff = { r, finish };
    sendApp({ t: 'push_off', r }).catch(() => finish(false));
  }).finally(() => {
    offSend = null;
    // A newer host-on can arrive between this ack and cleanup. Do not lose that revocation.
    if (explicitlyOff() && hostEnabled === true && hostOnSerial !== at && isReady()) queueMicrotask(() => retryOff());
  });
  return offSend;
}
// Ignore delayed acknowledgements after a local off; an older host-on reply cannot grant consent.
export function gotPushStatus(enabled, r) {
  if (typeof enabled !== 'boolean') return;
  if (enabled && explicitlyOff()) ++hostOnSerial;
  if (!enabled && pendingOff && r === pendingOff.r && explicitlyOff()) pendingOff.finish(true);
  if (explicitlyOff()) {
    hostEnabled = enabled;
    pushUi('offer');
    if (enabled) retryOff();
    return;
  }
  // The IndexedDB marker may outlive a failed localStorage write across reloads.
  if (enabled) storedOff().then(off => { if (off) { localOff = true; ++intent; pushUi('offer'); retryOff(); } });
  if (hostEnabled === enabled) return; // push_sub acknowledgement is not a new intent
  hostEnabled = enabled;
  ++intent;
  if (!enabled) pushUi('offer');
  else if (lastKey && pushState !== 'on') gotPushKey(lastKey);
}
export const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
export const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const isAndroid = () => /Android/.test(navigator.userAgent);
export const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
let swReg = null;
export async function registration() {
  if (swReg) return swReg;
  // A failed register must not poison later retries with a rejected cached promise.
  const reg = await navigator.serviceWorker.register('sw.js' + (lang() === 'en' ? '?lang=en' : ''), { scope: './' });
  swReg = reg;
  return reg;
}
export function reregister() {                       // the worker's two notification sentences follow the language
  if (!pushSupported()) return;
  swReg = null;
  registration().catch(() => {});
}
const sameKey = (sub, key) => {
  const k = sub?.options?.applicationServerKey;
  if (!k) return false;
  const a = new Uint8Array(k);
  return a.length === key.length && a.every((x, i) => x === key[i]);
};
function subMsg(sub) {
  const j = sub.toJSON();
  return { t: 'push_sub', endpoint: j.endpoint, p256dh: j.keys?.p256dh, auth: j.keys?.auth };
}
// P204 (§9): which kinds this phone wants — 审批 ask · 完成 reply · 告警 security. Kept here (localStorage "aj.pushKinds")
// and told to the computer after every subscribe; the computer then sends only those (the push itself stays empty).
export const KINDS = ['ask', 'reply', 'security'];
const PUSH_KINDS_KEY = 'aj.pushKinds';
export function kinds() {
  try {
    const k = JSON.parse(localStorage.getItem(PUSH_KINDS_KEY) || 'null');
    if (Array.isArray(k) && k.every((x) => KINDS.includes(x))) return KINDS.filter((x) => k.includes(x));
  } catch { /* blocked storage / garbage: all of them */ }
  return KINDS.slice();
}
const sendKinds = () => sendApp({ t: 'push_pref', kinds: kinds() }).catch(() => {});
export async function setKind(kind, on) {
  if (!KINDS.includes(kind)) return kinds();
  const k = new Set(kinds());
  if (on) k.add(kind); else k.delete(kind);
  const list = KINDS.filter((x) => k.has(x));
  try { localStorage.setItem(PUSH_KINDS_KEY, JSON.stringify(list)); } catch { /* blocked storage: this session only */ }
  if (isReady() && pushState === 'on') await sendKinds();
  return list;
}
let pushState = 'none';
const watchers = [];
/** Settings (F13) shows the same state: 'none' | 'offer' | 'on' | 'ios' | 'denied' | 'failed'. */
export const state = () => pushState;
export function onChange(fn) { watchers.push(fn); }
export function pushUi(stateName = pushState) {
  pushState = stateName;
  for (const f of watchers) { try { f(stateName); } catch { /* a view's problem, not the push state's */ } }
}
export async function gotPushKey(k) {
  let key;
  try { key = Uint8Array.from(atob(k.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((k.length + 3) % 4)), (c) => c.charCodeAt(0)); } catch { return; }
  if (key.length !== 65) return;
  pushKey = key;
  lastKey = k;
  if (explicitlyOff()) { retryOff(); return pushUi('offer'); }
  const generation = ++intent;
  const current = () => generation === intent && !explicitlyOff() && hostEnabled !== false && isReady();
  if (!pushSupported()) return pushUi(isIOS() && !standalone() ? 'ios' : 'none');
  if (Notification.permission === 'denied') return pushUi('denied');
  let created = null;
  try {
    if (!current() || await storedOff()) { if (current()) { pushUi('offer'); retryOff(); } return; }
    const reg = await registration();
    if (!current() || await storedOff()) { retryOff(); return; }
    let sub = await reg.pushManager.getSubscription();
    if (!current() || await storedOff()) { retryOff(); return; }
    if (Notification.permission === 'granted') {
      if (sub && !sameKey(sub, key)) {
        await sub.unsubscribe(); sub = null;
        if (!current()) return;
      }
      // Permission alone is not opt-in. Only a host-enabled device can restore a missing subscription.
      if (!sub && hostEnabled === true && current() && !await storedOff()) {
        created = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
        sub = created;
      }
      if (!current() || await storedOff()) { if (created) await created.unsubscribe(); retryOff(); return; }
      if (sub) {
        await sendApp(subMsg(sub));
        if (!current()) { if (created) await created.unsubscribe(); return; }
        await sendKinds();
        if (current()) pushUi('on');
        return;
      }
    }
  } catch { if (!current()) { if (created) await created.unsubscribe().catch(() => {}); return; } return pushUi('failed'); }
  if (current()) pushUi('offer');
}
export async function enablePush() {
  if (!pushKey || !isReady()) return;
  const generation = ++intent;
  const current = () => generation === intent;
  let created = null;
  try {
    if ((await Notification.requestPermission()) !== 'granted') {
      if (current()) pushUi(Notification.permission === 'denied' ? 'denied' : 'offer');
      return;
    }
    if (!current()) return;
    const reg = await registration();
    if (!current()) return;
    let sub = await reg.pushManager.getSubscription();
    if (!current()) return;
    if (sub && !sameKey(sub, pushKey)) { await sub.unsubscribe(); sub = null; }
    if (!current()) return;
    if (!sub) { created = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: pushKey }); sub = created; }
    if (!current()) { if (created) await created.unsubscribe(); return; }
    // Persist the new owner's intent BEFORE the host can acknowledge push_sub.
    if (!await markOff(false) || !current()) { if (created) await created.unsubscribe().catch(() => {}); return pushUi('failed'); }
    await sendApp(subMsg(sub));
    if (!current()) { if (created) await created.unsubscribe(); retryOff(); return; }
    await sendKinds();
    if (!current()) { if (created) await created.unsubscribe(); retryOff(); return; }
    hostEnabled = true;
    pushUi('on');
  } catch {
    if (!current() && created) await created.unsubscribe().catch(() => {});
    if (!current()) retryOff();
    if (current()) pushUi('failed');
  }
}

/** Settings → 锁屏提醒 → 关闭: drop this browser's subscription and tell the host (§9 `push_off`). → true when done. */
export async function disablePush() {
  ++intent;
  localOff = true; // revoke in memory synchronously, before any await
  hostEnabled = false;
  pushUi(pushKey ? 'offer' : 'none');
  const durable = await markOff(true);
  let browserOff = true;
  try {
    const reg = await registration();
    const sub = await reg.pushManager.getSubscription();
    if (sub) await sub.unsubscribe();
  } catch { browserOff = false; }
  // Unsubscribe failure must not prevent the host from dropping its device record.
  const sent = await retryOff();
  if (!durable || !browserOff || sent !== true || !isReady()) { pushUi('failed'); return false; }
  return true;
}

// ---------------------------------------------------------------- Add to Home Screen hint
// The one thing this page keeps in local storage besides the language / theme (brand/lang.js) and the two display
// preferences (reader size aj.readerFs, launch colour aj.chrome): that the hint was dismissed.
const A2HS_KEY = 'aj.a2hs';
let a2hsDismissed = false, installedHome = false, hintView = 'pair';
try { installedHome = localStorage.getItem('aj.homeInstalled') === '1'; if (standalone()) localStorage.setItem('aj.homeInstalled', '1'); } catch {}
// Safari may isolate app storage; related-app detection is optional, never a pairing gate.
globalThis.navigator?.getInstalledRelatedApps?.().then(apps => { if (apps.length) { installedHome = true; renderA2hs(hintView); } }).catch(() => {});
try { a2hsDismissed = localStorage.getItem(A2HS_KEY) === '1'; } catch { /* blocked storage: show it again next time */ }
export function renderA2hs(view) {
  hintView = view;
  const box = el('a2hs');
  if (!box) return;
  const kind = isIOS() ? 'ios' : isAndroid() ? 'android' : null;
  box.hidden = (a2hsDismissed && !(isIOS() && installedHome)) || standalone() || !kind || !['pair', 'chat'].includes(view);
  // pairing: the first thing on the page; chat: a card at the top of the reading area (the header stays free)
  const home = view === 'chat' ? el('stage') : el('pages');
  if (!box.hidden && box.parentElement !== home) home.prepend(box);
  box.classList.toggle('hint--stage', view === 'chat');
  if (!box.hidden) fillText(el('a2hs-text'), t(kind === 'ios' && (installedHome || a2hsDismissed) ? 'a2hs.openInstalled' : 'a2hs.' + kind));
  el('pair-iphone').hidden = !(isIOS() && !standalone());
  fillText(el('pair-step1'), t(isIOS() ? 'pair.m2.s1ios' : 'pair.m2.s1'));   // iPhone Safari cannot scan inside the page:
  fillText(el('pair-step2'), t(isIOS() ? 'pair.m2.s2ios' : 'pair.m2.s2'));   // pair with the link, from the Home Screen app
  el('pm-cmd').textContent = isIOS() ? 'agentj pair --link' : 'agentj pair';    // P210: the command block says the same
}
export function dismissA2hs(view) {
  a2hsDismissed = true;
  try { localStorage.setItem(A2HS_KEY, '1'); } catch { /* blocked storage */ }
  renderA2hs(view);
}
