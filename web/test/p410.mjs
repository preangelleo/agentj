// P410: isolated Chromium + real WebCrypto signed settings and handover card.
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
const out='reports/qa/p410/phone/';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),br=await launch();
const failures=[];let checks=0;const check=(v,s)=>{checks++;if(!v)failures.push(s);};
try {
 for(const lang of ['zh','en'])for(const scheme of ['light','dark']){
  fake.reset();fake.st.prefs={appearance:{language:lang,theme:scheme}};const p=await newPage(br,390,844,scheme,{allow:[web.url,fake.relay+'/']});
  const base=web.url+(lang==='en'?'?lang=en':'');
  await navigate(p,base);await waitState(p,'idle');await navigate(p,'about:blank');
  await navigate(p,fake.newPairing(base));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('confirm-no')?.click()`);
  const F=fake.friends(sampleFriends());
  await navigate(p,web.url+'friends'+(lang==='en'?'?lang=en':''));
  await waitFor(p,`!!document.querySelector('#fr-list .fr-item')`);
  await evaluate(p,`document.querySelector('#fr-list .fr-item').click()`);
  await waitFor(p,`!!document.getElementById('fr-open-detail')`);
  await evaluate(p,`document.getElementById('fr-open-detail').click()`);
  await waitFor(p,`!!document.getElementById('fr-hold-for-owner')`);
  check(await evaluate(p,`document.documentElement.getAttribute('data-theme')===${JSON.stringify(scheme)}`),`${lang}/${scheme}: actual theme`);
  check(await evaluate(p,`!document.getElementById('fr-hold-for-owner').checked`),`${lang}/${scheme}: default off`);
  for(const on of [true,false]){
   const before=F.writes.length;
   await evaluate(p,`document.getElementById('fr-hold-for-owner').click()`);
   for(let i=0;i<60&&F.writes.length===before;i++)await sleep(50);
   const w=F.writes.at(-1);
   check(w?.op==='hold-for-owner'&&w.value===(on?'on':'off')&&w.sigOk===true,`${lang}/${scheme}: genuine signed ${on}`);
   await waitFor(p,`document.getElementById('fr-hold-for-owner')?.checked===${on}`);
   const {data}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(out+`${lang}-${scheme}-${on?'on':'off'}.png`,Buffer.from(data,'base64'));
  }
  check(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth`),`${lang}/${scheme}: no overflow`);
  fake.st.holdAnswers=true;
  const id=(lang==='zh'?'a':'b').repeat(32);
  await fake.ask({t:'ask',id,tool:'peer_question',summary:'Approve only this handover: check task progress',ttl:86400,cat:['send'],pq:{handover:true,friend:F.friends[0].id,name:'Kai',text:'检查任务进展 / Check task progress',draft:''}});
  await waitFor(p,`!!document.getElementById('frHoldAllow')`);
  check(await evaluate(p,`!document.getElementById('frQAuto')&&!document.getElementById('frQMain')&&!document.getElementById('frQSend')`),`${lang}/${scheme}: no alternate automatic path`);
  const {data}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(out+`${lang}-${scheme}-approval.png`,Buffer.from(data,'base64'));
  await evaluate(p,`document.getElementById('frHoldAllow').click()`);
  for(let i=0;i<60&&!fake.st.answers.length;i++)await sleep(50);
  check(fake.st.answers.at(-1)?.ok===true&&fake.st.sigOk.at(-1)===true,`${lang}/${scheme}: signed per-request approval`);
  check(!p.problems.length,`${lang}/${scheme}: browser console clean ${p.problems.join(',')}`);
  await p.dispose();
 }
}finally{await br.close();await fake.stop();await web.stop();}
writeFileSync(out+'result.json',JSON.stringify({checks,failures},null,2)+'\n');console.log(JSON.stringify({checks,failures}));if(failures.length)process.exitCode=1;
