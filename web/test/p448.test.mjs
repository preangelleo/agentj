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
test('P458 only approval interaction colours survive the P448 rollback',()=>{
 const css=readFileSync(new URL('../public/controls.css',import.meta.url),'utf8');
 assert.ok(contrast('#2c2b26','#faf9f5')>=4.5);
 assert.match(css,/:is\(#elev-allow,\.apprallow\):is\(:hover,:active,:focus\)/);
 assert.match(css,/user-select:none/);assert.match(css,/-webkit-touch-callout:none/);
 assert.doesNotMatch(css,/control-primary|control-hover|:disabled|transition-property/);
});
