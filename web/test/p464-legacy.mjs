// Exact legacy theme replay; no live host calls or state writes. Default art is a portable green control.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const art=readFileSync(process.env.P464_LOGO_FILE||'web/public/brand/img/logo-mark.png');
const out='reports/qa/p464/legacy'+(process.env.P464_BASELINE==='1'?'-before':'');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{
 const ctx=await B.newContext({viewport:{width:390,height:844},colorScheme:'dark'}),p=await ctx.newPage();
 await p.route('**/*',r=>{const u=new URL(r.request().url());if(!['localhost','127.0.0.1'].includes(u.hostname))return r.abort();if(process.env.P464_BASELINE==='1'&&['/js/logo.js','/js/relay.js'].includes(u.pathname))return r.fulfill({status:200,contentType:'text/javascript',body:execFileSync('git',['show','e96cc19ae:web/public'+u.pathname])});return r.continue()});
 fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()});
 await fake.send({t:'theme',v:1,id:'r2-02-ivory',colors:{state:{idle:'#629887',working:'#6a9bcc',waiting:'#d97757',question:'#827dbd',offline:'#858585'}},logo:{png:art.toString('base64'),disc:true}});
 await fake.addTurn({k:'phone',text:'P464 isolated replay'},'小助理正在处理，请稍候。','open');
 await fake.send({t:'busy',tasks:Array.from({length:5},(_,i)=>({id:'p464-'+i,who:'Worker '+i,label:'Local fixture',kind:'temp',c:i}))});
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await p.waitForTimeout(100);
 await fake.send({t:'status',s:'working',agent:'claude',name:'Agent J'});await p.waitForTimeout(250);
 const r=await p.evaluate(async()=>{const b=document.body,o=document.getElementById('orb');await o.decode();const c=document.createElement('canvas');c.width=o.naturalWidth;c.height=o.naturalHeight;const g=c.getContext('2d');g.drawImage(o,0,0);const a=g.getImageData(c.width/2,c.height/2,1,1).data;
 return {status:b.dataset.status,kind:b.dataset.kind||null,conn:b.dataset.conn,state:getComputedStyle(b).getPropertyValue('--state').trim(),background:getComputedStyle(b).backgroundColor,src:o.getAttribute('src').startsWith('blob:')?'blob:':'official',selectedState:o.dataset.v,pixel:'#'+[...a].slice(0,3).map(x=>x.toString(16).padStart(2,'0')).join(''),busy:document.querySelectorAll('#busyDots .rb').length,theme:document.documentElement.dataset.look};});
 r.art_sha256=createHash('sha256').update(art).digest('hex');r.reproduced=r.pixel==='#629887'&&r.state==='#6a9bcc'&&r.busy===5;r.note='Single official-green theme artwork, five background workers and open reply; exact body pixel vs CSS state.';
 writeFileSync(out+'/results.json',JSON.stringify(r,null,2)+'\n');await p.screenshot({path:out+'/390-dark-exact-theme.png'});console.log(JSON.stringify(r));
 if(process.env.P464_BASELINE==='1')assert.equal(r.reproduced,true,'positive reproduction of legacy static logo mismatch');
 else assert.equal(r.pixel,r.state,'official-derived single theme artwork follows background');
 if(process.env.P464_BASELINE!=='1'){
 const preserved=await p.evaluate(async()=>{const o=document.getElementById('orb'),{themeLogo}=await import('./js/theme.js');const im=new Image();im.src=themeLogo();await im.decode();await o.decode();const c=document.createElement('canvas');c.width=im.naturalWidth;c.height=im.naturalHeight;const g=c.getContext('2d');g.drawImage(im,0,0);const before=g.getImageData(0,0,c.width,c.height).data;g.clearRect(0,0,c.width,c.height);g.drawImage(o,0,0);const after=g.getImageData(0,0,c.width,c.height).data;let changed=0;for(let i=0;i<before.length;i+=4){const tint=before[i]===98&&before[i+1]===152&&before[i+2]===135&&before[i+3]>0;const expected=tint?[106,155,204,before[i+3]]:[...before.slice(i,i+4)];if(tint)changed++;for(let k=0;k<4;k++)if(after[i+k]!==expected[k])return {ok:false,i,k};}return {ok:true,changed};});assert.equal(preserved.ok,true,JSON.stringify(preserved));assert.ok(preserved.changed>0);console.log('PASS exact pixel replacement; all other RGBA bytes unchanged',preserved.changed);
 }
}finally{await B.close();await fake.stop();await web.stop()}
