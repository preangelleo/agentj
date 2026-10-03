// Web Push (PROTOCOL §9): the subscription goes to the host only (end to end), pushes carry no content; and the
// 「添加到主屏幕」 hint (phones only, not when installed, dismissed once — localStorage "aj.a2hs").
import { sendApp, isReady } from './session.js';
import { t, lang, fillText } from './t.js';
import { el } from './ui.js';

let pushKey = null;
export const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
export const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const isAndroid = () => /Android/.test(navigator.userAgent);
export const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
let swReg = null;
export async function registration() {
  if (swReg) return swReg;
  swReg = await navigator.serviceWorker.register('sw.js' + (lang() === 'en' ? '?lang=en' : ''), { scope: './' });
  return swReg;
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
let pushState = 'none';
export function pushUi(stateName = pushState) {
  pushState = stateName;
  const row = el('push-row'), btn = el('push-on'), txt = el('push-text');
  row.hidden = stateName === 'none';
  btn.hidden = stateName !== 'offer';
  txt.textContent = ['offer', 'on', 'ios', 'denied', 'failed'].includes(stateName) ? t('push.' + stateName) : '';
}
export async function gotPushKey(k) {
  let key;
  try { key = Uint8Array.from(atob(k.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((k.length + 3) % 4)), (c) => c.charCodeAt(0)); } catch { return; }
  if (key.length !== 65) return;
  pushKey = key;
  if (!pushSupported()) return pushUi(isIOS() && !standalone() ? 'ios' : 'none');
  if (Notification.permission === 'denied') return pushUi('denied');
  try {
    const reg = await registration();
    const sub = await reg.pushManager.getSubscription();
    if (sub && sameKey(sub, key) && Notification.permission === 'granted') {
      await sendApp(subMsg(sub));                    // re-sent on every connect; the host keeps one per device
      return pushUi('on');
    }
  } catch { /* fall through to the offer */ }
  pushUi('offer');
}
export async function enablePush() {
  if (!pushKey || !isReady()) return;
  try {
    if ((await Notification.requestPermission()) !== 'granted') return pushUi(Notification.permission === 'denied' ? 'denied' : 'offer');
    const reg = await registration();
    let sub = await reg.pushManager.getSubscription();
    if (sub && !sameKey(sub, pushKey)) { await sub.unsubscribe(); sub = null; }
    sub = sub ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: pushKey });
    await sendApp(subMsg(sub));
    pushUi('on');
  } catch {
    pushUi('failed');
    el('push-on').hidden = false;
  }
}

// ---------------------------------------------------------------- Add to Home Screen hint
// The one thing this page keeps in local storage besides the language / theme (brand/lang.js) and the two display
// preferences (reader size aj.readerFs, launch colour aj.chrome): that the hint was dismissed.
const A2HS_KEY = 'aj.a2hs';
let a2hsDismissed = false;
try { a2hsDismissed = localStorage.getItem(A2HS_KEY) === '1'; } catch { /* blocked storage: show it again next time */ }
export function renderA2hs(view) {
  const box = el('a2hs');
  if (!box) return;
  const kind = isIOS() ? 'ios' : isAndroid() ? 'android' : null;
  box.hidden = a2hsDismissed || standalone() || !kind || !['pair', 'chat'].includes(view);
  // pairing: the first thing on the page; chat: a card at the top of the reading area (the header stays free)
  const home = view === 'chat' ? el('stage') : el('pages');
  if (!box.hidden && box.parentElement !== home) home.prepend(box);
  box.classList.toggle('hint--stage', view === 'chat');
  if (!box.hidden) fillText(el('a2hs-text'), t('a2hs.' + kind));
  el('pair-iphone').hidden = !(isIOS() && !standalone());
  fillText(el('pair-step1'), t(isIOS() ? 'pair.step1ios' : 'pair.step1'));   // iPhone Safari cannot scan inside the page:
  fillText(el('pair-step2'), t(isIOS() ? 'pair.step2ios' : 'pair.step2'));   // pair with the link, from the Home Screen app
}
export function dismissA2hs(view) {
  a2hsDismissed = true;
  try { localStorage.setItem(A2HS_KEY, '1'); } catch { /* blocked storage */ }
  renderA2hs(view);
}
