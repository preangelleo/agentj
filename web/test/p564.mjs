#!/usr/bin/env node
// P564 (董事长 10-28 10:4x): the chat header gear's remote-count badge — Lotion circle in every state, the digit = the page's
// state colour itself (var(--state), unmixed). Five states × light / dark × zh / en × 390 / 1440, plus a custom theme.
//   node web/test/p564.mjs                                      → after (asserts) + screenshots
//   P564_PHASE=before P564_PUBLIC=<HEAD copy>/web/public node … → before screenshots only
// Screens: $P564_OUT (default reports/qa/p564) /<phase>/<state>-<scheme>-<lang>-<w>.png — the header's right end (no link / token).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor, sleep } from './browser.mjs';

const phase = process.env.P564_PHASE === 'before' ? 'before' : 'after';
const OUT = resolve(process.env.P564_OUT || 'reports/qa/p564', phase);
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, publicDir: process.env.P564_PUBLIC ? resolve(process.env.P564_PUBLIC) + '/' : PUBLIC_DIR });
const fake = await startFakeHost(), B = await launch();
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const STATES = { idle: ['idle', ''], working: ['working', ''], waiting: ['waiting', 'permission'], question: ['waiting', 'question'], offline: ['unknown', ''] };
const rgbOf = (c) => { const m = /color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)/.exec(c); return m ? m.slice(1, 4).map((v) => Math.round(+v * 255)) : c.match(/[\d.]+/g).slice(0, 3).map(Number); };
const toHex = (c) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
const LOTION = '#faf9f5';
let failed = 0;
const audit = [];
const ok = (name, fn) => fn().then(() => console.log('  ✓ ' + name), (e) => { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); });

async function paired({ w, scheme, lang }) {
  const phone = w < 600;
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
  fake.remotes({ limit: 10, items: [{ id: 'ME', me: true }, ...Array.from({ length: 2 }, (_, i) => ({ id: 'r' + i }))] });
  const p = await newPage(B, w, phone ? 844 : 900, scheme, { touch: phone, ua: phone ? IPHONE : undefined, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await waitFor(p, `document.querySelector('#setBtn [data-remote-count]')?.textContent === '3'`);
  await evaluate(p, `(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme='${scheme}';
    const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);await document.fonts.ready})()`);
  return p;
}
// the header's right end: the gear with the page colour around it
async function shot(p, file) {
  const clip = await evaluate(p, `(()=>{const r=document.getElementById('setBtn').getBoundingClientRect();const x=Math.max(0,r.left-150),y=Math.max(0,r.top-14);
    return {x,y,width:Math.min(innerWidth,r.right+14)-x,height:r.bottom+14-y,scale:3}})()`);
  await sleep(120);
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip });
  writeFileSync(file, Buffer.from(data, 'base64'));
}
const BADGE = `(()=>{const b=document.querySelector('#setBtn [data-remote-count]'),c=getComputedStyle(b),i=document.createElement('i');i.style.color='var(--state)';document.body.append(i);const st=getComputedStyle(i).color;i.remove();
  return {fg:c.color,bg:c.backgroundColor,state:st,page:getComputedStyle(document.body).backgroundColor}})()`;

try {
  for (const lang of ['zh', 'en']) for (const w of [390, 1440]) for (const scheme of ['light', 'dark']) await ok(`${lang}-${w}-${scheme}`, async () => {
    const p = await paired({ w, scheme, lang });
    try {
      for (const [name, [s, kind]] of Object.entries(STATES)) {
        await fake.send({ t: 'status', s, kind, agent: 'claude', name: 'Agent J' });
        await waitFor(p, `document.body.dataset.status==='${s}'`);
        await sleep(250);
        const m = await evaluate(p, BADGE), fg = toHex(rgbOf(m.fg)), bg = toHex(rgbOf(m.bg)), state = toHex(rgbOf(m.state));
        await shot(p, `${OUT}/${name}-${scheme}-${lang}-${w}.png`);
        audit.push({ state: name, scheme, lang, w, phase, digit: fg, circle: bg, stateColour: state });
        if (phase === 'after') { assert.equal(bg, LOTION, name + ' circle'); assert.equal(fg, state, name + ' digit = var(--state)'); }
      }
      if (lang === 'zh' && w === 390) {
        const custom = { idle: '#ffe292', working: '#71bbde', waiting: '#ebc9b7', question: '#cacadb', offline: '#858585' };
        await evaluate(p, `(async()=>{(await import('./js/theme.js')).applyTheme({t:'theme',v:1,id:'custom',colors:{state:${JSON.stringify(custom)}}})})()`);
        for (const [name, [s, kind]] of Object.entries(STATES)) {
          await evaluate(p, `document.body.dataset.status='${s}';document.body.dataset.kind='${kind}'`);
          await sleep(120);
          const m = await evaluate(p, BADGE), fg = toHex(rgbOf(m.fg)), bg = toHex(rgbOf(m.bg));
          await shot(p, `${OUT}/custom-${name}-${scheme}-zh-390.png`);
          audit.push({ state: 'custom-' + name, scheme, lang, w, phase, digit: fg, circle: bg });
          if (phase === 'after') { assert.equal(bg, LOTION, 'custom ' + name + ' circle'); assert.equal(fg, custom[name], 'custom ' + name + ' digit'); }
        }
      }
    } finally { await p.dispose(); }
  });
} finally {
  writeFileSync(`${OUT}/audit.json`, JSON.stringify(audit, null, 1));
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p564 (${phase}): ${failed} FAILED` : `p564 (${phase}): all passed`);
process.exit(failed ? 1 : 0);
