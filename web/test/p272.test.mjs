import test from 'node:test';
import assert from 'node:assert/strict';
import {bindReaderSwipe} from '../public/js/reader-swipe.js';
function fixture(){
 const events={},steps=[];let selected=false,open=true;
 globalThis.innerWidth=390;globalThis.getSelection=()=>({isCollapsed:!selected});
 const node={addEventListener:(k,fn)=>events[k]=fn};bindReaderSwipe(node,{open:()=>open,step:n=>steps.push(n)});
 const e=(x=180,y=300,blocked=false,n=1)=>({target:{closest:()=>blocked},touches:Array.from({length:n},()=>({clientX:x,clientY:y})),changedTouches:[{clientX:x,clientY:y}],cancelable:true,preventDefault(){this.prevented=true;}});
 const start=(x=180,blocked=false)=>events.touchstart(e(x,300,blocked));
 const move=(x,y=300,n=1)=>{const z=e(x,y,false,n);events.touchmove(z);return z;};
 const end=(x,y=300)=>events.touchend(e(x,y,false,0));
 return {steps,start,move,end,events,e,select:()=>selected=true,close:()=>open=false};
}
test('reader: right is older, left is newer and horizontal motion alone owns preventDefault',()=>{
 const f=fixture();f.start();assert.equal(f.move(260).prevented,true);f.end(290);assert.deepEqual(f.steps,[-1]);
 f.start();f.move(100);f.end(70);assert.deepEqual(f.steps,[-1,1]);
});
test('reader: edge back, controls and native vertical scrolling never page',()=>{
 const f=fixture();f.start(10);f.move(120);f.end(140);f.start(180,true);f.move(280);f.end(300);
 f.start();assert.equal(f.move(190,360).prevented,undefined);f.end(290,370);assert.deepEqual(f.steps,[]);
});
test('reader: selection, pinch, cancellation and a closed reader never page',()=>{
 for(const kind of ['selection','pinch','cancel','close']){
  const f=fixture();f.start();f.move(230);
  if(kind==='selection'){f.select();f.move(260);}if(kind==='pinch')f.move(260,300,2);
  if(kind==='cancel')f.events.touchcancel();if(kind==='close')f.close();
  f.end(300);assert.deepEqual(f.steps,[],kind);
 }
});
