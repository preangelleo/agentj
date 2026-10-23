// P482 public-chain phone copy: actual before/current privacy projection, same encrypted phone renderer.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out='reports/qa/p482/phone';mkdirSync(out,{recursive:true});
const py=process.env.AJ_PYTHON || execFileSync('git',['rev-parse','--path-format=absolute','--git-common-dir'],{encoding:'utf8'}).trim()+'/../host/.venv/bin/python';
const fixture=JSON.parse(execFileSync(py,['-c',`
import sys, types, subprocess, json
sys.path[:0]=['agentjarvis/host','host/tests']
from agentj import privacy
from test_p482_chain import b58
old=types.ModuleType('p482_old_privacy');sys.modules[old.__name__]=old;old.__file__=privacy.__file__
exec(compile(subprocess.check_output(['git','show','4c6bb4942:host/agentj/privacy.py']),'<baseline>','exec'),old.__dict__)
values=[b58(44),b58(88),'0x'+'aB19'*10,'0x'+'aB19'*16]
raw='SEAL 911 report\\n\\n'+'\\n\\n'.join('Address / transaction\\n\\n\\x60\\x60\\x60\\n'+v+'\\n\\x60\\x60\\x60' for v in values)
print(json.dumps({'before':old.owner_text(raw),'after':privacy.owner_text(raw),'values':values}))
`],{encoding:'utf8'}));
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();let checks=0;
const result=[];
try{for(const lang of ['zh','en'])for(const phase of ['before','after']){
 fake.reset();fake.st.prefs={appearance:{language:lang},voice:{speak_replies:false}};
 const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();`);
  await fake.addTurn({k:'phone',text:'SEAL 911'},fixture[phase]);
  await waitFor(p,`document.querySelector('#words pre code')`);
  const code=await evaluate(p,`Array.from(document.querySelectorAll('#words pre code')).map(n=>n.textContent.trim())`);
  assert.equal(code.length,4);checks++;
  if(phase==='after'){assert.deepEqual(code,fixture.values);checks++;
   // Select/copy through the real browser clipboard, preserving every character.
   await evaluate(p,`(()=>{const r=document.createRange();r.selectNodeContents(document.querySelector('#words pre code'));const s=getSelection();s.removeAllRanges();s.addRange(r);document.execCommand('copy');s.removeAllRanges();})()`);
   assert.equal((await evaluate(p,`navigator.clipboard.readText()`)).trim(),fixture.values[0]);checks++;
  }else{assert.ok(code[0].includes('<redacted>'));assert.ok(code[1].includes('<redacted>'));checks+=2;}
  const layout=await evaluate(p,`(()=>{const w=document.getElementById('words'),r=w.getBoundingClientRect();return {x:r.x,width:r.width,viewport:innerWidth};})()`);
  assert.ok(layout.x>=0 && layout.width<=layout.viewport);checks++;
  writeFileSync(`${out}/${lang}-${phase}.png`,await shoot(p));result.push({lang,phase,layout});
  assert.deepEqual(p.problems,[]);checks++;
 }finally{await p.dispose();}
}}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(out+'/results.json',JSON.stringify({checks,result,scope:'390px isolated Chromium, real privacy projection and Noise fake-host transport; no physical phone/production'},null,2)+'\n');
writeFileSync(out+'/index.html','<!doctype html><meta charset="utf-8"><title>P482 before / after</title><style>body{font:16px sans-serif}section{display:flex;gap:16px}img{width:390px}</style><h1>P482 · before / after</h1>'+['zh','en'].map(l=>`<h2>${l}</h2><section><img src="${l}-before.png"><img src="${l}-after.png"></section>`).join(''));
const rec=parityRecorder('web/test/p482.mjs');
rec.record('P482 public-chain phone copy',true);rec.write();
console.log(`P482 phone PASS ${checks} checks`);
