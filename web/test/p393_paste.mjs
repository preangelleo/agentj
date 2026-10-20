// P393 #143 · real Chromium + real Noise to the local fake host. ⌘V / Ctrl+V with the focus on no field pastes into the
// composer: text lands at the end of the message box and the box takes the focus; an image goes into the attachment tray
// (the same path as a paste in the box). A paste in the box itself is not doubled; an open menu / page / dialog and any other
// field (search, settings) keep their paste. The paste is a real Ctrl+V on the real clipboard (trusted event).
// 390 + 1440 × light / dark, before/after shots → reports/qa/p393/paste/ (`P393_PASTE_TAG=before` names the before set).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitState,waitFor,sleep,shoot} from './browser.mjs';
const out=fileURLToPath(new URL('../../../reports/qa/p393/paste/',import.meta.url));mkdirSync(out,{recursive:true});
const TAG=process.env.P393_PASTE_TAG||'after';
const tmp=mkdtempSync(join(tmpdir(),'p393-paste-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const rec=parityRecorder('web/test/p393_paste.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[];
const val=p=>ev(p,`document.getElementById('input').value`);
const chips=p=>ev(p,`document.getElementById('tray').querySelectorAll('[role=listitem]').length`);
const active=p=>ev(p,`document.activeElement?.id||document.activeElement?.tagName`);
// the real clipboard, then a real Ctrl+V (the browser's own paste command → a trusted paste event where the focus is)
const clipText=(p,s)=>ev(p,`navigator.clipboard.writeText(${JSON.stringify(s)}).then(()=>1)`);
const clipImage=p=>ev(p,`(async()=>{const c=document.createElement('canvas');c.width=24;c.height=24;const x=c.getContext('2d');x.fillStyle='#d97757';x.fillRect(0,0,24,24);
 const b=await new Promise(r=>c.toBlob(r,'image/png'));await navigator.clipboard.write([new ClipboardItem({'image/png':b})]);return b.size;})()`);
async function ctrlV(p){
 const base={key:'v',code:'KeyV',windowsVirtualKeyCode:86,modifiers:2};
 await p.send('Input.dispatchKeyEvent',{type:'rawKeyDown',...base,commands:['paste']});
 await p.send('Input.dispatchKeyEvent',{type:'keyUp',...base});await sleep(200);
}
const blank=p=>ev(p,`(()=>{document.activeElement?.blur();document.body.tabIndex=-1;document.body.focus();return document.activeElement===document.body;})()`);
async function pair(w,scheme){
 fake.reset();const p=await newPage(B,w,900,scheme,{allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);await p.send('Emulation.setFocusEmulationEnabled',{enabled:true});return p;
}
try{
 for(const w of [390,1440])for(const scheme of ['light','dark']){
  const tag=`${w}-${scheme}`,p=await pair(w,scheme),r={w,scheme};
  // 1. focus on nothing → text at the end of the box, the box focused
  await ev(p,`(()=>{const i=document.getElementById('input');i.value='开头';i.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  assert.ok(await blank(p),'the focus is on the page itself');
  await clipText(p,'粘贴的一句话');await ctrlV(p);
  writeFileSync(join(out,`${TAG}-${tag}-text.png`),await shoot(p));
  r.text=await val(p);r.focus=await active(p);
  assert.equal(r.text,'开头粘贴的一句话',`#143 ${tag}: the words go to the end of the message box`);
  assert.ok(['input','workflow-compose'].includes(r.focus),`#143 ${tag}: the message box has the focus (${r.focus})`);
  // 2. a paste in the box itself is not doubled
  await ev(p,`(()=>{const i=document.getElementById('input');i.focus();i.setSelectionRange(i.value.length,i.value.length);})()`);
  await clipText(p,'X');await ctrlV(p);
  assert.equal(await val(p),'开头粘贴的一句话X',`#143 ${tag}: a paste in the box lands once`);
  // 3. an image with the focus on nothing → the attachment tray (same path as in the box)
  await ev(p,`(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await blank(p);const before=await chips(p);await clipImage(p);await ctrlV(p);
  await waitFor(p,`document.getElementById('tray').querySelectorAll('[role=listitem]').length===${before+1}`);
  r.image_chips=await chips(p);assert.equal(await val(p),'',`#143 ${tag}: an image puts no text in the box`);
  writeFileSync(join(out,`${TAG}-${tag}-image.png`),await shoot(p));
  // 4. the ≡ menu open → nothing hijacked
  await ev(p,`document.getElementById('slashBtn').click()`);await waitFor(p,`!document.getElementById('menu').hidden`);
  await blank(p);await clipText(p,'菜单开着');await ctrlV(p);
  assert.equal(await val(p),'',`#143 ${tag}: with the menu open the paste is not taken`);
  await ev(p,`document.getElementById('slashBtn').click()`);await waitFor(p,`document.getElementById('menu').hidden`);
  // 5. Settings open → nothing hijacked
  await ev(p,`document.getElementById('setBtn').click()`);await waitFor(p,`!document.getElementById('settings').hidden`);
  await blank(p);await ctrlV(p);
  assert.equal(await val(p),'',`#143 ${tag}: with Settings open the paste is not taken`);
  writeFileSync(join(out,`${TAG}-${tag}-settings.png`),await shoot(p));
  await p.send('Input.dispatchKeyEvent',{type:'rawKeyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
  await waitFor(p,`document.getElementById('settings').hidden`);
  // 6. another field (the search box) keeps its own paste, nothing goes to the composer
  await ev(p,`document.getElementById('srchBtn').click()`);await waitFor(p,`!document.getElementById('srch').hidden`);
  await ev(p,`document.getElementById('srchInput').focus()`);await clipText(p,'找这个');await ctrlV(p);
  r.search=await ev(p,`document.getElementById('srchInput').value`);
  assert.equal(r.search,'找这个',`#143 ${tag}: the search box gets its own paste`);
  assert.equal(await val(p),'',`#143 ${tag}: the composer is untouched while the search box has the focus`);
  // 7. a plain input outside any dialog (the key / secret card stands in) is never redirected
  await ev(p,`document.getElementById('srch').querySelector('[data-close],.srchClose,#srchClose')?.click()`);
  if(!(await ev(p,`document.getElementById('srch').hidden`))){await p.send('Input.dispatchKeyEvent',{type:'rawKeyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});}
  await waitFor(p,`document.getElementById('srch').hidden`);
  await ev(p,`(()=>{const f=document.createElement('input');f.type='password';f.id='p393-secret';document.querySelector('.composer').before(f);f.focus();})()`);
  await clipText(p,'not-for-the-composer');await ctrlV(p);
  assert.equal(await val(p),'',`#143 ${tag}: a secret field's paste never goes to the composer`);
  assert.equal(await ev(p,`document.getElementById('p393-secret').value`),'not-for-the-composer');
  await ev(p,`document.getElementById('p393-secret').remove()`);
  assert.deepEqual(p.problems,[],'no console errors');
  results.push(r);console.log(`P393 paste ${tag} PASS`);await p.dispose();
 }
 writeFileSync(join(out,'result.json'),JSON.stringify({ok:true,results},null,1)+'\n');
 passed=true;console.log('P393 #143 paste anywhere goes to the composer PASS');
}catch(e){console.error(e);process.exitCode=1;}finally{rec.record('p393-paste',passed);rec.write();await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
