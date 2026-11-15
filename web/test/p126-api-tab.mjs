// Real paired Noise UI -> immutable Host candidate -> Controls/ApiAdmin/Host.ask -> virtualWebAuthn.
// Only isolated profile metadata/local published state are fixtures. No real cloud/model/APIcaller.
import assert from 'node:assert/strict';
import {spawn}from'node:child_process';import{createInterface}from'node:readline';import{fileURLToPath}from'node:url';import{mkdirSync,writeFileSync}from'node:fs';
import{startWebServer}from'./serve.mjs';import{startFakeHost}from'./fakehost.mjs';import{launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep}from'./browser.mjs';
const out=process.env.P126_OUT||process.env.OLARES_RUN_OUT||'reports/qa/p126-api-tab';mkdirSync(out,{recursive:true});let checks=0,serial=0,pending=[];const visualOnly=process.env.P126_VISUAL_ONLY==='1';const traces=[];
const py=spawn(process.env.P126_PYTHON||'python3',[fileURLToPath(new URL('./p126-host-bridge.py',import.meta.url))],{env:process.env,stdio:['pipe','pipe','pipe']});let stderr='';py.stderr.on('data',b=>stderr+=b);
createInterface({input:py.stdout}).on('line',s=>{if(s.startsWith('P126RPC:'))pending.shift()?.(JSON.parse(s.slice(8)));});
async function rpc(v){let timer;try{const r=await Promise.race([new Promise(resolve=>{pending.push(resolve);py.stdin.write(JSON.stringify(v)+'\n');}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Host RPC timeout '+stderr.slice(-800))),15000);})]);assert.ok(r.ok,(r.error||'')+' '+(r.trace||''));return r;}finally{clearTimeout(timer);}}
const check=(v,n)=>{assert.ok(v,n);checks++;};const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),url=web.url.replace('127.0.0.1','localhost');
async function tap(p,selector){const r=await evaluate(p,`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',...r,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',...r,button:'left',clickCount:1});}
try{
 for(const width of [390,1440])for(const theme of ['light','dark'])for(const published of [false,true])for(const lang of (visualOnly?['zh','en']:['en'])){
  fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme}};const p=await newPage(B,width,900,theme,{allow:[url,fake.relay+'/']});let latest,poll,polling,writeFrame,negativeDone=false;const trace={width,theme,published,lang,frames:[],negative:[]};traces.push(trace);
  const forward=async r=>{latest=r;for(const frame of r.frames){trace.frames.push({t:frame.t,tool:frame.tool,ok:frame.ok,why:frame.why,passkey_required:frame.passkey_required});await fake.send(frame);}};
  try{
   await navigate(p,fake.newPairing(url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   const keys=await evaluate(p,`(async()=>{const s=await import('./js/store.js'),w=await import('./proto/wire.js');return {pub:w.b64u((await s.deviceKey()).pub),sk:w.b64u((await s.signKey()).pub)}})()`);
   const setup=await rpc({op:'setup',serial:++serial,...keys,device:fake.devs.at(-1).id,channel:fake.channel,published});
   await p.send('WebAuthn.enable',{enableUI:false});const{authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
   await p.send('WebAuthn.addCredential',{authenticatorId,credential:setup.credential});await fake.send(setup.pk_state);
   fake.st.onApp=async(c,m)=>{
    if(!['bots_read','bots_write','answer','pk_peer_req','pk_offer_req'].includes(m.t))return false;
    if(m.t==='bots_write'){
     writeFrame=m;
     const bad=await rpc({op:'frame',frame:{...m,r:'forged-source',request:{...m.request,enabled:!m.request.enabled},sig:'A'.repeat(86)}});
     check(bad.enabled===published&&bad.pending===0,'forged/APIcaller frame cannot mutate or openUV');trace.negative.push('forged-frame');
    }
    if(m.t==='answer'&&m.ok&&!negativeDone){negativeDone=true;
     for(const bad of [{...m,sig:'A'.repeat(86)},Object.fromEntries(Object.entries(m).filter(([k])=>k!=='fa'))]){
      const r=await rpc({op:'frame',frame:bad});check(r.pending===1&&r.enabled===published&&r.keys===0,'bad signature/missing actual UV never mutates');
     }trace.negative.push('bad-answer-signature','missing-WebAuthn');
    }
    await forward(await rpc({op:'frame',frame:m}));return true;
   };
   poll=setInterval(()=>{if(!polling)polling=rpc({op:'drain'}).then(forward).finally(()=>{polling=null;});},100);
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/bots.js').then(m=>m.open())`);
   await waitFor(p,`[...document.querySelectorAll('#bots-side button')].some(b=>b.textContent.includes('Synthetic API bot'))`);
   await evaluate(p,`[...document.querySelectorAll('#bots-side button')].find(b=>b.textContent.includes('Synthetic API bot')).click()`);
   await waitFor(p,`document.querySelectorAll('.bots-tabs button').length===8`);
   await evaluate(p,`[...document.querySelectorAll('.bots-tabs button')].find(b=>b.textContent==='API').click()`);
   await waitFor(p,`document.querySelectorAll('#bots-main [data-area]').length===5`);
   assert.deepEqual(await evaluate(p,`[...document.querySelectorAll('#bots-main [data-area]')].map(x=>x.dataset.area)`),['endpoints','keys','usage','logs','docs']);checks++;
   check(await evaluate(p,`[...document.querySelectorAll('#bots-main button')].filter(b=>b.offsetHeight).every(b=>b.getBoundingClientRect().height>=44)`),'current actor actions keep44px');
   check(await evaluate(p,`document.documentElement.scrollWidth<=innerWidth+1`),'viewport fits');
   for(const area of (visualOnly?['endpoints','keys','usage','logs','docs']:['endpoints'])){await evaluate(p,`document.querySelector('#bots-main [data-area=${area}]').scrollIntoView({block:'start'})`);writeFileSync(`${out}/${width}-${theme}-${published?'published':'off'}-${lang}-${area}.png`,await shoot(p,''));}if(visualOnly){console.log('P126 visual PASS '+width+' '+theme+' '+published+' '+lang);continue;}
   if(published)await evaluate(p,`document.querySelector('#bots-main [data-area=keys] input').value='Synthetic caller';[...document.querySelectorAll('#bots-main button')].find(b=>b.textContent==='Issue a 30-day key').click()`);
   else await evaluate(p,`[...document.querySelectorAll('#bots-main button')].find(b=>b.textContent==='Turn API on').click()`);
   await waitFor(p,`document.body.dataset.sheet==='1'&&document.getElementById('sheet').dataset.approvalV==='2'`);
   check(latest.pending===1&&latest.enabled===published&&latest.keys===0,'signed button alone does not authorize management');
   check(trace.frames.some(f=>f.t==='ask'&&f.tool==='agentj api manage'&&f.passkey_required),'real ApiAdmin emitted owner-only UV ask');
   await tap(p,'#apprAllow');
   await waitFor(p,published?`document.getElementById('bots-main').textContent.includes('private card')`:`[...document.querySelectorAll('#bots-main button')].some(b=>b.textContent==='Turn API off')`,20000);
   check(published?latest.keys===1:latest.enabled===true,'real signature andWebAuthn authorize exact operation');
   check(await evaluate(p,`!document.getElementById('bots-main').textContent.match(/ajapi_|ajprov_/)`),'rawcallerkey never inAPI DOM');
   if(published)check(trace.frames.some(f=>f.t==='secret_out'),'issue uses real only-owner secretcard');
   const replay=await rpc({op:'frame',frame:writeFrame});check(replay.pending===0&&replay.keys===(published?1:0),'signed frame replay cannot repeat operation');
   check(latest.audits.every(x=>x==='ok'),'actualHost approval audit valid');
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);checks+=2;console.log('P126 case PASS '+width+' '+theme+' '+published);
  }catch(error){console.error('P126 diagnostic',JSON.stringify({pending:latest?.pending,enabled:latest?.enabled,keys:latest?.keys,frames:trace.frames,status:await evaluate(p,`document.getElementById('bots-status').textContent`),keyNotice:await evaluate(p,`[...document.querySelectorAll('#bots-main [role=status]')].map(e=>e.textContent)`)}));writeFileSync(`${out}/${width}-${theme}-${published}-FAIL.png`,await shoot(p,''));throw error;}finally{clearInterval(poll);if(polling)await polling;await p.dispose();}
 }
 writeFileSync(`${out}/results.json`,JSON.stringify({checks,traces,scope:'realHost/Controls/ApiAdmin/Host.ask/virtualUV withpairedNoise; no publicAPI service/model'},null,2));console.log('P126 API tab PASS '+checks+' checks');
}finally{await rpc({op:'stop'}).catch(()=>{});py.stdin.end();await B.close();await fake.stop();await web.stop();}
