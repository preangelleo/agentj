// P131 (Leo 10-11): the first-run checklist never blocks the chat — real page + encrypted local fake host.
// Phone 360/390 and desktop 1440, zh/en, light/dark. A host snapshot (show:true) only shows a thin banner above the chat: no
// dialog, nothing over the input, the chat still sends. The banner opens the checklist as its own page (sticky "do it on the
// computer" note, where-tag per row, the host's fact / effect, "use my existing browser" first); "use it" sends a setup_mark
// whose Ed25519 signature verifies over (browser, adopt_browser, revision); ← goes back to chat; the banner's ✕ hides it at once
// and sends setup_close; the chat input still sends; the menu's 「首次准备清单」 reopens it (setup_get open:true).
// Touch targets ≥ 56 px on the page, ≥ 44 px on the banner; no horizontal overflow; no console errors.
// Own temporary Chromium (browser.mjs), closed in finally; screenshots → reports/qa/p131/screens.
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import * as wire from '../../protocol/wire.js';

const FIX = JSON.parse(readFileSync(new URL('./fixtures/p131-setup.json', import.meta.url), 'utf8'));
const OUT = resolve(process.env.P131_SCREENS || 'reports/qa/p131/screens');
const $ = (id) => `document.getElementById('${id}')`;
const clone = (o) => JSON.parse(JSON.stringify(o));
const visible = (id) => `(() => { const n = ${$(id)}; if (!n || n.hidden) return false; const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(n).visibility !== 'hidden'; })()`;
// the topmost element at the centre of the input must be the input itself (nothing covers it)
const inputFree = `(() => { const n = ${$('input')}; const r = n.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return !!top && (top === n || n.contains(top) || top.closest('label.field') === n.closest('label.field')); })()`;

function adopted(snap) {
  const s = clone(snap);
  s.revision += 2;
  const b = s.items.find((i) => i.id === 'browser');
  Object.assign(b, { choice: 'selected', state: 'verified', reason: null, explain: null });
  s.browser_source = { kind: 'adopted', port: 9222, running: true };
  s.action = { item: 'browser', choice: 'adopt_browser', state: 'done', result: 'adopted', port: 9222, at: 1 };
  const g = s.items.find((i) => i.id === 'gmail');
  Object.assign(g, { state: 'waiting_local', reason: 'site_signin_required', explain: {
    fact: { zh: '没通过：你登记的浏览器（端口 9222）里还没登录 Gmail 网页登录。', en: 'Not passed: Gmail website sign-in is not signed in in the browser you registered (port 9222).' },
    effect: { zh: '只影响要用 Gmail 网页登录 的事，其他照常。', en: 'Only tasks that use Gmail website sign-in wait; everything else works.' },
    next: ['open_login', 'check'] } });
  s.current = 'gmail';
  s.progress = { done: s.progress.done + 1, total: s.progress.total + 1 };
  return s;
}

