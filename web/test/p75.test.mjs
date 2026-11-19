import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as activity from '../public/js/phone-activity.js';
import * as turnsplit from '../public/js/turnsplit.js';   // 0.26.9 #471: relay.js pages() lays out continuation cards (its import, given to the vm)
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
function fixture(batches){
 const ctx=vm.createContext({...turnsplit,...activity,chanCard:()=>null,historyGeneration:0,acceptHistoryHead(){},C:{api:{history:async()=>batches.shift()??null}},showPage(){},renderSilent(){},announceTurn(){},el:()=>({hidden:true}),search:{onEpoch(){}}});   // P188: resetHistory empties the search cache
 vm.runInContext(src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replaceAll('export function','function'),ctx);
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
 run('var silentBusy=false, activityMode=false');await run('moreSilent()');
 assert.equal(run('hist.turns.filter(t=>silentTurn(t)).length'),1);assert.equal(run('silentBusy'),false);
});
test('P75 stale old epoch cannot repopulate a cleared session',async()=>{
 const run=fixture([{epoch:1,first_id:1,turns:[{id:1,reply:'〔不回群〕'}]}]);
 assert.equal(await run('loadOlderBatch()'),null);assert.equal(run('hist.turns.length'),1);
 run('resetHistory(2)');assert.equal(run('hist.count'),0);assert.equal(run('hist.turns.length'),0);
});
test('0340 previous crosses a whole batch of hidden background activity and preserves the rendered cursor',async()=>{
 const activity={id:70,source:{k:'agent',via:'background_done'},reply:'completed'};
 const run=fixture([{epoch:0,first_id:1,turns:[activity]},{epoch:0,first_id:1,turns:[{id:1,reply:'yesterday'}]}]);
 await run('loadOlder()');
 assert.equal(run('pages()[0].reply'),'yesterday');
 assert.equal(run('pages()[pageAt].reply'),'last');
});
