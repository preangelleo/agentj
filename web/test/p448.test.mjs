import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {controlTarget,initControlGestures,CONTROL} from '../public/js/control-gestures.js';
import {contrast} from '../public/js/palette-color.js';
test('P448 gesture guard is delegated, scoped, cancellable and leaves taps/scroll/typing alone',()=>{
 const listeners={};initControlGestures({addEventListener(type,fn,opts){listeners[type]=fn;assert.equal(opts.capture,true);assert.equal(opts.passive,false)}});
 assert.deepEqual(Object.keys(listeners),['selectstart','contextmenu']);
 for(const editable of [true,false])for(const control of [true,false]){
  const n={closest:s=>s===CONTROL?(control?{}:null):(editable?{}:null)};
  assert.equal(!!controlTarget(n),!editable&&control);
  for(const fn of Object.values(listeners)){let prevented=false;fn({target:n,cancelable:true,preventDefault(){prevented=true}});assert.equal(prevented,!editable&&control)}
 }
 assert.equal(controlTarget(null),null);
});
test('P448 state pairs use only approved palette entries and pass strict 4.5:1',()=>{
 // Leo palette v2: Sage Sensation, Sooty, Noir, Lotion (2026-10-12).
 const approved=new Set(['#b7e396','#141414','#2c2b26','#faf9f5']);
 for(const [bg,fg] of [['#b7e396','#141414'],['#2c2b26','#faf9f5']]){assert.ok(approved.has(bg)&&approved.has(fg));assert.ok(contrast(bg,fg)>=4.5)}
 const css=readFileSync(new URL('../public/controls.css',import.meta.url),'utf8');
 for(const hex of css.match(/#[0-9a-f]{6}/gi)||[])assert.ok(approved.has(hex.toLowerCase()),hex);
 assert.match(css,/user-select:none/);assert.match(css,/-webkit-touch-callout:none/);
});
