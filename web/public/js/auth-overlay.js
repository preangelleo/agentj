// Rescue confirmation uses the existing approval sheet and handlers above Settings. Restore its exact DOM position.
import { registerLayer, TOP_RANK } from './layers.js';
let active = false, homes = [];
export function floatAuthAsk() {
  if (active) return;
  active = true;
  for (const id of ['scrim','sheet']) {
    const n = document.getElementById(id); if (!n) continue;
    const mark = document.createComment('rescue approval home'); n.before(mark); homes.push([n,mark]);
    document.body.append(n); n.classList.add('auth-approval');
  }
  // P549 (#316): the card takes focus from the dialog under it (its own controls, never left behind the dialog)
  //   P550: in this order (as ontop.js), not document order — #sheetClose comes first in the DOM and took 长按批准's focus
  setTimeout(() => ['apprAllow', 'askSend', 'sheetClose'].map((id) => document.getElementById(id)).find((b) => b && !b.disabled && b.getClientRects().length)?.focus({ preventScroll: true }), 60);
}
registerLayer({element:()=>document.getElementById('sheet'),open:()=>active && document.body.dataset.sheet==='1',
  close:()=>document.getElementById('sheetClose').click(),fallback:()=>document.getElementById('set-back'),rank:TOP_RANK});
new MutationObserver(()=>{
  if (!active || document.body.dataset.sheet==='1') return;
  for (const [n,mark] of homes) { n.classList.remove('auth-approval'); mark.replaceWith(n); }
  homes=[]; active=false;
}).observe(document.body,{attributes:true,attributeFilter:['data-sheet']});
