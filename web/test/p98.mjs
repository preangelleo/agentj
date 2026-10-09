// P98 encrypted fake-host browser acceptance; isolated Chromium, local services only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p98/web/',import.meta.url);mkdirSync(out,{recursive:true});
const results={},web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 for (const language of ['zh','en']) {
  const p=await newPage(B,360,780,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.addTurn({k:'host',text:'P98 shared session'},'P98 test reply');
  await waitFor(p,`document.getElementById('words').textContent.includes('P98 test reply')`);
  // A configured Codex pin is not yet a successfully followed session.
  await fake.send({t:'meter',shared_status:null,shared_follow:{agent:'codex',id:'d'.repeat(32)}});
  await sleep(150);
  assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),false);
  // Rapid selection replaces only the old informational toast, with a fresh deadline.
  await fake.send({t:'meter',shared_status:'following',shared_follow:{agent:'codex',id:'a'.repeat(32)}});
  await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
  await sleep(1900);
  await fake.send({t:'meter',shared_status:null,shared_follow:{agent:'claude',id:'b'.repeat(32)}});
  await waitFor(p,`document.getElementById('toast').textContent.includes('Claude Code')`);
  await sleep(1800);
  assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),true);
  await waitFor(p,`!document.getElementById('toast').classList.contains('on')`);
  // Actionable occupancy immediately clears an in-flight information toast.
  const occupying={agent:'codex',id:'c'.repeat(32)};
  await fake.send({t:'meter',shared_status:'following',shared_follow:occupying});
  await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
  await fake.send({t:'meter',shared_status:'desktop_writer',shared_follow:occupying});
  await waitFor(p,`!document.getElementById('shared-status').hidden && !document.getElementById('toast').classList.contains('on')`);
  await fake.send({t:'meter',shared_status:'following',shared_follow:occupying});
  await sleep(100);
  assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),false);
  let seq=1;
  for (const agent of ['codex','claude','opencode']) {
   const meter={t:'meter',shared_status:agent==='codex'?'following':null,shared_follow:{agent,id:String(seq++).repeat(32)}};
   await fake.send({t:'status',s:'idle',agent});
   await fake.send(meter);
   await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
   assert.equal(await evaluate(p,`document.getElementById('shared-status').hidden`),true);
   const before=await evaluate(p,`document.querySelector('.composer').getBoundingClientRect().top`);
   const text=await evaluate(p,`document.getElementById('toast').textContent`);
   assert.match(text,language==='zh'?/正在跟/:/Following/);
   const box=await evaluate(p,`(()=>{const r=document.getElementById('toast').getBoundingClientRect();return {left:r.left,right:r.right}})()`);
   assert.ok(box.left>=0 && box.right<=360,'narrow toast within viewport');
   writeFileSync(new URL(agent+'-'+language+'-visible.png',out),await shoot(p,''));
   // Repeated meters may not restart the original deadline.
   await sleep(1800);await fake.send(meter);await sleep(1300);
   assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),true);
   await waitFor(p,`!document.getElementById('toast').classList.contains('on')`);
   await sleep(300);
   assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('toast')).opacity`),'0');
   assert.equal(await evaluate(p,`document.querySelector('.composer').getBoundingClientRect().top`),before);
   writeFileSync(new URL(agent+'-'+language+'-gone.png',out),await shoot(p,''));
   await navigate(p,web.url+'?lang='+language);await waitState(p,'ready');await sleep(500);
   assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),false);
   fake.setUp(false);await sleep(120);fake.setUp(true);await waitState(p,'ready');await sleep(500);
   assert.equal(await evaluate(p,`document.getElementById('toast').classList.contains('on')`),false);
  }
  await fake.send({t:'meter',shared_status:'desktop_writer',shared_follow:{agent:'codex',id:'f'.repeat(32)},shared_writer:{zh:'终端会话被占用：请退出后重发。',en:'Terminal session is read-only: quit and resend.'}});
  await waitFor(p,`!document.getElementById('shared-status').hidden`);await sleep(4000);
  assert.equal(await evaluate(p,`document.getElementById('shared-status').hidden`),false);
  await navigate(p,web.url+'?lang='+language);await waitState(p,'ready');
  await waitFor(p,`!document.getElementById('shared-status').hidden`);
  writeFileSync(new URL('action-'+language+'.png',out),await shoot(p,''));
  // A rejected send must still produce its error, not be swallowed by sharing information.
  fake.st.sayWhy='no_agent';
  await evaluate(p,`(()=>{const n=document.getElementById('input');n.value='P98 rejected send';n.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('send').click()})()`);
  await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
  const errorText=await evaluate(p,`document.getElementById('toast').textContent`);
  assert.doesNotMatch(errorText,/Following|正在跟/);
  assert.equal(await evaluate(p,`document.getElementById('input').value`),'P98 rejected send');
  await fake.send({t:'meter',shared_status:'following',shared_follow:{agent:'codex',id:'e'.repeat(32)}});
  await sleep(100);
  assert.equal(await evaluate(p,`document.getElementById('toast').textContent`),errorText);
  await fake.send({t:'meter',shared_status:null,shared_follow:null});
  await waitFor(p,`document.getElementById('shared-status').hidden`);
  assert.deepEqual(p.problems,[]);results['shared-toast-'+language]='pass';console.log('PASS shared-toast-'+language);
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('results.json',out),JSON.stringify(results,null,2)+'\n');
if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p98.mjs',results}));
