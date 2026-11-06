// #536 real client + encrypted fake host. Synthetic data only; no installed credentials.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,key} from './browser.mjs';
const out=process.env.P536_OUT||'reports/qa/p536'; mkdirSync(out,{recursive:true});
const before=process.env.P536_BEFORE==='1';
const web=await startWebServer(process.env.P536_PUBLIC?{publicDir:resolve(process.env.P536_PUBLIC)}:{}), fake=await startFakeHost(), B=await launch();
const results=[];
try {
 for(const width of [390,1440]) for(const theme of ['dark','light']) for(const lang of ['zh','en']) {
  fake.reset();fake.setUp(true); const seen=[];
  const original={claude:{active:'11111111',items:[{id:'11111111',name:'主号',method:'token',quota:null},{id:'22222222',name:'示例二',method:'login',quota:null}]},codex:{active:'33333333',items:[{id:'33333333',name:'示例三',method:'device',quota:null}]},conflict:[],primary:{claude:{state:'in',use:'key',use_name:'ANTHROPIC_API_KEY'},codex:{state:'in',use:'login'}}};
  let view=structuredClone(original);
  fake.st.onApp=async(c,m,send)=>{
   if(m.t.startsWith('acct_')) {seen.push(m); if(m.t==='acct_use') view[m.harness].active=m.id; await send({t:'acct_res',r:m.r,ok:true,view});return true;}
   if(m.t==='auth_get') {await send({t:'auth_res',r:m.r,ok:true,view:{dot:false,flow:null,harnesses:[]}});return true;}
   return false;
  };
  const p=await newPage(B,width,width===390?844:1000,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  const tag=`${lang}-${theme}-${width}`; const save=async(name)=>{if(lang==='zh'){const f=resolve(out,`536-${before?'改前':'改后'}-最终-${width===390?'手机':'电脑'}-系统${theme==='dark'?'深色':'浅色'}-${name}.png`);writeFileSync(f,await shoot(p,''));}};
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}';document.getElementById('setBtn').click()`);
   await waitFor(p,"!document.getElementById('settings').hidden");
   await evaluate(p,"document.getElementById('set-accts-nav').scrollIntoView({block:'center'})");await save('设置入口');
   if(!before) assert.match(await evaluate(p,"document.getElementById('set-accts-nav').textContent"),lang==='zh'?/备用 AI 账号/:/Backup AI accounts/);
   await evaluate(p,"document.getElementById('set-accts-nav').click()");
   await waitFor(p,"document.querySelectorAll('#acct-list .acctrow').length===3");
   await save('账号页面');
   const paint=await evaluate(p,`({theme:document.documentElement.dataset.theme,bg:getComputedStyle(document.getElementById('settings')).backgroundColor,fg:getComputedStyle(document.getElementById('settings')).color})`);
   await evaluate(p,"document.querySelector('#acct-list [data-kind=claude] .acctadd').scrollIntoView({block:'center'})");await save('备用管理');
   await evaluate(p,"document.getElementById('set-accts').scrollTop=0");
   if(!before){
    const primary=await evaluate(p,"document.getElementById('acct-primary').textContent");assert.match(primary,/ANTHROPIC_API_KEY/);assert.match(primary,lang==='zh'?/主账号/:/Primary account/);
    assert.equal(await evaluate(p,"document.querySelectorAll('#acct-primary input,#acct-primary textarea,#acct-primary .acctadd,#acct-primary .acctren,#acct-primary .acctdel').length"),0);
    assert.deepEqual(await evaluate(p,"[...document.querySelectorAll('#acct-list .acctname')].map(x=>x.textContent)"),original.claude.items.concat(original.codex.items).map(x=>x.name));
    await evaluate(p,"document.querySelector('#acct-list [data-kind=claude] .acctadd').click()");await waitFor(p,"!!document.querySelector('#acct-list .acctform input')");await key(p,'Escape');await waitFor(p,"!document.querySelector('#acct-list .acctform input')");
    await evaluate(p,"document.querySelector('#acct-list .acctuse').click()");await waitFor(p,"document.querySelector('#acct-list [data-id=\"22222222\"]')?.dataset.active==='1'");
    assert.equal(seen.find(m=>m.t==='acct_use')?.id,'22222222');assert.deepEqual(view.primary,original.primary,'backup switching does not invent a primary identity');
    const target=await evaluate(p,"document.querySelector('.acctprimaryedit').getBoundingClientRect().height");assert.ok(target>=44,'primary edit link is a touch target');
    delete view.primary;
    await evaluate(p,"import('./js/accounts.js').then(m=>m.load())");
    await waitFor(p,"document.querySelectorAll('#acct-primary .acctprimary').length===2");
    assert.match(await evaluate(p,"document.getElementById('acct-primary').textContent"),lang==='zh'?/暂时无法读取主账号/:/Primary account is unavailable/);
    assert.doesNotMatch(await evaluate(p,"document.getElementById('acct-primary').textContent"),/主号|ANTHROPIC_API_KEY/,'older hosts cannot turn a stored slot into the primary');
    await evaluate(p,"document.querySelector('.acctprimaryedit').click()");await waitFor(p,"document.body.dataset.view==='models'");
    assert.equal(await evaluate(p,"document.getElementById('settings').hidden"),true,'edit link follows the normal settings→models route');
    assert.ok(seen.every(m=>['acct_get','acct_use'].includes(m.t)), 'read-only primary never sends account writes');
   }
   assert.equal(await evaluate(p,"document.documentElement.scrollWidth>innerWidth"),false);
   results.push({tag,ok:true,paint});
  } finally {await p.close();}
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(resolve(out,`536-${before?'before':'after'}-checks.json`),JSON.stringify(results,null,2)+'\n'); console.log(JSON.stringify(results));
