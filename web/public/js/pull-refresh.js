// Touch only, one gesture starting at the message scroller's top. Never claim cards/composer/other scrollers.
import { t, onLang } from './t.js';
import { SVG } from './icons.js';
export function initPullRefresh(main, {blocked, refresh}) {
  const hint=document.createElement('div');
  hint.id='pull-refresh'; hint.hidden=true; hint.setAttribute('role','status');
  const svg=document.createElementNS(SVG,'svg');
  for(const [k,v] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.8','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(k,v);
  const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M12 4v14m-5-5 5 5 5-5');svg.append(path);
  hint.append(svg,document.createElement('span'));
  main.prepend(hint);
  let start=null, distance=0, busy=false;
  const words=()=>{hint.lastElementChild.textContent=t(busy?'p422.refreshing':distance>=72?'p422.release':'p422.pull');};
  onLang(words);
  new MutationObserver(()=>{if(blocked() || document.body.dataset.fivePage && document.body.dataset.fivePage!=='chat')reset();}).observe(document.body,{attributes:true,attributeFilter:['data-view','data-five-page']});
  const reset=()=>{start=null;distance=0;hint.hidden=true;delete main.dataset.pulling;};
  const allowed=target=>{
    if (blocked() || document.body.dataset.fivePage && document.body.dataset.fivePage!=='chat') return false;
    for(let n=target;n && n!==main;n=n.parentElement) {
      if(n.matches('button,a,input,textarea,select,[contenteditable=true],.om,.mcard,.mview,.sheet,.ajmodal,.gdlg'))return false;
      if(n.scrollHeight>n.clientHeight+1 && /auto|scroll/.test(getComputedStyle(n).overflowY)) return false;
    }
    return true;
  };
  main.addEventListener('touchstart',e=>{
    reset();
    if(busy || e.touches.length!==1 || main.scrollTop>0 || !allowed(e.target))return;
    start={x:e.touches[0].clientX,y:e.touches[0].clientY,id:e.touches[0].identifier};
  },{passive:true});
  main.addEventListener('touchmove',e=>{
    if(!start)return;
    const p=e.touches[0];
    if(e.touches.length!==1 || p.identifier!==start.id || main.scrollTop>0 || blocked()){reset();return;}
    const dx=p.clientX-start.x,dy=p.clientY-start.y;
    if(dy<0 || Math.abs(dx)>Math.max(12,dy*.7)){reset();return;}
    if(dy<10)return;
    if(!e.cancelable){reset();return;}
    e.preventDefault(); // cancel WebKit rubber-band only after claiming a vertical top-edge pull
    distance=Math.min(110,dy*.55);hint.hidden=false;main.dataset.pulling='1';words();
    hint.style.setProperty('--pull',Math.min(54,distance)+'px');
    main.style.setProperty('--pull-space',Math.min(72,distance)+'px');
  },{passive:false});
  main.addEventListener('touchcancel',reset,{passive:true});
  main.addEventListener('touchend',async e=>{
    if(!start)return;
    const go=!e.touches.length && distance>=72 && main.scrollTop<=0 && !blocked();
    reset();if(!go)return;
    busy=true;hint.hidden=false;words();
    try{await refresh();}finally{busy=false;reset();}
  },{passive:true});
  return {cancel:reset};
}
