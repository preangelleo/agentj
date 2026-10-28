#!/usr/bin/env node
// P555 phone leg (p44 `p555-phone`): REAL web client in headless Chromium, synthetic Noise host (fakehost.mjs), a CDP virtual
// platform authenticator standing in for Face ID / fingerprint. Walks, on an iPhone and an Android page (zh) and in English:
//   recognised  pk_state local (a key saved before the upgrade) → 「本机 Face ID：已设好」, no button, nothing asked
//   unplaced    pk_state unknown + self-check → 「用 Face ID 确认」 → get() in the tap → pk_check → 已设好; NO pk_peer_req
//   check fails → the peer request; nobody online + host:1 → 「在 Agent J 主机上批准」 with `agentj faceid approve <code>`
//   transfer    「转移席位到另一台 Agent J 主机」 → steps + notes left-aligned, wrapped lines under their own words
//   pickup      an opened key pickup card: the ⛶ icon borderless, inside the content box's top-right corner
//   icons       no icon-only button on the page has a border (the owner's composer tool row aside)
//   mac (d)     Chrome on a Mac: 「本机 Touch ID：已设好」 ⇔ the approval card's get() goes straight to the platform
//               authenticator (P561 追加 8: transports as create() reported, else none; no hints — was ['internal'] + hints ['client-device']); the phone-QR /
//               security-key route holds is never offered; a borrowed key asks for ['hybrid'] (董事长 10-27 13:16)
// Shots → reports/qa/p555/shots/ (no address bar, no secrets). Usage: node web/test/p555.mjs
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const OUT = process.env.P555_OUT || fileURLToPath(new URL('../../../reports/qa/p555/shots/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const b64u = (b) => Buffer.from(b).toString('base64url');
const checks = [];
const ok = (cond, msg) => { assert.ok(cond, msg); checks.push(msg); };
const IOS = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';

const web = await startWebServer();
web.url = web.url.replace('127.0.0.1', 'localhost');     // WebAuthn wants a host name, not an IP
const fake = await startFakeHost(), B = await launch();

function host(f) {
  const H = { reqs: [], checks: [], checkOk: true };
  f.st.onApp = async (c, m, reply) => {
    if (m.t === 'pk_check') {
      H.checks.push(m);
      await reply({ t: 'pk_check_res', r: m.r, ...(H.checkOk ? { ok: true, s: 'local' } : { ok: false }) });
      if (H.checkOk) await reply({ t: 'pk_state', s: 'local' });
      return true;
    }
    if (m.t === 'pk_peer_req') {
      H.reqs.push(m);
      await reply({ t: 'pk_peer_res', r: m.r, ok: true, mode: 'peer', id: b64u(randomBytes(16)), code: '482913', exp: Math.floor(Date.now() / 1000) + 300, online: 0, host: 1 });
      return true;
    }
    if (m.t === 'pk_peer_cancel') return true;
    if (m.t === 'seat_xfer_state') { await reply({ t: 'seat_xfer_state', r: m.r, state: 'none', expires_at: null }); return true; }
    return false;
  };
  return H;
}

const visible = (sel) => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); return !!e && !e.hidden && !e.closest('[hidden]') && e.getClientRects().length > 0; })()`;
const txt = (sel) => `(document.querySelector(${JSON.stringify(sel)})?.textContent || '')`;
const click = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
async function tapSel(p, sel) {                            // a real pointer tap: WebAuthn wants user activation from it
  const r = await evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.scrollIntoView({ block: 'center' }); const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
}
const tr = (p, key, vars) => evaluate(p, `import('./js/t.js').then(({ t }) => t(${JSON.stringify(key)}, ${JSON.stringify(vars || null)} || undefined))`);
const bio = (p, key) => evaluate(p, `import('./js/pkassist.js').then((m) => m.withBio(${JSON.stringify(key)}))`);
async function snap(p, tag, name, keep = false) {
  if (!keep) await evaluate(p, `import('./js/ui.js').then((m) => m.toastOff())`);
  writeFileSync(`${OUT}${tag}-${name}.png`, await shoot(p, ''));
}

async function phone(ua, scheme, lang) {
  const p = await newPage(B, 390, 844, scheme, { touch: true, ua, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready'); await sleep(400);
  await p.send('WebAuthn.enable', { enableUI: false });
  await p.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await evaluate(p, `(() => { PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = async () => true; window.__cred = [];
    const c = navigator.credentials, g = c.get.bind(c); c.get = (o) => { window.__cred.push('get'); return g(o); }; return true; })()`);
  // the key this remote made before the upgrade (its id is what the Agent J host has on record)
  const id = await evaluate(p, `navigator.credentials.create({ publicKey: { challenge: crypto.getRandomValues(new Uint8Array(32)), rp: { id: location.hostname, name: 'Agent J' },
    user: { id: crypto.getRandomValues(new Uint8Array(16)), name: 'remote', displayName: 'remote' }, pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
    authenticatorSelection: { authenticatorAttachment: 'platform', residentKey: 'required', userVerification: 'required' } } })
    .then((c) => btoa(String.fromCharCode(...new Uint8Array(c.rawId))).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, ''))`);
  return { p, id };
}
const unknown = (id) => ({ t: 'pk_state', s: 'unknown', fa: id, fa_ch: b64u(randomBytes(32)), n: b64u(randomBytes(16)) });
const row = (p) => evaluate(p, `({ label: ${txt('#set-pk-state')}, btn: ${visible('#set-pk-setup')}, btnText: ${txt('#set-pk-setup')}, note: ${txt('#set-pk-note')} })`);

async function walk({ p, id }, H, tag, os) {
  const cue = os === 'ios' ? 'Face ID' : 'Touch ID';   // P559: two names only
  // 1. a key saved before the upgrade: recognised, zero taps
  await fake.send({ t: 'pk_state', s: 'local' });
  await evaluate(p, `import('./js/settings.js').then((m) => m.open())`);
  await waitFor(p, visible('#set-pk-row'));
  await evaluate(p, `document.getElementById('set-pk-row').scrollIntoView({ block: 'center' })`);
  let r = await row(p);
  ok(r.label === await bio(p, 'p539.pk.st.local') && !r.btn, tag + ' recognised key → 「已设好」, no button');
  if (tag.startsWith('zh')) ok(r.label.includes(cue) || os === 'en', tag + ` names ${cue}`);
  await snap(p, tag, '1-recognised');
  // 2. a key the host cannot place: one tap, no other remote
  await fake.send(unknown(id));
  await waitFor(p, `${visible('#set-pk-setup')}`);
  r = await row(p);
  ok(r.btnText === await bio(p, 'p555.pk.check') && r.note === await bio(p, 'p555.pk.unknownNote'), tag + ' unknown → 「用 … 确认」 + why');
  await snap(p, tag, '2-unplaced');
  await tapSel(p, '#set-pk-setup');
  await waitFor(p, `!${visible('#set-pk-setup')}`, 8000);
  ok(H.checks.length === 1 && H.checks[0].n && H.checks[0].fa?.id === id, tag + ' pk_check carries this key\'s assertion');
  ok((await evaluate(p, 'window.__cred')).includes('get'), tag + ' get() ran in the tap');
  ok(H.reqs.length === 0, tag + ' a passing self-check sends NO pk_peer_req');
  ok((await row(p)).label === await bio(p, 'p539.pk.st.local'), tag + ' → 已设好');
  await snap(p, tag, '3-self-check-ok', true);
  // 3. the check fails (key gone) → a new key; nobody online → the Agent J host's terminal
  H.checkOk = false;
  await fake.send(unknown(id));
  await waitFor(p, `${visible('#set-pk-setup')}`);
  await tapSel(p, '#set-pk-setup');
  await waitFor(p, `${visible('#pkp')} && document.getElementById('pkp').dataset.mode === 'wait'`, 8000);
  ok(H.reqs.length === 1, tag + ' failed check → pk_peer_req');
  const sheet = await evaluate(p, txt('#pkp'));
  ok(sheet.includes(await tr(p, 'pkp.waitHostTitle')) && sheet.includes('agentj faceid approve 482913'), tag + ' nobody online → the Agent J host terminal way, with the code');
  ok(!sheet.includes(await tr(p, 'pkp.waitText')), tag + ' does not claim a card went to other remotes');
  await snap(p, tag, '4-wait-host');
  await click(p, '#pkp-cancel');
  await waitFor(p, `!${visible('#pkp')}`);
  // 4. seat transfer: the list
  await evaluate(p, `document.getElementById('set-seat-xfer').scrollIntoView({ block: 'center' })`);
  ok((await evaluate(p, txt('#set-seat-xfer'))).includes(await tr(p, 'sxf.rowNote')), tag + ' row note says Agent J 主机');
  await click(p, '#set-seat-xfer');
  await waitFor(p, `!document.getElementById('confirm').hidden && !!document.querySelector('#confirm-text .sxf-list')`);
  const geo = await evaluate(p, `(() => { const t = document.getElementById('confirm-text'), li = [...t.querySelectorAll('li')];
    const s = getComputedStyle(t), l = getComputedStyle(li[0]);
    // every line of an item starts at the item's text edge: a wrapped item's last line box shares the first line's left
    const lefts = li.map((e) => { const r = document.createRange(); r.selectNodeContents(e); const b = [...r.getClientRects()]; return b.map((x) => Math.round(x.left)); });
    return { align: s.textAlign, pad: l.paddingLeft, n: li.length, ol: t.querySelectorAll('ol li').length, ul: t.querySelectorAll('ul li').length, lefts, wraps: lefts.some((x) => new Set(x).size === 1 && x.length > 1) || lefts.every((x) => new Set(x).size === 1) }; })()`);
  ok(geo.align === 'left' && geo.n === 6 && geo.pad !== '0px' && geo.ol === 3 && geo.ul === 3, tag + ' the sheet is left-aligned: 3 numbered steps + 3 notes');
  ok(geo.lefts.every((x) => new Set(x).size === 1), tag + ' each wrapped line starts under its own item\'s words');
  await snap(p, tag, '5-transfer-explain');
  await click(p, '#confirm-no');
  await evaluate(p, `import('./js/settings.js').then((m) => m.close())`);
  // 5. the key pickup card, opened (董事长 10-27): the ⛶ icon has no border and sits inside the content box's top-right corner
  const sid = randomBytes(16).toString('hex'), val = 'P555 合成夹具：这张卡能用 Face ID 打开。';
  await evaluate(p, `import('./js/secretout.js').then((m) => m.add({ id: ${JSON.stringify(sid)}, n: ${JSON.stringify(randomBytes(16).toString('hex'))}, kind: 'text', name: 'Face ID 测试', purpose: 'P555 合成夹具', size: ${Buffer.byteLength(val)}, ttl: 600 }))`);
  await waitFor(p, `${visible('#sout')}`);
  await evaluate(p, `import('./js/secretout.js').then((m) => m.value({ id: ${JSON.stringify(sid)}, value: ${JSON.stringify(val)} }))`);
  await waitFor(p, `${visible('#sout-expand')} && !!document.querySelector('#sout-content pre')`);
  const ic = await evaluate(p, `(() => { const e = document.getElementById('sout-expand'), s = getComputedStyle(e);
    const b = e.getBoundingClientRect(), box = document.querySelector('#sout-content pre').getBoundingClientRect();
    return { bw: ['Top', 'Right', 'Bottom', 'Left'].map((k) => parseFloat(s['border' + k + 'Width'])), bg: s.backgroundColor, sh: s.boxShadow,
      inside: b.left >= box.left - 0.5 && b.right <= box.right + 0.5 && b.top >= box.top - 0.5 && b.bottom <= box.bottom + 0.5,
      corner: box.right - b.right < 4 && b.top - box.top < 4, textClear: (() => { const r = document.createRange(); r.selectNodeContents(document.querySelector('#sout-content pre')); return [...r.getClientRects()].every((x) => x.right <= b.left + 0.5 || x.top >= b.bottom); })() }; })()`);
  ok(ic.bw.every((w) => w === 0) && /rgba\(0, 0, 0, 0\)|transparent/.test(ic.bg) && ic.sh === 'none', tag + ' pickup ⛶ icon: no border, no fill, no shadow');
  ok(ic.inside && ic.corner, tag + ' pickup ⛶ icon sits inside the content box, top-right');
  ok(ic.textClear, tag + ' the value text never runs under the icon');
  await snap(p, tag, '6-pickup-icon');
  // 6. every icon-only button the page has (shown or not): computed border 0, but the owner's composer tool row (static.test.mjs)
  const framed = await evaluate(p, `[...document.querySelectorAll('button')].filter((e) => {
      const w = (e.textContent || '').replace(/\\s+/g, ''), icon = (!!e.querySelector('svg,img') && !w) || /^[^\\p{L}\\p{N}]{1,2}$/u.test(w);
      if (!icon || e.classList.contains('tool')) return false;
      const s = getComputedStyle(e);
      return ['Top', 'Right', 'Bottom', 'Left'].filter((k) => s['border' + k + 'Style'] !== 'none' && parseFloat(s['border' + k + 'Width']) > 0).length > 1;
    }).map((e) => e.id || e.className)`);
  ok(Array.isArray(framed) && framed.length === 0, tag + ' no icon button has a border: ' + JSON.stringify(framed));
  await click(p, '#sout-done');
}

// 追加 d: the Mac Chrome case. Two virtual authenticators: Touch ID (internal) holds this remote's key, an external one (usb,
// standing in for the phone-QR / security-key route) holds another. The approval card's get() must name where to look.
async function mac() {
  const tag = 'zh-mac-light';
  const p = await newPage(B, 1280, 800, 'light', { ua: MAC, allow: [web.url, fake.relay + '/'] });
  try {
    await navigate(p, fake.newPairing(web.url + '?lang=zh'));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready'); await sleep(400);
    await p.send('WebAuthn.enable', { enableUI: false });
    const opt = (transport) => ({ options: { protocol: 'ctap2', transport, hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
    const ext = (await p.send('WebAuthn.addVirtualAuthenticator', opt('usb'))).authenticatorId;
    await p.send('WebAuthn.setUserVerified', { authenticatorId: ext, isUserVerified: true });
    const mk = (att) => evaluate(p, `navigator.credentials.create({ publicKey: { challenge: crypto.getRandomValues(new Uint8Array(32)), rp: { id: location.hostname, name: 'Agent J' },
      user: { id: crypto.getRandomValues(new Uint8Array(16)), name: 'remote', displayName: 'remote' }, pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
      authenticatorSelection: { authenticatorAttachment: '${att}', residentKey: 'required', userVerification: 'required' } } })
      .then((c) => btoa(String.fromCharCode(...new Uint8Array(c.rawId))).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, ''))`);
    const away = await mk('cross-platform');                       // only the external route has this one
    const tid = (await p.send('WebAuthn.addVirtualAuthenticator', opt('internal'))).authenticatorId;
    const id = await mk('platform');                               // made by this Mac's Touch ID
    ok((await p.send('WebAuthn.getCredentials', { authenticatorId: tid })).credentials.length === 1, tag + ' Touch ID holds this remote\'s key');
    await evaluate(p, `(() => { PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = async () => true; window.__get = [];
      const c = navigator.credentials, g = c.get.bind(c); c.get = (o) => { const k = o.publicKey;
        window.__get.push({ tr: k.allowCredentials.map((x) => x.transports || null), hints: k.hints || null }); return g(o); }; return true; })()`);
    await fake.send({ t: 'pk_state', s: 'local' });
    await evaluate(p, `localStorage.setItem('aj.pk.tr.${id}', 'internal'), true`);   // P561: what register() keeps from getTransports()
    await evaluate(p, `import('./js/settings.js').then((m) => m.open())`);
    await waitFor(p, visible('#set-pk-row'));
    const label = await evaluate(p, txt('#set-pk-state'));
    ok(label === await bio(p, 'p539.pk.st.local') && label.includes('Touch ID'), tag + ' Settings: 「本机 Touch ID：已设好」');
    await evaluate(p, `document.getElementById('set-pk-row').scrollIntoView({ block: 'center' })`);
    await snap(p, tag, '7-mac-touchid-set');
    await evaluate(p, `import('./js/settings.js').then((m) => m.close())`);
    // the approval card (relay sheet 「用 Touch ID 确认」 → api.approve → faceid.approve)
    const ch = b64u(randomBytes(32));
    const got = await evaluate(p, `import('./js/faceid.js').then((m) => m.approve(Uint8Array.from(atob('${ch}'.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - '${ch}'.length % 4) % 4)), (c) => c.charCodeAt(0)), '${id}'))
      .then((fa) => fa.id, (e) => 'ERR ' + e.name)`);
    ok(got === id, tag + ' the card\'s get() is answered by this Mac\'s Touch ID');
    let g = (await evaluate(p, 'window.__get')).at(-1);
    // P561 (追加 8): own key → the transports create() reported on this browser (kept as aj.pk.tr.<id>), else none, and no
    // hints (a Google Password Manager key was not found under a hard-coded ['internal'] + 'client-device')
    ok(JSON.stringify(g.tr) === '[["internal"]]' && g.hints === null, tag + ' own key → the reported transports [internal], no hints: ' + JSON.stringify(g));
    // a key that only the external route holds is NOT offered for an own (local) key — no phone QR / security key detour
    const t0 = Date.now();
    const far = await evaluate(p, `import('./js/faceid.js').then((m) => Promise.race([m.approve(new Uint8Array(32), '${away}').then((fa) => 'USED ' + fa.id, (e) => 'ERR ' + e.name),
      new Promise((r) => setTimeout(() => r('WAIT'), 4000))]))`);
    // P561 (追加 8) supersedes P555 here: with no transports filter the browser looks everywhere this site's key may be kept
    // (Google Password Manager included) — a key on another route may answer; the Agent J host still checks it is THIS key
    ok(far === 'USED ' + away, tag + ' a local-key request, no filter: any route may answer (' + far + ', ' + (Date.now() - t0) + ' ms)');
    // control: the same id asked the pre-fix way (a bare allowCredentials entry) IS answered by the external route — the
    // detour the Mac took to its phone-QR / security-key sheet
    const bare = await evaluate(p, `navigator.credentials.get({ publicKey: { challenge: new Uint8Array(32), rpId: location.hostname, userVerification: 'required', timeout: 8000,
      allowCredentials: [{ type: 'public-key', id: Uint8Array.from(atob('${away}'.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - '${away}'.length % 4) % 4)), (c) => c.charCodeAt(0)) }] } })
      .then(() => 'USED', (e) => 'ERR ' + e.name)`);
    ok(bare === 'USED', tag + ' control: a bare entry (before the fix) goes to the external authenticator (' + bare + ')');
    // a borrowed key (made over a phone scan, #300) still asks the phone route
    await fake.send({ t: 'pk_state', s: 'borrowed' }); await sleep(200);
    const bor = await evaluate(p, `import('./js/faceid.js').then((m) => m.approve(new Uint8Array(32), '${away}').then((fa) => fa.id, (e) => 'ERR ' + e.name))`);
    g = (await evaluate(p, 'window.__get')).at(-1);
    ok(JSON.stringify(g.tr) === '[["hybrid"]]' && JSON.stringify(g.hints) === '["hybrid"]', tag + ' borrowed key → transports [hybrid] + hints [hybrid]');
    ok(typeof bor === 'string', tag + ' borrowed request settles (' + (bor === away ? 'ok' : bor) + ')');
    const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION_REFUSED|127\.0\.0\.1:1|favicon|NotAllowedError/i.test(x));
    assert.deepEqual(problems, [], tag + ' console / CSP clean');
  } finally { await p.dispose(); }
}

const runs = [
  { ua: IOS, os: 'ios', scheme: 'dark', lang: 'zh' },
  { ua: ANDROID, os: 'android', scheme: 'dark', lang: 'zh' },
  { ua: IOS, os: 'en', scheme: 'light', lang: 'en' },
];
try {
  for (const run of runs) {
    fake.reset();
    const H = host(fake);
    const tag = `${run.lang}-${run.os}-${run.scheme}`;
    const ph = await phone(run.ua, run.scheme, run.lang);
    try {
      await walk(ph, H, tag, run.os);
      const problems = ph.p.problems.filter((x) => !/WebSocket|ERR_CONNECTION_REFUSED|127\.0\.0\.1:1|favicon/i.test(x));
      assert.deepEqual(problems, [], tag + ' console / CSP clean');
    } finally { await ph.p.dispose(); }
  }
  fake.reset(); host(fake);
  await mac();
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(`${OUT}result.json`, JSON.stringify({ ok: true, checks, scope: 'Chromium + synthetic Noise host + CDP virtual authenticator; no physical Face ID / WebKit claim' }, null, 2) + '\n');
if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({ suite: 'web/test/p555.mjs', results: { 'p555-phone': 'pass' } }));
console.log('P555 phone PASS', checks.length, 'checks · shots:', OUT);
