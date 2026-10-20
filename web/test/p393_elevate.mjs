// #116 + #112 (browser, fake host): the sudo card offers a 10-minute toggle naming the program and its fixed options, OFF by
// default (an answer without it carries no grant); switched on, the answer carries the class hash and signs it; the bar shows 「收回授权」 and sends elev_revoke; an auto-run is announced; a shell /
// interpreter (red) card never offers the window, shows Executing after submit and closes on its result.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const out='reports/qa/p393-elevate';mkdirSync(out,{recursive:true});
const fake=await startFakeHost(), web=await startWebServer(), B=await launch();
const epk=()=>generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
const H='f'.repeat(64), GRANT={secs:600,prog:'gzip',fmt:'gzip -c -d',h:H};
const submit=`(()=>{const i=document.getElementById('elev-input');i.value='pw-for-test';i.dispatchEvent(new Event('input'));document.getElementById('elev-allow').click();return true})()`;
async function answers(got,n){for(let i=0;i<100 && got.filter(m=>m.t==='elev_answer').length<n;i++) await new Promise(r=>setTimeout(r,50));return got.filter(m=>m.t==='elev_answer');}
let count=0;
try{
for(const lang of ['zh','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
 fake.reset();fake.setUp(true);
 const got=[];
 fake.st.onApp=async(c,m)=>{ if(m.t==='elev_answer'||m.t==='elev_revoke'){got.push(m);return true;} return false; };
 const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click(); if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click(); document.documentElement.dataset.theme='${theme}'; true`);
  // 1a. An eligible card: the toggle names the program + options and is OFF; approving as is asks for no window.
  const id0='9'.repeat(32);
  await fake.send({t:'elev',id:id0,kind:'sudo',n:'8'.repeat(32),epk:epk(),ttl:120,tries:3,cmd:'gzip -dc /var/backups/a.gz',why:'Read the nightly backup to check it',effect:'',grant:GRANT});
  // the card root is built on the first card (elevate.js), so it may not exist yet when the wait starts
  await waitFor(p,`document.getElementById('elev')?.hidden===false && document.getElementById('elev-grant-opt')?.hidden===false`);
  assert.equal(await evaluate(p,`document.getElementById('elev-grant-on').checked`),false,'off by default');
  assert.equal(await evaluate(p,`document.getElementById('elev-grant-opt-main').textContent`),lang==='zh'?'10 分钟内，同样参数格式的 gzip 只读命令不再询问':"For 10 minutes, don't ask again for read-only gzip commands in this same format");
  assert.match(await evaluate(p,`document.getElementById('elev-grant-opt-fmt').textContent`),/gzip -c -d/);
  await evaluate(p,submit);
  let ans=await answers(got,1);
  assert.ok(ans[0].ok===true && typeof ans[0].ct==='string' && !('grant' in ans[0]),'toggle off: no grant in the answer');
  await fake.send({t:'elev_done',id:id0,result:'done',code:0});
  await waitFor(p,`document.getElementById('elev').hidden`);
  // 1b. Switched on: the answer carries the offered class hash (the page signs it into the decision).
  const id='a'.repeat(32);
  await fake.send({t:'elev',id,kind:'sudo',n:'b'.repeat(32),epk:epk(),ttl:120,tries:3,cmd:'gzip -dc /var/backups/nightly.gz',why:'Read the nightly backup to check it',effect:'',grant:GRANT});
  await waitFor(p,`!document.getElementById('elev').hidden && document.getElementById('elev-grant-on').checked===false`);
  await evaluate(p,`document.getElementById('elev-grant-opt').click(); true`);
  assert.equal(await evaluate(p,`document.getElementById('elev-grant-on').checked`),true,'the whole row is the tap target');
  writeFileSync(`${out}/web-${lang}-${theme}-${width}-card.png`,await shoot(p,''));
  await evaluate(p,submit);
  await waitFor(p,`document.getElementById('elev-allow').textContent===${JSON.stringify(lang==='zh'?'执行中…':'Executing…')}`);
  ans=await answers(got,2);
  assert.ok(ans[1].ok===true && ans[1].grant===H && typeof ans[1].ct==='string','toggle on: the answer carries the class hash');
  await fake.send({t:'elev_done',id,result:'done',code:0});
  await waitFor(p,`document.getElementById('elev').hidden`);
  // 2. The active approval: a bar with the program and 「收回授权」.
  await fake.send({t:'elev_grants',items:[{g:'0123456789abcdef',exe:'gzip',fmt:'gzip -c -d',dev:'x',left:600}]});
  await waitFor(p,`document.getElementById('elev-grant')?.hidden===false`);
  assert.match(await evaluate(p,`document.getElementById('elev-grant-text').textContent`),lang==='zh'?/gzip 只读命令不再询问.*还剩 10 分钟/:/read-only gzip.*10 min left/);
  assert.equal(await evaluate(p,`document.getElementById('elev-grant-revoke').textContent`),lang==='zh'?'收回授权':'Revoke');
  // 3. A command that ran under it: announced, no card.
  await fake.send({t:'elev_auto',id:'c'.repeat(32),g:'0123456789abcdef',exe:'gzip',result:'done',code:0,left:540});
  await waitFor(p,`document.getElementById('toast').textContent.includes(${JSON.stringify(lang==='zh'?'已按 10 分钟授权自动执行：gzip':'Ran automatically under your 10-minute approval: gzip')})`);
  assert.equal(await evaluate(p,`document.getElementById('elev').hidden`),true);
  assert.ok(await evaluate(p,`(()=>{const b=document.getElementById('elev-grant-revoke').getBoundingClientRect();return document.getElementById('elev-grant-revoke').contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2))})()`),'the revoke button stays tappable while the notice shows');
  writeFileSync(`${out}/web-${lang}-${theme}-${width}-grant.png`,await shoot(p,''));
  // 4. Revoke.
  await evaluate(p,`document.getElementById('elev-grant-revoke').click()`);
  for(let i=0;i<100 && !got.some(m=>m.t==='elev_revoke');i++) await new Promise(r=>setTimeout(r,50));
  assert.ok(got.some(m=>m.t==='elev_revoke'),'revoke reaches the host');
  await waitFor(p,`document.getElementById('elev-grant').hidden`);
  // 5. Red path: a shell / interpreter card never offers the window; Executing after submit, closes on its result.
  const red='e'.repeat(32);
  await fake.send({t:'elev',id:red,kind:'sudo',n:'d'.repeat(32),epk:epk(),ttl:120,tries:3,cmd:"bash -c 'zcat /var/backups/*.gz | wc -l'",why:'Count lines in all backups',effect:'',interpreter:true,grant:GRANT});
  await waitFor(p,`!document.getElementById('elev').hidden && document.getElementById('elev').classList.contains('interpreter')`);
  assert.equal(await evaluate(p,`document.getElementById('elev-grant-opt').hidden`),true,'a red card never offers the toggle');
  await evaluate(p,submit);
  await waitFor(p,`document.getElementById('elev-left').textContent===${JSON.stringify(lang==='zh'?'执行中…':'Executing…')}`);
  ans=await answers(got,3);
  assert.equal(ans[2].grant,undefined,'a red card answer never asks for the window');
  await fake.send({t:'elev_done',id:red,result:'timeout'});
  await waitFor(p,`document.getElementById('elev').hidden`);
  count++;
 }finally{await p.close();}
}
writeFileSync(`${out}/web-result.json`,JSON.stringify({pass:true,combos:count,screenshots:count*2},null,2));
{const rec=parityRecorder('web/test/p393_elevate.mjs');rec.record('p393-elevate',true);rec.write();}
console.log('PASS P393 elevate grant',count,'combinations');
}finally{await B.close();await fake.stop();await web.stop();}
