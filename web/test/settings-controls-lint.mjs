#!/usr/bin/env node
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch,newPage,navigate,waitState,evaluate,sleep,shoot,waitFor } from './browser.mjs';
import { auditSettings } from './settings-style-audit.mjs';
const OUT=process.env.P517_OUT || fileURLToPath(new URL('../../../reports/qa/asr-settings-ui-517-20261106/engineering/',import.meta.url));
mkdirSync(OUT,{recursive:true});
const web=await startWebServer(), fake=await startFakeHost(), B=await launch();
const results=[];
const SEARCH_ONLY=process.env.P517_SEARCH_ONLY==='1';
const audit=p=>evaluate(p,`(${auditSettings.toString()})()`);
try {
 for(const width of (SEARCH_ONLY?[390]:[390,1440])) for(const theme of (SEARCH_ONLY?['dark']:['light','dark'])) {
  fake.reset();fake.st.prefSet=true;fake.st.runCmds=['doctor'];fake.st.notify={automations:[],tasks:[]};fake.st.extraCaps=['contacts','jcards1','hooks1','externalmcp1'];fake.st.platform='macos';
  fake.remotes({limit:10,items:[{id:'remote-test',name:'Test remote',online:false,paired_at:1}],can_add:true});
  fake.st.mail={config:{enabled:false,preset:'custom',address:'agent@example.invalid',username:'',imap:{host:'imap.example.invalid',port:993,security:'ssl'},smtp:{host:'smtp.example.invalid',port:465,security:'ssl'},poll_minutes:5,auth:'standard',auth_servid:'',allow:[{addr:'fixture@example.invalid',role:'guest'}],reply_per_hour:20},has_password:false,presets:[{id:'custom',name:'Custom',domains:[],imap:{host:'',port:993,security:'ssl'},smtp:{host:'',port:465,security:'ssl'}}],blocked:[],today:{}};
  fake.st.onApp=async(c,m,send)=>{ if(m.t==='hook_list'){await send({t:'hook_list_res',r:m.r,ok:true,up:true,hooks:[],recent:[],connectors:[]});return true} if(m.t==='ct_list'){await send({t:'ct_res',r:m.r,ok:true,contacts:[],count:0,me:[],owner:null,mail:{ready:false}});return true} if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:[],colors:{}});return true} return false; };
  const p=await newPage(B,width,1000,theme,{ua:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1',allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'preferences',v:{voice:{asr:{mode:'custom',base_url:'https://example.invalid/v1',model:'test',key_env:'',hotwords:['Agent J']}}},host:{version:'0.26.12',settable:['voice.asr.mode']}});
   await evaluate(p,`document.getElementById('setBtn').click()`);await sleep(200);
   // Visit real renderers so dynamic controls join the same all-DOM class lint.
   for(const sub of (SEARCH_ONLY?['mail','auth','main']:['main','mail','auth','accts','files','imessage','cards','notify','hooks','contacts','ios','remotes','main'])) {
    await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);
    const nav={mail:'set-mail-nav',auth:'set-auth-nav',accts:'set-accts-nav',files:'set-local-files-nav',imessage:'set-imsg-nav',cards:'set-cards-nav',notify:'set-notify-nav',hooks:'set-hooks-nav',contacts:'set-contacts-nav',ios:'set-a2hs',remotes:'set-remotes-open'}[sub];
    if(nav) await evaluate(p,`document.getElementById('${nav}').click()`);
    await sleep(250);
    if(sub==='remotes') { await waitFor(p,`!!document.querySelector('#rm-list [data-act="rename"]')`); await evaluate(p,`document.querySelector('#rm-list [data-act="rename"]').click()`); await waitFor(p,`!!document.querySelector('#rm-list .rmrename .aj-input')`); }
    if(sub==='hooks') await waitFor(p,`!!document.getElementById('mcp474-name')`);
    if(sub==='mail') await waitFor(p,`!!document.querySelector('#mail-allow select.aj-select.mail-role')`);
    const r=await audit(p);results.push({width,theme,sub,...r});
    if(['mail','auth'].includes(sub)) {
     for(const phase of ['search-open','search-reopen']) {
      await evaluate(p,`import('./js/settings.js').then(m=>m.toggleSearch())`);
      await waitFor(p,`!!document.querySelector('#set-${sub}>.pfind:not([hidden]) input')`);
      assert.equal(await evaluate(p,`document.querySelectorAll('#set-${sub}>.pfind input').length`),1,'search reopening reuses one field');
      results.push({width,theme,sub:sub+'-'+phase,...await audit(p)});
      await evaluate(p,`import('./js/settings.js').then(m=>m.closeSearch())`);
      assert.equal(await evaluate(p,`document.querySelector('#set-${sub}>.pfind').hidden`),true,'subpage search closes');
     }
    }
   }
   await evaluate(p,`document.getElementById('set-asr').hidden=false;document.querySelectorAll('#set-asr [hidden]').forEach(e=>e.hidden=false);document.getElementById('set-asr').scrollIntoView({block:'center'})`);
   // P537 still forbids an inline multiline editor, including authored ASR fields.
   // Verify the real watchSettings conversion and shared editor, rather than banning textarea source markup.
   await waitFor(p,`document.getElementById('set-asr-hotwords').type==='hidden'`);
   assert.equal(await evaluate(p,`document.querySelectorAll('#settings textarea').length`),0,'all settings multiline fields use the shared editor');
   const draft='Agent J\nOlares',previous=await evaluate(p,`document.getElementById('set-asr-hotwords').value`);
   await evaluate(p,`document.querySelector('#set-asr-hotwords + .text-editor-field').click()`);
   await waitFor(p,`!document.getElementById('fsEd').hidden`);
   assert.equal(await evaluate(p,`document.getElementById('fsText').value`),previous,'the hotword launcher reads its multiline value');
   await evaluate(p,`document.getElementById('fsText').value='cancelled';document.getElementById('fsExit').click()`);
   assert.equal(await evaluate(p,`document.getElementById('set-asr-hotwords').value`),previous,'closing leaves the ASR value unchanged');
   await evaluate(p,`document.querySelector('#set-asr-hotwords + .text-editor-field').click()`);
   await waitFor(p,`!document.getElementById('fsEd').hidden`);
   await evaluate(p,`document.getElementById('fsText').value=${JSON.stringify(draft)};document.getElementById('fsSend').click()`);
   await waitFor(p,`document.getElementById('fsEd').hidden`);
   assert.equal(await evaluate(p,`document.getElementById('set-asr-hotwords').value`),draft,'saving preserves multiline hotwords');
   assert.deepEqual(await evaluate(p,`import('./js/asr-settings.js').then(m=>m.readAsr().hotwords)`),['Agent J','Olares'],'the ASR save reads both lines from the shared editor');
   const r=await audit(p);results.push({width,theme,sub:'asr-custom',...r});
   writeFileSync(join(OUT,`${theme}-${width}-asr.png`),await shoot(p,''));
   // The same lint rejects both regressions, including a dynamically appended control.
   await evaluate(p,`document.getElementById('set-asr').insertAdjacentHTML('beforeend','<button id="p517-negative">bad</button>')`);
   assert.ok((await audit(p)).errors.some(s=>s.includes('p517-negative')),'dynamic bare button must fail');
   await evaluate(p,`document.getElementById('p517-negative').remove();document.getElementById('set-asr-mode').classList.remove('aj-select')`);
   assert.ok((await audit(p)).errors.some(s=>s.includes('set-asr-mode')),'bare select must fail');
   await evaluate(p,`document.getElementById('set-asr-mode').classList.add('aj-select');document.getElementById('set-asr').style.marginTop='0'`);
   assert.ok((await audit(p)).errors.some(s=>s.startsWith('section-seam:')),'missing section gap must fail');
   await evaluate(p,`document.getElementById('set-asr').style.removeProperty('margin-top');document.querySelector('#set-asr h4').classList.remove('setg')`);
   assert.ok((await audit(p)).errors.some(s=>s.startsWith('section-component:')),'unclassed semantic section title must fail');
   await evaluate(p,`document.querySelector('#set-asr h4').classList.add('setg');document.getElementById('set-asr').style.setProperty('--space-section','0px')`);
   assert.ok((await audit(p)).errors.some(s=>s.startsWith('section-token:')),'reduced token must fail');
   await evaluate(p,`document.getElementById('set-asr').style.removeProperty('--space-section');document.getElementById('set-asr').insertAdjacentHTML('beforeend','<button id="p517-fake" class="setsegment">bad</button>')`);
   assert.ok((await audit(p)).errors.some(s=>s.includes('p517-fake')),'fake specialised component without its structure must fail');
   await evaluate(p,`document.getElementById('p517-fake').remove();document.getElementById('set-asr-mode').style.borderRadius='0px'`);
   assert.ok((await audit(p)).errors.some(s=>s.startsWith('square-control:')),'square ID/style override must fail');
   await evaluate(p,`document.getElementById('set-asr-mode').style.removeProperty('border-radius');document.getElementById('set-asr-save').style.borderRadius='0px'`);
   assert.ok((await audit(p)).errors.some(s=>s.startsWith('square-control:')&&s.includes('set-asr-save')),'square generic button override must fail');
   await evaluate(p,`document.getElementById('set-asr-save').style.removeProperty('border-radius');document.getElementById('set-asr').style.marginTop='23.9px'`);
   assert.ok((await audit(p)).errors.some(s=>s.startsWith('section-seam:')),'23.9px is below the fixed minimum');
   await evaluate(p,`document.getElementById('set-asr').style.removeProperty('margin-top');document.querySelector('#set-asr h4').style.transform='translateY(-25px)'`);
   assert.ok((await audit(p)).errors.some(s=>s.startsWith('section-seam:')),'heading crossing nearest seam must fail with signed gap');
   await evaluate(p,`document.querySelector('#set-asr h4').style.removeProperty('transform')`);
  }finally{await p.dispose()}
 }
 writeFileSync(join(OUT,'style-audit.json'),JSON.stringify(results,null,2));
 const errors=results.flatMap(r=>r.errors.map(e=>`${r.width}/${r.theme}/${r.sub}: ${e}`));
 assert.equal(errors.length,0,errors.join('\n'));
 console.log(`PASS settings-style-lint: ${results.length} rendered scenes; missing component, dynamic button and section-gap negative controls rejected`);
}finally{await B.close();await fake.stop();await web.stop()}
