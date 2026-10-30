// P581: host-filtered official notes, delivered over Noise to the existing phone history renderer.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot} from './browser.mjs';

const out=resolve(process.env.P581_OUT||'reports/qa/p581/phone');
mkdirSync(out,{recursive:true});
const prose={
 zh:{title:'Agent J 主机更新了什么',common:'手机连接更加稳定。',legacy:'通知显示更加清楚。',claude:'Claude 状态栏升级后自动修复。',codex:'Codex 会话恢复更加可靠。',opencode:'OpenCode 配置读取更加可靠。'},
 en:{title:'What changed in the Agent J host',common:'Phone connections are more reliable.',legacy:'Notices are easier to read.',claude:'The Claude status line repairs itself after an upgrade.',codex:'Codex sessions resume more reliably.',opencode:'OpenCode configuration reads are more reliable.'},
};
const notice={type:'upgrade'};
for(const [lang,s] of Object.entries(prose)){
 notice['title_'+lang]=s.title;
 notice['body_'+lang]=['[all] '+s.common,s.legacy,...['claude','codex','opencode'].map(h=>'['+h+'] '+s[h])].join('\n');
}
// Real host formatter, with no State, network, environment-file or credential access.
const formatted=JSON.parse(execFileSync(process.env.P581_PYTHON||'python3',['-c',
 'import json,sys; sys.path.insert(0,"agentjarvis/host"); from agentj import notices; n=json.load(sys.stdin); print(json.dumps({lang:{h:notices.body(n,lang,h) for h in ("claude","codex","opencode")} for lang in ("zh","en")}))'],
 {cwd:resolve('.'),input:JSON.stringify(notice),encoding:'utf8'}));

const web=await startWebServer({port:0}),fake=await startFakeHost(),B=await launch(),evidence=[];
try{
 for(const width of [390,1440])for(const scheme of ['light','dark'])for(const lang of ['zh','en']){
  fake.reset();fake.st.prefs={appearance:{language:lang,theme:scheme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
  const p=await newPage(B,width,844,scheme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   const s=prose[lang],prefix=lang==='zh'?'Agent J 官方':'Agent J official';
   const present=async(text)=>{
    await fake.addTurn({k:'sys',text:''},prefix+' · '+s.title+'\n'+text);
    await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(text.split('\n').at(-1))})`);
    return evaluate(p,`document.getElementById('words').textContent`);
   };
   // Before image is a faithful raw-notice input, not an assertion of a second UI implementation.
   await present(notice['body_'+lang]);
   writeFileSync(join(out,`${width}-${scheme}-${lang}-before.png`),await shoot(p));
   for(const harness of ['claude','codex','opencode']){
    await fake.send({t:'status',s:'idle',agent:harness,name:'Agent J'});
    const text=await present(formatted[lang][harness]);
    assert.ok(text.includes(s.common));assert.ok(text.includes(s.legacy));assert.ok(text.includes(s[harness]));
    for(const other of ['claude','codex','opencode'].filter(h=>h!==harness))assert.ok(!text.includes(s[other]),`${harness} excludes ${other}`);
    assert.ok(!/\[(?:all|claude|codex|opencode)\]/.test(text),'harness labels are not user prose');
    writeFileSync(join(out,`${width}-${scheme}-${lang}-${harness}-after.png`),await shoot(p));
    evidence.push({width,scheme,lang,harness,text});
   }
   assert.deepEqual(p.problems,[]);
  }finally{await p.dispose();}
 }
 writeFileSync(join(out,'evidence.json'),JSON.stringify(evidence,null,2));
 console.log(`PASS P581 phone: ${evidence.length} rendered cases; 32 before/after screenshots`);
}finally{
 // browser.mjs stops its guarded process group before Linux-only /proc cleanup.
 // On macOS retain that real shutdown, then remove only this test's own temp profile.
 try{await B.close();}catch(e){if(process.platform!=='darwin'||e.code!=='ENOENT'||e.path!=='/proc')throw e;rmSync(B.profile,{recursive:true,force:true});}
 await fake.stop();await web.stop();
}
