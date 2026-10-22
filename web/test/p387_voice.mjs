// Real page + local paired fake host; only installation timing is injected (no package downloads).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,waitFor,key,sleep,shoot} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/p387/voice/',import.meta.url));mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p387-voice-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[];
try {
 for(const w of [390,1440])for(const scheme of ['light','dark'])for(const lang of ['zh','en']){
  fake.reset(); const p=await newPage(B,w,900,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`(async()=>{
    const {createAsrSetup}=await import('./js/asr-setup.js'), {notify}=await import('./js/ui.js'), {t}=await import('./js/t.js');
    const relay=await import('./js/relay.js'), session=await import('./js/session.js');
    document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur();
    session.peer.asr='not_installed';relay.paintVoiceReadiness();
    window.voiceFixture=createAsrSetup({t,notify,confirm:async()=>true,
     install:progress=>{progress('download');return new Promise(resolve=>window.finishVoice=resolve);},
     ready:()=>{session.peer.asr='ready';},repaint:()=>relay.paintVoiceReadiness()});
    window.voiceFixture.run();
   })()`);
   await waitFor(p,`document.getElementById('toast').dataset.kind==='progress'`);
   assert.equal(await evaluate(p,`document.getElementById('micLabel').textContent`),lang==='zh'?'准备中':'Preparing');
   assert.equal(await evaluate(p,`document.getElementById('mic').getAttribute('aria-label')`),lang==='zh'?'准备中':'Preparing');
   assert.notEqual(await evaluate(p,`getComputedStyle(document.getElementById('micLabel')).display`),'none');
   assert.ok(await evaluate(p,`(()=>{const m=document.getElementById('mic'),l=document.getElementById('micLabel');return l.getBoundingClientRect().width<=m.getBoundingClientRect().width;})()`));
   await sleep(200);writeFileSync(join(out,`progress-${w}-${scheme}-${lang}.png`),await shoot(p));
   await evaluate(p,`window.finishVoice({ok:true,state:'ready'})`);await waitFor(p,`document.getElementById('toast').dataset.kind==='success'`);
   assert.equal(await evaluate(p,`document.querySelector('#toast .nt-body').textContent`),lang==='zh'?'语音输入已就绪':'Voice input is ready');
   assert.equal(await evaluate(p,`document.getElementById('toast').dataset.hold`),undefined);
   assert.equal(await evaluate(p,`document.querySelectorAll('#toast button').length`),0);
   assert.notEqual(await evaluate(p,`document.getElementById('mic').getAttribute('aria-label')`),lang==='zh'?'准备中':'Preparing');
   await sleep(200);writeFileSync(join(out,`ready-${w}-${scheme}-${lang}.png`),await shoot(p));
   await waitFor(p,`!document.getElementById('toast').classList.contains('on')`,3000);
   await evaluate(p,`void window.voiceFixture.run()`);await waitFor(p,`document.getElementById('toast').dataset.kind==='progress'`);
   await evaluate(p,`window.finishVoice({ok:false,why:'download_failed'})`);await waitFor(p,`document.getElementById('toast').dataset.kind==='error'`);
   assert.equal(await evaluate(p,`document.querySelector('#toast .nt-acts button').textContent`),lang==='zh'?'重试':'Try again');
   await sleep(200);writeFileSync(join(out,`failed-${w}-${scheme}-${lang}.png`),await shoot(p));
   await evaluate(p,`document.querySelector('#toast .nt-acts button').click()`);await waitFor(p,`document.getElementById('toast').dataset.kind==='progress'`);
   await evaluate(p,`window.finishVoice({ok:true,state:'ready'})`);
   results.push({w,scheme,lang,preparingAria:true,progress:true,transientReady:true,buttonless:true,retry:true});
   console.log('PASS voice',w,scheme,lang);
  } finally {await p.dispose();}
 }
 writeFileSync(join(out,'results.json'),JSON.stringify({scope:'Chromium emulation, actual paired page and Noir notifications; injected installer timing, not physical iOS or real package installation',results},null,2));
} finally {await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
