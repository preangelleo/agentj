// P375: touch owners focus the composer only by tapping it. Shared by every draft surface.
export const touchComposer = () => (globalThis.navigator?.maxTouchPoints || 0) > 0 || !!globalThis.matchMedia?.('(any-pointer: coarse)').matches;
export function focusDraft(node, options){ if (!touchComposer()) node?.focus(options); }
export function releaseDraft(node){
  node?.blur(); document.getElementById('workflow-compose')?.blur();
  document.body.tabIndex = -1; document.body.focus({preventScroll: true});
}
