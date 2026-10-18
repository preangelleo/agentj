// Independent P385 QA. Isolated local fixtures, synthetic values only; Chromium simulation, no real iOS/Windows.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startServer} from '../../site/test/server.mjs';
import {startDashboard} from '../../dashboard/test/server.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,key} from './browser.mjs';
const out='reports/qa/p385/evidence';mkdirSync(out,{recursive:true});
const site=await startServer(),dash=await startDashboard({billingGate:true}),web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const siteBase=`http://127.0.0.1:${site.port}`,results=[];
const ev=evaluate;
async function click(p,s){await ev(p,`document.querySelector(${JSON.stringify(s)}).click();true`);}
async function audit(p,lang,theme){
 const r=await ev(p,`({lang:document.documentElement.lang,theme:document.documentElement.dataset.theme,overflow:document.documentElement.scrollWidth-innerWidth,bg:getComputedStyle(document.body).backgroundColor})`);
 assert.ok(r.lang.startsWith(lang),JSON.stringify(r));assert.equal(r.theme,theme);assert.ok(r.overflow<=1,JSON.stringify(r));return r;
}
async function setTheme(p,theme){await ev(p,`localStorage.setItem('aj.theme',${JSON.stringify(theme)});document.documentElement.dataset.theme=${JSON.stringify(theme)};true`);}
async function shot(p,name){writeFileSync(`${out}/${name}.png`,await shoot(p,''));}
async function copy(p,selector,want){await click(p,selector);await waitFor(p,`navigator.clipboard.readText().then(x=>x===${JSON.stringify(want)})`);}
try{
 // Set up a genuine local account through email + virtual passkey; no production requests.
 const p=await newPage(B,390,844,'light',{allow:[dash.appOrigin]});
 await p.send('WebAuthn.enable');await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
 await navigate(p,dash.appUrl);await waitFor(p,`!!document.getElementById('email')`);
 await ev(p,`document.getElementById('email').value='qa-placeholder@example.invalid';document.getElementById('send').click();true`);
 await waitFor(p,`!document.getElementById('code-box').hidden`);
 await ev(p,`document.getElementById('code').value=${JSON.stringify(dash.lastCode('qa-placeholder@example.invalid'))};document.getElementById('verify').click();true`);
 await waitFor(p,`!!document.getElementById('first')&&!document.getElementById('first-passkey-box').hidden`,15000);await click(p,'#first');
 await waitFor(p,`!document.getElementById('company-create-box').hidden`,15000);await ev(p,`document.getElementById('tname').value='QA placeholder';document.getElementById('company-create').click();true`);
 await waitFor(p,`!!document.querySelector('#companies .company')`,15000);
 const tenant=dash.db.raw.prepare('SELECT id,slug FROM tenants').get();dash.db.raw.prepare('UPDATE tenants SET comp_seats=1 WHERE id=?').run(tenant.id);
 await ev(p,`fetch('/account/api/seat-setups',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({tenant:${JSON.stringify(tenant.slug)}})}).then(r=>{if(!r.ok)throw Error('setup '+r.status);return true})`);
 await navigate(p,dash.appUrl);
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const w of [390,1440]){
  await p.send('Emulation.setDeviceMetricsOverride',{width:w,height:w===390?844:900,deviceScaleFactor:1,mobile:w===390});
  await ev(p,`window.AJLang.set(${JSON.stringify(lang)});true`);await setTheme(p,theme);
  await waitFor(p,`document.querySelectorAll('a[href="https://agentj.app/docs/install"]').length>=2`);
  await waitFor(p,`[...document.querySelectorAll('a[href="https://agentj.app/docs/install"]')].every(a=>a.textContent===${JSON.stringify(lang==='zh'?'安装指南':'Installation guide')})`);
  const links=await ev(p,`[...document.querySelectorAll('a[href="https://agentj.app/docs/install"]')].map(a=>({text:a.textContent,target:a.target,rel:a.rel,head:!!a.closest('header'),gap:getComputedStyle(a.parentElement).gap}))`);
  for(const a of links){assert.equal(a.text,lang==='zh'?'安装指南':'Installation guide');assert.equal(a.target,'_blank');assert.match(a.rel,/noopener/);assert.match(a.rel,/noreferrer/);}
  assert.ok(links.some(x=>x.head));assert.ok(links.some(x=>!x.head));
  // The screenshot uses unmistakable placeholders for generated synthetic setup materials and account identity.
  await ev(p,`document.querySelectorAll('code,.co-id').forEach(n=>n.textContent='QA_PLACEHOLDER');document.querySelectorAll('input').forEach(n=>{if(n.value.includes('@'))n.value='QA_PLACEHOLDER'});document.querySelectorAll('*').forEach(n=>{if(n.children.length===0)n.textContent=n.textContent.replaceAll('qa-placeholder@example.invalid','QA_PLACEHOLDER').replaceAll(${JSON.stringify(tenant.slug)},'QA_PLACEHOLDER')});window.scrollTo({top:0,behavior:'instant'});true`);
  await shot(p,`account-header-${lang}-${theme}-${w}`);
  await ev(p,`document.querySelectorAll('a[href="https://agentj.app/docs/install"]')[1].scrollIntoView({block:'center',behavior:'instant'});true`);
  results.push({surface:'account',lang,theme,w,...await audit(p,lang,theme),links});await shot(p,`account-${lang}-${theme}-${w}`);
 }
 await p.dispose();
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const w of [390,1440]){
  const q=await newPage(B,w,w===390?844:900,theme,{allow:[siteBase]});
  try{await navigate(q,siteBase+'/docs/install/?lang='+lang);await setTheme(q,theme);await waitFor(q,`document.querySelectorAll('.site-copy').length>=6`);
   let count=0;
   for(const os of ['windows','macos','linux']){
    await click(q,`.site-md[data-l="${lang}"] .site-ostab[data-os="${os}"]`);
    const root=`.site-md[data-l="${lang}"] .site-ospanel[data-os="${os}"]`;
    const blocks=await ev(q,`[...document.querySelectorAll(${JSON.stringify(root+' .site-codeblock')})].map(n=>({text:n.querySelector('code').textContent,btn:!!n.querySelector('.site-copy'),visible:!!n.getClientRects().length}))`);
    assert.ok(blocks.length>=2);assert.ok(blocks.every(x=>x.btn&&x.visible));count+=blocks.length;
    for(let i=0;i<blocks.length;i++){await ev(q,`document.querySelectorAll(${JSON.stringify(root+' .site-copy')})[${i}].click();true`);await waitFor(q,`navigator.clipboard.readText().then(x=>x===${JSON.stringify(blocks[i].text)})`);}
    await ev(q,`document.querySelector(${JSON.stringify(root+' .site-codeblock')}).scrollIntoView({block:'center',behavior:'instant'});true`);await shot(q,`install-${os}-${lang}-${theme}-${w}`);
    await ev(q,`document.querySelector(${JSON.stringify(root+' code.language-text')}).closest('.site-codeblock').scrollIntoView({block:'center',behavior:'instant'});true`);await shot(q,`install-prompt-${os}-${lang}-${theme}-${w}`);
   }
   results.push({surface:'install',lang,theme,w,blocks:count,...await audit(q,lang,theme)});
  }finally{await q.dispose();}
 }
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const w of [390,1440]){
  fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme}};
  const q=await newPage(B,w,w===390?844:900,theme,{touch:w===390,allow:[web.url,fake.relay+'/']});
  try{await navigate(q,fake.newPairing(web.url+'?lang='+lang));await waitState(q,'awaiting-approval');await fake.approve();await waitState(q,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'QA placeholder'});
   await ev(q,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true`);
   const msg=(lang==='zh'?'Telegram 配对码（测试占位）':'Telegram pairing code (test placeholder)')+'\n\n```text\nQA_PLACEHOLDER\n```\n\n```text\n/start QA_PLACEHOLDER\n```\n\nhttps://t.me/qa_placeholder_bot?start=QA_PLACEHOLDER\n\n```sh\nagentj reload\n```\n\n```sh\nagentj session resume QA_PLACEHOLDER\n```';
   await fake.addTurn({k:'sys',text:''},msg);await waitFor(q,`document.querySelectorAll('pre').length>=4`);
   const pres=await ev(q,`[...document.querySelectorAll('#words pre')].map(x=>x.textContent)`);
   assert.deepEqual(pres.map(x=>x.trim()),['QA_PLACEHOLDER','/start QA_PLACEHOLDER','agentj reload','agentj session resume QA_PLACEHOLDER']);
   for(let i=0;i<4;i++){await ev(q,`document.querySelectorAll('#words .copy')[${i}].click();true`);await waitFor(q,`navigator.clipboard.readText().then(x=>x.trim()===${JSON.stringify(pres[i].trim())})`);}
   const a=await ev(q,`(()=>{let a=document.querySelector('a[href^="https://t.me/"]');return a&&{target:a.target,rel:a.rel,href:a.href}})()`);assert.ok(a);assert.equal(a.target,'_blank');assert.match(a.rel,/noopener/);
   results.push({surface:'system',lang,theme,w,copy:4,cmdCopies:1,...await audit(q,lang,theme),link:a});await shot(q,`system-${lang}-${theme}-${w}`);
   await ev(q,`document.getElementById('main').scrollTo({top:document.getElementById('main').scrollHeight,behavior:'instant'});true`);await shot(q,`system-recovery-${lang}-${theme}-${w}`);
   await fake.addTurn({k:'cmd',text:'/recovery'},msg);await waitFor(q,`document.getElementById('omText').textContent==='/recovery'`);await copy(q,'#words .copy','QA_PLACEHOLDER');await shot(q,`command-${lang}-${theme}-${w}`);
   await click(q,'#setBtn');await waitFor(q,`!!document.getElementById('rs-restart-go')`);assert.equal(await ev(q,`document.getElementById('rs-restart-go').textContent`),lang==='zh'?'重启服务':'Restart service');await key(q,'Escape');
  }finally{await q.dispose();}
 }
 writeFileSync(`${out}/browser.json`,JSON.stringify({simulation:'Desktop Chromium viewport/touch simulation; no physical iOS or Windows verification',results},null,2));console.log('PASS P385 independent browser evidence',results.length,'matrix cases');
}finally{await B.close();await fake.stop();await web.stop();await dash.stop();await new Promise(r=>site.server.close(r));}
