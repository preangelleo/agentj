// P386 user-layer colors: host configuration survives upgrades, never local storage.
import {ask1} from './session.js';
import {t} from './t.js';
import {mountPalette} from './palette.js';
import {registerLayer} from './layers.js';
registerLayer({element:()=>document.querySelector('.jc-dialog[open]'),open:()=>!!document.querySelector('.jc-dialog[open]'),close:()=>document.querySelector('.jc-dialog[open]')?.dispatchEvent(new Event('cancel',{cancelable:true})),rank:80});
let colors={},generation=0;
export function resetColors(){generation++;document.querySelector('.jc-color-dialog')?.dispatchEvent(new Event('cancel',{cancelable:true}));setColors({});}
export function setColors(value) { colors=value&&typeof value==='object'?value:{}; for(const el of document.querySelectorAll('[data-color-card]')) apply(el,colors[el.dataset.colorCard]); }
const valid=v=>typeof v==='string'&&/^#[0-9a-f]{6}$/i.test(v);
function luminance(hex) { const a=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return .2126*a[0]+.7152*a[1]+.0722*a[2]; }
// P448 supersedes P393/P412's low-contrast colour exception for card buttons.
// Keep the owner's fill; use Lotion only when it reaches the strict 4.5:1 floor, otherwise Sooty.
const LOTION='#faf9f5',SOOTY='#141414',SYSTEM=['#629887','#6a9bcc','#827dbd','#d97757','#2c2b26'];
const ratio=(a,b)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
export const FLOOR=4.5; // P448: card buttons obey the same strict label floor as every control.
export function ink(hex) { const l=ratio(LOTION,hex);return l>=FLOOR?{color:LOTION,ratio:l}:{color:SOOTY,ratio:ratio(SOOTY,hex)}; }
function apply(el,color) { if(!valid(color)){el.style.removeProperty('background-color');el.style.removeProperty('color');el.style.removeProperty('--jc-ink');return;}const c=ink(color).color;el.style.backgroundColor=color;el.style.color=c;el.style.setProperty('--jc-ink',c); }
// P412 #177 (「应该像一个完整的调色板……可以选择任意颜色，当然也可以输入 HEX value」): hue / saturation / value ⇄ #rrggbb
export function hsv(hex) { const [r,g,b]=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255),M=Math.max(r,g,b),d=M-Math.min(r,g,b);let h=0;if(d)h=M===r?((g-b)/d+6)%6:M===g?(b-r)/d+2:(r-g)/d+4;return {h:h*60,s:M?d/M:0,v:M}; }
export function fromHsv(h,s,v) { const f=n=>{const k=(n+h/60)%6;return v-v*s*Math.max(0,Math.min(k,4-k,1));};return '#'+[5,3,1].map(n=>Math.round(f(n)*255).toString(16).padStart(2,'0')).join(''); }
/** The palette, kept as the picker's 「推荐」 row (the owner may still choose any colour). */
export const RECOMMENDED=[...SYSTEM,'#141414','#f7827e','#fcbe7c','#ffe292','#b7e396','#78dedc','#71bbde','#d1a7e8','#f0aad4','#dbb293','#e3dacc','#ebc9b7','#c7dedc'];
function node(tag,text,cls) { const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el; }
function button(text,fn,cls) { const b=node('button',text,cls);b.type='button';b.onclick=fn;return b; }
/** Hidden geometric hot spot within the card, with no extra visible control. */
// @ix app.card_color
export function cardColor(el,id) {
 el.dataset.colorCard=id;apply(el,colors[id]);let timer=null,start=null,suppress=false;
 const cancel=()=>{clearTimeout(timer);timer=null;};
 el.addEventListener('pointerdown',e=>{suppress=false;const r=el.getBoundingClientRect(),x=e.clientX-(r.right-26),y=e.clientY-(r.top+26);if(e.button!==0||x*x+y*y>26*26)return;start=[e.clientX,e.clientY];cancel();timer=setTimeout(()=>{suppress=true;setTimeout(()=>{suppress=false;},700);open(id,el);},600);});
 el.addEventListener('pointermove',e=>{if(start&&Math.hypot(e.clientX-start[0],e.clientY-start[1])>10)cancel();});
 for(const event of ['pointerup','pointercancel','pointerleave'])el.addEventListener(event,cancel);
 el.addEventListener('contextmenu',e=>{if(timer||suppress)e.preventDefault();});
 el.addEventListener('click',e=>{if(suppress&&document.querySelector('.jc-color-dialog[open]')){e.preventDefault();e.stopImmediatePropagation();}suppress=false;},true);
}
function open(id,source) {
 document.querySelector('.jc-color-dialog')?.dispatchEvent(new Event('cancel',{cancelable:true}));
 const opened=generation;
 const dialog=node('dialog',undefined,'jc-dialog jc-color-dialog'), title=node('h2',t('p386.color')), close=button('×',()=>finish(), 'jc-dialog-close');close.setAttribute('aria-label',t('p386.cancel'));
 let current=colors[id]||getComputedStyle(source).backgroundColor,saving=false;
 const rgbHex=v=>{const a=v.match(/[0-9.]+/g);return a&&a.length>=3&&!(a.length===4&&Number(a[3])===0)?'#'+a.slice(0,3).map(x=>Math.round(Number(x)).toString(16).padStart(2,'0')).join(''):null;};
 if(!valid(current))current=rgbHex(current)||'#cacadb';
 let {h,s:sat,v:val}=hsv(current);
 // the saturation / value square (drag, tap, arrow keys; Shift = bigger steps) and the hue bar under it
 const sv=node('div',undefined,'jc-sv'),dot=node('span',undefined,'jc-sv-dot');sv.append(dot);sv.tabIndex=0;sv.setAttribute('role','slider');sv.setAttribute('aria-label',t('p412.svPanel'));
 const hue=node('input',undefined,'jc-hue');hue.type='range';hue.min='0';hue.max='359';hue.step='1';hue.setAttribute('aria-label',t('p412.hue'));
 const fromSv=e=>{const r=sv.getBoundingClientRect();sat=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));val=Math.max(0,Math.min(1,1-(e.clientY-r.top)/r.height));choose(fromHsv(h,sat,val),true);};
 sv.addEventListener('pointerdown',e=>{if(e.button)return;e.preventDefault();sv.setPointerCapture?.(e.pointerId);sv.focus({preventScroll:true});fromSv(e);});
 sv.addEventListener('pointermove',e=>{if(sv.hasPointerCapture?.(e.pointerId))fromSv(e);});
 sv.addEventListener('keydown',e=>{const st=e.shiftKey?.1:.02,d={ArrowLeft:[-st,0],ArrowRight:[st,0],ArrowUp:[0,st],ArrowDown:[0,-st]}[e.key];if(!d)return;e.preventDefault();sat=Math.max(0,Math.min(1,sat+d[0]));val=Math.max(0,Math.min(1,val+d[1]));choose(fromHsv(h,sat,val),true);});
 hue.oninput=()=>{h=+hue.value;choose(fromHsv(h,sat,val),true);};
 const rec=node('div',undefined,'jc-color-rec');
 for(const color of RECOMMENDED){const b=button('',()=>choose(color),'jc-tone');b.style.backgroundColor=color;b.setAttribute('aria-label',color.toUpperCase());b.dataset.color=color;rec.append(b);}
 const fieldrow=node('div',undefined,'jc-color-fields'),hex=node('input'),rgb=[];hex.maxLength=7;hex.spellcheck=false;hex.setAttribute('aria-label','HEX');
 const field=(label,input)=>{const l=node('label',label);l.append(input);fieldrow.append(l);};field('HEX',hex);
 for(const key of ['R','G','B']) {const input=node('input');input.type='number';input.min='0';input.max='255';input.inputMode='numeric';input.setAttribute('aria-label',key);rgb.push(input);field(key,input);}
 const sample=node('div',t('p386.preview'),'jc-color-preview'),contrast=node('p',undefined,'jc-color-contrast'),error=node('p',undefined,'jc-note jc-color-why');error.setAttribute('role','status');
 const save=button(t('p386.save'),()=>persist(current),'fp-action');
 // never a Save that looks live and does nothing: it is either enabled, or grey with the reason written right above it
 function block(why){save.disabled=!!why;error.textContent=why||'';if(why)save.title=why;else save.removeAttribute('title');}
 function paint(color,keepHsv){sv.style.setProperty('--jc-hue',fromHsv(h%360,1,1));dot.style.left=sat*100+'%';dot.style.top=(1-val)*100+'%';dot.style.background=color;sv.setAttribute('aria-valuetext',color.toUpperCase());if(!keepHsv||document.activeElement!==hue)hue.value=String(Math.round(h)%360);
  for(const b of rec.children)b.setAttribute('aria-pressed',String(b.dataset.color===color));}
 let palette;
 function choose(color,keepHsv) {current=color;palette?.setSeed(color);if(!keepHsv)({h,s:sat,v:val}=hsv(color));if(document.activeElement!==hex)hex.value=color.toUpperCase();rgb.forEach((f,i)=>{if(document.activeElement!==f)f.value=parseInt(color.slice(1+i*2,3+i*2),16);});apply(sample,color);const k=ink(color);
  contrast.textContent=t('p386.contrast')+' '+k.ratio.toFixed(2)+':1'+(k.color===SOOTY?' · '+t('p412.darkInk'):'');paint(color,keepHsv);block(saving?t('p412.saving'):'');}
 function updateHex(){const v=hex.value.trim(),full=/^#?[0-9a-f]{6}$/i.test(v)?(v[0]==='#'?v:'#'+v).toLowerCase():null;if(full)choose(full);else block(t('p386.value'));}hex.oninput=updateHex;
 rgb.forEach(input=>input.oninput=()=>{if(rgb.every(f=>/^\d{1,3}$/.test(f.value)&&+f.value<=255)){choose('#'+rgb.map(f=>(+f.value).toString(16).padStart(2,'0')).join(''));}else block(t('p386.value'));});
 async function persist(color){if(opened!==generation||!dialog.isConnected||saving)return;if(color!==null&&!valid(color))return;saving=true;block(t('p412.saving'));const m=await ask1({t:'jcard_color',id,color},'jcard_color',15000);saving=false;if(opened!==generation||!dialog.isConnected)return;if(m.ok){if(color===null)delete colors[id];else colors[id]=color;setColors(colors);finish();}else{block('');error.textContent=t('p386.failed');}}
 function finish(){dialog.close();dialog.remove();source.focus({preventScroll:true});}
 const actions=node('div',undefined,'jc-dialog-actions');actions.append(button(t('p386.reset'),()=>persist(null),'jc-plain'),button(t('p386.cancel'),()=>finish(),'jc-plain'),save);
 palette=mountPalette({seed:current,onChoose:choose,embedded:true});
 dialog.append(title,close,sv,hue,node('p',t('p412.recommended'),'jc-color-caption'),rec,fieldrow,sample,contrast,palette.element,error,actions);dialog.addEventListener('cancel',e=>{e.preventDefault();finish();});document.body.append(dialog);choose(current);dialog.showModal();
}
