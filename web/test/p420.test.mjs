import {test} from 'node:test';
import assert from 'node:assert/strict';
import {generate,valid,oklch,fromOklch,contrast,DIRECTIONS} from '../public/js/palette-color.js';
test('P420 known sRGB conversions round-trip including neutrals',()=>{
  for(const hex of ['#000000','#ffffff','#ff0000','#00ff00','#0000ff','#827dbd','#141414','#faf9f5']){
    const {l,c,h}=oklch(hex);assert.equal(fromOklch(l,c,h),hex);
  }
  assert.ok(Math.abs(oklch('#ff0000').l-.627955)<.00001);
  assert.equal(contrast('#141414','#141414'),1);
  assert.ok(contrast('#141414','#faf9f5')>17);
});
test('P420 all directions/counts/sliders are deterministic bounded HEX, invalid inputs rejected',()=>{
  assert.deepEqual(generate('url(javascript:x)'),[]);
  const sets=new Set();
  for(const d of DIRECTIONS)for(const n of [5,8,10])for(const seed of ['#827dbd','#000000','#ffffff','#ff0000']){
    for(const h of [-180,0,180])for(const s of [0,100,200])for(const l of [-40,0,40]){
      const a=generate(seed,d,n,h,s,l);assert.equal(a.length,n);assert.equal(new Set(a).size,n,`${seed}/${d}/${n}/${h}/${s}/${l}`);assert.ok(a.every(valid));assert.deepEqual(a,generate(seed,d,n,h,s,l));
    }
    if(n===5&&seed==='#827dbd')sets.add(JSON.stringify(generate(seed,d,n)));
  }
  assert.equal(sets.size,5);
  assert.notDeepEqual(generate('#827dbd'),generate('#827dbd','analogous',5,60,80,10));
});
