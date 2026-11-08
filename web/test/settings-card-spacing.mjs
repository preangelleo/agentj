// Shared rendered geometry assertion for Settings cards, including dynamically added rescue cards.
export const SETTINGS_CARD_GAPS=`(()=>{const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
const cards=[...document.querySelectorAll('#settings .setcard,#settings .rscard')].filter(visible),pairs=[];
const group=e=>e.matches('.setcard')?'sections':e.closest('.setcard')?.id;
const probe=document.createElement('i');probe.style.height='var(--ui-space-control)';probe.style.display='block';document.getElementById('settings').append(probe);const floor=probe.getBoundingClientRect().height;probe.remove();
for(const a of cards){const ar=a.getBoundingClientRect();let next=null,gap=Infinity;
for(const b of cards){if(a===b||a.contains(b)||b.contains(a)||group(a)!==group(b))continue;const br=b.getBoundingClientRect();if(Math.min(ar.right,br.right)-Math.max(ar.left,br.left)<1)continue;const g=br.top-ar.bottom;if(br.top>=ar.top&&g<gap){next=b;gap=g}}
if(next)pairs.push({from:a.id,to:next.id,gap:Math.round(gap*100)/100});}
return {floor,pairs,cardCount:cards.length,violations:pairs.filter(p=>p.gap+0.5<floor)}})()`;
