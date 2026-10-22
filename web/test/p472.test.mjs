import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
const rule=s=>s.match(/html\[data-ios="1"\]::before\{([^}]+)\}/)?.[1];
test('P472 one state-independent root layer owns iPhone status pixels',()=>{
 const r=rule(css);assert.ok(r);assert.match(r,/background:#2c2b26/);assert.match(r,/transition:none!important/);
 assert.match(r,/top:var\(--vvtop,0px\)/);assert.match(r,/height:var\(--ios-safe-top\)/);
 assert.doesNotMatch(r,/--(?:pg|state|c-noir|ios-chrome|ios-bottom)/);
 assert.match(css,/html\[data-ios="1"\] \.satband\{display:none\}/);
 const baseline=readFileSync(new URL('./fixtures/p472-before.css',import.meta.url),'utf8');
 assert.equal(rule(baseline),undefined,'negative control: P469 lacks independently owned root status layer');
});
