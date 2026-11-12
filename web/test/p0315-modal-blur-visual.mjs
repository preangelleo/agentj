#!/usr/bin/env node
// P0315: real local web client, isolated Chromium and synthetic Noise host. The "sent" receipt is a fake host response;
// NO SMTP service, real account, or external network is contacted. Only four named dialog scenarios are photographed.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';

const OUT=join(homedir(),'coding/.agentj/outbox/0315-int');
const scenarios=['forward-email','email-sent','owner-contact','upgrade-confirm'];
const widths=[390,768,1280], themes=['light','dark'];
const id=()=>randomBytes(16).toString('hex');
const J=JSON.stringify;
const web=await startWebServer(), fake=await startFakeHost(), B=await launch();
let count=0;
try {
 mkdirSync(OUT,{recursive:true});
 for(const width of widths)for(const theme of themes){
  fake.reset();fake.st.caps=['contacts'];fake.st.prefs={appearance:{language:'zh',theme}};
  const forwarded=[];
  fake.st.onApp=async(_conn,m,reply)=>{
   if(m.t==='ct_list'){
    await reply({t:'ct_res',r:m.r,ok:true,contacts:[],count:0,me:['owner@example.com'],mail:{ready:true}});return true;
   }
   if(m.t==='mail_fwd'){
    // A synthetic result, not an SMTP operation. Never relay the message to any network service.
    forwarded.push({turn:m.turn,to:m.to});
    await reply({t:'mail_fwd_res',r:m.r,ok:true,sent:1,files:0,skips:[]});return true;
   }
   return false;
  };
  const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang=zh'));
   await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=${J(theme)}`);
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await fake.addTurn({k:'phone',text:'请总结这段合成演示'},'## 本地演示\n已完成测试内容。');
   await waitFor(p,`!document.getElementById('mailBtn').hidden`);
   // Sample the actual composited hit-test at header and composer. A backdrop confined to #main fails this check.
   const capture=async(scene,sel)=>{
    await waitFor(p,`(()=>{const n=document.querySelector(${J(sel)});return n&&!n.hidden&&n.getClientRects().length>0})()`);
    await sleep(240);
    const check=await evaluate(p,`(()=>{
      const n=document.querySelector(${J(sel)}),r=n.getBoundingClientRect(),cs=getComputedStyle(n),veil=getComputedStyle(n,'::before');
      const hit=(x,y)=>{const e=document.elementFromPoint(x,y);return !!e&&(e===n||n.contains(e));};
      const hdr=document.querySelector('#setBtn')||document.querySelector('header');
      const comp=document.querySelector('.composer');
      const point=e=>{if(!e)return null;const b=e.getBoundingClientRect();return [Math.max(2,Math.min(innerWidth-2,b.left+b.width/2)),Math.max(2,Math.min(innerHeight-2,b.top+b.height/2))]};
      const a=point(hdr),b=point(comp);
      return {visible:!n.hidden,viewport:veil.position==='fixed'&&veil.top==='0px'&&veil.right==='0px'&&veil.bottom==='0px'&&veil.left==='0px',
        headerCovered:a&&hit(...a),composerCovered:b&&hit(...b),headerPoint:a,composerPoint:b,
        backdrop:veil.backdropFilter||veil.webkitBackdropFilter,overflow:document.documentElement.scrollWidth>innerWidth};
    })()`);
    assert.ok(check.visible&&check.viewport&&check.headerCovered&&check.composerCovered&&!check.overflow,`${scene} ${width} ${theme} full viewport: ${J(check)}`);
    assert.match(check.backdrop,/blur\([1-9]/,`${scene} needs backdrop blur`);
    assert.deepEqual(p.offsite,[],'no off-site requests');
    assert.deepEqual(p.problems,[],'no browser errors');
    const path=join(OUT,`modal-blur-${scene}-${width}-${theme}.png`);
    writeFileSync(path,await shoot(p));count++;
    console.log('PASS',scene,width,theme,path);
   };
   // The same entry as the reply card mail button; never click Send for the first capture.
   await evaluate(p,`document.getElementById('mailBtn').click()`);
   await waitFor(p,`!document.getElementById('ctFwd').hidden&&!document.getElementById('ctFwd-send').disabled`);
   await capture('forward-email','#ctFwd');
   assert.equal(forwarded.length,0,'forward scene never sends');
   // Click Send only against the in-process fake host; its response creates the authentic receipt UI.
   await evaluate(p,`document.getElementById('ctFwd-send').click()`);
   await waitFor(p,`!!document.getElementById('ctFwd-to')`);
   assert.equal(forwarded.length,1,'exactly one fake-host-only forward request');
   assert.equal(forwarded[0].to[0].addr,'owner@example.com');
   await capture('email-sent','#ctFwd');
   await evaluate(p,`document.getElementById('ctFwd-cancel').click()`);
   // A host-issued contact editor, not a fabricated DOM card.
   const cid=id();await fake.send({t:'ct_card',id:cid,n:id(),mode:'owner',contact:{emails:[{v:'owner@example.com',label:''}]},base:null,why:'',ttl:600,proposed:'agent'});
   await capture('owner-contact','#ctCard');
   await evaluate(p,`document.getElementById('ctCard-no').click()`);
   await fake.send({t:'ct_card_done',id:cid,result:'declined'});
   await waitFor(p,`document.getElementById('ctCard').hidden`);
   // A second host-issued modal; do not click its approval action.
   await fake.upgCard({id:id(),items:[{k:'shared_turn',n:1}],from:'0.26.8',to:'0.26.9',ttl:600});
   await capture('upgrade-confirm','#confirm');
   await evaluate(p,`document.getElementById('confirm-no').click()`);
  }finally{await p.dispose();}
 }
 assert.equal(count,scenarios.length*widths.length*themes.length);
 console.log(`P0315 modal blur: ${count} synthetic local screenshots; no external mail`);
}finally{await B.close();await fake.stop();await web.stop();}
