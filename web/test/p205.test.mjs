// P205: the working line speaks to the owner (2026-10-13: 「董事长，您的最新指示已经收到，小助理正在处理，请稍后」, written
// 请稍候) and uses the host's `owner.address` when set — text only, ≤ 16 characters, "" = 董事长 / Chairman.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const read=(p)=>readFileSync(new URL(p,import.meta.url),'utf8');
const src=read('../public/js/relay.js');
const words=src.slice(src.indexOf('const NO_ECHO_MS'),src.indexOf('function fill('));
const zh=JSON.parse(read('../i18n/web.zh.json')).r, en=JSON.parse(read('../i18n/web.en.json')).r;
const zhSrc=JSON.parse(read('../i18n/web.zh.src.json')).r, ov=JSON.parse(read('../i18n/web.zh.override.json'));

test('P205 dictionaries: the working line and the default address', ()=>{
  assert.equal(zh.replyWorking,'{address}，您的最新指示已经收到，小助理正在处理，请稍候。');
  assert.equal(en.replyWorking,'{address}, your latest instruction is in — your assistant is on it.');
  assert.equal(zh.ownerAddress,'董事长'); assert.equal(en.ownerAddress,'Chairman');
  assert.equal(zhSrc.replyWorking,zh.replyWorking); assert.equal(ov['r.replyWorking'],zh.replyWorking, 'the human fix wins over the polish');
  // only the "working" moment changed: delivered-not-yet-taken still says so (the Agent J host has not picked it up)
  assert.equal(zh.replyPending,'送到了，等Agent J 主机接手…'); assert.equal(en.replyPending,'Delivered — waiting for the Agent J host to pick it up…');   // P280 wording
});

function ctx(){
  const DICT={zh:{'r.replyWorking':zh.replyWorking,'r.ownerAddress':zh.ownerAddress,'r.replyPending':zh.replyPending}};
  const t=(k,v)=>String(DICT.zh[k]??k).replace(/\{(\w+)\}/g,(m,n)=>v&&n in v?String(v[n]):m);
  const painted=[], c=vm.createContext({setTimeout:()=>0,clearTimeout(){},Date,historySync:false,shownKey:'1|x',wordsSrc:null,
    cur:{status:'working',hostStatus:'working'},t,el:()=>({dataset:{}}),fill:(_,x,plain)=>painted.push([x,plain]),afterWords(){},rdFollow(){}});
  vm.runInContext(words,c);
  return {c,painted,show:()=>vm.runInContext(`renderWords('',true)`,c)};
}

test('P205 the open page while working: default address, the owner\'s address, plain text', ()=>{
  const {c,painted,show}=ctx();
  show(); assert.deepEqual(painted.at(-1),['董事长，您的最新指示已经收到，小助理正在处理，请稍候。',true]);
  vm.runInContext(`ownerAddr=cleanAddress('老王')`,c); show();
  assert.equal(painted.at(-1)[0],'老王，您的最新指示已经收到，小助理正在处理，请稍候。','a new address redraws the same page');
  vm.runInContext(`cur={status:'idle',hostStatus:'idle'}`,c); show();
  assert.equal(painted.at(-1)[0],'送到了，等Agent J 主机接手…');   // P280 wording
});

test('P205 cleanAddress: text only, control characters out, ≤ 16 characters, junk → ""', ()=>{
  const {c}=ctx(); const clean=(v)=>vm.runInContext('cleanAddress',c)(v);
  assert.equal(clean('  老王 '),'老王');
  assert.equal(clean('王\n总\u0007'),'王 总');
  assert.equal(clean('x'.repeat(40)),'x'.repeat(16));
  assert.equal(clean('😀'.repeat(20)),'😀'.repeat(16),'cut by characters, never half a surrogate pair');
  for(const v of [undefined,null,3,{},['a'],'', '   ']) assert.equal(clean(v),'');
  assert.match(src,/ownerAddr=cleanAddress\(userPreferences\.owner\?\.address\)/,'applyPreferences takes it from the host preferences');
  assert.match(src,/fill\(w, pending && !tx \? \(label \|\| waitText\(wait\)\) : tx, pending && !tx, sys\)/,'wait lines are drawn as plain text');
});

test('P205 a wait line breaks at its commas on a narrow phone', ()=>{
  assert.match(read('../public/app.css'),/\.words\.empty p\{[^}]*word-break:keep-all;text-wrap:balance/);
});
