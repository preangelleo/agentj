// Rescue confirmation uses the existing approval sheet and handlers above Settings. Restore its exact DOM position.
import { registerLayer } from './layers.js';
let active = false, homes = [];
export function floatAuthAsk() {
  if (active) return;
  active = true;
  for (const id of ['scrim','sheet']) {
    const n = document.getElementById(id); if (!n) continue;
    const mark = document.createComment('rescue approval home'); n.before(mark); homes.push([n,mark]);
    document.body.append(n); n.classList.add('auth-approval');
  }
}
registerLayer({element:()=>document.getElementById('sheet'),open:()=>active && document.body.dataset.sheet==='1',
  close:()=>document.getElementById('sheetClose').click(),fallback:()=>document.getElementById('set-back'),rank:52});
new MutationObserver(()=>{
  if (!active || document.body.dataset.sheet==='1') return;
  for (const [n,mark] of homes) { n.classList.remove('auth-approval'); mark.replaceWith(n); }
  homes=[]; active=false;
}).observe(document.body,{attributes:true,attributeFilter:['data-sheet']});
