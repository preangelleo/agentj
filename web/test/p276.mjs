// P276: the Workflow page's 「初始化 / 复查组织结构」 button, the three workflows that come with Agent J when no workflow was
// found, and the 「未登记」 badge. Real client + paired local Noise session; host output = the P244 fixture (scanner output).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep} from './browser.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));const out=root+'reports/qa/p276/';mkdirSync(out,{recursive:true});
const fx=JSON.parse(readFileSync(root+'host/tests/fixtures/p244_wf.json','utf8'));
const zh=JSON.parse(readFileSync(root+'web/i18n/web.zh.json','utf8')),en=JSON.parse(readFileSync(root+'web/i18n/web.en.json','utf8'));
const last=fx.wf_list.at(-1);
// empty working root: one wf_items with no items, no workflow, the roster present but nothing registered
const empty=[{t:'wf_items',items:[],more:false,root_name:'root',counts:{all:0,wdb:0,busy:0},skills:last.skills||[],agentj:last.agentj,memory:last.memory||[],
 org:{registry:true,ready:false,unreg:0},etag:'p276-empty'}];
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
 await waitFor(p,`!!document.querySelector('.fp-org button')`);await sleep(200);return p;
}
try{
 for(const w of [390,1440]){
  for(const [language,scheme,D] of [['zh','light',zh],['en','dark',en]]){
   // 1) empty root: 「初始化组织结构」 + the three workflows that come with Agent J
   let p=await open(w,scheme,language,empty);const tag=`${language}-${scheme}-${w}`;
   check(await ev(p,`document.querySelector('.fp-org button').textContent===${JSON.stringify(D['p276.setup'])}`),'first time: set up '+tag);
   check(await ev(p,`[...document.querySelectorAll('[data-starter]')].map(e=>e.dataset.starter).join()==='llm-wiki,script-article-writing,ebook-writing'`),'three starters '+tag);
   check(await ev(p,`document.querySelector('[data-starter="llm-wiki"]').textContent.includes(${JSON.stringify(D['p276.s1name'])})`),'starter name in language '+tag);
   check(await ev(p,`(()=>{const r=document.querySelector('.fp-org button').getBoundingClientRect();return r.height>=44&&getComputedStyle(document.querySelector('.fp-org button')).backgroundColor==='rgba(0, 0, 0, 0)';})()`),'secondary style, 44px target '+tag);
   check(await ev(p,`document.documentElement.scrollWidth<=innerWidth`),'no horizontal overflow '+tag);
   writeFileSync(out+`empty-${tag}.png`,await shoot(p,''));
   let n=fake.st.says.length;await click(p,'.fp-org button');await waitFor(p,`document.getElementById('five-page').hidden`);
   for(let i=0;i<40&&fake.st.says.length===n;i++)await sleep(100);
   const said=fake.st.says.at(-1)?.text||'';
   check(said===D['p276.setupPrompt'],'set-up prompt sent as is '+tag);
   check(['agentj-workflow-wizard','documentation/ROLES.md','agentj wizard register --dir . --json','agentj wizard doctor --dir . --json'].every(x=>said.includes(x)),'prompt carries skill + roster + self-check '+tag);
   await click(p,'#orb');await click(p,'.fp-menu-card[data-page="workflows"]');await waitFor(p,`!!document.querySelector('.fp-org button')`);
   check(await ev(p,`document.querySelector('.fp-org button').textContent===${JSON.stringify(D['p276.review'])}`),'after one tap: review '+tag);
   n=fake.st.says.length;await click(p,'[data-starter="ebook-writing"] .fp-secondary');for(let i=0;i<40&&fake.st.says.length===n;i++)await sleep(100);
   check((fake.st.says.at(-1)?.text||'').includes('agentj plaza install ebook-writing'),'starter install prompt '+tag);
   check(await ev(p,`!document.body.dataset.uiError`),'no UI error (empty) '+tag);await p.dispose();
   // 2) the P244 fixture root: workflows found, roster ready → 「复查」; unregistered ones carry the badge
   p=await open(w,scheme,language,fx.wf_list);
   await waitFor(p,`document.querySelectorAll('[data-folder]').length>=3`);
   check(await ev(p,`document.querySelector('.fp-org button').textContent===${JSON.stringify(D['p276.review'])}`),'organized root: review '+tag);
   check(await ev(p,`!document.querySelector('[data-starter]')`),'no starters when workflows exist '+tag);
   check(await ev(p,`!!document.querySelector('[data-folder="video-studio"] .fp-unreg') && !document.querySelector('[data-folder="music-studio"] .fp-unreg')`),'unregistered badge only where missing '+tag);
   writeFileSync(out+`found-${tag}.png`,await shoot(p,''));
   check(await ev(p,`!document.body.dataset.uiError`),'no UI error (found) '+tag);await p.dispose();
  }
 }
 console.log(`P276 PASS ${checks} checks`);writeFileSync(out+'browser.json',JSON.stringify({ok:true,checks,results},null,2));
}finally{await B.close();await fake.stop();await web.stop();}
