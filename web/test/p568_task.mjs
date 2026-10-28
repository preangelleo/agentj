// P568 A: replay the old denial, then the delegated task's real signed-card wire format.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out='reports/qa/p568/appendix-a/screens';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {for(const lang of ['zh','en'])for(const theme of ['light','dark']) {
 fake.reset();fake.setUp(true);
 const p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
 try {
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}';true`);
  await fake.addTurn({k:'sys',text:''},lang==='zh'?'后台任务无法执行：rejected by user。':'Background task failed: rejected by user.');
  await waitFor(p,`document.body.innerText.includes('rejected by user')`);
  writeFileSync(`${out}/${lang}-${theme}-before.png`,await shoot(p,''));
  const id='568a'.padEnd(32,'0');
  await fake.ask({id,tool:'Bash',summary:'git push origin main',task:lang==='zh'?'每日评论草稿':'Daily comment draft',ttl:120,confirm:'hold',passkey_required:false});
  await waitFor(p,`document.body.dataset.sheet==='1'`);
  assert.equal(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth+1`),true);
  writeFileSync(`${out}/${lang}-${theme}-after-card.png`,await shoot(p,''));
  await fake.askDone(id,'gone');
  await evaluate(p,`document.getElementById('sheetClose').click()`);
  await waitFor(p,`document.body.dataset.sheet!=='1'`);
  const reason=lang==='zh'?'未核验来源：不能请求主人批准，没有发卡片；不是主人手动拒绝。':'Unverified source: cannot request owner approval; no card was sent. The owner did not reject this action.';
  await fake.addTurn({k:'sys',text:''},reason);
  await waitFor(p,`document.body.innerText.includes(${JSON.stringify(reason)})`);
  writeFileSync(`${out}/${lang}-${theme}-after-reason.png`,await shoot(p,''));
  assert.deepEqual(p.problems,[]);
 }finally{await p.dispose();}
}}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(out+'/results.json',JSON.stringify({pass:true,cases:4,kind:'paired fake-host replay; old denial is reconstructed, not a customer screenshot'})+'\n');
console.log('P568 A phone PASS: 4 language/theme cases');
