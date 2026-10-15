// Real Agent J page + isolated fake host. No production provider or credentials.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep,key} from './browser.mjs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {KEYMAP} from '../public/js/interactions.gen.js';
const out=process.env.AJ_SHOTS || 'reports/qa/p265/browser'; mkdirSync(out,{recursive:true});
const rec=parityRecorder('web/test/p265.mjs');let qualified=false;
let web,fake,B;
const results=[];
try{
 web=await startWebServer();fake=await startFakeHost();B=await launch();
 for(const lang of ['zh','en']){
  fake.reset();const p=await newPage(B,1440,900,'dark',{allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()`);
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Wren'});
   // Actual QR bytes from the existing encoder; one-time test pairing material stays on stdin/in memory.
   const link=fake.newPairing(web.url), host=new URL('../../host/',import.meta.url).pathname;
   const qr=execFileSync(process.env.AJ_PYTHON || resolve(execFileSync('git',['rev-parse','--path-format=absolute','--git-common-dir'],{encoding:'utf8'}).trim(),'../host/.venv/bin/python'),['-c',`import sys;sys.path.insert(0,${JSON.stringify(host)});from agentj import wire;print(wire.pairing_qr(sys.stdin.read()).png_data_uri(scale=4,border=2,dark="#141414",light="#faf9f5"))`],{input:link,encoding:'utf8'}).trim();
   fake.remotes({limit:10,items:[],link,qr,code:'482913'});

   await fake.addTurn({k:'phone',text:'Hello'},'A reply for the shortcuts test.');
   await waitFor(p,`document.getElementById('words').textContent.includes('shortcuts test')`);
   await evaluate(p,`document.getElementById('set-hostupg').replaceWith(document.getElementById('set-hostupg').cloneNode(true));import('./js/settings.js').then(m=>{window.p265={shot:0,upgrade:0,refresh:0};m.configure({screenshot:()=>window.p265.shot++,upgradeHost:()=>window.p265.upgrade++,reload:()=>window.p265.refresh++});});const field=document.createElement('input');field.id='p265-field';document.body.append(field);const edit=document.createElement('div');edit.id='p265-edit';edit.contentEditable='true';document.body.append(edit);Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{}}});true`);
   for(const [match,[id]] of Object.entries(KEYMAP).filter(([k])=>k.startsWith('alt+shift:'))) for(const focus of ['body','textarea','input','contenteditable']){
    await evaluate(p,`Promise.all([import('./js/five-pages.js').then(m=>m.go('chat')),import('./js/settings.js').then(m=>m.close())])`);
    for(let i=0;i<5;i++)await key(p,'Escape');
    await evaluate(p,`(()=>{for(const id of ['keys','silent','badge-panel'])document.getElementById(id).hidden=true;const e=document.getElementById('input');e.value='draft stays';e.dispatchEvent(new Event('input'));document.getElementById('p265-field').value='field stays';document.getElementById('p265-edit').textContent='edit stays';${focus==='textarea'?'e.focus()':focus==='input'?"document.getElementById('p265-field').focus()":focus==='contenteditable'?"document.getElementById('p265-edit').focus()":"document.getElementById('setBtn').focus()"};return true;})()`);
    const expectedFocus={body:'setBtn',textarea:'input',input:'p265-field',contenteditable:'p265-edit'}[focus];
    assert.equal(await evaluate(p,'document.activeElement.id'),expectedFocus,`real ${focus} focus before ${id}`);
    const code=match.split(':')[1],letter=code.slice(3);
    await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'ﬂ',code,modifiers:9,text:'ﬂ',windowsVirtualKeyCode:letter.charCodeAt(0)});
    await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'ﬂ',code,modifiers:9,windowsVirtualKeyCode:letter.charCodeAt(0)});
    await sleep(100);
    assert.equal(await evaluate(p,`document.getElementById('input').value`),'draft stays',`${lang} ${letter} focused=${focus}: no special character`);
    assert.equal(await evaluate(p,`document.getElementById('p265-field').value`),'field stays');
    assert.equal(await evaluate(p,`document.getElementById('p265-edit').textContent`),'edit stays');
    if(id==='app.keys')assert.equal(await evaluate(p,`document.getElementById('keys').hidden`),false);
    if(id==='app.settings'||id==='app.pair_add')assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false,`${lang} ${id} focus=${focus}`);
    if(id==='app.pair_add'){
     await waitFor(p,`!document.getElementById('rm-addbox').hidden && document.getElementById('rm-qr').getAttribute('src')?.startsWith('data:image/png;base64,')`);
     assert.equal(fake.st.remotes.writes.at(-1).t,'dev_add');assert.equal(fake.st.remotes.writes.at(-1).sigOk,true,'pair QR request is signed');
    }

    if(id==='app.search')assert.equal(await evaluate(p,`document.getElementById('srch').hidden`),false);
    if(id==='app.friends'||id==='app.bots')assert.equal(await evaluate(p,`document.body.dataset.view`),id.split('.')[1]);
    results.push({lang,id,focus,result:'pass'});
   }
   // From an ordinary reference/reader layer, a global page action must reveal its destination.
   for(const letter of ['F','E','O','P','V','K']){
    await evaluate(p,`Promise.all([import('./js/five-pages.js').then(m=>m.go('chat')),import('./js/settings.js').then(m=>m.close())])`);
    for(let i=0;i<5;i++)await key(p,'Escape');
    await evaluate(p,`document.getElementById('input').blur()`);await key(p,'?',{code:'Slash',shift:true,text:'?'});
    assert.equal(await evaluate(p,`document.getElementById('keys').hidden`),false);
    await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'ﬂ',code:'Key'+letter,modifiers:9,text:'ﬂ',windowsVirtualKeyCode:letter.charCodeAt(0)});
    await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'ﬂ',code:'Key'+letter,modifiers:9,windowsVirtualKeyCode:letter.charCodeAt(0)});
    await sleep(100);assert.equal(await evaluate(p,`document.getElementById('keys').hidden`),true,letter+' destination must not remain behind shortcut layer');
    if(['F','E','V'].includes(letter))assert.equal(await evaluate(p,`document.body.dataset.view`),{F:'friends',E:'bots',V:'setup'}[letter]);
    if(['O','P'].includes(letter))assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false);
    if(letter==='K')assert.equal(await evaluate(p,`document.getElementById('srch').hidden`),false);
   }
   await evaluate(p,`Promise.all([import('./js/five-pages.js').then(m=>m.go('chat')),import('./js/settings.js').then(m=>m.close())])`);
   for(let i=0;i<5;i++)await key(p,'Escape');
   await evaluate(p,`document.getElementById('input').blur()`);await key(p,'f');await waitFor(p,`!document.getElementById('rd').hidden`);
   await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'ﬂ',code:'KeyF',modifiers:9,text:'ﬂ',windowsVirtualKeyCode:70});
   await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'ﬂ',code:'KeyF',modifiers:9,windowsVirtualKeyCode:70});
   await sleep(200);
   assert.equal(await evaluate(p,`document.getElementById('rd').hidden`),true,'reader closes through its own history/focus handler');
   assert.equal(await evaluate(p,`document.body.dataset.view`),'friends');
   // Reader close installs a 350 ms ghost-click guard; Upgrade must still invoke its configured callback once.
   await evaluate(p,`Promise.all([import('./js/five-pages.js').then(m=>m.go('chat')),import('./js/settings.js').then(m=>m.close())])`);
   for(let i=0;i<5;i++)await key(p,'Escape');
   await evaluate(p,`document.getElementById('input').blur()`);await key(p,'f');await waitFor(p,`!document.getElementById('rd').hidden`);
   await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'ﬂ',code:'KeyU',modifiers:9,text:'ﬂ',windowsVirtualKeyCode:85});
   await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'ﬂ',code:'KeyU',modifiers:9,windowsVirtualKeyCode:85});
   await sleep(200);   // P290 merge: P302's one back path closes the reader through history (asynchronous), as above
   assert.equal(await evaluate(p,`document.getElementById('rd').hidden`),true);
   const counts=await evaluate(p,'window.p265');assert.deepEqual(counts,{shot:4,upgrade:5,refresh:4});
   // A real image preview owns all document-capture keys. App functions must win at window capture.
   const image=readFileSync(new URL('../public/apple-touch-icon.png',import.meta.url)),mid='v'.repeat(22);
   fake.st.onApp=async(c,m,send)=>{if(m.t!=='media_get'||m.mid!==mid)return false;await send({t:'media_chunk',mid,o:m.o,d:image.subarray(m.o).toString('base64url'),last:true});return true;};
   const turn=await fake.addTurn({k:'phone',text:'Preview fixture'},'![Preview](out/preview.png)');
   await fake.updateTurn(turn.id,{media:[{mid,name:'preview.png',mime:'image/png',kind:'image',bytes:image.length,sha256:createHash('sha256').update(image).digest('hex'),ref:'out/preview.png'}],media_skip:[]});
   await waitFor(p,`document.querySelector('.mcard[data-mid="${mid}"]')?.dataset.state==='ready'`);
   for(const letter of ['H','O','F']){
    await evaluate(p,`Promise.all([import('./js/five-pages.js').then(m=>m.go('chat')),import('./js/settings.js').then(m=>m.close())])`);
    for(let i=0;i<5;i++)await key(p,'Escape');
    await sleep(400); // the reader's existing ghost-click guard is deliberately retained
    await evaluate(p,`document.querySelector('.mcard[data-mid="${mid}"] img').click()`);await waitFor(p,`!document.querySelector('.mview').hidden`);
    await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'ﬂ',code:'Key'+letter,modifiers:9,text:'ﬂ',windowsVirtualKeyCode:letter.charCodeAt(0)});
    await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'ﬂ',code:'Key'+letter,modifiers:9,windowsVirtualKeyCode:letter.charCodeAt(0)});
    assert.equal(await evaluate(p,`document.querySelector('.mview').hidden`),true,'global '+letter+' escapes the preview keyboard owner');
    if(letter==='H')assert.equal(await evaluate(p,`document.getElementById('keys').hidden`),false);
    if(letter==='O')assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false);
    if(letter==='F')assert.equal(await evaluate(p,`document.body.dataset.view`),'friends');
   }
   // Global functions preserve the old locked-take any-key release and still carry out the action.
   await evaluate(p,`Promise.all([import('./js/five-pages.js').then(m=>m.go('chat')),import('./js/settings.js').then(m=>m.close())])`);
   for(let i=0;i<5;i++)await key(p,'Escape');
   await evaluate(p,`document.activeElement?.blur()`);await key(p,'m');await sleep(80);await key(p,'m');
   await waitFor(p,`import('./js/relay.js').then(m=>!!m.debug().ptt?.locked)`);
   await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'ﬂ',code:'KeyH',modifiers:9,text:'ﬂ',windowsVirtualKeyCode:72});
   await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'ﬂ',code:'KeyH',modifiers:9,windowsVirtualKeyCode:72});
   await waitFor(p,`import('./js/relay.js').then(m=>!m.debug().ptt)`);
   assert.equal(await evaluate(p,`document.getElementById('keys').hidden`),false,'stop locked take and open help in the same function press');
   await evaluate(p,`Promise.all([import('./js/five-pages.js').then(m=>m.go('chat')),import('./js/settings.js').then(m=>m.close())])`);
   for(let i=0;i<5;i++)await key(p,'Escape');
   await evaluate(p,`document.getElementById('input').blur()`);await key(p,'?');
   assert.equal(await evaluate(p,`document.getElementById('keys').hidden`),false,'legacy ? opens local offline table');
   const groups=await evaluate(p,`Array.from(document.querySelectorAll('#keys section')).map(e=>e.dataset.group)`);assert.deepEqual(groups,['functional','single','gestures']);
   assert.equal(await evaluate(p,`document.querySelector('#keys a').href`),'https://agentj.app/hotkeys');
   writeFileSync(out+'/'+lang+'-keys.png',await shoot(p,''));
   await key(p,'Escape');await key(p,'s');assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false);
   await evaluate(p,`document.getElementById('keysBtn').click()`);assert.equal(await evaluate(p,`document.getElementById('keys').hidden`),false);
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
  }finally{await p.dispose();}
 }
 writeFileSync(out+'/results.json',JSON.stringify({result:'pass',checks:results.length,navigationChecks:24,results},null,2)+'\n');qualified=true;console.log('PASS P265: '+results.length+' actual focused/unfocused global key checks + zh/en offline table / legacy aliases');
}finally{rec.record('global-shortcuts',qualified);rec.write();await B?.close();await fake?.stop();await web?.stop();}
