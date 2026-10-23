// P489 RESUME1: real encrypted host messages, both requested and measured geometry.
// Synthetic insets/viewport; this cannot emulate browser-owned iOS compositor pixels.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,writeFileSync,readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const base='4dcffd181',baseline=process.argv.includes('--baseline'),out=`reports/qa/p489/on0254/matrix/${baseline?'before':'after'}`;
mkdirSync(out,{recursive:true});
const temp=mkdtempSync('/tmp/p489-base-');
if(baseline)execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive',base,'web/public'],{maxBuffer:100e6})});
const web=await startWebServer(baseline?{publicDir:temp+'/web/public'}:{}),fake=await startFakeHost(),browser=await pw.webkit.launch();
const hosts=JSON.parse(readFileSync('reports/qa/p489/hosts.json')),rows=[],failures=[];
const same=(a,b)=>a.every((v,i)=>Math.abs(v-b[i])<=1);
try{for(const [width,height,top,bottom] of [[390,844,63,63],[420,912,68,68]])for(const mode of ['safari','chrome','standalone']){
 const ctx=await browser.newContext({...pw.devices['iPhone 15'],viewport:{width,height},deviceScaleFactor:1,colorScheme:'dark',serviceWorkers:'block',...(mode==='chrome'?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1'}:{})});
 await ctx.addInitScript(s=>{Object.defineProperty(navigator,'standalone',{get:()=>s});window.Audio=undefined;const D=Date;window.Date=class extends D{constructor(...a){super(...(a.length?a:[1792754640000]))}static now(){return 1792754640000}}},mode==='standalone');
 const p=await ctx.newPage();
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();if(u.pathname.endsWith('.css')){const res=await r.fetch();return r.fulfill({response:res,body:(await res.text()).replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,s)=>({top:top+'px',bottom:bottom+'px',left:'0px',right:'0px'}[s]))})}return r.continue()});
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(async()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();await document.fonts.ready;const s=document.createElement('style');s.textContent='.wave{animation-play-state:paused!important}*{caret-color:transparent!important}';document.head.append(s)});
 const capture=async label=>{
  await p.waitForTimeout(1600);
  const m=await p.evaluate(()=>{const w=document.getElementById('water'),color=e=>{const c=document.createElement('canvas').getContext('2d');c.fillStyle=getComputedStyle(e).backgroundColor;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data].slice(0,3)};const ws=getComputedStyle(w),r=w.getBoundingClientRect(),b=document.body.getBoundingClientRect();return {water:color(w),page:color(document.getElementById('chromeProbe')),band:color(document.getElementById('sabband')),token:document.documentElement.style.getPropertyValue('--ios-bottom'),rect:r.toJSON(),body:b.toJSON(),known:w.dataset.known,compacting:w.dataset.compacting,level:w.style.getPropertyValue('--lvl'),visible:ws.display!=='none'&&ws.visibility!=='hidden'&&Number(ws.opacity)>0&&r.height>0&&r.top<b.bottom&&r.bottom>=b.bottom-1,html:{...document.documentElement.dataset}}});
  const path=`${out}/${width}-${mode}-${label}.png`;await p.screenshot({path});
  const raw=execFileSync('magick',[path,'-depth','8','rgb:-'],{maxBuffer:8e6}),pixel=(x,y)=>[...raw.slice((y*width+x)*3,(y*width+x)*3+3)],target=m.visible?m.water:m.page;
  const bottomBad=Array.from({length:bottom},(_,i)=>height-bottom+i).filter(y=>[1,width-2].some(x=>!same(pixel(x,y),target)));
  if(bottomBad.length)failures.push(`${width}-${mode}-${label}: bottom does not match visible ${m.visible?'water':'page'}`);
  rows.push({width,height,top,bottom,mode,label,path,...m,target,bottomBad});
 };
 for(const h of hosts){await fake.send(h.theme);await fake.send({t:'preferences',value:{appearance:{theme:h.appearance,language:'zh'}}});await fake.send({t:'status',s:'idle',agent:h.agent,name:h.name});await fake.send({t:'meter',...h.meter});await capture(h.id)}
 await fake.send({t:'theme',v:1,id:'robots',factory:'robots'});await fake.send({t:'preferences',value:{appearance:{theme:'dark',language:'zh'}}});await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent Air'});
 for(const pct of [12,14,0,null]){await fake.send({t:'meter',model:'claude-opus-5-5',ctx:pct===null?null:{pct},h5:{pct:4},week:{pct:68}});await capture(pct===null?'unknown':`air-${pct}`)}
 // Current height plus shrunk and panned visual viewport: keep the real viewport handler.
 await fake.send({t:'meter',ctx:{pct:12},h5:{pct:4},week:{pct:68}});
 await p.evaluate(()=>{Object.defineProperty(window.visualViewport,'height',{configurable:true,get:()=>innerHeight-68});window.visualViewport.dispatchEvent(new Event('resize'))});await capture('air-12-short');
 await p.evaluate(()=>{Object.defineProperty(window.visualViewport,'offsetTop',{configurable:true,get:()=>34});window.visualViewport.dispatchEvent(new Event('scroll'))});await capture('air-12-pan');
 await ctx.close();
}}finally{await browser.close();await fake.stop();await web.stop();rmSync(temp,{recursive:true,force:true})}
const result={base,baseline,combined:baseline?JSON.parse(execFileSync('git',['show',base+':web/public/version.json'],{encoding:'utf8'})).combined:execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim(),rows,failures,model:'Desktop WebKit; 390x844 + measured420x912, synthetic63/68px safe areas, dark Safari/CriOS/standalone. 12/14%, quotas4/68 reconstructed from RESUME1. Current three-host samples reused. No physical iOS browser UI.'};
writeFileSync(out+'/results.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({cases:rows.length,failures},null,2));if(!baseline)assert.equal(failures.length,0);
