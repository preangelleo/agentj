// Actual Python startup_help -> real phone renderer. Desktop Chromium simulation, not native Windows/iOS.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out='reports/qa/p385/startup-evidence';mkdirSync(out,{recursive:true});
const common=spawnSync('git',['rev-parse','--path-format=absolute','--git-common-dir'],{encoding:'utf8'});
assert.equal(common.status,0,common.stderr);
const sharedPython=resolve(common.stdout.trim(),'..','host/.venv/bin/python');
const py=spawnSync(sharedPython,['-c',`import json
from agentj.agent_opencode import startup_help
print(json.dumps({lang:{status:startup_help('models.dev SYNTHETIC_PRIVATE_SENTINEL',slow=status=='slow',lang=lang) for status in ('slow','failed')} for lang in ('zh','en')}))`],{env:{...process.env,PYTHONPATH:'agentjarvis/host'},encoding:'utf8'});
assert.equal(py.status,0,py.stderr);const notices=JSON.parse(py.stdout);
for(const lang of ['zh','en'])for(const kind of ['slow','failed']){assert.ok(!notices[lang][kind].includes('SYNTHETIC_PRIVATE_SENTINEL'));assert.ok(!notices[lang][kind].includes('models.dev'));}
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch(),results=[];
try{for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
 fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme}};
 const p=await newPage(B,width,width===390?844:900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.send({t:'status',s:'idle',agent:'opencode',name:'QA placeholder'});await evaluate(p,`document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true`);
  for(const kind of ['slow','failed']){
   const text=notices[lang][kind];await fake.addTurn({k:'sys'},text);
   await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify(kind==='slow'?'30':lang==='zh'?'未能启动':'did not start')})`);
   const code=await evaluate(p,`[...document.querySelectorAll('#words pre code')].map(n=>n.textContent.trim())`);assert.deepEqual(code,['agentj doctor','agentj service restart']);
   const rendered=await evaluate(p,`document.getElementById('words').textContent`);assert.ok(!rendered.includes('SYNTHETIC_PRIVATE_SENTINEL'));assert.ok(!rendered.includes('models.dev'));assert.ok(rendered.includes(lang==='zh'?'模型目录':'model catalog'));
   for(let i=0;i<2;i++){await evaluate(p,`document.querySelectorAll('#words .copy')[${i}].click();true`);await waitFor(p,`navigator.clipboard.readText().then(t=>t.trim()===${JSON.stringify(code[i])})`);}
   const m=await evaluate(p,`({lang:document.documentElement.lang,theme:document.documentElement.dataset.theme,overflow:document.documentElement.scrollWidth-innerWidth})`);assert.ok(m.lang.startsWith(lang));assert.equal(m.theme,theme);assert.equal(m.overflow,0);
   const file=`${kind}-${lang}-${theme}-${width}.png`;writeFileSync(`${out}/${file}`,await shoot(p,''));results.push({kind,width,...m,clipboard:2,file});
  }
 }finally{await p.dispose();}
}writeFileSync(`${out}/browser.json`,JSON.stringify({simulation:'Local desktop Chromium viewport/touch simulation. No Windows or iOS hardware.',origin:'Actual agentj.agent_opencode.startup_help with synthetic raw diagnostic sentinel',results},null,2));
 writeFileSync(`${out}/manifest.json`,JSON.stringify({files:[...results.map(r=>r.file),'browser.json'].map(name=>({name,sha256:createHash('sha256').update(readFileSync(`${out}/${name}`)).digest('hex')}))},null,2));
 console.log('PASS P385 startup-help evidence: 16 cases, 32 actual clipboard comparisons; raw diagnostic never rendered');
}finally{await B.close();await fake.stop();await web.stop();}
