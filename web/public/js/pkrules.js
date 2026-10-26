// P539 (#300 / #299): the pure rules behind js/pkassist.js — no DOM, no dictionary, so snap.js / api.js and node tests can
// use them. pkassist.js re-exports everything here.
export const FA_S = ['local', 'borrowed', 'unknown'];
/** A card's `fa_s` → 'local' | 'borrowed' | 'unknown' | null (older host: nothing extra is shown). */
export const faState = (v) => (FA_S.includes(v) ? v : null);
export const PK_STATES = ['none', 'local', 'borrowed', 'unknown'];
/** `pk_state.s` / a remotes row's `pk` → one of PK_STATES or null. */
export const pkState = (v) => (PK_STATES.includes(v) ? v : null);
/** The quiet line under a confirm button, by `fa_s` (null = none). */
export function hintKey(fa) {
  return fa === 'borrowed' ? 'p539.hint.borrowed' : fa === 'unknown' ? 'p539.hint.unknown' : null;
}
/** A WebAuthn failure (DOMException or its name) → the reason line. NotAllowedError = cancelled, timed out, or no
 *  matching passkey here (the browser does not say which); on a borrowed record it is almost always the last one. */
export function failKey(err, fa) {
  const n = typeof err === 'string' ? err : err && err.name;
  if (n === 'NotAllowedError') return fa === 'borrowed' ? 'p539.fail.borrowed' : 'p539.fail.cancel';
  if (n === 'AbortError') return 'p539.fail.abort';
  return 'p539.fail.other';
}
/** The error name worth keeping (never the message: it may carry browser detail). */
export const errName = (e) => (e && typeof e.name === 'string' && /^[A-Za-z]{1,40}$/.test(e.name) ? e.name : 'Error');

/** The error name of a WebAuthn rejection (a DOMException), or null for our own "no passkey here" errors. */
export const webauthnErr = (e) => (typeof DOMException !== 'undefined' && e instanceof DOMException ? errName(e) : null);

export const WHY = ['timeout', 'denied', 'passkey', 'nobody', 'estop', 'serve_stop'];
/** `ask_done.why` → its dictionary key, or null (absent / unknown: the card keeps r.appr.st.*). */
export const whyKey = (w) => (WHY.includes(w) ? 'p539.why.' + w : null);

export const PREAUTH_RE = /^preauth:[a-z.]{1,40}$/;
const LABEL = { 'computer.control': 'p539.pre.computer.control' };
export const NAME = { 'computer.control': 'p539.pre.name.computer.control' };
export const actionOf = (a) => String(a).replace(/^preauth:/, '');
/** allow_offer items a non-mail card may carry: strict pattern, at most 3. */
export function preauthOffer(list) {
  return Array.isArray(list) ? list.filter((a) => typeof a === 'string' && PREAUTH_RE.test(a)).slice(0, 3) : [];
}
/** The checkbox text key of one `preauth:<action>` item, null for an action this page cannot name (not rendered). */
export const preauthLabelKey = (item) => (typeof item === 'string' && PREAUTH_RE.test(item) && Object.hasOwn(LABEL, actionOf(item)) ? LABEL[actionOf(item)] : null);
/** The offers worth a checkbox. */
export const preauthShown = (list) => preauthOffer(list).filter((a) => preauthLabelKey(a));
const clean = (s, n) => (typeof s === 'string' ? s.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, n) : '');
/** Host `{t:'preauth', items}` → [{action, device, name, at}] (≤ 32 rows; bad rows dropped). */
export function preauthItems(m) {
  const items = Array.isArray(m?.items) ? m.items : [];
  // One row per action × remote: a remote may hold one grant per binding (`via`), and 「取消免问」 revokes them all at once
  const rows = new Map();
  for (const x of items.slice(0, 32).filter((x) => x && typeof x.action === 'string' && /^(?:preauth:)?[a-z.]{1,40}$/.test(x.action))) {
    const r = { action: x.action, device: clean(x.device, 64), name: clean(x.name, 64), at: Number.isInteger(x.at) && x.at > 0 ? x.at : null };
    const k = r.action.replace(/^preauth:/, '') + '\n' + (r.device ?? '');
    const old = rows.get(k);
    if (!old || (r.at ?? 0) > (old.at ?? 0)) rows.set(k, r);
  }
  return [...rows.values()];
}
export const DRAFT_MAX = 20000;
/** `draft_restore` → {text, key}: text null = nothing to put back (say 「请重新输入要发的话」). */
export function draftRestore(m) {
  const s = typeof m?.text === 'string' ? m.text.slice(0, DRAFT_MAX) : '';
  return s.trim() ? { text: s, key: 'p539.draft.back' } : { text: null, key: 'p539.draft.empty' };
}
/** Composer empty → the text; else the current words, then the text on a new line. */
export function mergeDraft(cur, text) {
  const c = String(cur ?? '');
  return c.trim() ? c.replace(/\s+$/, '') + '\n' + text : text;
}
