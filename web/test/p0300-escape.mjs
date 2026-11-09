// Real CDP key events, existing product modal close handlers and the focused PTY DOM guard.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,key} from './browser.mjs';
const out=process.env.P0300_ESCAPE_OUT||'reports/qa/0300/web/escape';mkdirSync(out,{recursive:true});
const csvOnly=process.env.P0300_CSV_DIAGNOSTIC==='1';
const readerOnly=process.env.P0300_READER_DIAGNOSTIC==='1';
const checks=[];const check=(v,name)=>{checks.push({name,pass:!!v});assert.ok(v,name);};
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();

async function readerDiagnostic(width=390,theme='light'){
 fake.reset();await fake.addTurn({k:'phone',text:'Inspect the synthetic reply'},'# Reader fixture\n\n```text\nsynthetic reader copy\n```','done');
 const p=await newPage(B,width,width===390?844:900,theme,{allow:[web.url,fake.relay+'/']});
 const executionCount=()=>fake.log.filter(m=>['say','slash','cmd_run','reset','answer'].includes(m.t)).length;
 const trace=[];
 const snap=async tag=>{const state=await evaluate(p,`(async()=>{const layers=await import('./js/layers.js'),top=layers.activeLayer();return{rdHidden:document.getElementById('rd').hidden,toastOn:document.getElementById('toast').classList.contains('on'),toastText:document.getElementById('toast').textContent,top:top?.element()?.id||null,rank:top?.rank??null,focus:document.activeElement?.id||null}})()`);const row={tag,width,theme,...state,executions:executionCount()};trace.push(row);console.log('READER_DIAGNOSTIC '+JSON.stringify(row));return row;};
 const tap=async selector=>{const point=await evaluate(p,`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});};
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();`);
  await tap('#readBtn');await waitFor(p,`!document.getElementById('rd').hidden&&!!document.querySelector('#rdWords .codeblock .copy')`);
  await new Promise(r=>setTimeout(r,700)); // same opening-click guard as the original p183 entry
  await tap('#rdWords .codeblock .copy');await waitFor(p,`document.querySelector('#rdWords .codeblock .copy').dataset.done==='1'`);
  const before=await snap('before-one-trusted-Escape');
  await key(p,'Escape');const after=await snap('after-one-trusted-Escape');
  check(before.rdHidden===false&&before.toastOn&&before.top==='rd',`${width}/${theme}: real reader and copy notice are present before the single Escape`);
  check(after.rdHidden&&after.toastOn&&after.executions===before.executions,`${width}/${theme}: one trusted Escape closes reader, preserves the underlying notice and executes nothing`);
 }finally{writeFileSync(out+(readerOnly?'/reader-diagnostic.json':`/reader-${width}-${theme}.json`),JSON.stringify({trace},null,2)+'\n');await p.dispose();}
}

