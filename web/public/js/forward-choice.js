// P536 → P554: 转发去向 is one Agent J host setting (`ui.forward_destination`), the same on every remote. A change is the
// signed `forward_set` control (no Face ID); the host's `preferences` broadcast repaints every remote. The first time a host
// has none, this remote's own earlier local choice moves there (`migrate`, the first one wins) and the local copy goes.
// An older host (no `host.forward_set`) keeps the P536 remote-local choice. While the chosen channel is unbound the button
// uses system share; on a synced host that never rewrites the host value (one remote missing a push must not reset all).
import {hostId, peer, isReady} from './session.js';
import {t} from './t.js';
import {toast} from './ui.js';
import {signedWrite, whyText} from './controls.js';
const DESTS = ['share', 'wx', 'tg'];
let owner = '', selected = 'share', sync = false, hostVal = '', migrating = false, told = false, stored = '';
const key = () => 'aj.forward.destination.' + owner;
export const available = x => x === 'share' || (x === 'wx' ? peer.wxbound : x === 'tg' && peer.tgbound);
const local = () => { try { const v = localStorage.getItem(key()); return DESTS.includes(v) ? v : ''; } catch { return ''; } };
const changed = () => window.dispatchEvent(new Event('agentj-forward-change'));
function follow() {
  const id = hostId() || '';
  if (owner === id) return;
  owner = id; sync = false; hostVal = ''; migrating = false; told = false;
  stored = local();                                 // as saved before this load (a reset below must not be what migrates)
  selected = ['wx','tg'].includes(stored) ? stored : 'share';
}
export function destination() {
  follow();
  if (!sync) {
    if (isReady() && !available(selected)) { selected = 'share'; save(); toast(t('p536.reset'), 3500); }
    return selected;
  }
  const want = hostVal || stored || 'share';
  if (!isReady() || available(want)) { told = false; return want; }
  if (!told) { told = true; toast(t('p536.reset'), 3500); }
  return 'share';
}
function save() { try { localStorage.setItem(key(), selected); } catch {} }
const send = (dest, migrate) => signedWrite('forward_set', {dest, migrate}, {t: 'forward_set', dest, migrate});
export function select(target) {
  destination();
  if (!DESTS.includes(target) || !available(target)) return false;
  if (!sync) { selected = stored = target; save(); changed(); return true; }
  const before = hostVal;
  hostVal = target; changed();                      // shown at once; the broadcast confirms it on every remote
  send(target, false).then(res => {
    if (res.ok) return;
    if (hostVal === target) { hostVal = before; changed(); }
    toast(t('p554.syncFail', {why: whyText(res.why)}), 5000);
  });
  return true;
}
/** settings.js setPrefs: every `preferences` broadcast (value + the host block). */
export function fromHost(value, host) {
  follow();
  sync = !!(host && host.forward_set === true);
  if (!sync) return;
  const v = value?.ui?.forward_destination;
  hostVal = DESTS.includes(v) ? v : '';
  if (hostVal) { stored = ''; try { localStorage.removeItem(key()); } catch {} }
  else if (stored && !migrating && isReady()) {
    migrating = true;                               // once per host connection; the broadcast that follows ends it
    send(stored, true).then(res => {
      if (!res.ok) { migrating = false; return; }
      if (!hostVal && DESTS.includes(res.dest)) { hostVal = res.dest; stored = ''; try { localStorage.removeItem(key()); } catch {} changed(); }
    });
  }
  changed();
}
export function render() {
  const target = destination();
  const tg = document.querySelector('[data-i18n="p536.telegram"]');
  if (tg) tg.textContent = t('p536.telegram', {channel: 'Telegram'});
  const note = document.getElementById('set-fwd-note'), nk = sync ? 'p554.noteHost' : 'p536.destinationNote';
  if (note) { note.dataset.i18n = nk; note.textContent = t(nk); }
  for (const input of document.querySelectorAll('input[name="forward-destination"]')) {
    input.disabled = !available(input.value); input.checked = target === input.value;
  }
  // P554: say why WeChat cannot be chosen (not bound → 去绑定) or why it cannot send right now (reply window).
  const why = document.getElementById('set-fwd-wx-why');
  if (why) {
    const key = !peer.wxbound ? 'p554.unbound' : target === 'wx' && !peer.wxfwd ? 'p554.window' : '';
    why.hidden = !key;
    document.getElementById('set-fwd-wx-note').textContent = key ? t(key) : '';
    const bind = document.getElementById('set-fwd-wx-bind');
    bind.hidden = key !== 'p554.unbound'; bind.textContent = t('p554.bind');
  }
}
export function configure() {
  for (const input of document.querySelectorAll('input[name="forward-destination"]')) input.addEventListener('change', () => { select(input.value); render(); });
  window.addEventListener('agentj-forward-change', render);
  render();
}
