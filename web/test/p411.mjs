#!/usr/bin/env node
// P411 (#172 + #76): the screen-help card in a real headless Chromium against the fake host — zh/en × 360/390 × light/dark for
// the QR card and the screenshot card (P87 button audit, no horizontal overflow, the countdown), then: 「我扫好了」 /「已完成」/
// a typed answer / 「先不处理」 send exactly help_done / help_text / help_cancel; a refused answer (looks like a secret) keeps
// the card; help_end closes it; tap = full screen; nothing of the picture in storage. Shots + results: reports/qa/p411/web/.
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import { buttonAuditSource } from '../../brand/test/button-audit.mjs';

const out = new URL('../../../reports/qa/p411/web/', import.meta.url); mkdirSync(out, { recursive: true });
const results = {};
const ok = (name) => { results[name] = 'pass'; console.log('PASS ' + name); };
const QR = readFileSync(new URL('./fixtures/p411-qr.png', import.meta.url)).toString('base64');
const hex = (c, i) => (c + String(i)).padEnd(32, '0').replace(/[^0-9a-f]/g, '0').slice(0, 32);
const N = 'b'.repeat(32);

const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const got = [];
fake.st.onApp = async (c, m) => { if (String(m.t).startsWith('help_')) { got.push(m); return true; } return false; };

// a realistic "stuck" page: a sign-in screen with a QR code and a captcha, shot by the same Chromium
let SHOT;
{
  const p = await newPage(B, 1280, 800, 'light');
  const html = `<!doctype html><meta charset=utf-8><body style="margin:0;font:16px system-ui;background:#1f2329;color:#eee;display:grid;place-items:center;height:100vh">
    <div style="background:#fff;color:#222;border-radius:16px;padding:32px 48px;text-align:center;width:420px">
    <h2 style="margin:0 0 12px">Sign in</h2><img src="data:image/png;base64,${QR}" width="200" height="200" style="image-rendering:pixelated">
    <p>Scan with the app, or enter the code shown below</p>
    <div style="font:700 34px/1 monospace;letter-spacing:10px;background:repeating-linear-gradient(45deg,#ddd 0 6px,#f6f6f6 6px 12px);padding:12px;border-radius:8px">7KQ2X</div>
    <input placeholder="Verification code" style="margin-top:14px;width:100%;height:40px;font-size:16px"></div></body>`;
  await navigate(p, 'data:text/html;base64,' + Buffer.from(html).toString('base64'));
  SHOT = (await shoot(p, '')).toString('base64');
  await p.dispose();
}
const qrCard = (id, extra = {}) => ({ t: 'help_card', id, n: N, kind: 'qr', mime: 'image/png', img: QR, text: '微信公众号后台登录要扫码',
  reply: 'done', by: 'agent', from: 'agent', ttl: 118, ...extra });
const screenCard = (id, extra = {}) => ({ t: 'help_card', id, n: N, kind: 'screen', mime: 'image/png', img: SHOT,
  text: '登录页要验证码：图里的验证码是什么？', reply: 'text', by: 'auto', from: 'nightly-report', ttl: 296, ...extra });

