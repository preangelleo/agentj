#!/usr/bin/env node
// P369 · three small layout fixes in a real headless Chromium against the fake host (never :9222).
//   node web/test/p369.mjs → reports/qa/p369/web-{360,390,768,1440}-{light,dark}-*.png
// #80 the input card's new-message badge sits at the card's centre: |badge centre − card centre| ≤ 1 px with 「1」 and
//     「99+」, vertically centred in the header, the label clear of it.
// #77 wide Settings: the side column is navigation only; 「下载 Agent J Brand Kit」 sits under the content column (same left /
//     right edges, below the cards); a phone keeps it last in the one column.
// #81 Settings 「输入框按钮顺序」: the four buttons share one top and one bottom (Δ 0); the shared row audit
//     (brand/test/row-audit.mjs) reports nothing on chat or Settings — and does report a planted `X + X { margin-top }` row
//     (negative control, so the detector is not vacuous).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { rowAuditSource } from '../../brand/test/row-audit.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate as ev, navigate, waitState, waitFor, shoot, sleep } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p369/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const $ = (id) => `document.getElementById('${id}')`;
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let n = 0;
try {
  for (const [w, h] of [[360, 780], [390, 844], [768, 1024], [1440, 900]]) for (const scheme of ['light', 'dark']) {
    const tag = `${w}-${scheme}`, phone = w < 600, lang = scheme === 'dark' ? 'en' : 'zh';
    fake.reset();
    fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: true, session_mode: 'shared' } };
    const p = await newPage(B, w, h, scheme, { touch: phone, allow: [web.url, fake.relay + '/'] });
    const save = async (name) => writeFileSync(OUT + `web-${tag}-${name}.png`, await shoot(p, ''));
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await ev(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await fake.addTurn({ k: 'agent', via: 'task' }, lang === 'zh' ? '后台任务跑完了。' : 'The background task is done.');
      await waitFor(p, `!${$('om')}.hidden && /\\d+ \\/ \\d+/.test(${$('pg')}.textContent)`); await sleep(200);

      // #80 — the badge at the card's centre (its state is set directly: this is a layout check)
      for (const count of ['1', '99+']) {
        await ev(p, `(()=>{${$('om')}.dataset.unread='1';${$('omUnreadN')}.textContent='${count}';return true})()`); await sleep(150);
        const g = await ev(p, `(()=>{const u=document.querySelector('#omUnread .unb').getBoundingClientRect(),c=${$('om')}.getBoundingClientRect(),
          hd=document.querySelector('.omhd').getBoundingClientRect(),pg=${$('pg')}.getBoundingClientRect(),
          lab=[${$('omLabel')},${$('omTime')}].map(e=>e.getBoundingClientRect()).filter(r=>r.width),t={right:Math.max(...lab.map(r=>r.right)),top:Math.min(...lab.map(r=>r.top)),bottom:Math.max(...lab.map(r=>r.bottom))};
          return {dx:(u.left+u.width/2)-(c.left+c.width/2),dy:(u.top+u.height/2)-(hd.top+hd.height/2),w:u.width,
            clearL:t.right<=u.left-2||t.bottom<=u.top||t.top>=u.bottom,clearR:pg.left>=u.right+2,overflow:document.documentElement.scrollWidth>innerWidth}})()`);
        assert.ok(g.w > 0, `${tag}: the badge shows`);
        assert.ok(Math.abs(g.dx) <= 1, `${tag} badge 「${count}」: centre ${g.dx.toFixed(2)} px off the card's centre`);
        assert.ok(Math.abs(g.dy) <= 1, `${tag} badge 「${count}」: vertically centred in the header (${g.dy.toFixed(2)})`);
        assert.ok(g.clearL && g.clearR, `${tag} badge 「${count}」: clear of the label and the page number ${JSON.stringify(g)}`);
        assert.equal(g.overflow, false);
        if (count === '1') await save('badge');
      }
      await ev(p, `(()=>{${$('om')}.dataset.unread='0';return true})()`);
      const chatRows = await ev(p, rowAuditSource);
      assert.deepEqual(chatRows, [], `${tag}: chat rows share a top`);

      // Settings
      await ev(p, `${$('setBtn')}.click()`); await sleep(350);
      // #81 — 「输入框按钮顺序」: one top, one bottom
      await ev(p, `${$('set-tools')}.scrollIntoView({block:'center',behavior:'instant'})`); await sleep(150);
      const ts = await ev(p, `[...document.querySelectorAll('#set-tools > li')].map(li=>{const r=li.getBoundingClientRect(),b=li.querySelector('.tschip').getBoundingClientRect();return [r.top,r.bottom,b.top,b.bottom]})`);
      assert.equal(ts.length, 5, `${tag}: five buttons including full screen`);
      for (const k of [0, 1, 2, 3]) {
        const v = ts.map((x) => x[k]);
        assert.equal(Math.max(...v) - Math.min(...v), 0, `${tag}: button order row ${['li top', 'li bottom', 'chip top', 'chip bottom'][k]} Δ ${JSON.stringify(v)}`);
      }
      await save('toolsort');
      const setRows = await ev(p, rowAuditSource);
      assert.deepEqual(setRows, [], `${tag}: Settings rows share a top`);

      // #77 — Brand Kit under the content column on a wide screen; last in the one column on a phone
      await ev(p, `${$('set-brandkit')}.scrollIntoView({block:'center',behavior:'instant'})`); await sleep(150);
      const k = await ev(p, `(()=>{const a=${$('set-brandkit')}.getBoundingClientRect(),c=document.querySelector('#set-main .setcards').getBoundingClientRect(),
        s=document.querySelector('#set-main .setside'),sr=s.getBoundingClientRect(),main=${$('set-main')};
        const items=[...main.children].filter(x=>x.offsetParent);
        return {l:a.left,r:a.right,cl:c.left,cr:c.right,top:a.top,cb:c.bottom,side:getComputedStyle(s).display!=='none',sr:sr.right,
          inSide:s.contains(${$('set-brandkit')}),last:items[items.length-1].id==='set-brandkit',overflow:main.scrollWidth>main.clientWidth}})()`);
      assert.ok(Math.abs(k.l - k.cl) <= 1 && Math.abs(k.r - k.cr) <= 1, `${tag}: Brand Kit as wide as the content column ${JSON.stringify(k)}`);
      assert.ok(k.top >= k.cb - 1, `${tag}: Brand Kit below the cards`);
      assert.equal(k.inSide, false); assert.equal(k.last, true); assert.equal(k.overflow, false);
      if (w >= 900) { assert.ok(k.side, `${tag}: the side column shows`); assert.ok(k.l > k.sr, `${tag}: Brand Kit right of the side column ${JSON.stringify(k)}`); }
      await save('brandkit');

      // negative control (inside a Settings card, on screen): a planted grid row with a stacking sibling margin is reported
      // (the page's CSP refuses an injected <style>, so the stray sibling margin is set through CSSOM)
      const planted = await ev(p, `(()=>{const g=document.createElement('div');g.id='p369-planted';g.style.cssText='display:grid;grid-template-columns:1fr 1fr;gap:8px';
        for(const t of ['a','b']){const d=document.createElement('div');d.style.height='40px';d.textContent=t;g.append(d)}
        g.lastChild.style.marginTop='24px';document.querySelector('#settings .setcard').prepend(g);
        const r=(${rowAuditSource});g.remove();return r})()`);
      assert.ok(planted.some((x) => x.startsWith('#p369-planted')), `${tag}: the audit reports a planted misaligned row (${JSON.stringify(planted)})`);

      assert.deepEqual(p.problems.filter((x) => !/favicon/.test(x)), []);
      console.log(`PASS P369 ${tag}`);
      n++;
    } finally { await p.dispose(); }
  }
  console.log(`p369: ${n} combinations OK → reports/qa/p369/`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
process.exit(0);
