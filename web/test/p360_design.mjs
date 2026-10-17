// P360 card-wall design sheet (Leo 10-17: 「1/5、1/6 还是 1/4……出个设计方案看一下」): the same 12 cards (the five system cards +
// seven of the owner's) with the peek at 1/4, 1/5 and 1/6 of the 220 px card, on an iPhone (390 × 844) and an iPad
// (820 × 1180). The shipped value is cards.css --jc-peek (1/5 = 44 px); this only overrides it for the comparison shots.
// Shots → reports/qa/p360/design-<peek>-<w>.png. Not a gate: run by hand when the geometry is reconsidered.
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p360');
const FX = JSON.parse(readFileSync(join(ROOT, 'host/tests/fixtures/p360_cards.json'), 'utf8'));
const WF = JSON.parse(readFileSync(join(ROOT, 'host/tests/fixtures/p244_wf.json'), 'utf8'));
const PEEKS = { '1-4': 55, '1-5': 44, '1-6': 37 };
const NAMES = ['读书清单', '周报草稿', '家庭账本', '健身打卡', '旅行计划', '灵感收集'];

mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const cards = JSON.parse(JSON.stringify(FX.list.cards)).slice(0, 1);
NAMES.forEach((n, i) => cards.push({ ...FX.list.cards[2], id: `d${i}`, name: { zh: n, en: n }, accent: ['windstorm', 'magic-carpet', 'peachy-feeling', 'noir', 'emerald-oasis', 'windstorm'][i] }));
const out = [];
try {
  for (const [w, h] of [[390, 844], [820, 1180]]) {
    fake.reset(); fake.st.caps = ['wf1', 'jcards1'];
    fake.st.prefs = { appearance: { language: 'zh', theme: 'dark' }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'wf_list') { for (const x of WF.wf_list) await send({ ...x, r: m.r }); return true; }
      if (m.t === 'jcard_list') { await send({ t: 'jcard_items', r: m.r, cards }); return true; }
      return false;
    };
    const p = await newPage(B, w, h, 'dark', { allow: [web.url, fake.relay + '/'], touch: true });
    await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`); await sleep(250);
    await evaluate(p, `(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));})()`);
    await waitFor(p, `document.querySelectorAll('#five-menu .fp-menu-card').length===12`); await sleep(400);
    for (const [k, px] of Object.entries(PEEKS)) {
      await evaluate(p, `document.documentElement.style.setProperty('--jc-peek','${px}px')`); await sleep(250);
      const g = await evaluate(p, `(()=>{const m=document.getElementById('five-menu');return {sh:m.scrollHeight,ch:m.clientHeight}})()`);
      writeFileSync(join(OUT, `design-${k}-${w}.png`), await shoot(p, ''));
      out.push({ peek: k, px, w, deck: g.sh, screen: g.ch, per100: Math.round((99 * px + 220 + 32) / g.ch * 10) / 10 });
    }
    await p.dispose();
  }
  writeFileSync(join(OUT, 'design.json'), JSON.stringify(out, null, 2));
  console.table(out);
} finally { await B.close(); await fake.stop(); await web.stop(); }
