// Native WebAuthn + candidate Host challenge expiry; only the final Settings notice is under test.
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
try{for(const width of [390,1440])for(const theme of ['light','dark'])for(const how of ['expired',...(width===390&&theme==='dark'?['evicted','restarted','replayed','signature']:[])]){
 fake.reset();const p=await newPage(B,width,900,theme,{allow:[url,fake.relay+'/']});const row={width,theme,how,responses:[]};rows.push(row);
 try{
  await navigate(p,fake.newPairing(url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  const keys=await evaluate(p,`(async()=>{const s=await import('./js/store.js'),w=await import('./proto/wire.js');return {pub:w.b64u((await s.deviceKey()).pub),sk:w.b64u((await s.signKey()).pub)}})()`);
  const setup=await rpc({op:'setup',serial:++serial,...keys,device:fake.devs.at(-1).id,channel:fake.channel,has_key:true});
  await p.send('WebAuthn.enable',{enableUI:false});const{authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});await p.send('WebAuthn.addCredential',{authenticatorId,credential:setup.credential});
  const {challenge}=await rpc({op:'check_challenge'});await fake.send(challenge);
  let count=0;fake.st.onApp=async(c,m)=>{if(m.t!=='pk_check')return false;count++;const frame=how==='signature'&&count===1?{...m,fa:{...m.fa,sig:'AA'}}:m;const result=await rpc({op:'frame',frame});for(const f of result.frames){if(f.t==='pk_check_res')row.responses.push({ok:f.ok,why:f.why});await fake.send(f);}return true;};
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.getElementById('setBtn').click();window.__gets=0;const get=navigator.credentials.get.bind(navigator.credentials);navigator.credentials.get=o=>{window.__gets++;return get(o);};`);
  await waitFor(p,`!document.getElementById('set-pk-test').hidden`);
  if(how==='replayed'){
   await evaluate(p,`document.getElementById('set-pk-test').click()`);await waitFor(p,`document.getElementById('toast').textContent.includes('正常')`);
   await fake.send(challenge);await sleep(80);
  }else if(how!=='signature')await rpc({op:'invalidate_check',how});
  await evaluate(p,`document.getElementById('set-pk-test').click()`);
  await waitFor(p,`document.querySelector('#toast[data-pk-test="1"]')?.textContent.includes('${how==='signature'?'验证未通过':'这次测试已过期'}')`);
  const notice=await evaluate(p,`(()=>{const n=document.getElementById('toast'),r=n.getBoundingClientRect(),s=getComputedStyle(n);return {class:n.className,text:n.textContent,align:s.textAlign,border:s.borderTopColor,borderWidth:s.borderTopWidth,logo:getComputedStyle(document.documentElement).getPropertyValue('--c-shield').trim(),width:r.width,height:r.height,left:r.left,right:r.right,centerX:r.x+r.width/2,center:r.y+r.height/2,gets:window.__gets}})()`);row.notice=notice;
  check(row.responses.at(-1).why===(how==='signature'?'signature':'no_check'),'candidate Host returns real fixed failure '+how);
  check(!notice.text.includes('没有认出'),'failure never claims the host forgot a credential');
  check(notice.class.includes('square')&&!notice.class.split(' ').includes('rect')&&notice.height>=220&&notice.align==='center','self-test result is a centered rounded square with centered text');
  const rgb=notice.logo.replace('#','').match(/../g).map(x=>parseInt(x,16));
  check(parseFloat(notice.borderWidth)>0&&notice.border===`rgb(${rgb.join(', ')})`&&Math.abs(notice.center-450)<2,'Settings self-test has logo-green border and viewport center');
  check(Math.abs(notice.centerX-width/2)<2&&notice.left>=16&&notice.right<=width-16,'Settings self-test is horizontally centered and unclipped');
  const before=count;await sleep(200);check(count===before,'fresh challenge never causes automatic second fingerprint prompt');
  writeFileSync(`${out}/${width}-${theme}-${how}.png`,await shoot(p));
  if(how!=='signature'){
   await evaluate(p,`document.querySelector('#toast .nt-acts button').click()`);await waitFor(p,`document.getElementById('toast').textContent.includes('正常')`);
   check(row.responses.at(-1).ok===true&&count===before+1,'explicit user retry verifies fresh Host challenge');
   check(await evaluate(p,`document.getElementById('toast').classList.contains('circle')||document.getElementById('toast').classList.contains('square')`),'short success uses the existing round-fit rule');
  }
  await evaluate(p,`(async()=>{const u=await import('./js/ui.js');u.notify({text:'Later reply notice',shape:'rect',ms:5000})})()`);
  check(await evaluate(p,`!document.getElementById('toast').hasAttribute('data-pk-test')`),'next ordinary notice clears self-test marker');
 }finally{fake.st.onApp=null;await p.dispose();}
}console.log(`PASS 0.30.0 passkey test: ${checks.length}/${checks.length} checks, ${rows.length} real Host/native scenarios`);
}finally{try{await rpc({op:'stop'});}catch{}py.kill();await B.close();await fake.stop();await web.stop();writeFileSync(out+'/validation.json',JSON.stringify({checks,rows,stderr},null,2)+'\n');}
