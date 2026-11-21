import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, tap } from './browser.mjs';
import { approvalEnvelopeDigest, approvalV2Message, unb64u } from '../../protocol/wire.js';

const out=process.env.AJ_PLAZA_CARD_OUT||'reports/qa/plaza-install-reason';
const before=process.env.AJ_PLAZA_CARD_BEFORE;
mkdirSync(out,{recursive:true});
const web=await startWebServer({publicDir:before||undefined}),fake=await startFakeHost(),browser=await launch();
const packageName='color-of-the-year-palette',version='1.2.0';
const sha256=createHash('sha256').update('Synthetic Plaza package').digest('hex');
const q=JSON.stringify,rows=[];
const cases=[];
for(const width of [390,1440])for(const language of ['zh','en'])for(const theme of ['light','dark']){
 cases.push({width,language,theme,origin:'direct'});
 if(!before&&width===390)for(const origin of ['same-owner','unproved','friend','bad-digest'])cases.push({width,language,theme,origin});
}

try {
 for(const {width,language,theme,origin} of cases){
  const direct=origin==='direct',sameOwner=origin==='same-owner',badDigest=origin==='bad-digest';
  const zh=language==='zh',request=zh?`装 ${packageName}`:`Install ${packageName}`;
  const expected=before||badDigest?(zh?'此确认请求未附独立理由字段':'This confirmation request contains no separate reason'):
   zh?`${direct?'你刚才请求从广场安装':'请确认安装广场包'} ${packageName} ${version}（Agent J 官方签名已校验）。摘要 SHA-256 ${sha256.slice(0,12)}…`:
    direct?`You just asked to install ${packageName} ${version} (Agent J signature verified) from Plaza. SHA-256 ${sha256.slice(0,12)}…`:
     `Confirm the Plaza installation of ${packageName} ${version} (Agent J signature verified). SHA-256 ${sha256.slice(0,12)}…`;
  fake.reset();fake.setUp(true);fake.st.prefSet=true;
  fake.st.prefs={appearance:{language,theme},voice:{speak_replies:false,wake_enabled:false}};
  const p=await newPage(browser,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+language));
   await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await evaluate(p,`for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click();}`);
   await fake.addTurn({k:'phone',name:zh?'你':'You',text:request},'');
   const id='03063000000000000000000000000001';
   // The v2 wire shape comes from the Host's Plaza `_card` plus `list_approval` projection. Values are synthetic.
   const details='agentj plaza\n'+q({package:packageName,version,certified:true,sha256,digest:'b548c766b2a09d01',
    publisher:'c'.repeat(64),target:`~/.claude/skills/${packageName}`,items:[],summary:zh?'Agent J 官方签名，已校验':'Agent J official signature, verified'});
   const source=direct?{channel:'phone',verified:true,suspicious:false,text:request,source_trust:'owner',source_type:'owner_remote',
    message_id:1,trigger_message_id:1,root_message_id:1}:{channel:'unknown',verified:false,suspicious:true,source_trust:'external',
    source_type:origin==='friend'?'friend':'same_owner',source_label:origin==='friend'?'好友 测试机':'同主人设备 测试机',
    source_label_en:origin==='friend'?'Friend fixture':'Same-owner device fixture',
    verified_same_owner:origin!=='unproved'};
   const envelope={v:2,channel:fake.channel,session:String(fake.st.epoch),request_id:id,revision:1,nonce:'test-only',
    title:request,action:'agentj plaza',details,source,effects:[],plan:null,expires_at:Math.floor(Date.now()/1000)+120};
   const card={id,tool:'agentj plaza',summary:details,ttl:120,cat:[],approval_v:2,envelope,
    envelope_sha256:badDigest?'0'.repeat(64):await approvalEnvelopeDigest(envelope),revision:1,review_state:'pending'};
   let accepted=false;
   fake.st.onApp=async(c,m,send)=>{
    if(m.t!=='answer')return false;
    const dev=fake.devs.find(d=>d.id===c.devId);
    const key=await webcrypto.subtle.importKey('raw',unb64u(dev.sk),{name:'Ed25519'},false,['verify']);
    accepted=await webcrypto.subtle.verify('Ed25519',key,unb64u(m.sig),await approvalV2Message(fake.channel,c.devId,{...card,envelope_verified:true},'allow'));
    await send({t:'ask_done',id,result:accepted?'allow':'deny'});
    return true;
   };
   await fake.ask(card);
   await waitFor(p,badDigest?`document.getElementById('sheet').dataset.approvalV==='2'`:
    `import('./js/snap.js').then(m=>m.S.asks.get(${q(id)})?.envelope_verified===true)`);
   const dom=await evaluate(p,`(()=>{
    const line=document.getElementById('action541'),r=line.getBoundingClientRect();
    return {reason:line.textContent,visible:r.width>0&&r.height>0&&r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,
     detailsOpen:document.getElementById('details541').open,allowDisabled:document.getElementById('apprAllow').disabled,
     warningHidden:document.getElementById('warn541').hidden,extraHidden:document.getElementById('approvalSource572').hidden,
     source:document.getElementById('source541').textContent};
   })()`);
   if(before||badDigest)assert.ok(dom.reason.startsWith(expected),dom.reason);
   else assert.equal(dom.reason,expected);
   assert.ok(dom.visible&&!dom.detailsOpen);
   assert.equal(dom.allowDisabled,badDigest);
   if(!direct){
    assert.equal(dom.warningHidden,sameOwner);
    assert.equal(dom.extraHidden,sameOwner);
    if(sameOwner)assert.equal(dom.source,zh?'同主人设备 测试机':'Same-owner device fixture');
    if(badDigest){rows.push({origin,width,language,theme,approvalRejected:true});continue;}
   }
   await sleep(400);
   const icons=await evaluate(p,`['apprAllow','doubt541c','apprDeny'].map(id=>{
    const node=document.querySelector('#'+id+' svg');
    return node?{size:node.getBoundingClientRect().width,color:getComputedStyle(node).stroke}:null;
   })`);
   const path=join(out,`plaza-install-${width}-${language}-${theme}${direct?'':'-'+origin}.png`);
   writeFileSync(path,await shoot(p));
   const point=await evaluate(p,`(()=>{const r=document.getElementById('apprAllow').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
   await tap(p,point.x,point.y);
   await waitFor(p,`import('./js/snap.js').then(m=>m.S.lastAsk?.id===${q(id)}&&m.S.lastAsk?.state==='approved')`);
   assert.ok(accepted);
   rows.push({origin,width,language,theme,reason:dom.reason,path,icons,signatureVerified:accepted});
  } finally {await p.dispose();}
 }
 console.log(`PASS Plaza install reason (${before?'baseline':'candidate'}), ${rows.length} scenes; ${rows.filter(row=>row.signatureVerified).length} paired Ed25519 decisions`);
} finally {
 writeFileSync(join(out,'browser.json'),JSON.stringify({physical:false,rows},null,2));
 await browser.close();await fake.stop();await web.stop();
}
