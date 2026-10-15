#!/usr/bin/env node
// P318 (Leo, 2026-10-15, Android Pad Chrome + iPhone Air Safari): the composer and the input card's header.
//   (a) the four tool buttons (file / photo / camera / microphone) are icon-only at every width — the Pad (≥ 520 px) used to
//       show 「单击锁定 · 按住说…」 truncated with the microphone half clipped; the icon is centred and inside its button
//   (b) the placeholder is one short line (「说点什么…」 / "Message…") on every width; the 20 000 cap shows only near the limit
//       (≥ 90 %: a small 「18,000 / 20,000」 note on the field)
//   (c) the header label (「Notice」 / 「Background task finished」) is not cut to 3 letters when the unread badge shows
// Real page + local fake host (Noise relay), Chromium; zh/en × 390 / 520 / 820 (mobile Pad) / 1280 / 1440, 390 + 1440 in dark
// and light. Shots → reports/qa/p318/<tag>/ (tag = "after" by default; `--tag before` on the old tree for the comparison).
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const ti = process.argv.indexOf('--tag');
const TAG = ti > 0 ? process.argv[ti + 1] : 'after';
const OUT = ROOT + `reports/qa/p318/${TAG}/`;
mkdirSync(OUT, { recursive: true });
const D = { zh: JSON.parse(readFileSync(ROOT + 'web/i18n/web.zh.json', 'utf8')), en: JSON.parse(readFileSync(ROOT + 'web/i18n/web.en.json', 'utf8')) };
const $ = (id) => `document.getElementById('${id}')`;
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pad) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';

const runs = [];
for (const lang of ['zh', 'en']) for (const w of [390, 520, 820, 1280, 1440]) for (const scheme of (w === 390 || w === 1440) ? ['dark', 'light'] : ['dark']) runs.push({ lang, w, scheme });

