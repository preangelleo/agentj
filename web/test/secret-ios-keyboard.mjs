// Regression for iPhone grow-card keyboard collapse. Viewport injection is layout evidence only, not a real IME.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitFor,waitState,shoot,key,sleep} from './browser.mjs';
import {elevateFields,elevateDigest,elevateMessage,b64u,unb64u,SEAL_CONTEXT} from '../../protocol/wire.js';
const baseline=process.env.KEYBOARD_BASELINE_DIR;
const out=process.env.OLARES_RUN_OUT||'reports/qa/secret-editor-input';mkdirSync(out,{recursive:true});
const fake=await startFakeHost(),web=await startWebServer({port:0,...(baseline?{publicDir:baseline}:{})}),B=await launch(),subtle=webcrypto.subtle;
const hex=n=>Array.from(webcrypto.getRandomValues(new Uint8Array(n)),b=>b.toString(16).padStart(2,'0')).join('');
let checks=0;const records=[];const ok=(v,m)=>{assert.ok(v,m);checks++;};
async function hostCard(kind, extra) {
  const kp = await subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  const epk = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  const m = { t: 'elev', id: hex(16), kind, n: hex(16), epk: b64u(epk), ttl: 120, tries: 3, ...extra };
  return { m, priv: kp.privateKey, epk };
}
/** What serve does with an elev_answer: the signature over what IT showed, then the seal with its one-time key. */
async function hostOpen(card, a) {
  const dev = fake.devs.at(-1);
  const digest = await elevateDigest(card.m.kind, elevateFields(card.m));
  const ctSha = a.ok && a.ct ? Array.from(new Uint8Array(await subtle.digest('SHA-256', unb64u(a.ct))), (b) => b.toString(16).padStart(2, '0')).join('') : null;
  const key = await subtle.importKey('raw', unb64u(dev.sk), { name: 'Ed25519' }, false, ['verify']);
  const msg = elevateMessage(fake.channel, dev.id, card.m.id, card.m.kind, a.ok ? 'allow' : 'deny', card.m.n, a.ts, digest, ctSha);
  assert.ok(await subtle.verify({ name: 'Ed25519' }, key, unb64u(a.sig), msg), 'signature over the shown card');
  if (!a.ok || !a.ct) return null;
  const peEpk = unb64u(a.epk), blob = unb64u(a.ct);
  const pk = await subtle.importKey('raw', peEpk, { name: 'X25519' }, false, []);
  const shared = new Uint8Array(await subtle.deriveBits({ name: 'X25519', public: pk }, card.priv, 256));
  const ikm = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const salt = new Uint8Array([...card.epk, ...peEpk]);
  const k = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(SEAL_CONTEXT) }, ikm, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const aad = new TextEncoder().encode([SEAL_CONTEXT, fake.channel, dev.id, card.m.id, card.m.kind, card.m.n, digest].join('\n'));
  const pt = await subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: aad }, k, blob.slice(12));
  // a different nonce (a replay against a re-armed card) does not open
  const bad = new TextEncoder().encode([SEAL_CONTEXT, fake.channel, dev.id, card.m.id, card.m.kind, '0'.repeat(32), digest].join('\n'));
  await assert.rejects(subtle.decrypt({ name: 'AES-GCM', iv: blob.slice(0, 12), additionalData: bad }, k, blob.slice(12)));
  return new TextDecoder().decode(pt);
}
async function type(p, text) {
  await ev(p, `document.getElementById('elev-input').focus()`);
  await p.send('Input.insertText', { text });
}
const answers = () => fake.log.filter((m) => m.t === 'elev_answer');
async function noTrace(p, secret) {
  const blob = JSON.stringify(fake.log);
  assert.ok(!blob.includes(secret) && !blob.includes(b64u(new TextEncoder().encode(secret))), 'never in what the host received');
  assert.equal(await ev(p, `document.getElementById('elev-input').value`), '', 'the field is cleared');
  assert.equal(await ev(p, `document.documentElement.outerHTML.includes(${JSON.stringify(secret)})`), false, 'not in the DOM');
  assert.equal(await ev(p, `JSON.stringify(Object.assign({}, localStorage)).includes(${JSON.stringify(secret)})`), false, 'not in localStorage');
  const idb = await ev(p, `new Promise((res) => { const out = []; const rq = indexedDB.open('agentjarvis'); rq.onsuccess = () => { const db = rq.result; const names = [...db.objectStoreNames]; if (!names.length) return res('');
    const tx = db.transaction(names, 'readonly'); for (const n of names) { const c = tx.objectStore(n).openCursor(); c.onsuccess = () => { const cur = c.result; if (cur) { try { out.push(JSON.stringify(cur.value, (k, v) => v instanceof CryptoKey ? '[key]' : v)); } catch { out.push('?'); } cur.continue(); } }; }
    tx.oncomplete = () => res(out.join('\\n')); }; rq.onerror = () => res(''); })`);
  assert.ok(!String(idb).includes(secret), 'not in IndexedDB');
}

