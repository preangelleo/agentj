// P119: one keyboard owner for transient UI. Owners supply their existing close/back action;
// hiding a DOM node here would skip camera cleanup, confirmation resolution or reader history.
const layers=[];
let serial=0,lastTarget=null;
const visible=e=>!!e&&e.isConnected&&e.getClientRects().length&&!e.closest('[hidden]');
const focusable='button:not([disabled]),a[href],summary,input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]';
function restore(l){
 if(layers.some(other=>other!==l&&other.open()&&other.element()?.contains(document.activeElement)&&visible(document.activeElement)))return;
 const target=visible(l.opener)&&l.opener.matches?.(focusable)?l.opener:l.fallback?.();
 if(visible(target)&&target.matches?.(focusable))target.focus({preventScroll:true});
}
function sync(){
 for(const l of layers){const on=l.open();
  if(on&&!l.on){l.order=++serial;l.opener=l.trigger?.() || (visible(lastTarget)&&!l.element()?.contains(lastTarget)?lastTarget:document.activeElement);
   if(l.element()?.contains(l.opener))l.opener=l.fallback?.();}
  if(!on&&l.on)restore(l);
  l.on=on;
 }
}
export function registerLayer({element,open,close,fallback,trigger,trap=true,rank=0}){
 const l={element,open,close,fallback,trigger,trap,rank,on:false};layers.push(l);sync();return l;
}
export function initLayers({onEscape}={}){
 document.addEventListener('click',e=>{lastTarget=e.target.closest?.(focusable)||document.activeElement;},true);
 document.addEventListener('pointerdown',e=>{lastTarget=e.target.closest?.(focusable)||document.activeElement;},true);
 document.addEventListener('keydown',e=>{
  // Keyboard activation is also an opener (including shortcut keys).
  if(e.key!=='Escape'&&e.key!=='Tab')lastTarget=document.activeElement;
  sync();const top=layers.filter(l=>l.on).sort((a,b)=>b.rank-a.rank||b.order-a.order)[0];
  if(!top)return;
  if(e.key==='Escape'&&!e.isComposing){e.preventDefault();e.stopImmediatePropagation();onEscape?.();top.close();sync();return;}
  if(e.key==='Tab'&&top.trap){
   const box=top.element(),items=[...box.querySelectorAll(focusable)].filter(e=>visible(e)&&e.tabIndex>=0);
   if(!items.length)return;
   const at=items.indexOf(document.activeElement);
   if(at<0||(!e.shiftKey&&at===items.length-1)||(e.shiftKey&&at===0)){
    e.preventDefault();(e.shiftKey?items.at(-1):items[0]).focus();
   }
  }
 },true);
 new MutationObserver(sync).observe(document.body,{subtree:true,attributes:true,attributeFilter:['hidden','open','class','data-view','data-sheet'],childList:true});
}
