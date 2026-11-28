// Actual paired Web App and Chromium virtual CTAP against a local host
// fixture whose Ed25519 and WebAuthn proof verifiers are real.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { appendFileSync, copyFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PUBLIC_DIR, startWebServer } from './serve.mjs';
import { foreground, requireLotion } from './lotion-foreground.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, rect } from './browser.mjs';
import { b64u, controlObject, sha256hex } from '../../protocol/wire.js';
import { verifyBindingAssertion } from '../public/js/ownergrant.js';
import { elevateChallenge } from '../public/js/faceid.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/p131-setup.json', import.meta.url), 'utf8'));
const out = process.env.AJ_CREATOR_OUT || 'reports/qa/browser-creator-grants';
mkdirSync(out, {recursive: true});
const baseline = process.env.AJ_CREATOR_BASELINE_PUBLIC;
const B = await launch();
const facts = [];
const selector = '#setup-browser-grants';
const scratch = mkdtempSync(join(tmpdir(), 'aj-creator-preview-'));
const previewPublic = join(scratch, 'public');
const preview = fileURLToPath(new URL('../creator-preview/', import.meta.url));

function installPreview() {
  cpSync(PUBLIC_DIR, previewPublic, {recursive: true});
  execFileSync('git', ['apply', '--whitespace=nowarn', join(preview, 'public-fixture.patch')], {cwd: previewPublic});
  copyFileSync(join(preview, 'browser-grants.js'), join(previewPublic, 'js/browser-grants.js'));
  appendFileSync(join(previewPublic, 'app.css'), '\n' + readFileSync(join(preview, 'browser-grants.css'), 'utf8'));
}

