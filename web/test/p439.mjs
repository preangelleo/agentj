// P439's two-action phone journey under the current device RP and fresh-UV contract.
import assert from 'node:assert/strict';
import {generateKeyPairSync,randomBytes} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,rect,navigate,waitState,waitFor} from './browser.mjs';
import {keysHandler} from './p352-fixture.mjs';

const web = await startWebServer();
const origin = web.url.replace('127.0.0.1','localhost');
const fake = await startFakeHost();
const browser = await launch();
try {
  const p = await newPage(browser,390,844,'light',{allow:[origin,fake.relay+'/']});
  const {privateKey,publicKey} = generateKeyPairSync('ec',{namedCurve:'P-256'});
  const id = randomBytes(16);
  const approved = [];
  keysHandler(fake.st,{fa:id.toString('base64url')});
  const handler = fake.st.onApp;
  fake.st.onApp = async (client,msg,send) => {
    if(msg.t==='keys_op') approved.push({msg,device:client.devId});
    return handler(client,msg,send);
  };
  await p.send('WebAuthn.enable',{enableUI:false});
  const {authenticatorId} = await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',
    hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
  await p.send('WebAuthn.addCredential',{authenticatorId,credential:{credentialId:id.toString('base64'),isResidentCredential:true,rpId:'localhost',
    privateKey:privateKey.export({type:'pkcs8',format:'der'}).toString('base64'),userHandle:randomBytes(16).toString('base64'),signCount:0}});
  await navigate(p,fake.newPairing(origin+'?lang=zh')); await waitState(p,'awaiting-approval');
  await fake.approve(); await waitState(p,'ready');
  await evaluate(p,"document.getElementById('phone-env-dismiss')?.click();document.getElementById('setBtn').click()");
  async function click(selector) {
    const element = 'document.querySelector('+JSON.stringify(selector)+')';
    await waitFor(p,element+' && !'+element+'.disabled');
    await evaluate(p,element+".scrollIntoView({block:'center'})");
    const {x,y}=await rect(p,selector);
    for(const type of ['mousePressed','mouseReleased']) await p.send('Input.dispatchMouseEvent',{type,x,y,button:'left',clickCount:1});
  }
  await click('#open-models');
  await click('.krow[data-name="OPENAI_API_KEY"] .kbtn--copy');
  await waitFor(p,"document.getElementById('toast').textContent.includes('已复制')");
  await click('.krow[data-name="ELEVENLABS_API_KEY"] .kbtn--delete');
  await click('#confirm-no'); assert.equal(approved.length,1);
  await click('.krow[data-name="ELEVENLABS_API_KEY"] .kbtn--delete');
  await click('#confirm-yes'); await waitFor(p,"document.getElementById('toast').textContent.includes('已删除')");
  assert.deepEqual(approved.map(x=>x.msg.op),['copy','delete']);
  const host=fileURLToPath(new URL('../../host/',import.meta.url));
  const verify=spawnSync(process.env.AJ_PYTHON||'python3',['-c',`
import json,sys
from agentj import keys,passkey
data=json.load(sys.stdin)
record=data['record']
challenges=[]
for event in data['approved']:
    msg=event['msg']
    challenge=passkey.elevate_challenge(data['channel'],event['device'],msg['id'],'key_'+msg['op'],msg['n'],keys.digest(msg['op'],msg['name']))
    assert msg['fa']['id']==record['id']
    record['sc']=passkey.verify_assertion(record,passkey.approval_fields(msg['fa']),challenge)
    challenges.append(challenge)
assert challenges[0]!=challenges[1]
`],{cwd:host,env:{...process.env,PYTHONPATH:host,AGENTJ_TEST_PASSKEY_ORIGIN:'loopback'},encoding:'utf8',
    input:JSON.stringify({channel:fake.channel,approved,record:{id:id.toString('base64url'),rp:'localhost',alg:-7,
      spki:publicKey.export({format:'der',type:'spki'}).toString('base64url')}})});
  assert.equal(verify.status,0,verify.stderr);
  console.log('PASS paired 390px Chromium, cancel, delete and copy, two independently host-verified assertions');
} finally { await browser.close(); await fake.stop(); await web.stop(); }
