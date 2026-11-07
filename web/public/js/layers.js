import {focusDraft} from './composer-focus.js';
// P119: one keyboard owner for transient UI. Owners supply their existing close/back action;
// hiding a DOM node here would skip camera cleanup, confirmation resolution or reader history.
// P551: every confirmation card (approval sheet, confirm sheet, elevate / sign-in / secret cards) is drawn on the top tier
// (ontop.js) — so it is also the first layer Esc / Back reaches; among cards the latest one wins (open order).
export const TOP_RANK=1000;
const layers=[];
let serial=0,lastTarget=null,clickFocus;
const visible=e=>!!e&&e.isConnected&&e.getClientRects().length&&!e.closest('[hidden],.aj-layer-covered');
const focusable='button:not([disabled]),a[href],summary,input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]';
function restore(l){
 // A non-modal notice can disappear while the owner edits outside it. Keep that live focus.
 if(!l.trap&&l.preserveOutsideFocus&&visible(document.activeElement)&&!l.element()?.contains(document.activeElement)&&document.activeElement.matches?.(focusable))return;
 // P417 #188: this very click put the caret in the message box (≡ 全部命令 → a command): never pull it back to ≡
 const a=document.activeElement;if(clickFocus!==undefined&&a!==clickFocus&&/^(input|workflow-compose|fsText)$/.test(a?.id)&&visible(a)&&!l.element()?.contains(a))return;
 if(layers.some(other=>other!==l&&other.open()&&other.element()?.contains(document.activeElement)&&visible(document.activeElement)))return;
 const target=visible(l.opener)&&l.opener.matches?.(focusable)?l.opener:l.fallback?.();
 if(visible(target)&&target.matches?.(focusable))(/^(input|workflow-compose)$/.test(target.id) ? focusDraft(target,{preventScroll:true}) : target.focus({preventScroll:true}));
}
// Shared A/B popups use one existing modal tier. Opening a B card must not leave
// an older A card visually above it while Escape and Tab still target that A card.
const popup=l=>l.element()?.classList.contains('aj-shared-popup');
const priority=l=>popup(l)?Math.max(TOP_RANK,l.rank):l.rank;
const nativeModal=()=>[...document.querySelectorAll('dialog[open]')].filter(n=>{try{return n.matches(':modal');}catch{return false;}}).at(-1);
const nativeOwners=new WeakMap();
const ordered=()=>{const stack=layers.filter(l=>l.on),native=nativeModal();
 if(native){
  // Unregistered native editors keep their existing browser cancel/close lifecycle.
  if(!stack.some(l=>l.element()===native)){let owner=nativeOwners.get(native);if(!owner){owner={element:()=>native,trap:true,rank:0,order:0,close:()=>{if(native.dispatchEvent(new Event('cancel',{cancelable:true})))native.close();}};nativeOwners.set(native,owner);}stack.push(owner);}
 }
 const inside=l=>!!native&&(native===l.element()||native.contains(l.element()));
 return stack.sort((a,b)=>Number(inside(b))-Number(inside(a))||priority(b)-priority(a)||b.order-a.order);
};
const presentation=new WeakMap();
let frontPopup=null;
function sync(){
 for(const l of layers){const on=l.open(),card=l.element()?.dataset.card||'';
  if(on&&(!l.on||(card&&!/^[pq]\|/.test(card)&&card!==l.card))){l.order=++serial;l.opener=l.trigger?.() || (visible(lastTarget)&&!l.element()?.contains(lastTarget)?lastTarget:document.activeElement);
   if(l.element()?.contains(l.opener))l.opener=l.fallback?.();}
  if(!on&&l.on)restore(l);
  l.on=on;l.card=card;
 }
 const stack=ordered(),top=stack[0],front=top&&popup(top)?top:null;
 // A rescue sheet has two registered owners. Coverage/inert belong to its DOM
 // node, not to either registration; capture the original value only once.
 const nodes=new Set(layers.filter(popup).map(l=>l.element()));
 for(const node of nodes){
  const owner=stack.find(l=>l.element()===node),covered=!!owner&&node!==front?.element()&&!node.contains(front?.element());
  let saved=presentation.get(node);
  if(node.classList.contains('aj-layer-covered')!==covered)node.classList.toggle('aj-layer-covered',covered);
  // Keep a native modal ancestor visible and interactive when ontop moves A into it.
  if(covered&&!saved){saved={inert:node.inert};presentation.set(node,saved);node.inert=true;}
  else if(!covered&&saved){node.inert=saved.inert;presentation.delete(node);}
  if(owner&&node.style.getPropertyValue('--popup-layer-order')!==String(owner.order))node.style.setProperty('--popup-layer-order',String(owner.order));
 }
 if(front&&(front!==frontPopup||(front.trap&&!front.element().contains(document.activeElement)))){
  const box=front.element();if(!box.contains(document.activeElement)){
   const target=[...box.querySelectorAll(focusable)].find(e=>visible(e)&&e.tabIndex>=0);
   target?.focus({preventScroll:true});
  }
 }
 frontPopup=front;
}
export function registerLayer({element,open,close,fallback,trigger,trap=true,rank=0,preserveOutsideFocus=false}){
 const l={element,open,close,fallback,trigger,trap,rank,preserveOutsideFocus,on:false};
 // One-shot native editors release their closures after closing. Existing persistent layers need no change.
 l.dispose=()=>{const at=layers.indexOf(l);if(at>=0)layers.splice(at,1);};
 layers.push(l);sync();return l;
}
export function initLayers({onEscape}={}){
 document.addEventListener('click',e=>{lastTarget=e.target.closest?.(focusable)||document.activeElement;clickFocus=document.activeElement;setTimeout(()=>{clickFocus=undefined;});},true);
 document.addEventListener('pointerdown',e=>{lastTarget=e.target.closest?.(focusable)||document.activeElement;},true);
 document.addEventListener('keydown',e=>{
  // Keyboard activation is also an opener (including shortcut keys).
  if(e.key!=='Escape'&&e.key!=='Tab')lastTarget=document.activeElement;
  sync();const top=ordered()[0];
  if(!top)return;
  // Native showModal() is above ordinary page layers and owns its cancel/focus trap.
  const native=document.activeElement?.closest?.('dialog[open]');
  if(native&&!native.contains(top.element())&&!top.element()?.contains(native))return;
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
 new MutationObserver(sync).observe(document.body,{subtree:true,attributes:true,attributeFilter:['hidden','open','class','data-view','data-sheet','data-card'],childList:true});
}

// P387: remote Back uses the very same top-layer owner and close action as Escape.
export function activeLayer(){sync();return ordered()[0]||null;}
