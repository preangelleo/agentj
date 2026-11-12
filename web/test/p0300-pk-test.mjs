// Native WebAuthn + real Host click-time challenge; bounded expiry retry and signature denial.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';import {createInterface} from 'node:readline';
import {mkdirSync,writeFileSync} from 'node:fs';import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,sleep,shoot} from './browser.mjs';
import {pkTestResult} from '../public/js/pk-test-result.js';
const out=process.env.P0300_PK_OUT||'reports/qa/0300/web/pk-test';mkdirSync(out,{recursive:true});
const checks=[],rows=[];const check=(v,name)=>{checks.push({name,pass:!!v});assert.ok(v,name);};
check(pkTestResult({t:'pk_check_res',ok:false,why:'no_check'}).key==='p0300.pk.testExpired','no_check is an expired challenge, not an unknown credential');
check(pkTestResult({t:'pk_check_res',ok:false}).key==='p0300.pk.testIncomplete','older host failure is inconclusive');
check(pkTestResult({t:'timeout'}).key==='p566.notSent','transport timeout is not a key rejection');
for(const why of ['credential','rp','origin','signature','uv','counter','challenge'])check(pkTestResult({t:'pk_check_res',ok:false,why}).setup,'fixed verification failure '+why+' has reset path');
const py=spawn(process.env.P0300_PYTHON||'python3',[fileURLToPath(new URL('./p0300-account-host.py',import.meta.url))],{env:process.env,stdio:['pipe','pipe','pipe']});let pending=[],stderr='';py.stderr.on('data',b=>stderr+=b);createInterface({input:py.stdout}).on('line',s=>{if(s.startsWith('0300RPC:'))pending.shift()?.(JSON.parse(s.slice(8)));});
async function rpc(req){let timer;try{const r=await Promise.race([new Promise(resolve=>{pending.push(resolve);py.stdin.write(JSON.stringify(req)+'\n');}),new Promise((_,reject)=>timer=setTimeout(()=>reject(Error(stderr||'Host bridge timeout')),15000))]);assert.ok(r.ok,(r.error||'')+(r.trace||''));return r;}finally{clearTimeout(timer);}}
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),url=web.url.replace('127.0.0.1','localhost');let serial=0;
try{for(const width of [390,1440])for(const theme of ['light','dark'])for(const how of ['expired',...(width===390&&theme==='dark'?['evicted','restarted','replayed','twice','signature']:[])]){
 fake.reset();const p=await newPage(B,width,900,theme,{allow:[url,fake.relay+'/']});const row={width,theme,how,responses:[]};rows.push(row);
 try{
  await navigate(p,fake.newPairing(url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  const keys=await evaluate(p,`(async()=>{const s=await import('./js/store.js'),w=await import('./proto/wire.js');return {pub:w.b64u((await s.deviceKey()).pub),sk:w.b64u((await s.signKey()).pub)}})()`);
  const setup=await rpc({op:'setup',serial:++serial,...keys,device:fake.devs.at(-1).id,channel:fake.channel,has_key:true});
  await p.send('WebAuthn.enable',{enableUI:false});const{authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});await p.send('WebAuthn.addCredential',{authenticatorId,credential:setup.credential});
  const {challenge}=await rpc({op:'check_challenge'});await fake.send(challenge); // stale pk_state must not be used by Settings.
  let count=0,challenges=0;row.challenges=[];
  fake.st.onApp=async(c,m)=>{
   if(m.t==='pk_check_ch'){
    challenges++;
    const result=await rpc({op:'frame',frame:m});
    const issued=result.frames.find(f=>f.t==='pk_check_ch_res');
    check(issued?.r===m.r&&!!issued.n&&!!issued.fa_ch,'real Host echoes request id and issues a challenge');
    row.challenges.push(issued.n);
    if((how==='twice'&&challenges<=2)||(['expired','evicted','restarted'].includes(how)&&challenges===1))
     await rpc({op:'invalidate_check',how:how==='twice'?'expired':how});
    for(const f of result.frames)await fake.send(f);
    return true;
   }
   if(m.t!=='pk_check')return false;
   count++;
   const frame=how==='signature'?{...m,fa:{...m.fa,sig:'AA'}}:m;
   const result=await rpc({op:'frame',frame});
   for(const f of result.frames){if(f.t==='pk_check_res')row.responses.push({ok:f.ok,why:f.why});await fake.send(f);}
   return true;
  };
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.getElementById('setBtn').click();window.__gets=0;const get=navigator.credentials.get.bind(navigator.credentials);navigator.credentials.get=o=>{window.__gets++;return get(o);};`);
  await waitFor(p,`!document.getElementById('set-pk-test').hidden`);
  if(how==='replayed'){
   await evaluate(p,`document.getElementById('set-pk-test').click()`);await waitFor(p,`document.getElementById('toast').textContent.includes('正常')`);
   await fake.send(challenge);await sleep(80);
  }
  await evaluate(p,`document.getElementById('set-pk-test').click()`);
  await waitFor(p,`document.querySelector('#toast[data-pk-test="1"]')?.textContent.includes('${how==='signature'?'验证未通过':how==='twice'?'这次测试已过期':'正常'}') && window.__gets >= ${how==='replayed'?2:1}`);
  const notice=await evaluate(p,`(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect(),s=getComputedStyle(n);return {class:n.className,text:n.textContent,align:s.textAlign,border:s.borderTopColor,borderWidth:s.borderTopWidth,logo:getComputedStyle(document.documentElement).getPropertyValue('--c-shield').trim(),width:r.width,height:r.height,left:r.left,right:r.right,centerX:r.x+r.width/2,center:r.y+r.height/2,gets:window.__gets}})()`);row.notice=notice;
  const denied=how==='signature', exhausted=how==='twice', retries=['expired','evicted','restarted','twice'].includes(how);
  check(challenges===(retries?2:how==='replayed'?2:1),'fresh challenge per click, with one bounded expiry retry '+how);
  check(count===challenges&&notice.gets===count,'one native assertion per Host challenge '+how);
  check(new Set(row.challenges).size===challenges,'Host does not reuse a challenge '+how);
  check(row.responses.at(-1).why===(denied?'signature':exhausted?'no_check':undefined),'Host result matches final state '+how);
  if(retries)check(row.responses[0].why==='no_check','first challenge really rejected before retry '+how);
  if(denied)check(count===1&&!row.responses.at(-1).ok,'signature denial never triggers retry or success');
  else if(!exhausted)check(row.responses.at(-1).ok===true,'fresh Host challenge succeeds '+how);
  check(!notice.text.includes('没有认出'),'result never claims the host forgot a credential');
  check(notice.class.includes('square')&&!notice.class.split(' ').includes('rect')&&notice.height>=220&&notice.align==='center','self-test result is a centered rounded square with centered text');
  const rgb=notice.logo.replace('#','').match(/../g).map(x=>parseInt(x,16));
  check(parseFloat(notice.borderWidth)>0&&notice.border===`rgb(${rgb.join(', ')})`&&Math.abs(notice.center-450)<2,'Settings self-test has logo-green border and viewport center');
  check(Math.abs(notice.centerX-width/2)<2&&notice.left>=16&&notice.right<=width-16,'Settings self-test is horizontally centered and unclipped');
  const before=count;await sleep(200);check(count===before,'no further automatic fingerprint prompt after terminal result');
  writeFileSync(`${out}/${width}-${theme}-${how}.png`,await shoot(p));
  if(exhausted){
   await evaluate(p,`document.querySelector('#toast .nt-acts button').click()`);await waitFor(p,`document.getElementById('toast').textContent.includes('正常')`);
   check(row.responses.at(-1).ok===true&&count===before+1&&challenges===3,'explicit user retry uses a third fresh Host challenge');
  }
  await evaluate(p,`(async()=>{const u=await import('./js/ui.js');u.notify({text:'Later reply notice',shape:'rect',ms:5000})})()`);
  check(await evaluate(p,`!document.getElementById('toast').hasAttribute('data-pk-test')`),'next ordinary notice clears self-test marker');
 }finally{fake.st.onApp=null;await p.dispose();}
}console.log(`PASS 0.30.0 passkey test: ${checks.length}/${checks.length} checks, ${rows.length} real Host/native scenarios`);
}finally{try{await rpc({op:'stop'});}catch{}py.kill();await B.close();await fake.stop();await web.stop();writeFileSync(out+'/validation.json',JSON.stringify({checks,rows,stderr},null,2)+'\n');}
