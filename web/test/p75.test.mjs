import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
function fixture(batches){
 const ctx=vm.createContext({C:{api:{history:async()=>batches.shift()??null}},showPage(){},renderSilent(){},announceTurn(){},el:()=>({hidden:true})});
 vm.runInContext(src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replace('export function','function').replace('export function','function'),ctx);
 const run=x=>vm.runInContext(x,ctx);
 run(`hist.turns=[{id:101,reply:'last'}];hist.firstId=1;hist.count=101;hist.lastId=101;hist.epoch=0;pageAt=0;follow=false`);
 run.ctx=ctx;return run;
}
test('P75 older pagination crosses silent-only batches and preserves visible cursor',async()=>{
 const run=fixture([{epoch:0,first_id:1,turns:[{id:70,reply:'〔不回群〕'}]},{epoch:0,first_id:1,turns:[{id:1,reply:'first'}]}]);
 assert.equal(await run('loadOlder()'),true);
 assert.equal(run('pageAt'),1);assert.equal(run('pages().length'),2);assert.equal(run('pages()[pageAt].reply'),'last');
});
test('P75 panel load-earlier stops on a silent row, even after a batch of normal replies',async()=>{
 const run=fixture([{epoch:0,first_id:1,turns:[{id:50,reply:'middle'}]},{epoch:0,first_id:1,turns:[{id:1,reply:'〔不回群〕'}]}]);
 vm.runInContext(src.slice(src.indexOf('async function moreSilent()'),src.indexOf('\nconst dialogOpen')),run.ctx);
 run('var silentBusy=false');await run('moreSilent()');
 assert.equal(run('hist.turns.filter(t=>isSilent(t.reply)).length'),1);assert.equal(run('silentBusy'),false);
});
test('P75 stale old epoch cannot repopulate a cleared session',async()=>{
 const run=fixture([{epoch:1,first_id:1,turns:[{id:1,reply:'〔不回群〕'}]}]);
 assert.equal(await run('loadOlderBatch()'),null);assert.equal(run('hist.turns.length'),1);
 run('resetHistory(2)');assert.equal(run('hist.count'),0);assert.equal(run('hist.turns.length'),0);
});
