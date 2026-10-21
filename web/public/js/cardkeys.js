// P412 #176 (Leo 10-20: 「每个卡片能不能用快捷键直接打开呢？记事本啊、待办事项啊、自动化工作流啊……Alt+Shift 对应着他们那个
// 抬头的那个字母」): every card on the logo menu gets Alt(⌥)+Shift + a letter of its English title — the first letter, or
// the next free one when another card or one of the app's own Alt+Shift keys (interactions.json) has it; when the
// title's letters are all taken, a digit (1–9, 0). The owner's cards choose first (they are the ones asked for), then the
// system cards; 对话 has none (Esc goes back to it). Assigned again whenever the cards change, so a new card gets one too.
import { KEYMAP } from './interactions.gen.js';

/** The letters the app's own Alt+Shift keys already use (read from the generated table, so a new one is never stolen). */
export function taken() {
  const out = new Set();
  for (const k of Object.keys(KEYMAP)) { const m = /^alt\+shift:Key([A-Z])$/.exec(k); if (m) out.add(m[1]); }
  return out;
}
const letters = (c) => [...new Set([...String(c.en || '').toUpperCase(), ...String(c.id || '').toUpperCase()].filter((ch) => ch >= 'A' && ch <= 'Z'))];

/** cards: [{key, en, id}] in priority order → Map key → 'N' | '1'. */
export function assign(cards, busy = taken()) {
  const used = new Set(busy), out = new Map();
  for (const c of cards) { const f = letters(c)[0]; if (f && !used.has(f)) { used.add(f); out.set(c.key, f); } }
  for (const c of cards) {
    if (out.has(c.key)) continue;
    const f = letters(c).find((ch) => !used.has(ch));
    if (f) { used.add(f); out.set(c.key, f); }
  }
  let d = 1;
  for (const c of cards) {
    if (out.has(c.key)) continue;
    if (d > 10) break;
    out.set(c.key, String(d % 10)); d++;
  }
  return out;
}
/** The key a keydown names (e.code, so ⌥⇧ on a Mac — which types a symbol — still counts), or ''. */
export function keyOf(e) {
  if (!e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey || e.isComposing || e.keyCode === 229) return '';
  const m = /^(?:Key([A-Z])|Digit([0-9]))$/.exec(e.code || '');
  return m ? m[1] || m[2] : '';
}
export const comboOf = (k) => 'Alt+Shift+' + k;
