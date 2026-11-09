// Settings button → Noise → candidate Host AuthRescue → native WebAuthn → Host verifier.
// The account list and official login subprocess use synthetic fixtures; no live accounts.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,sleep,shoot} from './browser.mjs';

const out=process.env.P0300_WEB_OUT||'reports/qa/0300/web';mkdirSync(out,{recursive:true});
const checks=[],traces=[];
const check=(v,name)=>{checks.push({name,pass:!!v});assert.ok(v,name);};
const py=spawn(process.env.P0300_PYTHON||'python3',[fileURLToPath(new URL('./p0300-account-host.py',import.meta.url))],{env:process.env,stdio:['pipe','pipe','pipe']});
let stderr='',pending=[];py.stderr.on('data',b=>stderr+=b);
createInterface({input:py.stdout}).on('line',s=>{if(s.startsWith('0300RPC:'))pending.shift()?.(JSON.parse(s.slice(8)));});
async function rpc(req){let timer;try{const r=await Promise.race([new Promise(resolve=>{pending.push(resolve);py.stdin.write(JSON.stringify(req)+'\n');}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Host RPC timeout: '+stderr.slice(-1000))),15000);})]);assert.ok(r.ok,(r.error||'')+' '+(r.trace||''));return r;}finally{clearTimeout(timer);}}
const web=await startWebServer(process.env.P0300_WEB_PUBLIC?{publicDir:process.env.P0300_WEB_PUBLIC}:{}),fake=await startFakeHost(),B=await launch();
const url=web.url.replace('127.0.0.1','localhost');let serial=0;
async function tap(p,selector){const r=await evaluate(p,`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',...r,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',...r,button:'left',clickCount:1});}
try{
 for(const width of [390,1440])for(const theme of ['light','dark'])for(const scenario of ['allow','no-key',...(width===390&&theme==='dark'?['denied','forged','expired','lost-key']:[])]){
  fake.reset();const p=await newPage(B,width,900,theme,{allow:[url,fake.relay+'/']});
  const row={width,theme,scenario,frames:[],executions:0};traces.push(row);let latest,answerCount=0,forged=false,poll,polling;
  const forward=async r=>{latest=r;row.executions=r.executions;row.audits=r.audits;for(const frame of r.frames){row.frames.push({t:frame.t,tool:frame.tool,ok:frame.ok,why:frame.why,passkey_required:frame.passkey_required,has_fa:!!frame.fa});await fake.send(frame);}};
  try{
   await navigate(p,fake.newPairing(url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   const keys=await evaluate(p,`(async()=>{const s=await import('./js/store.js'),w=await import('./proto/wire.js');return {pub:w.b64u((await s.deviceKey()).pub),sk:w.b64u((await s.signKey()).pub)}})()`);
   const setup=await rpc({op:'setup',serial:++serial,...keys,device:fake.devs.at(-1).id,channel:fake.channel,has_key:scenario!=='no-key',expire:scenario==='expired'});
   await p.send('WebAuthn.enable',{enableUI:false});const{authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
   if(!['no-key','lost-key'].includes(scenario))await p.send('WebAuthn.addCredential',{authenticatorId,credential:setup.credential});
   await fake.send(setup.pk_state);
   fake.st.onApp=async(c,m)=>{
    if(!['acct_get','auth_get','auth_start','answer','pk_peer_req','pk_offer_req'].includes(m.t))return false;
    row.frames.push({t:m.t,kind:m.kind,route:m.route,has_account:!!m.account,ok:m.ok,has_fa:!!m.fa});
    if(m.t==='answer'){
     answerCount++;
     if(scenario==='forged'&&!forged){forged=true;await forward(await rpc({op:'frame',frame:{...m,sig:'A'.repeat(86)}}));check(latest.executions===0&&latest.pending===1,'forged signed answer does not execute or resolve login');}
    }
    await forward(await rpc({op:'frame',frame:m}));return true;
   };
   poll=setInterval(()=>{if(!polling)polling=rpc({op:'drain'}).then(forward).catch(e=>{row.pollError=String(e);}).finally(()=>{polling=null;});},100);
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();window.__getCount=0;const get=navigator.credentials.get.bind(navigator.credentials);navigator.credentials.get=o=>{window.__getCount++;return get(${scenario==='lost-key'?"{...o,publicKey:{...o.publicKey,timeout:250}}":"o"});};document.getElementById('setBtn').click();document.getElementById('set-accts-nav').click();`);
   await waitFor(p,`!!document.querySelector('#acct-list [data-kind=codex] .acctadd')`);
   await tap(p,'#acct-list [data-kind=codex] .acctadd');
   await tap(p,'#acct-list [data-kind=codex] .acctform button');
   await waitFor(p,`document.body.dataset.sheet==='1'&&document.getElementById('sheet').dataset.approvalV==='2'`);
   check(row.frames.some(f=>f.t==='auth_start'&&f.kind==='codex'&&f.route==='device'&&f.has_account),'real account button sent auth_start');
   check(row.frames.some(f=>f.t==='ask'&&f.tool==='AuthRescue'&&f.passkey_required),'candidate Host emitted listed UV request');
   const geometry=await evaluate(p,`(()=>{const n=document.getElementById('sheet'),r=n.getBoundingClientRect(),s=getComputedStyle(n);return{system:n.dataset.popupSystem,bg:s.backgroundColor,ink:s.color,center:r.y+r.height/2,centerX:r.x+r.width/2,left:r.left,right:r.right,width:r.width,top:r.top,bottom:r.bottom,visible:!!n.getClientRects().length,settings:!document.getElementById('settings').hidden}})()`);
   row.geometry=geometry;await sleep(100);writeFileSync(`${out}/${width}-${theme}-${scenario}-card.png`,await shoot(p));
   check(geometry.settings&&geometry.visible,'login approval stays above original Settings page');
   check(geometry.system==='center'&&Math.abs(geometry.center-450)<2,'Settings login approval uses viewport-centered B dialog');
   check(geometry.bg==='rgb(44, 43, 38)'&&geometry.ink==='rgb(250, 249, 245)','Settings login approval uses Noir and Lotion');
   check(geometry.width<=480&&geometry.top>=16&&geometry.bottom<=884,'Settings login approval preserves B width and viewport edges');
   check(Math.abs(geometry.centerX-width/2)<2&&geometry.left>=16&&geometry.right<=width-16,'Settings login approval is horizontally centered without clipping');
   if(scenario==='no-key'){
    check(await evaluate(p,`!document.getElementById('apprGo').hidden&&document.getElementById('apprGo').textContent.includes('在本机设置 Touch ID')`),'no-key listed card offers explicit local Touch ID setup');
    check(await evaluate(p,`document.getElementById('apprAllow').hidden&&window.__getCount===0`),'no-key does not start a misleading browser credential ceremony');
    check(await evaluate(p,`!document.getElementById('apprPk').textContent.includes('已设置的遥控器')&&document.getElementById('apprPk').textContent.includes('再重新发起登录')`),'settings-only card never promises approval on another remote');
    check(await evaluate(p,`(()=>{const n=document.getElementById('apprGo'),r=n.getBoundingClientRect(),p=document.getElementById('apprBtns').getBoundingClientRect();return n.scrollWidth<=n.clientWidth+1&&r.width>=p.width-1})()`),'entire local Touch ID setup label occupies the full no-key action row');
    await tap(p,'#apprGo');await waitFor(p,`(document.getElementById('pkp')&&!document.getElementById('pkp').hidden)||!document.getElementById('confirm').hidden`);
    check(row.frames.some(f=>['pk_peer_req','pk_offer_req'].includes(f.t)),'local setup action reaches real Host enrollment from the Settings subpage');
    check(latest.executions===0,'no-key enrollment path cannot execute pending login');
    writeFileSync(`${out}/${width}-${theme}-${scenario}-setup.png`,await shoot(p));
   }else if(scenario==='expired'){
    await sleep(2200);await forward(await rpc({op:'drain'}));await waitFor(p,`document.getElementById('acct-msg').textContent.includes('已过期')`);
    check(latest.executions===0,'expired listed request never executes login');
    check(await evaluate(p,`!document.querySelector('#acct-msg .acct-pk-setup')&&!document.querySelector('#acct-list [data-kind=codex] .acctadd').disabled`),'expired verification honestly offers another account attempt, not a key reset');
    writeFileSync(`${out}/${width}-${theme}-${scenario}-result.png`,await shoot(p));
   }else if(scenario==='denied'){
    await tap(p,'#apprDeny');await waitFor(p,`document.getElementById('acct-msg').textContent.includes('已拒绝')`);
    check(latest.executions===0&&answerCount===1,'refusal is signed and never executes login');
    check(await evaluate(p,`window.__getCount===0`),'refusal does not ask for biometrics');
    writeFileSync(`${out}/${width}-${theme}-${scenario}-result.png`,await shoot(p));
   }else if(scenario==='lost-key'){
    await tap(p,'#apprAllow');await waitFor(p,`!!document.querySelector('#apprPk .pkassist__setup')`,5000);
    check(await evaluate(p,`window.__getCount===1`),'lost-key case attempted real native WebAuthn');
    check(latest.executions===0&&answerCount===0,'lost native key cannot send an approval or execute login');
    writeFileSync(`${out}/${width}-${theme}-${scenario}-failed.png`,await shoot(p));
    await tap(p,'#apprPk .pkassist__setup');await sleep(200);
    check(await evaluate(p,`(document.getElementById('pkp')&&!document.getElementById('pkp').hidden)||!document.getElementById('confirm').hidden||document.getElementById('set-pk-row').classList.contains('set-found')`),'lost-key setup action opens real setup flow');
   }else{
    await tap(p,'#apprAllow');await waitFor(p,`!!document.querySelector('#acct-flow .arcode')`);
    check(latest.executions===1&&answerCount===1,'fresh signed native UV executes official login exactly once');
    check(await evaluate(p,`window.__getCount===1`),'local approval invokes native WebAuthn exactly once');
    check(latest.audits.includes('ok'),'candidate Host validates signed approval audit');
    writeFileSync(`${out}/${width}-${theme}-${scenario}-login.png`,await shoot(p));
   }
   check(!p.problems.some(s=>s.startsWith('exception:')),'no uncaught browser exception');
  }finally{clearInterval(poll);if(polling)await polling;await p.dispose();fake.st.onApp=null;}
 }
 console.log(`PASS 0.30.0 account UV: ${checks.length}/${checks.length} checks, ${traces.length} real Host/Noise/browser scenarios`);
}finally{
 try{await rpc({op:'stop'});}catch{}py.kill();await B.close();await fake.stop();await web.stop();
 writeFileSync(out+'/validation.json',JSON.stringify({checks,traces,stderr},null,2)+'\n');
}
