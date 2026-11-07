import assert from 'node:assert/strict';import{mkdirSync,writeFileSync}from'node:fs';
import{startWebServer}from'./serve.mjs';import{startFakeHost}from'./fakehost.mjs';
import{launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep}from'./browser.mjs';
const out=process.env.PAGE_ISOLATION_OUT||'reports/qa/p0285-page-isolation';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),b=await launch(),rows=[];
try{for(const width of[390,768,1440])for(const lang of['zh','en'])for(const theme of['light','dark']){
 fake.reset();fake.st.caps=['wf1','jcards1'];fake.st.prefs={appearance:{theme,language:lang}};fake.st.onApp=async(c,m,send)=>{if(m.t==='wf_list'){await send({t:'wf_items',r:m.r,items:[],skills:[],memory:[],counts:{wdb:0},more:false});return true;}if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:[]});return true;}return false;};
 const p=await newPage(b,width,1000,theme,{allow:[web.url,fake.relay+'/']});try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/settings.js').then(m=>m.open())`);await sleep(300);
 const settings=await evaluate(p,`(()=>{const n=document.getElementById('settings'),r=n.getBoundingClientRect();return{shared:n.classList.contains('aj-shared-popup'),x:r.x,y:r.y,w:r.width,h:r.height};})()`);rows.push({width,lang,theme,settings});writeFileSync(`${out}/${width}-${lang}-${theme}-settings.png`,await shoot(p));
 assert.equal(settings.shared,false,'full-screen Settings is an explicitly declared non-card surface');assert.deepEqual([settings.x,settings.y,settings.w,settings.h],[0,0,width,1000]);
 await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);await fake.addTurn({k:'phone',name:'你',text:'合成页面边界检查'},'界面检查');await sleep(450);await evaluate(p,`document.getElementById('readBtn').click()`);await waitFor(p,`!document.getElementById('rd').hidden`);assert.equal(await evaluate(p,`document.getElementById('rd').classList.contains('aj-shared-popup')`),false,'reader remains a page');await evaluate(p,`document.getElementById('rdClose').click()`);await sleep(450);
 await evaluate(p,`import('./js/five-pages.js').then(m=>m.go('memory'))`);await waitFor(p,`document.body.dataset.fivePage==='memory'&&!document.getElementById('five-page').hidden`);assert.equal(await evaluate(p,`!!document.querySelector('#five-page.aj-shared-popup')`),false,'five-page remains a page');
 await evaluate(p,`import('./js/five-pages.js').then(m=>m.go('card'))`);await waitFor(p,`document.body.dataset.fivePage==='card'&&!document.getElementById('five-page').hidden`);assert.equal(await evaluate(p,`!!document.querySelector('#five-page.aj-shared-popup')`),false,'card list remains a page');
 console.log('PASS page isolation',width,lang,theme);
 }finally{await p.dispose();}
}}finally{writeFileSync(`${out}/results.json`,JSON.stringify(rows,null,2));await b.close();await fake.stop();await web.stop();}
