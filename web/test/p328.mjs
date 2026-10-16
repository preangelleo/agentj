#!/usr/bin/env node
// P328 (Leo, 0.22.0a1 real phone): the composer's 「× 取消」 and the command page's reply card.
//   A. while the Agent works (blue, status "working") the × shows for every harness — not only while this phone's message
//      is still in flight / queued — and a tap stops the turn (§8 slash stop). It sits vertically centred in the message
//      field: |pill centre − field centre| ≤ 1 px (one line and three lines, every mode).
//   B. every reply card keeps ADR-035's fixed height — a one-line command answer (/clear …) too (Leo 10-16 11:3x: a fixed
//      margin above the composer, empty space and all); a long command answer fits the deck and scrolls inside.
// Real page + local fake host (Noise relay), headless Chromium; zh/en × 390 / 1440 (+ 1440 fine pointer) × dark / light.
// Shots → reports/qa/p328/<tag>/ (tag "after" by default; `--tag before` on the old tree for the comparison).
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const ti = process.argv.indexOf('--tag');
const TAG = ti > 0 ? process.argv[ti + 1] : 'after';
const OUT = ROOT + `reports/qa/p328/${TAG}/`;
mkdirSync(OUT, { recursive: true });
const $ = (id) => `document.getElementById('${id}')`;

const runs = [];
for (const lang of ['zh', 'en']) for (const w of [390, 1440]) for (const scheme of ['dark', 'light']) runs.push({ lang, w, scheme });
// + a mouse / trackpad desktop (the Mac app of Leo's screenshot): `@media (hover:hover) and (pointer:fine)` makes --rtap 48
// (a 48 px field). Headless Chromium cannot emulate hover / pointer (Emulation.setEmulatedMedia ignores them), so that
// block's own rules are lifted out of app.css and applied unconditionally on these runs.
for (const lang of ['zh', 'en']) for (const scheme of ['dark', 'light']) runs.push({ lang, w: 1440, scheme, fine: true });
const FINE_CSS = (() => { const css = readFileSync(ROOT + 'web/public/app.css', 'utf8'), i = css.indexOf('@media (hover:hover) and (pointer:fine){');
  if (i < 0) throw new Error('no fine-pointer block in app.css'); let d = 0, j = css.indexOf('{', i);
  for (let k = j; k < css.length; k++) { if (css[k] === '{') d++; else if (css[k] === '}' && --d === 0) return css.slice(j + 1, k); }
  throw new Error('unclosed fine-pointer block'); })();

// the × pill (its visible <b>) against the message field: centre offset, sizes, overlap with the typed text
const GEO = `(()=>{const c=${$('sayCancel')},b=c.querySelector('b'),f=document.querySelector('.composer .field'),ta=${$('input')};
  const vis=!c.hidden&&getComputedStyle(c).display!=='none';
  if(!vis)return {vis:false,mode:c.dataset.mode||null};
  const rb=b.getBoundingClientRect(),rc=c.getBoundingClientRect(),rf=f.getBoundingClientRect(),rt=ta.getBoundingClientRect();
  return {vis:true,mode:c.dataset.mode||null,dy:+(((rb.top+rb.bottom)/2)-((rf.top+rf.bottom)/2)).toFixed(2),
    tapH:Math.round(rc.height),tapW:Math.round(rc.width),fieldH:Math.round(rf.height),
    textEnd:Math.round(rt.right),pillL:Math.round(rb.left),aria:c.getAttribute('aria-label'),
    inField:rb.left>=rf.left&&rb.right<=rf.right&&rb.top>=rf.top&&rb.bottom<=rf.bottom}})()`;

