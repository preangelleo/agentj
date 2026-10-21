// P352 「模型和 API Key」 — the real page against the local fake host (real Noise), own headless Chromium (never :9222), a CDP
// virtual platform authenticator holding this phone's passkey (Face ID's stand-in). Checks:
//   · the rename (page title, the Settings entry), the model buttons on top, then 基座模型 (one dashed card per vendor) and
//     第三方 API Key; rows = tag · mono NAME · state; set rows 拷贝 / 修改 / 删除, empty rows 填写, sign-in rows state only;
//     every icon button has aria-label + title and a ≥ 44 px box, buttons ≥ 8 px apart
//   · 拷贝: a real click → Face ID → keys_op copy {id, n, fa} → the fake value lands in the clipboard (read back), 「已拷贝」,
//     and the value is nowhere in document.documentElement.outerHTML (nor in localStorage)
//   · 删除: the confirm card names the key; on the key in use it carries the strong warning; 「删除」 → Face ID → keys_op delete
//   · 修改 / 填写 → keys_set NAME, the 密钥卡 toast; 「添加」 → a NAME (bad one refused), Esc closes the form only
//   · no passkey on this phone → 拷贝 offers 「设置 Face ID」 and sends nothing
//   · Esc leaves the page
// Shots (fake data only: no account, key or address): reports/p352/chromium/<theme>-<w>x<h>[-<part>].png at 390×844, 690×900
// (fold), 1440×900, light + dark, plus en at 390. P352_BEFORE=<a public/ dir of the base tree> instead shoots the old page
// (provider list, empty on a Claude host) into reports/p352/before/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key, rect } from './browser.mjs';
import { keysHandler, FAKE_VALUE } from './p352-fixture.mjs';

const BEFORE = process.env.P352_BEFORE || '';
const OUT = new URL(`../reports/p352/${BEFORE ? 'before' : 'chromium'}/`, import.meta.url);
mkdirSync(OUT, { recursive: true });
const SIZES = [[390, 844], [690, 900], [1440, 900]];
let n = 0;
const ok = (c, name) => { assert.ok(c, name); n++; console.log('PASS ' + name); };
const $ = (sel) => `document.querySelector(${JSON.stringify(sel)})`;

const web = await startWebServer(BEFORE ? { publicDir: BEFORE } : {});
const PAGE = web.url.replace('127.0.0.1', 'localhost');   // WebAuthn wants a domain rp id, not an IP
const fake = await startFakeHost();
const B = await launch();
async function click(p, sel) {
  await evaluate(p, `${$(sel)}.scrollIntoView({block:'center'})`); await sleep(80);
  const r = await rect(p, sel);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
}
let lang = 'zh';
async function view(p, w, h, scheme) {
  await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
  await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
  // the page's theme is the host preference appearance.theme (as p303 / p314 set it), not only the media query
  await fake.send({ t: 'preferences', value: { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false } }, problem: null });
  await waitFor(p, `document.documentElement.getAttribute('data-theme')===${JSON.stringify(scheme)}`);
  // P299 (owner 2026-10-15): every page but the chat is Noir in BOTH themes — a light theme does not lighten this page
  if (!BEFORE) assert.equal(await evaluate(p, `getComputedStyle(document.body).backgroundColor`), 'rgb(44, 43, 38)', 'models page canvas = Noir in ' + scheme);
  await sleep(250);
}
const save = async (p, name) => writeFileSync(new URL(name + '.png', OUT), await shoot(p, ''));
async function openModels(p) {
  await evaluate(p, `document.getElementById('settings').hidden && document.getElementById('setBtn').click()`); await sleep(250);
  await click(p, '#open-models');
  await waitFor(p, `document.body.dataset.view==='models'`);
}
async function shots(p, prefix = '') {
  for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) {
    await view(p, w, h, scheme);
    await evaluate(p, `document.querySelector('.ajpages').scrollTop=0`); await sleep(120);
    await save(p, `${prefix}${scheme}-${w}x${h}`);
    if (w === 390 && !BEFORE) {
      await evaluate(p, `(()=>{const s=document.querySelector('.ajpages');s.scrollTop=s.scrollHeight})()`); await sleep(150);
      await save(p, `${prefix}${scheme}-${w}x${h}-bottom`);
      await evaluate(p, `document.querySelector('.ajpages').scrollTop=0`);
    }
  }
  await view(p, 390, 844, 'light');
}