async function csvDiagnostic(width=390,theme='light'){
 fake.reset();const bytes=Buffer.from('label,count\nfixture,1\n'),mid='c'.repeat(22),media={mid,name:'synthetic.csv',mime:'text/csv',kind:'file',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),ref:'out/synthetic.csv'};
 fake.st.onApp=async(c,m,send)=>{if(m.t!=='media_get')return false;const d=bytes.subarray(m.o);await send({t:'media_chunk',mid:m.mid,o:m.o,d:d.toString('base64url'),last:true});return true;};
 const turn=await fake.addTurn({k:'phone',text:'Inspect the synthetic CSV'},'# CSV fixture\n\n```text\nsynthetic CSV copy\n```','done');await fake.updateTurn(turn.id,{media:[media],media_skip:[]});
 const p=await newPage(B,width,width===390?844:900,theme,{allow:[web.url,fake.relay+'/']});const trace=[];
 const count=()=>fake.log.filter(m=>['say','slash','cmd_run','reset','answer'].includes(m.t)).length;
 const snap=async tag=>{const state=await evaluate(p,`(async()=>{const m=await import('./js/layers.js'),top=m.activeLayer(),n=document.querySelector('.mview');return{viewerHidden:n?.hidden,qbarHidden:document.getElementById('qbar').hidden,toastOn:document.getElementById('toast').classList.contains('on'),toastText:document.getElementById('toast').textContent,top:top?.element()?.id||top?.element()?.className||null,rank:top?.rank??null,trap:top?.trap??null,focus:document.activeElement?.id||null}})()`);const row={tag,width,theme,...state,executions:count()};trace.push(row);console.log('CSV_DIAGNOSTIC '+JSON.stringify(row));return row;};
 const tap=async selector=>{const point=await evaluate(p,`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});};
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();`);
  await waitFor(p,`!!document.querySelector('.mcard[data-mid="${mid}"]')&&document.querySelector('.mcard[data-mid="${mid}"]').dataset.state==='ready'&&!!document.querySelector('#words .codeblock .copy')`);
  await tap('#words .codeblock .copy');await waitFor(p,`document.querySelector('#words .codeblock .copy').dataset.done==='1'`);
  await tap('.mcard[data-mid="'+mid+'"] .mact.pv');await waitFor(p,`!!document.querySelector('.mview')&&!document.querySelector('.mview').hidden&&!!document.querySelector('.mdoc table')`);
  const before=await snap('before-one-trusted-Escape');await key(p,'Escape');const after=await snap('after-one-trusted-Escape');
  check(before.viewerHidden===false&&before.toastOn&&before.trap===true,`${width}/${theme}: actual CSV modal owner and copy notice are present`);
  check(after.viewerHidden&&after.toastOn&&after.executions===before.executions,`${width}/${theme}: single Escape closes CSV viewer, preserves notice and executes nothing`);
  if(!csvOnly){
    await tap('#replyBtn');await waitFor(p,`!document.getElementById('qbar').hidden`);
    await evaluate(p,`import('./js/ui.js').then(m=>m.notify({text:'Nonmodal quote fixture',ms:10000}))`);
    const qbefore=await snap('before-nonmodal-qbar-Escape');await key(p,'Escape');const qafter=await snap('after-nonmodal-qbar-Escape');
    check(qbefore.top==='qbar'&&qbefore.trap===false&&qbefore.toastOn&&!qbefore.qbarHidden,`${width}/${theme}: real quote bar is a nonmodal owner under the notification`);
    check(!qafter.toastOn&&!qafter.qbarHidden&&qafter.executions===qbefore.executions,`${width}/${theme}: nonmodal layer still lets Escape dismiss notification first without executing`);
  }

 }finally{writeFileSync(out+`/csv-${width}-${theme}.json`,JSON.stringify({trace},null,2)+'\n');fake.st.onApp=null;await p.dispose();}
}
try{if(csvOnly)await csvDiagnostic();else if(readerOnly)await readerDiagnostic();else for(const width of [390,1440])for(const theme of ['light','dark'])await readerDiagnostic(width,theme);if(!readerOnly&&!csvOnly)for(const width of [390,1440])for(const theme of ['light','dark'])await csvDiagnostic(width,theme);for(const width of (readerOnly||csvOnly)?[]:[390,1440])for(const theme of ['light','dark']){
 fake.reset();const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();`);
  // Existing standard dialog + recent notification: one Escape belongs to the modal, preserving the toast.
  await evaluate(p,`(async()=>{const u=await import('./js/ui.js'),m=await import('./js/usagedlg.js');u.notify({text:'Saved fixture',ms:10000});m.openUsage({source:true,windows:[],ctx:null,heartbeat:{available:true,total_tokens:12,input_tokens:10,output_tokens:2,turns:1,measured_turns:1,complete:true}},document.getElementById('mWeek'));})()`);
  await key(p,'Escape');
  check(await evaluate(p,`document.getElementById('usageDlg').hidden&&document.getElementById('toast').classList.contains('on')`),`${width}/${theme}: standard modal consumes first Escape and preserves notice`);
  // No modal: the same notification still closes on Escape.
  await key(p,'Escape');check(await evaluate(p,`!document.getElementById('toast').classList.contains('on')`),`${width}/${theme}: no-modal Escape dismisses notification`);
  // Existing native dialog: browser/layer cancel lifecycle continues to own Escape.
  await evaluate(p,`(async()=>{const u=await import('./js/ui.js');u.notify({text:'Native fixture',ms:10000});const d=document.getElementById('full541');d.showModal();d.querySelector('button').focus();})()`);
  await key(p,'Escape');
  check(await evaluate(p,`!document.getElementById('full541').open&&document.getElementById('toast').classList.contains('on')`),`${width}/${theme}: native modal closes while notification remains`);
  await evaluate(p,`(async()=>{const u=await import('./js/ui.js');u.toastOff();u.notify({text:'PTY fixture',ms:10000});const h=document.createElement('div');h.id='term-xt';const a=document.createElement('textarea');a.setAttribute('aria-label','Isolated PTY focus fixture');h.append(a);document.body.append(h);window.__ptyEsc=0;h.addEventListener('keydown',e=>{if(e.key==='Escape'&&e.isTrusted)window.__ptyEsc++;});a.focus();})()`);
  await key(p,'Escape');
  check(await evaluate(p,`window.__ptyEsc===1&&document.getElementById('toast').classList.contains('on')`),`${width}/${theme}: real Escape reaches focused PTY descendant without dismissing notice`);
  await evaluate(p,`document.getElementById('term-xt').remove()`);
  // Product shared confirm dialog retains its original false/cancel result.
  await evaluate(p,`(async()=>{const u=await import('./js/ui.js');u.notify({text:'Confirm fixture',ms:10000});window.__confirmResult='pending';u.confirmSheet('确认测试','仅测试Esc优先级','确认').then(v=>window.__confirmResult=v);})()`);
  await waitFor(p,`!document.getElementById('confirm').hidden`);await key(p,'Escape');
  check(await evaluate(p,`document.getElementById('confirm').hidden&&window.__confirmResult===false&&document.getElementById('toast').classList.contains('on')`),`${width}/${theme}: shared confirm cancels once and preserves notice`);
  // Fullscreen composer uses its own role=dialog root, outside the standard .gdlg class.
  const sentBefore=fake.log.filter(m=>['say','slash','cmd_run'].includes(m.t)).length;
  await evaluate(p,`(async()=>{const u=await import('./js/ui.js');u.notify({text:'Editor fixture',ms:10000});document.getElementById('input').value='Original draft';document.getElementById('tFull').click();document.getElementById('fsText').value='Edited draft kept by Escape';})()`);
  await waitFor(p,`!document.getElementById('fsEd').hidden`);await key(p,'Escape');
  check(await evaluate(p,`document.getElementById('fsEd').hidden&&document.getElementById('toast').classList.contains('on')`),`${width}/${theme}: fullscreen editor owns Escape and preserves notice`);
  check(await evaluate(p,`document.getElementById('input').value==='Edited draft kept by Escape'`)&&fake.log.filter(m=>['say','slash','cmd_run'].includes(m.t)).length===sentBefore,`${width}/${theme}: Escape retains edited composer draft and executes nothing`);
 }finally{await p.dispose();}
}console.log(`PASS 0.30.0 Escape ownership: ${checks.length}/${checks.length} checks (standard/native/no-modal/PTY/shared-confirm/fullscreen-editor)`);
}finally{await B.close();await fake.stop();await web.stop();writeFileSync(out+'/validation.json',JSON.stringify({checks},null,2)+'\n');}
