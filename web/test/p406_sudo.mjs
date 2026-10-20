// P406 #173 (browser, fake host): the sudo card leaves the screen once the computer says the command started; a status strip
// above the composer shows it running (tap → a signed elev_watch, only then the host-masked output; 「停止」 two taps → a signed
// elev_stop), then the result (its output tail only when the output was opened). A wrong password keeps the card. The down-arrow button and ESC collapse the card at every stage (asking,
// checking the password) without sending anything — no deny — and the strip opens it again; a reconnect replay of a running
// card goes straight to the strip. zh/en × light/dark × 390 (+1440 once) → reports/qa/p406-sudo/.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,key,shoot,sleep} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const out='reports/qa/p406-sudo';mkdirSync(out,{recursive:true});
const fake=await startFakeHost(), web=await startWebServer(), B=await launch();
const epk=()=>generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
const hex=(c)=>c.repeat(32);
const card=(id,n,extra={})=>({t:'elev',id,kind:'sudo',n,epk:epk(),ttl:120,tries:3,cmd:'bash /opt/restore.sh --all',why:'Restore last night’s backup to the server',effect:'Replaces files under /srv',...extra});
const submit=(pw)=>`(()=>{const i=document.getElementById('elev-input');i.value=${JSON.stringify(pw)};i.dispatchEvent(new Event('input'));document.getElementById('elev-allow').click();return true})()`;
const shown=`(document.getElementById('elev')?.hidden===false)`;
const strip=(state)=>`(document.getElementById('elev-run')?.hidden===false && document.getElementById('elev-run').dataset.state===${JSON.stringify(state)})`;
const txt=(id)=>evaluate(p0(),`document.getElementById(${JSON.stringify(id)}).textContent`);
let P=null;const p0=()=>P;
let count=0;
const combos=[];for(const lang of ['zh','en']) for(const theme of ['light','dark']) combos.push([lang,theme,390]);combos.push(['zh','light',1440]);
try{
for(const [lang,theme,width] of combos){
 fake.reset();fake.setUp(true);
 const got=[];
 fake.st.onApp=async(c,m)=>{
  if(m.t==='elev_answer'){got.push(m);return true;}
  if(m.t==='elev_stop'){got.push({...m,verified:await fake.verifyControl(c,'elev_stop',m,{id:m.id})});return true;}
  if(m.t==='elev_watch'){got.push({...m,verified:await fake.verifyControl(c,'elev_watch',m,{id:m.id,on:m.on})});return true;}
  return false;
 };
 const answers=()=>got.filter(m=>m.t==='elev_answer');
 async function until(fn,ms=5000){for(let i=0;i<ms/50;i++){if(fn())return true;await sleep(50);}return false;}
 const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});P=p;
 const zh=lang==='zh';
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click(); if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click(); document.documentElement.dataset.theme='${theme}'; true`);
  const A=hex('a');
  // 1. asking: the down-arrow collapses — nothing is sent, the strip says it is waiting; tapping the strip brings it back
  await fake.send(card(A,hex('1')));
  await waitFor(p,shown);
  assert.equal(await evaluate(p,`getComputedStyle(document.getElementById('elev-hide')).display!=='none' && document.getElementById('elev-hide').getAttribute('aria-label')`),zh?'收起（不会拒绝）':'Collapse (nothing is declined)');
  writeFileSync(`${out}/web-${lang}-${theme}-${width}-card.png`,await shoot(p,''));
  await evaluate(p,`document.getElementById('elev-hide').click(); true`);
  await waitFor(p,`!${shown} && ${strip('ask')}`);
  assert.equal(await txt('elev-run-text'),zh?'sudo 等你确认 · 点这里打开':'sudo is waiting for you · tap to open');
  writeFileSync(`${out}/web-${lang}-${theme}-${width}-waiting.png`,await shoot(p,''));
  await evaluate(p,`document.getElementById('elev-run-main').click(); true`);
  await waitFor(p,`${shown} && document.getElementById('elev-run').hidden`);
  // ESC = the same collapse (P142's layer), never the deny
  await evaluate(p,`document.activeElement?.blur(); true`);
  await key(p,'Escape');
  await waitFor(p,`!${shown} && ${strip('ask')}`);
  await sleep(200);
  assert.equal(got.length,0,'collapse / ESC while asking sends nothing (no deny)');
  await evaluate(p,`document.getElementById('elev-run-main').click(); true`);
  await waitFor(p,shown);
  // 2. a wrong password keeps the card: the host re-arms it (new nonce) with bad:1
  await evaluate(p,submit('wrong-pw'));
  assert.ok(await until(()=>answers().length===1),'first answer');
  await fake.send(card(A,hex('2'),{bad:1,tries:2}));
  await waitFor(p,`${shown} && document.getElementById('elev-bad').hidden===false && document.getElementById('elev-allow').disabled===false`);
  assert.equal(await evaluate(p,`document.getElementById('elev-run').hidden`),true,'no strip while the card asks again');
  // 3. the right password: checking (card shows Executing…) → ESC collapses it there too → reopen from the strip
  await evaluate(p,submit('right-pw'));
  assert.ok(await until(()=>answers().length===2),'second answer');
  await waitFor(p,`document.getElementById('elev-left').textContent===${JSON.stringify(zh?'执行中…':'Executing…')}`);
  await key(p,'Escape');
  await waitFor(p,`!${shown} && ${strip('verify')}`);
  assert.equal(await txt('elev-run-text'),zh?'sudo 正在核对密码…':'sudo is checking the password…');
  await evaluate(p,`document.getElementById('elev-run-main').click(); true`);
  await waitFor(p,shown);
  // the host says the command started → the card is gone within 2 s, the strip shows it running
  const t0=Date.now();
  await fake.send({t:'elev_progress',id:A,kind:'sudo',state:'running',phase:'started',elapsed:1,timeout:600});
  await waitFor(p,`!${shown} && ${strip('running')}`,2000);
  assert.ok(Date.now()-t0<2000,'card closes within 2 s of started');
  assert.match(await txt('elev-run-text'),zh?/^sudo 执行中… 0:0\d$/:/^sudo running… 0:0\d$/);
  assert.equal(await evaluate(p,`document.getElementById('elev-run-stop').hidden`),false);
  writeFileSync(`${out}/web-${lang}-${theme}-${width}-running.png`,await shoot(p,''));
  // 4. tap → the phone asks for the output with a signed elev_watch (F1: nothing is streamed before), then shows the host-masked
  //    output; ESC folds the panel and says watch off
  assert.equal(got.filter(m=>m.t==='elev_watch').length,0,'no watch before the owner opens the output');
  await evaluate(p,`document.getElementById('elev-run-main').click(); true`);
  assert.ok(await until(()=>got.some(m=>m.t==='elev_watch'&&m.on===true)),'expanding sends elev_watch on');
  assert.ok(got.find(m=>m.t==='elev_watch').verified===true&&got.find(m=>m.t==='elev_watch').id===A,'the watch is signed over this card id + on');
  await fake.send({t:'elev_out',id:A,text:'restoring 1/3\n',full:true});
  await waitFor(p,`document.getElementById('elev-run-panel').hidden===false && document.getElementById('elev-run-out').textContent.includes('restoring 1/3')`);
  await fake.send({t:'elev_out',id:A,text:'token <redacted>\nrestoring 2/3\n'});
  await waitFor(p,`document.getElementById('elev-run-out').textContent.includes('restoring 2/3')`);
  assert.match(await txt('elev-run-note'),zh?/只有这台手机能看到/:/only this phone sees it/);
  writeFileSync(`${out}/web-${lang}-${theme}-${width}-output.png`,await shoot(p,''));
  await key(p,'Escape');
  await waitFor(p,`document.getElementById('elev-run-panel').hidden===true && ${strip('running')}`);
  assert.ok(await until(()=>got.some(m=>m.t==='elev_watch'&&m.on===false&&m.verified)),'folding sends a signed elev_watch off');
  await evaluate(p,`document.getElementById('elev-run-main').click(); true`);
  await waitFor(p,`document.getElementById('elev-run-panel').hidden===false`);
  assert.ok(await until(()=>got.filter(m=>m.t==='elev_watch'&&m.on===true).length===2),'reopening asks again');
  // 5. Stop: the first tap only arms it; the second sends a signed elev_stop for this card
  await evaluate(p,`document.getElementById('elev-run-stop').click(); true`);
  await waitFor(p,`document.getElementById('elev-run-stop').textContent===${JSON.stringify(zh?'确定停止？':'Stop it?')}`);
  await sleep(150);
  assert.equal(got.filter(m=>m.t==='elev_stop').length,0,'one tap does not stop');
  await evaluate(p,`document.getElementById('elev-run-stop').click(); true`);
  assert.ok(await until(()=>got.some(m=>m.t==='elev_stop')),'elev_stop sent');
  const stop=got.find(m=>m.t==='elev_stop');
  assert.ok(stop.id===A && stop.verified===true,'elev_stop is signed over this card id with the approval key');
  await waitFor(p,strip('stopping'));
  await fake.send({t:'elev_done',id:A,result:'stopped',tail:'restoring 1/3\nrestoring 2/3\n',stop:true});
  await waitFor(p,`${strip('done')} && document.getElementById('elev-run-text').textContent===${JSON.stringify(zh?'sudo 已停止':'sudo stopped')}`);
  assert.match(await txt('elev-run-out'),/restoring 2\/3/);
  await evaluate(p,`document.getElementById('elev-run-x').click(); true`);
  await waitFor(p,`document.getElementById('elev-run').hidden`);
  // 6. a second command that finishes: the result + exit code + output tail come back in the strip (and the toast)
  const C=hex('c');
  await fake.send(card(C,hex('3')));
  await waitFor(p,shown);
  await evaluate(p,submit('right-pw'));
  assert.ok(await until(()=>answers().length===3));
  await fake.send({t:'elev_progress',id:C,kind:'sudo',state:'running',phase:'started',elapsed:0,timeout:600});
  await waitFor(p,`!${shown} && ${strip('running')}`,2000);
  //    nobody opened its output: the result is status + exit code only, nothing to expand
  await fake.send({t:'elev_done',id:C,result:'done',code:3});
  await waitFor(p,`${strip('done')} && document.getElementById('elev-run-text').textContent===${JSON.stringify(zh?'sudo 执行失败（退出码 3）':'sudo failed (exit code 3)')}`);
  writeFileSync(`${out}/web-${lang}-${theme}-${width}-result.png`,await shoot(p,''));
  await evaluate(p,`document.getElementById('elev-run-main').click(); true`);
  await sleep(200);
  assert.equal(await evaluate(p,`document.getElementById('elev-run-panel').hidden`),true,'no tail, no panel');
  assert.equal(got.filter(m=>m.t==='elev_watch'&&m.id===C).length,0,'a finished command is never asked for output');
  await evaluate(p,`document.getElementById('elev-run-x').click(); true`);
  await waitFor(p,`document.getElementById('elev-run').hidden`);
  // 7. a reconnect replay of a running card goes straight to the strip (its output replaces the page's)
  const D=hex('d');
  await fake.send({...card(D,null),n:null,state:'running',phase:'started',ttl:0,elapsed:65,timeout:600});
  await waitFor(p,`!${shown} && ${strip('running')}`);
  assert.match(await txt('elev-run-text'),/1:0\d/);
  await fake.send({t:'elev_out',id:D,text:'so far\n',full:true});
  await fake.send({t:'elev_done',id:D,result:'done',code:0,tail:'so far\nok\n'});
  await waitFor(p,`${strip('done')} && document.getElementById('elev-run-text').textContent===${JSON.stringify(zh?'sudo 已完成':'sudo finished')}`);
  assert.equal(answers().filter(m=>m.ok===false).length,0,'nothing in this walk ever declined');
  assert.ok(!JSON.stringify(got).includes('right-pw')&&!JSON.stringify(got).includes('wrong-pw'),'the password only travels sealed');
  count++;
 }finally{await p.close();}
}
writeFileSync(`${out}/web-result.json`,JSON.stringify({pass:true,combos:count,screenshots:count*5},null,2));
{const rec=parityRecorder('web/test/p406_sudo.mjs');rec.record('p406-sudo-running',true);rec.write();}
console.log('PASS P406 sudo running strip',count,'combinations');
}finally{await B.close();await fake.stop();await web.stop();}
