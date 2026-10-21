// P415 Chromium desktop + mobile emulation, local paired Noise host only.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,key} from './browser.mjs';
const out='reports/qa/p415/';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
let checks=0;const check=(v,m)=>{assert.ok(v,m);checks++;},ev=evaluate;
try{for(const width of [390,1280])for(const lang of ['zh','en']){
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 const mutations=[];let notes=[{id:'n1',text:'First note',editable:true}],todos=[{id:'t1',text:'First todo',owner:'owner',status:'pending'}];
 fake.st.onApp=async(c,m,send)=>{
  const reply=async(t,v)=>{await send({t,r:m.r,...v});return true;};
  if(m.t==='jcard_list')return reply('jcard_items',{cards:['notes','todo'].map(id=>({id,name:{zh:id,en:id},on:true,official:true,accent:'windstorm',plus:id==='notes'?'agentj-add-note':'agentj-add-todo'}))});
  if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:m.id,template:m.id==='notes'?'notes':'checklist',entries:notes,items:todos,todo:m.id==='todo'?{counts:{}}:undefined,revision:'rev'});
  if(m.t==='jcard_note'){
   mutations.push(m);if(m.text==='secret-fixture')return reply('jcard_note',{ok:false,why:'looks_like_a_secret'});
   if(m.id==='notes'){if(m.op==='add')notes.unshift({id:'new',text:m.text});if(m.op==='edit')notes=notes.map(x=>x.id===m.entry?{...x,text:m.text}:x);if(m.op==='delete')notes=notes.filter(x=>x.id!==m.entry);}
   return reply('jcard_note',{ok:true,id:m.id,item:{id:'t2'}});
  }return false;
 };
 await fake.addTurn({k:'phone',text:'Question'},'One [A](reply:A) [B](reply:B)\n\nTwo [C](reply:C) [D](reply:D)\n\n- Three [E](reply:E) [F](reply:F)','done');
 const p=await newPage(B,width,844,'light',{allow:[web.url,fake.relay+'/'],touch:width<600});
 try{
  await navigate(p,fake.newPairing(web.url+(lang==='en'?'?lang=en':'')));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await ev(p,"document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();");
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await sleep(200);
  await waitFor(p,"document.querySelectorAll('#words .rbtn').length===6");
  for(const [idx,say] of ['A','C','E'].entries()){
   await ev(p,`document.querySelector('#words button[data-say="${say}"]').click()`);
   for(let i=0;i<60&&fake.st.says.length<idx+1;i++)await sleep(50);
   assert.equal(fake.st.says.at(-1).text,say);checks++;
   await ev(p,"import('./js/relay.js').then(m=>{while(m.debug().pageAt>0)m.remoteMessage(-1)})");await sleep(200);
  }
  check(await ev(p,"document.querySelectorAll('#words .rb-note').length===3"),'all groups retain their choices');
  await ev(p,"document.activeElement.blur();document.body.focus()");await key(p,'@',{code:'Digit2',shift:true});
  await waitFor(p,"document.getElementById('input').value==='@'&&!document.getElementById('five-mentions').hidden");check(await ev(p,"document.activeElement.id==='input'"),'@ focuses composer');
  const paste=async(text)=>ev(p,`(()=>{document.getElementById('input').blur();const d=new DataTransfer();d.setData('text/plain',${JSON.stringify(text)});document.body.dispatchEvent(new ClipboardEvent('paste',{clipboardData:d,bubbles:true,cancelable:true}));})()`);
  await ev(p,"document.getElementById('input').value='Draft ';document.getElementById('input').dispatchEvent(new Event('input'));document.getElementById('five-mentions').hidden=true");
  await paste('address\nTokyo\nHong Kong');check(await ev(p,"document.getElementById('input').value==='Draft address\\nTokyo\\nHong Kong'"),'global short multiline inserts');
  await ev(p,"document.getElementById('input').value='';document.getElementById('input').dispatchEvent(new Event('input'))");await paste('x'.repeat(2500));
  await waitFor(p,"import('./js/relay.js').then(m=>m.debug().atts.length===1)");checks++;
  await ev(p,`(()=>{document.getElementById('input').blur();const d=new DataTransfer(),b=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6dN8AAAAASUVORK5CYII='),c=>c.charCodeAt(0));d.items.add(new File([b],'pixel.png',{type:'image/png'}));document.body.dispatchEvent(new ClipboardEvent('paste',{clipboardData:d,bubbles:true,cancelable:true}));})()`);
  await waitFor(p,"import('./js/relay.js').then(m=>m.debug().atts.length===2)");checks++;
  // Another editable field keeps its paste and @; a native dialog also owns them.
  check(await ev(p,"(()=>{const d=document.createElement('dialog'),i=document.createElement('input');d.append(i);document.body.append(d);d.showModal();i.focus();const dt=new DataTransfer();dt.setData('text/plain','private');const e=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});i.dispatchEvent(e);const ok=!e.defaultPrevented;d.close();d.remove();return ok})()"),'dialog field is not intercepted');
  await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");
  for(const id of ['notes','todo']){
   await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget('${id}'))`);await waitFor(p,`document.querySelector('.jc-body[data-card=${id}] .jc-action')`);
   await ev(p,"document.querySelector('.jc-action[data-act=add]').click()");await waitFor(p,"document.querySelector('dialog[open] textarea')");
   if(id==='notes') {
    await ev(p,"document.querySelector('dialog textarea').value='secret-fixture'");await key(p,'Enter',{code:'Enter',meta:true});await sleep(150);
    check(await ev(p,"document.querySelector('dialog[open] textarea').value==='secret-fixture'"),'refused save retains draft');
   }
   writeFileSync(out+`dialog-${id}-${width}-${lang}.png`,await shoot(p));
   await ev(p,"document.querySelector('dialog textarea').value='';Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:async()=> 'clipboard memo'}});document.querySelector('dialog button').click()");await sleep(30);
   check(await ev(p,"document.querySelector('dialog textarea').value==='clipboard memo'"),'one tap clipboard');
   await key(p,'Enter',{code:'Enter',ctrl:true});await waitFor(p,"!document.querySelector('.jc-dialog')");
   check(mutations.at(-1).op==='add'&&mutations.at(-1).id===id,'direct add channel');check(fake.st.says.length===3,'no agent message for direct save');
   await sleep(100);await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget('${id}'))`);await sleep(150);
   await ev(p,"document.getElementById('cardPlus').click()");await waitFor(p,"document.querySelector('.jc-dialog[open]')");await key(p,'Escape',{code:'Escape'});await waitFor(p,"!document.querySelector('.jc-dialog')");checks++;
   const sel=id==='notes'?'[data-note-op=edit]':'[data-todo-op=edit]';
   await ev(p,`document.querySelector('${sel}').click()`);await waitFor(p,"document.querySelector('.jc-dialog textarea')");
   await ev(p,"document.querySelector('.jc-dialog textarea').value='Edited directly'");await key(p,'Enter',{code:'Enter',ctrl:true});await waitFor(p,"!document.querySelector('.jc-dialog')");
   check(mutations.at(-1).op==='edit'&&mutations.at(-1).revision==='rev','direct edit with revision');await sleep(150);
   await ev(p,`document.querySelector('${id==='notes'?'[data-note-op=delete]':'[data-todo-op=delete]'}').click()`);await waitFor(p,"!document.getElementById('confirm').hidden");await ev(p,"document.getElementById('confirm-yes').click()");await sleep(150);
   check(mutations.at(-1).op==='delete','direct confirmed delete');
   await ev(p,"document.querySelector('[data-act=collaborate]').click()");await waitFor(p,`document.getElementById('input').value==='/agentj-add-${id==='notes'?'note':'todo'} '`);checks++;
  }
  writeFileSync(out+`web-${width}-${lang}.png`,await shoot(p));check(p.problems.length===0,p.problems.join('\n'));
 }finally{await p.dispose();}
}writeFileSync(out+'browser.json',JSON.stringify({ok:true,checks,physical:false},null,2));const rec=parityRecorder('web/test/p415.mjs'); for(const id of ['p415-direct','p415-reply','p415-mention','p415-paste'])rec.record(id,true);rec.write();console.log('P415 PASS',checks);
}finally{await B.close();await fake.stop();await web.stop();}
