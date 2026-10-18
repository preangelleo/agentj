// P386 regression: real touch / mouse hold, dismiss, then the first ordinary tap.
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,sleep} from './browser.mjs';
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch(),results=[];
try{for(const width of [390,1440]){
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:'en',theme:'dark'},voice:{speak_replies:false,wake_enabled:false}};
 fake.st.onApp=async(c,m,send)=>{if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:[{id:'notes',on:true,name:{en:'Notebook'},accent:'emerald-oasis',icon:'note',plus:'agentj-add-note'}]});return true;}if(m.t==='jcard_get'){await send({t:'jcard_get',r:m.r,ok:true,id:'notes',template:'notes',entries:[{id:'first',text:'Example memo',editable:true}],revision:'r1'});return true;}return false;};
 const p=await newPage(B,width,844,'dark',{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'codex'});await evaluate(p,"document.getElementById('phone-env-dismiss')?.click()");await evaluate(p,"import('./js/jcards.js').then(m=>m.refresh())");
 const down=(x,y)=>p.send(width===390?'Input.dispatchTouchEvent':'Input.dispatchMouseEvent',width===390?{type:'touchStart',touchPoints:[{x,y}]}:{type:'mousePressed',button:'left',clickCount:1,x,y});
 const up=(x,y)=>p.send(width===390?'Input.dispatchTouchEvent':'Input.dispatchMouseEvent',width===390?{type:'touchEnd',touchPoints:[]}:{type:'mouseReleased',button:'left',clickCount:1,x,y});
 for(const dismiss of ['cancel','escape']){await evaluate(p,"import('./js/five-pages.js').then(m=>m.toggleMenu())");await waitFor(p,"!document.querySelector('#five-menu').hidden");const {x,y}=await evaluate(p,"(()=>{const r=document.querySelector('.fp-menu-card[data-card=notes]').getBoundingClientRect();return {x:r.right-24,y:r.top+24}})()");await down(x,y);await waitFor(p,"!!document.querySelector('.jc-color-dialog[open]')");await up(x,y);if(dismiss==='cancel')await evaluate(p,"document.querySelector('.jc-dialog-actions button:nth-child(2)').click()");else{await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});}await waitFor(p,"!document.querySelector('.jc-color-dialog')");await down(x,y);await up(x,y);await sleep(200);assert.ok(await evaluate(p,"!document.querySelector('#five-page').hidden&&document.querySelector('.jc-body')?.dataset.card==='notes'"),`${width} ${dismiss}: first ordinary card tap`);results.push(`${width===390?'touch emulation':'mouse'} ${dismiss}: first tap opens same card`);}
 }finally{await p.dispose();}
}writeFileSync(fileURLToPath(new URL('../../../reports/qa/p386/web-dismiss.json',import.meta.url)),JSON.stringify({ok:true,checks:results.length,results},null,2));console.log('P386 dismiss PASS '+results.length);}finally{await B.close();await fake.stop();await web.stop();}
