// P142 (Leo 10-11 13:49, iPhone home-screen app): after the lock screen the page came back using only the top ~60 % of the
// screen — --vvh still held a keyboard-time (or lock-time) visualViewport height, and iOS standalone fires no visualViewport
// resize on resume. Rules:
//  - normally the body follows visualViewport when it is > 120 px shorter than the layout viewport (the keyboard), else
//    --vvh / --vvtop are dropped and CSS falls back to 100dvh, which the engine recomputes itself;
//  - on a RESUME (pageshow, visibilitychange → visible) with no text field focused, the keyboard is not up: full height, and
//    the short visualViewport height seen then is treated as stale until it changes or a text field gets focus;
//  - re-checked on pageshow, visibility → visible, window focus, resize and visualViewport resize — now, on the next frame
//    and 300 ms later (iOS settles its numbers late).
const EDITABLE = 'input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable="true"]';
let staleH = null, resuming = false;

const editing = () => { const a = document.activeElement; return !!a && !!a.matches && a.matches(EDITABLE); };

export function applyViewport() {
  const vv = window.visualViewport, st = document.documentElement.style, typing = editing();
  const short = !!vv && window.innerHeight - vv.height > 120;
  if (resuming && !typing) staleH = short ? vv.height : null;
  else if (staleH !== null && (typing || !vv || vv.height !== staleH)) staleH = null;
  const kb = short && staleH === null;
  // Safari's toolbar can shrink the visual viewport by less than the keyboard threshold.
  // Always follow its live bounds at normal zoom; standalone retains P142's stale-resume guard.
  const html = document.documentElement;
  const safari = html.dataset.ios === '1' && html.dataset.standalone !== '1' && vv && (!vv.scale || vv.scale === 1);
  if (kb || safari) { st.setProperty('--vvh', vv.height + 'px'); st.setProperty('--vvtop', vv.offsetTop + 'px'); }
  else { st.removeProperty('--vvh'); st.removeProperty('--vvtop'); }
  if (document.body) document.body.dataset.kb = kb ? '1' : '0';
  return kb;
}

/** Wire `fit` (which calls applyViewport) to every resume / resize signal. Idempotent per function. */
const wired = new Set();
export function watchViewport(fit) {
  if (wired.has(fit)) return;
  wired.add(fit);
  const once = (resume) => { resuming = resume; try { fit(); } finally { resuming = false; } };
  const again = (resume) => { once(resume); requestAnimationFrame(() => once(resume)); setTimeout(() => once(resume), 300); };
  window.addEventListener('pageshow', () => again(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) again(true); });
  window.addEventListener('focus', () => again(false));
  window.addEventListener('resize', () => again(false));
  window.visualViewport?.addEventListener('resize', () => again(false));
  window.visualViewport?.addEventListener('scroll', () => once(false));
  fit();
}
