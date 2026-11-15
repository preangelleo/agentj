// CDP drives Chromium's composition engine; this is not a physical phone/third-party keyboard claim.
import assert from'node:assert/strict';import{mkdirSync,writeFileSync}from'node:fs';import{randomBytes,webcrypto}from'node:crypto';
import{startWebServer}from'./serve.mjs';import{startFakeHost}from'./fakehost.mjs';import{launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep}from'./browser.mjs';
const out=process.env.OLARES_RUN_OUT||'reports/qa/secret-editor-ime';mkdirSync(out,{recursive:true});const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),rows=[];let checks=0;
try{for(const lang of['zh','en'])for(const theme of['light','dark']){
 fake.reset();fake.st.prefs={appearance:{language:lang,theme}};const p=await newPage(B,390,900,theme,{touch:true,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
  const kp=await webcrypto.subtle.generateKey({name:'X25519'},true,['deriveBits']),id=randomBytes(16).toString('hex');
  await fake.send({t:'elev',kind:'secret',id,n:randomBytes(16).toString('hex'),epk:Buffer.from(await webcrypto.subtle.exportKey('raw',kp.publicKey)).toString('base64url'),ttl:120,name:'SYNTHETIC_IME_KEY',purpose:'Synthetic IME boundary',dest:'env:/tmp/synthetic-ime#TEST_KEY',verify:'',button_only:true,passkey_required:false});
  await waitFor(p,`document.getElementById('elev-input')&&!document.getElementById('elev').hidden`);await evaluate(p,`document.getElementById('elev-input').click()`);await waitFor(p,`!document.getElementById('fsEd').hidden`);
  await evaluate(p,`window.__composition=[];document.getElementById('fsText').addEventListener('keydown',e=>window.__composition.push({composing:e.isComposing,keyCode:e.keyCode}));document.getElementById('fsText').focus()`);
  await p.send('Input.imeSetComposition',{text:'合成输入',selectionStart:4,selectionEnd:4});await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});await sleep(100);
  const state=await evaluate(p,`({events:window.__composition,open:!document.getElementById('fsEd').hidden,type:document.getElementById('fsText').type})`);
  assert.ok(state.events.some(x=>x.composing||x.keyCode===229),'actual browser composition boundary observed');assert.equal(state.open,true);assert.equal(state.type,'password');assert.equal(fake.log.filter(m=>m.t==='elev_answer'&&m.id===id).length,0);checks+=4;
  // End composition, then explicitly submit. No secret values are emitted in metadata or screenshots.
  await p.send('Input.insertText',{text:'disposable-IME-canary'});await evaluate(p,`document.getElementById('fsSend').click()`);await waitFor(p,`document.getElementById('fsEd').hidden`);assert.equal(fake.log.filter(m=>m.t==='elev_answer'&&m.id===id&&m.ok&&m.ct).length,1);checks++;
  await fake.send({t:'elev_done',id,result:'saved'});
  for(const [cls,owner]of[['fs-secret-shell','aj-prose'],['ordinary-dialog','aj-editor']]){
   const negative=await evaluate(p,`(async()=>{const d=document.createElement('dialog');d.className=${JSON.stringify(cls)};d.setAttribute('data-aj-owner',${JSON.stringify(owner)});d.textContent='Synthetic geometry negative';document.body.append(d);d.showModal();const m=await import('./js/popup-layout.js');m.layoutPopups([d]);const result={system:d.dataset.popupSystem,hasGeometry:!!d.style.getPropertyValue('--popup-center')};d.close();d.remove();return result})()`);
   assert.equal(negative.system,'center');assert.equal(negative.hasGeometry,true);checks+=2;
  }
  rows.push({lang,theme,compositionObserved:true,earlyAnswers:0,genericGeometryNegatives:2});
 }catch(e){await evaluate(p,`for(const n of document.querySelectorAll('input,textarea'))n.value=''`);writeFileSync(`${out}/${lang}-${theme}-FAIL-cleared.png`,await shoot(p));throw e;}finally{await p.dispose();}
}writeFileSync(out+'/result.json',JSON.stringify({checks,rows,qualification:'Chromium composition engine only; physical IME deferred'},null,2));console.log('secret editor IME '+checks+' PASS');}finally{await B.close();await fake.stop();await web.stop();}
