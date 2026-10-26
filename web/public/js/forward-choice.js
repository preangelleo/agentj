// Explicit preference, scoped to this remote and host. Binding loss alone resets it.
import {hostId, peer, isReady} from './session.js';
import {t} from './t.js';
import {toast} from './ui.js';
let owner = '', selected = 'share';
const key = () => 'aj.forward.destination.' + owner;
export const available = x => x === 'share' || (x === 'wx' ? peer.wxbound : x === 'tg' && peer.tgbound);
export function destination() {
  const id = hostId() || '';
  if (owner !== id) {
    owner = id; selected = 'share';
    try { const saved = localStorage.getItem(key()); if (['wx','tg'].includes(saved)) selected = saved; } catch {}
  }
  if (isReady() && !available(selected)) {
    selected = 'share'; save(); toast(t('p536.reset'), 3500);
  }
  return selected;
}
function save() { try { localStorage.setItem(key(), selected); } catch {} }
export function select(target) {
  destination();
  if (!['share','wx','tg'].includes(target) || !available(target)) return false;
  selected = target; save(); window.dispatchEvent(new Event('agentj-forward-change')); return true;
}
export function render() {
  const target = destination();
  const tg = document.querySelector('[data-i18n="p536.telegram"]');
  if (tg) tg.textContent = t('p536.telegram', {channel: 'Telegram'});
  for (const input of document.querySelectorAll('input[name="forward-destination"]')) {
    input.disabled = !available(input.value); input.checked = target === input.value;
  }
}
export function configure() {
  for (const input of document.querySelectorAll('input[name="forward-destination"]')) input.addEventListener('change', () => { select(input.value); render(); });
  window.addEventListener('agentj-forward-change', render);
  render();
}
