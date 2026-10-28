#!/usr/bin/env node
// P563 追加 1 / 7 (贾维斯审查):
//  1 — the gear's remote-count digit: no interpolated colour (closed Leo palette). Working / waiting / question / offline
//      paint a palette state colour on a palette circle at ≥ 4.5:1; idle keeps P517's Lotion circle + logo green; a custom
//      theme still shows its own state colour (never a flat black digit).
//  7 — the no-key approval card (P561 追加 17): the words on top, the buttons (去设置 · 拒绝) on one row under them.
//   node web/test/p563.mjs                                      → after (asserts) + screenshots
//   P563_PHASE=before P563_PUBLIC=<HEAD copy>/web/public node … → before screenshots only
// Screens: $P563_OUT (default reports/qa/p563/badge) — 1-badge-<state>-<scheme>-<phase>.png, 1-badge-custom-<phase>.png,
// 7-nokey-card-<lang>-<w>-<scheme>-<phase>.png. No pairing link / token is in any shot (the clip is the gear / the sheet).
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor, sleep } from './browser.mjs';

const phase = process.env.P563_PHASE === 'before' ? 'before' : 'after';
const OUT = resolve(process.env.P563_OUT || 'reports/qa/p563/badge');
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, publicDir: process.env.P563_PUBLIC ? resolve(process.env.P563_PUBLIC) + '/' : PUBLIC_DIR });
const fake = await startFakeHost(), B = await launch();
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const STATES = { idle: ['idle', ''], working: ['working', ''], waiting: ['waiting', 'permission'], question: ['waiting', 'question'], offline: ['unknown', ''] };

// The closed palette (leo-palette.json) — every colour the badge wears must be one of these, exactly.
const PJ = resolve(homedir(), '.claude/skills/leo-design-language/references/leo-palette.json');
const PALETTE = existsSync(PJ) ? JSON.parse(readFileSync(PJ, 'utf8')).groups.flatMap((g) => g.colors.map((c) => c.hex.toLowerCase()))
  : readFileSync(new URL('../../brand/palette.css', import.meta.url), 'utf8').match(/#[0-9a-f]{6}\b/g).concat('#827dbd');   // no skill here: the repo's palette copies (app.css spells Magic Carpet)
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const rgbOf = (c) => { const m = /color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)/.exec(c); return m ? m.slice(1, 4).map((v) => Math.round(+v * 255)) : c.match(/[\d.]+/g).slice(0, 3).map(Number); };
const toHex = (c) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
// P563 追加 1: what each default state paints — digit on circle, both palette hex
const WANT = { idle: ['#629887', '#faf9f5'], working: ['#6a9bcc', '#141414'], waiting: ['#d97757', '#141414'], question: ['#827dbd', '#141414'], offline: ['#858585', '#141414'] };
let failed = 0;
const audit = [];
const ok = (name, fn) => fn().then(() => console.log('  ✓ ' + name), (e) => { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); });

async function paired({ w, scheme, lang, phone }) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
  fake.remotes({ limit: 10, items: [{ id: 'ME', me: true }, ...Array.from({ length: 6 }, (_, i) => ({ id: 'r' + i }))] });
  const p = await newPage(B, w, phone ? 844 : 900, scheme, { touch: phone, ua: phone ? IPHONE : undefined, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await waitFor(p, `document.querySelector('#setBtn [data-remote-count]')?.textContent === '7'`);
  await evaluate(p, `(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme='${scheme}';
    const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);await document.fonts.ready})()`);
  return p;
}
async function shot(p, sels, file, pad = 12) {
  const clip = await evaluate(p, `(()=>{const els=${JSON.stringify(sels)}.map(s=>document.querySelector(s)).filter(e=>e&&e.getClientRects().length);if(!els.length)return null;
    const rs=els.map(e=>e.getBoundingClientRect());const pad=${pad};
    const x=Math.max(0,Math.min(...rs.map(r=>r.left))-pad),y=Math.max(0,Math.min(...rs.map(r=>r.top))-pad);
    const r=Math.min(innerWidth,Math.max(...rs.map(r=>r.right))+pad),b=Math.min(innerHeight,Math.max(...rs.map(r=>r.bottom))+pad);
    return {x,y,width:r-x,height:b-y,scale:2}})()`);
  if (!clip) return null;
  await sleep(120);
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip });
  writeFileSync(file, Buffer.from(data, 'base64'));
  return file;
}
const BADGE = `(()=>{const b=document.querySelector('#setBtn [data-remote-count]'),c=getComputedStyle(b);return {fg:c.color,bg:c.backgroundColor}})()`;

