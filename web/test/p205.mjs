// P205: the working line on an open page speaks to the owner — 「董事长，您的最新指示已经收到，小助理正在处理，请稍候。」 /
// "Chairman, your latest instruction is in — your assistant is on it." — with the host's `owner.address` preference when
// set (text only). Real page + local fake host, independent headless Chromium (never :9222).
//   a) zh / en at 360 · 390 · 1440: the default address, at most two lines on a phone and one on a desktop, inside the card
//   b) `preferences.value.owner.address` replaces the default at once; a hostile value stays plain text (no element, no
//      Markdown), control characters dropped, cut to 16 characters; "" falls back to the default
// Shots → reports/qa/p205/wait-<lang>-<theme>-<width>.png; measurements → reports/qa/p205/wait-results.json.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p205');
const NAME = 'AgentJ-Father';
const TEXT = {
  zh: (a) => `${a}，您的最新指示已经收到，小助理正在处理，请稍候。`,
  en: (a) => `${a}, your latest instruction is in — your assistant is on it.`,
};
const DEFAULT = { zh: '董事长', en: 'Chairman' };
export const CASES = { a: 'working-line-address-default', b: 'working-line-address-setting' };
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'independent' } };
// [lang, theme, width, height, touch]
const RUNS = [['zh', 'light', 390, 844, true], ['zh', 'dark', 360, 800, true], ['zh', 'light', 1440, 900, false],
  ['en', 'dark', 390, 844, true], ['en', 'light', 360, 800, true], ['en', 'dark', 1440, 900, false]];

const geom = (p) => evaluate(p, `(()=>{const w=document.getElementById('words'),ps=[...w.querySelectorAll('p')];
  const r=ps[0]?ps[0].getBoundingClientRect():null,card=document.getElementById('rm').getBoundingClientRect();
  const lh=ps[0]?parseFloat(getComputedStyle(ps[0]).lineHeight):0;
  const range=document.createRange(); const txt=ps[0]&&[...ps[0].childNodes].find(n=>n.nodeType===3); if(txt) range.selectNodeContents(txt);   // P281: lines of the words, not the robot's own box
  const lines=ps[0]?new Set([...range.getClientRects()].map(x=>Math.round(x.top))).size:0;
  let second='';const tn=ps[0]&&[...ps[0].childNodes].find(n=>n.nodeType===3);   // P281: the robot <i> comes first
  if(tn){const t0=(()=>{const r=document.createRange();r.setStart(tn,0);r.setEnd(tn,1);return r.getBoundingClientRect().top;})();
    for(let i=1;i<tn.length;i++){const r=document.createRange();r.setStart(tn,i);r.setEnd(tn,i+1);if(r.getBoundingClientRect().top>t0+2){second=tn.data.slice(i);break;}}}
  return {second,text:w.textContent,paras:ps.length,children:w.querySelectorAll('*').length,robot:w.querySelectorAll('p>i.wbot:first-child').length,lines,lh,h:r?r.height:0,
    l:r?r.left:0,r:r?r.right:0,cardL:card.left,cardR:card.right,empty:w.classList.contains('empty'),fs:ps[0]?getComputedStyle(ps[0]).fontSize:''}})()`);

export async function runP205() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p205.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  const results = [];
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const step = async (c, f) => { try { await f(); rec.record(CASES[c], true); } catch (e) { rec.record(CASES[c], false); throw e; } };
  try {
    for (const [lang, theme, w, h, touch] of RUNS) {
      fake.reset();
      fake.st.prefs = structuredClone({ ...PREFS, appearance: { language: lang, theme } });
      const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
      const tag = `${lang}-${theme}-${w}`;
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: NAME });
        await fake.send({ t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 30, max: 100 }, h5: { pct: 34, reset: 0 }, week: { pct: 58, reset: 0 }, at: 0 });
        await fake.addTurn({ k: 'phone', dev: 'other', text: lang === 'zh' ? '帮我对一下三家供应商的报价' : 'Compare the three suppliers' }, '', 'open');
        await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
        await waitFor(p, `document.body.dataset.status==='working' && document.getElementById('words').textContent.includes(${JSON.stringify(DEFAULT[lang])})`);
        await sleep(250);

        await step('a', async () => {
          const g = await geom(p);
          results.push({ lang, theme, width: w, address: 'default', ...g });
          check(g.text === TEXT[lang](DEFAULT[lang]), `default text ${tag}: ${g.text}`);
          check(g.empty && g.paras === 1 && g.children === 2 && g.robot === 1, `one plain paragraph, P281's robot first ${tag}`);
          check(g.lines <= (w >= 1000 ? 1 : 2), `${g.lines} line(s) at ${tag}`);
          // a phone breaks at a comma, never inside a word: zh 「…收到，」 / 「小助理正在处理，请稍候。」
          if (g.lines === 2 && lang === 'zh') check(g.second === '小助理正在处理，请稍候。', `zh breaks after 收到， (${g.second}) ${tag}`);
          if (g.lines === 2 && lang === 'en') check(/^[a-z—]/.test(g.second) && !g.second.startsWith('—'), `en line 2 not a dash (${g.second}) ${tag}`);
          check(g.l >= g.cardL - 1 && g.r <= g.cardR + 1, `inside the reply card ${tag}`);
          writeFileSync(join(OUT, `wait-${tag}.png`), await shoot(p, ''));
        });

        await step('b', async () => {
          const send = async (address) => { fake.st.prefs = { ...fake.st.prefs, owner: { address } }; await fake.send(fake.prefsMsg()); };
          await send('老王');
          await waitFor(p, `document.getElementById('words').textContent===${JSON.stringify(TEXT[lang]('老王'))}`);
          check(true, 'address applied ' + tag);
          if (w === 390) writeFileSync(join(OUT, `wait-${tag}-address.png`), await shoot(p, ''));
          const hostile = '<b>x</b>**y**\n[z](https://e.x)\u0007 and a very long tail';
          await send(hostile);
          const want = Array.from('<b>x</b>**y** [z](https://e.x) and a very long tail'.replace(/\s+/g, ' ')).slice(0, 16).join('').trim();
          await waitFor(p, `document.getElementById('words').textContent===${JSON.stringify(TEXT[lang](want))}`);
          const g = await geom(p);
          check(g.children === 2 && g.robot === 1, `hostile address is text only ${tag} (${g.children} elements: the paragraph + P281's robot)`);
          await send('');
          await waitFor(p, `document.getElementById('words').textContent===${JSON.stringify(TEXT[lang](DEFAULT[lang]))}`);
          check(true, 'empty address → default ' + tag);
        });
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify(p.problems.slice(0, 2)));
      } finally { await p.dispose(); }
    }
    writeFileSync(join(OUT, 'wait-results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone', checks, results, complete: true }, null, 2) + '\n');
    console.log(`p205: ${checks} checks passed`);
    for (const r of results) console.log(`  ${r.lang}/${r.theme}/${r.width}: ${r.lines} line(s), font ${r.fs}${r.second ? ', line 2: ' + r.second : ''}`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP205();
