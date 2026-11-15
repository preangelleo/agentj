// Actual Host-generated approval display -> real phone DOM -> Ed25519 answer, isolated Noise transport.
import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,tap} from './browser.mjs';
import {approvalEnvelopeDigest,approvalV2Message,unb64u} from '../../protocol/wire.js';
const seed=JSON.parse(readFileSync(process.env.AJ_UNINSTALL_CARD_FILE,'utf8'));
const out=process.env.OLARES_RUN_OUT||'reports/qa/plaza-uninstall-phone';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),browser=await launch();
const rows=[];let card,accepted=0;
try {
 for(const lang of ['zh','en'])for(const theme of ['light','dark']) {
  fake.reset();fake.setUp(true);fake.st.prefSet=true;
  fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false}};
  const p=await newPage(browser,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await evaluate(p,`for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click();}`);
   card=structuredClone(seed);card.id='03050000000000000000000000000001';card.ttl=600;card.passkey_required=false;
   for(const k of ['fa','fa_ch','fa_s','pk_kind'])delete card[k];
   Object.assign(card.envelope,{channel:fake.channel,session:String(fake.st.epoch),request_id:card.id,revision:1,expires_at:Math.floor(Date.now()/1000)+600});
   card.envelope.source.session=String(fake.st.epoch);card.revision=1;
   card.envelope_sha256=await approvalEnvelopeDigest(card.envelope);
   fake.st.onApp=async(c,m,send)=>{
    if(m.t!=='answer')return false;
    const dev=fake.devs.find(d=>d.id===c.devId),key=await webcrypto.subtle.importKey('raw',unb64u(dev.sk),{name:'Ed25519'},false,['verify']);
    assert.equal(await webcrypto.subtle.verify('Ed25519',key,unb64u(m.sig),await approvalV2Message(fake.channel,c.devId,{...card,envelope_verified:true},m.ok?'allow':'deny')),true);
    accepted++;await send({t:'ask_done',id:card.id,result:m.ok?'allow':'deny'});return true;
   };
   await fake.ask(card);
   await waitFor(p,`document.querySelector('#sheetTitle').textContent.includes(${JSON.stringify(lang==='en'?'Uninstall':'卸载')})`);
   const dom=await evaluate(p,`(async()=>({title:document.getElementById('sheetTitle').textContent,action:document.getElementById('action541').textContent,yes:document.getElementById('apprAllowLbl').textContent,no:document.getElementById('apprDeny').textContent,enabled:!document.getElementById('apprAllow').disabled,verified:[...((await import('./js/snap.js')).S.asks.values())].some(x=>x.envelope_verified===true)}))()`);
   assert.ok(dom.verified&&dom.enabled);
   assert.ok(dom.action.includes(lang==='en'?'recoverable backup':'可恢复备份'));
   assert.ok(dom.action.includes(lang==='en'?'publisher trust':'发布方信任'));
   assert.ok(dom.action.includes(lang==='en'?'installation history':'安装历史'));
   assert.ok(!/^(安装|Install(?:\s|$))/i.test(dom.title));
   assert.ok(dom.yes.trim()&&dom.no.trim());assert.ok(!/Install|安装/.test(dom.yes));
   writeFileSync(`${out}/uninstall-${lang}-${theme}.png`,await shoot(p));rows.push({lang,theme,dom});
   const before=accepted;
   const point=await evaluate(p,`(()=>{const r=document.getElementById('apprAllow').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
   await tap(p,point.x,point.y);
   await waitFor(p,`import('./js/snap.js').then(m=>m.S.lastAsk?.id==='03050000000000000000000000000001'&&m.S.lastAsk?.state==='approved')`);
   assert.equal(accepted,before+1);
   const data=JSON.parse(card.envelope.details.slice('agentj plaza\n'.length));
   data.operation='install';data.note='operation=uninstall';card.id='03050000000000000000000000000002';
   card.envelope.request_id=card.id;card.envelope.nonce+='-negative';
   card.envelope.details='agentj plaza\n'+JSON.stringify(data);card.summary=card.envelope.details;
   card.envelope_sha256=await approvalEnvelopeDigest(card.envelope);
   await fake.ask(card);
   await waitFor(p,`import('./js/snap.js').then(m=>m.S.asks.get('03050000000000000000000000000002')?.envelope_verified===true)`);
   assert.notEqual(await evaluate(p,`document.getElementById('sheetTitle').textContent`),lang==='en'?'Uninstall plaza package':'卸载广场包','body text cannot select uninstall projection');
   await fake.send({t:'ask_done',id:card.id,result:'deny'});
  } finally {await p.dispose();}
 }
 console.log('PASS plaza uninstall phone DOM: 4 language/theme scenes, 4 verified Ed25519 answers');
} finally {writeFileSync(`${out}/phone-result.json`,JSON.stringify({physical:false,host_generated_display:true,transport_rebound:true,rows},null,2));await browser.close();await fake.stop();await web.stop();}
