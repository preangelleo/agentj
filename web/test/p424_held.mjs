// P424 #75 · real Chromium + real Noise to the local fake host (pages shaped like agentj/held.py writes them).
// Owner (10-17): 「这条消息是怎么产生的？既没有 input，怎么会产生 output？……我怎么会发了一个空消息呢？」 The shared Claude Code did
// not take a phone message in time: the 「尚未接收」 reason is on that message's own page (the owner's words right above it,
// one page, not a system page that reads as an empty message); the settings diagnosis (crossSessionInbound …) is folded
// behind 「详情」; when the message reaches the Agent after all the page shows one 「已送达」 line and mark, 「详情」 gone.
// 390 / 1440 × light (zh) / dark (en) → reports/qa/p424/held/<tag>/. P424_TAG=before = HEAD's phone + the old host output
// (owner page with no reply + a separate `sys` notice page carrying the diagnosis), recorded only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitState,waitFor,sleep,shoot} from './browser.mjs';
const ROOT=fileURLToPath(new URL('../../../',import.meta.url));
const TAG=process.env.P424_TAG||'after',BEFORE=TAG==='before';
const out=join(ROOT,`reports/qa/p424/held/${TAG}/`);mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p424-held-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
if(BEFORE)for(const f of ['js/relay.js','app.css'])writeFileSync(join(tmp,f),execFileSync('git',['show','HEAD:web/public/'+f],{cwd:ROOT,maxBuffer:64<<20}));
const rec=parityRecorder('web/test/p424_held.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[],seen=[];
let held=null;
const check=(ok,msg)=>{if(BEFORE){seen.push({ok:!!ok,msg});return;}assert.ok(ok,msg);};
const q=JSON.stringify;
// the host's words (claude_inbound.turn_notice / diagnostic / held.delivered), one language each
const T={zh:{ask:'帮我调研一下 PPT 模板，下午开会要用',
  notice:'Agent J 主机上的 Claude Code 尚未接收这条消息：设置已允许手机消息，它多半正忙（在跑工具或等你处理Agent J 主机上的对话框）。请先在Agent J 主机核实，别重复发送，原消息仍可能送达。',
  detail:'Claude 实际文件设置：user 层 crossSessionInbound=accept。设置前启动的会话仍可能扣住消息，需新会话或在Agent J 主机运行 /clear 生效；会话/CLI 与远程 managed 覆盖无法从文件核实。',
  done:'Agent J 主机上的 Claude Code 后来接收了这条消息，回复会接着出现。',details:'详情',delivered:'已送达',mark:'尚未接收'},
 en:{ask:'Research some slide templates for this afternoon',
  notice:'Claude Code on the Agent J host has not taken this message: phone messages are allowed, so it is most likely busy (running a tool or waiting on a desktop dialog). Check the desktop before resending: the original may still arrive.',
  detail:'Claude settings: user crossSessionInbound=accept. Sessions started before the change may still hold messages; start a new session or run /clear on the Agent J host for it to take effect. Session/CLI and remote managed overrides cannot be verified from these files.',
  done:'Claude Code on the Agent J host took this message after all; its reply follows.',details:'Details',delivered:'Delivered',mark:'has not taken'}};
const view=p=>ev(p,`(()=>{const x=document.getElementById('cmdx'),d=x.querySelector('details.heldx'),s=d?.querySelector('summary'),ok=x.querySelector('.heldok');
 return {pg:document.getElementById('pg').textContent.trim(),om:document.getElementById('omText').textContent,omHeader:document.getElementById('om').dataset.header,
  words:document.getElementById('words').innerText,cmdx:!x.hidden,details:!!d,open:!!d?.open,summary:s?.textContent||'',sumH:s?Math.round(s.getBoundingClientRect().height):0,
  detailText:d?.querySelector('p')?.textContent||'',ok:ok&&!ok.hidden?ok.textContent:'',okH:ok?Math.round(ok.getBoundingClientRect().height):0,
  visible:document.body.innerText};})()`);
async function pair(w,scheme,lang){
 fake.reset();const L=T[lang];
 if(BEFORE){await fake.addTurn({k:'phone',dev:'X',name:'Remote',text:L.ask},'','failed');await fake.addTurn({k:'sys',text:''},L.notice+' '+L.detail,'done');}
 else held=(await fake.addTurn({k:'phone',dev:'X',name:'Remote',text:L.ask},L.notice,'done',{held:'pending',detail:L.detail})).id;
 const p=await newPage(B,w,900,scheme,{allow:[web.url,fake.relay+'/'],touch:w<500});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await waitFor(p,`document.getElementById('words').innerText.includes(${q(L.notice.slice(0,12))})`);
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);await sleep(150);return p;
}
try{
 for(const [w,scheme,lang] of [[390,'light','zh'],[390,'dark','en'],[1440,'light','zh'],[1440,'dark','en']]){
  const tag=`${w}-${scheme}`,L=T[lang],p=await pair(w,scheme,lang),r={w,scheme,lang};
  const shot=async name=>writeFileSync(join(out,`${tag}-${name}.png`),await shoot(p));
  await shot('held');let v=r.held=await view(p);
  check(v.pg==='1 / 1',`#75 ${tag}: one page, not a second 「empty」 one (${v.pg})`);
  check(v.om===L.ask&&v.omHeader==='0',`#75 ${tag}: the owner's own words above the notice ${q(v.om)}`);
  check(v.words.includes(L.mark)&&!/crossSessionInbound/.test(v.words),`#75 ${tag}: the reason in the words, no internal field ${q(v.words.slice(0,80))}`);
  check(v.details&&!v.open&&v.summary===L.details&&v.sumH>=44,`#75 ${tag}: 「${L.details}」 folded, ≥ 44 px (${v.summary} ${v.sumH})`);
  check(!/crossSessionInbound/.test(v.visible),`#75 ${tag}: the field is not visible while 「${L.details}」 is folded`);
  await ev(p,`document.querySelector('#cmdx details.heldx summary')?.click()`);await sleep(150);await shot('held-details');
  v=r.details=await view(p);
  check(v.open&&/crossSessionInbound/.test(v.detailText),`#75 ${tag}: 「${L.details}」 opens on the diagnosis`);
  // the message reaches Claude Code after all → 「已送达」, the notice and the details go
  if(!BEFORE){await fake.updateTurn(held,{reply:{text:L.done},card:{held:'delivered'}});
   await waitFor(p,`!!document.querySelector('#cmdx .heldok')`);}
  await sleep(150);await shot('delivered');v=r.delivered=await view(p);
  check(v.ok===L.delivered&&!v.details&&v.okH>=44,`#75 ${tag}: one 「${L.delivered}」 mark, 「${L.details}」 gone (${q(v.ok)} ${v.okH})`);
  check(v.words.includes(L.done.slice(0,30))&&!v.words.includes(L.mark),`#75 ${tag}: the notice gave way to the delivered line`);
  check(v.pg==='1 / 1'&&v.om===L.ask,`#75 ${tag}: still the owner's page`);
  check(p.problems.length===0,'no console errors '+p.problems.join(' | '));
  results.push(r);console.log(`P424 #75 held ${tag} ${BEFORE?'RECORDED':'PASS'}`);await p.dispose();
 }
 writeFileSync(join(out,'result.json'),JSON.stringify({ok:!BEFORE,tag:TAG,results,...(BEFORE?{seen}:{})},null,1)+'\n');
 if(BEFORE){const bad=seen.filter(x=>!x.ok);console.log(`P424 before: ${bad.length} of ${seen.length} checks red`);bad.forEach(x=>console.log('  red:',x.msg));}
 else{passed=true;console.log('P424 #75 held notice on the owner page, 「详情」, 「已送达」 PASS');}
}catch(e){console.error(e);process.exitCode=1;}finally{if(!BEFORE){rec.record('p424-held',passed);rec.write();}await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
