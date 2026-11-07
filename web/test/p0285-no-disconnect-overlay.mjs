import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,shoot,sleep} from './browser.mjs';
const phase=process.env.OFF_PHASE||'after',out=process.env.OFF_OUT||`reports/qa/p0285-no-disconnect-overlay/${phase}`;
mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.OFF_PUBLIC?{publicDir:process.env.OFF_PUBLIC}:{}),fake=await startFakeHost(),browser=await launch(),rows=[];
try{for(const width of [390,768,1440])for(const lang of ['zh','en'])for(const theme of ['dark','light']){
 fake.reset();fake.st.prefs={appearance:{language:lang}};
 const p=await newPage(browser,width,1000,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.fonts.ready.then(()=>{document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);document.getElementById('input').focus();window.__recoveryNotices=[];new MutationObserver(()=>{const t=document.getElementById('toast')?.textContent||'';if(/本次断开|原因未知|Connection restored after|unknown reason/i.test(t))window.__recoveryNotices.push(t);}).observe(document.getElementById('toast'),{childList:true,subtree:true,characterData:true});})`);await sleep(2200);
  writeFileSync(`${out}/${width}-${lang}-${theme}-chat.png`,await shoot(p));
  await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);writeFileSync(`${out}/${width}-${lang}-${theme}-settings.png`,await shoot(p));await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);
  fake.setUp(false);await sleep(5800);
  const down=await evaluate(p,`({state:window.__ajState,exists:!!document.getElementById('offDlg'),visible:!!document.getElementById('offDlg')&&!document.getElementById('offDlg').hidden,bodyFlag:document.body.hasAttribute('data-offline-card'),quietIndicator:!!document.getElementById('status'),outbox:!!document.getElementById('outbox')})`);
  assert.notEqual(down.state,'ready');assert.ok(down.quietIndicator&&down.outbox);
  if(phase==='before')assert.equal(down.visible,true,'formal04 still auto-opens the old overlay');
  else assert.deepEqual([down.exists,down.visible,down.bodyFlag],[false,false,false],'no disconnected overlay or blur flag survives');
  writeFileSync(`${out}/${width}-${lang}-${theme}-offline.png`,await shoot(p));
  await evaluate(p,`dispatchEvent(new Event('pageshow'));document.dispatchEvent(new Event('resume'));`);
  fake.setUp(true);await waitState(p,'ready',15000);await sleep(700);
  const notices=await evaluate(p,'window.__recoveryNotices.length');assert.equal(notices,0,'recovery never shows an outage duration/reason notice');
  assert.equal(await evaluate(p,`!!document.getElementById('offDlg')&&!document.getElementById('offDlg').hidden`),false);
  rows.push({width,lang,theme,...down,recovered:true,recoveryNotices:notices});console.log('PASS quiet',phase,width,lang,theme);
 }finally{await p.dispose();}
}}finally{writeFileSync(`${out}/results.json`,JSON.stringify({phase,fixture:'isolated real Chromium and Noise relay; actual host-down/recovery transitions',rows},null,2));await browser.close();await fake.stop();await web.stop();}