try {
  // ---------------------------------------------------------------- 1: the gear digit
  for (const scheme of ['light', 'dark']) await ok('badge ' + scheme, async () => {
    const p = await paired({ w: 390, scheme, lang: 'zh', phone: true });
    try {
      for (const [name, [s, kind]] of Object.entries(STATES)) {
        await fake.send({ t: 'status', s, kind, agent: 'claude', name: 'Agent J' });
        await waitFor(p, `document.body.dataset.status==='${s}'`);
        await sleep(200);
        const m = await evaluate(p, BADGE), fg = rgbOf(m.fg), bg = rgbOf(m.bg);
        await shot(p, ['#setBtn'], `${OUT}/1-badge-${name}-${scheme}-${phase}.png`);
        audit.push({ badge: name, scheme, phase, fg: toHex(fg), bg: toHex(bg), ratio: +ratio(fg, bg).toFixed(2) });
        if (phase === 'after') {
          assert.equal(toHex(fg), WANT[name][0], name + ' digit');
          assert.equal(toHex(bg), WANT[name][1], name + ' circle');
          assert.ok(PALETTE.includes(toHex(fg)) && PALETTE.includes(toHex(bg)), name + ': both colours in the closed palette');
          if (name !== 'idle') assert.ok(ratio(fg, bg) >= 4.5, `${name} ${ratio(fg, bg).toFixed(2)} ≥ 4.5:1`);
        }
      }
      // a custom theme: its own state colours (palette light cluster here) still read on the badge — never a flat black digit
      const custom = { idle: '#ffe292', working: '#71bbde', waiting: '#ebc9b7', question: '#cacadb', offline: '#858585' };
      await evaluate(p, `(async()=>{(await import('./js/theme.js')).applyTheme({t:'theme',v:1,id:'custom',colors:{state:${JSON.stringify(custom)}}})})()`);
      for (const [name, [s, kind]] of Object.entries(STATES)) {
        await evaluate(p, `document.body.dataset.status='${s}';document.body.dataset.kind='${kind}'`);
        await sleep(60);
        const m = await evaluate(p, BADGE), fg = rgbOf(m.fg), bg = rgbOf(m.bg);
        audit.push({ badge: 'custom-' + name, scheme, phase, fg: toHex(fg), bg: toHex(bg), ratio: +ratio(fg, bg).toFixed(2) });
        if (scheme === 'light') await shot(p, ['#setBtn'], `${OUT}/1-badge-custom-${name}-${phase}.png`);
        if (phase === 'after') {
          assert.equal(toHex(fg), custom[name], 'custom ' + name + ' digit = the theme state colour (idle: P535, unchanged)');
          if (name !== 'idle') { assert.equal(toHex(bg), '#141414'); assert.ok(ratio(fg, bg) >= 4.5, 'custom ' + name + ' ≥ 4.5:1'); }
        }
      }
    } finally { await p.dispose(); }
  });

  // ---------------------------------------------------------------- 7: the no-key card
  for (const lang of ['zh', 'en']) for (const [w, phone] of [[390, true], [1440, false]]) for (const scheme of ['light', 'dark']) {
    const tag = `${lang}-${w}-${scheme}`;
    await ok('no-key card ' + tag, async () => {
      const p = await paired({ w, scheme, lang, phone });
      try {
        await fake.send({ t: 'ask', id: 'e'.repeat(32), tool: 'Bash', summary: 'rm -rf build/', ttl: 300, cat: [], why: '', passkey_required: true });
        await waitFor(p, `document.getElementById('apprPk')?.dataset.state==='nokey' && !document.getElementById('apprPk').hidden`);
        await sleep(250);
        await shot(p, ['#sheet'], `${OUT}/7-nokey-card-${tag}-${phase}.png`, 0);
        if (phase === 'after') {
          const g = await evaluate(p, `(()=>{const q=s=>document.querySelector(s),r=e=>e.getBoundingClientRect();
            const box=q('#apprPk'),line=box.querySelector('.pkassist__text'),note=box.querySelector('.pkassist__note'),btns=q('#apprBtns'),
              go=q('#apprGo'),deny=q('#apprDeny'),allow=q('#apprAllow');
            const pos=(a,b)=>!!(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING);
            return {words:pos(box,btns),noteFirst:pos(note,btns),goInRow:!!go&&go.parentElement===btns,goVisible:!!go&&!go.hidden&&go.getClientRects().length>0,
              allowHidden:allow.hidden,boxActs:box.querySelectorAll('button').length,
              lineBottom:r(note).bottom,btnTop:r(btns).top,goTop:go&&r(go).top,denyTop:r(deny).top,goMid:go&&(r(go).top+r(go).bottom)/2,denyMid:(r(deny).top+r(deny).bottom)/2,goH:go&&r(go).height,denyH:r(deny).height,
              goX:go&&r(go).left,denyX:r(deny).left,goText:go&&go.textContent.trim(),display:getComputedStyle(btns).display}})()`);
          audit.push({ card: tag, ...g });
          assert.ok(g.words && g.noteFirst, 'the words come before the button row in the DOM');
          assert.equal(g.boxActs, 0, 'no button left inside the words block');
          assert.ok(g.goInRow && g.goVisible, '去设置 sits in the card\'s own button row');
          assert.equal(g.allowHidden, true, '「用 Face ID 确认」 stays hidden');
          assert.equal(g.goText, lang === 'zh' ? '去设置' : 'Set up');
          assert.ok(g.btnTop >= g.lineBottom, `button row (${g.btnTop}) under the words (${g.lineBottom})`);
          assert.ok(g.goH >= 44 && g.denyH >= 44, 'tap targets ≥ 44 px');
          if (phone) assert.ok(g.denyTop > g.goTop, 'phone: the two buttons stack like every approval card (base.css ≤ 600 px)');
          else assert.ok(Math.abs(g.denyMid - g.goMid) < 1 && g.denyX > g.goX, 'computer: 去设置 · 拒绝 side by side on one row (拒绝 centred, as on every approval card)');
        }
      } finally { await p.dispose(); }
    });
  }
} finally {
  writeFileSync(`${OUT}/p563-audit-${phase}.json`, JSON.stringify(audit, null, 1));
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p563 (${phase}): ${failed} FAILED` : `p563 (${phase}): all passed`);
process.exit(failed ? 1 : 0);
