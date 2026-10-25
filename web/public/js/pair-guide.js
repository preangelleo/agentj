// Display-only host state: null clears a hint after pairing; reconnect sends the current value.
import { t, onLang } from './t.js';
import { el } from './ui.js';
let kind = null;
export function showPairGuide(value) {
  if (value === null) {
    kind = null;
    el('pair-guide').hidden = true;
    return;
  }
  if (!['phone', 'computer'].includes(value)) return;
  kind = value;
  const node = el('pair-guide');
  node.dataset.i18n = 'pairGuide.' + kind;
  node.textContent = t(node.dataset.i18n);
  node.hidden = false;
}
onLang(() => { if (kind && !el('pair-guide').hidden) showPairGuide(kind); });
