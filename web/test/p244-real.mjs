// P244 real-machine shared Codex session + real host + local Noise relay + real phone-width browser.
// Only the native Codex login is linked into a throw-away CODEX_HOME. No QA account, provider or production state changes.
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,symlinkSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir,homedir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync,spawn} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startLocalRelay} from '../../relay/test/local_relay.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot} from './browser.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url)),out=join(root,'reports/qa/p244');
const tmp=mkdtempSync(join(tmpdir(),'p244-real-')),work=join(tmp,'work'),ch=join(tmp,'codex');mkdirSync(work);mkdirSync(ch);mkdirSync(join(tmp,'state'));
symlinkSync(join(homedir(),'.codex/auth.json'),join(ch,'auth.json'));
writeFileSync(join(ch,'config.toml'),'sandbox_mode = "workspace-write"\napproval_policy = "never"\n');
const py=process.env.AJ_TEST_PYTHON || fileURLToPath(new URL('../../host/.venv/bin/python',import.meta.url));
const wrapper=join(tmp,'agentj');writeFileSync(wrapper,`#!/bin/sh\nexec '${py}' '${root}host/tests/wiredump.py' "$@"\n`);chmodSync(wrapper,0o700);
process.env.AJ_TEST_HOST_BIN=wrapper;
const {HostProc}=await import('../../tests/lib/host.mjs');
const env={CODEX_HOME:ch,XDG_CONFIG_HOME:join(tmp,'xdg'),AGENTJ_SETUP_AGENTS:'0',PYTHONPATH:join(root,'agentjarvis/host')};
let relay,web,B,host,p;const result={task:'P244',mode:'real shared Codex + local Noise + Chromium 393×852',checks:[]};
const check=(ok,name)=>{assert.ok(ok,name);result.checks.push(name);console.log('PASS '+name);};
async function nativeThread(){
 const proc=spawn('codex',['app-server'],{cwd:work,env:{...process.env,...env},stdio:['pipe','pipe','pipe'],detached:true});
 let seq=0,buffer='',notes=[],waiters=new Map(),finish,toolCalls=0;
 const completed=new Promise(resolve=>finish=resolve);
 const send=o=>proc.stdin.write(JSON.stringify(o)+'\n');
 proc.stdout.on('data',d=>{buffer+=d;let at;while((at=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,at);buffer=buffer.slice(at+1);let m;try{m=JSON.parse(line);}catch{continue;}
  if(m.id!==undefined&&!m.method){const w=waiters.get(m.id);if(w){waiters.delete(m.id);m.error?w.reject(new Error('Native RPC: '+m.error.message)):w.resolve(m.result);}}
  else if(m.id!==undefined&&m.method){toolCalls++;send({id:m.id,result:{decision:'decline'}});}
  else if(m.method==='turn/completed')finish(m.params);
 }});
 const call=(method,params)=>new Promise((resolve,reject)=>{const id=++seq;waiters.set(id,{resolve,reject});send({id,method,params});});
 const timeout=new Promise((_,reject)=>setTimeout(()=>reject(new Error('native read-only setup timeout')),90000));
 try{return await Promise.race([(async()=>{
  await call('initialize',{clientInfo:{name:'p244-qa',version:'1'},capabilities:{experimentalApi:true}});send({method:'initialized'});
  const started=await call('thread/start',{cwd:work,model:'gpt-6.1-sol',approvalPolicy:'never',permissions:':read-only',persistExtendedHistory:true});
  const id=started.thread.id;await call('turn/start',{threadId:id,input:[{type:'text',text:'Reply exactly P244_NATIVE_READY. Do not call any tools.',text_elements:[]}]});await completed;assert.equal(toolCalls,0);return id;
 })(),timeout]);}finally{try{process.kill(-proc.pid,'SIGTERM');}catch{};}
}
try{
 const sid=await nativeThread();check(!!sid,'native Codex read-only session created');
 // Scanner fixture creates three WDB companies inside this throw-away working root.
 execFileSync(py,['-c',"import sys;sys.path.insert(0,'host/tests');from p244_fixture import build;from pathlib import Path;import shutil;fx=build(sys.argv[1]);[shutil.copytree(p,Path(sys.argv[2])/p.name,dirs_exist_ok=True) for p in fx['root'].iterdir() if p.is_dir() and not p.is_symlink()]",join(tmp,'fx'),work],{cwd:root,env:{...process.env,...env}});
 relay=await startLocalRelay();web=await startWebServer();host=new HostProc({relay:relay.url,web:web.url,stateDir:join(tmp,'state'),env,sessionMode:'shared'}).init();
 host.run(['agent','codex','--dir',work,'--model','gpt-6.1-sol','--shared']);
 host.run(['config','set','agent.shared_session_id',sid]);
 await host.serve();B=await launch();p=await newPage(B,393,852,'dark',{touch:true,allow:[web.url,relay.url]});
 const pr=await host.pair();await navigate(p,web.url+pr.link.slice(pr.link.indexOf('#')));await waitState(p,'awaiting-approval');
 const sas=(await evaluate(p,"document.getElementById('sas').textContent")).replace(/\D/g,'');await pr.waitPrompt();pr.typeCode(sas);await waitState(p,'ready');check((await pr.exited).code===0,'real host pairing complete');
 await evaluate(p,"document.getElementById('phone-env-dismiss')?.click()");
 const orb="(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));})()";
 await evaluate(p,orb);await evaluate(p,"document.querySelector('[data-page=workflows]').click()");await waitFor(p,"document.querySelectorAll('[data-folder]').length>=3",30000);check(true,'real host scanned three workflows over Noise');
 await evaluate(p,"document.querySelector('[data-folder=video-studio] .fp-at').click()");await waitFor(p,"!!document.querySelector('.workflow-token')");check(await evaluate(p,"document.getElementById('input').value.includes('CEO')"),'workflow mention remains plain transport text');
 // No delegation needed for the smoke: a short tool-free phone turn resumes that native desktop thread.
 await evaluate(p,"(()=>{const i=document.getElementById('input');i.value='请只回复 P244_SHARED_OK，不调用工具。';i.dispatchEvent(new Event('input'));})()");
 await waitFor(p,"!document.getElementById('send').disabled");await evaluate(p,"document.getElementById('send').click()");
 await waitFor(p,"document.getElementById('words').textContent.includes('P244_SHARED_OK')",120000);check(true,'phone message answered by real shared Codex session');
 writeFileSync(join(out,'real-shared-393-dark.png'),await shoot(p,''));result.ok=true;
}catch(e){result.ok=false;result.error=String(e);if(p){result.ui=await evaluate(p,"({state:window.__ajState,status:document.body.dataset.agent,words:document.getElementById('words').textContent.slice(0,600),input:document.getElementById('input').value,sendDisabled:document.getElementById('send').disabled})").catch(()=>({}));writeFileSync(join(out,'real-failure.png'),await shoot(p,''));}result.events=host?.events.map(e=>({ev:e.ev,s:e.s,why:e.why,reason:e.reason}));console.log(result.error,JSON.stringify(result.ui));process.exitCode=1;}
finally{if(B)await B.close();if(host)await host.stop();if(web)await web.stop();if(relay)await relay.stop();rmSync(tmp,{recursive:true,force:true});mkdirSync(out,{recursive:true});writeFileSync(join(out,'real-shared.json'),JSON.stringify(result,null,2));}
