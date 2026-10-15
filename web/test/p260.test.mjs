import test from 'node:test';
import assert from 'node:assert/strict';
import {fileDoubleTap, DOUBLE_MS, TAP_MS} from '../public/js/file-gestures.js';
function fixture() {
 const handlers={}, card={addEventListener:(k,f)=> (handlers[k] ||= []).push(f)};
 let t=1000, opened=0;
 fileDoubleTap(card,()=>opened++,()=>t);
 const fire=(k,extra={})=>{const e={pointerType:'touch',isPrimary:true,pointerId:1,clientX:20,clientY:20,target:{closest:()=>false},preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},stopImmediatePropagation(){this.stopped=true;},...extra}; for(const f of handlers[k]||[])f(e);return e;};
 const tap=(duration=40)=>{fire('pointerdown');t+=duration;return fire('pointerup');};
 return {fire,tap,advance:n=>t+=n,opened:()=>opened};
}
test('file pair owns propagation; compatibility dblclick and click cannot reopen or pause',()=>{
 const f=fixture(); f.tap(); f.advance(80);const e=f.tap();assert.ok(e.stopped&&e.prevented);assert.equal(f.opened(),1);
 f.fire('dblclick');assert.equal(f.opened(),1);assert.ok(f.fire('click').stopped);
 f.advance(701);f.fire('dblclick');assert.equal(f.opened(),2);
});
test('holds, slow pairs, movement, cancelled and multiple pointers do not open a file',()=>{
 for(const scenario of ['hold','slow','move','cancel','multi','button']) {
  const f=fixture(); f.tap();
  if(scenario==='hold')f.tap(TAP_MS+1);
  if(scenario==='slow'){f.advance(DOUBLE_MS+1);f.tap();}
  if(scenario==='move'){f.fire('pointerdown');f.fire('pointerup',{clientX:60});}
  if(scenario==='cancel'){f.fire('pointerdown');f.fire('pointercancel');f.fire('pointerup');}
  if(scenario==='multi'){f.fire('pointerdown',{isPrimary:false});f.fire('pointerup');}
  if(scenario==='button'){f.fire('pointerdown',{target:{closest:()=>true}});f.fire('pointerup');f.fire('dblclick',{target:{closest:()=>true}});}
  assert.equal(f.opened(),0,scenario);
 }
});
