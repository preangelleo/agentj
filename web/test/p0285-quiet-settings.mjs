import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=process.env.QUIET_SETTINGS_OUT||'reports/qa/p0285-no-disconnect-overlay/settings';mkdirSync(out,{recursive:true});
assert.ok(process.env.QUIET_SETTINGS_BEFORE,'QUIET_SETTINGS_BEFORE must name the baseline public directory');
const before=await startWebServer({publicDir:process.env.QUIET_SETTINGS_BEFORE}),after=await startWebServer(),fake=await startFakeHost(),b=await launch(),rows=[];
try{for(const phase of ['before','after'])for(const width of [390,1440])for(const lang of ['zh','en']){
 fake.reset();fake.st.prefs={appearance:{language:lang}};const web=phase==='before'?before:after,p=await newPage(b,width,1000,'dark',{allow:[web.url,fake.relay+'/']});
 try{await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/settings.js').then(m=>m.open())`);await sleep(200);
 const count=await evaluate(p,`(async()=>{window.__notices=[];const ui=await import('./js/ui.js');ui.toastOff();new MutationObserver(()=>{const n=document.getElementById('toast');if(n.textContent.trim())window.__notices.push(n.textContent);}).observe(document.getElementById('toast'),{childList:true,subtree:true,characterData:true});const m=await import('./js/settings.js');m.connectionChanged(false);await new Promise(r=>setTimeout(r,30));m.connectionChanged(true);await new Promise(r=>setTimeout(r,30));return window.__notices.length;})()`);
 assert.equal(count>0,phase==='before');writeFileSync(`${out}/${phase}-${width}-${lang}.png`,await shoot(p));rows.push({phase,width,lang,notices:count});console.log('PASS Settings quiet',phase,width,lang,count);
 }finally{await p.dispose();}
}}finally{writeFileSync(`${out}/results.json`,JSON.stringify(rows,null,2));await b.close();await fake.stop();await before.stop();await after.stop();}