let p;
try {
  let i = 0;
  for (const lang of ['zh', 'en']) for (const [w, h] of [[360, 800], [390, 844]]) for (const scheme of ['light', 'dark']) {
    p = await newPage(B, w, h, scheme, { touch: true, allow: [web.url, fake.relay + '/'] });
    await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    for (const [kind, mk] of [['qr', qrCard], ['screen', screenCard]]) {
      const id = hex(kind === 'qr' ? 'a' : 'c', i++);
      await fake.send(mk(id));
      await waitFor(p, `!!document.getElementById('hlp') && !document.getElementById('hlp').hidden && document.getElementById('hlp-img').complete`, 8000);
      await sleep(200);
      await waitFor(p, `[...document.querySelectorAll('#hlp .hlp-acts .aj-btn, #hlp .hlp-form .aj-btn')].filter(b => b.offsetParent).every(b => b.getBoundingClientRect().height >= 56)`, 5000).catch(() => {});
      const lay = await evaluate(p, `({over: document.documentElement.scrollWidth > innerWidth + 1, src: document.getElementById('hlp-img').src.slice(0, 22),
        kind: document.getElementById('hlp').dataset.kind, left: document.getElementById('hlp-left').textContent,
        form: !document.getElementById('hlp-form').hidden, img: document.getElementById('hlp-img').getBoundingClientRect().width,
        btns: [...document.querySelectorAll('#hlp .hlp-acts .aj-btn, #hlp .hlp-form .aj-btn')].filter(b => b.offsetParent).map(b => Math.round(b.getBoundingClientRect().height)),
        kicker: document.getElementById('hlp-kicker').textContent})`);
      assert.equal(lay.over, false); assert.equal(lay.src, 'data:image/png;base64,'); assert.equal(lay.kind, kind);
      assert.ok(lay.btns.length && lay.btns.every((x) => x >= 56), 'buttons ≥ 56 px ' + lay.btns);
      assert.match(lay.left, lang === 'zh' ? /^\d:\d\d 内有效$/ : /^Valid for \d:\d\d$/);
      assert.equal(lay.form, kind === 'screen');
      assert.ok(lay.img >= (kind === 'qr' ? 200 : w - 120), 'a big picture ' + lay.img);
      assert.match(lay.kicker, kind === 'qr' ? (lang === 'zh' ? /来自 Agent/ : /From the Agent/) : /nightly-report/);
      assert.deepEqual(await evaluate(p, buttonAuditSource), []);
      writeFileSync(new URL(`${kind}-${lang}-${w}-${scheme}.png`, out), await shoot(p, ''));
      ok(`layout:${kind}:${lang}:${w}:${scheme}`);
      if (lang === 'zh' && w === 390 && scheme === 'light') {
        if (kind === 'qr') {
          const l0 = await evaluate(p, `document.getElementById('hlp-left').textContent`); await sleep(2100);
          assert.notEqual(await evaluate(p, `document.getElementById('hlp-left').textContent`), l0); ok('countdown:ticks');
          got.length = 0;
          await evaluate(p, `document.getElementById('hlp-done').click()`); await sleep(300);
          assert.deepEqual(got.map((m) => [m.t, m.id, m.n]), [['help_done', id, N]]); ok('qr:scanned');
        } else {
          await evaluate(p, `document.getElementById('hlp-frame').click()`); await sleep(250);
          assert.equal(await evaluate(p, `document.getElementById('hlp').classList.contains('zoom') && getComputedStyle(document.getElementById('hlp-frame')).position`), 'fixed');
          writeFileSync(new URL('screen-zh-390-zoom.png', out), await shoot(p, ''));
          await evaluate(p, `document.getElementById('hlp-frame').click()`); await sleep(200); ok('screen:tap-zoom');
          got.length = 0;
          await evaluate(p, `(()=>{const i=document.getElementById('hlp-input');i.value='  7KQ2X ';i.dispatchEvent(new Event('input'));document.getElementById('hlp-send').click()})()`); await sleep(300);
          assert.deepEqual(got.map((m) => [m.t, m.id, m.n, m.text]), [['help_text', id, N, '7KQ2X']]);
          await fake.send({ t: 'help_err', id, why: 'secret' }); await sleep(250);
          assert.equal(await evaluate(p, `!document.getElementById('hlp').hidden && !document.getElementById('hlp-send').disabled`), true);
          assert.ok(await evaluate(p, `document.body.textContent.includes('这看起来像密码或密钥')`));
          writeFileSync(new URL('screen-zh-390-refused.png', out), await shoot(p, '')); ok('screen:secret-refused-card-stays');
          await fake.send(screenCard(hex('e', 1))); await sleep(250);
          assert.match(await evaluate(p, `document.getElementById('hlp-more').textContent`), /还有 1 张/); ok('queue:more');
          got.length = 0;
          await evaluate(p, `document.getElementById('hlp-later').click()`); await sleep(300);
          assert.deepEqual(got.map((m) => m.t), ['help_cancel']); ok('screen:later-cancel');
          await fake.send({ t: 'help_end', id, result: 'cancelled' }); await sleep(250);
          await fake.send({ t: 'help_end', id: hex('e', 1), result: 'page_changed' }); await sleep(250);
        }
      }
      await fake.send({ t: 'help_end', id, result: 'done' }); await sleep(250);
      assert.equal(await evaluate(p, `document.getElementById('hlp').hidden && !document.getElementById('hlp-img').getAttribute('src')`), true);
    }
    if (lang === 'zh' && w === 390 && scheme === 'light') {
      ok('end:gone');
      const stored = await evaluate(p, `(async()=>{const ls=JSON.stringify(localStorage)+JSON.stringify(sessionStorage);const dbs=(await indexedDB.databases()).map(d=>d.name);
        let all='';for(const n of dbs){all+=await new Promise(r=>{const q=indexedDB.open(n);q.onsuccess=()=>{const db=q.result;const names=[...db.objectStoreNames];if(!names.length)return r('');
        const tx=db.transaction(names);let s='';let left=names.length;for(const st of names){const g=tx.objectStore(st).getAll();g.onsuccess=()=>{try{s+=JSON.stringify(g.result)}catch{};if(!--left)r(s)}}};q.onerror=()=>r('')})}
        return [${JSON.stringify(QR.slice(40, 80))}, ${JSON.stringify(SHOT.slice(400, 440))}, '7KQ2X'].some(x => (ls+all).includes(x))})()`);
      assert.equal(stored, false); ok('storage:no-picture-no-answer');
    }
    assert.deepEqual(p.problems, []);
    await p.dispose(); p = null;
  }
} finally { if (p) await p.dispose(); await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(new URL('results.json', out), JSON.stringify(results, null, 2) + '\n');
console.log(`p411 web: ${Object.keys(results).length} checks passed`);
if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({ suite: 'web/test/p411.mjs', results }));
