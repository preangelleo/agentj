// P488: shipped WebKit, live height transitions; synthetic safe insets are explicit.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,writeFileSync,readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const base='f7f1c526f',baseline=process.argv.includes('--baseline'),out=`reports/qa/p488/${baseline?'before':'after'}`;
mkdirSync(out,{recursive:true});
if(process.argv.includes('--audit')){
 const r=JSON.parse(readFileSync(out+'/results.json'));
 // Preserve the original browser run. Re-audit immutable PNGs without rerendering.
 writeFileSync(out+'/results-original-audit.json',JSON.stringify(r,null,2)+'\n');
 r.failures=r.failures.filter(f=>!f.endsWith(' no internal horizontal band'));
 for(const row of r.rows){row.excludedForegroundRows=row.tankBad.filter(y=>y<Math.max(row.top,row.height/2));row.tankBad=row.tankBad.filter(y=>y>=Math.max(row.top,row.height/2));if(row.tankBad.length)r.failures.push(row.label+' no internal horizontal band')}
 r.audit='Immutable screenshot reuse: tank samples start at mid-screen, excluding fixed Noir status paint and upper quota divider. All remaining page pixels independently checked against baseline by p488-compare.mjs.';
 writeFileSync(out+'/results.json',JSON.stringify(r,null,2)+'\n');
 console.log(JSON.stringify({cases:r.rows.length,failures:r.failures,audit:r.audit},null,2));if(!baseline)assert.equal(r.failures.length,0);process.exit(0);
}
const temp=mkdtempSync('/tmp/p488-base-');
if(baseline)execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive',base,'web/public'],{maxBuffer:100e6})});
const web=await startWebServer(baseline?{publicDir:temp+'/web/public'}:{}),fake=await startFakeHost(),browser=await pw.webkit.launch();
const rows=[],failures=[],ok=(v,s)=>{if(!v)failures.push(s)},now=1792737780000;
const rgb=s=>{if(s.startsWith('#'))return [1,3,5].map(i=>parseInt(s.slice(i,i+2),16));return s.match(/[\d.]+/g).slice(0,3).map(Number)};
const same=(a,b)=>a.every((v,i)=>Math.abs(v-b[i])<=1);
try{for(const model of ['15','Air'])for(const mode of ['safari','standalone']){
 const width=model==='15'?393:420,height=model==='15'?852:912,top=model==='15'?59:62,bottom=34;
 const ctx=await browser.newContext({...pw.devices['iPhone 15'],viewport:{width,height},deviceScaleFactor:1,serviceWorkers:'block'});
 await ctx.addInitScript(({standalone,now})=>{Object.defineProperty(navigator,'standalone',{get:()=>standalone});window.Audio=undefined;const D=Date;window.Date=class extends D{constructor(...a){super(...(a.length?a:[now]))}static now(){return now}}}, {standalone:mode==='standalone',now});
 const p=await ctx.newPage();
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();if(u.pathname.endsWith('.css')){const res=await r.fetch();return r.fulfill({response:res,body:(await res.text()).replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:top+'px',bottom:bottom+'px',left:'0px',right:'0px'}[s]))})}return r.continue()});
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(async()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation-play-state:paused!important;caret-color:transparent!important}';document.head.append(s);await document.fonts.ready;document.getElementById('words').textContent='董事长，您的最新指示已经收到，小助理正在处理，请稍候。'});
 for(const state of ['idle','working','waiting','question','offline'])for(const level of [0,.5,1]){
  // Start from a settled zero so --lvl changes exercise the production 1.4s transition.
  await p.evaluate(async state=>{const b=document.body;b.dataset.agent='running';b.dataset.conn=state==='offline'?'off':'on';b.dataset.status=state==='question'?'waiting':state==='offline'?'unknown':state;b.dataset.kind=state==='question'?'question':'';const w=document.getElementById('water');w.dataset.known='1';w.style.setProperty('--lvl','0');await import('./js/relay.js').then(m=>m.paintChrome());},state);
  await p.waitForTimeout(1550);
  await p.evaluate(async()=>{await import('./js/relay.js').then(m=>m.paintChrome());});
  await p.evaluate(level=>document.getElementById('water').style.setProperty('--lvl',String(level)),level);
  await p.waitForTimeout(1550);
  const m=await p.evaluate(()=>{const cs=e=>getComputedStyle(e),w=document.getElementById('water'),c=document.createElement('canvas').getContext('2d'),color=e=>{c.clearRect(0,0,1,1);c.fillStyle=cs(e).backgroundColor;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data].slice(0,3)};return{water:color(w),page:color(document.getElementById('chromeProbe')),band:color(document.getElementById('sabband')),root:color(document.documentElement),rect:w.getBoundingClientRect().toJSON(),token:cs(document.documentElement).getPropertyValue('--ios-bottom'),meta:[...document.querySelectorAll('meta[name="theme-color"]')].map(m=>m.content),viewport:document.querySelector('meta[name="viewport"]').content,ios:document.documentElement.dataset.ios,standalone:document.documentElement.dataset.standalone,meter:document.getElementById('m5h').getBoundingClientRect().toJSON()}});
  const label=`iphone${model}-${mode}-${state}-${level}`,path=`${out}/${label}.png`;await p.screenshot({path});
  const raw=execFileSync('magick',[path,'-depth','8','rgb:-'],{maxBuffer:8e6}),pixel=(x,y)=>[...raw.slice((y*width+x)*3,(y*width+x)*3+3)],target=level?m.water:m.page;
  const bottomBad=Array.from({length:bottom},(_,i)=>height-bottom+i).filter(y=>[1,width-2].some(x=>!same(pixel(x,y),target)));
  ok(!bottomBad.length,label+' bottom pixels match visible water/page');ok(m.meta.every(c=>c==='#2c2b26'),label+' Noir meta');ok(same(pixel(1,1),[44,43,38]),label+' Noir top');
  // Below both wave shapes, the tank must be continuous to its bottom at both edges.
  const tankBad=level?Array.from({length:height-Math.ceil(m.rect.top)-2},(_,i)=>Math.ceil(m.rect.top)+i+1).filter(y=>y>=Math.max(top,height/2)&&(y<Math.floor(m.meter.top)-1||y>Math.ceil(m.meter.bottom)+1)&&[1,width-2].some(x=>!same(pixel(x,y),target))):[];
  ok(!tankBad.length,label+' no internal horizontal band');
  const seamBad=level?Array.from({length:18},(_,i)=>Math.ceil(m.rect.top)-18+i).filter(y=>y>=top&&[1,width-2].some(x=>pixel(x,y).some((v,i)=>v<Math.min(m.water[i],m.page[i])-1))):[];
  ok(!seamBad.length,label+' wave never darker than tank');
  rows.push({label,model,mode,state,level,width,height,top,bottom,...m,target,bottomBad,tankBad,seamBad,path});
 }
 await ctx.close();
}}finally{await browser.close();await fake.stop();await web.stop();rmSync(temp,{recursive:true,force:true})}
const result={base,baseline,combined:execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim(),rows,failures,model:'WebKit iPhone 15 393x852 / Air 420x912, synthetic CSS 59/62 top and 34 bottom. Page pixels only; no physical Safari UI or OS home-indicator claim. Water transitions enabled.'};
writeFileSync(out+'/results.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({cases:rows.length,failures},null,2));if(!baseline)assert.equal(failures.length,0);
