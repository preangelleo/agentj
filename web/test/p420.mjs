import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate as ev,shoot,sleep} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const OUT=fileURLToPath(new URL('../../../reports/qa/p420/',import.meta.url));mkdirSync(OUT,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();let checks=0;const results=[];
const check=(v,name)=>{assert.ok(v,name);checks++;results.push(name);};
const click=(p,s)=>ev(p,`document.querySelector(${JSON.stringify(s)}).click()`);
const open=async(p,id)=>{await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget('${id}'))`);await waitFor(p,`document.querySelector('.jc-body[data-card=${id}]')`);};
try{
for(const width of [390,1440])for(const lang of ['zh','en'])for(const scheme of ['dark','light']){
 fake.reset();fake.st.caps=['jcards1','jpalette1'];fake.st.prefs={appearance:{language:lang,theme:scheme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 let palettes=[],colors={},requests=[],delay=false,fail=false;
 const cards=[{id:'official-palette',name:{zh:'配色板',en:'Palette'},desc:{zh:'生成并保存配色',en:'Generate and save palettes'},template:'palette',icon:'board',accent:'magic-carpet',official:true,on:false},{id:'notes',name:{zh:'记事本',en:'Notebook'},desc:{zh:'备忘',en:'Notes'},template:'notes',icon:'note',accent:'emerald-oasis',official:true,on:true}];
 fake.st.onApp=async(c,m,send)=>{
  const reply=(t,v)=>send({t,r:m.r,...v}).then(()=>true);
  if(m.t==='jcard_list')return reply('jcard_items',{cards,colors});
  if(m.t==='jcard_set'){cards.find(c=>c.id===m.id).on=m.on;return reply('jcard_res',{ok:true,id:m.id,on:m.on});}
  if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:m.id,template:m.id==='official-palette'?'palette':'notes',entries:[],actions:[],links:[],anchors:[]});
  if(m.t==='jcard_color'){colors[m.id]=m.color;return reply('jcard_color',{ok:true,id:m.id});}
  if(m.t==='jpalette'){
   requests.push(m);
   if(fail){fail=false;return reply('jpalette',{ok:false,why:'refused'});}
   if(m.op==='save')palettes=[m.palette,...palettes.filter(p=>p.id!==m.palette.id)];
   if(m.op==='delete')palettes=palettes.filter(p=>p.id!==m.id);
   const result={ok:true,palettes:JSON.parse(JSON.stringify(palettes))};
   if(delay){delay=false;setTimeout(()=>reply('jpalette',result),400);return true;}
   return reply('jpalette',result);
  }
  return false;
 };
 const p=await newPage(B,width,844,scheme,{touch:width===390,allow:[web.url,fake.relay+'/']});const tag=`${width}-${lang}-${scheme}`;
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await fake.send({t:'status',s:'idle',agent:'codex',name:'Agent J'});await ev(p,"document.getElementById('phone-env-dismiss')?.click()");await sleep(200);
  await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");
  check(await ev(p,"import('./js/jcards.js').then(m=>!m.deck().some(c=>c.id==='official-palette'))"),'default off '+tag);
  await ev(p,"import('./js/settings.js').then(m=>m.open())");await click(p,'#set-cards-nav');await waitFor(p,"document.querySelector('.jc-setrow[data-card=official-palette] .tgl')");
  await click(p,'.jc-setrow[data-card=official-palette] .tgl');await waitFor(p,"document.querySelector('.jc-setrow[data-card=official-palette] .tgl').getAttribute('aria-checked')==='true'");
  await ev(p,"import('./js/settings.js').then(m=>m.close())");await open(p,'official-palette');await waitFor(p,"document.querySelector('.jp-palette>button')?.disabled===false");
  check(await ev(p,"document.querySelectorAll('.jp-palette>.jp-swatches .jp-swatch').length===5"),'five initial colors '+tag);
  for(const dir of ['analogous','complementary','triadic','split','mono']){
   await ev(p,`(()=>{const e=document.querySelector('.jp-controls select');e.value='${dir}';e.dispatchEvent(new Event('change'));})()`);
   check(await ev(p,"document.querySelectorAll('.jp-palette>.jp-swatches .jp-swatch').length===5"),'direction '+dir+' '+tag);
  }
  await ev(p,"(()=>{const e=document.querySelectorAll('.jp-controls select')[1];e.value='10';e.dispatchEvent(new Event('change'));})()");
  check(await ev(p,"document.querySelectorAll('.jp-palette>.jp-swatches .jp-swatch').length===10"),'ten colors '+tag);
  const before=await ev(p,"document.querySelector('.jp-palette>.jp-swatches').textContent");
  await ev(p,"(()=>{for(const [i,v] of [60,130,20].entries()){const e=document.querySelectorAll('.jp-controls input[type=range]')[i];e.value=v;e.dispatchEvent(new Event('input'));}})()");
  check(before!==await ev(p,"document.querySelector('.jp-palette>.jp-swatches').textContent"),'sliders change colors '+tag);
  // Invalid seed cannot be saved; recovering it restores save.
  await ev(p,"(()=>{const e=document.querySelector('.jp-controls input');e.value='bad';e.dispatchEvent(new Event('input'));})()");check(await ev(p,"document.querySelector('.jp-palette>button').disabled"),'invalid seed '+tag);
  await ev(p,"(()=>{const e=document.querySelector('.jp-controls input');e.value='#abcdef';e.dispatchEvent(new Event('input'));})()");
  const selected=await ev(p,"document.querySelector('.jp-swatch').dataset.color");
  const point=await ev(p,"(()=>{const r=document.querySelector('.jp-swatch').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()");
  await p.send(width===390?'Input.dispatchTouchEvent':'Input.dispatchMouseEvent',width===390?{type:'touchStart',touchPoints:[point]}:{type:'mousePressed',button:'left',clickCount:1,...point});await sleep(650);
  await p.send(width===390?'Input.dispatchTouchEvent':'Input.dispatchMouseEvent',width===390?{type:'touchEnd',touchPoints:[]}:{type:'mouseReleased',button:'left',clickCount:1,...point});
  await waitFor(p,"navigator.clipboard.readText().then(v=>v.startsWith('#'))");
  check((await ev(p,"navigator.clipboard.readText()"))===selected.toUpperCase(),'hold copies HEX '+tag);
  await click(p,'.jp-palette>button');await waitFor(p,"document.querySelector('.jp-saved')");check(palettes.length===1&&palettes[0].colors.length===10,'save host library '+tag);
  check(await ev(p,"[...document.querySelectorAll('.jp-swatch')].every(e=>{const r=e.getBoundingClientRect();return r.height>=44&&r.width>=44})"),'touch targets '+tag);
  check(await ev(p,"document.querySelector('.jp-palette').scrollWidth<=document.querySelector('.jp-palette').clientWidth"),'no horizontal overflow '+tag);
  writeFileSync(OUT+'card-'+tag+'.png',await shoot(p));
  await open(p,'notes');await open(p,'official-palette');await waitFor(p,"document.querySelector('.jp-saved')");check(true,'library survives page navigation '+tag);
  // Existing hidden color hotspot; P412 replaces only the picker above our mount.
  await ev(p,"import('./js/five-pages.js').then(m=>m.toggleMenu())");await waitFor(p,"!document.querySelector('#five-menu').hidden");
  const pt=await ev(p,"(()=>{const r=document.querySelector('.fp-menu-card[data-card=notes]').getBoundingClientRect();return {x:r.right-24,y:r.top+24};})()");
  await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...pt});await sleep(650);await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...pt});
  await waitFor(p,"document.querySelector('.jc-color-dialog[open] .jp-saved')");
  check(await ev(p,"document.querySelectorAll('.jc-color-dialog .jp-saved .jp-swatch').length===10"),'shared library in popup '+tag);
  await ev(p,"document.querySelector('.jc-color-dialog .jp-saved').scrollIntoView({block:'center'})");
  const savedColor=palettes[0].colors[0];await click(p,'.jc-color-dialog .jp-saved .jp-swatch');
  check(await ev(p,`document.querySelector('.jc-color-dialog input[aria-label=HEX]').value==='${savedColor.toUpperCase()}'`),'library color updates picker '+tag);
  writeFileSync(OUT+'popup-'+tag+'.png',await shoot(p));
  await click(p,'.jc-dialog-actions .fp-action');await waitFor(p,"!document.querySelector('.jc-color-dialog')");check(colors.notes===savedColor,'palette applies as card fill '+tag);
  await open(p,'official-palette');await waitFor(p,"document.querySelector('.jp-saved')");
  fail=true;await click(p,'.jp-palette>button');await waitFor(p,"!document.querySelector('.jp-palette>button').disabled");check(palettes.length===1,'failed save preserves library '+tag);
  await click(p,'.jp-saved-head button:last-child');await waitFor(p,"!document.querySelector('.jp-saved')");check(palettes.length===0,'delete '+tag);
  delay=true;await click(p,'.jp-palette>button');await sleep(40);await open(p,'notes');await sleep(450);check(await ev(p,"document.querySelector('.jc-body').dataset.card==='notes'&&!document.querySelector('.jp-palette')"),'late response cannot replace page '+tag);
  await open(p,'official-palette');await waitFor(p,"document.querySelector('.jp-saved')");
  delay=true;await click(p,'.jp-saved-head button:last-child');await sleep(40);
  await ev(p,"import('./js/jcards.js').then(m=>m.clear())");await sleep(450);
  check(await ev(p,"!!document.querySelector('.jp-saved')"),'host reset invalidates mounted library response '+tag);
  await ev(p,"import('./js/jcards.js').then(m=>m.refresh())");
  await ev(p,"import('./js/session.js').then(m=>{m.peer.jpalette1=false;})");
  await open(p,'official-palette');check(!await ev(p,"!!document.querySelector('.jp-controls')"),'old host upgrade hint '+tag);
  check(p.problems.length===0,'no console errors '+tag+': '+p.problems.join('|'));
 }finally{await p.dispose();}
}
writeFileSync(OUT+'browser.json',JSON.stringify({ok:true,checks,results,physicalDevice:false},null,2));console.log(`P420 PASS ${checks} checks`);
}finally{await B.close();await fake.stop();await web.stop();}
const rec=parityRecorder('web/test/p420.mjs');rec.record('p420-palette',true);rec.write();
