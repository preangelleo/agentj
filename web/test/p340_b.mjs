// P340 B — the real page (own headless Chromium + the JS fake host, real Noise) for Leo's 10-16 items:
//   #38 reply-card icons: right-packed, glyph gap = the last glyph's right margin (±1 px), 44 px targets, larger left gap
//   #41 header 扫码 · 搜索 · 设置: drawn gaps = the gear's drawn right margin on ≤ 390 px (±1 px), unchanged from 600 px
//   #35 a sent message with 5 quotes: one folded row (height of one quote row ±1 px), tap opens all 5, tap folds again
//   #21 reading an older page: sending, the Agent's reply and a new message leave the page and scrollTop where they were
//       (the page number pulses); from the newest page a send still follows to the new page
// zh / en × light / dark × 350 (fold cover) / 360 / 390 / 690 (fold inside) / 1440. Shots → reports/qa/p340/b/.
// P340_PUBLIC=<dir> serves another public/ (e.g. the HEAD tree) and only measures + shoots into before/ (修前 shots).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';

const BEFORE = !!process.env.P340_PUBLIC;
const OUT = 'reports/qa/p340/b' + (BEFORE ? '/before' : ''); mkdirSync(OUT, { recursive: true });
const WIDTHS = (process.env.P340_WIDTHS || '350,360,390,690,1440').split(',').map(Number);
const web = await startWebServer(BEFORE ? { publicDir: process.env.P340_PUBLIC } : {}), fake = await startFakeHost(), B = await launch();
const results = [];
const geom = `(() => {
  const vis = (e) => e && e.getClientRects().length && getComputedStyle(e).display !== 'none';
  const ink = (b) => { const svg = [...b.querySelectorAll('svg')].find(vis); const r = svg.getBoundingClientRect(), bb = svg.getBBox(), k = r.width / 24, h = (parseFloat(svg.getAttribute('stroke-width')) || 0) / 2;
    return { l: r.left + (bb.x - h) * k, r: r.left + (bb.x + bb.width + h) * k, box: [r.left, r.right], btn: (() => { const q = b.getBoundingClientRect(), a = getComputedStyle(b, '::after'), hw = a.content !== 'none' && a.position === 'absolute' ? Math.max(q.width, parseFloat(a.width) || 0) : q.width, c = (q.left + q.right) / 2;
      return { width: q.width, height: q.height, l: q.left, r: q.right, hitL: c - hw / 2, hitR: c + hw / 2, hitW: hw }; })() }; };
  const rm = document.getElementById('rm').getBoundingClientRect(), wr = document.getElementById('webReportBtn').getBoundingClientRect();
  const acts = [...document.querySelectorAll('#rmIcons .act')].filter((b) => !b.hidden && vis(b)).map((b) => {
    const svg = [...b.querySelectorAll('svg')].find(vis), s = svg.getBoundingClientRect(), r = b.getBoundingClientRect(), a = getComputedStyle(b, '::after');
    const cx = s.left + s.width / 2, cy = s.top + s.height / 2, hit = document.elementFromPoint(cx, cy);
    return { id: b.id, l: s.left, r: s.right, w: r.width, h: r.height, hitW: parseFloat(a.width), hitH: parseFloat(a.height), own: !!hit && hit.closest('button') === b };
  });
  const top = ['scanBtn', 'srchBtn', 'setBtn'].map((id) => document.getElementById(id)).filter(vis).map(ink);
  const meta = document.getElementById('meta'), scan = document.getElementById('scanBtn').getBoundingClientRect();
  return { iw: document.documentElement.clientWidth, rmRight: rm.right, wrRight: wr.right, acts, top,
    room: scan.left - document.getElementById('orb').getBoundingClientRect().right, metaW: vis(meta) ? meta.getBoundingClientRect().width : 0 };
})()`;
const pg = (p) => evaluate(p, `document.getElementById('pg').textContent.replace(/\\s+/g,' ').trim()`);
const omq = `(() => { const v = (e) => !!(e && e.getClientRects().length && getComputedStyle(e).display !== 'none');
  const f = document.getElementById('omQuoteFold'), q = document.getElementById('omQuote'), more = document.getElementById('omQuoteMore');
  return { fold: v(f), foldH: v(f) ? f.getBoundingClientRect().height : 0, foldText: v(f) ? f.textContent : '', expanded: f ? f.getAttribute('aria-expanded') : null,
    q: v(q), qH: v(q) ? q.getBoundingClientRect().height : 0, more: v(more) ? [...more.children].filter(v).length : 0,
    om: document.getElementById('om').getBoundingClientRect().height }; })()`;

