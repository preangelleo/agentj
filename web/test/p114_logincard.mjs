#!/usr/bin/env node
// P114 (ADR-A194 §5): the sign-in QR card in a real headless Chromium against the fake host — zh/en × 360/390 × light/dark,
// P87 button audit, no horizontal overflow, the four requests, the Telegram consent tap, expiry → refresh, done → gone,
// and nothing of the QR kept in storage. Shots + results: reports/qa/p114/web/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import { buttonAuditSource } from '../../brand/test/button-audit.mjs';

const out = new URL('../../../reports/qa/p114/web/', import.meta.url); mkdirSync(out, { recursive: true });
const results = {};
const ok = (name) => { results[name] = 'pass'; console.log('PASS ' + name); };

function png(n = 29) {   // a QR-looking test pattern (not a real code)
  const px = 8, w = n * px, rows = [];
  for (let y = 0; y < w; y++) { const r = [0]; for (let x = 0; x < w; x++) { const on = ((Math.floor(x / px) * 7 + Math.floor(y / px) * 3) % 5) < 2; r.push(on ? 0 : 255); } rows.push(Buffer.from(r)); }
  const crc = (b) => { let c = ~0; for (const x of b) { c ^= x; for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1)); } return ~c >>> 0; };
  const chunk = (t, d) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]); };
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(w, 4); ih[8] = 8; ih[9] = 0;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ih), chunk('IDAT', deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]).toString('base64');
}
const IMG = png();
const card = (id, extra = {}) => ({ t: 'login_qr', id, n: 'b'.repeat(32), site: 'mp-weixin', title: '微信公众号', img: IMG, ttl: 120, bind: 'c'.repeat(64), tg: 'text', channel: 'Telegram', ...extra });

const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const got = [];
fake.st.onApp = async (c, m) => { if (String(m.t).startsWith('login_qr')) { got.push(m); return true; } return false; };
let p;
try {
  for (const lang of ['zh', 'en']) for (const [w, h] of [[360, 800], [390, 844]]) for (const scheme of ['light', 'dark']) {
    p = await newPage(B, w, h, scheme, { touch: true, allow: [web.url, fake.relay + '/'] });
    await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    const id = (lang === 'zh' ? 'a' : 'd') + String(w).padEnd(31, '0').slice(0, 31);
    await fake.send(card(id.replace(/[^0-9a-f]/g, '0')));
    await waitFor(p, `!!document.getElementById('lqr') && !document.getElementById('lqr').hidden`, 8000);
    await sleep(150);
    const lay = await evaluate(p, `({over: document.documentElement.scrollWidth > innerWidth + 1, src: document.getElementById('lqr-img').src.slice(0, 22),
      btns: [...document.querySelectorAll('#lqr .lqr-acts .aj-btn')].filter(b => !b.hidden).map(b => Math.round(b.getBoundingClientRect().height)),
      fw: [...document.querySelectorAll('#lqr .lqr-acts .aj-btn')].filter(b => !b.hidden).every(b => b.getBoundingClientRect().width > 200),
      title: document.getElementById('lqr-title').textContent, tg: document.getElementById('lqr-tg').textContent})`);
    assert.equal(lay.over, false); assert.equal(lay.src, 'data:image/png;base64,');
    assert.ok(lay.btns.every((x) => x >= 56), 'buttons ≥ 56 px'); assert.ok(lay.fw, 'full width');
    assert.deepEqual(await evaluate(p, buttonAuditSource), []);
    assert.match(lay.tg, /Telegram/);
    writeFileSync(new URL(`card-${lang}-${w}-${scheme}.png`, out), await shoot(p, ''));
    ok(`layout:${lang}:${w}:${scheme}`);
    if (lang === 'zh' && w === 390 && scheme === 'light') {
      const cid = id.replace(/[^0-9a-f]/g, '0');
      got.length = 0;
      await evaluate(p, `document.getElementById('lqr-check').click()`); await waitFor(p, 'true'); await sleep(300);
      assert.deepEqual(got.map((m) => [m.t, m.id, m.n]), [['login_qr_check', cid, 'b'.repeat(32)]]);
      await fake.send({ t: 'login_qr_state', id: cid, state: 'waiting' }); await sleep(200);
      assert.ok(await evaluate(p, `document.body.textContent.includes(${JSON.stringify('电脑还没看到登录成功')})`)); ok('check:waiting-toast');
      await evaluate(p, `document.getElementById('lqr-tg').click()`); await sleep(300);
      assert.deepEqual([got.at(-1).t, got.at(-1).on], ['login_qr_tg', true]);
      await fake.send({ t: 'login_qr_state', id: cid, state: 'settings', tg: 'qr' }); await sleep(200);
      assert.match(await evaluate(p, `document.getElementById('lqr-tg').textContent`), /不再/); ok('tg:consent-tap');
      writeFileSync(new URL('card-zh-390-tg-on.png', out), await shoot(p, ''));
      await fake.send({ t: 'login_qr_done', id: cid, site: 'mp-weixin', result: 'expired' }); await sleep(200);
      assert.equal(await evaluate(p, `document.getElementById('lqr-frame').hidden && !document.getElementById('lqr-img').getAttribute('src')`), true);
      assert.deepEqual(await evaluate(p, buttonAuditSource), []);
      writeFileSync(new URL('card-zh-390-expired.png', out), await shoot(p, ''));
      await evaluate(p, `document.getElementById('lqr-refresh').click()`); await sleep(300);
      assert.deepEqual([got.at(-1).t, got.at(-1).site], ['login_qr_refresh', 'mp-weixin']); ok('expired:refresh');
      await fake.send(card('e'.repeat(32))); await waitFor(p, `!document.getElementById('lqr-frame').hidden`);
      await evaluate(p, `document.getElementById('lqr-later').click()`); await sleep(300);
      assert.equal(got.at(-1).t, 'login_qr_cancel'); ok('later:cancel');
      await fake.send({ t: 'login_qr_done', id: 'e'.repeat(32), site: 'mp-weixin', result: 'done' }); await sleep(300);
      assert.equal(await evaluate(p, `document.getElementById('lqr').hidden`), true); ok('done:gone');
      const stored = await evaluate(p, `(async()=>{const ls=JSON.stringify(localStorage);const dbs=(await indexedDB.databases()).map(d=>d.name);
        let all='';for(const n of dbs){all+=await new Promise(r=>{const q=indexedDB.open(n);q.onsuccess=()=>{const db=q.result;const names=[...db.objectStoreNames];if(!names.length)return r('');
        const tx=db.transaction(names);let s='';let left=names.length;for(const st of names){const g=tx.objectStore(st).getAll();g.onsuccess=()=>{try{s+=JSON.stringify(g.result)}catch{};if(!--left)r(s)}}};q.onerror=()=>r('')})}
        return (ls+all).includes(${JSON.stringify(IMG.slice(20, 60))})})()`);
      assert.equal(stored, false); ok('storage:no-qr');
    }
    assert.deepEqual(p.problems, []);
    await p.dispose(); p = null;
  }
} finally { if (p) await p.dispose(); await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(new URL('results.json', out), JSON.stringify(results, null, 2) + '\n');
console.log(`p114 web: ${Object.keys(results).length} checks passed`);
if (process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT, JSON.stringify({ suite: 'web/test/p114_logincard.mjs', results }));
