// P263: one header owner, official dance outside chat; custom logos remain chat-only.
import { themeLogo, singleThemeLogo, onTheme } from './theme.js';
export const LOGOS = {idle:'brand/img/logo-mark.png',working:'brand/img/status/logo-working.png',waiting:'brand/img/status/logo-waiting.png',question:'brand/img/status/logo-question.png',offline:'brand/img/status/logo-offline.png'};
const sheets = Object.fromEntries(Object.keys(LOGOS).map(k=>[k,`brand/img/status/dance-${k}.png`]));
const preloads = [...Object.values(LOGOS), ...Object.values(sheets)].map(src=>{const im=new Image();im.src=src;return im;});
// P464: CSS owns the effective state, including connection, stop and theme overrides.
// Read its resolved --state (the input to the page's --pg); never parse a parallel
// status snapshot here. Custom artwork remains owned by the theme.
const OFFICIAL = {idle:'#629887',working:'#6a9bcc',waiting:'#d97757',question:'#827dbd',offline:'#858585'};
const tint = new Map();
export function pageState() {
  const style=getComputedStyle(document.body), hex=style.getPropertyValue('--state').trim().toLowerCase();
  const state=Object.keys(LOGOS).find(k=>style.getPropertyValue('--c-'+k).trim().toLowerCase()===hex) || 'offline';
  return {state, hex:/^#[0-9a-f]{6}$/.test(hex) ? hex : OFFICIAL[state]};
}
// Cache by original artwork and colour; never tint a previously tinted output.
let tintEpoch = 0;
function clearTints() {
  tintEpoch++;
  for (const url of tint.values()) if (url?.startsWith('blob:')) URL.revokeObjectURL(url);
  tint.clear();
}
function tinted(hex, src=LOGOS.working, from=[106,155,204]) {
  const key=src+'|'+hex, epoch=tintEpoch;
  if (tint.has(key)) return tint.get(key);
  tint.set(key, null);
  const im=new Image();
  im.onload=()=>{try{if(epoch!==tintEpoch)return;
    const c=document.createElement('canvas');c.width=im.naturalWidth;c.height=im.naturalHeight;const g=c.getContext('2d');g.drawImage(im,0,0);
    const d=g.getImageData(0,0,c.width,c.height),a=d.data,to=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));let changed=false;
    // Inspect the robot's body, not stray idle-green pixels elsewhere in custom art.
    const chest=(Math.floor(c.height/2)*c.width+Math.floor(c.width/2))*4;
    if(from[0]===98 && (a[chest]!==98||a[chest+1]!==152||a[chest+2]!==135||a[chest+3]!==255)){tint.set(key,src);return;}
    for(let i=0;i<a.length;i+=4)if(a[i]===from[0]&&a[i+1]===from[1]&&a[i+2]===from[2]&&a[i+3]>0){a[i]=to[0];a[i+1]=to[1];a[i+2]=to[2];changed=true;}
    if(!changed){tint.set(key,src);return;}
    g.putImageData(d,0,0);c.toBlob(b=>{if(!b||epoch!==tintEpoch)return;tint.set(key,URL.createObjectURL(b));paintHeader();},'image/png');}catch(_){/* unreadable artwork stays unchanged */}};
  im.src=src;
  return null;
}
export function paintHeader() {
  const b=document.body, orb=document.getElementById('orb');
  if (!orb) return;
  const outside=b.dataset.view!=='chat' || (b.dataset.fivePage && b.dataset.fivePage!=='chat') || document.getElementById('settings')?.hidden===false || document.getElementById('five-menu')?.hidden===false;
  const {state,hex:colour}=pageState();
  const own=!outside && themeLogo(state), single=!outside && singleThemeLogo();
  const src=own ? (single && colour!==OFFICIAL.idle && tinted(colour,single,[98,152,135])) || own
    : (!outside && colour!==OFFICIAL[state] && tinted(colour)) || LOGOS[state];
  if(orb.getAttribute('src')!==src) orb.setAttribute('src',src);
  if(orb.dataset.v!==state)orb.dataset.v=state;
  if(orb.classList.contains('father-dance')!==!!outside)orb.classList.toggle('father-dance',!!outside);
  const sheet=`url("${sheets[state]}")`;
  if(orb.style.getPropertyValue('--father-sheet')!==sheet)orb.style.setProperty('--father-sheet',sheet);
}
export function initHeaderLogo() {
  const obs=new MutationObserver(()=>paintHeader());
  obs.observe(document.body,{attributes:true,attributeFilter:['data-view','data-five-page','data-conn','data-status','data-kind','data-agent','style','class']});
  const settings=document.getElementById('settings');
  if(settings)obs.observe(settings,{attributes:true,attributeFilter:['hidden']});
  const menuReady=()=>{const menu=document.getElementById('five-menu');if(!menu)return false;
    obs.observe(menu,{attributes:true,attributeFilter:['hidden']});paintHeader();return true;};
  // The five-page menu is created on ready. Stop watching content once it exists:
  // media cards and streamed replies must never cause header style writes/layout work.
  if(!menuReady()){const born=new MutationObserver(()=>{if(menuReady())born.disconnect();});born.observe(document.body,{subtree:true,childList:true});}
  onTheme(()=>{clearTints();paintHeader();});
  const scheme=matchMedia('(prefers-color-scheme: dark)');
  scheme.addEventListener('change',()=>paintHeader());
  obs.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','style','class']});
  paintHeader();
}
