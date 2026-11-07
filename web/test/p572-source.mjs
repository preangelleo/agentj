#!/usr/bin/env node
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
import * as wire from '../../protocol/wire.js';
const OUT=process.env.P572_OUT||process.cwd()+'/reports/qa/572/web';mkdirSync(OUT,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),checks=[];
const check=(v,n)=>{checks.push({name:n,pass:!!v});assert.ok(v,n);};let serial=0;
const mk=async(v2=true)=>{const id=(++serial).toString(16).padStart(32,'0'),envelope={v:2,channel:fake.channel,request_id:id,revision:1,nonce:'source-572-'+serial,details:'rm -- exports/old-summary.csv',source:{root:{text:'请整理上个月的导出，保留本月数据。'},text:'已核对，清理上个月的旧文件。',root_message_id:42,message_id:45,verified:true,chain:['主人 · 手机','贾维斯','掌枢','后台任务']},action:'永久删除上个月的一份旧导出，需要你确认。',action_en:'Permanently delete one old export; your confirmation is required.'};return {t:'ask',id,tool:'Bash',summary:envelope.details,ttl:600,cat:['delete'],why:'删除后无法撤回',reason:envelope.action,...(v2?{approval_v:2,envelope,envelope_sha256:await wire.approvalEnvelopeDigest(envelope),revision:1}:{})};};
let received=[];fake.st.onApp=async(c,m)=>{if(m.t==='answer'){received.push({c,m});return true;}return false;};
try{for(const width of [390,1440])for(const theme of ['light','dark']){
 fake.reset();received=[];const p=await newPage(B,width,width<500?844:900,theme,{allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click()}`);
 const act=id=>evaluate(p,`(async()=>{const s=await import('./js/snap.js'),a=await import('./js/api.js');return a.approve(s.S.asks.get('${id}'),'deny')})()`);
 const legacy=await mk(false);await fake.ask(legacy);await waitFor(p,`document.getElementById('approvalSource572').textContent.includes('当前 Agent J 主机')`);
 writeFileSync(OUT+`/${width}-${theme}-before.png`,await shoot(p));
 const r=await act(legacy.id);await sleep(100);check(r.ok&&received.length===1&&received[0].m.approval_v===undefined,`${width}/${theme}: legacy v1 stays v1`);
 const dev=fake.devs.find(d=>d.id===received[0].c.devId),key=await webcrypto.subtle.importKey('raw',wire.unb64u(dev.sk),'Ed25519',false,['verify']);
 check(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(received[0].m.sig),await wire.approveMessage(fake.channel,dev.id,legacy.id,'deny',legacy.tool,legacy.summary)),`${width}/${theme}: legacy signature valid`);
 await fake.send({t:'ask_done',id:legacy.id,result:'deny'});
 const card=await mk();await fake.ask(card);await waitFor(p,`(async()=>{const s=await import('./js/snap.js');return s.S.asks.get('${card.id}')?.envelope_verified===true})()`);await sleep(100);
 const text=await evaluate(p,`document.getElementById('approvalSource572').textContent`);
 check(text.includes('请整理上个月')&&text.includes('来源链')&&text.includes('掌枢')&&text.includes('永久删除'),`${width}/${theme}: root chain reason shown`);
 check(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth`),`${width}/${theme}: no horizontal overflow`);
 writeFileSync(OUT+`/${width}-${theme}-after.png`,await shoot(p));
 const n=received.length,r2=await act(card.id);await sleep(100);check(r2.ok&&received.length===n+1&&received.at(-1).m.approval_v===2,`${width}/${theme}: v2 no downgrade`);
 check(await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(received.at(-1).m.sig),await wire.approvalV2Message(fake.channel,dev.id,{...card,envelope_verified:true},'deny')),`${width}/${theme}: source bound signature valid`);
 const altered=structuredClone(card);altered.envelope.source.root.text='伪造原话';altered.envelope_sha256=await wire.approvalEnvelopeDigest(altered.envelope);
 check(!await webcrypto.subtle.verify('Ed25519',key,wire.unb64u(received.at(-1).m.sig),await wire.approvalV2Message(fake.channel,dev.id,{...altered,envelope_verified:true},'deny')),`${width}/${theme}: changed source rejects signature`);
 await fake.send({t:'ask_done',id:card.id,result:'deny'});
 for(const shape of ['unmarked','digest','future','minimum']){const bad=await mk();if(shape==='unmarked')delete bad.approval_v;if(shape==='digest')bad.envelope.source.root.text='tampered';if(shape==='future')bad.approval_v=3;if(shape==='minimum'){delete bad.approval_v;delete bad.envelope;delete bad.envelope_sha256;bad.minimum_approval_protocol=2;}await fake.ask(bad);await sleep(100);const before=received.length,r=await act(bad.id);check(!r.ok&&received.length===before,`${width}/${theme}: ${shape} refuses signature`);await fake.send({t:'ask_done',id:bad.id,result:'deny'});}
 await p.dispose();
}}finally{await B.close();await fake.stop();await web.stop();writeFileSync(OUT+'/validation.json',JSON.stringify({checks},null,2));}
console.log(JSON.stringify({checks:checks.length,passed:checks.filter(x=>x.pass).length}));
