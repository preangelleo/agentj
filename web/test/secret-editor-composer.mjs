// Ordinary shared-editor regression after leaving secret mode; no production service or protocol changes.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitFor,waitState,key,sleep,shoot} from './browser.mjs';
const out=process.env.OLARES_RUN_OUT||'reports/qa/secret-editor-composer';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0}),fake=await startFakeHost(),B=await launch();let checks=0;
const ok=(v,msg)=>{assert.ok(v,msg);checks++;console.log('PASS',msg);};
async function click(p,selector,touch){const r=await ev(p,`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);if(touch){await p.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[r]});await p.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});}else{await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...r});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...r});}}
try{for(const theme of ['light','dark'])for(const width of [390,1440]){
 const touch=width===390;fake.reset();fake.st.prefs={appearance:{language:'zh',theme}};fake.st.menu={kind:'claude',source:'default',cmds:['clear','compact'],items:[],skills:[],native:[{cmd:'/init',desc:'Initialise project',state:'run'}]};
 const p=await newPage(B,width,844,theme,{touch,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await sleep(250);
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').value='ordinary draft';document.getElementById('input').dispatchEvent(new Event('input'));`);
 await ev(p,`import('./js/fsedit.js').then(m=>{window.editorApi=m;m.openText({secret:true,focus:true,title:'Secret fixture'});})`);
 await p.send('Input.insertText',{text:'disposable-hidden-editor-fixture'});
 await ev(p,`window.editorApi.close()`);
 ok(await ev(p,`document.getElementById('fsText').tagName==='TEXTAREA'&&document.getElementById('input').value==='ordinary draft'&&!document.getElementById('fsEd').hasAttribute('data-secret')`),'secret exit restores ordinary textarea/actions and preserves composer');
 await ev(p,`document.getElementById('input').focus();document.getElementById('input').setSelectionRange(8,8)`);await key(p,'g',{ctrl:true});await waitFor(p,`!document.getElementById('fsEd').hidden`);
 ok(await ev(p,`document.getElementById('fsText').value==='ordinary draft'&&document.getElementById('fsText').selectionStart===8&&document.activeElement.id==='fsText'`),'Ctrl+G restores ordinary draft/caret/focus after secret mode');
 await ev(p,`document.getElementById('fsText').setSelectionRange(14,14)`);await p.send('Input.insertText',{text:' continued'});
 ok(await ev(p,`document.getElementById('input').value==='ordinary draft continued'`),'ordinary typing still mirrors into composer draft');
 await click(p,'#fsSlash',touch);await waitFor(p,`!document.getElementById('fsCmds').hidden`);
 ok(await ev(p,`import('./js/layers.js').then(m=>m.activeLayer()?.element()?.id==='fsCmds')`),'command menu owns the higher ordinary editor sublayer');
 await key(p,'Escape');await sleep(80);
 ok(await ev(p,`!document.getElementById('fsEd').hidden&&document.getElementById('fsCmds').hidden`),'Escape closes only command menu and retains ordinary editor');
 await click(p,'#fsSlash',touch);await waitFor(p,`!document.getElementById('fsCmds').hidden`);
 await ev(p,`document.getElementById('fsCmdQ').focus()`);await p.send('Input.insertText',{text:'init'});await sleep(60);
 ok(await ev(p,`document.querySelectorAll('#fsCmdList button').length===1&&document.querySelector('#fsCmdList button b').textContent.includes('/init')`),'ordinary command filter still uses the current harness menu');
 await click(p,'#fsCmdList button',touch);await sleep(100);
 ok(await ev(p,`document.getElementById('fsText').value==='ordinary draft continued /init '&&document.getElementById('input').value==='ordinary draft continued /init '&&document.getElementById('fsCmds').hidden`),'real pointer command selection inserts at caret and updates ordinary draft');
 await ev(p,`document.getElementById('fsText').focus()`);await key(p,'a',{ctrl:true});
 ok(await ev(p,`document.getElementById('fsText').selectionStart===0`),'ordinary terminal Ctrl+A remains bound after secret mode');
 await key(p,'e',{ctrl:true});ok(await ev(p,`document.getElementById('fsText').selectionStart===document.getElementById('fsText').value.length`),'ordinary terminal Ctrl+E remains bound after secret mode');
 writeFileSync(`${out}/${theme}-${width}.png`,await shoot(p,''));await click(p,'#fsExit',touch);await sleep(100);
 ok(await ev(p,`document.getElementById('fsEd').hidden&&document.getElementById('input').value==='ordinary draft continued /init '`),'real pointer exit preserves ordinary draft without sending');
 ok(fake.st.says.length===0,'ordinary command insertion and exiting send no chat message');
 }finally{await p.dispose();}
}console.log(`ordinary shared editor ${checks} PASS`);}finally{await B.close();await fake.stop();await web.stop();}