async function click(p, sel) {
  await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`);
  const {x, y} = await rect(p, sel);
  for (const type of ['mousePressed', 'mouseReleased']) {
    await p.send('Input.dispatchMouseEvent', {type, x, y, button: 'left', clickCount: 1});
  }
}

async function virtualPasskey(p) {
  await p.send('WebAuthn.enable');
  await p.send('WebAuthn.addVirtualAuthenticator', {options: {protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true}});
  return evaluate(p, `navigator.credentials.create({publicKey: {
    challenge: crypto.getRandomValues(new Uint8Array(32)), rp: {name: 'Local fixture'},
    user: {id: new Uint8Array([1]), name: 'fixture', displayName: 'Fixture'},
    pubKeyCredParams: [{type: 'public-key', alg: -7}],
    authenticatorSelection: {authenticatorAttachment: 'platform', residentKey: 'required', userVerification: 'required'}
  }}).then(c => ({id:c.id, alg:-7, rp:location.hostname,
    spki: btoa(String.fromCharCode(...new Uint8Array(c.response.getPublicKey()))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'')}))`);
}

async function run(theme, kind, width = 390) {
  const prior = kind === 'baseline', enabled = kind === 'preview';
  const publicDir = prior ? resolve(baseline) : enabled ? previewPublic : PUBLIC_DIR;
  const web = await startWebServer({publicDir});
  web.url = web.url.replace('127.0.0.1', 'localhost');
  const fake = await startFakeHost();
  let entry, offer, active, submissions = 0, challenges = 0;
  const snap = {...structuredClone(fixture), browser_source: {kind: 'own', backend: 'ego-lite'},
    creator_site_grants: !prior};
  const b = snap.items.find(row => row.id === 'browser');
  Object.assign(b, {choice: 'selected', state: 'verified', reason: null, explain: null});
  fake.st.prefs = {appearance: {theme, language: 'zh'}, voice: {speak_replies: false}};
  fake.st.onApp = async (c, m, send) => {
    const ctl = (body) => send({t: 'ctl_res', r: m.r, action: m.t, ...body});
    if (m.t === 'setup_get') {
      await send({t: 'setup_card', r: m.r, show: true, ...snap});
      return true;
    }
    if (m.t === 'browser_grants') {
      await ctl({ok: true, available: true, grants: active ? [active] : []});
      return true;
    }
    if (m.t === 'browser_grant_prepare') {
      assert.deepEqual([m.site, m.account, m.minutes], ['bilibili', '1234', 15]);
      offer = {v: 1, id: 'ab'.repeat(16), site: m.site, account: m.account, origin: 'https://member.bilibili.com',
        actions: ['archive_status'], expires: Math.floor(Date.now() / 1000) + 15 * 60,
        profile: 'Agent J', backend: 'ego-lite', host_binding: 'ab'.repeat(32)};
      await ctl({ok: true, target: offer});
      return true;
    }
    if (m.t === 'browser_grant') {
      assert.deepEqual(m.target, offer);
      assert.equal(await fake.verifyControl(c, m.t, m, offer), true);
      const digest = await sha256hex(new TextEncoder().encode(controlObject(m.t, offer)));
      const challenge = await elevateChallenge(fake.channel, c.devId, m.n, m.t, m.n, digest);
      if (!m.fa) {
        challenges++;
        await ctl({ok: false, why: 'passkey_required', fa: entry.id, fa_ch: b64u(challenge), fa_s: 'local'});
        return true;
      }
      assert.ok(await verifyBindingAssertion(entry, m.fa, challenge, {rp: 'localhost', origin: new URL(web.url).origin}));
      submissions++;
      active = offer;
      await ctl({ok: true, grant: active});
      return true;
    }
    if (m.t === 'browser_grant_revoke') {
      assert.equal(m.id, active.id);
      assert.ok(await fake.verifyControl(c, m.t, m, {id: m.id}));
      active = null;
      await ctl({ok: true});
      return true;
    }
    return false;
  };

  const p = await newPage(B, width, width === 390 ? 844 : 900, theme, {touch: width === 390, allow: [web.url, fake.relay + '/']});
  try {
    await navigate(p, fake.newPairing(web.url + '?lang=zh'));
    await waitState(p, 'awaiting-approval');
    await fake.approve();
    await waitState(p, 'ready');
    await evaluate(p, `import('./js/settings.js').then(m=>m.applyThemeLocal(${JSON.stringify(theme)}))`);
    await evaluate(p, "document.getElementById('a2hs-ok')?.click()");
    await fake.send({t: 'setup_card', show: true, ...snap});
    await waitFor(p, "!document.getElementById('setup-banner').hidden");
    await evaluate(p, "document.getElementById('setup-banner-open').click()");
    await waitFor(p, "!document.getElementById('setup-view').hidden && !!document.getElementById('setup-src')");
    if (!enabled) {
      assert.equal(await evaluate(p, `!!document.querySelector('${selector}')`), false);
      assert.equal((await fetch(new URL('js/browser-grants.js', web.url))).status, 404);
      assert.equal(fake.log.some(({t}) => t?.startsWith('browser_grant')), false);
      if (prior || !baseline) {
        await evaluate(p, "document.getElementById('setup-src').scrollIntoView({block:'start'})");
        writeFileSync(join(out, 'before-390-' + theme + '.png'), await shoot(p));
      }
      return;
    }
    await waitFor(p, `!!document.querySelector('${selector} button')`);
    entry = await virtualPasskey(p);
    await evaluate(p, `document.querySelector('${selector} input').value = '1234';`);
    const formInk = await foreground(p, selector);
    requireLotion(formInk, 'site grant form');
    await click(p, selector + ' button');
    await waitFor(p, "!document.getElementById('confirm').hidden");
    const visible = await evaluate(p, "document.getElementById('confirm-text').textContent");
    assert.match(visible, /https:\/\/member\.bilibili\.com/);
    assert.match(visible, /1234/);
    assert.match(visible, /读取稿件状态/);
    await waitFor(p, `document.getElementById('confirm').dataset.popupSystem === 'grow' &&
      ['confirm-no','confirm-yes'].every(k =>
        getComputedStyle(document.getElementById(k)).backgroundColor === 'rgb(250, 249, 245)')`);
    const approvalInk = await foreground(p, '#confirm');
    requireLotion(approvalInk, 'site grant approval');
    writeFileSync(join(out, `approval-${width}-${theme}.png`), await shoot(p));
    await click(p, '#confirm-no');
    await waitFor(p, "document.getElementById('confirm').hidden");
    assert.equal(submissions, 0);
    await click(p, selector + ' button');
    await waitFor(p, "!document.getElementById('confirm').hidden");
    await click(p, '#confirm-yes');
    await waitFor(p, `document.querySelector('${selector} .browser-grant-fact')?.textContent.includes('1234')`);
    assert.equal(submissions, 1);
    const colors = await evaluate(p, `Array.from(document.querySelectorAll('${selector} button'),
      b=>({bg:getComputedStyle(b).backgroundColor,h:b.getBoundingClientRect().height}))`);
    assert.deepEqual(colors, Array(2).fill({bg: 'rgb(250, 249, 245)', h: 56}));
    assert.equal(await evaluate(p, "document.documentElement.scrollWidth > innerWidth"), false);
    await evaluate(p, `document.querySelector('${selector}').scrollIntoView({block:'center'})`);
    const grantedInk = await foreground(p, selector);
    requireLotion(grantedInk, 'site grant and receipt');
    writeFileSync(join(out, `after-${width}-${theme}.png`), await shoot(p));
    await click(p, selector + ' .browser-grant-list button');
    await waitFor(p, `!document.querySelector('${selector} .browser-grant-fact')`);
    assert.equal(active, null);
    assert.ok(challenges >= 2);
    assert.deepEqual(p.offsite, []);
    assert.equal(await evaluate(p, "document.documentElement.dataset.theme"), theme);
    const revokedInk = await foreground(p, selector);
    requireLotion(revokedInk, 'site grant revoke receipt');
    facts.push({theme, width, cancelled: true, uv: true, revoked: true, colors, formInk, approvalInk, grantedInk, revokedInk});
  } finally {
    await p.dispose();
    await fake.stop();
    await web.stop();
  }
}
try {
  installPreview();
  for (const theme of ['light', 'dark']) {
    if (baseline) await run(theme, 'baseline');
    await run(theme, 'shipped');
    for (const width of [390, 1440]) await run(theme, 'preview', width);
  }
  writeFileSync(join(out, 'journey.json'), JSON.stringify(facts, null, 2));
  console.log('PASS paired Chromium 390/1440px light/dark setup, exact signed displayed object, UV cancel, real virtual assertion, access list and revoke.');
  console.log('PASS shipped public tree omits the creator panel and asset even when a paired host advertises it.');
} finally {
  await B.close();
  rmSync(scratch, {recursive: true, force: true});
}
