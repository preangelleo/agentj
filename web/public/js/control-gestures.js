// P448: delegate only control gestures, including controls added after pairing.
// Never intercept message/code selection, editable fields, scrolling, ordinary clicks or keyboard activation.
export const CONTROL = 'button,[role="button"],a.aj-btn,[role="toolbar"],.top,.rmtop,.omtools,.tools,[data-no-select]';
export function controlTarget(target) {
  if (!target?.closest || target.closest('input,textarea,select,[contenteditable="true"]')) return null;
  return target.closest(CONTROL);
}
export function initControlGestures(root = document) {
  for (const type of ['selectstart', 'contextmenu']) root.addEventListener(type, e => {
    if (controlTarget(e.target) && e.cancelable) e.preventDefault();
  }, {capture: true, passive: false});
}