const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const res = { tag: TAG, scope: 'real page + local fake host, headless Chromium (not a physical device)', runs: [] };
let failed = 0;
try {
  for (const { lang, w, scheme } of runs) {
    const tag = `${lang}-${w}-${scheme}`, h = w < 600 ? 844 : w < 1000 ? 1180 : 900, mobile = w < 1000;
    const out = { tag, checks: [] }; res.runs.push(out);
    const C = async (n, fn) => { try { const d = await fn(); out.checks.push({ n, ok: true, d }); console.log(`PASS ${tag} ${n}`); }
      catch (e) { failed++; out.checks.push({ n, ok: false, err: String(e.message || e) }); console.log(`FAIL ${tag} ${n}: ${e.message || e}`); } };
    fake.reset();
    const p = await newPage(B, w, h, scheme, { touch: mobile, ua: w === 820 ? ANDROID : undefined, allow: [web.url, fake.relay + '/'] });
    if (w === 820) await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: true });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click();return true})()`);
      await evaluate(p, `document.documentElement.dataset.theme=${JSON.stringify(scheme)}`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ-Father' });
      await fake.addTurn({ k: 'sys' }, lang === 'zh' ? '配置在等待时变了，这次批准没有生效。' : 'The configuration changed while waiting; this approval was not applied.');
      await waitFor(p, `${$('pg')}.textContent==='1 / 1'`); await sleep(300);

      // (a) icon-only tool buttons, icon centred and not clipped
      await C('tools: icon only, centred, not clipped', async () => {
        const g = await evaluate(p, `[...document.querySelectorAll('#tools .tool')].map(b=>{const r=b.getBoundingClientRect(),s=b.querySelector('svg').getBoundingClientRect();
          const txt=[...b.querySelectorAll('span')].filter(x=>getComputedStyle(x).display!=='none'&&x.getBoundingClientRect().width>0&&x.textContent.trim()).map(x=>x.textContent);
          return {id:b.id,txt,w:r.width,h:r.height,dx:Math.abs((s.left+s.right)/2-(r.left+r.right)/2),dy:Math.abs((s.top+s.bottom)/2-(r.top+r.bottom)/2),in:s.left>=r.left&&s.right<=r.right&&s.top>=r.top&&s.bottom<=r.bottom,sw:s.width,label:b.getAttribute('aria-label')}})`);
        if (g.length !== 4) throw new Error('four tool buttons: ' + g.length);
        for (const b of g) {
          if (b.txt.length) throw new Error(`${b.id} shows text ${JSON.stringify(b.txt)}`);
          if (!b.in || b.sw < 20) throw new Error(`${b.id} icon clipped ${JSON.stringify(b)}`);
          if (b.dx > 1 || b.dy > 1) throw new Error(`${b.id} icon off centre ${JSON.stringify(b)}`);
          if (b.w < 44 || b.h < 44) throw new Error(`${b.id} target under 44 px ${JSON.stringify(b)}`);
          if (!b.label) throw new Error(`${b.id} has no aria-label`);
        }
        return g.map((b) => ({ id: b.id, w: Math.round(b.w) }));
      });

      // (b) the placeholder: the short key, shown, on one line
      await C('placeholder: short, one line, on every width', async () => {
        const ph = await evaluate(p, `${$('input')}.placeholder`);
        const want = D[lang]['p318.ph'];
        if (!ph) throw new Error('placeholder empty');
        if (ph !== want) throw new Error(`placeholder ${JSON.stringify(ph)} ≠ ${JSON.stringify(want)}`);
        const fit = await evaluate(p, `(()=>{const i=${$('input')},cs=getComputedStyle(i,'::placeholder'),c=document.createElement('canvas').getContext('2d');
          c.font=cs.fontWeight+' '+cs.fontSize+' '+cs.fontFamily;return {tw:c.measureText(i.placeholder).width,cw:i.clientWidth,color:cs.color,vis:getComputedStyle(i).visibility}})()`);
        if (fit.tw > fit.cw) throw new Error('placeholder wider than the field ' + JSON.stringify(fit));
        return fit;
      });
      await C('cap note: hidden until 90 %, then 「n / max」', async () => {
        const st = (v) => evaluate(p, `(()=>{const i=${$('input')};i.value=${JSON.stringify(v)};i.dispatchEvent(new Event('input'));const n=${$('capNote')};
          return n?{hidden:n.hidden||getComputedStyle(n).display==='none',text:n.textContent,max:i.maxLength}:null})()`);
        const a = await st('x'.repeat(100));
        if (!a) throw new Error('no #capNote');
        if (!a.hidden) throw new Error('note shown far from the limit');
        const max = a.max, b = await st('x'.repeat(Math.ceil(max * 0.9)));
        if (b.hidden || !b.text.includes(String(Math.ceil(max * 0.9)).replace(/\B(?=(\d{3})+$)/g, ','))) throw new Error('no note at 90 % ' + JSON.stringify(b));
        await sleep(100); writeFileSync(`${OUT}${tag}-capnote.png`, await shoot(p, ''));
        const c = await st('');
        if (!c.hidden) throw new Error('note stays after clearing');
        return { max, at90: b.text };
      });

      // (c) the header label with the unread badge on (the badge's state is set directly: this is a layout check)
      const hdr = () => evaluate(p, `(()=>{const b=${$('omLabel')},u=document.querySelector('#omUnread .unb').getBoundingClientRect(),r=b.getBoundingClientRect(),t=${$('omTime')}.getBoundingClientRect(),pg=${$('pg')}.getBoundingClientRect();
        const cs=getComputedStyle(b),c=document.createElement('canvas').getContext('2d');c.font=cs.fontWeight+' '+cs.fontSize+' '+cs.fontFamily;
        const word=c.measureText(b.textContent.split(' ')[0]).width*1.06,lines=Math.round(r.height/parseFloat(cs.lineHeight));
        return {label:b.textContent,lines,wordFits:b.clientWidth>=word&&b.scrollWidth<=b.clientWidth+1,cut:b.scrollWidth>b.clientWidth+1||b.scrollHeight>b.clientHeight+1,w:Math.round(r.width),badgeL:u.width?u.left:null,end:t.right,pgL:pg.left,
          overlapBadge:u.width>0&&t.right>u.left-2&&t.top<u.bottom&&t.bottom>u.top}})()`);
      for (const [pgn, name] of [[0, 'notice'], [1, 'bgTask']]) {
        if (pgn === 1) { await fake.addTurn({ k: 'agent', via: 'task' }, lang === 'zh' ? '后台任务跑完了。' : 'The background task is done.');
          await waitFor(p, `${$('pg')}.textContent==='2 / 2'`); await sleep(200); }
        await evaluate(p, `(()=>{${$('om')}.dataset.unread='1';${$('omUnreadN')}.textContent='99+';return true})()`); await sleep(120);
        await C(`header (${name}) with 99+ badge: label not cut, clear of the badge`, async () => {
          const g = await hdr();
          // out of room (a phone with the badge on): two lines, every word whole, an ellipsis only at the end of the second
          if (g.cut && !(g.lines === 2 && g.wordFits)) throw new Error('label truncated ' + JSON.stringify(g));
          if (g.overlapBadge) throw new Error('label runs under the badge ' + JSON.stringify(g));
          return g;
        });
        writeFileSync(`${OUT}${tag}-header-${name}.png`, await shoot(p, ''));
        await evaluate(p, `(()=>{${$('om')}.dataset.unread='0';return true})()`); await sleep(80);
        await C(`header (${name}) without badge: label not cut`, async () => { const g = await hdr(); if (g.cut) throw new Error('label truncated ' + JSON.stringify(g)); return g; });
      }
      await C('no horizontal overflow, no page errors', async () => {
        if (await evaluate(p, `document.documentElement.scrollWidth>innerWidth`)) throw new Error('horizontal overflow');
        const errs = p.problems.filter((x) => !/favicon|net::ERR/.test(x)); if (errs.length) throw new Error(errs.join(' | ').slice(0, 400));
      });
      await evaluate(p, `(()=>{const i=${$('input')};i.value='';i.dispatchEvent(new Event('input'));return true})()`); await sleep(100);
      writeFileSync(`${OUT}${tag}-composer.png`, await shoot(p, ''));
    } finally { await p.dispose?.(); }
  }
} finally { await B.close(); await fake.stop?.(); await web.stop?.(); }
writeFileSync(OUT + 'result.json', JSON.stringify(res, null, 1));
console.log(failed ? `P318 browser: ${failed} FAIL (${TAG})` : `P318 browser: all PASS (${TAG})`);
process.exit(failed ? 1 : 0);
