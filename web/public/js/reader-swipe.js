// Reader-only touch gestures. Native vertical scrolling, selection and edge-back keep ownership.
export function bindReaderSwipe(node, {open, step}) {
  let down = null;
  const selection = () => { const s = getSelection(); return s && !s.isCollapsed; };
  const blocked = t => !!t.closest('button,a,input,textarea,select,audio,video,iframe,.mcard,pre,table');
  node.addEventListener('touchstart', e => {
    down = null;
    if (!open() || e.touches.length !== 1 || selection() || blocked(e.target)) return;
    const t = e.touches[0];
    if (t.clientX < 24 || t.clientX > innerWidth - 24) return;
    down = {x:t.clientX,y:t.clientY,t:performance.now(),horizontal:false};
  }, {passive:true});
  node.addEventListener('touchmove', e => {
    if (!down) return;
    if (e.touches.length !== 1 || selection()) { down = null; return; }
    const t=e.touches[0], dx=t.clientX-down.x, dy=t.clientY-down.y;
    if (!down.horizontal) {
      if (performance.now()-down.t > 330 || Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)*0.58) {down=null;return;}
      if (Math.abs(dx)>12 && Math.abs(dy)<Math.abs(dx)*0.58) down.horizontal=true;
    }
    if (down.horizontal && e.cancelable) e.preventDefault();
  }, {passive:false});
  node.addEventListener('touchend', e => {
    const d=down; down=null;
    if (!d || !open() || e.touches.length || selection() || !d.horizontal) return;
    const t=e.changedTouches[0]; if (!t) return;
    const dx=t.clientX-d.x, dy=t.clientY-d.y;
    if (Math.abs(dx)>=60 && Math.abs(dy)<Math.abs(dx)*0.58) step(dx>0?-1:1);
  });
  node.addEventListener('touchcancel', () => {down=null;});
}
