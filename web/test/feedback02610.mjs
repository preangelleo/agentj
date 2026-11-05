import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate as ev,shoot} from './browser.mjs';
const out='reports/qa/feedback02610-web';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();let checks=0;
const check=(value,label)=>{assert.ok(value,label);checks++;};
try {
 for(const width of [390,1440])for(const scheme of ['light','dark']){
  fake.reset();const p=await newPage(B,width,900,scheme,{touch:true,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,web.url+'?lang=zh');
   await ev(p,`document.getElementById('setBtn').click()`);
   check(await ev(p,`document.getElementById('settings').hidden`),'unpaired gear inert');
   await ev(p,`import('./js/settings.js').then(m=>m.open())`);
   check(await ev(p,`[...document.querySelectorAll('#settings button,input,select')].filter(n=>n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden').length===0`),'forced settings has no controls');
   check(await ev(p,`[...document.querySelectorAll('#settings a')].filter(n=>n.getClientRects().length).length===5`),'only five public website links');
   writeFileSync(`${out}/${width}-${scheme}-unpaired.png`,await shoot(p,''));
   await ev(p,`import('./js/settings.js').then(m=>m.close())`);
   await navigate(p,fake.newPairing(web.url+'?lang=zh&pairtest=1'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);
   // Chromium's actual MediaRecorder over its synthetic audio device; every take yields data and stops its source.
   check(await ev(p,`import('./js/microphone.js').then(async m=>{const mic=m.createMicrophone({getUserMedia:()=>navigator.mediaDevices.getUserMedia({audio:true}),visible:()=>true});for(let i=0;i<5;i++){const lease=await mic.acquire(),rec=new MediaRecorder(lease.stream),chunks=[];rec.ondataavailable=e=>chunks.push(e.data);const done=new Promise(resolve=>rec.onstop=resolve);rec.start();await new Promise(resolve=>setTimeout(resolve,160));rec.stop();lease.release();await done;if(!chunks.some(b=>b.size>0)||lease.stream.getTracks().some(t=>t.readyState!=='ended'))return false;}return true;})`),'five actual MediaRecorder takes yield bytes and release every track');
   await fake.send({t:'status',s:'working',goal_active:true});
   await waitFor(p,`document.getElementById('orb').classList.contains('father-dance')`);
   check(await ev(p,`getComputedStyle(document.getElementById('orb')).animationName==='father-dance'`),'active goal reuses same animation');
   await fake.send({t:'status',s:'idle',goal_active:false});
   await waitFor(p,`!document.getElementById('orb').classList.contains('father-dance')`);checks++;
   await ev(p,`import('./js/settings.js').then(m=>m.open())`);
   await ev(p,`import('./js/ui.js').then(m=>m.notify({text:'Saved',ms:0}))`);
   await waitFor(p,`document.getElementById('toast').classList.contains('aj-surface-outline') && getComputedStyle(document.getElementById('toast')).outlineWidth==='2px' && getComputedStyle(document.getElementById('toast')).outlineColor==='rgb(98, 152, 135)'`);checks++;
   writeFileSync(`${out}/${width}-${scheme}-settings-outline.png`,await shoot(p,''));
   await ev(p,`import('./js/ui.js').then(m=>m.toastOff());import('./js/settings.js').then(m=>m.close())`);
   // Isolate actual painted background: this exercises hit testing and alpha composition, not page-name heuristics.
   for(const [colour,expected] of [['rgb(20,20,20)',true],['rgb(98,152,135)',false],['rgb(22,22,22)',true]]){
    check(await ev(p,`import('./js/surface-outline.js').then(m=>{let bg=document.createElement('div'),surface=document.createElement('div');bg.style.cssText='position:fixed;inset:0;z-index:100000;background:${colour}';surface.style.cssText='position:fixed;left:20px;top:150px;width:200px;height:100px;z-index:100001;background:rgb(20,20,20)';document.body.append(bg,surface);m.paintSurfaceOutline(surface);let result=surface.classList.contains('aj-surface-outline');bg.remove();surface.remove();return result===${expected}})`),'actual backdrop '+colour);
   }
   await ev(p,`document.body.style.setProperty('--c-idle','#2c2b26');import('./js/ui.js').then(m=>m.notify({text:'Saved',ms:0}))`);
   await waitFor(p,`document.getElementById('toast').classList.contains('aj-surface-outline') && getComputedStyle(document.getElementById('toast')).outlineWidth==='2px' && getComputedStyle(document.getElementById('toast')).outlineColor==='rgb(98, 152, 135)'`);checks++;
   writeFileSync(`${out}/${width}-${scheme}-custom-noir.png`,await shoot(p,''));
   check(!p.problems.length,'no browser errors '+p.problems.join(';'));
   console.log('PASS',width,scheme);
  }finally{await p.dispose();}
 }
 writeFileSync(`${out}/browser.json`,JSON.stringify({ok:true,checks,scope:'Chromium client, synthetic microphone in separate unit tests; not physical iPhone'},null,2));
}finally{await B.close();await fake.stop();await web.stop();}
