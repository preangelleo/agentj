import { copyButton } from './copycode.js';
// P420: same component on card/palette and in the card-color dialog; host owns persistence.
import {ask1,peer} from './session.js';
import {t} from './t.js';
import {valid,generate,contrast,DIRECTIONS} from './palette-color.js';
let generation=0;
export function resetPalettes(){generation++;}
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const button=(text,fn)=>{const e=node('button',text,'jc-chip');e.type='button';e.onclick=fn;return e;};
export function mountPalette({seed='#827dbd',onChoose=null,embedded=false}={}) {
  const box=node('section',undefined,'jp-palette');
  box.setAttribute('aria-label',t('p420.title'));
  if(!peer.jpalette1){box.append(node('p',t('p420.upgrade'),'jc-note'));return {element:box,setSeed:()=>{}};}
  const opened=generation;
  let current=valid(seed)?seed.toLowerCase():'#827dbd',rows=[],colors=[],saving=false,serial=0,picking=false;
  const status=node('p','','jc-note');status.setAttribute('role','status');
  const controls=node('div',undefined,'jp-controls'),seedInput=node('input'),direction=node('select'),count=node('select');
  seedInput.value=current;seedInput.maxLength=7;seedInput.spellcheck=false;seedInput.setAttribute('aria-label',t('p420.seed'));
  const label=(key,input)=>{const l=node('label',t(key));l.append(input);controls.append(l);};
  label('p420.seed',seedInput);
  for(const key of DIRECTIONS){const opt=node('option',t('p420.'+key));opt.value=key;direction.append(opt);}
  label('p420.direction',direction);
  for(const n of [5,8,10]){const o=node('option',String(n));o.value=String(n);count.append(o);}
  label('p420.count',count);
  const sliders={};
  for(const [key,min,max,value] of [['hue',-180,180,0],['saturation',0,200,100],['lightness',-40,40,0]]) {
    const input=node('input');input.type='range';input.min=String(min);input.max=String(max);input.value=String(value);input.step='1';input.setAttribute('aria-label',t('p420.'+key));
    const output=node('output',String(value));input.oninput=()=>{output.value=input.value;paint();};
    const l=node('label',t('p420.'+key));l.append(output,input);controls.append(l);sliders[key]=input;
  }
  const swatches=node('div',undefined,'jp-swatches'),library=node('div',undefined,'jp-library');
  const save=button(t('p420.save'),async()=>{
    if(saving || !valid(seedInput.value))return;
    await request({op:'save',palette:{id:crypto.randomUUID(),colors}},'p420.saved');
  });
  const refresh=button(t('p420.refresh'),()=>request({op:'list'}));
  function showStatus(key){status.textContent=key?t(key):'';}
  function swatch(color,select) {
    const light=contrast(color,'#faf9f5'),dark=contrast(color,'#141414'),ink=light>=dark?'#faf9f5':'#141414';
    const b=button('',()=>select(color));b.className='jp-swatch';b.style.backgroundColor=color;b.style.color=ink;b.dataset.color=color;
    b.setAttribute('aria-label',t('p420.pick',{hex:color.toUpperCase()}));
    b.append(node('b',color.toUpperCase()),node('small',t('p420.'+(ink==='#141414'?'dark':'light'))+' '+Math.max(light,dark).toFixed(2)+':1'));
    let timer=null,start=null,held=false;
    const cancel=()=>{clearTimeout(timer);timer=null;};
    const copy=()=>copyButton(color.toUpperCase(),b);
    b.onclick=()=>{if(held){held=false;return;}select(color);};
    // @ix app.palette_copy
    b.onpointerdown=e=>{held=false;if(e.button!==0)return;start=[e.clientX,e.clientY];cancel();timer=setTimeout(()=>{if(!box.isConnected||opened!==generation)return;held=true;copy();},600);};
    b.onpointermove=e=>{if(start&&Math.hypot(e.clientX-start[0],e.clientY-start[1])>10)cancel();};
    for(const k of ['pointerup','pointerleave','pointercancel'])b.addEventListener(k,cancel);
    b.oncontextmenu=e=>{e.preventDefault();cancel();held=true;copy();};
    b.onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='c'){e.preventDefault();copy();}};
    return b;
  }
  function choose(color){
    for(const b of box.querySelectorAll('.jp-swatch'))b.setAttribute('aria-pressed',String(b.dataset.color===color));
    if(onChoose){picking=true;try{onChoose(color);}finally{picking=false;}}else {seedInput.value=color;current=color;showStatus('p420.selected');}
  }
  function paint(){
    colors=generate(current,direction.value,+count.value,+sliders.hue.value,+sliders.saturation.value,+sliders.lightness.value);
    swatches.replaceChildren(...colors.map(c=>swatch(c,choose)));save.disabled=saving||!valid(seedInput.value);
  }
  seedInput.oninput=()=>{if(!valid(seedInput.value)){save.disabled=true;showStatus('p386.value');return;}current=seedInput.value.toLowerCase();showStatus('');paint();};
  direction.onchange=count.onchange=paint;
  function drawLibrary(){
    library.replaceChildren();
    if(!rows.length)library.append(node('p',t('p420.empty'),'jc-note'));
    rows.forEach((row,i)=>{
      const item=node('article',undefined,'jp-saved'),head=node('div',undefined,'jp-saved-head');
      head.append(node('b',t('p420.theme',{n:rows.length-i})),button(t('p420.load'),()=>{
        current=row.colors[0];seedInput.value=current;colors=row.colors.slice();swatches.replaceChildren(...colors.map(c=>swatch(c,choose)));save.disabled=saving;showStatus('p420.loaded');
      }),button(t('p420.delete'),()=>request({op:'delete',id:row.id},'p420.deleted')));
      const strip=node('div',undefined,'jp-swatches');strip.append(...row.colors.map(c=>swatch(c,choose)));item.append(head,strip);library.append(item);
    });
  }
  async function request(fields,success){
    if(saving || !box.isConnected||opened!==generation)return;
    const s=++serial;saving=true;save.disabled=refresh.disabled=true;showStatus('p420.loading');
    let m;try{m=await ask1({t:'jpalette',...fields},'jpalette',15000);}catch{m={ok:false};}
    if(s!==serial||!box.isConnected||opened!==generation)return;
    saving=false;save.disabled=!valid(seedInput.value);refresh.disabled=false;
    if(m.ok && Array.isArray(m.palettes)){
      rows=m.palettes.slice(0,32).filter(r=>r&&typeof r.id==='string'&&Array.isArray(r.colors)&&r.colors.length>=5&&r.colors.length<=10&&r.colors.every(valid));drawLibrary();showStatus(success);
    } else showStatus(m.why==='full'?'p420.full':'p386.failed');
  }
  box.append(node(embedded?'h3':'h2',t('p420.title')),node('p',t('p420.hint'),'jc-note'),controls,swatches,save,status,node('h3',t('p420.mine')),refresh,library);
  paint();
  // Mount first, then read: no request is sent for an abandoned/unmounted component.
  queueMicrotask(()=>request({op:'list'}));
  return {element:box,setSeed(color){if(!picking&&valid(color)&&color!==current){current=color;seedInput.value=color;paint();}}};
}
