// P561 追加 16 (董事长 10-28): a Settings button shows its keyboard shortcut so the owner learns it — 「紧急停止所有 Agent」 in
// the button, 「升级Agent J 主机上的 Agent J」 in its note (with 「或在对话里直接发 /update」, the same api.slash('update') path).
// The key is never written here: it is read from the shortcut table (interactions.json → interactions.gen.js ROWS), drawn
// like the 「键盘快捷键」 page draws it (kbdTokens: ⌥ on a Mac, Alt elsewhere). A phone / tablet (coarse pointer or touch)
// shows no key, and an entry without a key shows nothing. P563 追加 2: 「紧急停止所有 Agent」 now has one (Alt+Shift+S, app.stop_all).
import { ROWS, kbdTokens } from './interactions.gen.js';
import { t, onLang, lang } from './t.js';

const isMac = () => /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent || '');   // = relay.js paintKeys
/** A keyboard and a fine pointer, as relay.js's isDesktop (the ⌘↩ hint on the approval card). */
export const hasKeyboard = () => !matchMedia('(pointer:coarse)').matches && !('ontouchstart' in window);
/** The table's function key for an interaction id (an Alt+Shift+… combo first), or ''. */
export function comboOf(id, rows = ROWS) {
  const row = rows.find((r) => r[0] === id);
  if (!row || !row[1].length) return '';
  const fn = row[1].find(([combo]) => combo.startsWith('Alt+Shift+')) || row[1][0];
  return fn[0];
}
/** The key as drawn on this device: tokens, or [] on a phone / for an id without a key. */
export function keyTokens(id, { rows = ROWS, keyboard = hasKeyboard(), mac = isMac() } = {}) {
  const combo = keyboard ? comboOf(id, rows) : '';
  return combo ? kbdTokens(combo, mac) : [];
}
function kbds(tokens) {
  const f = document.createDocumentFragment();
  tokens.forEach((tok, i) => { if (i) f.append('+'); const k = document.createElement('kbd'); k.textContent = tok; f.append(k); });
  return f;
}
export function paint() {
  for (const node of document.querySelectorAll('.setkbd[data-ix-key]')) {   // the key inside a button
    const tokens = keyTokens(node.dataset.ixKey);
    node.hidden = !tokens.length;
    node.textContent = tokens.join('+');
    const button = node.closest('button');
    if (button && tokens.length) button.setAttribute('aria-keyshortcuts', comboOf(node.dataset.ixKey));
    else if (button) button.removeAttribute('aria-keyshortcuts');
  }
  const how = document.getElementById('set-hostupg-how');   // the upgrade note: 「也可以按 …，或在对话里直接发 /update」
  if (how) {
    const tokens = keyTokens(how.dataset.ixKey);
    const [before, after = ''] = t(tokens.length ? 'p561.upgHowKey' : 'p561.upgHow', { key: '{key}' }).split('{key}');
    // one paragraph with the note before it: English needs a space between the sentences, Chinese none
    how.replaceChildren((lang() === 'en' ? ' ' : '') + before, ...(tokens.length ? [kbds(tokens), after] : []));
  }
}
let wired = false;
export function configure() {
  if (wired) return;
  wired = true;
  paint();
  onLang(paint);
  try { matchMedia('(pointer:coarse)').addEventListener('change', paint); } catch { /* old Safari: no change event on MediaQueryList */ }
}
