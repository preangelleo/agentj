import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
test('P417 secret-save completion refreshes once with only the public name and verification result',()=>{
 const src=read('../public/js/elevate.js');
 const part=src.slice(src.indexOf('export function done('),src.indexOf('/** The computer did not accept'));
 const calls=[];
 const ctx=vm.createContext({completed:new Set(),cards:new Map([['c1',{kind:'secret',name:'DASHSCOPE_API_KEY'}]]),
  shownId:null,busy:false,RESULT:{},secretSaved:(...args)=>calls.push(args),render(){},Date,clearTimeout});
 vm.runInContext(part.replace('export function','function'),ctx);
 vm.runInContext("done({id:'c1',result:'saved',verify:'fail'});done({id:'c1',result:'saved',verify:'fail'})",ctx);
 assert.deepEqual(calls,[['DASHSCOPE_API_KEY','fail']]);
});
test('P417 refreshed key state keeps validation and asks for a fresh metadata snapshot',()=>{
 const src=read('../public/js/keys.js');
 const part=src.slice(src.indexOf('export function saved('),src.indexOf('const chs ='));
 const checks=new Map();let loads=0;
 const ctx=vm.createContext({okName:n=>/^[A-Z_]+$/.test(n),verification:checks,checks:new Map(),CHECKABLE:new Set(),checkRow(){},root:{isConnected:true},load(){loads++;return Promise.resolve();}});
 vm.runInContext(part.replace('export function','function'),ctx);
 vm.runInContext("saved('DASHSCOPE_API_KEY','ok');saved('invalid name','ok')",ctx);
 assert.equal(loads,1);assert.equal(checks.get('DASHSCOPE_API_KEY'),'ok');
});
test('P417 Bailian rows explain plan/key source and keep variable names in Details',()=>{
 const src=read('../public/js/keys.js');
 assert.match(src,/mk\('details','keys-details'\)/);
 for(const lang of ['zh','en']){
  const d=JSON.parse(read(`../i18n/web.${lang}.json`));
  for(const k of ['tokenTitle','tokenHint','payTitle','payHint','apiHint','subHint','details','get']) assert.ok(d['p417.key.'+k]);
  assert.match(d['p352.needFaceId'],lang==='zh'?/账号登录不会自动开通/:/Account sign-in does not enable/);
 }
 assert.match(src,/cn-beijing\/model\/settings\/api-key/);
 assert.match(src,/link\.rel='noopener noreferrer'/);
 assert.doesNotMatch(src,/\bconsole\.(?:log|info|warn|error|debug|trace)\s*\(/);
});
