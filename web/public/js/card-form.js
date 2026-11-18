// P592 (chairman INCOMING-0321): every card's add / edit form saves on Ctrl+Enter (Windows / Linux) or ⌘+Enter (Mac) —
// the same chord as sending a message and approving a card (relay.js apprHotkey). One helper, not one copy per card.
// Plain Enter keeps its native meaning (single-line submit, newline in a textarea); an IME composition never saves.
// On a computer the hint 「⌘ + Enter 保存」 sits beside 保存; phones (coarse pointer / touch) show none.
import { t } from './t.js';

const MAC = /Mac|iPhone|iPad|iPod/.test((typeof navigator !== 'undefined' && ((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform)) || '');
export const fineKeyboard = () => typeof matchMedia === 'function' && !matchMedia('(pointer:coarse)').matches && !('ontouchstart' in window);
/** The chord as said beside 保存 ('' on a phone). */
export const saveKeyText = () => (fineKeyboard() ? t('p0321.saveKey', { key: MAC ? '⌘ + Enter' : 'Ctrl + Enter' }) : '');
/** True for the save chord only. */
export function isSaveChord(e) {
  return e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && !e.isComposing && e.keyCode !== 229 && !e.repeat;
}
/** Bind the chord on `root` (the form or dialog) to `submit`; mark `button` with it. Returns the hint node (null on a phone),
 *  owned by the card's own component (`owner`, the data-aj-owner of its surface). */
export function attachCardSaveShortcut(root, submit, button, owner) {
  if (!root || typeof submit !== 'function') return null;
  if (button) button.setAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');
  root.addEventListener('keydown', (e) => {
    if (!isSaveChord(e)) return;
    e.preventDefault(); e.stopPropagation();
    if (button && button.disabled) return;
    submit();
  });
  return paintSaveKey(null, owner);
}
/** Create (node = null) or refresh the hint line; hidden when there is no keyboard to press it on. */
export function paintSaveKey(node, owner) {
  const text = saveKeyText();
  if (!node) {
    if (!text) return null;
    node = document.createElement('p');
    node.className = 'aj-save-key';
    if (owner) node.setAttribute('data-aj-owner', owner);
  }
  node.textContent = text;
  node.hidden = !text;
  return node;
}
