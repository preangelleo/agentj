// P414 (PROTOCOL §37.12, ADR-P414-bang, backlog #179②): 「!」 commands — the always-loaded part. A message whose text starts
// with 「!」 (no attachment, no quote) is a line for this remote's web terminal, never a chat message (L1: phone-side only;
// the computer never treats chat text starting with 「!」 as a command). Terminal open → `term_line {…, bang: <sid>}`;
// closed but allowed → the P408 open (Face ID) in the collapsed state, then the line; off / not allowed / an older host →
// one explanation. The result comes back as the owner's own page (`src.term`) and is drawn here as a terminal result card
// above the reply. The terminal page itself (js/terminal.js) is still loaded on first use only.
import * as termhook from './termhook.js';
import { isReady } from './session.js';
import { newSid, myIdSync } from './api.js';
import { t } from './t.js';
import { el, mk, toast, noticeSheet } from './ui.js';
import { copyable } from './copycode.js';

/** A 「!」 at the very start (no space before it; 「!!」 / 「！！」 stay chat). A full-width 「！」 counts only before an ASCII
 *  character (Chinese input often types 「！」 in ordinary sentences). */
const HEAD = /^(?:!(?!!)|！(?=\s*[\x21-\x7e])(?!！))/;
/** H1c: Unicode format characters (bidi U+202A–202E / U+2066–2069, zero-width U+200B–200F / U+2060 / U+FEFF, …) or a run
 *  of more than 8 spaces — never sent as a terminal line (the host refuses them too). */
export const UNSAFE = /\p{Cf}| {9,}/u;
export const CARD_LINES = 12;
/** Chat text → the terminal line it asks for, or null (not a 「!」 command: one line, something after the 「!」). Pure. */
export function bangLine(text) {
  if (typeof text !== 'string') return null;
  const s = text.replace(/\s+$/, '');
  if (!HEAD.test(s) || /[\r\n\u2028\u2029]/.test(s)) return null;
  const line = s.slice(1).trim();
  return line ? line : null;
}

/** relay.js say(): → false (not a 「!」 command: send as chat) or a promise of true (handled — clear the field) / false (keep it). */
export function take(text) {
  const line = bangLine(text);
  if (line === null) return false;
  return run(line);
}
async function run(line) {
  if (UNSAFE.test(line)) { toast(t('p414.cf'), 5000, 'error'); return false; }
  if (!isReady()) { toast(t('p414.offline'), 3600); return false; }
  const s = termhook.state();
  if (s === 'ok') return termhook.bang(line, newSid());
  explain(s);
  return false;
}
/** The one explanation: what 「!」 does, why it cannot run here, what to run at the computer, the docs page. */
function explain(s) {
  const info = termhook.termInfo(), id = myIdSync() || '<device>';
  const why = s === 'phone' ? t('p417.phone') : termhook.canEnable() ? t('p417.exOff')   // P417 #185
    : s === 'off' ? t('p414.ex.off') : s === 'notme' ? t('p414.ex.notMe', { id }) : s === 'noweb' ? t('p414.ex.noWeb', { id })
    : info ? t('p414.ex.os') : t('p414.ex.old');
  noticeSheet(t('p414.ex.title'), t('p414.ex.what') + ' ' + why + ' ' + t('p414.ex.more'), t('p414.ex.ok'));
}

// ---------------------------------------------------------------- the result card (the owner's page with src.term)
const STATES = ['done', 'running', 'closed'];
/** The page keeps at most this many UTF-16 units of `term.out`. The host caps it at 16 000 characters (head + 「…」 + tail),
 *  counted in code points and measured before the agent mask's 「[已隐藏]」 words — so the page allows twice that, never
 *  cutting the tail (the last lines) of an output the host sent whole. */
export const OUT_MAX = 32768;
/** snap.toPage's `term` → {out, cut, hidden, state} or null. Pure. */
export function termOf(x) {
  if (!x || typeof x !== 'object' || typeof x.out !== 'string') return null;
  return { out: x.out.slice(0, OUT_MAX), cut: x.cut === true, hidden: Number.isInteger(x.hidden) && x.hidden > 0 ? Math.min(x.hidden, 9999) : 0,
    state: STATES.includes(x.state) ? x.state : 'done' };
}
let shownFor = null, shownId = null, open = false;
/** relay.js showPage(): draw (or remove) the card above the reply of page `p`. */
export function paintCard(p) {
  const words = el('words');
  if (!words) return;
  let box = el('bangCard');
  const term = p && p.source && p.source.k === 'phone' ? termOf(p.source.term) : null;
  el('omText')?.classList.toggle('bang', !!term);
  if (!term) { if (box) box.remove(); shownFor = null; return; }
  if (shownId !== p.id) { open = false; shownId = p.id; }
  const key = p.id + '|' + term.state + '|' + term.out.length + '|' + term.hidden + '|' + term.cut;
  if (box && shownFor === key) return;
  shownFor = key;
  if (!box) { box = mk('section', 'bangcard'); box.id = 'bangCard'; }
  if (box.nextSibling !== words) words.before(box);
  box.setAttribute('aria-label', t('p414.card.aria'));
  const lines = term.out.replace(/\n$/, '').split('\n');
  const long = lines.length > CARD_LINES;
  const pre = mk('pre', 'bangout'); pre.id = 'bangOut';
  const preBox = copyable(pre, { variant: 'block', click: false });   // P292: the copy icon (the whole output)
  pre.textContent = term.out || t('p414.card.empty');
  pre.classList.toggle('none', !term.out);
  box.classList.toggle('folded', long && !open);
  const notes = [];
  if (term.state !== 'done') notes.push(t('p414.card.' + term.state));
  if (term.hidden) notes.push(t('p414.card.hidden', { n: term.hidden }));
  if (term.cut) notes.push(t('p414.card.cut'));
  const kids = [preBox];
  if (notes.length) { const n = mk('p', 'bangnote', notes.join(' · ')); n.id = 'bangNote'; kids.push(n); }
  if (long) {
    const b = mk('button', 'bangmore', t(open ? 'p414.card.less' : 'p414.card.more', { n: lines.length })); b.type = 'button'; b.id = 'bangMore';
    b.setAttribute('aria-expanded', String(open));
    b.addEventListener('click', () => { open = !open; shownFor = null; paintCard(p); });
    kids.push(b);
  }
  box.replaceChildren(...kids);
}
// tests
export const _debug = () => ({ shownFor, open });
