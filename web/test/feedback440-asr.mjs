import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {launch,newPage,evaluate,navigate,waitFor,rect,tap,shoot,key} from './browser.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const out='reports/qa/feedback440-asr';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const results=[];
const click=async(p,sel)=>{await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`);const r=await rect(p,sel);await tap(p,r.x,r.y)};
try { for(const width of (process.env.P440_SECURITY_ONLY ? [390] : [390,1440])) for(const theme of (process.env.P440_SECURITY_ONLY ? ['dark'] : ['light','dark'])) {
 fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:'zh',theme},voice:{asr:{mode:width===390?'cloud':'local',model:width===390?'provider/audio-model':'',base_url:'',key_env:width===390?'OPENROUTER_API_KEY':'',hotwords:['Agent J','Olares','Claude Code','Codex','OpenCode']}}};
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try {
  await navigate(p,fake.newPairing(web.url));await waitFor(p,"window.__ajState==='awaiting-approval'");await fake.approve();await waitFor(p,"window.__ajState==='ready'");
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=${JSON.stringify(theme)}`);
  await click(p,'#setBtn');await waitFor(p,"!document.getElementById('set-asr').hidden");
  await evaluate(p,`document.getElementById('set-asr-mode').value='custom';document.getElementById('set-asr-mode').dispatchEvent(new Event('change'));document.getElementById('set-asr-url').value='http://asr.local/v1';document.getElementById('set-asr-model').value='qwen-asr';document.getElementById('set-asr-key-env').value='';document.getElementById('set-asr-hotwords').value='Agent J\\nOlares\\nCodex';`);
  // A named host key cannot be bound to a phone-chosen endpoint through preferences.
  await evaluate(p,"document.getElementById('set-asr-key-env').value='ASR_API_KEY'");
  await click(p,'#set-asr-save');
  await waitFor(p,"document.getElementById('toast').textContent.includes('设置尚未保存')");
  assert.deepEqual(fake.st.prefSets,[]);
  await evaluate(p,"document.getElementById('set-asr-key-env').value=''");
  await click(p,'#set-asr-save');await waitFor(p,"!document.getElementById('set-asr-save').disabled");
  assert.equal(fake.st.prefs.voice.asr.mode,'custom');assert.equal(fake.st.prefs.voice.asr.base_url,'http://asr.local/v1');assert.deepEqual(fake.st.prefs.voice.asr.hotwords,['Agent J','Olares','Codex']);
  assert.deepEqual(fake.st.prefSets.map(m=>m.key),['voice.asr.mode','voice.asr.key_env','voice.asr.base_url','voice.asr.model','voice.asr.hotwords','voice.asr.mode']);
  assert.equal(fake.st.prefSets.some(m=>m.key==='voice.asr.key'),false);
  await evaluate(p,"document.getElementById('set-asr').scrollIntoView({block:'start'})");
  assert.ok(await evaluate(p,"document.documentElement.scrollWidth<=innerWidth"));
  assert.ok(await evaluate(p,"document.getElementById('set-asr-save').getBoundingClientRect().height>=44"));
  writeFileSync(`${out}/${width}-${theme}-settings.png`,await shoot(p));
  await key(p,'Escape',{keyCode:27});
  // Round-trip the real blob transport: only fixed status, never endpoint error bodies.
  fake.st.asrNotice='local_fallback';
  const result=await evaluate(p,`(async()=>{const api=await import('./js/api.js');const b=new ArrayBuffer(32044),v=new DataView(b);for(const [i,s] of [[0,'RIFF'],[8,'WAVE'],[12,'fmt '],[36,'data']])for(let j=0;j<s.length;j++)v.setUint8(i+j,s.charCodeAt(j));v.setUint32(4,32036,true);v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);v.setUint32(40,32000,true);return await api.transcribe(new Blob([b],{type:'audio/wav'}),1)})()`);
  assert.equal(result.ok,true);assert.equal(result.notice,'local_fallback');
  assert.deepEqual(p.problems,[]);results.push({width,theme,settings:true,notice:true,pass:true});console.log('PASS',width,theme);
 } catch(error) {
  const diagnostic = await evaluate(p, `({state:window.__ajState,body:document.body.dataset,settings:{hidden:document.getElementById('settings').hidden,classes:document.getElementById('settings').className},asr:{hidden:document.getElementById('set-asr').hidden,html:document.getElementById('set-asr').outerHTML.slice(0,1200)},toast:document.getElementById('toast').textContent,gearCover:(()=>{const r=document.getElementById('setBtn').getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,500)})()})`).catch(e=>({captureError:String(e)}));
  const stem=`${out}/failure-${width}-${theme}-${Date.now()}`;
  writeFileSync(stem+'.json',JSON.stringify({error:String(error),diagnostic,problems:p.problems},null,2));
  writeFileSync(stem+'.png',await shoot(p));
  console.error('browser diagnostic',JSON.stringify(diagnostic),JSON.stringify(p.problems));
  throw error;
 } finally {await p.dispose()}
}} finally {await B.close();await fake.stop();await web.stop();writeFileSync(out+'/results.json',JSON.stringify(results,null,2)+'\n')}
