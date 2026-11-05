import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
function fixture(){
 const ctx=vm.createContext({C:{api:{},myDev:()=> 'mine'},showPage(){},announceTurn(){},el:()=>({hidden:true})});
 vm.runInContext(src.slice(src.indexOf('const hist ='),src.indexOf('function hhmm')).replaceAll('export function','function'),ctx);
 return x=>vm.runInContext(x,ctx);
}
test('P87 empty agent turns do not add pages, human input retains no-reply state',()=>{
 const run=fixture();
 run(`hist.turns=[{id:1,reply:'useful',end:'done'},{id:2,reply:'',source:{k:'agent'},end:'done'},{id:3,reply:'  ',source:{k:'host',text:'human'},end:'done'}]`);
 assert.equal(run('pages().length'),2);
 assert.equal(run('newTurnArrived(pages())'),false);
 run(`hist.turns.push({id:4,reply:'',source:{k:'agent'},end:'done'});pagesCache=null`);
 assert.equal(run('newTurnArrived(pages())'),false);
});
test('P87 empty pending input cannot trigger auto focus; visible reply can',()=>{
 const run=fixture();run(`hist.turns=[{id:1,reply:'useful',end:'done'}];newTurnArrived(pages())`);
 run(`hist.turns.push({id:2,reply:'',source:{k:'phone',dev:'mine',text:'question'},end:'open'});pagesCache=null`);
 assert.equal(run('newTurnArrived(pages())'),false);
 run(`hist.turns[1].reply='answer';pagesCache=null`);
 assert.equal(run('newTurnArrived(pages())'),true);
});

test('P87 phone WAV limit covers its ten-minute recorder',async()=>{
 const {ASR_MAX_BYTES}=await import('../public/js/blobs.js');
 const {ASR_MAX}=await import('../../protocol/wire.js');
 assert.equal(ASR_MAX_BYTES,ASR_MAX);
 assert.ok(ASR_MAX_BYTES>=44+600*16000*2);
});
