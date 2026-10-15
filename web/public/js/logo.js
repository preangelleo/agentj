// P263: one header owner, official dance outside chat; custom logos remain chat-only.
import { themeLogo, onTheme } from './theme.js';
export const LOGOS = {idle:'brand/img/logo-mark.png',working:'brand/img/status/logo-working.png',waiting:'brand/img/status/logo-waiting.png',question:'brand/img/status/logo-question.png',offline:'brand/img/status/logo-offline.png'};
const sheets = Object.fromEntries(Object.keys(LOGOS).map(k=>[k,`brand/img/status/dance-${k}.png`]));
const preloads = [...Object.values(LOGOS), ...Object.values(sheets)].map(src=>{const im=new Image();im.src=src;return im;});
let chatState = 'idle';
export function paintHeader(st,kind) {
  if (st) chatState = st==='waiting' && kind==='question' ? 'question' : (LOGOS[st] ? st : 'offline');
  const b=document.body, orb=document.getElementById('orb');
  if (!orb) return;
  const outside=b.dataset.view!=='chat' || (b.dataset.fivePage && b.dataset.fivePage!=='chat') || document.getElementById('settings')?.hidden===false || document.getElementById('five-menu')?.hidden===false;
  const state=b.dataset.conn==='off' ? 'offline' : chatState;
  const src=(!outside && themeLogo(state)) || LOGOS[state];
  if(orb.getAttribute('src')!==src) orb.setAttribute('src',src);
  if(orb.dataset.v!==state)orb.dataset.v=state;
  if(orb.classList.contains('father-dance')!==!!outside)orb.classList.toggle('father-dance',!!outside);
  const sheet=`url("${sheets[state]}")`;
  if(orb.style.getPropertyValue('--father-sheet')!==sheet)orb.style.setProperty('--father-sheet',sheet);
}
export function initHeaderLogo() {
  const obs=new MutationObserver(()=>paintHeader());
  obs.observe(document.body,{attributes:true,attributeFilter:['data-view','data-five-page','data-conn','data-status','data-kind']});
  const settings=document.getElementById('settings');
  if(settings)obs.observe(settings,{attributes:true,attributeFilter:['hidden']});
  const menuReady=()=>{const menu=document.getElementById('five-menu');if(!menu)return false;
    obs.observe(menu,{attributes:true,attributeFilter:['hidden']});paintHeader();return true;};
  // The five-page menu is created on ready. Stop watching content once it exists:
  // media cards and streamed replies must never cause header style writes/layout work.
  if(!menuReady()){const born=new MutationObserver(()=>{if(menuReady())born.disconnect();});born.observe(document.body,{subtree:true,childList:true});}
  onTheme(()=>paintHeader());paintHeader();
}
