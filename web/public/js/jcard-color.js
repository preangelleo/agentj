// P386 user-layer colors: host configuration survives upgrades, never local storage.
import {ask1} from './session.js';
import {t} from './t.js';
import {registerLayer} from './layers.js';
registerLayer({element:()=>document.querySelector('.jc-dialog[open]'),open:()=>!!document.querySelector('.jc-dialog[open]'),close:()=>document.querySelector('.jc-dialog[open]')?.dispatchEvent(new Event('cancel',{cancelable:true})),rank:80});
let colors={},generation=0;
export function resetColors(){generation++;document.querySelector('.jc-color-dialog')?.dispatchEvent(new Event('cancel',{cancelable:true}));setColors({});}
export function setColors(value) { colors=value&&typeof value==='object'?value:{}; for(const el of document.querySelectorAll('[data-color-card]')) apply(el,colors[el.dataset.colorCard]); }
const valid=v=>typeof v==='string'&&/^#[0-9a-f]{6}$/i.test(v);
function luminance(hex) { const a=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return .2126*a[0]+.7152*a[1]+.0722*a[2]; }
export function ink(hex) { const l=luminance(hex),dark=(l+.05)/(luminance('#141414')+.05),light=(luminance('#faf9f5')+.05)/(l+.05);return {color:dark>=light?'#141414':'#faf9f5',ratio:Math.max(dark,light)}; }
function apply(el,color) { if(!valid(color)||ink(color).ratio<4.5){el.style.removeProperty('background-color');el.style.removeProperty('color');return;}el.style.backgroundColor=color;el.style.color=ink(color).color; }
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
 let current=colors[id]||getComputedStyle(source).backgroundColor;
 const rgbHex=v=>{const a=v.match(/[0-9.]+/g);return a&&a.length>=3&&!(a.length===4&&Number(a[3])===0)?'#'+a.slice(0,3).map(x=>Math.round(Number(x)).toString(16).padStart(2,'0')).join(''):null;};
 if(!valid(current))current=rgbHex(current)||'#cacadb';
 const ring=node('div',undefined,'jc-color-ring'),inner=node('div',undefined,'jc-color-center'), label=node('b',t('p386.color')),center=node('small',current.toUpperCase());inner.append(label,center);ring.append(inner);
 const ringColors=['#f7827e','#fcbe7c','#ffe292','#b7e396','#78dedc','#71bbde','#d1a7e8','#f0aad4','#dbb293'];
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 230 230');svg.classList.add('jc-ring-svg');
 ringColors.forEach((color,i)=>{const point=(radius,angle)=>{const a=(angle-90)*Math.PI/180;return [115+radius*Math.cos(a),115+radius*Math.sin(a)].join(' ');};const a=i*40,b=a+40,path=document.createElementNS(ns,'path');path.setAttribute('d',`M ${point(115,a)} A 115 115 0 0 1 ${point(115,b)} L ${point(68,b)} A 68 68 0 0 0 ${point(68,a)} Z`);path.setAttribute('fill',color);path.setAttribute('role','button');path.setAttribute('tabindex','0');path.setAttribute('aria-label',color);path.onclick=()=>{choose(color);showTones(i);};path.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(color);showTones(i);}};svg.append(path);});ring.append(svg);

 const tones=node('div',undefined,'jc-color-tones');
 const fieldrow=node('div',undefined,'jc-color-fields'),hex=node('input'),rgb=[];hex.maxLength=7;hex.spellcheck=false;hex.setAttribute('aria-label','HEX');
 const field=(label,input)=>{const l=node('label',label);l.append(input);fieldrow.append(l);};field('HEX',hex);
 for(const key of ['R','G','B']) {const input=node('input');input.type='number';input.min='0';input.max='255';input.inputMode='numeric';input.setAttribute('aria-label',key);rgb.push(input);field(key,input);}
 const sample=node('div',t('p386.preview'),'jc-color-preview'),contrast=node('p',undefined,'jc-color-contrast'),error=node('p',undefined,'jc-note');error.setAttribute('role','status');
 const save=button(t('p386.save'),()=>persist(current),'fp-action');
 function choose(color) {current=color;hex.value=color.toUpperCase();rgb.forEach((f,i)=>f.value=parseInt(color.slice(1+i*2,3+i*2),16));apply(sample,color);center.textContent=color.toUpperCase();contrast.textContent=t('p386.contrast')+' '+ink(color).ratio.toFixed(2)+':1';error.textContent=ink(color).ratio<4.5?t('p386.lowContrast'):'';save.disabled=ink(color).ratio<4.5;}
 function updateHex(){if(valid(hex.value)){choose(hex.value.toLowerCase());}else {error.textContent=t('p386.value');save.disabled=true;}}hex.oninput=updateHex;
 rgb.forEach(input=>input.oninput=()=>{if(rgb.every(f=>/^\d{1,3}$/.test(f.value)&&+f.value<=255)){choose('#'+rgb.map(f=>(+f.value).toString(16).padStart(2,'0')).join(''));}else{error.textContent=t('p386.value');save.disabled=true;}});
 function showTones(index){const families=[['#fbc8c6','#f7827e','#d97757'],['#fee2c5','#fcbe7c','#ebc9b7'],['#fbf2c5','#ffe292','#f7e17c'],['#dff3d1','#b7e396','#629887'],['#c4f0f0','#78dedc','#c7dedc'],['#c1e1f0','#71bbde','#6a9bcc'],['#ebd8f5','#d1a7e8','#cacadb','#827dbd'],['#f8daec','#f0aad4'],['#efddcf','#dbb293','#e3dacc']];if(index===undefined){index=families.findIndex(f=>f.includes(current));if(index<0)index=6;}tones.replaceChildren();for(const color of [...families[index],'#2c2b26','#141414']){const b=button('',()=>choose(color),'jc-tone');b.style.backgroundColor=color;b.setAttribute('aria-label',color);tones.append(b);}}showTones();
 async function persist(color){if(opened!==generation||!dialog.isConnected)return;if(color!==null&&(!valid(color)||ink(color).ratio<4.5))return;save.disabled=true;const m=await ask1({t:'jcard_color',id,color},'jcard_color',15000);if(opened!==generation||!dialog.isConnected)return;if(m.ok){if(color===null)delete colors[id];else colors[id]=color;setColors(colors);finish();}else{error.textContent=t('p386.failed');save.disabled=false;}}
 function finish(){dialog.close();dialog.remove();source.focus({preventScroll:true});}
 const actions=node('div',undefined,'jc-dialog-actions');actions.append(button(t('p386.reset'),()=>persist(null),'jc-plain'),button(t('p386.cancel'),()=>finish(),'jc-plain'),save);
 dialog.append(title,close,ring,node('p',t('p386.tones'),'jc-color-caption'),tones,fieldrow,sample,contrast,error,actions);dialog.addEventListener('cancel',e=>{e.preventDefault();finish();});document.body.append(dialog);choose(current);dialog.showModal();
}
