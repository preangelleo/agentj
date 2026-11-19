// Real host, local workerd, paired Chromium. Only the model's duration and replies are scripted.
import assert from 'node:assert/strict';
import {mkdirSync,mkdtempSync,writeFileSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startLocalRelay} from '../../relay/test/local_relay.mjs';
import {HostProc} from '../../tests/lib/host.mjs';
import {launchBrowser} from '../../tests/lib/browser.mjs';

const temp=mkdtempSync(join(tmpdir(),'aj-0319-'));
const out=process.env.OLARES_RUN_OUT || 'reports/qa/p0319';
mkdirSync(out,{recursive:true});
let web,relay,host,browser,page,stage;
const prompts=['整理今天的会议记录','列出下周要办的三件事','把确认过的日期整理成表'];
const replies=['会议记录整理好了。','下周有三件事。','日期表整理好了。'];
const str=JSON.stringify;
try {
  web=await startWebServer();
  relay=await startLocalRelay({allowLoopback:true});
  const local=web.url.replace('127.0.0.1','localhost');
  const work=join(temp,'work'), home=join(temp,'home'), bin=join(work,'claude');
  mkdirSync(work);mkdirSync(home);
  const standin=fileURLToPath(new URL('../../host/tests/fakeclaude.py',import.meta.url));
  const scripts=Object.fromEntries(prompts.map((p,i)=>[p,{sleep:i===0?20:2,reply:replies[i]}]));
  writeFileSync(join(work,'script.json'),str(scripts));
  writeFileSync(join(work,'fakeclaude.py'),readFileSync(standin,'utf8').replace('elif text in SCRIPT:', 'elif isinstance(text,str) and any(k in text for k in SCRIPT):\n        text=next(k for k in SCRIPT if k in text)'));
  writeFileSync(bin,'#!/bin/sh\nexport FAKE_CLAUDE_SCRIPT='+join(work,'script.json')+'\nexec /usr/bin/python3 '+join(work,'fakeclaude.py')+' "$@"\n',{mode:0o700});
  stage='start host';
  host=new HostProc({relay:relay.url,web:local,stateDir:join(temp,'state'),env:{HOME:home,AGENTJ_CLAUDE_BIN:bin,AGENTJ_TEST_PASSKEY_ORIGIN:'loopback'}}).init({workingRoot:work});
  host.run(['agent','claude','--dir',work]);
  await host.serve();
  browser=await launchBrowser({extraArgs:['--no-sandbox']});
  page=await browser.newPage();
  await page.send('WebAuthn.enable',{enableUI:false});
  await page.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
  stage='pair';
  const pair=await host.pair();
  await page.goto(local+pair.link.slice(pair.link.indexOf('#')));
  await page.waitState('awaiting-approval');
  const sas=(await page.eval("document.querySelector('#sas').textContent")).replace(/\D/g,'');
  await pair.waitPrompt();pair.typeCode(sas);assert.equal((await pair.exited).code,0);await page.waitState('ready');
  await page.waitFor("document.querySelector('#confirm-yes')?.offsetWidth>0");
  await page.click('#confirm-no');
  await page.eval("for(const id of ['phone-env-dismiss','a2hs-ok']) {const e=document.getElementById(id);if(e?.offsetParent)e.click()}");
  stage='three paired sends';
  for(const text of prompts){
    await page.type('#input',text);await page.hold('#send',40);
    await page.waitFor("!document.querySelector('#input').value");
  }
  stage='queue badge and compact list';
  await page.waitFor("document.querySelector('#ownerQueueBtn:not([hidden]) .queueCount')?.textContent==='2'",12000);
  assert.equal(await page.eval("document.querySelector('#ownerQueueDialog').open"),false);
  const shots=[];
  for(const width of [390,1280,1440])for(const theme of ['light','dark']){
    await page.send('Emulation.setDeviceMetricsOverride',{width,height:width===390?844:900,deviceScaleFactor:1,mobile:width===390});
    await page.eval(`import('./js/settings.js').then(s => s.applyThemeLocal(${str(theme)}))`);
    await page.waitFor("document.documentElement.dataset.theme==="+str(theme));
    await page.shot(join(out,width+'-'+theme+'-closed.png'));
    assert.equal(await page.eval('!!document.querySelector("#webReportBtn").offsetWidth'),false);
    await page.hold('#ownerQueueBtn',20);
    await page.waitFor("document.querySelectorAll('#ownerQueueDialog[open] li').length===2");
    assert.deepEqual(await page.eval("Array.from(document.querySelectorAll('#ownerQueueList li p'),n=>n.textContent)"),prompts.slice(1));
    assert.doesNotMatch(await page.eval("document.querySelector('#words').innerText"),/排队|前面还有|正在收尾/);
    assert.equal(await page.eval("document.querySelector('#ownerQueueStop').disabled"),false);
    await page.shot(join(out,width+'-'+theme+'-open.png'));
    await page.hold('#ownerQueueClose',20);
    await page.waitFor("!document.querySelector('#ownerQueueDialog').open");
    shots.push({width,theme,queued:2});
  }
  stage='three replies on their own pages';
  await page.waitFor("document.querySelector('#words').innerText.includes("+str(replies[2])+")",45000);
  for(let i=2;i>=0;i--){
    await page.waitFor("document.querySelector('#omText').textContent.includes("+str(prompts[i])+") && document.querySelector('#words').innerText.includes("+str(replies[i])+")");
    if(i) await page.hold('#pgPrev',30);
  }
  assert.equal(await page.eval('document.querySelector("#ownerQueueBtn").hidden'),true);
  assert.equal(await page.eval('!!document.querySelector("#webReportBtn").offsetWidth'),true);
  writeFileSync(join(out,'journey.json'),str({paired:true,threeSeparateReplies:true,originalActionRestored:true,shots},null,2));
  console.log('ok 1 - real paired owner queue');
} catch(e) {
  console.error('Failed at',stage,String(e).split('\n')[0].slice(0,160));
  if(page) console.error('UI',await page.eval("({count:document.querySelector('.queueCount')?.innerText, theme:document.documentElement.dataset.theme, open:document.querySelector('#ownerQueueDialog')?.open, pages:document.querySelector('#pg')?.innerText, errors:document.body.dataset.state})"));
  if(host)console.error('Host event names',host.events.slice(-20).map(x=>x.ev));
  process.exitCode=1;
} finally {
  for(const stop of [()=>browser?.close(),()=>host?.stop(),()=>relay?.stop(),()=>web?.stop()])await stop();
  rmSync(temp,{recursive:true,force:true});
}
