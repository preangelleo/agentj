// P0304: signed final-step repair, no optimistic success on reconnect, owner-only rollback.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';

const out=resolve(process.env.P0304_OUT || 'reports/qa/p0304');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const calls=[];const lightChat=new Map();
fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='rescue_do')return false;
  const valid=await fake.verifyControl(c,'rescue_do',m,{action:m.action});
  calls.push({action:m.action,valid});
  if(m.action==='deep_repair'){
    await send({t:'reset_step',r:m.r,flow:m.action,step:1,state:'run',text:'Checking installation'});
    await send({t:'reset_done',r:m.r,flow:m.action,ok:false,step:2,why:'failed',rollback:true,text:'Repair could not finish; check the host service.'});
  }else await send({t:'reset_done',r:m.r,flow:m.action,ok:true,step:1,text:'Agent J is back on the previous version.'});
  return true;
};
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();calls.length=0;fake.st.prefs={appearance:{language:lang,theme}};
  const p=await newPage(B,width,width===390?844:900,theme,{touch:true,allow:[web.url,fake.relay+'/']});
  const tag=`${lang}-${theme}-${width}`;
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,"document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.getElementById('setBtn').click()");
   await waitFor(p,"!document.getElementById('settings').hidden && !!document.getElementById('rs-deep-repair-go')");
   await evaluate(p,`document.querySelector('[data-set-theme=${JSON.stringify(theme)}]').click()`);
   assert.deepEqual(await evaluate(p,`({selected:document.querySelector('[data-set-theme=${JSON.stringify(theme)}]').getAttribute('aria-pressed'),stored:localStorage.getItem('aj.theme'),applied:document.documentElement.dataset.theme})`),{selected:'true',stored:theme,applied:theme});
   // Non-chat Settings is deliberately Noir in both modes; the chat state canvas does respond to theme.
   assert.equal(await evaluate(p,"getComputedStyle(document.getElementById('settings')).backgroundColor"),'rgb(44, 43, 38)');
   await evaluate(p,"document.getElementById('setBtn').click()");
   await waitFor(p,"document.getElementById('settings').hidden");
   assert.equal(await evaluate(p,"getComputedStyle(document.body).getPropertyValue('--deep').trim()"),theme==='light'?'0%':'28%');
   const chatPixel=await evaluate(p,"getComputedStyle(document.body).backgroundColor");
   assert.notEqual(chatPixel,'rgb(44, 43, 38)','chat is not the fixed Noir Settings canvas');
   const chatShot=await shoot(p,'');
   writeFileSync(join(out,`${tag}-chat.png`),chatShot);
   const pair=`${lang}-${width}`;
   if(theme==='light')lightChat.set(pair,{chatPixel,chatShot});
   else {assert.notEqual(chatPixel,lightChat.get(pair).chatPixel,'chat theme must change its rendered background');
     assert.notDeepEqual(chatShot,lightChat.get(pair).chatShot,'chat light/dark screenshots must differ');}
   await evaluate(p,"document.getElementById('setBtn').click()");
   await waitFor(p,"!document.getElementById('settings').hidden");
   assert.equal(await evaluate(p,"document.getElementById('rs-deep-repair-go').textContent"),lang==='zh'?'彻底修复':'Run full repair');
   assert.equal(await evaluate(p,"document.getElementById('rs-clear').compareDocumentPosition(document.getElementById('rs-deep-repair')) & Node.DOCUMENT_POSITION_FOLLOWING"),4,'final step follows existing repairs');
   assert.equal(await evaluate(p,"document.getElementById('rs-rollback').hidden"),true);
   await evaluate(p,"document.getElementById('rs-deep-repair').scrollIntoView({block:'center'})");
   await waitFor(p,"!document.getElementById('toast').classList.contains('on')");await sleep(150);
   writeFileSync(join(out,`${tag}-idle.png`),await shoot(p,''));
   await evaluate(p,"document.getElementById('rs-deep-repair-go').click()");await waitFor(p,"!document.getElementById('confirm').hidden");
   assert.deepEqual(calls,[],'confirmation must precede signature');
   await evaluate(p,"document.getElementById('confirm-no').click()");await sleep(100);assert.deepEqual(calls,[],'cancel sends nothing');
   await evaluate(p,"document.getElementById('rs-deep-repair-go').click()");await waitFor(p,"!document.getElementById('confirm').hidden");
   await evaluate(p,"document.getElementById('confirm-yes').click()");
   await waitFor(p,"document.getElementById('rs-deep-repair').dataset.state==='fail' && !!document.getElementById('rs-deep-rollback-go')");
   assert.deepEqual(calls,[{action:'deep_repair',valid:true}]);
   // The fake answers in English like an older host would: zh falls back to our own plain sentence, en shows the host's.
   const failure=lang==='zh'?'彻底修复没有完成，可以请 Agent J Father 远程帮忙。':'Repair could not finish; check the host service.';
   assert.equal(await evaluate(p,`document.getElementById('rs-deep-repair').textContent.includes(${JSON.stringify(failure)})`),true);
   assert.equal(await evaluate(p,"document.getElementById('rs-deep-repair').textContent.includes('Repair could not finish')"),lang==='en');
   assert.equal(await evaluate(p,"document.getElementById('rs-deep-rollback-go').getBoundingClientRect().height>=44"),true);
   writeFileSync(join(out,`${tag}-failed.png`),await shoot(p,''));
   await evaluate(p,"document.getElementById('rs-deep-rollback-go').click()");await waitFor(p,"!document.getElementById('confirm').hidden");
   assert.equal(calls.length,1,'rollback requires separate consent');
   await evaluate(p,"document.getElementById('confirm-yes').click()");await waitFor(p,"document.getElementById('rs-rollback').dataset.state==='ok'");
   assert.deepEqual(calls,[{action:'deep_repair',valid:true},{action:'rollback',valid:true}]);
   const restored=lang==='zh'?'已恢复到之前的版本。':'Agent J is back on the previous version.';
   assert.equal(await evaluate(p,`document.getElementById('rs-rollback').textContent.includes(${JSON.stringify(restored)})`),true);
   if(lang==='zh')assert.equal(await evaluate(p,"document.getElementById('rs-rollback').textContent.includes('Agent J is back')"),false);
   assert.equal(await evaluate(p,'document.documentElement.scrollWidth>innerWidth'),false);
   writeFileSync(join(out,`${tag}-rollback.png`),await shoot(p,''));
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
   console.log('PASS p0304 '+tag);
  }finally{await p.dispose();}
 }
}finally{await B.close();await fake.stop();await web.stop();}
