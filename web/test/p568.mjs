// P568: actual paired browser panel; before/after language and theme evidence, no external providers.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out=process.env.P568_SHOTS||'reports/qa/p568/screens';mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.P568_BEFORE?{publicDir:process.env.P568_BEFORE}:{}),fake=await startFakeHost(),B=await launch();
let n=0;
try{for(const lang of ['zh','en'])for(const theme of ['light','dark']){
 fake.reset();fake.setUp(true);
 let state={t:'wechat_state',bound:true,pending:0,error:'state_unwritable',last_forward:{ok:false,why:'needs_message',at:1}};
 fake.st.onApp=async(c,m,send)=>{if(m.t!=='wechat')return false;if(m.op==='status')await send(state);return true;};
 const p=await newPage(B,390,844,theme,{touch:true,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.dataset.theme='${theme}';true`);
  await evaluate(p,`import('./js/wechat.js').then(m=>m.show())`);await waitFor(p,`document.getElementById('wechat-panel')?.hidden===false`);
  if(!process.env.P568_BEFORE){
   await waitFor(p,`document.getElementById('wc-error').textContent.includes('macOS')`);
   assert.match(await evaluate(p,`document.getElementById('wc-forward').textContent`),lang==='zh'?/先/:/message/);
   assert.equal(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth+1`),true);
  }
  writeFileSync(`${out}/${lang}-${theme}-write-error.png`,await shoot(p,''));
  state={...state,bound:false,error:'binding_taken_over'};await fake.send(state);
  if(!process.env.P568_BEFORE)await waitFor(p,`document.getElementById('wc-error').textContent.includes('${lang==='zh'?'接管':'took over'}')`);
  writeFileSync(`${out}/${lang}-${theme}-taken-over.png`,await shoot(p,''));
  await evaluate(p,`document.getElementById('wc-close').click()`);
  if(process.env.P568_BEFORE){
   await fake.send({t:'agent_msg',text:lang==='zh'?'Codex 要你填一张表：手机上填不了，已拒绝。':'Codex asked for a form: unsupported on the phone, declined.'});
  }else{
   const qs=[{q:lang==='zh'?'选择项目\n项目名称':'Choose a project\nProject name',h:'MCP example',m:false,o:[{l:'Example',d:''}]}];
   await fake.send({t:'status',s:'waiting',kind:'question',agent:'codex',name:'Agent J'});
   await fake.send({t:'question',id:'568'.padEnd(32,'0'),qs,text_max:2000,ttl:180});
   await waitFor(p,`document.querySelectorAll('#qs .qfree textarea').length===1 && document.body.dataset.sheet==='1'`);
  }
  writeFileSync(`${out}/${lang}-${theme}-form.png`,await shoot(p,''));
  assert.deepEqual(p.problems,[]);n++;
 }finally{await p.dispose();}
}}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(out+'/results.json',JSON.stringify({pass:true,n,baseline:!!process.env.P568_BEFORE})+'\n');console.log('P568 browser PASS',n);
