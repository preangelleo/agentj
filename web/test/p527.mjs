// P527: real Chromium + encrypted fake host, no upstream or real credentials.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync,randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {keysMsg} from './p352-fixture.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate as ev,shoot,sleep,key} from './browser.mjs';
const out=process.env.P527_OUT||'/tmp/p527-shots';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
let checks=0,shots=0;
const card=name=>({t:'elev',kind:'secret',name,purpose:name+' API Key',dest:'Host key file',verify:'',id:randomBytes(16).toString('hex'),n:randomBytes(16).toString('hex'),epk:generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x,ttl:120,passkey_required:true});
const click=(p,sel)=>ev(p,`document.querySelector(${JSON.stringify(sel)}).click()`);
try{
for(const width of [1440,390]){
 fake.reset();fake.st.prefs={appearance:{language:'zh',theme:'dark'},voice:{speak_replies:false,wake_enabled:false},jev:{mode:'typesafe'}};fake.st.jevReady=false;let fail=false;const requests=[];
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='keys_get'){const v=keysMsg();v.base.push({id:'jev',name:{zh:'Jev · 安全审核',en:'Jev · Safety review'},rows:[{type:'key',name:'TYPESAFE_API_KEY',set:false},{type:'key',name:'OPENROUTER_API_KEY',set:false}]});await send({...v,r:m.r});return true;}
  if(m.t==='keys_set'){requests.push(m.name);await send({t:'keys_res',r:m.r,ok:true,...(fail?{}:{card:card(m.name)})});return true;}
  return false;
 };
 const p=await newPage(B,width,900,'dark',{touch:false,allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await click(p,'#setBtn');await click(p,'#open-models');await waitFor(p,`document.querySelector('.krow[data-name="TYPESAFE_API_KEY"]')`);
 assert.match(await ev(p,`document.querySelector('.krow[data-name="TYPESAFE_API_KEY"] .kname').textContent`),/TypeSafe 官方/);checks++;
 assert.match(await ev(p,`document.querySelector('.krow[data-name="OPENROUTER_API_KEY"] .kname').textContent`),/OpenRouter/);checks++;
 await ev(p,`document.querySelector('.krow[data-name="TYPESAFE_API_KEY"]').scrollIntoView({block:'center'})`);await sleep(150);
 writeFileSync(`${out}/${++shots}-keys-${width}.png`,await shoot(p,''));
 for(const name of ['ANTHROPIC_API_KEY','OPENAI_API_KEY','DASHSCOPE_API_KEY','DEEPSEEK_API_KEY','TYPESAFE_API_KEY','OPENROUTER_API_KEY','ELEVENLABS_API_KEY']){
  await click(p,`.krow[data-name="${name}"] .kbtn--${['ANTHROPIC_API_KEY','OPENAI_API_KEY','ELEVENLABS_API_KEY'].includes(name)?'edit':'set'}`);
  await waitFor(p,`document.getElementById('elev')?.hidden===false`);
  assert.equal(await ev(p,`document.body.dataset.view`),'models');
  assert.equal(await ev(p,`document.getElementById('elev-title').textContent.includes('${name}')`),true);checks+=2;
  if(name==='TYPESAFE_API_KEY')writeFileSync(`${out}/${++shots}-key-card-${width}.png`,await shoot(p,''));
  await click(p,'#elev-hide');
 }
 const before=requests.length;
 await click(p,'.krow[data-name="TYPESAFE_API_KEY"] .kbtn--set');
 await waitFor(p,`document.getElementById('elev').hidden===false`);
 assert.equal(requests.length,before);checks++;
 await click(p,'#elev-hide');
 fail=true;await click(p,'.krow[data-name="WOLFRAM_APP_TOKEN"] .kbtn--edit');await sleep(200);
 assert.equal(await ev(p,`document.getElementById('elev').hidden`),true);checks++;
 assert.ok(requests.length>=8);checks++;
 await sleep(4500);
 await key(p,'Escape');
 await ev(p,`import('./js/elevate.js').then(m=>m.clear())`);
 await ev(p,`import('./js/settings.js').then(m=>m.open())`);
 await waitFor(p,`!document.getElementById('settings').hidden`);
 await ev(p,`document.getElementById('set-jev-mode-row').scrollIntoView({block:'center'})`);await sleep(200);
 assert.equal(await ev(p,`document.getElementById('set-jev-key').hidden`),false);checks++;
 writeFileSync(`${out}/${++shots}-jev-${width}.png`,await shoot(p,''));
 fail=false;await click(p,'#set-jev-key');await waitFor(p,`document.getElementById('elev').hidden===false`);
 assert.equal(requests.at(-1),'TYPESAFE_API_KEY');checks++;
 await click(p,'#elev-hide');
 await ev(p,`const s=document.getElementById('set-jev-source');s.value='openrouter';s.dispatchEvent(new Event('change'))`);
 await waitFor(p,`!document.getElementById('confirm').hidden`);await click(p,'#confirm-yes');
 await waitFor(p,`document.getElementById('elev').hidden===false && document.getElementById('elev-title').textContent.includes('OPENROUTER_API_KEY')`);
 assert.equal(requests.at(-1),'OPENROUTER_API_KEY');checks++;
 await click(p,'#elev-hide');
 await sleep(4500);
 await ev(p,`document.getElementById('layout-diag-card').scrollIntoView({block:'center'})`);await sleep(200);
 const margin=await ev(p,`parseFloat(getComputedStyle(document.getElementById('layout-diag-card')).marginTop)`);assert.ok(margin>=16);checks++;
 writeFileSync(`${out}/${++shots}-diagnostic-${width}.png`,await shoot(p,''));
 assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);checks+=2;
 }finally{await p.dispose();}
}
console.log(`P527 PASS: ${checks} assertions, ${shots} screenshots`);
}finally{await B.close();await fake.stop();await web.stop();}
