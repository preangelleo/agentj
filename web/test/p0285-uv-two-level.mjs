import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const phase=process.env.UV_PHASE||'after',out=process.env.UV_OUT||`reports/qa/p0285-uv-two-level/${phase}`;
mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.UV_PUBLIC?{publicDir:process.env.UV_PUBLIC}:{}),fake=await startFakeHost(),browser=await launch(),rows=[];
const id=Buffer.alloc(16,17).toString('base64url'),challenge=Buffer.alloc(32,19).toString('base64url');
try{for(const width of (process.env.UV_WIDTHS?process.env.UV_WIDTHS.split(',').map(Number):[390,768,1440]))for(const lang of ['zh','en'])for(const theme of ['dark','light']){
 fake.reset();fake.st.prefs={appearance:{language:lang}};
 const ua=width===390?'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1':width===768?'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36':'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36';
 const p=await newPage(browser,width,1000,theme,{ua,touch:width!==1440,allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.fonts.ready.then(()=>{document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);})`);await sleep(2200);
  for(const scene of ['chat','settings']){
   if(scene==='settings')await evaluate(p,`import('./js/settings.js').then(m=>m.open())`);
   writeFileSync(`${out}/${width}-${lang}-${theme}-${scene}.png`,await shoot(p));
  }
  await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);
  await evaluate(p,`import('./js/pkpeer.js').then(m=>{window.__uvAssertions=0;window.__uvAnswers=[];m.configure({canAssert:()=>true,approve:()=>{window.__uvAssertions++;return Promise.resolve({synthetic:true});},send:async o=>{window.__uvAnswers.push({type:o.t,ok:o.ok,idMatches:o.id===${JSON.stringify(id)},proofMatches:o.fa?.synthetic===true});}});})`);
  // New Host eligibility: a bound restored key arrives as an ordinary eligible ask.
  await fake.send({t:'pk_peer_ask',id,code:'123456',fa_ch:challenge,fa:'synthetic-bound-restored-key',name:'新遥控器',at:Math.floor(Date.now()/1000),exp:Math.floor(Date.now()/1000)+300});
  await waitFor(p,`!!document.getElementById('pkp-approve')&&!document.getElementById('pkp-approve').hidden`);
  const ask=await evaluate(p,`(()=>{const b=document.getElementById('pkp-approve'),d=document.getElementById('pkp-deny'),r=b.getBoundingClientRect(),box=b.closest('.box').getBoundingClientRect();return{body:document.getElementById('pkp').textContent,title:document.getElementById('pkp-title').textContent,label:b.textContent,deny:!d.hidden,code:document.getElementById('pkp-code').textContent.includes('123456'),inside:r.x>=box.x&&r.right<=box.right&&r.bottom<=box.bottom,target:r.width>=44&&r.height>=44,thirdTier:document.getElementById('pkp').textContent.includes('只能本机验证')||document.getElementById('pkp').textContent.includes('升级为可批准')};})()`);
  assert.ok(ask.deny&&ask.code&&ask.inside&&ask.target&&!ask.thirdTier);
  if(phase==='after'){assert.match(ask.body,lang==='zh'?/保存自己的验证密钥/:/save its own verification key/);assert.doesNotMatch(ask.body,/\/ 指纹|\/ fingerprint/);assert.match(ask.title,lang==='zh'?/授权设置/:/Authorize/);assert.match(ask.label,lang==='zh'?/^用\S/:/^Approve with /);if(width===390)assert.match(ask.label,/Face ID \/ Touch ID/,'explicit iOS label must survive generic biometric substitution');if(width===768)assert.match(ask.label,lang==='zh'?/指纹或面容/:/fingerprint or face/);}
  writeFileSync(`${out}/${width}-${lang}-${theme}-approver.png`,await shoot(p));
  await evaluate(p,`document.getElementById('pkp-approve').click()`);await waitFor(p,'window.__uvAnswers.length===1');
  const answer=await evaluate(p,`({count:window.__uvAssertions,answers:window.__uvAnswers})`);assert.deepEqual(answer,{count:1,answers:[{type:'pk_peer_answer',ok:true,idMatches:true,proofMatches:true}]});
  // A no-key incoming card may open Settings, but must never start another request.
  await evaluate(p,`import('./js/pkpeer.js').then(m=>{m._reset();window.__uvRequests=0;m.configure({supported:async()=>true,ask:async()=>{window.__uvRequests++;return{t:'timeout'};}});})`);
  await fake.send({t:'pk_peer_ask',id,code:'123456',fa_ch:challenge,fa:'',needs_setup:true,name:'新遥控器',exp:Math.floor(Date.now()/1000)+300});
  await waitFor(p,`!!document.getElementById('pkp-setup-first')`);
  if(phase==='after'){await evaluate(p,`document.getElementById('pkp-setup-first').click()`);await sleep(900);assert.equal(await evaluate(p,'window.__uvRequests'),0);assert.equal(await evaluate(p,`document.getElementById('settings').hidden`),false);}
  // Requester only waits for the other verified remote and displays the same code.
  await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);
  await evaluate(p,`import('./js/pkpeer.js').then(async m=>{m._reset();m.configure({supported:async()=>true,ask:async()=>({t:'pk_peer_res',ok:true,mode:'peer',id:${JSON.stringify(id)},code:'123456',exp:Math.floor(Date.now()/1000)+300,online:1}),view:null});await m.startPeerSetup();})`);
  await waitFor(p,`document.getElementById('pkp')?.dataset.mode==='wait'`);
  assert.equal(await evaluate(p,`!!document.querySelector('#pkp #pkp-approve')`),false);
  const waiting=await evaluate(p,`(()=>{const n=document.getElementById('pkp'),c=getComputedStyle(n.querySelector('.box'));return{body:n.textContent,outline:parseFloat(c.outlineWidth),context:n.dataset.popupContext};})()`);
  if(phase==='after'){assert.doesNotMatch(waiting.body,/\/ 指纹|\/ fingerprint|Touch ID、Face ID/);assert.equal(waiting.outline,0,'chat UV wait has no decorative green outline');}
  writeFileSync(`${out}/${width}-${lang}-${theme}-requester.png`,await shoot(p));
  await evaluate(p,`import('./js/pkpeer.js').then(async m=>{m.configure({regOpts:async()=>({syntheticFixture:true})});m.onDone({id:${JSON.stringify(id)},ok:true,by:'Synthetic approver'});await m.onOffer({n:${JSON.stringify(challenge)},peer:${JSON.stringify(id)}});})`);
  await waitFor(p,`document.getElementById('pkp')?.dataset.mode==='approved'`);
  const approved=await evaluate(p,`({body:document.getElementById('pkp').textContent,save:!!document.getElementById('pkp-save'),outline:parseFloat(getComputedStyle(document.querySelector('#pkp>.box')).outlineWidth)})`);
  if(phase==='after'){assert.doesNotMatch(approved.body,/\/ 指纹|\/ fingerprint/);assert.ok(approved.save);assert.equal(approved.outline,0);}
  writeFileSync(`${out}/${width}-${lang}-${theme}-approved.png`,await shoot(p));

  rows.push({width,lang,theme,...ask,assertions:1,answers:1,noKeyStartsRequest:false});console.log('PASS UV',phase,width,lang,theme);
 }finally{await p.dispose();}
}}finally{writeFileSync(`${out}/results.json`,JSON.stringify({phase,fixture:'isolated Chromium + Noise; synthetic assertion only, no physical enrollment or production state',rows},null,2));await browser.close();await fake.stop();await web.stop();}