try{
 for(const theme of ['light','dark'])for(const width of [390,1440]){
 fake.reset();fake.st.prefs={appearance:{language:'zh',theme}};
 const p=await newPage(B,width,844,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').value='ordinary draft';document.documentElement.dataset.ios='1'`);
 const c=await hostCard('secret',{name:'TEST_KEY',purpose:'Keyboard acceptance fixture',dest:'env:/tmp/agentj-keyboard-fixture#TEST_KEY',verify:'',button_only:true,passkey_required:false});
 await fake.send(c.m);await waitFor(p,`document.getElementById('elev-input')&&!document.getElementById('elev').hidden`);await sleep(120);
 await ev(p,`document.getElementById('elev-input').scrollIntoView({block:'center'})`);
 writeFileSync(`${out}/${theme}-${width}-before-keyboard.png`,await shoot(p,''));
 if(baseline){
   await ev(p,`document.getElementById('elev-input').focus();Object.defineProperty(window,'visualViewport',{configurable:true,value:Object.assign(new EventTarget(),{height:385,width:${width},offsetTop:27,offsetLeft:0,scale:1})});document.getElementById('m5h').getBoundingClientRect=()=>({top:55,height:30});document.getElementById('stage').getBoundingClientRect=()=>({top:60,left:0,width:${width},height:10,bottom:70});`);
   await ev(p,`import('./js/popup-layout.js').then(m=>m.layoutPopups([document.getElementById('elev')]))`);
   writeFileSync(`${out}/${theme}-${width}-after-keyboard.png`,await shoot(p,''));
   const height=await ev(p,`parseFloat(document.getElementById('elev').style.getPropertyValue('--popup-height'))`);
   writeFileSync(out+'/red.json',JSON.stringify({source:'519f95a0fcc4a238801e8da428320cfba4b58864',height,evidence:'injected visualViewport and collapsed chat frame, not a real keyboard'}));
   ok(height>100,'grow secret card retains usable keyboard-time height');
 }
 // Native modal ancestor deliberately has a transform and overflow clipping.
 if(width===1440)await ev(p,`(()=>{const d=document.createElement('dialog');d.id='keyboard-native';d.style.cssText='transform:translateY(-50%);overflow:hidden;width:320px;height:280px';document.body.append(d);d.showModal();d.append(document.getElementById('elev'));})()`);
 await ev(p,`document.getElementById('elev-input').click()`);await waitFor(p,`!document.getElementById('fsEd').hidden`);
 ok(await ev(p,`document.getElementById('fsText').type==='password'&&document.getElementById('fsText').autocomplete==='one-time-code'&&getComputedStyle(document.getElementById('fsText')).webkitTextSecurity==='disc'&&document.activeElement.id==='fsText'`),'native secure input receives focus and masks text');
 await ev(p,`(()=>{window.keyboardFixture={height:385,width:${width},offsetTop:27,offsetLeft:0,scale:1};Object.defineProperty(window,'visualViewport',{configurable:true,value:Object.assign(new EventTarget(),keyboardFixture)});dispatchEvent(new Event('resize'));})()`);await sleep(480);
 const geometry=await ev(p,`(()=>{const r=document.getElementById('fsEd').getBoundingClientRect(),a=document.getElementById('fsText').getBoundingClientRect(),b=document.getElementById('fsSend').getBoundingClientRect();return {r:{top:r.top,bottom:r.bottom,height:r.height},a:{top:a.top,bottom:a.bottom,height:a.height},b:{top:b.top,bottom:b.bottom,height:b.height},hit:document.elementFromPoint(b.left+b.width/2,b.top+b.height/2)?.closest('#fsSend')?.id,active:document.activeElement.id};})()`);
 records.push({theme,width,geometry});writeFileSync(`${out}/${theme}-${width}-geometry.json`,JSON.stringify(geometry));writeFileSync(`${out}/${theme}-${width}-probe.png`,await shoot(p,''));ok(Math.abs(geometry.r.top-27)<2&&Math.abs(geometry.r.height-385)<2,'editor fits live viewport including offset and native ancestor');ok(geometry.a.height>80&&geometry.a.top>=27&&geometry.b.bottom<=412&&geometry.hit==='fsSend','field and host save remain visible and hittable');
 writeFileSync(`${out}/${theme}-${width}-after-keyboard.png`,await shoot(p,''));
 await ev(p,`document.getElementById('fsSend').click()`);ok(await ev(p,`!document.getElementById('fsEd').hidden&&!!document.querySelector('#fsEd .fs-status')`),'empty save stays open with explanation');
 const canary='keyboard-'+hex(12);await p.send('Input.insertText',{text:canary});await ev(p,`document.getElementById('fsSend').click()`);
 await waitFor(p,`document.getElementById('fsEd').hidden`);await sleep(200);
 const answer=answers().find(a=>a.id===c.m.id);ok(!!answer,'save sends existing sealed signed answer');ok(await hostOpen(c,answer)===canary,'host can open the sealed value');await noTrace(p,canary);
 ok(await ev(p,`document.getElementById('input').value==='ordinary draft'&&document.getElementById('fsText').tagName==='TEXTAREA'`),'composer draft preserved and ordinary editor restored');
 await fake.send({t:'elev_done',id:c.m.id,result:'saved'});await sleep(80);await ev(p,`document.getElementById('keyboard-native')?.close()`);

 await p.dispose();
 }
 writeFileSync(out+'/layout.json',JSON.stringify({checks,records,kind:'injected visualViewport, not real soft keyboard'},null,2));console.log(`secret-ios-keyboard ${checks} PASS`);
}finally{await B.close();await fake.stop();await web.stop();}
