// P412 (#174, Leo 10-20: 「研究一下 Claude Code CLI……快捷键……能继承先继承」): the terminal editing keys of the message box,
// shared with the full-screen editor (js/fsedit.js). Ctrl only (never ⌘: on a Mac ⌘ keeps the system's own keys), and only
// inside the text box that has the key. Already there since 0.15: A / E / U / K / W / Y. New: D = delete the character after
// the caret, J = new line (the keys a browser lets a page take; the others stay the browser's).
let killBuf = '';

export function lineBounds(v, at) {
  const s0 = v.lastIndexOf('\n', at - 1) + 1;
  let e0 = v.indexOf('\n', at); if (e0 < 0) e0 = v.length;
  return [s0, e0];
}
// One edit through the browser's own undo stack when it can (execCommand), else by hand + an input event.
export function edit(ta, a, b, ins) {
  const v = ta.value, want = v.slice(0, a) + ins + v.slice(b);
  ta.setSelectionRange(a, b);
  try { document.execCommand(ins ? 'insertText' : 'delete', false, ins); } catch { /* old engines */ }
  if (ta.value !== want) {
    ta.value = want;
    ta.dispatchEvent(new Event('input'));
  }
  ta.setSelectionRange(a + ins.length, a + ins.length);
}
function kill(ta, a, b) {
  if (b <= a) return;
  killBuf = ta.value.slice(a, b);
  edit(ta, a, b, '');
}
const room = (ta, max) => Math.max(0, (max || Infinity) - ta.value.length + (ta.selectionEnd - ta.selectionStart));
export const KEYS = {
  a(ta) { const [s0] = lineBounds(ta.value, ta.selectionStart); ta.setSelectionRange(s0, s0); },
  e(ta) { const [, e0] = lineBounds(ta.value, ta.selectionEnd); ta.setSelectionRange(e0, e0); },
  u(ta) { const at = ta.selectionStart, [s0] = lineBounds(ta.value, at); kill(ta, s0, at); },
  k(ta) { const v = ta.value, at = ta.selectionStart, [, e0] = lineBounds(v, at);
          kill(ta, at, e0 === at && at < v.length ? at + 1 : e0); },
  w(ta) { const v = ta.value, at = ta.selectionStart; let i = at;
          while (i > 0 && /\s/.test(v[i - 1])) i--;
          while (i > 0 && !/\s/.test(v[i - 1])) i--;
          kill(ta, i, at); },
  y(ta, max) { if (killBuf) edit(ta, ta.selectionStart, ta.selectionEnd, killBuf.slice(0, room(ta, max))); },
  d(ta) { const a = ta.selectionStart, b = ta.selectionEnd;
          if (b > a) edit(ta, a, b, ''); else if (a < ta.value.length) edit(ta, a, a + 1, ''); },
  j(ta, max) { if (room(ta, max)) edit(ta, ta.selectionStart, ta.selectionEnd, '\n'); },
};
/** Ctrl+<letter> (no ⌘ / Alt / Shift) in `ta`: run it and return true; anything else → false (the browser keeps it). */
export function termKey(e, ta, max) {
  if (!e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.isComposing) return false;
  const k = e.key && e.key.length === 1 ? e.key.toLowerCase() : '';
  if (!KEYS[k]) return false;
  e.preventDefault();
  KEYS[k](ta, max);
  return true;
}
/** `\` right before the caret + Enter = a new line instead of sending (Claude Code's `\` + Enter). */
export function backslashEnter(ta) {
  const at = ta.selectionStart;
  if (at !== ta.selectionEnd || at < 1 || ta.value[at - 1] !== '\\') return false;
  edit(ta, at - 1, at, '\n');
  return true;
}
export const killed = () => killBuf;
export function forget() { killBuf = ''; }
