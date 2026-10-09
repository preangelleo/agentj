import {test} from 'node:test';
import assert from 'node:assert/strict';
import {historyDoubleTap} from '../public/js/history-corners.js';
function fixture(){
 const handlers={},node={addEventListener(k,f){(handlers[k]??=[]).push(f);}};
 let t=1000,jumps=0;
 historyDoubleTap(node,()=>jumps++,()=>t);
 const fire=(type,patch={})=>{let prevented=false;const e={isPrimary:true,pointerType:'touch',pointerId:1,clientX:10,clientY:10,preventDefault(){prevented=true;},...patch};for(const f of handlers[type]??[])f(e);return prevented;};
 const tap=(patch={},duration=50)=>{fire('pointerdown',patch);t+=duration;fire('pointerup',patch);};
 return {fire,tap,advance:n=>t+=n,jumps:()=>jumps};
}
test('P102 touch and pen need two close short taps; single click is inert',()=>{
 for(const pointerType of ['touch','pen']){
  const f=fixture();f.fire('click');f.tap({pointerType});assert.equal(f.jumps(),0);
  f.fire('pointerleave');f.advance(80);f.tap({pointerType});assert.equal(f.jumps(),1);
  f.fire('dblclick');assert.equal(f.jumps(),1,'compatibility dblclick cannot jump twice');
  f.advance(800);f.fire('dblclick');assert.equal(f.jumps(),2,'desktop dblclick remains available');
 }
});
test('P102 movement, long hold, cancel, leave, extra finger and separated taps cannot jump',()=>{
 for(const invalid of ['move','hold','pointercancel','pointerleave','second','late','far']){
  const f=fixture();f.tap();f.advance(50);
  if(invalid==='move'){f.fire('pointerdown');f.fire('pointerup',{clientX:40});}
  if(invalid==='hold')f.tap({},400);
  if(invalid==='pointercancel'||invalid==='pointerleave'){f.fire('pointerdown');f.fire(invalid);}
  if(invalid==='second')f.fire('pointerdown',{isPrimary:false,pointerId:2});
  if(invalid==='late'){f.advance(400);f.tap();}
  if(invalid==='far')f.tap({clientX:80});
  assert.equal(f.jumps(),0,invalid);
 }
});
test('P102 mouse double click prevents selection and keyboard provides accessible activation',()=>{
 const f=fixture();f.tap({pointerType:'mouse'});assert.equal(f.jumps(),0);
 assert.equal(f.fire('mousedown',{detail:2}),true);
 assert.equal(f.fire('dblclick'),true);assert.equal(f.jumps(),1);
 for(const key of ['Enter',' '])assert.equal(f.fire('keydown',{key}),true);
 assert.equal(f.jumps(),3);f.fire('keydown',{key:'Enter',repeat:true});assert.equal(f.jumps(),3);
 f.tap();f.fire('blur');f.tap();assert.equal(f.jumps(),3);
});
