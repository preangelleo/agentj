#!/usr/bin/env node
// P256: provider-specific sealed email cards and clipboard-only attachment decisions.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot } from './browser.mjs';
import { elevateFields, elevateDigest, elevateMessage, b64u, unb64u, SEAL_CONTEXT } from '../../protocol/wire.js';

const rec = parityRecorder('web/test/p256.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const subtle = webcrypto.subtle;
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');

async function paired(lang, w = 390, scheme = 'light') {
  fake.reset();
  const p = await newPage(B, w, 844, scheme, { touch: true, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J' });
  return p;
}
async function hostCard(kind, extra) {
  const kp = await subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
  const epk = new Uint8Array(await subtle.exportKey('raw', kp.publicKey));
  const m = { t: 'elev', id: hex(16), kind, n: hex(16), epk: b64u(epk), ttl: 120, tries: 3, ...extra };
  return { m, priv: kp.privateKey, epk };
}
/** What serve does with an elev_answer: the signature over what IT showed, then the seal with its one-time key. */
async function hostOpen(card, a) {
  const dev = fake.devs[0];
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
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const PY = process.env.AJ_PYTHON || ROOT + 'host/.venv/bin/python';
const OUT = ROOT + 'reports/qa/p256/browser'; mkdirSync(OUT, {recursive:true});
const fixture = JSON.parse(execFileSync(PY, ['-c', `import json
from agentj import mail as M
out=[]
for provider in M.PRESETS:
 c=M.blank_config(); c=M.apply_preset(c,provider,'test@'+(M.PRESETS[provider][8][0] if M.PRESETS[provider][8] else 'work.example'))
 for lang in ('zh','en'): out.append(dict(provider=provider,lang=lang,purpose=M.password_purpose(c,lang)))
print(json.dumps(out))`], {env:{...process.env,PYTHONPATH:ROOT+'agentjarvis/host'},encoding:'utf8'}));
try {
  await C('provider-cards-share-signed-content-and-sealed-secret', async () => {
    for (const lang of ['zh','en']) {
      const p = await paired(lang);
      try {
        for (const f of fixture.filter(x=>x.lang===lang)) {
          const card = await hostCard('secret',{name:'AGENTJ_MAIL_PASSWORD',purpose:f.purpose,dest:'Agent J mail channel (encrypted on the Agent J host)',verify:'IMAP + SMTP login'});
          await ev(p, `document.getElementById('toast').classList.remove('on')`);
          await fake.send(card.m);
          await waitFor(p, `document.getElementById('elev') && !document.getElementById('elev').hidden`);
          assert.equal(await ev(p, `document.getElementById('elev-label').textContent`),f.purpose.split('\n')[0]);
          assert.equal(await ev(p, `document.getElementById('elev-input').placeholder`),'abcd efgh ijkl mnop');
          const links=await ev(p, `[...document.querySelectorAll('#elev-rows a')].map(a=>a.href)`);
          if(f.provider==='gmail') {
            assert.ok(links.includes('https://agentj.app/docs/gmail-app-password/'));
            assert.ok(links.includes('https://myaccount.google.com/apppasswords'));
            writeFileSync(`${OUT}/${lang}-gmail.png`,await shoot(p,''));
          }
          const before=answers().length;
          await ev(p, `document.getElementById('elev-form').requestSubmit()`);
          assert.equal(answers().length,before,'empty password sends nothing');
          assert.ok((await ev(p, `document.getElementById('elev-empty').textContent`)).includes(f.purpose.split('\n')[0]),'empty hint uses provider label');
          await type(p,'test fake only pass');
          await ev(p, `document.getElementById('elev-form').requestSubmit()`);
          await waitFor(p, `document.getElementById('elev-input').value === ''`);
          for(let n=0;answers().length===before&&n<100;n++)await new Promise(r=>setTimeout(r,20));
          assert.equal(await hostOpen(card,answers().at(-1)),'test fake only pass');
          await noTrace(p,'test fake only pass');
          await fake.send({t:'elev_done',id:card.m.id,result:'saved',verify:'ok'});
          await waitFor(p, `document.getElementById('elev').hidden`);
          await waitFor(p, `(()=>{const r=document.getElementById('toast').getBoundingClientRect();return Math.abs(r.top+r.height/2-innerHeight/2)<2})()`);
          const center=await ev(p, `(()=>{const r=document.getElementById('toast').getBoundingClientRect();return Math.abs(r.top+r.height/2-innerHeight/2)})()`);
          assert.ok(center<2,'saved receipt is centered');
          await ev(p, `import('./js/ui.js').then(m=>m.toastAction('Undo','Undo',()=>{}))`);
          assert.equal(await ev(p, `document.getElementById('toast').classList.contains('mail-saved')`),false,'later action toast returns to its own placement');
        }
        assert.equal(p.problems.length,0,p.problems.join('\n'));
      } finally {await p.dispose();}
    }
  });
  await C('gmail-card-phone-desktop-dark-light-layout',async()=>{
    for(const lang of ['zh','en'])for(const w of [390,1440])for(const scheme of ['light','dark']){
      const p=await paired(lang,w,scheme);
      try{
        const f=fixture.find(x=>x.provider==='gmail'&&x.lang===lang);
        const card=await hostCard('secret',{name:'AGENTJ_MAIL_PASSWORD',purpose:f.purpose,dest:'mail',verify:'IMAP + SMTP login'});
        await fake.send(card.m);await waitFor(p,`document.getElementById('elev')&&!document.getElementById('elev').hidden`);
        const rect=await ev(p,`(()=>{const r=document.querySelector('#elev .box').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,wide:document.documentElement.scrollWidth>innerWidth}})()`);
        assert.ok(rect.left>=0&&rect.right<=w+1&&rect.top>=0&&rect.bottom<=845&&!rect.wide,JSON.stringify(rect));
        writeFileSync(`${OUT}/${lang}-${w}-${scheme}.png`,await shoot(p,''));
        assert.equal(p.problems.length,0,p.problems.join('\n'));
      }finally{await p.dispose();}
    }
  });
  await C('long-draft-short-clipboard-stays-at-cursor-long-clipboard-attaches',async()=>{
    const p=await paired('zh');
    try {
      await ev(p, `(()=>{for(const id of ['phone-env-dismiss','a2hs-ok']) document.getElementById(id)?.click(); const n=document.getElementById('confirm-no'); if(n&&!document.getElementById('confirm').hidden)n.click();})()`);
      const draft='a long draft line\n'.repeat(50), short='mail@example.com';
      await ev(p, `(()=>{const i=document.getElementById('input');i.value=${JSON.stringify(draft)};i.dispatchEvent(new Event('input'));i.focus();i.setSelectionRange(12,12)})()`);
      const paste=async(text)=>ev(p,`(()=>{const d=new DataTransfer();d.setData('text/plain',${JSON.stringify(text)});const e=new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:d});document.getElementById('input').dispatchEvent(e);return e.defaultPrevented})()`);
      assert.equal(await paste(short),false,'native short paste is not intercepted despite long draft');
      await p.send('Input.insertText',{text:short});
      assert.equal(await ev(p,`document.getElementById('input').value`),draft.slice(0,12)+short+draft.slice(12));
      const snapshot=await ev(p,`document.getElementById('input').value`);
      assert.equal(await paste('oversized line\n'.repeat(500)),true,'large clipboard alone intercepted');
      assert.equal(await ev(p,`document.getElementById('input').value`),snapshot);
      assert.equal(await paste('\u200b'.repeat(2200)),true,'length limit also catches zero-width text');
      assert.match(await ev(p,`document.getElementById('toast').textContent`),/刚粘贴/);
      await ev(p, `navigator.clipboard.writeText('short@example.com')`);
      await ev(p, `document.getElementById('input').setSelectionRange(0,0);import('./js/relay.js').then(m=>m.pasteClipText())`);
      assert.ok((await ev(p,`document.getElementById('input').value`)).startsWith('short@example.com'));
      const count=await ev(p,`import('./js/relay.js').then(m=>m.debug().atts.length)`);
      await ev(p,`navigator.clipboard.writeText('x'.repeat(3000))`);
      await ev(p,`import('./js/relay.js').then(m=>m.pasteClipText())`);
      assert.equal(await ev(p,`import('./js/relay.js').then(m=>m.debug().atts.length)`),count+1);
      assert.equal(p.problems.length,0,p.problems.join('\n'));
    }finally{await p.dispose();}
  });
  await C('unknown-provider-server-popup-and-settings-save-before-card',async()=>{
    const p=await paired('zh');
    try{
      await ev(p,`(()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();const n=document.getElementById('confirm-no');if(n&&!document.getElementById('confirm').hidden)n.click();document.getElementById('setBtn').click();})()`);
      await ev(p,`document.getElementById('set-mail-nav').click()`);
      await waitFor(p,`document.getElementById('mail-preset').options.length>0`);
      await ev(p,`(()=>{const i=document.getElementById('mail-addr');i.value='test@unknown.example';i.dispatchEvent(new Event('change'));document.getElementById('mail-key').click()})()`);
      await waitFor(p,`!!document.getElementById('mail-servers')`);
      assert.equal(await ev(p,`document.getElementById('mail-dialog-imap-host').value`),'','unknown domain does not inherit QQ servers');
      assert.equal(await ev(p,`document.getElementById('mail-dialog-smtp-host').value`),'');
      await ev(p,`(()=>{document.getElementById('mail-dialog-imap-host').value='imap.example.com';document.getElementById('mail-dialog-smtp-host').value='smtp.example.com';document.querySelector('#mail-servers form').requestSubmit()})()`);
      await waitFor(p,`document.getElementById('mail-msg').textContent.length>0&&!document.getElementById('mail-key').disabled`);
      const set=fake.st.mailMsgs.filter(m=>m.t==='mail_set').at(-1);
      assert.equal(set.cfg.imap.host,'imap.example.com');
      assert.equal(set.cfg.smtp.port,465);
      assert.equal(fake.st.mailMsgs.at(-1).t,'mail_key');
      assert.equal(await ev(p,`!!document.getElementById('mail-password')`),false,'no password field on settings page');
      assert.equal(p.problems.length,0,p.problems.join('\n'));
    }finally{await p.dispose();}
  });
}finally{rec.write();await B.close();await fake.stop();await web.stop();}
console.log(failed?`FAIL ${failed}`:'PASS P256 browser');process.exit(failed?1:0);