const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const res = { tag: TAG, scope: 'real page + local fake host, headless Chromium (not a physical device)', runs: [] };
let failed = 0;
try {
  for (const { lang, w, scheme, fine } of runs) {
    const tag = `${lang}-${w}${fine ? '-fine' : ''}-${scheme}`, h = w < 600 ? 844 : 900, mobile = w < 1000;
    const out = { tag, checks: [] }; res.runs.push(out);
    const C = async (n, fn) => { try { const d = await fn(); out.checks.push({ n, ok: true, d }); console.log(`PASS ${tag} ${n}`, d === undefined ? '' : JSON.stringify(d)); }
      catch (e) { failed++; out.checks.push({ n, ok: false, err: String(e.message || e) }); console.log(`FAIL ${tag} ${n}: ${e.message || e}`); } };
    const centred = (g, what) => {
      if (!g.vis) throw new Error(`${what}: × not shown ${JSON.stringify(g)}`);
      if (Math.abs(g.dy) > 1) throw new Error(`${what}: × off the field's centre by ${g.dy}px ${JSON.stringify(g)}`);
      if (g.tapH < 44) throw new Error(`${what}: tap target ${g.tapH}px < 44`);
      if (!g.inField) throw new Error(`${what}: pill leaves the field ${JSON.stringify(g)}`);
      if (g.textEnd > g.pillL + 1) throw new Error(`${what}: text runs under the pill ${JSON.stringify(g)}`);
      return g;
    };
    const type = (v) => evaluate(p, `(()=>{const i=${$('input')};i.value=${JSON.stringify(v)};i.dispatchEvent(new Event('input'));return true})()`);
    const status = async (s) => { await fake.send({ t: 'status', s, agent: 'claude', name: 'AgentJ' }); await waitFor(p, `document.body.dataset.status===${JSON.stringify(s)}`); await sleep(120); };
    fake.reset();
    const p = await newPage(B, w, h, scheme, { touch: mobile, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click();return true})()`);
      await evaluate(p, `document.documentElement.dataset.theme=${JSON.stringify(scheme)}`);
      if (fine) await evaluate(p, `(()=>{const s=document.createElement('style');s.textContent=${JSON.stringify(FINE_CSS)};document.head.append(s);return getComputedStyle(document.documentElement).getPropertyValue('--rtap')})()`);
      await status('idle');
      await fake.addTurn({ k: 'agent' }, lang === 'zh' ? '收到，正在看。' : 'On it.');
      await waitFor(p, `${$('pg')}.textContent==='1 / 1'`); await sleep(250);

      // ---- A. the × while the Agent works ------------------------------------------------------------------------
      await C('idle, nothing sent: no ×', async () => { const g = await evaluate(p, GEO); if (g.vis) throw new Error('× shown while idle'); return g; });
      await status('working');
      await C('working (any harness): × shown, mode turn, centred in the field', async () => {
        const g = centred(await evaluate(p, GEO), 'working');
        if (g.mode !== 'turn') throw new Error('mode ' + g.mode);
        return g;
      });
      writeFileSync(`${OUT}${tag}-working.png`, await shoot(p, ''));
      await type(lang === 'zh' ? '第一行\n第二行\n第三行' : 'line one\nline two\nline three'); await sleep(150);
      await C('working, three typed lines: × still centred, text clear of it', async () => centred(await evaluate(p, GEO), 'three lines'));
      writeFileSync(`${OUT}${tag}-working-3lines.png`, await shoot(p, ''));
      await type(''); await sleep(100);
      await C('tap × while working → slash stop (the real interrupt channel)', async () => {
        const before = fake.st.slashes.length;
        await evaluate(p, `${$('sayCancel')}.click()`);
        await waitFor(p, 'true'); await sleep(300);
        const got = fake.st.slashes.slice(before);
        if (!got.some((m) => m.cmd === 'stop')) throw new Error('no slash stop: ' + JSON.stringify(got));
        return got.map((m) => m.cmd);
      });
      await status('idle');
      await C('back to idle: × gone', async () => { const g = await evaluate(p, GEO); if (g.vis) throw new Error('× stays after the turn'); return g; });
      await fake.addTurn({ k: 'agent' }, lang === 'zh' ? '收到，正在看。' : 'On it.'); await sleep(200);

      // queued (a busy shared session): the × takes the message back, centred too
      fake.st.sayMode = 'queued';
      await type(lang === 'zh' ? '排队的一条' : 'a queued one'); await evaluate(p, `${$('send')}.click()`);
      await sleep(1000);
      await C('queued: × shown (mode queued), centred', async () => { const g = centred(await evaluate(p, GEO), 'queued'); if (g.mode !== 'queued') throw new Error('mode ' + g.mode); return g; });
      writeFileSync(`${OUT}${tag}-queued.png`, await shoot(p, ''));
      await C('queued: tap × withdraws the message (say_cancel), not a stop', async () => {
        const before = fake.st.slashes.length;
        await evaluate(p, `${$('sayCancel')}.click()`); await sleep(600);
        if (fake.st.slashes.length !== before) throw new Error('sent a slash: ' + JSON.stringify(fake.st.slashes.slice(before)));
        const v = await evaluate(p, `${$('input')}.value`);
        if (!v) throw new Error('the words did not come back');
        return { back: v };
      });
      fake.st.sayMode = 'delivered'; await type(''); await sleep(100);

      // a send in flight (slow say_res): mode send, centred
      fake.st.sayDelay = 2500; fake.st.autoReply = false;
      await type(lang === 'zh' ? '慢一点的一条' : 'a slow one'); await evaluate(p, `${$('send')}.click()`);
      await sleep(800);
      await C('send in flight: × shown (mode send), centred', async () => { const g = centred(await evaluate(p, GEO), 'in flight'); if (g.mode !== 'send') throw new Error('mode ' + g.mode); return g; });
      writeFileSync(`${OUT}${tag}-sending.png`, await shoot(p, ''));
      await sleep(2600); fake.st.sayDelay = 0; fake.st.autoReply = true;

      // ---- B. the command page's reply card height --------------------------------------------------------------
      const card = () => evaluate(p, `(()=>{const rm=${$('rm')},d=${$('deck')},bar=${$('rmbar')},rd=${$('read')},m=${$('main')};
        const kids=[...rd.children].filter(x=>!x.hidden&&getComputedStyle(x).display!=='none');
        const content=kids.length?Math.max(...kids.map(x=>x.getBoundingClientRect().bottom))-rd.getBoundingClientRect().top:0;
        const r=rm.getBoundingClientRect(),dr=d.getBoundingClientRect();
        return {rmH:Math.round(r.height),content:Math.round(content),bar:Math.round(bar.getBoundingClientRect().height),
          rmBottom:Math.round(r.bottom),deckBottom:Math.round(dr.bottom-parseFloat(getComputedStyle(d).paddingBottom)),
          scrolls:m.scrollHeight>m.clientHeight+1}})()`);
      const one = lang === 'zh' ? '没清成：Codex app-server 只能新建另一线程，不能替桌面切换当前线程。' : 'Not cleared: the Codex app-server can only start another thread.';
      await fake.addTurn({ k: 'cmd', text: '/clear', dev: null }, one, 'done', { cmd: 'clear', ok: false, kind: 'error' });
      await sleep(400);
      await C('command page, one line: the reply card keeps the fixed height (fills the deck)', async () => {
        const g = await card();
        if (Math.abs(g.rmBottom - g.deckBottom) > 2) throw new Error('command card no longer fills the deck ' + JSON.stringify(g));
        return g;
      });
      writeFileSync(`${OUT}${tag}-cmd-short.png`, await shoot(p, ''));
      const long = Array.from({ length: 80 }, (_, i) => (lang === 'zh' ? '第 ' : 'line ') + (i + 1)).join('\n');
      await fake.addTurn({ k: 'cmd', text: '/help', dev: null }, long, 'done', { cmd: 'help', ok: true, kind: 'info' });
      await sleep(400);
      await C('command page, long answer: the card stops at the deck and scrolls inside', async () => {
        const g = await card();
        if (g.rmBottom > g.deckBottom + 1) throw new Error('card past the deck ' + JSON.stringify(g));
        if (!g.scrolls) throw new Error('long answer does not scroll ' + JSON.stringify(g));
        return g;
      });
      await fake.addTurn({ k: 'agent' }, lang === 'zh' ? '一行回复。' : 'A one-line reply.');
      await sleep(400);
      let normal;
      await C('ordinary reply page keeps the fixed card (ADR-035)', async () => {
        normal = await card();
        if (Math.abs(normal.rmBottom - normal.deckBottom) > 2) throw new Error('ordinary card no longer fills the deck ' + JSON.stringify(normal));
        return normal;
      });
      writeFileSync(`${OUT}${tag}-reply-short.png`, await shoot(p, ''));
      await C('no horizontal overflow, no page errors', async () => {
        if (await evaluate(p, `document.documentElement.scrollWidth>innerWidth`)) throw new Error('horizontal overflow');
        const errs = p.problems.filter((x) => !/favicon|net::ERR/.test(x)); if (errs.length) throw new Error(errs.join(' | ').slice(0, 400));
      });
    } finally { await p.dispose?.(); }
  }
} finally { await B.close(); await fake.stop?.(); await web.stop?.(); }
writeFileSync(OUT + 'result.json', JSON.stringify(res, null, 1));
console.log(failed ? `P328 browser: ${failed} FAIL (${TAG})` : `P328 browser: all PASS (${TAG})`);
process.exit(failed ? 1 : 0);
