// P464: actual paired page, Noise fixture, Chromium pixels and native menu/settings clicks.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const out='reports/qa/p464/matrix';mkdirSync(out,{recursive:true});
const rec=parityRecorder('web/test/p464-matrix.mjs');
const web=await startWebServer(),fake=await startFakeHost(),B=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
const rows=[],failures=[];let ok=false;
try{for(const artwork of ['official','legacy'])for(const width of [390,1280])for(const scheme of ['light','dark']){
 const ctx=await B.newContext({viewport:{width,height:844},colorScheme:scheme}),p=await ctx.newPage();p.setDefaultTimeout(10000);
 await p.route('**/*',r=>['localhost','127.0.0.1'].includes(new URL(r.request().url()).hostname)?r.continue():r.abort());
 try{
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()});
 if(artwork==='legacy')await fake.send({t:'theme',v:1,id:'r2-02-ivory',logo:{png:readFileSync('web/public/brand/img/logo-mark.png').toString('base64'),disc:true}});
 const turn=await fake.addTurn({k:'phone',text:'P464 regression'},'P464 response','done');
 const sample=async(label,custom=false)=>{
 await p.waitForTimeout(50);
 const r=await p.evaluate(async()=>{const b=document.body,orb=document.getElementById('orb');await orb.decode();const c=document.createElement('canvas');c.width=orb.naturalWidth;c.height=orb.naturalHeight;const g=c.getContext('2d');g.drawImage(orb,0,0);const a=[...g.getImageData(c.width/2,c.height/2,1,1).data];
 return {src:orb.getAttribute('src').startsWith('blob:')?'blob:':orb.getAttribute('src'),pixel:'#'+a.slice(0,3).map(v=>v.toString(16).padStart(2,'0')).join(''),state:getComputedStyle(b).getPropertyValue('--state').trim().toLowerCase(),background:getComputedStyle(b).backgroundColor,status:b.dataset.status,kind:b.dataset.kind,conn:b.dataset.conn,agent:b.dataset.agent,v:orb.dataset.v,dance:orb.classList.contains('father-dance'),busy:document.querySelectorAll('#busyDots .rb').length,open:!document.getElementById('settings').hidden||!document.getElementById('five-menu').hidden};});
 rows.push({artwork,width,scheme,label,custom,...r});if(!custom&&r.pixel!==r.state)failures.push({width,scheme,label,pixel:r.pixel,state:r.state});return r;
 };
 for(const state of ['idle','working','waiting','question','offline'])for(const busy of [false,true])for(const replying of [false,true]){
 await fake.send({t:'busy',tasks:busy?Array.from({length:5},(_,i)=>({id:'p464-'+i,who:'Worker '+i,label:'P464 local fixture',c:i,kind:'temp'})):[]});
 await fake.updateTurn(turn.id,{reply:{text:replying?'小助理正在处理，请稍候。':'P464 response'},end:replying?'open':'done'});
 await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':state==='waiting'?'permission':undefined,agent:'claude',name:'Agent J'});
 await p.waitForFunction(s=>document.getElementById('orb').dataset.v===s,state);
 const label=`${state}-busy${+busy}-reply${+replying}`;
 await sample(label+'-chat');
 if(busy&&replying)await p.screenshot({path:`${out}/${artwork}-${width}-${scheme}-${state}.png`});
 await p.locator('#setBtn').click();await p.waitForFunction(()=>!document.getElementById('settings').hidden);await sample(label+'-settings');await p.keyboard.press('Escape');await p.waitForFunction(()=>document.getElementById('settings').hidden);await sample(label+'-back-settings');
 await p.locator('#orb').click();await p.waitForFunction(()=>!document.getElementById('five-menu').hidden);await sample(label+'-menu');
 const chat=p.locator('.fp-menu-card[data-page="chat"]');await chat.click({position:{x:50,y:22}});await p.waitForFunction(()=>document.getElementById('five-menu').hidden);await sample(label+'-back-menu');
 }
 // 20 switches in separate frames and 20 synchronous writes (MutationObserver coalescing).
 for(let i=0;i<20;i++){await p.evaluate(i=>document.body.dataset.status=i%2?'working':'idle',i);await sample('rapid-'+i)}
 await p.evaluate(()=>{for(let i=0;i<20;i++)document.body.dataset.status=i%2?'working':'idle'});await sample('coalesced-20');
 await p.evaluate(()=>document.body.dataset.agent='stopped');await sample('stopped');
 await p.evaluate(()=>{document.body.dataset.agent='idle';document.body.dataset.status='idle';document.body.style.setProperty('--state','#6a9bcc')});await sample('computed-blue-idle');
 await p.evaluate(()=>document.body.style.removeProperty('--state'));
 // Theme-only colours must tint official artwork. Theme-owned static art must remain byte/pixel identical.
 const colors={idle:'#3a6ea5',working:'#1f4e79',waiting:'#b8860b',question:'#6a5acd',offline:'#555555'};
 await fake.send({t:'theme',v:1,id:'p464-colors',colors:{state:colors}});await p.waitForTimeout(180);await sample('theme-colors');
 const own=readFileSync('web/public/brand/img/status/logo-question.png').toString('base64');
 await fake.send({t:'theme',v:1,id:'p464-custom',colors:{state:colors},logo:{png:own}});await p.waitForTimeout(180);
 for(const state of ['idle','working','waiting','question','offline']){
 await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':undefined,agent:'claude'});
 const custom=await sample('custom-logo-'+state,true);assert.equal(custom.v,state);assert.equal(custom.pixel,'#827dbd');
 assert.equal(await p.evaluate(async()=>{const {themeLogo}=await import('./js/theme.js');return document.getElementById('orb').getAttribute('src')===themeLogo()}),true,'custom logo URL unchanged');
 }
 // Even official-green artwork supplied explicitly per-state must remain untouched.
 const green=readFileSync('web/public/brand/img/logo-mark.png').toString('base64');
 await fake.send({t:'theme',v:1,id:'p464-states',logo:{states:Object.fromEntries(['idle','working','waiting','question','offline'].map(s=>[s,green]))}});await p.waitForTimeout(180);
 for(const state of ['idle','working','waiting','question','offline']){
 await fake.send({t:'status',s:state==='question'?'waiting':state==='offline'?'unknown':state,kind:state==='question'?'question':undefined,agent:'claude'});
 const custom=await sample('states-logo-'+state,true);assert.equal(custom.v,state);assert.equal(custom.pixel,'#629887');
 assert.equal(await p.evaluate(async()=>{const {themeLogo}=await import('./js/theme.js');return document.getElementById('orb').getAttribute('src')===themeLogo(document.getElementById('orb').dataset.v)}),true,'per-state logo URL unchanged');
 }
 console.log('PASS',width,scheme,rows.filter(r=>r.artwork===artwork&&r.width===width&&r.scheme===scheme).length);
 }finally{await ctx.close()}
}ok=failures.length===0;assert.deepEqual(failures,[]);
}finally{await B.close();await fake.stop();await web.stop();const combined=JSON.parse(readFileSync('web/public/version.json')).combined;writeFileSync(out+'/results.json',JSON.stringify({ok,combined,rows,failures},null,2)+'\n');rec.record('p464-logo-matrix',ok);rec.write()}
