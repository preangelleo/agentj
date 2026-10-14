#!/usr/bin/env node
// P238 reference IDs retained under Leo P251 amendment: all actions stay visible; fixed left target,
// equal right slots capped at the existing 46px desktop pitch, shrinking glyphs only after space is compressed.
// Local encrypted fake host + Chromium, 280/320/360/393/412/768/1440px; physical phones are not claimed.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, tap, key } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p238/bar/';
mkdirSync(OUT, { recursive: true });
const BEFORE = process.env.P238_BEFORE || '';
const rec = BEFORE ? { record() {}, write() {} } : parityRecorder('web/test/p238-bar.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(BEFORE ? { publicDir: BEFORE } : {}) });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const $ = (id) => `document.getElementById(${JSON.stringify(id)})`;
const REPLY = { zh: '三件事：定价、招聘、发布日期。下周一前把定价表发给财务。', en: 'Three things: pricing, hiring, the launch date. Pricing goes to finance by Monday.' };

async function paired({ w = 390, h = 844, lang = 'zh', scheme = 'light' } = {}) {
  fake.reset();
  fake.st.caps = ['contacts', 'tgfwd'];
  fake.st.tgfwd = true;
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'ct_list') return false;
    await send({ t: 'ct_res', r: m.r, ok: true, contacts: [], count: 0, me: ['owner@example.com'], mail: { ready: true } });
    return true;
  };
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 600 });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(()=>{${$('phone-env-dismiss')}?.click(); const n=${$('confirm-no')}; if(n&&!${$('confirm')}.hidden)n.click(); const a=${$('a2hs-ok')}; if(a&&!${$('a2hs')}.hidden)a.click();})()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '早上的会说了什么' : 'What did the morning meeting decide?' }, REPLY[lang]);
  await waitFor(p, `!${$('mailBtn')}.hidden && !${$('webReportBtn')}.disabled`);
  await sleep(250);
  return p;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
// every visible row button, the card, the text inset, the disc and every visible glyph
const GEOM = `(() => {
  const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height }; };
  const rm = R(${$('rm')}), bar = ${$('rmbar')};
  const btns = [...bar.querySelectorAll("button")].filter((b) => !b.hidden && b.getClientRects().length).map((b) => ({ id: b.id, ...R(b) }));
  const glyphs = [...bar.querySelectorAll('.act')].filter((b) => !b.hidden && b.getClientRects().length).map((b) => {
    const g = [...b.querySelectorAll('svg.g')].find((x) => getComputedStyle(x).display !== 'none'); return { id: b.id, ...R(g) }; });
  const disc = R(${$('webReportBtn')}.querySelector('svg'));
  const inset = parseFloat(getComputedStyle(document.querySelector('.rm main')).paddingLeft);
  const folded = ['mailBtn', 'readBtn', 'fwdBtn'].filter((id) => document.getElementById(id).classList.contains('folded'));   // fold order
  return { rm, btns, glyphs, disc, inset, folded, more: !!document.getElementById("rmMoreBtn"), vw: innerWidth, docX: document.documentElement.scrollWidth > innerWidth };
})()`;
const WIDTHS = [[280, 653], [320, 690], [360, 780], [393, 852], [412, 915], [768, 900], [1440, 900]];

try{
 await C('row-fits-every-width', async () => {
  for(const [w,h] of WIDTHS)for(const scheme of ['light','dark']){
   const p=await paired({w,h,scheme});try{
    const g=await ev(p,GEOM);assert.equal(g.btns.length,7);assert.equal(g.more,false);assert.deepEqual(g.folded,[]);
    for(const b of g.btns){assert.ok(b.l>=g.rm.l-.5&&b.r<=g.rm.r+.5);assert.ok(b.w>=24&&b.h>=44);}
    for(let i=1;i<g.btns.length;i++)assert.ok(g.btns[i].l>=g.btns[i-1].r-.5);
    assert.equal(g.docX,false);writeFileSync(`${OUT}after-zh-${scheme}-${w}.png`,await shoot(p,''));noProblems(p);
   }finally{await p.dispose();}
  }
 });
 await C('one-inset-one-baseline',async()=>{
  for(const w of [280,393,1440]){
   const p=await paired({w});try{
    const g=await ev(p,GEOM);assert.ok(Math.abs(g.disc.l-g.rm.l-g.inset)<=1);
    for(const x of g.glyphs)assert.ok(Math.abs(x.b-g.disc.b)<=1);
   }finally{await p.dispose();}
  }
 });
 // Preserve the parity reference ID, superseded by Leo's P251 no-fold decision.
 await C('narrow-folds-into-more',async()=>{
  const p=await paired({w:280});try{
   assert.equal(await ev(p,'!!document.getElementById("rmMoreBtn")'),false);
   await ev(p,'document.getElementById("readBtn").click()');await waitFor(p,'document.body.dataset.reader==="1"');
  }finally{await p.dispose();}
 });
}finally{await B.close();await fake.stop();await web.stop();rec.write();}
if(failed)process.exitCode=1;
