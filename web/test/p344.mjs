#!/usr/bin/env node
// P344 (backlog #52) · the shortcut sheet's search on the real page + local fake host, own headless Chromium (never :9222).
//   keys-search   ? opens the sheet; the magnifier opens one field; typing filters the rows by description, keys as written
//                 or drawn, gesture text — zh and en alike, case-insensitive; no match = one line; Esc clears + closes the
//                 field, the next Esc closes the sheet; / and Ctrl+K go straight into the field (not chat search), the field
//                 itself still types "/", a layer above the sheet keeps its keys; Settings → 键盘快捷键 has the same search.
// Shots (dark, zh + en × 390 + 1440) → agentjarvis/reports/qa/p344/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, key, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'agentjarvis/reports/qa/p344/';
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/p344.mjs');
let web, fake, B, checks = 0, ok = true;
const ev = evaluate;
const rows = (p) => ev(p, `[...document.querySelectorAll('#keysCols dt')].map(d => d.dataset.ix)`);
const shown = (p, id) => ev(p, `!document.getElementById(${JSON.stringify(id)}).hidden`);
const type = async (p, text) => { await ev(p, `(() => { const i = document.getElementById('keysFindInput'); i.value = ''; i.focus(); return true; })()`); await p.send('Input.insertText', { text }); await sleep(80); };
const check = (v, label) => { assert.ok(v, label); checks++; };
try {
  web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  fake = await startFakeHost();
  B = await launch();
  for (const lang of ['zh', 'en']) for (const w of [390, 1440]) {
    fake.reset();
    const p = await newPage(B, w, w === 390 ? 844 : 900, 'dark', { allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await ev(p, `if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
      await ev(p, `document.activeElement && document.activeElement.blur()`);
      await key(p, '?', { code: 'Slash', shift: true, keyCode: 191 });
      await waitFor(p, `!document.getElementById('keys').hidden`);
      const all = await rows(p);
      check(all.length > 40, 'the full sheet');
      check(!(await shown(p, 'keysFind')), 'search starts closed');
      const btn = await ev(p, `(() => { const b = document.getElementById('keysFindBtn').getBoundingClientRect(), h = document.querySelector('#keys .menuhead').getBoundingClientRect(), t = document.getElementById('keysTitle').getBoundingClientRect(); return { right: h.right - b.right, gap: b.left - t.right, w: b.width, h: b.height, top: b.top - h.top }; })()`);
      check(btn.w >= 44 && btn.h >= 44, 'magnifier is a 44 px target');
      check(btn.right >= 0 && btn.right <= 40, 'magnifier at the right edge of the header: ' + btn.right);
      check(btn.gap >= 8, 'magnifier keeps a gap from the title: ' + btn.gap);

      // tap the magnifier → field, focused
      await ev(p, `document.getElementById('keysFindBtn').click()`);
      check(await shown(p, 'keysFind'), 'magnifier opens the field');
      check(await ev(p, `document.activeElement.id === 'keysFindInput' && document.getElementById('keysFindBtn').getAttribute('aria-expanded') === 'true'`), 'field focused, button expanded');
      const pad = await ev(p, `parseFloat(getComputedStyle(document.getElementById('keysFindInput')).paddingLeft)`);
      check(pad >= 8, 'text kept off the field edge: ' + pad);

      // description, both languages, case, keys as written / drawn, gesture text
      for (const [q, id] of [['截取', 'app.screenshot'], ['CAPTURE THIS', 'app.screenshot'], ['alt+shift+c', 'app.screenshot'], ['长按齿轮', 'app.screenshot'],
        ['hold the gear', 'app.screenshot'], ['⌥⇧c', 'app.screenshot'], ['Ctrl+K', 'app.search'], ['cmd k', 'app.search'], ['esc', 'keys.search_close']]) {
        await type(p, q);
        const got = await rows(p);
        check(got.includes(id), `"${q}" finds ${id} (${lang})`);
        check(got.length < all.length, `"${q}" filters`);
        check(!(await shown(p, 'keysNone')), 'no "no match" line while rows match');
      }
      await type(p, '截取');
      if (lang === 'zh') writeFileSync(OUT + `zh-${w}-search.png`, await shoot(p));
      await type(p, 'capture');
      if (lang === 'en') writeFileSync(OUT + `en-${w}-search.png`, await shoot(p));
      // no match
      await type(p, 'zzqx 不存在的');
      check((await rows(p)).length === 0, 'nothing matches');
      check(await shown(p, 'keysNone'), 'no-match line');
      check(await ev(p, `document.getElementById('keysNone').textContent`) === (lang === 'zh' ? '没找到相关快捷键，换个词试试。' : 'No matching shortcuts. Try another word.'), 'no-match text');
      writeFileSync(OUT + `${lang}-${w}-none.png`, await shoot(p));
      // Esc: clears + closes the field, the sheet stays; Esc again closes the sheet
      await key(p, 'Escape');
      check(!(await shown(p, 'keysFind')) && (await shown(p, 'keys')), 'first Esc closes the search only ' + JSON.stringify(await ev(p, `({find: !document.getElementById('keysFind').hidden, keys: !document.getElementById('keys').hidden, view: document.body.dataset.view, act: document.activeElement.id})`)));
      check((await rows(p)).length === all.length && !(await shown(p, 'keysNone')), 'every row back');
      check(await ev(p, `document.getElementById('keysFindInput').value === ''`), 'query cleared');
      await key(p, 'Escape');
      check(!(await shown(p, 'keys')), 'second Esc closes the sheet');

      // / and Ctrl+K go straight into the field; the field types "/"; chat search stays shut
      await key(p, '?', { code: 'Slash', shift: true, keyCode: 191 });
      await waitFor(p, `!document.getElementById('keys').hidden`);
      await key(p, '/', { code: 'Slash', keyCode: 191 });
      check(await ev(p, `!document.getElementById('keysFind').hidden && document.activeElement.id === 'keysFindInput' && document.getElementById('keysFindInput').value === ''`), '/ opens the field without typing');
      check(await ev(p, `document.getElementById('input').value === ''`), '/ did not go to the message box');
      await key(p, '/', { code: 'Slash', keyCode: 191 });
      check(await ev(p, `document.getElementById('keysFindInput').value === '/'`), 'the field itself still types /');
      await key(p, 'Escape'); await ev(p, `document.getElementById('keysClose').focus()`);
      await key(p, 'k', { code: 'KeyK', ctrl: true });
      check(await ev(p, `!document.getElementById('keysFind').hidden && document.activeElement.id === 'keysFindInput'`), 'Ctrl+K opens the field');
      check(await ev(p, `document.getElementById('srch').hidden`), 'Ctrl+K did not open chat search');
      await key(p, 'Escape');
      // tapping the magnifier again closes it
      await ev(p, `document.getElementById('keysFindBtn').click()`); await type(p, 'reader');
      await ev(p, `document.getElementById('keysFindBtn').click()`);
      check(!(await shown(p, 'keysFind')) && (await rows(p)).length === all.length, 'magnifier again closes + restores');
      // a layer above the sheet keeps its keys
      await ev(p, `document.getElementById('confirm').hidden = false; document.getElementById('keysClose').focus()`);
      await key(p, '/', { code: 'Slash', keyCode: 191 });
      check(!(await shown(p, 'keysFind')), '/ is not taken over a confirm layer');
      await ev(p, `document.getElementById('confirm').hidden = true`);
      await key(p, 'Escape');
      check(!(await shown(p, 'keys')), 'sheet closed');
      // outside the sheet nothing changed: / still opens the command menu draft, ⌘K still the chat search
      await ev(p, `document.activeElement && document.activeElement.blur()`);
      await key(p, 'k', { code: 'KeyK', ctrl: true });
      check(await ev(p, `!document.getElementById('srch').hidden`), 'Ctrl+K outside the sheet = chat search');
      await key(p, 'Escape');
      // Settings → 键盘快捷键: the same sheet, the same search
      await ev(p, `document.getElementById('setBtn').click()`);
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await ev(p, `document.getElementById('keysBtn').click()`);
      await waitFor(p, `!document.getElementById('keys').hidden`);
      await ev(p, `document.getElementById('keysFindBtn').click()`); await type(p, lang === 'zh' ? '翻页' : 'page');
      check((await rows(p)).length > 0 && (await rows(p)).length < all.length, 'search from Settings');
      if (w === 390) writeFileSync(OUT + `${lang}-390-from-settings.png`, await shoot(p));
      check(p.problems.length === 0, 'no page errors: ' + p.problems.join(' | '));
    } catch (e) { ok = false; console.error(`FAIL ${lang} ${w}: ${e.message}`); }
    finally { await p.dispose?.(); }
  }
} finally {
  rec.record('keys-search', ok && checks > 0);
  rec.write();
  await Promise.allSettled([B?.close(), fake?.stop(), web?.stop()]);
}
console.log(ok ? `P344 PASS (${checks} checks)` : 'P344 FAIL');
process.exit(ok ? 0 : 1);
