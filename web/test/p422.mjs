// P422: Playwright Chromium, local fake host only. Device-sized images are emulation, not physical iOS acceptance.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startFakeHost} from './fakehost.mjs';
const {chromium}=process.env.PLAYWRIGHT_MODULE ? await import(process.env.PLAYWRIGHT_MODULE) : createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out=fileURLToPath(new URL('../../../reports/qa/p422/',import.meta.url));mkdirSync(out,{recursive:true});
const rec=parityRecorder('web/test/p422.mjs');
const web=await startWebServer(),fake=await startFakeHost();
const B=await chromium.launch({executablePath:process.env.CHROMIUM||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
const results=[];
const ios='Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Version/26.0 Mobile/15E148 Safari/604.1';
const android='Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/147.0.0.0 Mobile Safari/537.36';
const states={idle:{status:'idle',kind:'',conn:'on'},working:{status:'working',kind:'',conn:'on'},waiting:{status:'waiting',kind:'permission',conn:'on'},question:{status:'waiting',kind:'question',conn:'on'},offline:{status:'unknown',kind:'',conn:'off'}};
const rgb=h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16));
async function pair(name,w,h,scheme,ua,standalone=true){
 fake.reset();await fake.addTurn({k:'phone',dev:'X',name:'Remote',text:'Read this'},('Message to scroll. '.repeat(400)),'done');
 const ctx=await B.newContext({viewport:{width:w,height:h},colorScheme:scheme,isMobile:true,hasTouch:true,userAgent:ua,permissions:['microphone']});
 await ctx.addInitScript(s=>{Object.defineProperty(navigator,'standalone',{value:s});localStorage.setItem('aj.chrome','#6a9bcc');},standalone);
 const p=await ctx.newPage();await p.goto(fake.newPairing(web.url+'?lang=en'));
 await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur();});
 const cdp=await ctx.newCDPSession(p);await cdp.send('Emulation.setSafeAreaInsetsOverride',{insets:{top:54,bottom:34,left:0,right:0}});
 return {ctx,p,cdp};
}
async function pixels(p,points){return p.evaluate(async points=>{
 // Screenshot pixels are sampled outside the page below; canvas here tests the actual decoded robot image.
 const im=document.getElementById('orb');await im.decode();const c=document.createElement('canvas');c.width=im.naturalWidth;c.height=im.naturalHeight;
 const g=c.getContext('2d');g.drawImage(im,0,0);return [...g.getImageData(Math.floor(c.width*.5),Math.floor(c.height*.31),1,1).data];
},points);}
async function screenshotPixel(p,path,points){
 const b=await p.screenshot({path});
 return p.evaluate(async({b,points})=>{const im=new Image();im.src='data:image/png;base64,'+b;await im.decode();const c=document.createElement('canvas');c.width=im.width;c.height=im.height;const g=c.getContext('2d');g.drawImage(im,0,0);return points.map(([x,y])=>[...g.getImageData(x,y,1,1).data]);},{b:b.toString('base64'),points});
}
async function synthetic(p,{dy=180,dx=0,selector='#words p',type='end',touches=1}={}){
 return p.evaluate(({dy,dx,selector,type,touches})=>{const el=document.querySelector(selector),main=document.getElementById('main');
 const touch=(x,y,id=1)=>new Touch({identifier:id,target:el,clientX:x,clientY:y});
 const fire=(name,ts)=>{const e=new TouchEvent(name,{bubbles:true,cancelable:true,touches:ts,changedTouches:[touch(30+dx,120+dy)]});el.dispatchEvent(e);return e.defaultPrevented;};
 fire('touchstart',[touch(30,120)]);const prevented=fire('touchmove',Array.from({length:touches},(_,i)=>touch(30+dx,120+dy,i+1)));
 const shown=!document.getElementById('pull-refresh').hidden,text=document.getElementById('pull-refresh').textContent;
 if(type!=='hold')fire(type==='cancel'?'touchcancel':'touchend',[]);
 return {prevented,shown,text};
 },{dy,dx,selector,type,touches});
}
try{
 // Exact shipped iPhone Air startup PNG, rendered and sampled with Playwright, not an invented splash DOM.
 for(const scheme of ['light','dark']){
  const ctx=await B.newContext({viewport:{width:420,height:912},deviceScaleFactor:3,colorScheme:scheme});const p=await ctx.newPage();
  await p.goto(web.url+`startup/420x912-3-portrait-${scheme}.png`);
  await p.evaluate(()=>{const im=document.querySelector('img');document.documentElement.style.cssText='margin:0;background:#141414';document.body.style.cssText='margin:0';im.style.cssText='position:fixed;inset:0;width:100vw;height:100vh;display:block';});
  const px=await screenshotPixel(p,join(out,`iphone-air-startup-${scheme}.png`),[[0,0],[1259,0],[0,2735],[1259,2735],[490,1200],[770,1200]]);
  for(const c of px.slice(0,4))assert.deepEqual(c,[20,20,20,255]);
  for(const c of px.slice(4))assert.deepEqual(c,scheme==='dark'?[20,20,20,255]:[250,249,245,255],'no icon-sized plate around transparent robot');
  results.push({startup:scheme,pixels:px});await ctx.close();
  // Android folding display: transparent and maskable icon composited on the declared background at both widths.
  for(const w of [412,768]){
   const c=await B.newContext({viewport:{width:w,height:915},colorScheme:scheme});const q=await c.newPage();await q.goto(web.url);
   const px=await q.evaluate(async manifest=>{
    // Only this test requests public metadata/assets; production stays on the existing CSP boundary.
    const im=new Image();im.src=manifest.icons.find(x=>x.purpose==='maskable').src;await im.decode();
    const c=document.createElement('canvas');c.width=im.width;c.height=im.height;const g=c.getContext('2d');g.drawImage(im,0,0);const corners=[[0,0],[511,0],[0,511],[511,511]].map(([x,y])=>[...g.getImageData(x,y,1,1).data]);
    document.body.replaceChildren(im);document.body.style.cssText='margin:0;position:fixed;inset:0;display:grid;place-items:center;background:'+manifest.background_color;
    im.style.cssText='width:128px;height:128px;image-rendering:pixelated';return {corners,bg:manifest.background_color};},JSON.parse(readFileSync(join(PUBLIC_DIR,`manifest-${scheme}.webmanifest`))));
   for(const x of px.corners)assert.deepEqual(x,[...rgb(px.bg),255]);await q.screenshot({path:join(out,`android-fold-${w}-startup-${scheme}.png`)});results.push({android:w,scheme,...px});await c.close();
  }
 }
 rec.record('startup',true);
 for(const [device,w,h,ua,standalone] of [['iphone-air-pwa',420,912,ios,true],['iphone-safari',420,912,ios,false],['android-fold',412,915,android,true]]){
  for(const scheme of ['light','dark']){
   const {ctx,p}=await pair(device,w,h,scheme,ua,standalone);
   for(const [state,data] of Object.entries(states)){
    await p.evaluate(async data=>{Object.assign(document.body.dataset,data);const {paintChrome}=await import('./js/relay.js');paintChrome(true);},data);
    await p.waitForFunction(state=>document.getElementById('orb').dataset.v===state,state);
    const color=await p.evaluate(state=>getComputedStyle(document.body).getPropertyValue('--c-'+state).trim(),state);
    const robot=await pixels(p);assert.deepEqual(robot.slice(0,3),rgb(color),`${device} ${scheme} ${state} robot`);
    const sample=await screenshotPixel(p,join(out,`${device}-${scheme}-${state}.png`),[[3,0],[3,h-1]]);
    for(const px of sample)assert.deepEqual(px,[20,20,20,255],`${device}/${scheme}/${state} safe area`);
    assert.deepEqual(await p.locator('meta[name=theme-color]').evaluateAll(ms=>ms.map(m=>m.content)),['#141414']);
    results.push({device,scheme,state,robot,color,edges:sample});
   }
   await p.evaluate(()=>{document.body.dataset.conn='on';document.body.dataset.status='working';document.body.dataset.kind='';});
   const custom={idle:'#3a6ea5',working:'#1f4e79',waiting:'#b8860b',question:'#6a5acd',offline:'#555555'};
   await p.evaluate(async colors=>{const {applyTheme}=await import('./js/theme.js');applyTheme({t:'theme',v:1,id:'p422-colours',colors:{state:colors}});},custom);
   await p.waitForFunction(()=>document.getElementById('orb').src.startsWith('blob:'));assert.deepEqual((await pixels(p)).slice(0,3),rgb(custom.working));
   const prior=await p.locator('#orb').getAttribute('src');
   await p.evaluate(()=>{document.documentElement.style.setProperty('--c-working','#335577');document.documentElement.style.setProperty('--c-sooty','#ffffff');});
   await p.waitForFunction(prior=>document.getElementById('orb').src.startsWith('blob:')&&document.getElementById('orb').getAttribute('src')!==prior,prior);
   assert.deepEqual((await pixels(p)).slice(0,3),rgb('#335577'),'root theme variable writes repaint the robot without a status frame');
   const edges=await screenshotPixel(p,join(out,`${device}-${scheme}-custom-theme.png`),[[3,0],[3,h-1]]);
   for(const px of edges)assert.deepEqual(px,[20,20,20,255],'even a changed Sooty theme token cannot repaint system chrome');
   const own=readFileSync(join(PUBLIC_DIR,'brand/img/status/logo-question.png')).toString('base64');
   await p.evaluate(async({own,custom})=>{const {applyTheme}=await import('./js/theme.js');applyTheme({t:'theme',v:1,id:'p422-own',colors:{state:custom},logo:{png:own}});},{own,custom});
   assert.deepEqual((await pixels(p)).slice(0,3),rgb('#827dbd'),'custom image never tinted');
   await ctx.close();
  }
 }
 rec.record('logo-chrome',true);
 // Real app, real sealed IndexedDB draft and actual refresh path. All synthetic events bubble through the existing deck.
 const {ctx,p,cdp}=await pair('gesture',420,912,'dark',ios);
 await fake.send({t:'status',s:'idle'});
 await p.evaluate(()=>{document.getElementById('main').scrollTop=0;window.__p422Marker=1;document.getElementById('input').value='P422 preserved draft';});
 const noReload=async opts=>{const r=await synthetic(p,opts);assert.equal(await p.evaluate(()=>window.__p422Marker),1);assert.equal(r.shown,false,JSON.stringify(opts));};
 await p.evaluate(()=>document.getElementById('main').scrollTop=100);await noReload({});
 await p.evaluate(()=>document.getElementById('main').scrollTop=0);
 await noReload({dx:200});await noReload({touches:2});await noReload({selector:'#loadHit'});
 await synthetic(p,{dy:40});assert.equal(await p.evaluate(()=>window.__p422Marker),1);
 await synthetic(p,{type:'cancel'});assert.equal(await p.evaluate(()=>window.__p422Marker),1);
 await p.locator('#words').dispatchEvent('wheel',{deltaY:-200});assert.equal(await p.evaluate(()=>window.__p422Marker),1);
 await p.evaluate(()=>{const n=document.createElement('div');n.id='p422-nested';n.style.cssText='height:30px;overflow:auto';n.innerHTML='<div style="height:300px">Nested scroll</div>';document.getElementById('words').append(n);});
 await noReload({selector:'#p422-nested div'});
 await fake.send({t:'question',id:'d'.repeat(32),ttl:180,qs:[{q:'Choose one',h:'',m:false,o:[{l:'A',d:'First'},{l:'B',d:'Second'}]}]});
 await p.locator('#qs .opt').first().waitFor({state:'visible'});await p.locator('#qs .opt').first().click();await noReload({});
 await fake.send({t:'question_done',id:'d'.repeat(32),result:'gone'});
 await p.waitForFunction(()=>document.body.dataset.sheet!=='1');
 // Double native touch locks the real fake-microphone MediaRecorder; no generated recording is uploaded.
 const mr=await p.locator('#mic').boundingBox();const mx=mr.x+mr.width/2,my=mr.y+mr.height/2;
 for(let i=0;i<2;i++){await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:mx,y:my}]});await new Promise(r=>setTimeout(r,60));await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await new Promise(r=>setTimeout(r,100));}
 await p.waitForFunction(async()=>!!(await import('./js/relay.js')).debug().ptt?.locked);
 await noReload({});await p.locator('#pttCancel').click();
 await p.waitForFunction(async()=>!(await import('./js/relay.js')).debug().ptt);
 // Unsubmitted card/attachment work and active recording both use the real refreshBlocked guard.
 await p.evaluate(async()=>{const {attachShot}=await import('./js/relay.js');await attachShot(new File(['x'],'p422.png',{type:'image/png'}),true);});await noReload({});
 await p.reload();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur();document.getElementById('main').scrollTop=0;window.__p422Marker=1;});
 await fake.send({t:'status',s:'idle'});
 // A native touch drag, rather than just dispatchEvent, must claim the vertical gesture.
 const rect=await p.locator('#main').boundingBox();const x=rect.x+rect.width-12,y=rect.y+40;
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
 for(const dy of [15,40,90,145,180])await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y+dy}]});
 await p.locator('#pull-refresh').waitFor({state:'visible'});
 const hint=await p.locator('#pull-refresh').evaluate(n=>({bg:getComputedStyle(n).backgroundColor,fill:n.querySelector('svg').getAttribute('fill'),bottom:n.getBoundingClientRect().bottom,read:document.getElementById('read').getBoundingClientRect().top}));
 assert.equal(hint.bg,'rgba(0, 0, 0, 0)');assert.equal(hint.fill,'none');assert.ok(hint.bottom<=hint.read,'hint has its own gap above the text');
 assert.match(await p.locator('#pull-refresh').textContent(),/Release/);
 await p.screenshot({path:join(out,'pull-release-en.png')});
 await p.evaluate(()=>window.AJLang.set('zh'));assert.match(await p.locator('#pull-refresh').textContent(),/松开/);await p.screenshot({path:join(out,'pull-release-zh.png')});
 await p.evaluate(()=>document.getElementById('input').value='P422 preserved draft');
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await p.waitForFunction(()=>!window.__p422Marker&&window.__ajState==='ready'&&document.getElementById('input').value==='P422 preserved draft');
 await ctx.close();rec.record('gesture-draft',true);
 writeFileSync(join(out,'results.json'),JSON.stringify({scope:'Playwright Chromium device-size emulation; not physical Safari/WebAPK acceptance',results},null,2)+'\n');
 console.log('P422 startup, five-state logo/chrome, gesture boundaries and sealed draft reload PASS');
}catch(e){rec.record('complete',false);throw e;}finally{rec.write();await B.close();await fake.stop();await web.stop();}
