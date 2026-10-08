import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as snap from '../public/js/snap.js';
import {readFileSync} from 'node:fs';
test('P78 rejected model remains disabled in phone catalog and controls',()=>{
 snap.configure({onChange(){},onTurn(){}});
 snap.setModels({models:[{id:'gpt-6.1-sol',name:'Sol',disabled:true},{id:'gpt-6',name:'GPT-6'}],default:{model:'gpt-6'}});
 assert.equal(snap.snapshot().switch.models[0].disabled,true);
 assert.equal(snap.snapshot().switch.models[1].disabled,false);
 const source=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
 assert.ok(source.includes('b.disabled = x.disabled === true'));
 assert.ok(source.includes('c.models.filter(x => !x.disabled)'));
 assert.ok(source.includes('raw.textContent = detail[3]'));
 const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
 assert.ok(css.includes('.cmdmodel:disabled{opacity:.4'));
});
