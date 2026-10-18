import {activeLayer} from './layers.js';
// P387: remote keys use existing card/message/reader actions. No new authority or wire messages.
import {canAssert} from './faceid.js';
import {t} from './t.js';
import {toast} from './ui.js';
export const isTV=()=>/web0s|webos|smart-?tv|netcast|tizen|hbbtv/i.test(navigator.userAgent);
export const isRemoteScreen=()=>isTV()||(innerWidth>=1600&&innerHeight>=850);
export function phoneConfirmation(noPasskey=false){if(isRemoteScreen()&&(noPasskey||!canAssert())){toast(t('p387.phoneConfirm'),5000);return true;}return false;}
const editing=n=>n?.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(n?.tagName);
export function initRemote(H){ // @ix card.remote_reader
 if(isRemoteScreen()){document.documentElement.dataset.tv='1';if(!navigator.mediaDevices?.getUserMedia)document.documentElement.dataset.tvNoMic='1';
  navigator.permissions?.query({name:'microphone'}).then(p=>{const paint=()=>{document.documentElement.dataset.tvNoMic=(!navigator.mediaDevices?.getUserMedia||p.state==='denied')?'1':'0';};paint();p.addEventListener('change',paint);}).catch(()=>{});
 }
 let lastEnter=-Infinity;
 const focus=n=>{document.querySelectorAll('[data-tv-focus]').forEach(x=>delete x.dataset.tvFocus);if(!n)return;n.tabIndex=-1;n.dataset.tvFocus='1';n.focus({preventScroll:true});};
 window.addEventListener('keydown',e=>{
  if(e.isComposing||e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||editing(e.target)||editing(document.activeElement))return;
  if(!isRemoteScreen())return;
  const back=e.key==='Escape'||e.key==='Backspace'||e.keyCode===461;
  const layer=activeLayer();
  if(layer&&layer.element()?.id!=='rd'&&layer.rank>=20){
   if(back){e.preventDefault();e.stopImmediatePropagation();layer.close();lastEnter=-Infinity;return;}
   if(layer.element()?.id!=='five-menu')return;
  }
  if(H.reading()&&back){e.preventDefault();e.stopImmediatePropagation();H.reader(false);queueMicrotask(()=>focus(document.getElementById('rm')));lastEnter=-Infinity;return;}
  if(document.body.dataset.view!=='chat')return;
  const menu=document.getElementById('five-menu');
  if(menu&&!menu.hidden){
   if(!['ArrowUp','ArrowDown'].includes(e.key))return;
   const items=[...menu.querySelectorAll('.fp-menu-card')],at=items.indexOf(document.activeElement);
   const next=items[Math.max(0,Math.min(items.length-1,at+(e.key==='ArrowUp'?-1:1)))];
   e.preventDefault();e.stopImmediatePropagation();focus(next);return;
  }
  if([...document.querySelectorAll('.ajmodal:not([hidden]),.gdlg:not([hidden]),.sheet')].some(n=>n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden'&&n.id!=='rd'))return;
  const key=e.key;
  if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','PageUp','PageDown','Enter'].includes(key))return;
  if(key==='Enter'&&e.target.closest?.('button,a,summary'))return;
  if(key==='Enter'&&(document.body.dataset.fivePage||'chat')!=='chat')return;
  e.preventDefault();e.stopImmediatePropagation();
  if(key!=='Enter')lastEnter=-Infinity;
  if(key==='ArrowUp'||key==='ArrowDown'){if(!H.reading())focus(H.card(key==='ArrowUp'?-1:1));}
  else if(key==='ArrowLeft'||key==='ArrowRight'){if((document.body.dataset.fivePage||'chat')==='chat')H.message(key==='ArrowLeft'?-1:1);}
  else if(key==='PageUp'||key==='PageDown')H.scroll(key==='PageUp'?-1:1);
  else if(!e.repeat){const now=performance.now();if(now-lastEnter<400){H.reader(true);focus(document.getElementById('rd'));lastEnter=-Infinity;}else{lastEnter=now;focus(document.getElementById('rm'));}}
 },true);
}
