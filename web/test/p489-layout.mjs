// New real Python host + old-host encrypted upload compatibility; no owner / QA credentials.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync,rmSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startLocalRelay} from '../../relay/test/local_relay.mjs';
import {HostProc} from '../../tests/lib/host.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const tmp=mkdtempSync('/tmp/p489-diag-'),out='reports/qa/p489/on0254/diag';mkdirSync(out,{recursive:true});mkdirSync(join(tmp,'home'));mkdirSync(join(tmp,'work'));mkdirSync(join(tmp,'state'));
const web=await startWebServer(),relay=await startLocalRelay(),browser=await pw.webkit.launch();
const host=new HostProc({relay:relay.url,web:web.url,stateDir:join(tmp,'state'),sessionMode:null,env:{HOME:join(tmp,'home'),XDG_CONFIG_HOME:join(tmp,'config'),AJ_PYTHON:process.env.AJ_PYTHON||resolve('host/.venv/bin/python'),AGENTJ_SETUP_AGENTS:'0',AGENTJ_TEST:'1'}});
let fake;const checks=[];
try{
 if(!process.argv.includes('--old-host-only')) {
 host.init();await host.serve();
 const c=await browser.newContext({...pw.devices['iPhone 15'],viewport:{width:390,height:844},deviceScaleFactor:1,serviceWorkers:'block',userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1'});
 const p=await c.newPage();const pair=await host.pair();await p.goto(web.url+pair.link.slice(pair.link.indexOf('#')));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await pair.waitPrompt();pair.typeCode((await p.locator('#sas').textContent()).replace(/\D/g,''));await p.waitForFunction(()=>window.__ajState==='ready');assert.equal((await pair.exited).code,0);
 await p.evaluate(async()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();await(await import('./js/settings.js')).openRescue();});
 await p.evaluate(()=>document.getElementById('layout-diag-send').click());await p.waitForTimeout(2000);console.log('new host receipt',await p.locator('#layout-diag-result').textContent());
 await p.waitForFunction(()=>document.getElementById('layout-diag-result').textContent.includes('已保存'),{},{timeout:10000});
 const saved=JSON.parse(readFileSync(join(tmp,'state/layout-diagnostic-last.json')));
 assert.equal(saved.sample.ua,'chrome');assert.equal(saved.sample.innerHeight,844);assert.ok(saved.sample.layers.water);assert.match(saved.sample.build.iosCode,/^[a-f0-9]{64}$/);
 const last=host.run(['diag','layout','--last']);assert.deepEqual(JSON.parse(last),saved);
 await new Promise(r=>setTimeout(r,1100));const logs=readFileSync(join(tmp,'state/host.log'),'utf8').split('\n').filter(Boolean).map(JSON.parse).filter(r=>r.ev==='layout_diag');assert.equal(logs.length,1);assert.equal(logs[0].status,'saved');assert.ok(!JSON.stringify(logs).includes(saved.sample.build.combined));
 writeFileSync(out+'/sample.json',JSON.stringify(saved,null,2));await p.locator('#layout-diag-send').evaluate(e=>e.scrollIntoView({block:'center',behavior:'instant'}));await p.screenshot({path:out+'/new-host-zh.png'});checks.push('WebKit CriOS → actual Noise → real Python Host → private file → metadata log → CLI');
 await c.close();
 }
 fake=await startFakeHost();
 const states=JSON.parse(readFileSync('reports/qa/p489/hosts.json'));
 for(const [lang,h] of [['zh',states[0]],['en',states[0]],['zh',states[1]],['zh',states[2]]]){
  fake.reset();const c=await browser.newContext({...pw.devices['iPhone 15'],viewport:{width:390,height:844},deviceScaleFactor:1,serviceWorkers:'block'});const p=await c.newPage();
  await p.goto(fake.newPairing(web.url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
  await fake.send(h.theme);await fake.send({t:'preferences',value:{appearance:{theme:h.appearance,language:lang}}});await fake.send({t:'status',s:'idle',agent:h.agent,name:h.name});await fake.send({t:'meter',...h.meter});await p.waitForTimeout(1600);
  await p.evaluate(async()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();await(await import('./js/settings.js')).openRescue();});
  await p.evaluate(()=>document.getElementById('layout-diag-send').click());await p.waitForFunction(()=>document.getElementById('layout-diag-result').textContent.includes('30'),{},{timeout:20000});
  const opened=fake.st.opens.filter(Boolean).at(-1);assert.equal(opened.name,'layout-diagnostic.json');assert.equal(opened.purpose,'att');assert.equal(fake.st.says.length,0);
  const sent=fake.log.findLast(x=>x.t==='layout_diag');assert.equal(sent.sample.ua,'safari');
  writeFileSync(out+'/sample-'+h.id+'-'+lang+'.json',JSON.stringify(sent.sample,null,2));
  const expected=typeof h.meter.ctx?.pct==='number'?Math.round(h.meter.ctx.pct):h.meter.ctx?.max>0?Math.round(h.meter.ctx.used/h.meter.ctx.max*100):null;
  assert.equal(sent.sample.meter.ctx,expected);
  const raw=JSON.stringify(sent.sample);for(const forbidden of ['http','ticket','session','chat','bodyText','storage'])assert.ok(!raw.includes(forbidden));
  assert.ok(fake.log.some(x=>x.t==='blob_end'));assert.deepEqual(JSON.parse(Buffer.concat(fake.st.blobs.get(opened.bid).parts).toString()),sent.sample);await p.locator('#layout-diag-send').evaluate(e=>e.scrollIntoView({block:'center',behavior:'instant'}));await p.screenshot({path:out+'/old-host-'+h.id+'-'+lang+'.png'});checks.push('old host '+h.id+' '+lang+': timeout → encrypted attachment receipt, 30min label, no Agent turn');
  fake.setUp(false);await p.waitForTimeout(300);await p.evaluate(async()=>{await(await import('./js/layout-diag.js')).send()});assert.ok(/连不上|offline/.test(await p.locator('#layout-diag-result').textContent()));checks.push('offline '+h.id+' '+lang+': no success');await c.close();fake.setUp(true);
 }
 writeFileSync(out+'/results.json',JSON.stringify({combined:JSON.parse(readFileSync('web/public/version.json')).combined,checks,ok:true},null,2));console.log(checks.join('\n'));
}catch(e){console.log('host tail',host.stderr.slice(-1200));throw e;}finally{await browser.close();await host.stop();if(fake)await fake.stop();await relay.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