async function run() {
  mkdirSync(OUT, { recursive: true });
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  const facts = [];
  try {
    for (const lang of ['zh', 'en']) for (const [w, h] of [[360, 780], [390, 844], [1440, 900]]) for (const theme of ['light', 'dark']) {
      fake.reset();
      let snap = clone(FIX);
      const marks = [], closes = [], gets = [];
      fake.st.onApp = async (c, m, send) => {
        if (m.t === 'setup_close') { closes.push(m); snap = { ...clone(snap), closed: true, revision: snap.revision + 1 }; await send({ t: 'setup_card', r: m.r, show: false, ...snap }); return true; }
        if (m.t === 'setup_get') {
          gets.push(m);
          if (m.open === true) snap = { ...clone(snap), closed: false, revision: snap.revision + 1 };
          await send({ t: 'setup_card', r: m.r, show: false, ...snap }); return true;
        }
        if (m.t !== 'setup_mark') return false;
        const dev = fake.devs.find((d) => d.id === c.devId);
        const msg = await wire.controlMessage(fake.channel, c.devId, 'setup_mark', m.n, m.ts, { item: m.item, choice: m.choice, rev: m.rev });
        const key = await webcrypto.subtle.importKey('raw', wire.unb64u(dev.sk), { name: 'Ed25519' }, false, ['verify']);
        const ok = await webcrypto.subtle.verify({ name: 'Ed25519' }, key, wire.unb64u(m.sig), msg) && m.rev === snap.revision;
        marks.push({ item: m.item, choice: m.choice, rev: m.rev, ok });
        await send({ t: 'ctl_res', r: m.r, action: 'setup_mark', ok, ...(ok ? {} : { why: 'bad_signature' }) });
        if (ok && m.choice === 'adopt_browser') { snap = adopted(snap); await fake.send({ t: 'setup_card', show: true, ...snap }); }
        return true;
      };
      const touch = w < 600;
      const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
      const tag = `${lang}-${w}-${theme}`;
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `if(${$('a2hs')} && !${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
        // 1. a pushed snapshot = a banner only; the chat stays the screen and its input is free
        await fake.send({ t: 'setup_card', show: true, ...snap });
        await waitFor(p, visible('setup-banner'));
        assert.equal(await evaluate(p, visible('setup-view')), false, tag + ' never opens the page by itself');
        assert.equal(await evaluate(p, `document.querySelectorAll('[aria-modal="true"]:not([hidden])').length`), 0, tag + ' no dialog');
        assert.equal(await evaluate(p, inputFree), true, tag + ' nothing covers the input');
        const want = lang === 'zh' ? `首次准备 ${FIX.progress.done}/${FIX.progress.total} · 继续` : `First-run setup ${FIX.progress.done}/${FIX.progress.total} · Continue`;
        assert.equal(await evaluate(p, `${$('setup-banner-open')}.textContent`), want, tag);
        for (const id of ['setup-banner-open', 'setup-banner-x']) {
          const r = await evaluate(p, `(() => { const r = ${$(id)}.getBoundingClientRect(); return { w: r.width, h: r.height }; })()`);
          assert.ok(r.h >= 44 && r.w >= 44, `${tag} ${id} ${JSON.stringify(r)}`);
        }
        writeFileSync(resolve(OUT, `banner-${tag}.png`), await shoot(p, ''));
        // 2. the banner opens the page: where-tags, the fixed note, the host's fact, "use my existing browser" first
        await evaluate(p, `${$('setup-banner-open')}.click()`);
        await waitFor(p, `${visible('setup-view')} && ${$('setup-title')} && ${$('setup-title')}.textContent.length > 0`);
        assert.equal(await evaluate(p, `${$('setup-title')}.textContent`), lang === 'zh' ? '专用浏览器' : 'Agent browser', tag);
        assert.equal(await evaluate(p, `${$('setup-sticky')}.textContent`), lang === 'zh'
          ? '这些步骤在Agent J 主机上做；手机上看进度。卡住了直接在聊天里告诉我。' : 'Do these steps on the Agent J host; watch progress on the phone. Stuck? Just tell me in the chat.');
        assert.match(await evaluate(p, `${$('setup-where')}.textContent`), lang === 'zh' ? /^在Agent J 主机上做 · / : /^On the Agent J host · /);
        assert.match(await evaluate(p, `${$('setup-fact')}.textContent`), /9222/);
        assert.match(await evaluate(p, `${$('setup-src')}.textContent`), /9222/);
        assert.equal(await evaluate(p, `document.querySelector('#setup-main .setup-actions .aj-btn').id`), 'setup-adopt-browser', tag + ' adopt first');
        assert.equal(await evaluate(p, `!!${$('setup-install-browser')} && !!${$('setup-unused')} && !!${$('setup-later')}`), true, tag);
        assert.equal(await evaluate(p, `document.querySelectorAll('#setup-main .setup-row').length`), FIX.items.length, tag + ' every row listed');
        assert.equal(await evaluate(p, `document.querySelectorAll('#setup-main .setup-row .setup-tag').length > 0`), true, tag + ' where-tags');
        for (const id of ['setup-adopt-browser', 'setup-install-browser', 'setup-unused', 'setup-later']) {
          const r = await evaluate(p, `(() => { const r = ${$(id)}.getBoundingClientRect(); return { w: r.width, h: r.height }; })()`);
          assert.ok(r.h >= 56 && r.w >= 44, `${tag} ${id} ${JSON.stringify(r)}`);
        }
        assert.equal(await evaluate(p, 'document.documentElement.scrollWidth > innerWidth'), false, tag + ' overflow');
        writeFileSync(resolve(OUT, `page-${tag}.png`), await shoot(p, ''));
        // 3. "use my existing browser" → a signed mark the fake host verifies → the Gmail row says which browser, open & sign in
        await evaluate(p, `${$('setup-adopt-browser')}.click()`);
        await waitFor(p, `${$('setup-main')}.dataset.revision === '${FIX.revision + 2}'`);
        assert.deepEqual(marks, [{ item: 'browser', choice: 'adopt_browser', rev: FIX.revision, ok: true }], tag);
        assert.match(await evaluate(p, `${$('setup-fact')}.textContent`), /9222/);
        assert.equal(await evaluate(p, `document.querySelector('#setup-main .setup-actions .aj-btn').id`), 'setup-open-login', tag);
        assert.match(await evaluate(p, `${$('setup-src')}.textContent`), lang === 'zh' ? /你已有的浏览器（端口 9222）/ : /your own browser \(port 9222\)/);
        writeFileSync(resolve(OUT, `adopted-${tag}.png`), await shoot(p, ''));
        // 4. ← back to chat: the banner is there; ✕ hides it at once and tells the host; the chat still sends
        await evaluate(p, `document.querySelector('#setup-view [data-back]').click()`);
        await waitFor(p, `!${visible('setup-view')} && ${visible('setup-banner')}`);
        await evaluate(p, `${$('setup-banner-x')}.click()`);
        assert.equal(await evaluate(p, visible('setup-banner')), false, tag + ' hidden at once (same tick)');
        for (let k = 0; k < 40 && !closes.length; k++) await sleep(50);
        assert.equal(closes.length, 1, tag + ' setup_close sent');
        assert.equal(await evaluate(p, inputFree), true, tag);
        const before = fake.st.says.length;
        await evaluate(p, `(() => { const n = ${$('input')}; n.focus(); n.value = 'P131 chat after close'; n.dispatchEvent(new Event('input', { bubbles: true })); })()`);
        await waitFor(p, `!${$('send')}.disabled`);
        await evaluate(p, `${$('send')}.click()`);
        for (let k = 0; k < 60 && fake.st.says.length === before; k++) await sleep(50);
        assert.equal(fake.st.says.at(-1)?.text, 'P131 chat after close', tag + ' the chat works');
        await sleep(200);
        assert.equal(await evaluate(p, visible('setup-banner')), false, tag + ' stays closed');
        // 4b. 追加2: on this device ✕ is for good — a newer revision, a host that says closed:false, show:true: still hidden
        snap = { ...clone(snap), closed: false, revision: snap.revision + 1 };
        await fake.send({ t: 'setup_card', show: true, ...snap });
        await sleep(200);
        assert.equal(await evaluate(p, visible('setup-banner')), false, tag + ' a later push never brings it back');
        assert.equal(await evaluate(p, visible('setup-view')), false, tag + ' nor opens the page');
        assert.equal(await evaluate(p, `localStorage.getItem('aj.setup.hidden')`), '1', tag + ' remembered on this device');
        writeFileSync(resolve(OUT, `closed-${tag}.png`), await shoot(p, ''));
        // 5. the menu reopens it (setup_get open:true) — as the page, and the banner is back in the chat
        await evaluate(p, `${$('setBtn')}.click()`);   // P171: the ≡ menu's rows live in Settings
        await waitFor(p, visible('open-setup'));
        await evaluate(p, `${$('open-setup')}.click()`);
        await waitFor(p, `${visible('setup-view')} && ${$('setup-main')}.dataset.revision === '${snap.revision}'`);
        assert.equal(gets.filter((g) => g.open === true).length, 1, tag + ' reopen asked the host');
        assert.equal(await evaluate(p, `localStorage.getItem('aj.setup.hidden')`), null, tag + ' the owner\'s own tap reopens it here');
        await evaluate(p, `${$('setup-close')}.click()`);              // the page's own ✕: closes and goes back to the chat
        await waitFor(p, `!${visible('setup-view')} && !${visible('setup-banner')}`);
        for (let k = 0; k < 40 && closes.length < 2; k++) await sleep(50);
        assert.equal(closes.length, 2, tag + ' page ✕ closes too');
        // 6. 追加3 (Leo 10:01 「断开恢复以后就会继续给我弹窗」): a dropped link, a reload, another device — never shown again.
        //    The host keeps "closed" (first_run journal); on ready it re-sends that closed snapshot (worst case: show:true too).
        const ready = { ...clone(snap), closed: true };
        await fake.kick();
        await waitState(p, 'ready');
        await fake.send({ t: 'setup_card', show: true, ...ready });
        await sleep(300);
        assert.equal(await evaluate(p, `${visible('setup-banner')} || ${visible('setup-view')}`), false, tag + ' not after a reconnect');
        await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
        await fake.send({ t: 'setup_card', show: true, ...ready });
        await sleep(300);
        assert.equal(await evaluate(p, `${visible('setup-banner')} || ${visible('setup-view')}`), false, tag + ' not after a reload');
        await evaluate(p, `localStorage.removeItem('aj.setup.hidden')`);   // another device: only the host's flag
        await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
        await fake.send({ t: 'setup_card', show: true, ...ready });
        await sleep(300);
        assert.equal(await evaluate(p, `${visible('setup-banner')} || ${visible('setup-view')}`), false, tag + ' not on another device');
        assert.deepEqual(p.problems.filter((x) => !/favicon/.test(x)), [], tag + ' console');
        facts.push({ tag, ok: true });
      } finally {
        await p.dispose();
      }
    }
  } finally {
    await B.close(); await fake.stop(); await web.stop();
  }
  writeFileSync(resolve(OUT, 'validation.json'), JSON.stringify({ cases: facts.length, facts }, null, 1) + '\n');
  console.log(`PASS p131 setup page: ${facts.length} viewport/language/theme cases`);
}
run().catch((e) => { console.error(e); process.exit(1); });
