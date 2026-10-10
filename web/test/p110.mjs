// Real Chromium multipart share-target navigation -> active SW -> composer -> Noise -> isolated fake host.
// No system Android share sheet / physical Safari claim. Permission branches below are explicitly fixtures.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const out=new URL('../../../reports/qa/p110/browser/',import.meta.url);mkdirSync(out,{recursive:true});
const docs=new URL('../../site/content/docs/22-share-screenshot/',import.meta.url);
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
// P122: the one allowed POST is the ticket cookie endpoint (/.aj/rt: same-origin JSON ≤ 512 B, worker.ts) — never share bytes
const posts=[];web.server.on('request',r=>{if(r.method==='POST'&&r.url!=='/.aj/rt')posts.push(r.url)});
const sender=createServer((req,res)=>res.end('<!doctype html><title>Synthetic screenshot share fixture</title><p>Local share sender</p>'));
await new Promise(r=>sender.listen(0,'127.0.0.1',r));const senderUrl=`http://127.0.0.1:${sender.address().port}/`;
const results={};
async function navigateShare(p,base64,{images=2,text='P110 private screenshot canary',mime='image/png'}={}){
 await navigate(p,senderUrl);
 // A sender outside the receiving app navigates with multipart, just as the OS share target does.
 await evaluate(p,`(()=>{const form=document.createElement('form');form.method='POST';form.enctype='multipart/form-data';form.action=${JSON.stringify(web.url+'share-target')};const input=document.createElement('input');input.type='file';input.name='images';input.multiple=true;const dt=new DataTransfer();const bytes=Uint8Array.from(atob(${JSON.stringify(base64)}),c=>c.charCodeAt(0));for(let i=0;i<${images};i++)dt.items.add(new File([bytes],'screen-'+i+'.png',{type:${JSON.stringify(mime)}}));input.files=dt.files;form.append(input);const note=document.createElement('input');note.name='text';note.value=${JSON.stringify(text)};form.append(note);document.body.append(form);form.submit()})()`);
 await waitFor(p,`location.origin===${JSON.stringify(new URL(web.url).origin)} && document.getElementById('share-status')?.textContent.length>0`);
}
async function click(p,id){const b=await evaluate(p,`(()=>{const r=document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...b});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...b});}
try{
 for(const language of ['zh','en']){
  const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/',senderUrl]});
  const frames=[];p.on(m=>{if(m.method==='Network.webSocketFrameSent')frames.push(m.params.response)});
  await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`navigator.serviceWorker.ready.then(()=>true)`);await waitFor(p,'!!navigator.serviceWorker.controller');
  const manifest=await p.send('Page.getAppManifest');assert.equal(manifest.errors.length,0);assert.match(manifest.data,/share_target/);
  await fake.addTurn({k:'host',text:'Local synthetic screenshot'},'P110 acceptance: safe test image');
  await waitFor(p,`document.getElementById('words').textContent.includes('P110 acceptance')`);
  const screenshot=await shoot(p,'');const image=Buffer.from(screenshot);const before=fake.st.says.length, oldBlobs=new Set(fake.st.blobs.keys());
  await navigateShare(p,image.toString('base64'));
  await waitState(p,'ready');await waitFor(p,`document.querySelectorAll('#tray .chip[data-st="ready"]').length===2`);
  assert.equal(await evaluate(p,'document.getElementById("input").value'),'P110 private screenshot canary');
  assert.equal(fake.st.says.length,before,'intake never auto-sends');assert.deepEqual(posts,[],'multipart was consumed locally; web server received no POST');
  assert.equal(await evaluate(p,'location.search.includes("share=")'),false,'one-time handle stripped');
  const received=[...fake.st.blobs.entries()].filter(([id,b])=>!oldBlobs.has(id)&&b.bytes).map(([,b])=>b);assert.equal(received.length,2);
  for(const b of received)assert.ok(b.bytes.equals(image),'Noise receiver gets exact screenshot bytes');
  const shot=await shoot(p,'');if(process.env.P110_DOC_SHOTS==='1')writeFileSync(new URL('android.'+language+'.png',docs),shot);writeFileSync(new URL('android-'+language+'.png',out),shot);
  await click(p,'send');await waitFor(p,`document.querySelectorAll('#tray .chip').length===0`);assert.equal(fake.st.says.length,before+1);assert.equal(fake.st.says.at(-1).att.length,2);
  assert.ok(frames.length>0);for(const f of frames){const bytes=f.opcode===2?Buffer.from(f.payloadData,'base64'):Buffer.from(f.payloadData);assert.ok(!bytes.includes(Buffer.from('P110 private screenshot canary')));assert.ok(!bytes.includes(image.subarray(0,64)));}
  results['android-multipart-'+language]='PASS';
  await navigate(p,web.url+'?lang='+language+'&from=share');await waitState(p,'ready');await waitFor(p,'!document.getElementById("share-paste").hidden');
  assert.equal(await evaluate(p,'document.querySelectorAll("#tray .chip").length'),0);
  const button=await evaluate(p,'document.getElementById("share-paste").getBoundingClientRect().height');assert.ok(button>=56);
  const iosShot=await shoot(p,'');if(process.env.P110_DOC_SHOTS==='1')writeFileSync(new URL('iphone.'+language+'.png',docs),iosShot);writeFileSync(new URL('iphone-'+language+'.png',out),iosShot);
  // Real Chromium Clipboard API, CDP-granted browser permission, not Safari's system prompt.
  await B.browser.send('Browser.grantPermissions',{origin:new URL(web.url).origin,permissions:['clipboardReadWrite','clipboardSanitizedWrite']});
  await evaluate(p,`navigator.clipboard.write([new ClipboardItem({'image/png':new Blob([Uint8Array.from(atob(${JSON.stringify(image.toString('base64'))}),c=>c.charCodeAt(0))],{type:'image/png'})})])`);
  await click(p,'share-paste');await waitFor(p,`document.querySelectorAll('#tray .chip[data-st="ready"]').length===1`);
  assert.equal(fake.st.says.length,before+1);await click(p,'send');await waitFor(p,`document.querySelectorAll('#tray .chip').length===0`);
  await B.browser.send('Browser.resetPermissions');
  // Explicit permission fixtures cover denial / empty clipboard without reading anybody's clipboard.
  await evaluate(p,`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{read:()=>Promise.reject(new DOMException('test','NotAllowedError'))}})`);
  await click(p,'share-paste');await waitFor(p,`document.getElementById('toast').classList.contains('on')`);
  const denied=await evaluate(p,'document.getElementById("toast").textContent');assert.match(denied,language==='zh'?/剪贴板|权限/:/clipboard|permission/i);
  await evaluate(p,`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{read:async()=>[]}})`);await click(p,'share-paste');await waitFor(p,`document.getElementById('toast').textContent!==${JSON.stringify(denied)}`);
  assert.equal(await evaluate(p,'document.querySelectorAll("#tray .chip").length'),0);
  await navigate(p,web.url+'?lang='+language);await waitState(p,'ready');assert.equal(await evaluate(p,'document.getElementById("share-notice").hidden'),true);
  results['clipboard-real-and-permission-fixtures-'+language]='PASS';
  assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
 }
 const unpaired=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/',senderUrl]});
 await navigate(unpaired,web.url+'?from=share');await waitFor(unpaired,'document.getElementById("share-status").textContent.includes("配对")');assert.equal(await evaluate(unpaired,'document.getElementById("share-paste").hidden'),true);
 await evaluate(unpaired,'navigator.serviceWorker.ready.then(()=>true)');await waitFor(unpaired,'!!navigator.serviceWorker.controller');
 await navigateShare(unpaired,Buffer.from('not secret').toString('base64'),{images:1});await waitFor(unpaired,'document.getElementById("share-status").textContent.includes("配对")');
 assert.equal(await evaluate(unpaired,'document.querySelectorAll("#tray .chip").length'),0);assert.deepEqual(posts,[]);results.unpaired='PASS';
}finally{await B.close();await fake.stop();await web.stop();await new Promise(r=>sender.close(r));}
writeFileSync(new URL('results.json',out),JSON.stringify({results,scope:'Real Chromium + Noise fake host; iOS/Android physical share menus and Apple import/signing pending',serverPosts:posts},null,2)+'\n');
console.log(JSON.stringify(results));

if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p110.mjs',results:Object.fromEntries(Object.entries(results).map(([k,v])=>[k,v.toLowerCase()]))}));
