// P302 追加 28 (owner 2026-10-15 11:0x: 「以后所有的这种弹窗……最高限度就是 message card 现在的这个顶部……不会和 weekly 的百分比重叠」):
// every panel that rises from the bottom stops at the sheet ceiling (DESIGN_TOKENS.md「Sheet ceiling」) — its top is never
// above the WEEKLY label's bottom. 390 × 844 phone and a fold outer screen (344 × 882), dark: the question card with three
// long questions, the / command card, the @ document list. Own Chromium (never :9222), the real client against fakehost.mjs.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep,key} from './browser.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));const out=root+'reports/qa/p302/after/';mkdirSync(out,{recursive:true});
const fx=JSON.parse(readFileSync(root+'host/tests/fixtures/p244_wf.json','utf8'));
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let checks=0;
const ev=(p,s)=>evaluate(p,s),check=(v,label)=>{assert.ok(v,label);checks++;console.log('  ✓ '+label);};
const hex=n=>[...crypto.getRandomValues(new Uint8Array(n/2))].map(b=>b.toString(16).padStart(2,'0')).join('');
const opt=(l,d)=>({l,d});
const QS=[1,2,3].map(i=>({q:`【轻度访谈 ${i}/3】你最想先交给 AI 的是哪些活？这一题故意写得很长很长，好让卡片比屏幕还高`,h:'业务'+i,m:i===2,
 o:[opt('跨境电商卖货','亚马逊、Shopify、TikTok Shop 等平台卖实物'),opt('内容自媒体','视频、公众号、播客，靠流量和付费变现'),opt('咨询或服务','按项目或按小时收费'),opt('软件或 SaaS','卖订阅或授权')]}));
// the WEEKLY label's bottom and an element's top, in viewport px
const LINE=`(()=>{const w=document.getElementById('mWeek'),l=w.querySelector('.lab');return Math.max(w.getBoundingClientRect().bottom,l.getBoundingClientRect().bottom);})()`;
const TOP=sel=>`document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect().top`;
try{
 for(const [w,h] of [[390,844],[344,882]]){const name=`${w}x${h}`;
  fake.reset();fake.st.caps=['wf1'];
  fake.st.onApp=async(c,m,send)=>{if(m.t==='wf_list'){if(m.etag){await send({t:'wf_same',r:m.r});return true;}for(const x of fx.wf_list)await send({...x,r:m.r});return true;}return false;};
  const p=await newPage(B,w,h,'dark',{allow:[web.url,fake.relay+'/'],touch:true});await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  await fake.send({t:'meter',model:'opus',model_name:'Opus 5.5',effort:'medium',ctx:{used:30,max:100},h5:{pct:28,reset:0},week:{pct:11,reset:0},at:0});
  await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);await sleep(250);
  await waitFor(p,`document.getElementById('mWeek').getBoundingClientRect().height>0&&document.querySelector('#mWeek .lab').getBoundingClientRect().height>0`);
  const line=await ev(p,LINE);check(line>40,`the WEEKLY label is on screen (bottom ${line.toFixed(1)}, ${name})`);
  // the ceiling = the message card's top
  const card=await ev(p,`(()=>{const d=document.getElementById('deck');return d.getBoundingClientRect().top+parseFloat(getComputedStyle(d).paddingTop);})()`);
  // ---- the question card ----
  await fake.send({t:'status',s:'waiting',agent:'claude',name:'Agent J'});
  await fake.send({t:'question',id:hex(32),ttl:180,qs:QS});
  await waitFor(p,`document.body.dataset.sheet==='1'`);await sleep(800);
  writeFileSync(out+`28-question-${name}.png`,await shoot(p,''));
  const qTop=await ev(p,TOP('#sheet'));
  check(await ev(p,`document.getElementById('sheet').parentElement.id==='stage'`),'P460 question belongs to the stage overlay');
  check(qTop>=card-1&&qTop<=card+1,`question card top ${qTop.toFixed(1)} = the message card top ${card.toFixed(1)} (${name})`);
  check(qTop>=line-0.5,`question card top ${qTop.toFixed(1)} ≥ WEEKLY label bottom ${line.toFixed(1)} (${name})`);
  check(await ev(p,`(()=>{const s=document.getElementById('sheet');return s.scrollHeight>s.clientHeight+20&&getComputedStyle(s).overflowY!=='visible';})()`),`a long question card scrolls inside (${name})`);
  check(await ev(p,`parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-sheet-ceil'))>=${line}-0.5`),`--ui-sheet-ceil ≥ the WEEKLY label (${name})`);
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await fake.send({t:'question_done',id:'x',result:'gone'});
  await ev(p,`document.getElementById('sheetClose')?.click()`);await sleep(300);
  // ---- the / command card ----
  await ev(p,`document.getElementById('slashBtn').click()`);await waitFor(p,`!document.getElementById('menu').hidden`);await sleep(600);
  writeFileSync(out+`28-slash-${name}.png`,await shoot(p,''));
  const sTop=await ev(p,TOP('#menuSheet'));
  check(sTop>=line-0.5,`/ card top ${sTop.toFixed(1)} ≥ WEEKLY label bottom ${line.toFixed(1)} (${name})`);
  await key(p,'Escape');await sleep(300);
  // ---- the @ list ----
  await ev(p,`(()=>{const i=document.getElementById('input');i.focus();i.value='现在 @';i.setSelectionRange(4,4);i.dispatchEvent(new Event('input'));})()`);await sleep(500);
  await waitFor(p,`!!document.getElementById('five-mentions')&&!document.getElementById('five-mentions').hidden`);{
   const mTop=await ev(p,TOP('#five-mentions'));writeFileSync(out+`28-mention-${name}.png`,await shoot(p,''));
   check(mTop>=line-0.5,`@ list top ${mTop.toFixed(1)} ≥ WEEKLY label bottom ${line.toFixed(1)} (${name})`);
  }
  await p.close?.();
 }
 console.log(`P302 sheet ceiling: ${checks} checks passed`);
}finally{await B.close?.();await fake.close?.();await web.close?.();}
process.exit(0);
