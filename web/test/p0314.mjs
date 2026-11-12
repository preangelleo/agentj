// P0314: fresh owner verification is mandatory on UV cards, including agentj unlink.
// Fake Noise host and isolated browser; screenshots crop the approval sheet (never pairing URLs).
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,sleep} from './browser.mjs';
const OUT=resolve(process.env.P0314_OUT || 'reports/qa/p0314');
const phase=process.env.P0314_PHASE==='before'?'before':'after';
const run=async()=>{
 mkdirSync(OUT,{recursive:true});
 const web=await startWebServer({publicDir:process.env.P0314_PUBLIC ? resolve(process.env.P0314_PUBLIC)+'/' : undefined});
 const fake=await startFakeHost(), B=await launch();
 try {
  for(const lang of ['zh','en']) for(const width of [390,1440]) for(const theme of ['light','dark']) {
   fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
   const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
   try {
    await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
    await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
    const id='a'.repeat(32);
    await fake.ask({t:'ask',id,tool:'agentj unlink',summary:'agentj unlink',ttl:300,cat:[],why:'',passkey_required:true,fa:'Y3JlZC0x',fa_ch:Buffer.alloc(32,7).toString('base64url'),fa_s:'local'});
    await waitFor(p,`!document.getElementById('sheet').hidden && !document.getElementById('apprAllow').hidden`);
    // No WebAuthn API means no assertion can be obtained. The card must explain setup before any Allow.
    await evaluate(p,`window.__pk=PublicKeyCredential;window.PublicKeyCredential=undefined;document.getElementById('sheetClose').click()`);
    await waitFor(p,`document.getElementById('sheet').hidden`);
    await evaluate(p,`document.getElementById('pending541').click()`);
    await waitFor(p,`document.body.dataset.sheet==='1'`);
    const state=await evaluate(p,`({allow:document.getElementById('apprAllow').hidden,go:!document.getElementById('apprGo').hidden,note:document.getElementById('apprPk').textContent,deny:!document.getElementById('apprDeny').hidden})`);
    if(phase==='after'){
     assert.ok(state.allow,'UV Allow hidden when passkey assertion unavailable');
     assert.ok(state.go && state.deny,'setup and deny remain available');
     assert.match(state.note,lang==='zh'?/设置/:/set up/i);
     assert.equal((fake.st.answers||[]).filter(a=>a.id===id).length,0,'no signed Allow sent');
    }
    await sleep(160);
    const box=await evaluate(p,`(()=>{const r=document.getElementById('sheet').getBoundingClientRect();return {x:Math.max(0,r.x),y:Math.max(0,r.y),width:Math.min(innerWidth,r.right)-Math.max(0,r.x),height:Math.min(innerHeight,r.bottom)-Math.max(0,r.y),scale:1}})()`);
    if(box.width>0&&box.height>0){const {data}=await p.send('Page.captureScreenshot',{format:'png',clip:box});writeFileSync(resolve(OUT,`${phase}-${lang}-${width}-${theme}.png`),Buffer.from(data,'base64'));}
    // Restoring WebAuthn offers Allow, but a rejected fresh assertion must send nothing.
    await evaluate(p,`window.PublicKeyCredential=window.__pk;window.__gets=0;navigator.credentials.get=()=>{window.__gets++;return Promise.reject(new DOMException('cancelled','NotAllowedError'))};document.getElementById('sheetClose').click()`);
    await waitFor(p,`document.getElementById('sheet').hidden`);
    await evaluate(p,`document.getElementById('pending541').click()`);
    await waitFor(p,`document.body.dataset.sheet==='1' && !document.getElementById('apprAllow').hidden`);
    await evaluate(p,`document.getElementById('apprAllow').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}))`);
    await waitFor(p,`window.__gets>0`);
    if(phase==='after') assert.equal((fake.st.answers||[]).filter(a=>a.id===id).length,0,'cancelled biometric never sends Allow');
    assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
    console.log('P0314',phase,lang,width,theme,state.allow?'blocked':'exposed');
   }finally{await p.dispose();}
  }
 }finally{await B.close();await fake.stop();await web.stop();}
};
if(process.argv[1]===fileURLToPath(import.meta.url)) await run();
export {run};
