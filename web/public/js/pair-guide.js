// Host emits this display-only hint once, for the first approved remote (never on resume).
import { t, onLang } from './t.js';
import { el } from './ui.js';
let kind = null;
export function showPairGuide(value) {
  if (!['phone', 'computer'].includes(value)) return;
  kind = value;
  const node = el('pair-guide');
  node.dataset.i18n = 'pairGuide.' + kind;
  node.textContent = t(node.dataset.i18n);
  node.hidden = false;
}
onLang(() => { if (kind && !el('pair-guide').hidden) showPairGuide(kind); });
