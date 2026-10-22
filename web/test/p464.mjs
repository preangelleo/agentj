import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const out='reports/qa/p464/'+(process.env.P464_TAG||'after');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
const rows=[],failures=[];
try{
 const ctx=await B.newContext({viewport:{width:390,height:844},colorScheme:'light'}),p=await ctx.newPage();
 await p.route('**/*',r=>['localhost','127.0.0.1'].includes(new URL(r.request().url()).hostname)?r.continue():r.abort());
 fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()});
 await fake.send({t:'status',s:'working',agent:'claude',name:'Agent J'});
 await fake.addTurn({k:'phone',text:'P464 fixture'},'', 'open');
 await p.waitForTimeout(400);
 const sample=async(label)=>{
 const row=await p.evaluate(async()=>{
 const b=document.body,orb=document.getElementById('orb');await orb.decode();const c=document.createElement('canvas');c.width=orb.naturalWidth;c.height=orb.naturalHeight;const g=c.getContext('2d');g.drawImage(orb,0,0);const a=g.getImageData(0,0,c.width,c.height).data,counts={};
 for(let i=0;i<a.length;i+=4){const [r,g,b,alpha]=a.slice(i,i+4);if(alpha!==255||Math.max(r,g,b)-Math.min(r,g,b)<25)continue;const k=[r,g,b].join(',');counts[k]=(counts[k]||0)+1;}
 return {src:orb.getAttribute('src'),v:orb.dataset.v,status:b.dataset.status,kind:b.dataset.kind,conn:b.dataset.conn,agent:b.dataset.agent,state:getComputedStyle(b).getPropertyValue('--state').trim(),background:getComputedStyle(b).backgroundColor,main:Object.entries(counts).sort((a,b)=>b[1]-a[1])[0],pixels:Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,5)};
 });rows.push({label,...row});console.log(label,JSON.stringify(row));return row;};
 await sample('working-open');await p.screenshot({path:out+'/390-working.png'});
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await p.waitForTimeout(100);await sample('idle-open');
 await fake.send({t:'status',s:'working',agent:'claude',name:'Agent J'});await p.waitForTimeout(100);const r=await sample('working-again');
 if(r.main?.[0]!=='106,155,204')failures.push('working robot is not blue');
 await p.evaluate(()=>document.body.dataset.agent='stopped');await p.waitForTimeout(100);const stopped=await sample('stopped-working');if(stopped.v!=='offline')failures.push('stopped background is offline but logo remains '+stopped.v);
 await p.evaluate(()=>{document.body.dataset.agent='idle';document.body.dataset.status='idle';document.body.style.setProperty('--state','#6a9bcc')});await p.waitForTimeout(100);const override=await sample('computed-blue-idle');if(override.main?.[0]!=='106,155,204')failures.push('computed blue background but green logo');
}finally{await B.close();await fake.stop();await web.stop();writeFileSync(out+'/results.json',JSON.stringify({rows,failures},null,2)+'\n')}
assert.deepEqual(failures,[]);
