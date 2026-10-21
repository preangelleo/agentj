// P299: the five menu pages at a 390 px iPhone width in light / dark / follow-system — menu canvas + header, inline code
// contrast, markdown title levels, long titles, card spacing, skill detail (owner-language summary, original folded),
// the weekly label, the 「全部」 icon. `node test/p299.mjs before` = shots only (the shipped 0.21.0a1 page); no tag = shots +
// checks. Own Chromium (never :9222), the real client against fakehost.mjs.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot,sleep} from './browser.mjs';
const tag=process.argv[2]||'after', CHECK=tag==='after';
const root=fileURLToPath(new URL('../../../',import.meta.url));const out=root+`reports/qa/p299/${tag}/`;mkdirSync(out,{recursive:true});
const fx=JSON.parse(readFileSync(root+'host/tests/fixtures/p244_wf.json','utf8'));
// Leo's real cases (IMG_2436–2440): a mixed English/Chinese bundled skill, a long CHANGELOG title, a doc with H1 + H2 + code.
const tail=structuredClone(fx.wf_list.at(-1));
tail.skills=[...tail.skills,{id:'global::zh-demo',name:'zh-demo',desc:'Demo of a host-sent Chinese line.',desc_zh:'主机送来的中文说明。',use_zh:'说「试一下」。',scope:'global'}];
tail.agentj.docs=[{name:'AWS_COST_HYGIENE.md',title:'AWS 成本审计与资源退役'},{name:'CHANGELOG.md',title:'CHANGELOG — ~/work（贾维斯的工作区）每一次变更的记录与回滚方法'},...tail.agentj.docs];
const list=[...fx.wf_list.slice(0,-1),tail];
const DOC='---\ntype: Changelog\n---\n# CHANGELOG — ~/work（贾维斯的工作区）\n\n## 2026-10-12 — boot set 落实「人人都能做董事长」\n\n- Leo 口述核心思想（原话 `~/work/notes/<redacted>.md`），贾维斯先改自己。\n- 备份 `~/.local/state/jarvis-docs-backup-20261012-2220/`。\n\n### 细节\n\n```bash\nagentj bots list\n```\n\n## 2026-10-07 — Agent J 0.15.7a1 上线\n\n正文一段。\n';
const SKILL='Use `agentj bots list`, then `agentj bots detail <bot-id>` and `agentj bots statistics <bot-id>`.\nManage bots for the owner from this main session.\n\n从主会话按主人的要求管理 bot。\n';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let checks=0;const results=[];
const ev=(p,s)=>evaluate(p,s),click=(p,s)=>ev(p,s==='#orb'?`(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));})()`:`document.querySelector(${JSON.stringify(s)}).click()`);
const cev=(p,s)=>CHECK?evaluate(p,s):null;
const check=(v,label)=>{if(!CHECK)return;assert.ok(v,label);checks++;results.push(label);};
// WCAG contrast of an element's text against the first opaque background up its tree (the palette has no translucent text here)
const CONTRAST=`(sel)=>{const e=document.querySelector(sel);if(!e)return 0;const rgb=s=>{const m=s.match(/[\\d.]+/g).map(Number),k=s.startsWith('color(')?255:1;return {c:m.slice(0,3).map(v=>v*k),a:m[3]??1};};
 const lum=c=>{const [r,g,b]=c.map(v=>{v/=255;return v<=.03928?v/12.92:((v+.055)/1.055)**2.4});return .2126*r+.7152*g+.0722*b;};
 let n=e,bg=null,stack=[];while(n&&n.nodeType===1){const b=rgb(getComputedStyle(n).backgroundColor);if(b.a>0){stack.push(b);if(b.a>=1)break;}n=n.parentElement;}
 let base=stack.length&&stack.at(-1).a>=1?stack.pop().c:[255,255,255];while(stack.length){const b=stack.pop();base=base.map((v,i)=>v*(1-b.a)+b.c[i]*b.a);}
 const f=rgb(getComputedStyle(e).color),fc=f.c.map((v,i)=>v*f.a+base[i]*(1-f.a));const a=lum(fc),b=lum(base);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);}`;
