// P494: before/after phone receipts, actual host-generated startup guidance, local Noise fixture only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out=new URL('../../../reports/qa/p494/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();const checks=[];
try {
 for (const lang of ['zh','en']) {
  fake.reset();const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  for (const stage of ['before','after']) {
   const text=stage==='before' ? (lang==='zh'?'没清成：桌面终端未提供安全输入通道（no_exact_pane）':'No change: the desktop terminal has no safe input channel (no_exact_pane)') :
    (lang==='zh'?'没清成：无法安全控制这段桌面对话。请在 Agent J 主机上当前的 Claude Code 终端运行 /compact；以后可在手机设置中把「会话模式」改为「独立会话」，由 Agent J 管理新会话。':'No change: this desktop conversation cannot be controlled safely. Run /compact in its Claude Code terminal on the computer; for future phone controls, choose Use a separate session in Settings so Agent J manages the new session.');
   await fake.addTurn({k:'sys',text:''},text,'done');await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(stage==='before'?'no_exact_pane':'Claude Code')})`,5000);
   if(stage==='after') assert.equal(await evaluate(p,`document.getElementById('words').textContent.includes('no_exact_pane')`),false);
   writeFileSync(new URL(`${stage}-compact-${lang}.png`,out),await shoot(p,''));
  }
  for (const stage of ['before','after']) {
   const note=stage==='before'?(lang==='zh'?'OpenCode 还没准备好，这条消息没有交给它。':'OpenCode is not ready. Your message was not delivered.'):
    execFileSync(process.env.AJ_PYTHON||'python3',['-c',`from agentj.startup_help import guidance; print(guidance('opencode','listen','${lang}'))`],{env:{...process.env,PYTHONPATH:'agentjarvis/host'}}).toString().trim();
   await fake.addTurn({k:'sys',text:''},note,'done',{recovery:'restart_session'});
   await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(stage==='after'?'AJ_START_OPENCODE_LISTEN':lang==='zh'?'OpenCode 还没准备好':'OpenCode is not ready')})`,5000);
   assert.equal(await evaluate(p,`!!document.querySelector('#cmdx [data-recovery="restart_session"]')`),true);
   if(stage==='after') for(const wanted of ['PATH','HOME','agentj provider ark','agentj provider bailian','Kimi']) assert.equal(await evaluate(p,`document.getElementById('words').textContent.includes(${JSON.stringify(wanted)})`),true);
   writeFileSync(new URL(`${stage}-startup-${lang}.png`,out),await shoot(p,''));
  }
  checks.push(lang+': compact recovery and startup code/model guidance visible');
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('phone.json',out),JSON.stringify({ok:true,checks},null,2)+'\n');console.log('P494 phone PASS',checks);

if(process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p494.mjs',results:{'p494-mac-pane':'pass','p494-startup-guidance':'pass'}}));
