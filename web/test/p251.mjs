// P251: the real shipped page on Chromium and WebKit + a local encrypted fake host. No physical-device claim.
import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const require=createRequire(import.meta.url);
const {chromium,webkit}=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const OUT='reports/qa/p251';mkdirSync(OUT,{recursive:true});
const manifest=JSON.parse(readFileSync('web/public/version.json'));
const next='99.0.0a1';
const web=await startWebServer({port:0}),fake=await startFakeHost();
const results=[];
async function paired(browser,w=393,scheme='light'){
 fake.reset();fake.st.caps=['contacts','tgfwd'];fake.st.tgfwd=true;
 fake.st.onApp=async(c,m,send)=>{if(m.t!=='ct_list')return false;await send({t:'ct_res',r:m.r,ok:true,contacts:[],count:0,me:['owner@example.com'],mail:{ready:true}});return true;};
 const context=await browser.newContext({viewport:{width:w,height:852},colorScheme:scheme,serviceWorkers:'block'});
 // Same headless-media limitation and stand-in as p181-webkit; layout/input/Noise remain real WebKit.
 if(browser.browserType().name()==='webkit')await context.addInitScript(()=>{
  window.Audio=undefined;
  for(const k of ['play','pause','load'])HTMLMediaElement.prototype[k]=function(){return k==='play'?Promise.resolve():undefined;};
 });
 const p=await context.newPage();
 p.errors=[];p.on('pageerror',e=>p.errors.push(String(e)));
 console.log('pair goto');
 await p.goto(fake.newPairing(web.url.replace('127.0.0.1','localhost')+'?lang=zh'));console.log('page loaded');await p.waitForFunction(()=>window.__ajState==='awaiting-approval');
 console.log('awaiting approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(()=>{document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('confirm').hidden)document.getElementById('confirm-no').click();document.getElementById('a2hs-ok')?.click();});
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await fake.addTurn({k:'phone',dev:'other',name:'Phone',text:'会议内容'},'定价、招聘、发布日期。\n'.repeat(30));
 await p.waitForFunction(()=>!document.getElementById('mailBtn').hidden&&!document.getElementById('webReportBtn').disabled);
 console.log('paired');return {p,context};
}
async function geom(p){return p.evaluate(()=>{
 const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,right:r.right,y:r.y,bottom:r.bottom,w:r.width,h:r.height};};
 const buttons=[...document.querySelectorAll('#rmbar button')].filter(n=>!n.hidden);
 return {card:rect(document.getElementById('rm')),buttons:buttons.map(n=>({id:n.id,...rect(n),icon:(()=>{const svg=[...n.querySelectorAll('svg')].find(g=>getComputedStyle(g).display!=='none');return {...rect(svg),cssw:parseFloat(getComputedStyle(svg).width),cssh:parseFloat(getComputedStyle(svg).height)};})()})),group:rect(document.getElementById('rmIcons')),more:!!document.getElementById('rmMoreBtn')};
});}
try{
 for(const [engine,type]of [['Chromium',chromium],['WebKit',webkit]].filter(([e])=>!process.env.P251_ENGINE||e===process.env.P251_ENGINE)){
  console.log('START',engine);
  const browser=await type.launch(engine==='Chromium'?{executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-background-networking','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']}:{ });
  try{
   for(const w of [280,320,360,393,768,1440])for(const scheme of ['light','dark']){
    console.log(engine,w,scheme);
    const {p,context}=await paired(browser,w,scheme);try{
     await p.waitForTimeout(200);const g=await geom(p);assert.equal(g.more,false);assert.equal(g.buttons.length,7);
     assert.equal(g.buttons[0].w,44);
     // P340 (#38) replaces P251's 46 px pitch: right-packed, glyph gap = the last glyph's right margin; surplus stays left
     {const ic=g.buttons.slice(1).map(b=>b.icon),gaps=ic.slice(1).map((x,i)=>x.x-ic[i].right),right=g.card.right-ic.at(-1).right;
      for(const x of gaps)assert.ok(Math.abs(x-right)<=1,`P340 glyph gap ${x} = right margin ${right}`);
      if(w>=768)assert.ok(g.group.x-g.buttons[0].right>20,'surplus stays left');
      assert.ok(ic[0].x-g.buttons[0].right>=right/2-.5,'the left button keeps at least half a gap');}
     for(let i=0;i<g.buttons.length;i++){
      const b=g.buttons[i];assert.ok(b.x>=g.card.x-.5&&b.right<=g.card.right+.5&&b.bottom<=g.card.bottom+.5);
      assert.ok(b.w>=24&&b.h>=44);assert.ok(b.icon.cssw>=18&&b.icon.cssw<=b.w,JSON.stringify(b));
      if(i>0)assert.ok(b.x>=g.buttons[i-1].right-.5);
     }
     // Distinct non-overlapping hot areas really receive clicks (capture prevents side effects in this geometry check).
     await p.evaluate(()=>{window.hits=[];document.getElementById('rmbar').addEventListener('click',e=>{const b=e.target.closest('button');if(b){window.hits.push(b.id);e.stopImmediatePropagation();e.preventDefault();}},true);});
     for(const b of g.buttons)await p.mouse.click(b.x+b.w/2,b.y+b.h/2);
     assert.deepEqual(await p.evaluate(()=>window.hits),g.buttons.map(b=>b.id));
     await p.screenshot({path:`${OUT}/footer-${engine}-${w}-${scheme}.png`});
     // Future count changes: remove / add an action without changing the layout algorithm.
     await p.evaluate(()=>{const b=document.getElementById('readBtn').cloneNode(true);b.id='future-action';document.getElementById('rmIcons').append(b);});
     await p.setViewportSize({width:w+1,height:852});await p.waitForTimeout(100);
     const future=await geom(p);for(let i=1;i<future.buttons.length;i++)assert.ok(future.buttons[i].x>=future.buttons[i-1].right-.5);
     await p.evaluate(()=>document.getElementById('future-action').remove());
     // Explicit narrow phase at the physical 18px floor; 6px between glyphs and distinct 24px targets.
     await p.evaluate(()=>document.getElementById('rmbar').style.width='210px');   // P340: the bar, not the group, is what fitBar measures
     await p.waitForFunction(()=>parseFloat(getComputedStyle(document.querySelector('#rmIcons .act:not([hidden]) svg')).width)===18&&Math.abs(document.querySelector('#rmIcons .act:not([hidden])').getBoundingClientRect().width-24)<.5);const floor=await geom(p);
     for(const b of floor.buttons.slice(1)){assert.ok(Math.abs(b.w-24)<.5,JSON.stringify({w,bw:b.w,bar:floor.card}));assert.ok(Math.abs(b.icon.cssw-18)<.5&&Math.abs(b.icon.cssh-18)<.5,JSON.stringify(b));}
     await p.evaluate(()=>document.getElementById('rmbar').style.removeProperty('width'));
     await p.waitForTimeout(100);
     // All actions may disappear transiently; a later action must recover from a zero-width group.
     await p.evaluate(()=>{window.oldActions=[...document.querySelectorAll('#rmIcons .act')].map(b=>[b,b.hidden]);for(const [b]of window.oldActions)b.hidden=true;});
     await p.waitForTimeout(100);
     await p.evaluate(()=>{const b=document.getElementById('readBtn').cloneNode(true);b.id='zero-recovery';b.hidden=false;document.getElementById('rmIcons').append(b);});
     await p.waitForTimeout(100);const recovered=await geom(p);
     assert.equal(recovered.buttons.length,2);assert.ok(recovered.buttons[1].w>=24,'zero-to-one action recovers');
     assert.ok(recovered.buttons[1].icon.cssw<=recovered.buttons[1].w);
     await p.evaluate(()=>{document.getElementById('zero-recovery').remove();for(const [b,h]of window.oldActions)b.hidden=h;delete window.oldActions;});
     await p.waitForTimeout(100);
     const chatColor=await p.evaluate(()=>document.querySelector('meta[name="theme-color"]').content);
     await p.locator('#setBtn').click();await p.waitForTimeout(150);
     const chrome=()=>p.evaluate(()=>({meta:document.querySelector('meta[name="theme-color"]').content,bg:getComputedStyle(document.getElementById('settings')).backgroundColor}));
     const c=await chrome();assert.equal(c.meta,'#2c2b26');assert.equal(c.bg,'rgb(44, 43, 38)');
     await p.locator('#keysBtn').click();await p.waitForTimeout(150);assert.equal((await chrome()).meta,'#2c2b26');
     assert.ok(await p.locator('#keysCols').textContent().then(t=>t.includes('Alt')&&t.includes('升级主机')));
     await p.screenshot({path:`${OUT}/settings-${engine}-${w}-${scheme}.png`});
     await p.keyboard.press('Escape');await p.keyboard.press('Escape');await p.waitForTimeout(150);
     assert.equal(await p.evaluate(()=>document.getElementById('settings').hidden),true);
     assert.equal(await p.evaluate(()=>document.querySelector('meta[name="theme-color"]').content),chatColor);
     const scroll=await p.evaluate(()=>{const n=document.createElement('div');n.id='scroll-check';n.tabIndex=0;n.style.cssText='position:fixed;top:100px;left:0;width:100px;height:100px;overflow:auto;z-index:999';n.innerHTML='<div style="height:1000px;width:1000px">scroll</div>';document.body.append(n);n.scrollTop=50;return {width:getComputedStyle(n).scrollbarWidth,display:getComputedStyle(n,'::-webkit-scrollbar').display,top:n.scrollTop};});
     assert.equal(scroll.width,'none');assert.ok(scroll.top>0);if(engine==='Chromium')assert.equal(scroll.display,'none');
     await p.locator('#scroll-check').focus();await p.keyboard.press('PageDown');await p.waitForTimeout(250);assert.ok(await p.locator('#scroll-check').evaluate(n=>n.scrollTop)>50);
     {const beforeWheel=await p.locator('#scroll-check').evaluate(n=>n.scrollTop);await p.mouse.move(50,150);await p.mouse.wheel(0,100);await p.waitForTimeout(250);assert.ok(await p.locator('#scroll-check').evaluate(n=>n.scrollTop)>beforeWheel,'wheel moves the hidden-bar scroller');}
     assert.deepEqual(p.errors,[]);results.push({engine,w,scheme,geometry:g,pass:true});
    }catch(e){console.error('P251 case failed',e);throw e;}finally{console.log('close context');await context.close();}
   }
   // Actual silent reload + encrypted draft restoration; fixed Date advances inactivity without slowing the suite.
   const {p,context}=await paired(browser);try{
    let reloads=0,serveNew=false;
    p.on('framenavigated',f=>{if(f===p.mainFrame())reloads++;});
    await context.route('**/version.json',r=>r.fulfill({json:{v:1,version:next}}));
    await context.route('**/version.js',r=>r.fulfill({contentType:'text/javascript',body:'export default '+JSON.stringify({...manifest,version:serveNew?next:manifest.version})}));
    await p.clock.setFixedTime(new Date());await p.locator('#input').fill('保留这份草稿');
    await p.evaluate(v=>import('./js/version-notice.js').then(m=>m.setHostVersion(v)),next);
    await p.waitForTimeout(1200);assert.equal(reloads,0);assert.equal(await p.locator('#version-notice').isVisible(),false);
    // Pending reply selection blocks even after ten seconds.
    await p.locator('#replyBtn').click();await p.clock.setFixedTime(new Date(Date.now()+20000));
    await p.waitForTimeout(1200);assert.equal(reloads,0);
    await p.locator('#qbX').click();
    await p.locator('#input').fill('保留这份草稿，再加一句');
    await p.waitForTimeout(1100);assert.equal(reloads,0,'typing resets the idle window');
    serveNew=true;fake.st.version=next;
    await p.evaluate(()=>document.body.dataset.view='cam');
    await p.clock.setFixedTime(new Date(Date.now()+40000));await p.waitForTimeout(1200);
    assert.equal(reloads,0,'unfinished camera/scan surface blocks a silent follow');
    await p.evaluate(()=>document.body.dataset.view='chat');
    await p.locator('#input').fill('保留这份草稿，再加一句');
    await p.clock.setFixedTime(new Date(Date.now()+60000));
    await p.waitForFunction(v=>document.getElementById('version-later')?.textContent.includes(v),next,{timeout:15000});
    assert.equal(reloads,1);await p.waitForFunction(()=>document.getElementById('input')?.value==='保留这份草稿，再加一句');
    assert.equal(await p.locator('#version-notice').isVisible(),false);
    await p.screenshot({path:`${OUT}/updated-${engine}.png`});
    results.push({engine,silentReload:true,draftRestored:true,pendingReplyBlocks:true,pass:true});
   }finally{await context.close();}
   console.log(engine,'silent reload PASS');
   // Shortcut routes through the same signed /update path; explicit reload stays available.
   const s=await paired(browser);try{
    await s.p.keyboard.press('Alt+Shift+U');await s.p.waitForTimeout(500);assert.equal(fake.st.slashes.at(-1)?.cmd,'update');
    const sig=fake.st.slashes.at(-1).sig;assert.ok(typeof sig==='string'&&sig.length>80);
    let nav=0;s.p.on('framenavigated',f=>{if(f===s.p.mainFrame())nav++;});
    await s.p.keyboard.press('Alt+Shift+R');await s.p.waitForTimeout(1500);assert.equal(nav,1);
    results.push({engine,shortcuts:true,pass:true});
   }finally{await s.context.close();}
  }finally{await browser.close();}
 }
 writeFileSync(`${OUT}/browser.json`,JSON.stringify({physical:false,results,complete:true},null,2)+'\n');console.log('P251 browsers PASS',results.length);
}finally{await fake.stop();await web.stop();}
