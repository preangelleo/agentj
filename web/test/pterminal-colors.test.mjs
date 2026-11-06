import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/termpty.js',import.meta.url),'utf8');
function fn(name, context={}) {
 const start=src.indexOf(`export function ${name}(`);
 assert.ok(start>=0,name);
 const body=src.slice(start+7,src.indexOf('\n}\n',start)+2);
 const box={...context}; vm.runInNewContext(body+`\nglobalThis.fn=${name}`,box);return box.fn;
}
test('only bounded complete terminal protocol responses take the reply channel',()=>{
 const valid=fn('isPtyReply');
 for(const s of ['\x1b]10;rgb:fafa/f9f9/f5f5\x1b\\','\x1b]11;rgb:2c2c/2b2b/2626\x07','\x1b]12;rgb:71/bb/de\x1b\\','\x1b]4;255;rgb:ff/ee/dd\x1b\\','\x1b[12;34R','\x1b[?12;34R','\x1b[0n'])assert.equal(valid(s),true,JSON.stringify(s));
 for(const s of ['hi','\x1b]11;?\x07','\x1b]52;c;evil\x07','\x1b]4;256;rgb:ff/ee/dd\x07','\x1b[0;1R','\x1b[10000;1R','\x1b]11;rgb:fffff/00/00\x07','\x1b[1;1Rwhoami\r','\x1b[1;1R\x1b[2;2R','\x1b[1n','\x1b[0nwhoami\r','\x1b[0N','\x1b[1;1r'])assert.equal(valid(s),false,JSON.stringify(s));
});
test('native terminal replies preserve sticky modifiers; owner keys still consume them',()=>{
 const isPtyReply=fn('isPtyReply'), withMods=fn('withMods');
 const route=fn('routePtyData',{isPtyReply,withMods});let takes=0;const out=[];
 const cb={data:d=>out.push(['data',d]),reply:d=>out.push(['reply',d])};
 const take=()=>{takes++;return{ctrl:true}};
 route('\x1b]11;rgb:2c2c/2b2b/2626\x1b\\',cb,take);
 assert.equal(takes,0);assert.equal(out[0][0],'reply');
 route('c',cb,take);assert.equal(takes,1);assert.equal(out[1][1],'\x03');
});
test('theme resolves explicit choice before operating system preference',()=>{
 const choice=fn('terminalScheme');
 assert.equal(choice('light',true),'light');assert.equal(choice('dark',false),'dark');
 assert.equal(choice(null,true),'dark');assert.equal(choice('system',false),'light');
});
test('page response transport is immediate, sequenced, bounded, and never retained behind a sheet or disconnect',()=>{
 const wire=readFileSync(new URL('../public/js/terminal.js',import.meta.url),'utf8');
 const start=wire.indexOf('function ptyReply('), body=wire.slice(start,wire.indexOf('\n}\n',start)+2);
 const sent=[], S={phase:'ready',pty:true,id:'fixture',inSeq:5,budget:65536,budgetAt:1000,inQ:[]};
 const box={S,sheet:null,PTY_RATE:65536,Date:{now:()=>1000},utf8Len:s=>Buffer.byteLength(s),send:m=>sent.push(m)};
 vm.runInNewContext(body+'\nglobalThis.reply=ptyReply',box);
 box.reply('\x1b[1;1R');assert.equal(sent.length,1);assert.equal(sent[0].reply,true);assert.equal(sent[0].seq,6);assert.equal(S.inQ.length,0);
 for(const patch of [{sheet:{}},{phase:'reconnecting'},{pty:false},{budget:0}]){
  Object.assign(S,{phase:'ready',pty:true,budget:65536});box.sheet=null;
  if('sheet'in patch)box.sheet=patch.sheet;else Object.assign(S,patch);
  box.reply('\x1b[1;1R');assert.equal(sent.length,1,JSON.stringify(patch));assert.equal(S.inQ.length,0);
 }
});