try {
  // the phone's passkey: a resident P-256 credential for this page's rp id; the host names it in `keys.fa`
  const credId = randomBytes(16);
  const priv = generateKeyPairSync('ec', { namedCurve: 'P-256' }).privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64');
  const fa = credId.toString('base64url');
  const opts = { fa };
  const calls = keysHandler(fake.st, opts);
  if (BEFORE) {
    const prev = fake.st.onApp;
    fake.st.onApp = async (c, m, send) => (m.t === 'provider_get' ? (await send({ t: 'providers', r: m.r, providers: [] }), true) : prev(c, m, send));
  }
  const p = await newPage(B, 390, 844, 'light', { touch: false, allow: [web.url, PAGE, fake.relay + '/'] });
  await p.send('WebAuthn.enable', { enableUI: false });
  const { authenticatorId } = await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal',
    hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await p.send('WebAuthn.addCredential', { authenticatorId, credential: { credentialId: credId.toString('base64'), isResidentCredential: true,
    rpId: 'localhost', privateKey: priv, userHandle: Buffer.from('p352-fixture').toString('base64'), signCount: 0 } });
  await navigate(p, fake.newPairing(PAGE + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);   // the headless-browser hint is not under test
  await openModels(p);
  if (BEFORE) {
    await sleep(800);
    await shots(p);
    console.log('before shots → ' + OUT.pathname);
  } else {
    await waitFor(p, `document.querySelectorAll('#keys-box .krow').length>0`);
    await sleep(300);
    // ---- structure
    ok(await evaluate(p, `document.querySelector('#models-view h1').textContent==='模型和 API Key'`), 'page title renamed 「模型和 API Key」');
    ok(await evaluate(p, `document.querySelector('#open-models b').textContent==='模型和 API Key'`), 'Settings entry renamed');
    ok(await evaluate(p, `document.getElementById('model-options').compareDocumentPosition(document.getElementById('keys-box')) & Node.DOCUMENT_POSITION_FOLLOWING`), 'model picker stays on top');
    const heads = await evaluate(p, `[...document.querySelectorAll('#keys-box h2')].map(h=>h.textContent)`);
    ok(heads[0] === '基座模型' && heads[1] === '第三方 API Key', 'two sections: 基座模型 · 第三方 API Key');
    ok(await evaluate(p, `[...document.querySelectorAll('#keys-box .kvendor')].every(c=>getComputedStyle(c).borderTopStyle==='dashed')`), 'vendor cards are dashed');
    ok(await evaluate(p, `document.querySelectorAll('#keys-box .kvendor:not(.kvendor--third)').length===4`), 'one card per vendor');
    const rows = await evaluate(p, `[...document.querySelectorAll('#keys-box .krow')].map(r=>({n:r.dataset.name,t:r.dataset.type,tag:r.querySelector('.ktag')?.textContent??'',st:r.querySelector('.kstate').textContent,ops:[...r.querySelectorAll('.kbtn')].map(b=>b.dataset.op)}))`);
    const R = Object.fromEntries(rows.map((r) => [r.n, r]));
    ok(R.claude.ops.length === 0 && R.claude.st === '已登录' && R.codex.st === '未登录' && R.claude.tag === '登录', 'sign-in rows: state only, no buttons');
    ok(R.ANTHROPIC_API_KEY.ops.join() === 'copy,edit,delete' && R.ANTHROPIC_API_KEY.tag === '按量' && R.ANTHROPIC_API_KEY.st.startsWith('••••••••x9Qa') && R.ANTHROPIC_API_KEY.st.includes('正在使用'), 'set row: masked + last 4, in use, 拷贝 · 修改 · 删除');
    ok(R.BAILIAN_TOKEN_PLAN_API_KEY.st === '••••••••' && R.BAILIAN_TOKEN_PLAN_API_KEY.tag === '订阅', 'set row without a tail: mask only; 订阅 tag');
    ok(R.DEEPSEEK_API_KEY.ops.join() === 'set' && R.DEEPSEEK_API_KEY.st === '未填', 'empty row: 未填 + 填写 only');
    ok(R.ELEVENLABS_API_KEY && R.WOLFRAM_APP_TOKEN && R.ELEVENLABS_API_KEY.ops.join() === 'copy,edit,delete' && R.ELEVENLABS_API_KEY.tag === '', 'third-party keys listed with the same row shape (no billing tag)');
    const btns = await evaluate(p, `[...document.querySelectorAll('#keys-box .kbtn')].map(b=>{const r=b.getBoundingClientRect();return {a:b.getAttribute('aria-label'),t:b.title,w:r.width,h:r.height,txt:b.textContent.trim(),svg:!!b.querySelector('svg[stroke-width="1.9"]')}})`);
    ok(btns.length > 10 && btns.every((b) => b.a && b.t && b.w >= 44 && b.h >= 44 && b.txt === '' && b.svg), 'icon-only buttons: aria-label + title, 44 px, line icons');
    ok(await evaluate(p, `[...document.querySelectorAll('#keys-box .kacts')].every(a=>{const b=[...a.children].map(x=>x.getBoundingClientRect());return b.every((r,i)=>i===0||r.left-b[i-1].right>=8)})`), 'icon buttons ≥ 8 px apart');
    ok(await evaluate(p, `(()=>{const t={};for(const b of document.querySelectorAll('#keys-box .kbtn'))t[b.dataset.op]=b.title;return t.copy==='拷贝'&&t.edit==='修改'&&t.delete==='删除'&&t.set==='填写'&&document.getElementById('keys-add').title==='添加 key'})()`), 'zh titles 拷贝 / 修改 / 删除 / 填写 / 添加 key');
    await shots(p);

    // ---- 拷贝 (Face ID → value → clipboard, never the DOM)
    await evaluate(p, `navigator.clipboard.writeText('p352-before')`);
    await click(p, '.krow[data-name="OPENAI_API_KEY"] .kbtn--copy');
    try { await waitFor(p, `document.getElementById('toast').textContent.includes('已复制')`, 10000); }
    catch (e) { console.log('toast:', await evaluate(p, `document.getElementById('toast').textContent`), 'calls:', JSON.stringify(calls.map((c) => [c.op, c.name, c.fa])), 'problems:', JSON.stringify(p.problems)); throw e; }
    ok(await evaluate(p, `document.querySelector('.krow[data-name="OPENAI_API_KEY"] .kbtn--copy').dataset.done==='1'`), 'copy: shared visible completed state');
    const got = await evaluate(p, `navigator.clipboard.readText()`);
    ok(got === FAKE_VALUE, 'copy: the value is in the clipboard');
    const c0 = calls.find((c) => c.op === 'copy');
    ok(c0 && c0.name === 'OPENAI_API_KEY' && c0.fa && /^[0-9a-f]{32}$/.test(c0.id) && /^[0-9a-f]{32}$/.test(c0.n), 'copy: keys_op carries page id, nonce and a Face ID assertion');
    ok(!(await evaluate(p, `document.documentElement.outerHTML.includes(${JSON.stringify(FAKE_VALUE)})||JSON.stringify(localStorage).includes(${JSON.stringify(FAKE_VALUE)})`)), 'copy: the value is not in the page or localStorage');
    await save(p, 'light-390x844-copied');
    await evaluate(p, `navigator.clipboard.writeText('')`);
    // a second copy uses the fresh nonce (re-armed from keys_res.n)
    await sleep(400);
    await click(p, '.krow[data-name="ELEVENLABS_API_KEY"] .kbtn--copy');
    await waitFor(p, `navigator.clipboard.readText().then(t=>t===${JSON.stringify(FAKE_VALUE)})`, 10000);
    const c1 = calls.filter((c) => c.op === 'copy')[1];
    ok(c1 && c1.n !== c0.n && c1.id === c0.id, 'second copy: same page, fresh nonce');
    await evaluate(p, `navigator.clipboard.writeText('')`);
    // ---- the clipboard refuses (no ClipboardItem, writeText outside the gesture refused): one more tap within 30 s copies it
    await evaluate(p, `(()=>{window.__CI=window.ClipboardItem;window.ClipboardItem=undefined;const c=navigator.clipboard;window.__wt=c.writeText.bind(c);window.__wtn=0;
      c.writeText=(t)=>{window.__wtn++;return window.__wtn===1?Promise.reject(new DOMException('refused','NotAllowedError')):window.__wt(t)};return 1})()`);
    await sleep(400);
    await click(p, '.krow[data-name="OPENAI_API_KEY"] .kbtn--copy');
    await waitFor(p, `[...document.querySelectorAll('#toast button')].some(b=>b.textContent==='再点一次拷贝')`, 10000);
    ok(await evaluate(p, `import('./js/keys.js').then(m=>m.holding())`) && !(await evaluate(p, `document.documentElement.outerHTML.includes(${JSON.stringify(FAKE_VALUE)})`)), 'refused clipboard: the value waits in memory only, 「再点一次拷贝」 offered');
    await save(p, 'light-390x844-copyagain');
    await click(p, '#toast .undo');
    await waitFor(p, `navigator.clipboard.readText().then(t=>t===${JSON.stringify(FAKE_VALUE)})`, 5000);
    ok(!(await evaluate(p, `import('./js/keys.js').then(m=>m.holding())`)), 'one more tap copies it and drops it');
    await evaluate(p, `navigator.clipboard.writeText('')`);
    await evaluate(p, `window.__wtn=0`); await sleep(300);
    await click(p, '.krow[data-name="OPENAI_API_KEY"] .kbtn--copy');
    await waitFor(p, `import('./js/keys.js').then(m=>m.holding())`, 10000);
    await evaluate(p, `dispatchEvent(new Event('pagehide'))`);
    ok(!(await evaluate(p, `import('./js/keys.js').then(m=>m.holding())`)), 'pagehide wipes a held value');
    await evaluate(p, `(()=>{window.ClipboardItem=window.__CI;navigator.clipboard.writeText=window.__wt;return 1})()`);
    await sleep(300);

    // ---- 删除 on the key in use: strong warning; cancel sends nothing
    await click(p, '.krow[data-name="ANTHROPIC_API_KEY"] .kbtn--delete');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    ok(await evaluate(p, `document.getElementById('confirm-title').textContent.includes('ANTHROPIC_API_KEY')&&document.getElementById('confirm-text').textContent.includes('主 Agent 现在正用这个 Key')`), 'delete in-use key: confirm names it with the strong warning');
    await save(p, 'dark-390x844-delete-inuse'.replace('dark', 'light'));
    await view(p, 390, 844, 'dark'); await save(p, 'dark-390x844-delete-inuse'); await view(p, 390, 844, 'light');
    await click(p, '#confirm-no'); await sleep(300);
    ok(!calls.some((c) => c.op === 'delete'), 'cancel: nothing sent');
    // ---- 删除 a third-party key
    await click(p, '.krow[data-name="ELEVENLABS_API_KEY"] .kbtn--delete');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    ok(!(await evaluate(p, `document.getElementById('confirm-text').textContent.includes('主 Agent')`)), 'delete other key: no in-use warning');
    await click(p, '#confirm-yes');
    await waitFor(p, `document.getElementById('toast').textContent.includes('已删除')`, 10000);
    ok(calls.some((c) => c.op === 'delete' && c.name === 'ELEVENLABS_API_KEY' && c.fa), 'delete: Face ID in the 删除 tap, keys_op delete');
    await waitFor(p, `document.querySelectorAll('#keys-box .krow').length>0`);

    // ---- 修改 / 填写 / 添加 → keys_set
    await click(p, '.krow[data-name="OPENAI_API_KEY"] .kbtn--edit');
    await waitFor(p, `document.getElementById('toast').textContent.includes('密钥卡')`);
    ok(calls.some((c) => c.op === 'set' && c.name === 'OPENAI_API_KEY'), 'edit: keys_set opens the existing key card');
    await click(p, '.krow[data-name="DEEPSEEK_API_KEY"] .kbtn--set');
    await sleep(300);
    ok(calls.some((c) => c.op === 'set' && c.name === 'DEEPSEEK_API_KEY'), 'add on an empty row: keys_set');
    await click(p, '#keys-add');
    await waitFor(p, `!document.querySelector('.kadd').hidden && document.activeElement.id==='keys-add-name'`);
    await evaluate(p, `(()=>{const i=document.getElementById('keys-add-name');i.value='1BAD';i.dispatchEvent(new Event('input'));i.form.requestSubmit()})()`);
    await sleep(150);
    ok(await evaluate(p, `!document.querySelector('.kadd-err').hidden`), 'add: a bad NAME is refused on the phone');
    await view(p, 390, 844, 'dark'); await save(p, 'dark-390x844-add'); await view(p, 390, 844, 'light');
    await key(p, 'Escape'); await sleep(200);
    ok(await evaluate(p, `document.querySelector('.kadd').hidden && document.body.dataset.view==='models'`), 'Esc closes the add form only');
    await click(p, '#keys-add');
    await evaluate(p, `(()=>{const i=document.getElementById('keys-add-name');i.value='WOLFRAM_ALPHA_KEY';i.dispatchEvent(new Event('input'));i.form.requestSubmit()})()`);
    await sleep(300);
    ok(calls.some((c) => c.op === 'set' && c.name === 'WOLFRAM_ALPHA_KEY'), 'add: a new NAME → keys_set');

    // ---- no passkey on this phone
    opts.fa = null;
    keysHandler(fake.st, { fa: null, calls });
    await key(p, 'Escape'); await sleep(300);
    await openModels(p);
    await waitFor(p, `document.querySelectorAll('#keys-box .krow').length>0`); await sleep(200);
    const before = calls.length;
    await click(p, '.krow[data-name="OPENAI_API_KEY"] .kbtn--copy');
    await waitFor(p, `document.getElementById('toast').textContent.includes('Face ID')`);
    ok(await evaluate(p, `[...document.querySelectorAll('#toast button')].some(b=>b.textContent==='开通审批通行密钥')`) && calls.length === before, 'no passkey: approval-passkey setup offered, nothing sent');
    await save(p, 'light-390x844-nopasskey');

    // ---- en
    await evaluate(p, `window.AJLang.set('en')`); lang = 'en'; await sleep(300);
    ok(await evaluate(p, `document.querySelector('#models-view h1').textContent==='Models & API keys' && [...document.querySelectorAll('#keys-box h2')].map(h=>h.textContent).join('|')==='Base models|Third-party API keys'`), 'en: title and sections');
    ok(await evaluate(p, `document.querySelector('.kbtn--copy').title==='Copy' && document.querySelector('.kbtn--set').title==='Add'`), 'en: icon titles');
    await view(p, 390, 844, 'dark'); await save(p, 'en-dark-390x844'); await view(p, 1440, 900, 'light'); await save(p, 'en-light-1440x900'); await view(p, 390, 844, 'light');
    await evaluate(p, `window.AJLang.set('zh')`); lang = 'zh'; await sleep(200);

    // ---- Esc leaves the page
    await evaluate(p, `document.activeElement?.blur()`);
    await key(p, 'Escape'); await sleep(300);
    ok(await evaluate(p, `document.body.dataset.view!=='models'`), 'Esc leaves the page');
    ok(p.problems.length === 0, 'no page errors ' + JSON.stringify(p.problems));
    ok(p.offsite.length === 0, 'nothing off-site ' + JSON.stringify(p.offsite));
    console.log(`p352 chromium: ${n} checks passed; shots → ${OUT.pathname}`);
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
