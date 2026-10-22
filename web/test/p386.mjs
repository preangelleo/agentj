// P386 real web client + local Noise fake host. iOS dimensions/touch are emulation, never a physical-device claim.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const OUT=fileURLToPath(new URL('../../../reports/qa/p386/',import.meta.url));
const ev=evaluate,q=JSON.stringify;
const click=(p,sel)=>ev(p,`document.querySelector(${q(sel)}).click()`);
async function open(p,id,at){await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget(${q(id)},${q(at)}))`);await waitFor(p,`document.querySelector('.jc-body[data-card=${id}]')`);await sleep(100);}
mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();let checks=0;const results=[];
// P417: features.json also names single checks (e.g. 「P412 #177: …」) — record each one without its width-lang-scheme tag (fail sticks)
const rec=parityRecorder('web/test/p386.mjs');
const check=(x,name)=>{rec.record(name.replace(/ \d+-(?:zh|en)-(?:dark|light)$/,''),!!x);assert.ok(x,name);checks++;results.push(name);};
try{
for(const width of [390,1440])for(const lang of ['zh','en'])for(const scheme of ['dark','light']){
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang,theme:scheme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 let entries=[{id:'founder',text:lang==='zh'?'创始人码 · 测试保留记录':'Founder code · preserved fixture',editable:true},{id:'address',text:lang==='zh'?'预约地点：花园餐厅\n电话：010-5550-0000':'Meeting: Garden restaurant\nPhone: 010-5550-0000',editable:true}],colors={},notes=[],colorOps=[],conflict=false,delayNote=null,delayGet=false,delayColor=false;
 const cards=[{id:'notes',name:{zh:'记事本',en:'Notebook'},desc:{zh:'常用地址、电话和备忘',en:'Addresses, phone numbers and memos'},icon:'note',accent:'emerald-oasis',on:true,official:true,plus:'agentj-add-note'},{id:'automation',name:{zh:'自动化',en:'Automation'},desc:{zh:'后台运行的自动化',en:'Scheduled background work'},icon:'calendar',accent:'magic-carpet',on:false,official:true,plus:'agentj-automation'}];
 fake.st.onApp=async(c,m,send)=>{
  const reply=async(t,v)=>{if(t==='jcard_get'&&m.id==='notes'&&delayGet){delayGet=false;setTimeout(()=>send({t,r:m.r,...v}),500);return true;}await send({t,r:m.r,...v});return true;};
  if(m.t==='jcard_list')return reply('jcard_items',{cards,colors});
  if(m.t==='jcard_get')return reply('jcard_get',m.id==='notes'?{ok:true,id:'notes',template:'notes',entries,text:'# Archive\n\n- repeated note\n\n## Address\n\n- repeated note',anchors:[{id:'address-section',heading:'Address'}],revision:'test-revision',actions:[{id:'add',label:lang==='zh'?'记一条':'Add a note',prompt:'/agentj-add-note ',draft:true}]}:{ok:true,id:'automation',template:'automation',items:[{id:'morning-brief',name:'morning-brief',source:'systemd',schedule:[{k:'cal',v:'*-*-* 08:00:00'}],repeat:[{k:'every',v:86400}],next:1792364400000000,harness:'Codex',model:'GPT-6',description:lang==='zh'?'整理每日新闻摘要':'Prepare the daily news briefing',prompt:'/agentj-automation @morning-brief'}]});
  if(m.t==='jcard_note'){notes.push(m);if(delayNote){const why=delayNote;delayNote=null;if(why==='ok'&&m.op==='edit')entries=entries.map(x=>x.id===m.entry?{...x,text:m.text}:x);setTimeout(()=>reply('jcard_note',{ok:why==='ok',why,id:m.id}),500);return true;}if(conflict){conflict=false;return reply('jcard_note',{ok:false,why:'conflict',id:m.id});}if(m.op==='edit')entries=entries.map(x=>x.id===m.entry?{...x,text:m.text}:x);else entries=entries.filter(x=>x.id!==m.entry);return reply('jcard_note',{ok:true,id:m.id});}
  if(m.t==='jcard_color'){colorOps.push(m);if(delayColor){delayColor=false;setTimeout(()=>reply('jcard_color',{ok:true,id:m.id}),500);return true;}if(m.color===null)delete colors[m.id];else colors[m.id]=m.color;return reply('jcard_color',{ok:true,id:m.id});}
  if(m.t==='jcard_set'){cards.find(c=>c.id===m.id).on=m.on;return reply('jcard_res',{ok:true,id:m.id,on:m.on});}return false;
 };
 const p=await newPage(B,width,844,scheme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'codex',name:'Agent J'});await ev(p,"document.getElementById('phone-env-dismiss')?.click()");await sleep(300);
 await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");
 const tag=`${width}-${lang}-${scheme}`;check(await ev(p,`document.documentElement.dataset.theme===${q(scheme)}`),'actual html theme '+tag);
 await open(p,'notes','latest');await waitFor(p,"document.querySelectorAll('.jc-entry').length===2");
 check(await ev(p,"document.querySelector('.jc-entry[data-at=\"1\"]')?.dataset.item==='founder'"),'latest '+tag);
 check(await ev(p,"[...document.querySelectorAll('.jc-entry-icon')].every(b=>{const r=b.getBoundingClientRect();return r.width>=44&&r.height>=44})"),'44px note icons '+tag);
 check(await ev(p,"[...document.querySelectorAll('.jc-entry')].every(e=>e.scrollWidth<=e.clientWidth)"),'notes fit '+tag);
 writeFileSync(join(OUT,'web-notes-'+tag+'.png'),await shoot(p));await open(p,'notes','address');await waitFor(p,"document.querySelector('.jc-entry[data-at=\"1\"]')?.dataset.item==='address'");check(true,'entry id anchor '+tag);await open(p,'notes','address-section');await waitFor(p,"document.querySelector('.jc-entry[data-at=\"1\"]')?.dataset.item==='address'");check(true,'legacy heading source order duplicate text '+tag);
 await click(p,'.jc-entry[data-item="address"] [data-note-op="copy"]');await waitFor(p,`document.querySelector('[data-item="address"] [data-note-op="copy"]').getAttribute('aria-label')===${q(lang==='zh'?'已复制':'Copied')}`);check(true,'copy '+tag);
 await click(p,'.jc-entry[data-item="address"] [data-note-op="edit"]');await waitFor(p,"document.querySelector('.jc-dialog[open]')");await ev(p,"document.querySelector('.jc-dialog textarea').value='Updated address';[...document.querySelectorAll('.jc-dialog button')].at(-1).click()");await waitFor(p,"document.querySelector('[data-item=address] .fp-prose')?.textContent==='Updated address'");check(notes.at(-1).revision==='test-revision'&&notes.at(-1).op==='edit','revision edit '+tag);
 conflict=true;await click(p,'.jc-entry[data-item="address"] [data-note-op="edit"]');await waitFor(p,"document.querySelector('.jc-dialog[open]')");await ev(p,"[...document.querySelectorAll('.jc-dialog button')].at(-1).click()");await waitFor(p,"document.querySelector('#five-body>.jc-note')");check(entries[1].text==='Updated address','conflict preserves entry '+tag);
 await click(p,'.jc-entry[data-item="address"] [data-note-op="delete"]');await waitFor(p,"!document.querySelector('#confirm').hidden");await ev(p,"document.querySelector('#confirm-yes').click()");await waitFor(p,"document.querySelectorAll('.jc-entry').length===1");check(entries[0].id==='founder','delete preserves founder '+tag);
 await click(p,'.jc-actions [data-act=collaborate]');   // P415: 记一条 opens the direct-save dialog; 协作 drafts the skill
 await waitFor(p,"document.querySelector('#input').value.startsWith('/agentj-add-note')");check(await ev(p,"document.querySelector('#five-page').hidden"),'slash draft returns chat '+tag);check(await ev(p,"document.querySelector('#input').value.startsWith('/agentj-add-note ')"),'P393 #131: the drafted command ends in a space '+tag);
 await ev(p,"import('./js/five-pages.js').then(m=>m.toggleMenu())");await waitFor(p,"!document.querySelector('#five-menu').hidden");
 const point=()=>ev(p,"(()=>{const r=document.querySelector('[data-card=notes].fp-menu-card').getBoundingClientRect();return {x:r.right-24,y:r.top+24};})()");
 const down=async(x,y)=>p.send(width===390?'Input.dispatchTouchEvent':'Input.dispatchMouseEvent',width===390?{type:'touchStart',touchPoints:[{x,y}]}:{type:'mousePressed',button:'left',clickCount:1,x,y});
 const up=async(x,y)=>p.send(width===390?'Input.dispatchTouchEvent':'Input.dispatchMouseEvent',width===390?{type:'touchEnd',touchPoints:[]}:{type:'mouseReleased',button:'left',clickCount:1,x,y});
 const longpress=async()=>{const {x,y}=await point();await down(x,y);await waitFor(p,"document.querySelector('.jc-color-dialog[open]')");await up(x,y);};
 const start=await point();await down(start.x,start.y);await p.send(width===390?'Input.dispatchTouchEvent':'Input.dispatchMouseEvent',width===390?{type:'touchMove',touchPoints:[{x:start.x-40,y:start.y}]}:{type:'mouseMoved',button:'left',x:start.x-40,y:start.y});await sleep(700);await up(start.x-40,start.y);check(!await ev(p,"!!document.querySelector('.jc-color-dialog[open]')"),'moving cancels longpress '+tag);
 if(await ev(p,"document.querySelector('#five-menu').hidden"))await ev(p,"import('./js/five-pages.js').then(m=>m.toggleMenu())");
 await longpress();check(await ev(p,"!!document.querySelector('.jc-color-dialog .jc-sv')&&!!document.querySelector('.jc-color-dialog input.jc-hue')&&document.querySelectorAll('.jc-color-rec .jc-tone').length===18"),'P412 #177: continuous picker + the palette as suggestions '+tag);writeFileSync(join(OUT,'web-palette-'+tag+'.png'),await shoot(p));
 await ev(p,"(()=>{const e=document.querySelector('.jc-color-dialog input[aria-label=HEX]');e.value='#bbbbbb';e.dispatchEvent(new Event('input'));})()");check(await ev(p,"!document.querySelector('.jc-dialog-actions .fp-action').disabled&&document.querySelector('.jc-color-preview').style.color==='rgb(20, 20, 20)'"),'P412 #177: a light fill gets Sooty words and can be saved '+tag);
 await ev(p,"(()=>{const e=document.querySelector('.jc-color-dialog input[aria-label=HEX]');e.value='#12';e.dispatchEvent(new Event('input'));})()");check(await ev(p,"(()=>{const b=document.querySelector('.jc-dialog-actions .fp-action'),w=document.querySelector('.jc-color-why');return b.disabled&&getComputedStyle(b).opacity==='0.4'&&getComputedStyle(b).cursor==='not-allowed'&&w.textContent.length>3&&getComputedStyle(w).display!=='none';})()"),'P458 native disabled state: invalid input disables Save and shows why '+tag);
 await ev(p,"(()=>{const e=document.querySelector('.jc-color-dialog input[aria-label=HEX]');e.value='#050505';e.dispatchEvent(new Event('input'));})()");check(await ev(p,"document.querySelector('.jc-color-preview').style.color==='rgb(250, 249, 245)'"),'Lotion words on a dark fill '+tag);
 await ev(p,"(()=>{const e=document.querySelector('.jc-color-dialog input[aria-label=HEX]');e.value='#6a9bcc';e.dispatchEvent(new Event('input'));})()");check(await ev(p,"!document.querySelector('.jc-dialog-actions .fp-action').disabled&&document.querySelector('.jc-color-preview').style.color==='rgb(20, 20, 20)'"),'P448: a system-card button fill gets >=4.5:1 ink and saves '+tag);
 await ev(p,"(()=>{const e=document.querySelector('.jc-color-dialog input[aria-label=HEX]');e.value='#050505';e.dispatchEvent(new Event('input'));})()");
 await ev(p,"document.querySelector('.jc-dialog-actions .fp-action').click()");await waitFor(p,"!document.querySelector('.jc-color-dialog')");check(colorOps.at(-1).color==='#050505','color saved to host '+tag);
 await longpress();await p.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await p.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await waitFor(p,"!document.querySelector('.jc-color-dialog')");check(true,'Escape '+tag);await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");check(await ev(p,"document.querySelector('[data-card=notes].fp-menu-card').style.backgroundColor==='rgb(5, 5, 5)'"),'saved color reload '+tag);
 await longpress();await click(p,'.jc-dialog-actions button:first-child');await waitFor(p,"!document.querySelector('.jc-color-dialog')");check(colorOps.at(-1).color===null,'reset host color '+tag);
 const short=await point();await down(short.x,short.y);await up(short.x,short.y);await sleep(100);check(!await ev(p,"!!document.querySelector('.jc-color-dialog[open]')"),'short tap does not open palette '+tag);if(await ev(p,"document.querySelector('#five-menu').hidden"))await ev(p,"import('./js/five-pages.js').then(m=>m.toggleMenu())");
 // Default-off automation remains in settings, opt-in appears in deck.
 check(await ev(p,"!document.querySelector('.fp-menu-card[data-card=automation]')"),'automation default off '+tag);
 await ev(p,"import('./js/jcards.js').then(m=>m.setOn('automation',true))");await waitFor(p,"document.querySelector('.fp-menu-card[data-card=automation]')");await open(p,'automation');await waitFor(p,"document.querySelector('.jc-automation')");writeFileSync(join(OUT,'web-automation-'+tag+'.png'),await shoot(p));
 check(await ev(p,"document.querySelector('.jc-automation').textContent.includes('morning-brief')&&document.querySelectorAll('.jc-automation dt').length===7"),'automation read-only fields '+tag);
 await click(p,'.jc-auto-at');await waitFor(p,"document.querySelector('#input').value.includes('/agentj-automation @morning-brief')");check(true,'unique automation mention '+tag);
 for(const mode of ['ok','conflict','refresh']) { await open(p,'notes');await waitFor(p,"document.querySelector('.jc-entry [data-note-op=edit]')");await click(p,'.jc-entry [data-note-op=edit]');await waitFor(p,"document.querySelector('.jc-dialog[open]')");if(mode==='refresh'){conflict=true;delayGet=true;}else delayNote=mode;await ev(p,"[...document.querySelectorAll('.jc-dialog button')].at(-1).click()");await sleep(100);await open(p,'automation');await sleep(650);check(await ev(p,"document.querySelector('.jc-body')?.dataset.card==='automation'&&!document.querySelector('#five-body>.jc-note')"),'late note '+mode+' keeps selected card '+tag); } // A host change clears editors and invalidates the old host's color callbacks.
 await open(p,'notes');await click(p,'.jc-entry [data-note-op=edit]');await waitFor(p,"document.querySelector('.jc-dialog[open]')");const priorNotes=notes.length;await ev(p,"import('./js/jcards.js').then(m=>m.clear())");await waitFor(p,"!document.querySelector('.jc-dialog')");check(notes.length===priorNotes,'host reset cancels note editor '+tag);
 await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");await ev(p,"import('./js/five-pages.js').then(m=>m.toggleMenu())");await waitFor(p,"!document.querySelector('#five-menu').hidden");await longpress();delayColor=true;await click(p,'.jc-dialog-actions .fp-action');await sleep(100);await ev(p,"Promise.all([import('./js/jcards.js'),import('./js/jcard-color.js')]).then(([cards,color])=>{cards.clear();color.setColors({notes:'#827dbd'});})");await waitFor(p,"!document.querySelector('.jc-color-dialog')");await sleep(650);check(await ev(p,"document.querySelector('[data-card=notes].fp-menu-card').style.backgroundColor==='rgb(130, 125, 189)'"),'late old host color cannot replace new host color '+tag);
 check(p.problems.length===0,'no console errors '+tag+': '+p.problems.join('|'));
 } finally{await p.dispose();}
}
writeFileSync(join(OUT,'web-browser.json'),JSON.stringify({ok:true,checks,results,ios:'390px Chromium touch emulation; not a physical iOS device'},null,2));console.log(`P386 PASS ${checks} checks`);
}finally{await B.close();await fake.stop();await web.stop();}

await import('./p386-dismiss.mjs');
rec.record('p386-cards',true);rec.write();
