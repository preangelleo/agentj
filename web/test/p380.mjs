// P380 #94: actual phone client + encrypted local fake host, long bilingual and unbroken checklist text.
// P380_BEFORE=1 captures the unmodified regression; normal run asserts wrapping and card actions.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep } from './browser.mjs';
const before = process.env.P380_BEFORE === '1';
const out = `reports/qa/p380/web/${before ? 'before' : 'after'}`;
const fx = JSON.parse(readFileSync(new URL('../../host/tests/fixtures/p360_cards.json', import.meta.url)));
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
mkdirSync(out, { recursive: true });
let n = 0; const results = [];
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const lang of ['zh', 'en']) {
    fake.reset(); fake.st.caps = ['jcards1']; fake.st.prefs = { appearance: { language: lang, theme } };
    const card = structuredClone(fx.get[lang].todo);
    card.items = card.items.slice(0, 3);
    card.items[0].text = lang === 'zh' ? '需要与团队确认本周的工作安排并记录下一次讨论需要准备的所有材料。'.repeat(3) : 'Confirm the weekly schedule with the team and prepare all materials for our next discussion. '.repeat(3);
    card.items[1].text = 'LongUnbrokenChecklistReference'.repeat(10);
    card.items[2].text = '一条完成的长待办事项'.repeat(10); card.items[2].done = true;
    fake.st.onApp = async (_c, m, send) => {
      if (m.t === 'jcard_list') { await send({ t: 'jcard_items', r: m.r, cards: fx.list.cards }); return true; }
      if (m.t === 'jcard_get') { await send({ t: 'jcard_get', r: m.r, ...card }); return true; }
      return false;
    };
    const p = await newPage(B, width, width === 390 ? 844 : 900, theme, { allow: [web.url, fake.relay + '/'], touch: true });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      // P380 QA: the persisted theme overrides emulated media; explicitly select and verify each visual variant.
      await evaluate(p, `import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');return true})`);
      assert.equal(await evaluate(p, `document.documentElement.getAttribute('data-theme')`), theme);
      const expectedBg = theme === 'light' ? '#faf9f5' : '#141414';
      assert.equal(await evaluate(p, `getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()`), expectedBg);
      await evaluate(p, `document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();true`);
      await fake.send({ t: 'jcard_open', id: 'todo' });
      await waitFor(p, `document.querySelectorAll('.jc-text').length === 3`); await sleep(250);
      const geom = await evaluate(p, `(()=>{const box=document.querySelector('.jc-body'),r=box.getBoundingClientRect();return [...box.querySelectorAll('.jc-text')].map(e=>{const t=e.getBoundingClientRect(),s=getComputedStyle(e),range=document.createRange();range.selectNodeContents(e);return {left:t.left,right:t.right,boxLeft:r.left,boxRight:r.right,sw:e.scrollWidth,cw:e.clientWidth,lines:range.getClientRects().length,whiteSpace:s.whiteSpace,text:e.textContent}})})()`);
      if (!before) {
        assert.ok(geom.every(g => g.left >= g.boxLeft && g.right <= g.boxRight + 1 && g.sw <= g.cw + 1 && g.lines > 1 && g.whiteSpace === 'normal'), JSON.stringify(geom)); n++;
        assert.deepEqual(geom.map(g => g.text), card.items.map(i => i.text)); n++;
        await evaluate(p, `document.querySelector('.jc-row').click()`); await waitFor(p, `!document.getElementById('confirm').hidden`);
        assert.equal(await evaluate(p, `document.getElementById('confirm-text').textContent`), card.items[0].acts[0].prompt); n++;
        await evaluate(p, `document.getElementById('confirm-no').click()`);
        assert.equal(fake.st.says.length, 0); n++;
      }
      results.push({ width, theme, lang, geom });
      writeFileSync(`${out}/todo-${lang}-${theme}-${width}.png`, await shoot(p, ''));
    } finally { await p.dispose(); }
  }
  writeFileSync(`${out}/todo-measurements.json`, JSON.stringify(results, null, 2));
  console.log(`P380 todo ${before ? 'BEFORE captured' : 'PASS'}: ${n} checks, 8 screenshots`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
