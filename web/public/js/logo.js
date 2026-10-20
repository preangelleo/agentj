// P263: one header owner, official dance outside chat; custom logos remain chat-only.
import { themeLogo, onTheme } from './theme.js';
export const LOGOS = {idle:'brand/img/logo-mark.png',working:'brand/img/status/logo-working.png',waiting:'brand/img/status/logo-waiting.png',question:'brand/img/status/logo-question.png',offline:'brand/img/status/logo-offline.png'};
const sheets = Object.fromEntries(Object.keys(LOGOS).map(k=>[k,`brand/img/status/dance-${k}.png`]));
const preloads = [...Object.values(LOGOS), ...Object.values(sheets)].map(src=>{const im=new Image();im.src=src;return im;});
// P393 #137: the robot wears the state colour the page shows. The state is read from the same body attributes the background
// colour reads (never a cached copy that can lag behind), and a theme that changes a state colour without its own logo gets
// the official robot repainted in that colour (body pixels only; a theme's own logo is never touched).
const OFFICIAL = {idle:'#629887',working:'#6a9bcc',waiting:'#d97757',question:'#827dbd',offline:'#858585'};
const tint = new Map();
const stateOf = (st,kind) => st==='waiting' && kind==='question' ? 'question' : (LOGOS[st] ? st : 'offline');
export function stateColour(state) {
  const v=getComputedStyle(document.body).getPropertyValue('--c-'+state).trim().toLowerCase();
  return /^#[0-9a-f]{6}$/.test(v) ? v : OFFICIAL[state];
}
function tinted(hex) {
  if (tint.has(hex)) return tint.get(hex);
  tint.set(hex, null);
  const im=new Image();
  im.onload=()=>{try{const c=document.createElement('canvas');c.width=im.naturalWidth;c.height=im.naturalHeight;const g=c.getContext('2d');g.drawImage(im,0,0);
    const d=g.getImageData(0,0,c.width,c.height),a=d.data,to=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));
    for(let i=0;i<a.length;i+=4)if(a[i]===106&&a[i+1]===155&&a[i+2]===204&&a[i+3]===255){a[i]=to[0];a[i+1]=to[1];a[i+2]=to[2];}
    g.putImageData(d,0,0);c.toBlob(b=>{if(!b)return;tint.set(hex,URL.createObjectURL(b));paintHeader();},'image/png');}catch(_){/* the official logo stays */}};
  im.src=LOGOS.working;
  return null;
}
let chatState = 'idle';
export function paintHeader(st,kind) {
  if (st) chatState = stateOf(st,kind);
  const b=document.body, orb=document.getElementById('orb');
  if (!orb) return;
  const outside=b.dataset.view!=='chat' || (b.dataset.fivePage && b.dataset.fivePage!=='chat') || document.getElementById('settings')?.hidden===false || document.getElementById('five-menu')?.hidden===false;
  const state=b.dataset.conn==='off' ? 'offline' : b.dataset.status ? stateOf(b.dataset.status,b.dataset.kind) : chatState;
  const hex=outside || themeLogo(state) ? null : stateColour(state);
  const src=(!outside && themeLogo(state)) || (hex && hex!==OFFICIAL[state] && tinted(hex)) || LOGOS[state];
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
