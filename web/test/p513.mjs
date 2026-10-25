// P513: generic final-effect card copy, full parameters and real synthetic UV.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,mkdtempSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,rect,tap} from './browser.mjs';
const out=process.env.AJ_P513_DIR || mkdtempSync(join(tmpdir(),'aj-p513-cards-'));mkdirSync(out,{recursive:true});
const web=await startWebServer();web.url=web.url.replace('127.0.0.1','localhost');
const fake=await startFakeHost(),B=await launch(),checks=[];
const py=process.env.AJ_PYTHON || execFileSync('git',['rev-parse','--path-format=absolute','--git-common-dir'],{encoding:'utf8'}).trim()+'/../host/.venv/bin/python';
try {
 for(const lang of ['zh','en']) {
  fake.reset();const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await sleep(500);
   await p.send('WebAuthn.enable');
   await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
   const registration=await evaluate(p,`import('./js/faceid.js').then(f=>f.register(new Uint8Array(32).fill(1),{relayIndex:2,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32)}))`);
   const labels=await evaluate(p,`import('./js/t.js').then(({t})=>({normal:t('p336.faTitle'),yes:t('p336.faYes'),sandbox:t('ask.sandboxTitle'),sandboxYes:t('ask.sandboxOnce')}))`);
   for(const [index,tool] of ['agentj data','agentj report script','SandboxOnce'].entries()) {
    const id=String(index+1).repeat(32),summary='Destination: approved.example\nCredits: 2\n'+('x'.repeat(2100))+'\nTAIL-FINAL-ARGUMENT';
    await fake.ask({id,tool,summary,ttl:120,passkey_required:true,fa:registration.id,fa_ch:Buffer.alloc(32,5).toString('base64url')});
    await waitFor(p,`document.body.dataset.sheet==='1' && !document.getElementById('apprAllow').hidden`);
    const sandbox=tool==='SandboxOnce';
    assert.equal(await evaluate(p,`document.getElementById('sheetTitle').textContent`),sandbox?labels.sandbox:labels.normal);
    assert.equal(await evaluate(p,`document.getElementById('apprAllowLbl').textContent`),sandbox?labels.sandboxYes:labels.yes);
    assert.equal(await evaluate(p,`document.getElementById('cmd').textContent.endsWith('TAIL-FINAL-ARGUMENT')`),true);
    if(!sandbox) assert.equal(await evaluate(p,`document.getElementById('sheet').textContent.includes(${JSON.stringify(labels.sandboxYes)})`),false,'generic UV card must never claim a sandbox escape');
    writeFileSync(join(out,`${lang}-${index}.png`),await shoot(p,''));
    const before=fake.st.answers.length,button=await rect(p,'#apprAllow');await tap(p,button.x,button.y);
    await waitFor(p,`document.body.dataset.sheet!=='1'`);
    assert.equal(fake.st.answers.length,before+1);
    const answer=fake.st.answers.at(-1);assert.ok(answer.fa);assert.equal(fake.st.sigOk.at(-1),true);
    execFileSync(py,['-c',`import json,sys;sys.path.insert(0,'agentjarvis/host');from agentj import passkey
v=json.load(sys.stdin);pk=passkey.verify_registration(v['registration'],bytes([1])*32);passkey.verify_assertion(pk,passkey.approval_fields(v['fa']),bytes([5])*32)`],{input:JSON.stringify({registration,fa:answer.fa}),env:{...process.env,AGENTJ_TEST_PASSKEY_ORIGIN:new URL(web.url).origin}});
    checks.push(`${lang}: ${tool}: correct title/button, complete tail, signed real UV`);
   }
   const before=fake.st.answers.length;
   await fake.ask({id:'9'.repeat(32),tool:'agentj data',summary:'No registered passkey',ttl:120,passkey_required:true});
   await waitFor(p,`document.body.dataset.sheet==='1'`);const button=await rect(p,'#apprAllow');await tap(p,button.x,button.y);await sleep(250);
   assert.equal(fake.st.answers.length,before,'missing passkey never sends an approval');
   checks.push(`${lang}: no passkey sends nothing`);
  } finally {await p.dispose();}
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(join(out,'result.json'),JSON.stringify({ok:true,checks,scope:'Chromium + Noise + virtual authenticator; no physical biometric claim'},null,2)+'\n');
console.log('P513 final cards PASS',checks,'artifacts:',out);
if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p513.mjs',results:{'p513-final-card-copy':'pass'}}));
