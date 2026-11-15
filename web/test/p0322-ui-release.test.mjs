import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {sourceFiles} from '../build.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const read=p=>readFileSync(resolve(root,p));
const sha=b=>createHash('sha256').update(b).digest('hex');
const plan=JSON.parse(read('web/deferred/0.30.22/manifest.json'));
const review=JSON.parse(read('web/ui-review-0322-r2.json'));
test('historical deferred source stays recoverable and final approved active sources match the manifest',()=>{
 assert.equal(plan.baseline,'70c8cf3e5');assert.ok(plan.files.length>25);
 const shipped=sourceFiles();
 for(const row of plan.files){
  assert.match(row.saved,/^agentjarvis\/web\/deferred\/0\.30\.22\//);
  assert.equal(sha(read(row.saved)),row.candidate_sha256,row.path+' retained source');
  const expected=review.active[row.path]?.sha256 || row.active_sha256;
  assert.equal(sha(read(row.path)),expected,row.path+' active contract');
  if(row.disposition==='baseline-active' && !review.active[row.path])assert.equal(row.active_sha256,row.baseline_sha256,row.path+' exact baseline');
  const rel=row.path.replace('web/public/','');
  if(shipped.has(rel))assert.equal(sha(shipped.get(rel)),expected,row.path+' default build');
 }
 assert.equal([...shipped.keys()].some(x=>x.includes('deferred')),false);
});
test('final decision opens every approved group while the secret editor remains off',()=>{
 const shipped=sourceFiles(),text=p=>shipped.get(p).toString();
 assert.deepEqual(review.disabled,['U04']);
 for(let n=1;n<=16;n++)if(n!==4)assert.ok(review.enabled.includes('U'+String(n).padStart(2,'0')));
 assert.match(text('index.html'),/id="set-updates-state"/);
 assert.match(text('js/ui.js'),/notice-fifo/);
 assert.match(text('js/t.js'),/accountNodes/);
 assert.match(text('js/media.js'),/export function orphanPunct/);
 assert.doesNotMatch(text('js/elevate.js'),/fullEditor|editSecret\(/);
 assert.doesNotMatch(text('js/keys.js'),/fullEditor|editSecret\(/);
 assert.doesNotMatch(text('js/fsedit.js'),/fs-secret-shell|secretMode/);
 assert.match(read('web/build.mjs').toString(),/accountHtml/);
 assert.match(text('index.html'),/id="silentHelp"/);
 for(const id of ['ar-relay-keys','acct-relay-keys'])assert.ok(text('index.html').includes('id="'+id+'"'));
 assert.match(text('js/approval-layout0281.js'),/friendsTellCard/);
 assert.match(text('js/approval-layout0281.js'),/plazaRemoval/);
 assert.match(text('js/pkpeer.js'),/p0285\.uv\.ineligibleKey/);
 assert.match(text('js/relay.js'),/backgroundCommandEntry/);
 assert.match(text('app.js'),/elevate\.forgetInput\(\)/);
 assert.match(text('js/elevate.js'),/export function forgetInput/);
});

import vm from 'node:vm';
test('approved Relay vendor is active and ordinary vendors and unrelated third-party keys remain available',async()=>{
 globalThis.window??=globalThis;
 globalThis.addEventListener??=()=>{};
 globalThis.document??={addEventListener(){},documentElement:{dataset:{}},getElementById:()=>null,querySelectorAll:()=>[],body:{}};
 const {parse}=await import('../public/js/keys.js');
 const row=name=>({type:'key',name,set:true});
 const model=parse({t:'keys',id:'1'.repeat(32),n:'2'.repeat(32),base:[
  {id:'agentsrelay',name:{en:'AgentsRelay'},rows:[row('AGENTSRELAY_CLAUDE_KEY'),row('AGENTSRELAY_OPENAI_KEY')]},
  {id:'openai',name:{en:'OpenAI'},rows:[row('OPENAI_API_KEY'),row('AGENTSRELAY_CLAUDE_KEY')]}
 ],third:[row('AGENTSRELAY_OPENAI_KEY'),row('OTHER_API_KEY')]});
 assert.deepEqual(model.base.map(v=>v.id),['agentsrelay','openai']);
 assert.deepEqual(model.base[0].rows.map(v=>v.name),['AGENTSRELAY_CLAUDE_KEY','AGENTSRELAY_OPENAI_KEY']);
 assert.deepEqual(model.base[1].rows.map(v=>v.name),['OPENAI_API_KEY','AGENTSRELAY_CLAUDE_KEY']);
 assert.deepEqual(model.third.map(v=>v.name),['OTHER_API_KEY']);
});
test('retained key subject guards refuse a changed page before send and discard old-page receipts',async()=>{
 const code=read('web/public/js/keys.js').toString().match(/async function run\([^\n]*\) \{[\s\S]*?\n\}/)[0];
 for(const phase of ['uv','receipt']){
  let release,calls=0,rearmed=0;
  const pending=new Promise(r=>{release=r;});
  const ctx={S:{id:'original-page',n:'original-nonce'},seq:1,busy:false,pkErr:null,chs:{clear(){}},draw(){},rearm(){rearmed++;},webauthnErr:()=>({}),api:{keysOp:async()=>{calls++;return phase==='receipt'?pending:{ok:true};}}};
  vm.runInNewContext(code+';this.start=run',ctx);
  const result=ctx.start('copy','OPENAI_API_KEY',phase==='uv'?pending:Promise.resolve('signed-original'));
  await new Promise(r=>setImmediate(r));ctx.S={id:'different-page',n:'different-nonce'};ctx.seq++;
  release(phase==='uv'?'signed-original':{ok:true,value:'synthetic-only',n:'3'.repeat(32)});
  assert.equal((await result).why,'stale_page');assert.equal(calls,phase==='uv'?0:1);assert.equal(rearmed,0);
 }
});
test('Kimi authorization retains the exact official-host allowlist without the deferred wording',async()=>{
 const {authOf}=await import('../public/js/authcard.js');
 const auth=host=>({kind:'kimi',url:'https://'+host+'/authorize',code:'',expires_at:1,round:1,rounds:1});
 assert.ok(authOf(auth('auth.kimi.com')));
 for(const host of ['unapproved.kimi.com','kimi.com.example.invalid','example.invalid'])assert.equal(authOf(auth(host)),null);
});
