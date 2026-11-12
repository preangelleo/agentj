#!/usr/bin/env node
// Signed synthetic card through the real client and isolated Noise fixture. No real keys, owner or production relay.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import * as wire from '../../protocol/wire.js';
import {elevateChallenge} from '../public/js/faceid.js';
import {shoot,waitFor} from './browser.mjs';
process.env.P541_LIBRARY='1';
const H=await import('./p541.mjs');
const out=process.env.P0312_OUT||'reports/qa/p0312/';mkdirSync(out,{recursive:true});
const baseline=process.env.P0312_BEFORE==='1';
try {
 for(const language of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  const p=await H.setup(width,width===390?844:900,theme);
  const card=structuredClone(H.currentCard());card.id='31200000000000000000000000000001';card.envelope.request_id=card.id;
  card.envelope.source={...card.envelope.source,channel:'phone',verified:true,suspicious:false,chain:[],root:{verified:true,text:language==='en'?'Set up the test webhook':'设置测试 Webhook'},sender:language==='en'?'You':'你',text:language==='en'?'Set up the test webhook':'设置测试 Webhook'};
  card.envelope.title=language==='en'?'Rotate the test webhook secret':'给测试 Webhook 换密钥';
  card.envelope.action=language==='en'?'Rotate the test webhook secret (synthetic only).':'给测试 Webhook 换密钥（仅测试）。';
  card.envelope.reason=language==='en'?'The new secret is generated on the host; the old one stops working after rotation.':'新密钥由主机生成，换好后旧密钥失效。';
  card.envelope.effects=['hooks'];
  card.envelope.details='agentj hook rotate test-webhook (synthetic, no keys or paths)';
  card.summary=card.envelope.details;
  card.envelope_sha256=await wire.approvalEnvelopeDigest(card.envelope);
  card.fa_ch=wire.b64u(await elevateChallenge(H.fake.channel,H.fake.devs.at(-1).id,card.id,card.pk_kind,card.id,card.envelope_sha256));
  await H.fake.askDone(H.currentCard().id,'deny');
  await H.fake.ask(card);
  await waitFor(p,`document.getElementById('sheetTitle').textContent.includes('${language==='en'?'Rotate':'Webhook'}')`);
  await H.ev(p,`window.AJLang.set('${language}')`);
  await waitFor(p,`document.getElementById('sheetTitle').textContent.includes('${language==='en'?'Rotate':'Webhook'}')`);
  const state=await H.ev(p,`(()=>{const ids=['apprAllow','apprDeny','doubt541c'];return{title:document.getElementById('sheetTitle').textContent,action:document.getElementById('action541').textContent,buttons:ids.map(id=>{const b=document.getElementById(id),r=b.getBoundingClientRect();return{id,x:r.x,y:r.y,w:r.width,h:r.height,label:b.getAttribute('aria-label'),text:b.textContent}}),overflow:document.getElementById('sheet').scrollWidth>document.getElementById('sheet').clientWidth}})()`);
  if(!baseline){
   assert.ok(state.title.length>0);
   assert.ok(state.action.length>0);
   assert.ok(state.buttons.every(b=>b.w>=44&&b.h>=44&&b.label));
   assert.equal(await H.ev(p,`document.querySelector('#apprDeny .appr-icon0312 path')?.getAttribute('d')`),'M6 6 18 18M18 6 6 18');
   assert.deepEqual(await H.ev(p,`(()=>{const sel=['#apprAllow .lbl','#apprDeny span','#apprKbd'];return sel.map(x=>getComputedStyle(document.querySelector(x)).display)})()`),['none','none','none']);
   assert.ok(await H.ev(p,`['apprAllow','apprDeny','doubt541c'].every(id=>{const b=document.getElementById(id);return b.getAttribute('aria-label')&&b.querySelector('.appr-icon0312')?.getBoundingClientRect().width>=24})`));
   assert.ok(state.buttons.every(b=>Math.abs(b.y-state.buttons[0].y)<2),'all actions on one row');
   assert.equal(state.overflow,false);
  }
  const file=`${out}/${baseline?'before':'after'}-${language}-${theme}-${width}.png`;
  writeFileSync(file,await shoot(p));
  writeFileSync(file.replace(/\.png$/,'.json'),JSON.stringify(state,null,2));
  if(!baseline){
   // Legacy (no signed v2 envelope) still uses the visible, labelled controls. This is display-only;
   // no legacy approval is submitted and no backend grant is simulated.
   await H.fake.askDone(card.id,'deny');
   const legacy={t:'ask',id:'31200000000000000000000000000002',tool:'Bash',summary:'echo synthetic test',ttl:600,cat:[],why:'synthetic test',passkey_required:false};
   await H.fake.ask(legacy);
   await waitFor(p,`document.body.dataset.sheet==='1'&&!document.getElementById('sheet').hasAttribute('data-approval-v')&&document.getElementById('apprAllow').getBoundingClientRect().width>0`);
   const visible=await H.ev(p,`(()=>{const ids=['#apprAllow .lbl','#apprDeny span'];return ids.map(q=>{const e=document.querySelector(q);return {display:getComputedStyle(e).display,rect:e.getBoundingClientRect().width,text:e.textContent}})})()`);
   assert.ok(visible.every(v=>v.display!=='none'&&v.rect>0&&v.text.trim()),'legacy approve/deny labels stay visible');
   assert.equal(await H.ev(p,`getComputedStyle(document.querySelector('#apprAllow .appr-icon0312')).display`),'none');
   assert.equal(await H.ev(p,`getComputedStyle(document.querySelector('#apprDeny .appr-icon0312')).display`),'none');
  }
  await p.dispose();
 }
}finally{await H.B.close();await H.fake.stop();await H.web.stop();}
console.log(`p0312 ${baseline?'before':'after'}: 8 synthetic signed card screenshots`);
