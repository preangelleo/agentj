// #563 isolated paired phone: normalized metadata only, no real accounts or reset calls.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const out=new URL('../../../reports/qa/563/web/',import.meta.url);mkdirSync(out,{recursive:true});
const before=process.env.FEEDBACK563_BEFORE || '';
const web=await startWebServer(before?{publicDir:before}:{}),fake=await startFakeHost(),B=await launch(),results=[];
const credit={status:'ok',count:3,nearest_expires_at:Date.UTC(2026,10,7,12)/1000};
fake.answer('acct_get',()=>({t:'acct_res',ok:true,view:{codex:{active:'a',items:[{id:'a',name:'A',method:'device',quota:{pct:31,window:'week'},reset_credits:credit},{id:'b',name:'B',method:'device',reset_credits:{status:'ok',count:0}},{id:'c',name:'C',method:'device',reset_credits:{status:'error',count:null}}]}}}));
try{for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
 fake.reset();const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`(()=>{for(const id of ['confirm-no','a2hs-ok','phone-env-dismiss']){const e=document.getElementById(id);if(e&&e.getBoundingClientRect().height)e.click();}})()`);
 await fake.send({t:'status',s:'idle',agent:'codex',name:'Agent J'});
 await fake.send({t:'meter',week:{pct:31},reset_credits:credit});
 await waitFor(p,`document.getElementById('mWeek').dataset.known==='1'`);
 await evaluate(p,`document.getElementById('mWeek').click()`);
 await waitFor(p,`!document.getElementById('usageDlg').hidden`);
 if (!before) assert.match(await evaluate(p,`document.getElementById('usageList').textContent`),lang==='zh'?/重置券 3 张，最近一张 11月7日到期/:/3 reset credits; next expires November 7/);
 else assert.doesNotMatch(await evaluate(p,`document.getElementById('usageList').textContent`),/重置券|reset credits/);
 writeFileSync(new URL(`${lang}-${theme}-${width}-quota${before?'-before':''}.png`,out),await shoot(p));
 await evaluate(p,`document.getElementById('usageClose').click()`);
 await evaluate(p,`import('./js/settings.js').then(x=>{x.open();document.getElementById('set-accts-nav').click();})`);
 await waitFor(p,`document.querySelectorAll('.acctrow').length===3`);
 if (!before) await waitFor(p,`document.querySelectorAll('.acctcredits').length===3`);
 const lines=await evaluate(p,`[...document.querySelectorAll('.acctcredits')].map(x=>x.textContent)`);
 if (!before) { assert.match(lines[0],lang==='zh'?/重置券 3 张/:/3 reset credits/);
 assert.match(lines[1],lang==='zh'?/重置券 0 张/:/0 reset credits/);
 assert.match(lines[2],lang==='zh'?/暂不可用/:/unavailable/); } else assert.deepEqual(lines,[]);
 assert.equal(await evaluate(p,`document.querySelector('#set-accts').scrollWidth<=document.querySelector('#set-accts').clientWidth`),true);
 // Synthetic labels removed only for screenshots: no account name enters artifacts.
 await evaluate(p,`document.querySelectorAll('.acctname').forEach(e=>e.textContent='—')`);
 writeFileSync(new URL(`${lang}-${theme}-${width}-accounts${before?'-before':''}.png`,out),await shoot(p));
 assert.deepEqual(p.problems,[]);
 results.push({lang,theme,width,quota:'PASS',accounts:'PASS'});console.log('PASS',lang,theme,width);
}}
finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL(before?'before-results.json':'results.json',out),JSON.stringify(results,null,2)+'\n');
