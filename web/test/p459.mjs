// Real shipped page + local encrypted fake host. No account, network or physical-iOS claim.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const baseline=process.argv.includes('--baseline'),out='reports/qa/p459/'+(baseline?'red':'after');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await pw.webkit.launch();
const checks=[],failures=[],shots=[];
const ok=(v,msg)=>{if(!v)failures.push(msg)};
try{
 const ctx=await browser.newContext({...pw.devices['iPhone 13'],viewport:{width:390,height:844},deviceScaleFactor:1});
 await ctx.addInitScript(()=>{
  Object.defineProperty(navigator,'standalone',{get:()=>true});
  const vv=new EventTarget();Object.assign(vv,{height:844,width:390,offsetTop:0,offsetLeft:0,scale:1});Object.defineProperty(window,'visualViewport',{value:vv});
  window.__vv=(h,top=0)=>{Object.assign(vv,{height:h,offsetTop:top});vv.dispatchEvent(new Event('resize'));vv.dispatchEvent(new Event('scroll'))};
  window.Audio=undefined;
 });
 const p=await ctx.newPage();p.setDefaultTimeout(15000);
 await p.route('**/*',async r=>{
  const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();
  const old = baseline && ['/app.css','/js/boot.js','/js/relay.js','/js/fsedit.js'].includes(u.pathname) ? execFileSync('git',['show','e96cc19ae:web/public'+u.pathname],{encoding:'utf8'}) : null;
  if(old && u.pathname.endsWith('.js'))return r.fulfill({contentType:'text/javascript',body:old});
  if(u.pathname.endsWith('.css')){const res=await r.fetch();let s=old || await res.text();s=s.replace(/env\(safe-area-inset-(top|bottom|left|right)(?:\s*,[^)]*)?\)/g,(_,side)=>({top:'47px',bottom:'34px',left:'0px',right:'0px'}[side]));return r.fulfill({response:res,body:s})}return r.continue();
 });
 fake.setUp(true);await p.goto(fake.newPairing(web.url+'?iosdiag=1&lang=en'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)});
 const set=async(state)=>p.evaluate(async state=>{const b=document.body;b.dataset.agent='running';b.dataset.conn=state==='offline'?'off':'on';b.dataset.status=state==='question'?'waiting':state==='offline'?'unknown':state;b.dataset.kind=state==='question'?'question':''},state);
 const fs=async on=>p.evaluate(async on=>{const m=await import('./js/fsedit.js');on?m.open():m.close()},on);
 const measure=async label=>{
  await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const m=await p.evaluate(()=>{const b=document.body,root=document.getElementById('fsEd'),vv=visualViewport,cs=e=>getComputedStyle(e),rect=e=>{const r=e.getBoundingClientRect();return{left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height}};return{color:cs(document.getElementById('chromeProbe')).backgroundColor,html:cs(document.documentElement).backgroundColor,top:getComputedStyle(document.documentElement,'::before').backgroundColor,visualTop:vv.offsetTop,band:rect(document.getElementById('satband')),open:!root.hidden,buttons:['fsSlash','fsSend'].map(id=>{const e=document.getElementById(id),r=rect(e),at=document.elementsFromPoint(e.getBoundingClientRect().x+e.clientWidth/2,r.bottom-2);const occluded=['satband','sabband'].filter(id=>{const n=document.getElementById(id),q=rect(n);return Number(cs(n).zIndex)>Number(cs(root).zIndex)&&q.right>r.left&&q.left<r.right&&q.bottom>r.top&&q.top<r.bottom});return{id,...r,occluded,ink:cs(id==='fsSlash'?e:e.querySelector('.rocket')).color,front:at[0]?.id===id||e.contains(at[0])}}),limit:vv.offsetTop+vv.height,kb:b.dataset.kb}});
  ok(m.html==='rgb(44, 43, 38)'&&m.top==='rgb(44, 43, 38)',label+' fixed Noir chrome');
  if(m.open)for(const b of m.buttons)ok(b.top>=47&&b.bottom<=m.limit-10&&b.front&&b.occluded.length===0,label+' '+b.id+' unclipped');
  checks.push({label,...m});return m;
 };
 for(const state of ['idle','working','waiting','question','offline'])for(const open of [false,true])for(const keyboard of [false,true]){
  await fs(false);await p.evaluate(k=>window.__vv(k?510:844,k?12:0),keyboard);await set(state);await fs(open);
  const label=`${state}-${open?'editor':'page'}-${keyboard?'keyboard':'full'}`,m=await measure(label);
  const path=`${out}/${label}.png`;await p.screenshot({path,clip:{x:0,y:0,width:390,height:keyboard?522:844}});shots.push({path,color:'rgb(44, 43, 38)',visualTop:m.visualTop,open,buttons:m.buttons});
 }
 for(const timing of ['before','during','after'])for(let i=0;i<20;i++){
  await fs(false);await p.evaluate(()=>window.__vv(844));
  if(timing==='before')await fs(true);
  await p.evaluate(()=>window.__vv(700,5));if(timing==='during')await fs(true);
  await p.evaluate(()=>window.__vv(510,12));if(timing==='after')await fs(true);
  await measure(`${timing}-${i}`);
  await p.evaluate(()=>window.__vv(844));await measure(`${timing}-${i}-closed-keyboard`);
 }
 await p.evaluate(()=>window.__vv(800,1));await measure('first-keyboard-pixels');
 // No event after the terminal geometry changes: settling-frame sampling must still take the latest value.
 await p.evaluate(()=>{window.__vv(680,4);requestAnimationFrame(()=>{visualViewport.height=510;visualViewport.offsetTop=12})});await p.waitForTimeout(400);await measure('late-final-value');
 for(const id of ['fsExit','fsClear','fsSlash'])for(const state of ['hover','focus','active']){
  await fs(true);const e=p.locator('#'+id);await e.hover({force:true});if(state==='focus')await e.focus();if(state==='active')await p.mouse.down();
  const css=await e.evaluate(e=>{const s=getComputedStyle(e);return[s.backgroundColor,s.backgroundImage,s.borderWidth,s.boxShadow]});ok(JSON.stringify(css)===JSON.stringify(['rgba(0, 0, 0, 0)','none','0px','none']),id+' '+state+' icon only');if(state==='active'){await p.mouse.move(200,200);await p.mouse.up();}
 }
 ok(await p.locator('#iosdiag').count()===1,'diagnostic opt-in visible');
 await fs(false);
 await p.evaluate(()=>document.documentElement.style.setProperty('--c-working','#335577'));await set('working');await measure('custom-theme-observer');
 await p.evaluate(()=>{document.body.dataset.reader='1';document.getElementById('rd').hidden=false});await measure('reader-colour');
 await p.evaluate(()=>{delete document.body.dataset.reader;document.getElementById('rd').hidden=true});
 await p.goto(web.url+'?lang=en');await p.waitForFunction(()=>window.__ajState==='ready');
 ok(await p.locator('#iosdiag').count()===0,'iOS diagnostic absent without opt-in');
 // Zero safe area must not manufacture the historical 1px strip.
 await p.addStyleTag({content:'html[data-ios="1"]{--ios-safe-top:0px}'});
 ok(await p.evaluate(()=>parseFloat(getComputedStyle(document.documentElement,'::before').height))===0,'opaque zero inset has no 1px minimum');
 await ctx.close();
 // Pixel samples cover all 47 top rows, with a clean x=2 seam through rows 43..50.
 const rgb=s=>s.match(/[0-9.]+/g).slice(0,3).map(v=>Math.round(Number(v)*(s.startsWith('color(srgb')?255:1)));
 const pixels=shots.map(s=>{
  const target=rgb(s.color),raw=execFileSync('magick',[s.path,'-crop',`1x51+2+${s.visualTop}`,'-depth','8','rgb:-']);
  const bad_rows=Array.from({length:47},(_,y)=>y).filter(y=>target.some((v,i)=>raw[y*3+i]!==v));
  const dark_seam_rows=Array.from({length:4},(_,i)=>47+i).filter(y=>[0,1,2].every(i=>raw[y*3+i]===20));
  const icons=s.open?s.buttons.map(b=>{
   const x=Math.ceil(b.left),y=Math.ceil((b.top+b.bottom)/2+3),w=Math.floor(b.right)-x,h=Math.floor(b.bottom-5)-y;
   const ink=b.id==='fsSlash'?[217,119,87]:[250,249,245];
   const crop=execFileSync('magick',[s.path,'-crop',`${w}x${h}+${x}+${y}`,'-depth','8','rgb:-']);let lower_ink_pixels=0;
   for(let i=0;i<crop.length;i+=3)if(ink.every((v,j)=>Math.abs(crop[i+j]-v)<=2))lower_ink_pixels++;
   return {id:b.id,lower_ink_pixels};
  }):[];
  return {path:s.path,bad_rows,dark_seam_rows,icons};
 });
 for(const row of pixels){ok(row.bad_rows.length===0&&row.dark_seam_rows.length===0,row.path+' top47 Noir / no sooty seam pixels');for(const b of row.icons)ok(b.lower_ink_pixels>0,row.path+' '+b.id+' lower glyph pixels visible');}
 writeFileSync(`${out}/results.json`,JSON.stringify({combined:execFileSync(process.execPath,['web/build.mjs','--print-hash'],{encoding:'utf8'}).trim(),baseline,checks,pixels,failures},null,2)+'\n');
 console.log(JSON.stringify({checks:checks.length,shots:shots.length,failures},null,2));assert.equal(failures.length,0);
}finally{await browser.close();await fake.stop();await web.stop()}
