// P303: the quota rules + usage dialog in the real client against fakehost.mjs, own Chromium (never :9222).
// Four meters (the owner's screenshot: day 1 8 %; fast: day 2 40 %; tight: 85 %; empty: 95 % + 5 h 92 %; and one with no
// reset time) × light / dark × 390 phone / 1280 desktop → reports/qa/p303/*.png + checks.json.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep} from './browser.mjs';
const root = fileURLToPath(new URL('../../../', import.meta.url)), out = root + 'reports/qa/p303/';
mkdirSync(out, {recursive: true});
const D = 86400, now = () => Math.floor(Date.now() / 1000);
const CASES = [
  {name: 'normal', week: {pct: 8, in: 0.4}, h5: {pct: 11, in: 1.2}, ctx: 38, glow: ['', ''], day: 1},
  {name: 'fast', week: {pct: 40, in: 1.5}, h5: {pct: 30, in: 2}, ctx: 52, glow: ['fast', ''], day: 2},
  {name: 'tight', week: {pct: 85, in: 6.3}, h5: {pct: 82, in: 3}, ctx: 81, glow: ['tight', 'tight'], day: 7},
  {name: 'empty', week: {pct: 95, in: 4.2}, h5: {pct: 92, in: 4}, ctx: 93, glow: ['empty', 'empty'], day: 5},
  {name: 'noreset', week: {pct: 40}, h5: {pct: 12}, ctx: 20, glow: ['', ''], day: null},
];
const meter = (c) => ({t: 'meter', model: 'claude-opus-5-5',
  week: {pct: c.week.pct, reset: c.week.in != null ? now() + Math.round((7 - c.week.in) * D) : null},
  h5: {pct: c.h5.pct, reset: c.h5.in != null ? now() + Math.round((5 - c.h5.in) * 3600) : null}, ctx: {pct: c.ctx}});
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const rows = [];
try {
  for (const [w, h] of [[390, 844], [1280, 800]]) for (const theme of ['light', 'dark']) {
    fake.reset();
    fake.st.prefs = {appearance: {language: 'zh', theme}, voice: {speak_replies: false, wake_enabled: false}};
    const p = await newPage(B, w, h, theme, {touch: w < 600, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await fake.send({t: 'status', s: 'idle', agent: 'claude', name: 'Agent J'});
    const dev = await evaluate(p, `import('./js/api.js').then(api=>api.myDeviceId())`);
    await fake.addTurn({k: 'phone', dev, text: '帮我看看这周的额度还够不够。'}, '够用，详情点上面的额度线。');
    await waitFor(p, `document.getElementById('pg').textContent==='1 / 1'`);
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
    for (const c of CASES) {
      await fake.send(meter(c));
      await waitFor(p, `document.querySelector('#mWeek .lab').textContent.includes('${c.week.pct}%')`);
      await sleep(250);
      const g = await evaluate(p, `(()=>{const q=s=>document.querySelector(s);return {wl:q('#mWeek .lab').textContent,hl:q('#m5h .lab').textContent,
        wg:q('#mWeek').dataset.glow||'',hg:q('#m5h').dataset.glow||'',ts:getComputedStyle(q('#mWeek .lab')).textShadow};})()`);
      assert.equal(g.wg, c.glow[0], `${c.name} weekly glow ${w} ${theme}`); assert.equal(g.hg, c.glow[1], `${c.name} 5h glow`);
      if (c.day) assert.ok(g.wl.endsWith(`（第 ${c.day} 天）`), g.wl); else assert.ok(!g.wl.includes('天'), g.wl);
      assert.equal(g.ts === 'none', !c.glow[0], `${c.name} text-shadow ${g.ts}`);
      const tag = `${c.name}-${w}-${theme}`;
      writeFileSync(out + `header-${tag}.png`, await shoot(p, ''));
      await evaluate(p, `document.getElementById('mWeek').click()`);
      await waitFor(p, `!document.getElementById('usageDlg').hidden`);
      await sleep(350);
      const d = await evaluate(p, `(()=>{const sq=document.querySelector('#usageDlg .sq'),om=document.getElementById('om');
        const li=[...document.querySelectorAll('#usageList li')];
        return {sq:sq.getBoundingClientRect().width,om:om.getBoundingClientRect().width,h:sq.getBoundingClientRect().height,vh:innerHeight,
          rows:li.map(l=>({k:l.firstChild.textContent,glow:l.dataset.glow||'',cyc:l.classList.contains('cyc'),
            fill:l.querySelector('.ub i')?getComputedStyle(l.querySelector('.ub i')).backgroundColor:'',sub:l.querySelector('.usub')?.textContent||''}))};})()`);
      assert.ok(Math.abs(d.sq - d.om) < 1.5, `${tag} dialog width ${d.sq} = input card ${d.om}`);
      assert.ok(d.h < d.vh, `${tag} height by content`);
      const wk = d.rows[0], cyc = d.rows.find((r) => r.cyc);
      assert.equal(wk.glow, c.glow[0]);
      const FILL = {fast: 'rgb(199, 222, 220)', tight: 'rgb(235, 201, 183)', empty: 'rgb(217, 119, 87)'};
      if (c.glow[0]) assert.equal(wk.fill, FILL[c.glow[0]], `${tag} weekly bar colour`);
      if (c.day) { assert.ok(cyc && cyc.k === '重置周期' && cyc.sub.includes(`第 ${c.day} 天`) && cyc.sub.includes('重置'), JSON.stringify(cyc)); }
      else assert.ok(!cyc, `${tag} no reset time → no cycle row`);
      const ctxRow = d.rows.at(-1);
      assert.equal(ctxRow.glow, c.ctx >= 90 ? 'empty' : c.ctx >= 80 ? 'tight' : '', `${tag} ctx tier`);
      writeFileSync(out + `dialog-${tag}.png`, await shoot(p, ''));
      rows.push({tag, header: g, dialog: d});
      await evaluate(p, `document.getElementById('usageClose').click()`);
      await waitFor(p, `document.getElementById('usageDlg').hidden`);
    }
    await p.dispose();
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(out + 'checks.json', JSON.stringify({passed: rows.length, rows}, null, 2) + '\n');
console.log('PASS p303 browser: ' + rows.length + ' cases');
