// P489 investigation: replay public host fields through the real encrypted fake-host seam.
// No product fix is implied; failures remain failures. Physical iOS UI is not emulated.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const out='reports/qa/p489/replay';mkdirSync(out,{recursive:true});
if(process.argv.includes('--audit')){
 const r=JSON.parse(readFileSync(out+'/results.json'));writeFileSync(out+'/results-original-audit.json',JSON.stringify(r,null,2)+'\n');r.failures=[];
 for(const row of r.rows){const raw=execFileSync('magick',[row.path,'-depth','8','rgb:-'],{maxBuffer:8e6});row.bottomBad=Array.from({length:34},(_,i)=>878+i).filter(y=>[1,418].some(x=>row.target.some((v,i)=>Math.abs(raw[(y*420+x)*3+i]-v)>1)));if(row.bottomBad.length)r.failures.push(row.mode+'-'+row.label+': edge pixels do not match visible layer')}
 r.audit='Immutable PNG audit: x=210 crosses the existing composer input in tab mode; sample x=1/418 only. All original failures retained in results-original-audit.json. No product changed.';writeFileSync(out+'/results.json',JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({cases:r.rows.length,failures:r.failures},null,2));assert.equal(r.failures.length,0);process.exit(0);
}
const hosts=JSON.parse(readFileSync('reports/qa/p489/hosts.json'));
const web=await startWebServer(),fake=await startFakeHost(),browser=await pw.webkit.launch();
const rows=[],failures=[];
try{for(const mode of ['safari','chrome','standalone']){
 const ctx=await browser.newContext({...pw.devices['iPhone 15'],viewport:{width:420,height:912},deviceScaleFactor:1,colorScheme:'dark',serviceWorkers:'block',...(mode==='chrome'?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1'}:{})});
 await ctx.addInitScript(s=>{Object.defineProperty(navigator,'standalone',{get:()=>s});window.Audio=undefined;const D=Date;window.Date=class extends D{constructor(...a){super(...(a.length?a:[1792740339000]))}static now(){return 1792740339000}}},mode==='standalone');const p=await ctx.newPage();
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();if(u.pathname.endsWith('.css')){const res=await r.fetch();return r.fulfill({response:res,body:(await res.text()).replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:'68px',bottom:'34px',left:'0px',right:'0px'}[s]))})}return r.continue()});
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()});
 const capture=async(label)=>{
  await p.waitForTimeout(1700);
  await p.evaluate(async()=>{await document.fonts.ready;const s=document.createElement('style');s.id='p489-still';s.textContent='.wave{animation-play-state:paused!important}*{caret-color:transparent!important}';document.head.append(s)});
  const m=await p.evaluate(()=>{const w=document.getElementById('water'),s=getComputedStyle(w),color=e=>{const c=document.createElement('canvas').getContext('2d');c.fillStyle=getComputedStyle(e).backgroundColor;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data].slice(0,3)};return {water:color(w),page:color(document.getElementById('chromeProbe')),band:color(document.getElementById('sabband')),token:document.documentElement.style.getPropertyValue('--ios-bottom'),rect:w.getBoundingClientRect().toJSON(),body:document.body.getBoundingClientRect().toJSON(),known:w.dataset.known,compacting:w.dataset.compacting,theme:document.documentElement.dataset.theme||'system',html:{...document.documentElement.dataset}}});
  const path=`${out}/${mode}-${label}.png`;await p.screenshot({path});
  const raw=execFileSync('magick',[path,'-depth','8','rgb:-'],{maxBuffer:8e6}),pixel=(x,y)=>[...raw.slice((y*420+x)*3,(y*420+x)*3+3)];
  const visible=m.known==='1'&&m.rect.height>0&&m.rect.top<912&&m.rect.bottom>=911,target=visible?m.water:m.page;
  const bad=Array.from({length:34},(_,i)=>878+i).filter(y=>[1,418].some(x=>pixel(x,y).some((v,i)=>Math.abs(v-target[i])>1)));
  if(bad.length)failures.push(`${mode}-${label}: bottom != visible ${visible?'water':'page'}`);
  rows.push({mode,label,path,...m,target,bottomBad:bad});await p.evaluate(()=>document.getElementById('p489-still')?.remove());
 };
 for(const h of hosts){
  await fake.send(h.theme);await fake.send({t:'preferences',value:{appearance:{theme:h.appearance,language:'zh'}}});
  await fake.send({t:'status',s:'idle',agent:h.agent,name:h.name});await fake.send({t:'meter',...h.meter});await capture(h.id);
 }
 // Screenshot-era values are explicitly reconstructed, not an archived host capture.
 await fake.send({t:'theme',v:1,id:'robots',factory:'robots'});await fake.send({t:'preferences',value:{appearance:{theme:'dark',language:'zh'}}});
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent Air'});await fake.send({t:'meter',model:'claude-opus-5-5',effort:'medium',ctx:{pct:72},h5:{pct:9},week:{pct:67}});await capture('air-screenshot-reconstruction');
 // Host confirmed recent compacting; exercise it without altering a real host.
 await fake.send({t:'status',s:'compacting',agent:'claude',name:'Agent Air'});await p.waitForTimeout(14500);
 await fake.send({t:'meter',model:'claude-opus-5-5',ctx:{used:118035,max:1000000}});await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent Air'});await capture('air-after-compacting');
 await ctx.close();
}}finally{await browser.close();await fake.stop();await web.stop()}
const result={base:'994f63fc8',combined:execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim(),rows,failures,model:'WebKit 420x912, synthetic safe top68/bottom34, Safari/CriOS UA/Home Screen. OS UI excluded. Screenshot-era context72% inferred from water line; quotas9/67 read from screenshot; current host samples are later and non-atomic.'};
writeFileSync(out+'/results.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({cases:rows.length,failures},null,2));assert.equal(failures.length,0);
