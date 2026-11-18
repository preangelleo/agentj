import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(process.env.P551_SOURCE || new URL('../public/js/relay.js',import.meta.url),'utf8');
const ctx=vm.createContext({chanCard:()=>null});
vm.runInContext(src.slice(src.indexOf('const hist ='),src.indexOf('const tailSeen')),ctx);
const run=code=>vm.runInContext(code,ctx);
const old='\u3014\u4e0d\u56de\u7fa4\u3015';
for(const marker of ['〔静默消息〕','[silent]',old]){
 for(const reply of [marker,` ${marker} `,`${marker} [silent]`])test('boundary '+JSON.stringify(reply),()=>assert.equal(run(`isSilent(${JSON.stringify(reply)})`),true));
 test('visible report around marker '+marker,()=>{
  for(const reply of [marker+'report','report'+marker]) {
   const row=run(`displayTurn({reply:${JSON.stringify(reply)}})`);
   assert.equal(row.silent,false);assert.equal(row.reply,'report');
  }
  assert.equal(run(`isSilent(${JSON.stringify('left '+marker+' right')})`),false);
  assert.equal(run(`displayTurn({reply:${JSON.stringify('left '+marker+' right')}}).reply`),'left  right');
 });
}
test('wire flag survives a cleaned reply',()=>assert.equal(run('silentTurn({silent:true,reply:""})'),true));
test('normalization keeps report visible while removing marker',()=>{
 assert.equal(run('displayTurn({reply:"〔静默消息〕body"}).silent'),false);
 assert.equal(run('displayTurn({reply:"〔静默消息〕body"}).reply'),'body');
});