const MODES=[['light','light','light'],['dark','dark','dark'],['system','dark','system-dark'],['system','light','system-light']];
try{
 for(const [theme,scheme,name] of MODES){
  fake.reset();fake.st.caps=['wf1'];fake.st.prefs={appearance:{language:'zh',theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
  fake.st.onApp=async(c,m,send)=>{if(m.t==='wf_list'){if(m.etag){await send({t:'wf_same',r:m.r});return true;}for(const x of list)await send({...x,r:m.r});return true;}
   if(m.t==='wf_doc'){await send({t:'wf_doc',ok:true,r:m.r,folder:m.folder,name:m.name,title:m.name,text:DOC});return true;}
   if(m.t==='skill_get'){await send({t:'skill_get',ok:true,r:m.r,id:m.id,name:'agentj-bots',text:SKILL});return true;}return false;};
  const p=await newPage(B,390,844,scheme,{allow:[web.url,fake.relay+'/'],touch:true});await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
  await fake.send({t:'meter',model:'claude-opus-5-5',quota_windows:[{window:'weekly',pct:97},{window:'5h',pct:20}],ctx:{used:32000,max:200000}});
  await ev(p,`document.getElementById('phone-env-dismiss')?.click()`);await sleep(250);
  const open=async page=>{await click(p,'#orb');await waitFor(p,`!document.getElementById('five-menu').hidden`);await click(p,`.fp-menu-card[data-page="${page}"]`);await sleep(300);};
  const snap=async file=>writeFileSync(out+`${file}-390-${name}.png`,await shoot(p,''));
  await click(p,'#orb');await waitFor(p,`!document.getElementById('five-menu').hidden`);await snap('0-menu');await click(p,'#orb');
  await open('workflows');await waitFor(p,`document.querySelectorAll('[data-folder]').length>=3`);await snap('1-workflows');
  // the header is the menu header (the memory page's), not the chat state colour
  const menuBg=await ev(p,`(()=>{const d=document.createElement('div');d.style.background='var(--menu-bg)';document.getElementById('five-page').append(d);const c=getComputedStyle(d).backgroundColor;d.remove();return c;})()`);
  check(await cev(p,`getComputedStyle(document.getElementById('five-page')).backgroundColor`)===menuBg,'five page = menu canvas '+name);
  check(await cev(p,`getComputedStyle(document.querySelector('.top')).backgroundColor`)===menuBg,'header = menu canvas '+name);
  check(menuBg==='rgb(44, 43, 38)','canvas + header = Noir (owner 02:3x) '+name);
  check(await cev(p,`getComputedStyle(document.getElementById('five-title')).color`)==='rgb(250, 249, 245)','title = Lotion '+name);
  check(await cev(p,`document.querySelector('meta[name="theme-color"]').content`)==='#141414','OS bar = Sooty (P422) '+name);
  check(await cev(p,`getComputedStyle(document.getElementById('status')).display!=='none'&&getComputedStyle(document.getElementById('srchBtn')).display!=='none'`),'header = menu header (connection line and P363 search) '+name);
  check(await cev(p,`Math.abs(document.getElementById('five-page').getBoundingClientRect().top-document.querySelector('.top').getBoundingClientRect().bottom)<=1`),'page starts at the header edge '+name);
  check(await cev(p,`(()=>{const l=document.querySelector('#mWeek .lab'),r=l.getBoundingClientRect(),x=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return r.width===0||!l.contains(x);})()`),'weekly label not peeking under the header '+name);
  check(await cev(p,`(()=>{const c=[...document.querySelectorAll('#five-body .fp-card')];return c.every((e,i)=>!i||e.getBoundingClientRect().top>=c[i-1].getBoundingClientRect().bottom+8);})()`),'cards do not overlap '+name);
  await open('skills');await waitFor(p,`document.querySelectorAll('#five-body .fp-card').length>3`);await snap('2-skills');
  check(await cev(p,`[...document.querySelectorAll('#five-body .fp-card')].find(c=>c.textContent.includes('/agentj-bots')).textContent.includes('管理你的客服 bot')`)&&await cev(p,`[...document.querySelectorAll('#five-body .fp-card')].find(c=>c.textContent.includes('/zh-demo')).textContent.includes('主机送来的中文说明')`),'skill list in the owner language (shipped table + host desc_zh) '+name);
  check(await cev(p,`(()=>{const i=document.querySelector('.fp-category-all');return i&&getComputedStyle(i).backgroundColor==='rgba(0, 0, 0, 0)'&&i.querySelector('path').getAttribute('d').split('M').length>=3&&!/h1v1/.test(i.querySelector('path').getAttribute('d'));})()`),'「全部」 = a line icon without a fill '+name);
  check(await cev(p,`[...document.querySelectorAll('.fp-category-icon')].every(i=>getComputedStyle(i).backgroundColor==='rgba(0, 0, 0, 0)')`),'filter icons: line icons, no dark/colour disc '+name);
  check(await cev(p,`(${CONTRAST})('.fp-tab')>=4.5`),'filter chip text contrast '+name);
  await click(p,'.fp-card-head');await waitFor(p,`!document.getElementById('five-detail').hidden && (!!document.querySelector('#five-detail .fp-raw')||!!document.querySelector('#five-detail .fp-prose code'))`);await sleep(200);await snap('3-skill-detail');
  check(await cev(p,`document.querySelector('#five-detail .fp-summary').textContent.includes('管理你的客服 bot')&&document.querySelector('#five-detail .fp-summary').textContent.includes('帮我建一个客服 bot')`),'skill detail leads with the Chinese summary + usage '+name);
  check(await cev(p,`!document.querySelector('#five-detail details.fp-raw').open && document.querySelector('#five-detail details.fp-raw summary').textContent==='查看原文'`),'original SKILL.md folded behind 查看原文 '+name);
  await ev(p,`document.querySelector('#five-detail details.fp-raw')&&(document.querySelector('#five-detail details.fp-raw').open=true)`);await sleep(200);await snap('3b-skill-original');
  check(await cev(p,`(${CONTRAST})('#five-detail .fp-prose code')>=4.5`),'inline code ≥ 4.5:1 '+name+' '+await cev(p,`(${CONTRAST})('#five-detail .fp-prose code').toFixed(2)`));
  await ev(p,`document.querySelector('#five-detail .fp-back').click()`);
  await open('agentj');await waitFor(p,`document.querySelectorAll('#five-body .fp-card').length>3`);await snap('4-agentj');
  check(await cev(p,`[...document.querySelectorAll('#five-body .fp-card-title')].every(t=>t.scrollWidth<=t.clientWidth+1&&t.getBoundingClientRect().right<=t.closest('.fp-card').getBoundingClientRect().right-8)`),'long titles stay inside the card '+name);
  check(await cev(p,`(()=>{const c=[...document.querySelectorAll('#five-body .fp-card')];return c.every((e,i)=>!i||e.getBoundingClientRect().top>=c[i-1].getBoundingClientRect().bottom+8);})()`),'agentj cards do not overlap '+name);
  await ev(p,`[...document.querySelectorAll('#five-body .fp-card-head')].find(b=>b.textContent.includes('CHANGELOG')).click()`);await waitFor(p,`!document.getElementById('five-detail').hidden && document.querySelector('.fp-prose h2')`);await sleep(200);await snap('5-doc');
  check(await cev(p,`(()=>{const f=document.querySelector('#five-detail .fp-file-name'),h1=document.querySelector('.fp-prose h1'),h2=document.querySelector('.fp-prose h2'),fs=e=>parseFloat(getComputedStyle(e).fontSize);return f&&fs(f)<fs(h2)&&fs(h2)<fs(h1)&&fs(h1)<=24;})()`),'file name small, H1 > H2, H1 ≤ 24 px '+name);
  check(await cev(p,`(()=>{const h1=document.querySelector('.fp-prose h1'),h2=document.querySelector('.fp-prose h2');return h2.getBoundingClientRect().top-h1.getBoundingClientRect().bottom>=12;})()`),'H1 / H2 separated '+name);
  check(await cev(p,`!document.querySelector('.fp-prose').textContent.includes('type: Changelog')`),'front matter not rendered '+name);
  check(await cev(p,`(${CONTRAST})('.fp-prose li code')>=4.5`),'doc inline code ≥ 4.5:1 '+name);
  check(await cev(p,`(${CONTRAST})('.fp-prose p')>=4.5&&(${CONTRAST})('#five-detail .fp-file-name')>=4.5`),'doc text contrast '+name);
  await ev(p,`document.querySelector('#five-detail .fp-back').click()`);
  await ev(p,`document.querySelector('#five-page .fp-header .fp-back').click()`);await sleep(300);
  check(await cev(p,`getComputedStyle(document.querySelector('.top')).backgroundColor!=='rgb(44, 43, 38)'&&document.querySelector('meta[name="theme-color"]').content!=='#2c2b26'`),'back on the chat = the chat header again '+name);
  await open('memory');await sleep(200);await snap('6-memory');
  check(await cev(p,`[...document.querySelectorAll('.fp-file')].every(b=>b.getBoundingClientRect().height>=44)`),'memory file buttons ≥ 44 px '+name);
  check(await cev(p,`(${CONTRAST})('#five-body .fp-card p')>=4.5`),'card text contrast '+name);
  // the old menu page next to them for the side-by-side (「它记住了什么」)
  await ev(p,`[...document.querySelectorAll('#five-body .fp-action')].at(-1)?.click()`);await sleep(400);await snap('7-old-memory-page');
  check(!p.problems.length,'no console errors '+name+' '+p.problems.join(' | '));
  await p.dispose();
 }
 if(CHECK){console.log(`P299 PASS ${checks} checks`);writeFileSync(out+'browser.json',JSON.stringify({ok:true,checks,results},null,2));}
 else console.log('P299 shots',out);
}finally{await B.close();await fake.stop();await web.stop();}
