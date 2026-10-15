// P302 (owner: 「在所有的子页面……从屏幕的最左边滑向右边……退出这个页面」): on a phone / tablet, a finger that starts at the left
// edge of a sub-page and slides right is a step back — the very same step as Esc and the ‹ button (it sends the page's own
// Esc, so every page closes the way it already does). Only while a sub-page is on screen, never on the chat; off where the
// browser owns that gesture itself (iOS Safari in a tab: its edge swipe already goes back through history).
const EDGE_PX = 28, BACK_PX = 72, TAN = Math.tan(30 * Math.PI / 180);
const PAGES = '#five-page,#five-detail,#five-menu,#settings,.menupage,.setpage,.ajpages,.cmdsheet';
const shown = n => n.getClientRects().length && !n.closest('[hidden]');
export const subPageOpen = () => [...document.querySelectorAll(PAGES)].some(shown);
export function browserOwnsEdge(nav = navigator, mm = matchMedia){
  const ios = /iP(hone|ad|od)/.test(nav.userAgent) || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1);
  const standalone = nav.standalone === true || mm('(display-mode: standalone)').matches || mm('(display-mode: fullscreen)').matches;
  return ios && !standalone;
}
export function initEdgeBack(){
  if (browserOwnsEdge()) return;
  let s = null;
  document.addEventListener('touchstart', e => {
    s = null;
    if (e.touches.length !== 1 || e.touches[0].clientX > EDGE_PX || !subPageOpen()) return;
    s = {x: e.touches[0].clientX, y: e.touches[0].clientY};
  }, {passive: true, capture: true});
  document.addEventListener('touchmove', e => {
    if (!s) return;
    const dx = e.touches[0].clientX - s.x, dy = e.touches[0].clientY - s.y;
    if (Math.abs(dy) > 24 && Math.abs(dy) > dx) { s = null; return; }   // a vertical scroll: let it be
    if (dx > 12 && e.cancelable) e.preventDefault();                      // ours now: no sideways page drag under it
  }, {passive: false, capture: true});
  document.addEventListener('touchend', e => {
    if (!s) return;
    const p = e.changedTouches[0], dx = p.clientX - s.x, dy = p.clientY - s.y; s = null;
    if (dx < BACK_PX || Math.abs(dy) > dx * TAN) return;
    const target = document.activeElement && document.activeElement !== document.body ? document.activeElement : document;
    target.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true, cancelable: true}));
  }, {passive: true, capture: true});
  document.addEventListener('touchcancel', () => { s = null; }, {passive: true, capture: true});
}
