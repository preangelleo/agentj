#!/usr/bin/env node
// 0.30.6: Settings → Mail trusted-server checkbox is tickable. Real paired page + virtual passkey; the synthetic host builds
// every view, summary and merge with the REAL host Python (mail.servid_status / servid_hint / action_policy.mail_config) and
// checks each mail_set: the patch shape, the Ed25519 signature over the host-built target, the passkey assertion, and that
// wire.js canonicalJson of that target equals the host's controls.object_text byte for byte. Synthetic mailbox only.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep} from './browser.mjs';
import {b64u, canonicalJson} from '../public/proto/wire.js';
const py = process.env.AJ_PYTHON || execFileSync('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'], {encoding:'utf8'}).trim() + '/../host/.venv/bin/python';
const hostDir = new URL('../../host', import.meta.url).pathname;
let origin = 'http://localhost';
function host(code, value) {
  return JSON.parse(execFileSync(py, ['-I', '-c', `import sys,json;sys.path.insert(0,${JSON.stringify(hostDir)});from agentj import mail,action_policy,passkey,controls\nv=json.load(sys.stdin)\n${code}`],
    {input:JSON.stringify(value), encoding:'utf8', env:{...process.env, AGENTJ_TEST_PASSKEY_ORIGIN:origin}}));
}
// The host's Mail.view() fields this page reads, from the real functions; `auto` = the host's servid_pin record says so.
const VIEW = `c=mail.validate(v['cfg']);h=mail._imap_host(c)
st={'auth_seen':v.get('seen')}
if c['auth_servid']: st['servid_pin']={'host':h,'value':c['auth_servid'],'source':v.get('source') or 'manual'}
print(json.dumps({'config':c,'servid_status':mail.servid_status(c,st),'servid_hint':mail.servid_hint(c),'has_password':False,'today':{},'blocked':[],'presets':mail.preset_list(v['lang'])}))`;
const out = new URL('../../../reports/qa/p0306/mail-servid-toggle/', import.meta.url); mkdirSync(out, {recursive:true});
const web = await startWebServer(); web.url = web.url.replace('127.0.0.1', 'localhost'); origin = new URL(web.url).origin;
const fake = await startFakeHost(), B = await launch(), checks = [];
const hex = () => Buffer.from(webcrypto.getRandomValues(new Uint8Array(16))).toString('hex');
const gmail = host(`print(json.dumps(mail.apply_preset(mail.blank_config(),'gmail','owner@example.com')))`, null);
const custom = {...gmail, preset:'custom', imap:{host:'imap.example.com', port:993, security:'ssl'}, smtp:{host:'smtp.example.com', port:465, security:'ssl'}};
const outlook = {...custom, imap:{host:'outlook.office365.com', port:993, security:'ssl'}};
const box = `(()=>{const b=document.getElementById('mail-trusted-check'),r=document.getElementById('mail-trusted-status').getBoundingClientRect();return {checked:b.checked,disabled:b.disabled,how:document.getElementById('mail-trusted-how').textContent,pinned:document.getElementById('mail-pinned').textContent,gmail:document.getElementById('mail-gmail-pinned').hidden?'':document.getElementById('mail-gmail-pinned').textContent,h:r.height,overflow:document.documentElement.scrollWidth>innerWidth+1}})()`;
try {
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    const tag = `${width}-${theme}-${lang}`;
    fake.reset();
    fake.st.prefs = {appearance:{language:lang, theme}, voice:{speak_replies:false, wake_enabled:false}, agent:{session_mode:'shared'}};
    let v = host(VIEW, {cfg:{...gmail, auth_servid:'mx.google.com'}, source:'auto', lang});
    let pending = null, offers = 0; const sets = [];
    let registered;
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'policy_prepare') {
        offers++; const target = {...m.target, before:structuredClone(v.config)};
        const display = host(`print(json.dumps(action_policy.summary('host_write',v['target'],lang=v['lang'],presentation=True)))`, {target, lang});
        pending = {target, n:hex()};
        await send({t:'policy_ready', r:m.r, ...pending, level:'uv', ...display, fa:registered.id, fa_ch:b64u(new Uint8Array(32).fill(3))}); return true;
      }
      if (m.t === 'mail_get') { await send({t:'mail_res', r:m.r, ok:true, view:v}); return true; }
      if (m.t === 'mail_set') {
        assert.ok(pending, 'mail_set only after a host-built offer'); assert.deepEqual(m.cfg, pending.target.cfg);
        assert.ok(m.authorization && m.authorization.sig && m.authorization.fa, 'signed with Face ID');
        assert.ok(await fake.verifyControl(c, 'host_write', m.authorization, pending.target), 'signature over the host-built target');
        host("rec=passkey.verify_registration(v['registered'],bytes([1])*32);passkey.verify_assertion(rec,passkey.approval_fields(v['fa']),bytes([3])*32);print('true')", {registered, fa:m.authorization.fa});
        assert.equal(canonicalJson(JSON.parse(JSON.stringify(pending.target))), host(`print(json.dumps(controls.object_text('host_write',v)))`, pending.target), 'canonical bytes');
        sets.push(m.cfg);
        const next = host(`print(json.dumps(action_policy.mail_config(v['before'],v['cfg'])))`, {before:v.config, cfg:m.cfg});
        const auto = next.auth_servid && host(`print(json.dumps(mail.servid_autopin({**v['n'],'auth_servid':''},v['o'])==v['n']['auth_servid']))`, {n:next, o:v.config});
        v = host(VIEW, {cfg:next, source:auto ? 'auto' : 'manual', lang}); pending = null;
        await send({t:'mail_res', r:m.r, ok:true, view:v}); return true;
      }
      return false;
    };
    const p = await newPage(B, width, 900, theme, {touch:width === 390, allow:[web.url, fake.relay + '/']});
    const shot = async (name) => { await evaluate(p, `document.getElementById('mail-trusted-status').scrollIntoView({block:'center'})`); await sleep(80); writeFileSync(new URL(`${tag}-${name}.png`, out), await shoot(p, '')); };
    const open = async () => {
      await evaluate(p, `document.getElementById('mail-pinned').textContent='';import('./js/settings.js').then(s=>s.openMail())`);
      await waitFor(p, `!document.getElementById('set-mail').hidden && document.getElementById('mail-pinned').textContent.length>0`);
    };
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await fake.send(theme === 'light' ? {t:'theme', v:1, id:'qa-light', colors:{menu:{background:'#faf9f5', panel:'#f0eee6', text:'#141414', accent:'#141414', line:'#e3dacc'}}} : {t:'theme', v:1, factory:'robots'});
      await evaluate(p, `(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();await document.fonts.ready;document.getElementById('setBtn').click()})()`);
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await evaluate(p, `document.querySelector('[data-set-theme=${JSON.stringify(theme)}]').click()`);
      await waitFor(p, `document.documentElement.dataset.theme===${JSON.stringify(theme)}`);
      await waitFor(p, `!document.getElementById('toast').classList.contains('on')`, 8000);
      await p.send('WebAuthn.enable');
      await p.send('WebAuthn.addVirtualAuthenticator', {options:{protocol:'ctap2', transport:'internal', hasResidentKey:true, hasUserVerification:true, isUserVerified:true, automaticPresenceSimulation:true}});
      registered = await evaluate(p, `import('./js/faceid.js').then(f=>f.register(new Uint8Array(32).fill(1),{relayIndex:2,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32)}))`);

      // 1. A host without the signed mail path (no mailuv1): display only, nothing to tick.
      await open();
      let r = await evaluate(p, box);
      assert.deepEqual([r.checked, r.disabled, r.how], [true, true, ''], 'no mailuv1 → read-only');
      await evaluate(p, `import('./js/session.js').then(s=>s.peer.mailuv1=true)`);
      await open();

      // 2. Pinned Gmail (auto): tickable; the Gmail hint stays.
      r = await evaluate(p, box);
      assert.deepEqual([r.checked, r.disabled], [true, false]); assert.ok(r.h >= 44, 'touch target ≥ 44 px');
      assert.equal(r.gmail, lang === 'zh' ? 'Gmail 已自动固定' : 'Gmail check server pinned automatically');
      assert.ok(r.how.includes(lang === 'zh' ? '取消勾选' : 'Untick')); assert.equal(r.overflow, false);
      await shot('1-pinned');

      // 3. Untick, then Esc on the confirm sheet: no write, box shows the host's state again.
      await evaluate(p, `document.getElementById('mail-trusted-check').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      assert.equal(await evaluate(p, `document.getElementById('mail-trusted-check').checked`), false, 'the pending choice is shown');
      const text = await evaluate(p, `document.getElementById('confirm-text').textContent`);
      assert.match(text, lang === 'zh' ? /可信邮件服务器: mx\.google\.com → 未设置/ : /Trusted mail server: mx\.google\.com → Not set/);
      assert.match(text, lang === 'zh' ? /不自动固定校验服务器: 无 → imap\.gmail\.com/ : /No automatic check-server pin: None → imap\.gmail\.com/);
      if (width === 390) await shot('2-confirm');
      await p.send('Input.dispatchKeyEvent', {type:'keyDown', key:'Escape', code:'Escape', windowsVirtualKeyCode:27});
      await waitFor(p, `document.getElementById('confirm').hidden && !document.getElementById('mail-trusted-check').disabled`);
      assert.equal(await evaluate(p, `document.getElementById('mail-trusted-check').checked`), true, 'cancel restores');
      assert.equal(sets.length, 0);

      // 4. Untick for real: signed {auth_servid:""}; the host records the opt-out.
      await evaluate(p, `document.getElementById('mail-trusted-check').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden`); await evaluate(p, `document.getElementById('confirm-yes').click()`);
      await waitFor(p, `!document.getElementById('mail-trusted-check').disabled && !document.getElementById('mail-trusted-check').checked`);
      assert.deepEqual(sets.at(-1), {auth_servid:''});
      assert.deepEqual([v.config.auth_servid, v.config.servid_optout, v.servid_hint], ['', ['imap.gmail.com'], 'unpinned_known']);
      r = await evaluate(p, box);
      assert.deepEqual([r.checked, r.disabled, r.gmail], [false, false, '']);
      assert.ok(r.how.includes(lang === 'zh' ? '你已取消固定' : 'You removed this pin'));
      await shot('3-unticked');

      // 5. Tick again: signed {servid_optout: [...without imap.gmail.com]}; the host pins its own table value.
      await evaluate(p, `document.getElementById('mail-trusted-check').click()`);
      await waitFor(p, `!document.getElementById('confirm').hidden`);
      assert.match(await evaluate(p, `document.getElementById('confirm-text').textContent`), /mx\.google\.com/);
      await evaluate(p, `document.getElementById('confirm-yes').click()`);
      await waitFor(p, `!document.getElementById('mail-trusted-check').disabled && document.getElementById('mail-trusted-check').checked`);
      assert.deepEqual(sets.at(-1), {servid_optout:[]});
      assert.deepEqual([v.config.auth_servid, v.config.servid_optout, v.servid_status.source], ['mx.google.com', [], 'auto']);
      assert.ok((await evaluate(p, box)).gmail.length > 0, 'Gmail hint back');
      await shot('4-reticked');
      assert.equal(sets.length, 2); assert.equal(offers, 3);

      // 6. Custom (complete sample) and Outlook: never a tick from mail content; a custom pin can still be cleared.
      for (const [name, cfg, seen, want] of [
        ['custom', custom, {checked:5, with_results:5, servid:'sender.example.net'}, lang === 'zh' ? '暂时不能在手机上固定' : "can't be pinned from the phone"],
        ['outlook', outlook, null, 'Outlook']]) {
        v = host(VIEW, {cfg, seen, lang}); await open();
        r = await evaluate(p, box);
        assert.deepEqual([r.checked, r.disabled], [false, true], name); assert.ok(r.how.includes(want), name + ' ' + r.how);
        await evaluate(p, `document.getElementById('mail-trusted-check').click()`); await sleep(150);
        assert.equal(await evaluate(p, `document.getElementById('confirm').hidden`), true, name + ': no sheet');
        await shot(`5-${name}`);
      }
      v = host(VIEW, {cfg:{...custom, auth_servid:'mail.example.com'}, source:'manual', lang}); await open();
      r = await evaluate(p, box); assert.deepEqual([r.checked, r.disabled], [true, false], 'manual custom pin can be cleared');
      assert.equal(sets.length, 2);
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      checks.push(`${tag}: read-only without mailuv1, Esc cancel, signed untick → opt-out, signed tick → host pin, custom/outlook untickable`);
      console.log(`${tag} PASS`);
    } finally { await p.dispose(); }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(new URL('phone.json', out), JSON.stringify({ok:true, checks, scope:'Chromium + virtual passkey; synthetic Noise host, real host merge/summary/canonical text; no physical phone or Gmail claim'}, null, 2) + '\n');
console.log('P0306 mail servid toggle PASS', checks.length);