async function seed(){
  fake.reset(); fake.st.caps = ['contacts', 'tgfwd', 'mq5']; fake.st.tgfwd = true;
  fake.st.onApp = async (c, m, send) => { if (m.t !== 'ct_list') return false; await send({ t: 'ct_res', r: m.r, ok: true, contacts: [], count: 0, me: ['owner@example.com'], mail: { ready: true } }); return true; };
  const A = [];
  for (let i = 1; i <= 6; i++) A.push(await fake.addTurn({ k: 'agent' }, i === 3 ? Array.from({ length: 60 }, (_, j) => `第 ${j + 1} 行：早上从第一条未读开始读。`).join('\n') : `第 ${i} 条回复：董事长，这一段是第 ${i} 段要点。`));
  const qt = (t) => ({ id: t.id, who: 'Agent J', ts: t.ts, text: t.reply.text.slice(0, 80) });
  await fake.addTurn({ k: 'phone', dev: 'other', name: 'Phone', text: '我这是测试最多的 5 段引用，看看发出去以后多高。', quote: { ...qt(A[0]), more: A.slice(1, 5).map(qt) } }, '5 段引用我这边都完整收到了。');
  await fake.addTurn({ k: 'phone', dev: 'other', name: 'Phone', text: '只引用一段。', quote: qt(A[5]) }, '收到这一段。');
}

