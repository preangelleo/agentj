// P405 targeted Chromium: real paired signing; no production browser/QA account.
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, sleep } from './browser.mjs';
const out = 'reports/qa/p405/phone/'; mkdirSync(out,{recursive:true});
const web = await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}), fake = await startFakeHost(), br = await launch();
const failures=[]; let checks=0;
const check=(v,s)=>{checks++;if(!v)failures.push(s);};
try {
 for(const lang of ['zh','en']) {
  fake.reset(); const p=await newPage(br,390,844,'light',{allow:[web.url,fake.relay+'/']});
  const base=web.url+(lang==='en'?'?lang=en':'');
  await navigate(p,base); await waitState(p,'idle'); await navigate(p,'about:blank');
  await navigate(p,fake.newPairing(base)); await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
  await evaluate(p,`document.getElementById('a2hs-ok')?.click(); document.getElementById('confirm-no')?.click();`);
  fake.st.holdAnswers=true;
  for (const [i,same] of [[1,false],[2,true]]) {
   const id=String(i).repeat(32), before=fake.st.answers.length;
   await fake.ask({t:'ask',id,tool:'peer_question',summary:'好友任务：检查后台进展',ttl:120,cat:['send'],pq:{friend:'AJ-PH8E-AJT4-26GJ-ACQ9',name:'Kai',text:'检查后台进展',draft:'',same_owner:same}});
   await waitFor(p,`!!document.getElementById('frQSend')`);
   check(await evaluate(p,`!!document.getElementById('frQMain')`)===same,`${lang}: main visible only for same owner`);
   check(await evaluate(p,`!document.getElementById('frQMainAuto')`),`${lang}: no obsolete automatic option`);
   const {data:openCard}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(out+`${lang}-${i}-open.png`,Buffer.from(openCard,'base64'));
   if(same){
    check(await evaluate(p,`document.querySelector('.fr-askbtns').firstElementChild.id`)==='frQMain',`${lang}: main first`);
    await evaluate(p,`document.getElementById('frQMain').click()`);
    for(let j=0;j<60 && fake.st.answers.length===before;j++)await sleep(50);
    check(fake.st.answers.at(-1)?.group==='main',`${lang}: signed handover scope`);
    check(fake.st.sigOk.at(-1)===true,`${lang}: real signature verified`);
   }
   const {data}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(out+`${lang}-${i}.png`,Buffer.from(data,'base64'));
   await fake.send({t:'ask_done',id,result:'deny'});
  }
  const F=fake.friends(sampleFriends());
  for (const on of [true,false]) {
   const id=(on?'a':'b').repeat(32);
   await fake.ask({t:'ask',id,tool:'peer_question',summary:'Same owner pairing',ttl:86400,cat:['send'],pq:{friend:F.friends[0].id,name:'Kai',pairing:true,code:'123456',challenge:'a'.repeat(32)}});
   await waitFor(p,`!!document.getElementById('frOwnerAllow')`);
   const {data:pairingShot}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(out+`${lang}-pairing-${on?'confirm':'reject'}.png`,Buffer.from(pairingShot,'base64'));
   check(await evaluate(p,`document.getElementById('frAsk').textContent.includes('Kai') && document.querySelector('.fr-owner-code').textContent === '123456'`),`${lang}: pairing popup identifies requester`);
   await evaluate(p,`document.getElementById('${on?'frOwnerAllow':'frOwnerDeny'}').click()`);
   for(let j=0;j<60 && F.writes.length < (on?1:2);j++)await sleep(50);
   check(F.writes.at(-1)?.sigOk===true && F.writes.at(-1)?.op===(on?'owner-confirm':'owner-reject') && F.writes.at(-1)?.value==='a'.repeat(32),`${lang}: pairing ${on?'confirm':'reject'} signed`);
   await fake.send({t:'ask_done',id,result:'ended'});
  }
  F.writes.length=0;
  await navigate(p,base.replace('?lang=en','')+'friends'+(lang==='en'?'?lang=en':''));
  await waitFor(p,`!!document.querySelector('#fr-list .fr-item')`);
  await evaluate(p,`document.querySelector('#fr-list .fr-item').click()`);
  await waitFor(p,`!!document.getElementById('fr-open-detail')`);
  await evaluate(p,`document.getElementById('fr-open-detail').click()`);
  await waitFor(p,`!!document.querySelector('#fr-group option[value="same-owner"]')`);
  check(await evaluate(p,`document.getElementById('fr-group').options[3].value`)==='same-owner',`${lang}: Same owner is fourth policy option`);
  await evaluate(p,`const e=document.getElementById('fr-group');e.value='same-owner';e.dispatchEvent(new Event('change',{bubbles:true}))`);
  await waitFor(p,`!document.getElementById('confirm').hidden`);
  await evaluate(p,`document.getElementById('confirm-yes').click()`);
  for(let j=0;j<60 && !F.writes.length;j++)await sleep(50);
  check(F.writes.at(-1)?.sigOk===true && F.writes.at(-1)?.value==='same-owner',`${lang}: genuine signed local confirmation`);
  F.friends.find(f=>f.id===F.writes.at(-1).friend).same_owner='pending'; await fake.send({t:'fr_changed',friend:F.friends[0].id});
  await waitFor(p,`document.getElementById('fr-group')?.value==='same-owner'`);
  await waitFor(p,`document.getElementById('fr-main').textContent.includes(${JSON.stringify(lang==='zh'?'等待对方确认':'Waiting for the other')})`);
  check(true,`${lang}: unilateral pending text`);
  const {data:pending}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(out+`${lang}-pending.png`,Buffer.from(pending,'base64'));
  F.friends.find(f=>f.id===F.writes.at(-1).friend).same_owner='active'; await fake.send({t:'fr_changed',friend:F.writes.at(-1).friend});
  await waitFor(p,`document.getElementById('fr-list').textContent.includes(${JSON.stringify(lang==='zh'?'我的设备':'My devices')})`);
  check(true,`${lang}: confirmed peers grouped under My devices`);
  await p.dispose();
 }
}finally{await br.close();await fake.stop();await web.stop();}
writeFileSync(out+'result.json',JSON.stringify({checks,failures},null,2)+'\n');
console.log(JSON.stringify({checks,failures}));if(failures.length)process.exitCode=1;
