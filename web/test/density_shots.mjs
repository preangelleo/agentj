#!/usr/bin/env node
// Density screenshots (F13 / Leo 10-05): the chat screen with long assistant replies at Mac wide 1440×900 and a phone
// 390×844, against the fake host, in an independent headless Chromium (never :9222).
//   node web/test/density_shots.mjs <before|after>   → reports/qa/release-0.15/density-<tag>.{desktop,mobile}.png
// Prints the measured geometry (base font, reply font, card width, top bar / composer heights) as JSON.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const TAG = process.argv[2] || 'after';
const OUT = fileURLToPath(new URL('../../../reports/qa/release-0.15/', import.meta.url));
mkdirSync(OUT, { recursive: true });

export const LONG = [
  '## 本周补货清单\n\n我把三家供应商最近 30 天的报价都对了一遍。结论先说：**A 家**的单价最低，但交期比 B 家长两天；如果这批货要赶在周五前上架，选 B 更稳。\n\n- 纸箱 40×30×20：A 家 2.10 元，B 家 2.35 元，C 家 2.60 元\n- 胶带 48 mm：A 家 4.80 元一卷，B 家 5.10 元\n- 气泡膜：三家价格差不多，B 家可以当天发货\n\n另外，`orders/2026-10-03.csv` 里有 17 单的地址缺邮编，我已经把它们单独列在 `restock.md` 的最后一节，等你确认后我再联系客户补全。',
  'I compared the last 30 days of quotes from all three suppliers. Short version: **A** is cheapest per unit, but ships two days later than B; if this batch has to be on the shelf before Friday, B is the safer pick.\n\nThe line items are in `restock.md`, grouped by supplier, with the total for each option at the bottom. I also flagged 17 orders in `orders/2026-10-03.csv` whose address has no postcode — they are listed separately at the end, and I will contact those customers once you confirm.\n\nNothing has been ordered yet; say the word and I will place the order with whichever supplier you choose.',
  '好了。清单在 `restock.md` 里，按供应商分组，每组最后有合计。三家的报价对比我做成了表格：\n\n| 品类 | A 家 | B 家 | C 家 |\n|---|---|---|---|\n| 纸箱 | 2.10 | 2.35 | 2.60 |\n| 胶带 | 4.80 | 5.10 | 5.00 |\n| 气泡膜 | 31.0 | 30.5 | 31.2 |\n\n如果选 B 家，总价比 A 家多 86 元，但周四就能到货。缺邮编的 17 单我先不动，等你一句话。还有一件小事：上周退回来的 3 件货已经入库，库存表我同步更新了，`stock.xlsx` 里标黄的就是。',
];

export async function chatWithLongReplies(B, fake, BASE, w, h, extra = {}) {
  fake.reset();
  const p = await newPage(B, w, h, 'light', { allow: [BASE, fake.relay + '/'], ...extra });
  await navigate(p, fake.newPairing(BASE));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 30, max: 100 }, h5: { pct: 34, reset: 0 }, week: { pct: 58, reset: 0 }, at: 0 });
  await fake.addTurn({ k: 'phone', dev: 'other', text: '帮我对一下三家供应商的报价，做个补货清单' }, LONG[0]);
  await fake.addTurn({ k: 'phone', dev: 'other', text: 'Summarise it in English too' }, LONG[1]);
  await fake.addTurn({ k: 'phone', dev: 'other', text: '做成表格，顺便看看退货' }, LONG[2]);
  await waitFor(p, `document.getElementById('words').textContent.includes('stock.xlsx')`);
  await sleep(400);
  return p;
}

export const GEOMETRY = `(() => {
  const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return { w: Math.round(b.width), h: Math.round(b.height) }; };
  const cs = (s, k) => { const e = document.querySelector(s); return e ? getComputedStyle(e)[k] : null; };
  const words = document.getElementById('words');
  return { body: cs('body', 'fontSize'), words: cs('#words', 'fontSize'), lineHeight: cs('#words', 'lineHeight'),
    top: r('.top'), deck: r('#deck'), rm: r('#rm'), wordsBox: r('#words'), composer: r('.composer'), tool: r('#tDoc'), field: r('.field'),
    mainPad: cs('#main', 'padding'), composerPad: cs('.composer', 'padding'), topPad: cs('.top', 'padding'),
    charsPerLine: words ? Math.round(words.getBoundingClientRect().width / parseFloat(getComputedStyle(words).fontSize)) : null };
})()`;

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  const out = {};
  try {
    for (const [name, w, h, opts] of [['desktop', 1440, 900, {}], ['mobile', 390, 844, { touch: true }]]) {
      const p = await chatWithLongReplies(B, fake, web.url, w, h, opts);
      out[name] = await evaluate(p, GEOMETRY);
      const f = join(OUT, `density-${TAG}.${name}.png`);
      writeFileSync(f, await shoot(p, f));
      console.log('shot', f);
      if (name === 'mobile') {
        const p360 = await chatWithLongReplies(B, fake, web.url, 360, 800, opts);
        out.mobile360 = await evaluate(p360, GEOMETRY);
        out.mobile360.sw = await evaluate(p360, 'document.documentElement.scrollWidth');
        await p360.dispose?.();
      }
      await p.dispose?.();
    }
    console.log(JSON.stringify(out, null, 1));
  } finally {
    await B.close(); await fake.stop(); await web.stop();
  }
}