try {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const w of WIDTHS) {
    const tag = `${lang}-${scheme}-${w}`, h = w >= 1000 ? 900 : 800;
    await seed();
    const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `document.getElementById('a2hs-ok')?.click(); document.getElementById('phone-env-dismiss')?.click(); if (!document.getElementById('confirm').hidden) document.getElementById('confirm-no').click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 30, max: 100 }, h5: { pct: 16, reset: 0 }, week: { pct: 33, reset: 0 }, at: 0 });
      await waitFor(p, `document.getElementById('pg').textContent.replace(/\\s/g,'')==='8/8' && !document.getElementById('mailBtn').hidden`);
      await sleep(300);
      // ---- #38 + #41 on the newest page
      const g = await evaluate(p, geom), r = { tag };
      const a = g.acts, gaps = a.slice(1).map((x, i) => x.l - a[i].r), right = g.rmRight - a.at(-1).r, lead = a[0].l - g.wrRight;
      r.footer = { gaps, right, lead, n: a.length };
      const tg = g.top, tgaps = tg.slice(1).map((x, i) => x.l - tg[i].r), tm = g.iw - tg.at(-1).r;
      r.header = { gaps: tgaps, margin: tm, room: g.room, metaW: g.metaW, btn: tg.map((x) => [x.btn.width, x.btn.height]), hit: tg.map((x) => [x.btn.hitW, x.btn.height]),
        boxGaps: tg.slice(1).map((x, i) => x.btn.l - tg[i].btn.r), hitGaps: tg.slice(1).map((x, i) => x.btn.hitL - tg[i].btn.hitR) };
      // the same page with the P340 header margins taken off = the old spacing, for the 让出的空间 number
      r.header.roomOld = await evaluate(p, `(() => { const b = ['scanBtn','srchBtn','setBtn'].map(i => document.getElementById(i)), m = b.map(x => [x.style.marginLeft, x.style.width]); b.forEach(x => { x.style.marginLeft = ''; x.style.width = ''; });
        const s = document.getElementById('scanBtn').getBoundingClientRect().left - document.getElementById('orb').getBoundingClientRect().right; b.forEach((x, i) => { x.style.marginLeft = m[i][0]; x.style.width = m[i][1]; }); return s; })()`);
      const single = await evaluate(p, omq); r.single = single;
      writeFileSync(`${OUT}/${tag}-newest.png`, await shoot(p, ''));
      if (!BEFORE){
        assert.equal(a.length, 6, tag + ' six icons');
        for (const x of gaps) assert.ok(Math.abs(x - right) <= 1, `${tag} #38 glyph gap ${x.toFixed(2)} = right margin ${right.toFixed(2)}`);
        assert.ok(lead >= right - 1, `${tag} #38 the left gap (${lead.toFixed(1)}) is at least the group gap`);
        if (w >= 350) assert.ok(lead > right + 8, `${tag} #38 the left gap grows (${lead.toFixed(1)} vs ${right.toFixed(1)})`);
        for (const x of a){ assert.ok(x.own, `${tag} ${x.id} owns its glyph centre`); assert.ok(x.h >= 44 && x.hitW >= 44 && x.hitH >= 44, `${tag} ${x.id} 44 px target`); }
        // P340 返工 (p302): boxes never overlap (≥ 8 px apart), every button a 44 × 44 target, targets never overlap
        for (const [hw, bh] of r.header.hit) assert.ok(hw >= 44 && bh >= 44, tag + ' #41 header targets ≥ 44 × 44 ' + JSON.stringify(r.header.hit));
        assert.ok(r.header.btn[0][0] >= 44 && r.header.btn.at(-1)[0] >= 44, tag + ' #41 扫码 and 设置 keep full 44 px boxes (P190)');
        for (const x of r.header.boxGaps) assert.ok(x >= 8 - 0.05, `${tag} #41 header boxes ≥ 8 px apart (${x.toFixed(2)})`);
        for (const x of r.header.hitGaps) assert.ok(x >= -0.05, `${tag} #41 header targets never overlap (${x.toFixed(2)})`);
        if (w <= 390) for (const x of tgaps) assert.ok(Math.abs(x - tm) <= 1, `${tag} #41 drawn gap ${x.toFixed(2)} = gear margin ${tm.toFixed(2)}`);
        if (w <= 390) assert.ok(r.header.room > r.header.roomOld + 4, `${tag} #41 room for the model pill grows (${r.header.roomOld.toFixed(1)} → ${r.header.room.toFixed(1)})`);
        if (w >= 600) assert.ok(Math.abs(r.header.room - r.header.roomOld) < 0.5, `${tag} #41 wide screens unchanged`);
        assert.equal(single.fold, false, tag + ' #35 one quote: no fold row ' + JSON.stringify(single));
        // Settings moves the three buttons into its own header: no P340 width / margin travels with them, and back again
        await evaluate(p, `document.getElementById('setBtn').click()`); await waitFor(p, `!document.getElementById('settings').hidden`); await sleep(250);
        const inSet = await evaluate(p, `['scanBtn','srchBtn','setBtn'].map(i => { const b = document.getElementById(i); return b.parentElement.id + '|' + b.style.width + '|' + b.style.marginLeft + '|' + b.classList.contains('tfit'); })`);
        assert.deepEqual(inSet, ['set-actions|||false', 'set-actions|||false', 'set-actions|||false'], tag + ' #41 hands off in Settings ' + JSON.stringify(inSet));
        await key(p, 'Escape', { code: 'Escape' }); await waitFor(p, `document.getElementById('settings').hidden`); await sleep(250);
        const back = await evaluate(p, geom), bg = back.top.slice(1).map((x, i) => x.l - back.top[i].r);
        if (w <= 390) for (const x of bg) assert.ok(Math.abs(x - tm) <= 1, `${tag} #41 refit after Settings (${x.toFixed(2)})`); assert.ok(single.q, tag + ' one quote shows as before');
      }
      // ---- #35 the 5-quote page
      await evaluate(p, `document.getElementById('pgPrev').click()`); await waitFor(p, `document.getElementById('pg').textContent.replace(/\\s/g,'')==='7/8'`); await sleep(200);
      const c1 = await evaluate(p, omq); r.fold = { collapsed: c1 };
      writeFileSync(`${OUT}/${tag}-quotes-folded.png`, await shoot(p, ''));
      if (!BEFORE){
        assert.ok(c1.fold && !c1.q && c1.more === 0, tag + ' #35 folded: one row, the five rows hidden');
        assert.ok(Math.abs(c1.foldH - single.qH) <= 1, `${tag} #35 folded row ${c1.foldH} = one quote row ${single.qH}`);
        assert.match(c1.foldText, lang === 'zh' ? /引用 5 段.*第 1 条回复.*\+4/ : /5 quotes.*\+4/);
        await evaluate(p, `document.getElementById('omQuoteFold').click()`); await sleep(150);
        const c2 = await evaluate(p, omq); r.fold.open = c2;
        assert.ok(c2.q && c2.more === 4 && c2.expanded === 'true', tag + ' #35 a tap shows all five');
        assert.ok(c2.om > c1.om + 3 * single.qH, `${tag} #35 open is taller (${c1.om} → ${c2.om})`);
        writeFileSync(`${OUT}/${tag}-quotes-open.png`, await shoot(p, ''));
        await evaluate(p, `document.getElementById('omQuoteFold').click()`); await sleep(150);
        const c3 = await evaluate(p, omq);
        assert.ok(c3.fold && !c3.q && Math.abs(c3.om - c1.om) <= 1, tag + ' #35 another tap folds again');
        // the owner card opened (…) shows every quote, as before — no fold row there
        await evaluate(p, `document.getElementById('omMore').click()`); await sleep(200);
        const c4 = await evaluate(p, omq);
        assert.ok(!c4.fold && c4.q && c4.more === 4, tag + ' #35 the opened card lists all five ' + JSON.stringify(c4));
        await evaluate(p, `document.getElementById('omMore').click()`); await sleep(200);
        assert.ok((await evaluate(p, omq)).fold, tag + ' #35 closed again: folded');
      }
      // ---- #21 reading page 3 (a long reply), scrolled into it
      for (let i = 0; i < 4; i++){ await evaluate(p, `document.getElementById('pgPrev').click()`); await sleep(80); }
      await waitFor(p, `document.getElementById('pg').textContent.replace(/\\s/g,'')==='3/8'`); await sleep(250);
      await evaluate(p, `document.getElementById('main').scrollTop = 140`); await sleep(150);
      const st0 = await evaluate(p, `document.getElementById('main').scrollTop`);
      const says = fake.st.says.length;
      await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ${JSON.stringify(lang === 'zh' ? '读到这里先回一句' : 'A reply while I read')}; i.dispatchEvent(new Event('input', {bubbles: true})); document.getElementById('send').click(); })()`);
      if (BEFORE){ await waitFor(p, `/\\/9$/.test(document.getElementById('pg').textContent.replace(/\\s/g,''))`, 8000); await sleep(400); r.read = { pg: await pg(p) }; writeFileSync(`${OUT}/${tag}-stay-after-send.png`, await shoot(p, '')); }
      else await waitFor(p, `document.getElementById('pg').textContent.replace(/\\s/g,'')==='3/9'`, 8000);
      if (!BEFORE){
        assert.equal(fake.st.says.length, says + 1, tag + ' #21 the message went');
        await waitFor(p, `document.getElementById('pgCorner').dataset.unread === '1'`, 4000);   // the echo reply landed → pulse
        assert.equal(await pg(p), '3 / 9', tag + ' #21 still page 3 after send + reply');
        const st1 = await evaluate(p, `document.getElementById('main').scrollTop`);
        assert.ok(st0 > 0 && Math.abs(st1 - st0) <= 1, `${tag} #21 scrollTop kept (${st0} → ${st1})`);
        writeFileSync(`${OUT}/${tag}-stay-after-send.png`, await shoot(p, ''));
        await fake.addTurn({ k: 'agent' }, lang === 'zh' ? '又来一条新消息。' : 'One more new message.');
        await waitFor(p, `document.getElementById('pg').textContent.replace(/\\s/g,'')==='3/10'`);
        assert.equal(await evaluate(p, `document.getElementById('pgCorner').dataset.unread`), '1', tag + ' #21 a new message: page number pulses');
        const st2 = await evaluate(p, `document.getElementById('main').scrollTop`);
        assert.ok(Math.abs(st2 - st0) <= 1, `${tag} #21 scrollTop kept after a new message (${st0} → ${st2})`);
        r.read = { st0, st1, st2 };
        // from the newest page a send still follows to the new page
        await evaluate(p, `document.getElementById('pgLatest').click()`);
        await waitFor(p, `document.getElementById('pg').textContent.replace(/\\s/g,'')==='10/10'`);
        await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ${JSON.stringify(lang === 'zh' ? '最新页发一句' : 'Sent from the newest page')}; i.dispatchEvent(new Event('input', {bubbles: true})); document.getElementById('send').click(); })()`);
        await waitFor(p, `document.getElementById('pg').textContent.replace(/\\s/g,'')==='11/11'`, 8000);
        writeFileSync(`${OUT}/${tag}-newest-send.png`, await shoot(p, ''));
        assert.deepEqual(p.problems.filter((x) => !/favicon|ERR_|Failed to load resource/.test(x)), [], tag + ' no page errors');
      }
      results.push({ ...r, pass: !BEFORE });
      console.log('ok', tag, JSON.stringify({ footer: { gap: gaps[0]?.toFixed(1), right: right.toFixed(1), lead: lead.toFixed(1) }, header: { gaps: tgaps.map((x) => x.toFixed(1)), m: tm.toFixed(1), room: [r.header.roomOld.toFixed(1), r.header.room.toFixed(1)] }, fold: c1.foldH, single: single.qH }));
    } finally { await p.dispose(); }
  }
  writeFileSync(`${OUT}/results.json`, JSON.stringify({ physical: false, before: BEFORE, results }, null, 1) + '\n');
  console.log(`P340 B ${BEFORE ? 'before shots' : 'PASS'}`, results.length);
} finally { await B.close(); await fake.stop(); await web.stop(); }
