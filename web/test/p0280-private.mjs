// Private-file UI only. Real paired page and Noise; synthetic file picker/bytes, no filesystem or customer data.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitFor,waitState,shoot,key,sleep} from './browser.mjs';
const out='reports/qa/p0280-private/';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),rows=[];
const data=Buffer.alloc(90112,42),card={id:'a'.repeat(32),seat:fake.channel,bytes:data.length,expires:Math.floor(Date.now()/1000)+3600};
const hash=b=>createHash('sha256').update(b).digest('hex');
try{for(const width of [390,1440])for(const theme of ['light','dark'])for(const lang of ['zh','en']){
 fake.reset();fake.st.extraCaps=['privatefiles1'];fake.st.prefs={appearance:{theme,language:lang}};const requests=[];
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='private_file_get'){
   requests.push(m);const r=m.request;
   if(r.info)await send({t:'private_file_res',r:m.r,ok:true,name:'synthetic.bin',card});
   else{const b=data.subarray(r.offset,r.offset+45056);await send({t:'private_file_res',r:m.r,ok:true,offset:r.offset,d:b.toString('base64url'),sha256:hash(b),last:r.offset+b.length===data.length});}
   return true;
  }
  if(m.t==='private_file_revoke'){requests.push(m);await send({t:'private_file_res',r:m.r,ok:true});return true;}return false;
 };
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`Object.defineProperty(window,'showSaveFilePicker',{configurable:true,writable:true,value:undefined})`});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  const offer=async()=>{const turn=await fake.addTurn({k:'agent'},'Synthetic private file');await fake.updateTurn(turn.id,{media_skip:[{name:'synthetic.bin',why:'too_big',bytes:data.length,download:card}]});};
  await offer();await waitFor(p,`!!document.querySelector('[data-private-file]')`);
  const unsupported=await evaluate(p,`document.querySelector('[data-private-file]').textContent`);assert.match(unsupported,lang==='zh'?/不支持流式保存/:/cannot stream/);assert.equal(requests.length,0);
  writeFileSync(out+`${width}-${theme}-${lang}-unsupported.png`,await shoot(p));
  await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`window.__sink={bytes:0,closed:0};window.showSaveFilePicker=async()=>({createWritable:async()=>({write:async bytes=>{window.__sink.bytes+=bytes.length},close:async()=>window.__sink.closed++,abort:async()=>{}})})`});
  await navigate(p,web.url+'?lang='+lang);await waitState(p,'ready');await waitFor(p,`document.querySelector('[data-private-file] a,[data-private-file] button:last-child')?.textContent===${JSON.stringify(lang==='zh'?'私有下载':'Private download')}`);
  await evaluate(p,`document.querySelector('[data-private-file]').lastElementChild.click()`);await waitFor(p,`window.__sink.closed===1`);
  assert.equal(await evaluate(p,`window.__sink.bytes`),data.length);assert.equal(requests.filter(x=>!x.request.info).length,2);for(const m of requests)assert.ok(m.sig&&m.n&&m.ts,'real client signs every request');
  writeFileSync(out+`${width}-${theme}-${lang}-saved.png`,await shoot(p));
  await evaluate(p,`location.hash='file=${card.id}'`);await waitFor(p,`!!document.getElementById('private-file-locator')&&!document.getElementById('private-file-locator').hidden`);await sleep(150);
  const geometry=await evaluate(p,`(()=>{const r=document.querySelector('#private-file-locator .sq').getBoundingClientRect();return {width:r.width,left:r.left,right:r.right,buttons:[...document.querySelectorAll('#private-file-locator button')].filter(x=>!x.hidden).map(x=>{const b=x.getBoundingClientRect();return {w:b.width,h:b.height}}),text:document.getElementById('private-file-locator').textContent}})()`);
  assert.ok(geometry.left>=0&&geometry.right<=width);for(const b of geometry.buttons)assert.ok(b.w>=44&&b.h>=44);assert.match(geometry.text,lang==='zh'?/此链接不是公开/:/not a public/);
  writeFileSync(out+`${width}-${theme}-${lang}-locator.png`,await shoot(p));await key(p,'Escape');await waitFor(p,`document.getElementById('private-file-locator').hidden`);
  assert.deepEqual(p.offsite,[]);rows.push({width,theme,lang,geometry,requests:requests.length});console.log('PASS',width,theme,lang);
 }finally{await p.dispose();}
}}finally{writeFileSync(out+'results.json',JSON.stringify(rows,null,2));await B.close();await fake.stop();await web.stop();}
