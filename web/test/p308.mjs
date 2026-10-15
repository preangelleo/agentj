// P308: a workflow CEO's staff on the Workflow page — 「只有 CEO，还没有员工」 on a card whose CEO has no staff, the count on
// 「复查组织结构」, the employee's own title instead of its slug, and step 5 (propose staff) in the review prompt.
// Real client + paired local Noise session; host output = the P244 fixture (real scanner output), one company's staff removed.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep} from './browser.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));const out=root+'reports/qa/p308/';mkdirSync(out,{recursive:true});
const fx=JSON.parse(readFileSync(root+'host/tests/fixtures/p244_wf.json','utf8'));
const zh=JSON.parse(readFileSync(root+'web/i18n/web.zh.json','utf8')),en=JSON.parse(readFileSync(root+'web/i18n/web.en.json','utf8'));
// the scanner's real output, with music-studio's only employee gone (what a CEO-only company looks like on the wire)
const solo=fx.wf_list.map(m=>{const x=structuredClone(m);if(x.items)for(const w of x.items)if(w.folder==='music-studio'){w.agents=[];w.staff=0;}
 if(x.org)x.org={...x.org,ready:true,nostaff:1};return x;});
const all=fx.wf_list.map(m=>{const x=structuredClone(m);if(x.org)x.org={...x.org,ready:true};return x;});
// an older host: no staff / nostaff fields at all
const old=fx.wf_list.map(m=>{const x=structuredClone(m);if(x.items)for(const w of x.items)delete w.staff;if(x.org){x.org={...x.org,ready:true};delete x.org.nostaff;}return x;});
assert.ok(fx.wf_list.some(m=>m.items?.some(w=>w.folder==='video-studio'&&w.staff===2)),'fixture: the scanner sends staff');
assert.ok(!fx.wf_list.some(m=>m.items?.some(w=>w.agents?.some(a=>a.name==='README'))),'fixture: README is not an employee');
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let checks=0;const results=[];
const ev=(p,s)=>evaluate(p,s),click=(p,s)=>ev(p,s==='#orb'?`(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));})()`:`document.querySelector(${JSON.stringify(s)}).click()`);
const check=(v,label)=>{assert.ok(v,label);checks++;results.push(label);};
async function open(w,scheme,language,list){
 fake.reset();fake.st.caps=['wf1'];fake.st.prefs={appearance:{language,theme:scheme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.st.onApp=async(c,m,send)=>{if(m.t==='wf_list'){for(const x of list)await send({...x,r:m.r});return true;}return false;};
 const p=await newPage(B,w,852,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);await sleep(200);
 await click(p,'#orb');await waitFor(p,`!document.getElementById('five-menu').hidden`);await click(p,'.fp-menu-card[data-page="workflows"]');
 await waitFor(p,`document.querySelectorAll('[data-folder]').length>=3`);await sleep(200);return p;
}
try{
 for(const w of [390,1440]){
  for(const [language,scheme,D] of [['zh','light',zh],['en','dark',en]]){
   const tag=`${language}-${scheme}-${w}`;
   // 1) one company with only a CEO: weak hint on its card only, the count on the review button
   let p=await open(w,scheme,language,solo);
   const want=D['p308.count'].replace('{label}',D['p276.review']).replace('{n}','1');
   check(await ev(p,`document.querySelector('.fp-org button').textContent===${JSON.stringify(want)}`),'review button carries the count '+tag);
   check(await ev(p,`document.querySelector('[data-folder="music-studio"] .fp-nostaff')?.textContent===${JSON.stringify(D['p308.nostaff'])}`),'CEO-only card hint '+tag);
   check(await ev(p,`document.querySelectorAll('.fp-nostaff').length===1`),'hint only on the CEO-only card '+tag);
   check(await ev(p,`(()=>{const a=getComputedStyle(document.querySelector('.fp-nostaff')),b=document.querySelector('[data-folder] small');return a.fontSize===getComputedStyle(b).fontSize&&a.display==='block';})()`),'same small type as the P276 badge '+tag);
   check(await ev(p,`document.documentElement.scrollWidth<=innerWidth`),'no horizontal overflow '+tag);
   await click(p,'[data-folder="video-studio"] .fp-card-head');await waitFor(p,`!!document.querySelector('[data-folder="video-studio"] .fp-expanded [data-agent]')`);
   check(await ev(p,`document.querySelector('[data-agent="scriptwriter"]').textContent.startsWith('编剧 · ')`),'employee title instead of the slug '+tag);
   check(await ev(p,`document.querySelector('[data-agent="editor"]').textContent.startsWith('editor · ')`),'no title → slug '+tag);
   check(await ev(p,`![...document.querySelectorAll('[data-agent]')].some(e=>e.dataset.agent==='README')`),'README is not listed '+tag);
   writeFileSync(out+`nostaff-${tag}.png`,await shoot(p,''));
   const n=fake.st.says.length;await click(p,'.fp-org button');for(let i=0;i<40&&fake.st.says.length===n;i++)await sleep(100);
   const said=fake.st.says.at(-1)?.text||'';
   check(said===D['p276.reviewPrompt'],'review prompt sent as is '+tag);
   check(['5. ','6. ','.claude/agents/','.codex/agents/','agentj wizard apply','3–7'].every(x=>said.includes(x)),'step 5: propose 3–7 staff, apply, Codex twin '+tag);
   check(await ev(p,`!document.body.dataset.uiError`),'no UI error (CEO only) '+tag);await p.dispose();
   // 2) every company has staff: no hint, plain label
   p=await open(w,scheme,language,all);
   check(await ev(p,`document.querySelector('.fp-org button').textContent===${JSON.stringify(D['p276.review'])}`),'no count when all have staff '+tag);
   check(await ev(p,`!document.querySelector('.fp-nostaff')`),'no hint when all have staff '+tag);await p.dispose();
   // 3) an older host (no staff fields): no hint, no count, no error
   p=await open(w,scheme,language,old);
   check(await ev(p,`!document.querySelector('.fp-nostaff')&&document.querySelector('.fp-org button').textContent===${JSON.stringify(D['p276.review'])}`),'older host: unchanged '+tag);
   check(await ev(p,`!document.body.dataset.uiError`),'no UI error (older host) '+tag);await p.dispose();
  }
 }
 // the setup prompt carries step 5 too (both languages)
 for(const D of [zh,en])check(D['p276.setupPrompt'].includes('.claude/agents/')&&D['p276.setupPrompt'].includes('6. '),'setup prompt has step 5 '+(D===zh?'zh':'en'));
 console.log(`P308 PASS ${checks} checks`);writeFileSync(out+'browser.json',JSON.stringify({ok:true,checks,results},null,2));
}finally{await B.close();await fake.stop();await web.stop();}
