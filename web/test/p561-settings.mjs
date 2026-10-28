#!/usr/bin/env node
// P561 追加 16 / 18 (董事长 10-28): Settings — words never touch the button under / over them (发送画面诊断, 重启服务 and every
// other text + button block), 「本机文件访问」 has its line icon and chevron, the shortcut hints come from the shortcut table
// (desktop only; a phone shows none), the upgrade note names /update; the gear's remote-count digit wears the state colour
// (idle / working / waiting / question / offline, light and dark, a custom theme).
//   node web/test/p561-settings.mjs                      → after (asserts) + screenshots
//   P561_PHASE=before P561_PUBLIC=<old tree>/web/public node …  → before screenshots + the audit only
// Screens: $P561_SHOTS (default reports/qa/p561/settings) /{before,after}/…; the gear crops under …/badge/{before,after}/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor, sleep } from './browser.mjs';

const phase = process.env.P561_PHASE === 'before' ? 'before' : 'after';
const root = resolve(process.env.P561_SHOTS || 'reports/qa/p561');
const OUT = resolve(root, 'settings', phase), OUT_BADGE = resolve(root, 'badge', phase);
mkdirSync(OUT, { recursive: true }); mkdirSync(OUT_BADGE, { recursive: true });
const web = await startWebServer({ port: 0, publicDir: process.env.P561_PUBLIC ? resolve(process.env.P561_PUBLIC) + '/' : PUBLIC_DIR });
const fake = await startFakeHost(), B = await launch();
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
const STATES = { idle: ['idle', ''], working: ['working', ''], waiting: ['waiting', 'permission'], question: ['waiting', 'question'], offline: ['unknown', ''] };
const audit = [];
// A computed colour (rgb() or color(srgb …), what Chromium reports for color-mix) → [r, g, b] 0–255
const rgbOf = (c) => { const m = /color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)/.exec(c); return m ? m.slice(1, 4).map((v) => +v * 255) : c.match(/[\d.]+/g).slice(0, 3).map(Number); };
const LOTION = [250, 249, 245];
/** P561 追加 18 · P563 追加 1: the digit is --state itself in every state (no color-mix — closed palette); idle on P517's Lotion
 *  circle, every other state on a Sooty circle (≥ 4.5:1) */
const want = (name, state) => rgbOf(state);
const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
const near = (got, exp, label) => assert.ok(got.every((v, i) => Math.abs(v - exp[i]) <= 1.5), `${label}: ${got.map(Math.round)} ≠ ${exp.map(Math.round)}`);
let failed = 0;
const ok = (name, fn) => fn().then(() => console.log('  ✓ ' + name), (e) => { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); });

async function paired({ w, scheme, lang, phone, mac }) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
  fake.remotes({ limit: 10, items: [{ id: 'ME', me: true }, ...Array.from({ length: 6 }, (_, i) => ({ id: 'r' + i }))] });
  const p = await newPage(B, w, phone ? 844 : 900, scheme, { touch: phone, ua: phone ? IPHONE : mac ? MAC : undefined, allow: [web.url, fake.relay + '/'] });
  if (mac) await p.send('Emulation.setUserAgentOverride', { userAgent: MAC, platform: 'MacIntel' });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await waitFor(p, `document.querySelector('#setBtn [data-remote-count]')?.textContent === '7'`);
  await evaluate(p, `(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme='${scheme}';
    const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);await document.fonts.ready})()`);
  return p;
}
const openSettings = async (p) => { await evaluate(p, `document.getElementById('setBtn').click()`); await waitFor(p, `!document.getElementById('settings').hidden`); await sleep(250); };

/** Scroll the first selector to the middle, clip the union of every visible selector (viewport-clamped) and save it. */
async function shot(p, sels, file) {
  const clip = await evaluate(p, `(()=>{const els=${JSON.stringify(sels)}.map(s=>document.querySelector(s)).filter(e=>e&&e.getClientRects().length);if(!els.length)return null;
    els[0].scrollIntoView({block:'center'});const rs=els.map(e=>e.getBoundingClientRect());const pad=12;
    const x=Math.max(0,Math.min(...rs.map(r=>r.left))-pad),y=Math.max(0,Math.min(...rs.map(r=>r.top))-pad);
    const r=Math.min(innerWidth,Math.max(...rs.map(r=>r.right))+pad),b=Math.min(innerHeight,Math.max(...rs.map(r=>r.bottom))+pad);
    return {x,y,width:r-x,height:b-y,scale:1}})()`);
  if (!clip) return null;
  await sleep(120);
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip });
  writeFileSync(file, Buffer.from(data, 'base64'));
  return file;
}

// Every visible Settings button (or button group) and the text block right above / below it in the same parent:
// the gap between the text's last line and the button's edge, in px. The floor is the token --ui-space-control.
const SWEEP = `(()=>{const S=document.getElementById('settings'),out=[];
  const vis=e=>e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const texty=e=>vis(e)&&!e.matches('button,.aj-btn,.rsacts,.tgl,select,input,hr')&&!e.querySelector('button,.aj-btn,input,select')&&e.textContent.trim().length>0;
  const units=new Set();
  for(const b of S.querySelectorAll('.aj-btn')){if(!vis(b))continue;const u=b.parentElement.matches('.rsacts,.setbtns,.btnrow')?b.parentElement:b;units.add(u)}
  for(const u of units){const ur=u.getBoundingClientRect(),kids=[...u.parentElement.children].filter(k=>k!==u&&texty(k));
    for(const dir of ['above','below']){
      // the text block visually right above / below (grid / order can move a button under its notes)
      let best=null,gap=Infinity;
      for(const k of kids){const r=k.getBoundingClientRect();if(Math.min(r.right,ur.right)-Math.max(r.left,ur.left)<=0)continue;
        const g=dir==='above'?ur.top-r.bottom:r.top-ur.bottom;if(g>=-2&&g<gap){gap=g;best=k}}
      if(!best)continue;
      const id=(u.id||u.querySelector('[id]')?.id||u.className)+'';
      out.push({button:id,dir,text:best.textContent.trim().slice(0,24),gap:Math.round(gap*10)/10});
    }}
  return out})()`;

const tokenPx = (p, name) => evaluate(p, `(()=>{const i=document.createElement('i');i.style.cssText='position:absolute;height:var(${name})';document.getElementById('settings').append(i);const h=i.getBoundingClientRect().height;i.remove();return h})()`);

try {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, phone] of [[390, true], [1280, false]]) {
    const tag = `${w}-${lang}-${scheme}`;
    await ok('settings ' + tag, async () => {
      const p = await paired({ w, scheme, lang, phone });
      try {
        await openSettings(p);
        const rows = await evaluate(p, SWEEP), floor = await tokenPx(p, '--ui-space-control');
        const bad = rows.filter((r) => r.gap < floor);
        audit.push({ tag, phase, floor, bad, rows: rows.length });
        await shot(p, ['#layout-diag-card', '#rs-restart'], `${OUT}/${tag}-rescue.png`);
        await shot(p, ['#set-local-files-nav', '#set-hooks-nav'], `${OUT}/${tag}-files.png`);
        await shot(p, ['#set-s-safety .setg', '.setrow:has(> #set-estop)'], `${OUT}/${tag}-estop.png`);
        await shot(p, ['.setrow:has(> #set-hostupg)'], `${OUT}/${tag}-upgrade.png`);
        if (phase === 'after') {
          assert.deepEqual(bad, [], 'text ↔ button closer than --ui-space-control');
          const g = await evaluate(p, `(()=>{const q=s=>document.querySelector(s),r=s=>q(s).getBoundingClientRect();
            return {diag:r('#layout-diag-card .setnote').top-r('#layout-diag-send').bottom,
              restart:r('#rs-restart .rsacts').top-r('#rs-restart-note').bottom,
              icon:!!q('#set-local-files-nav > svg.mi'),chev:!!q('#set-local-files-nav > svg.chev'),
              iconSize:[r('#set-local-files-nav > svg.mi').width,r('#open-models > svg.mi').width],
              iconColor:[getComputedStyle(q('#set-local-files-nav > svg.mi')).color,getComputedStyle(q('#open-models > svg.mi')).color],
              estopKbd:!q('#set-estop .setkbd').hidden?q('#set-estop .setkbd').textContent:'',
              upg:q('#set-hostupg-how').textContent,upgKbd:[...document.querySelectorAll('#set-hostupg-how kbd')].map(k=>k.textContent).join('+')}})()`);
          const content = await tokenPx(p, '--ui-space-content');
          assert.ok(g.diag >= floor, 'diag note gap ' + g.diag);
          assert.ok(g.restart >= content, 'restart note → buttons gap ' + g.restart);
          assert.ok(g.icon && g.chev, 'local files icon + chevron');
          assert.equal(g.iconSize[0], g.iconSize[1]); assert.equal(g.iconColor[0], g.iconColor[1]);
          // P563 追加 2: app.stop_all has Alt+Shift+S in the table → the button shows it on a desktop, a phone shows none
          assert.equal(g.estopKbd, phone ? '' : 'Alt+Shift+S');
          assert.match(g.upg, /\/update/);
          if (phone) assert.equal(g.upgKbd, '', 'a phone shows no key');
          else assert.equal(g.upgKbd, 'Alt+Shift+U', 'desktop shows the table key');
        }
      } finally { await p.dispose(); }
    });
  }
  if (phase === 'after') await ok('settings mac key glyphs', async () => {
    const p = await paired({ w: 1280, scheme: 'light', lang: 'zh', phone: false, mac: true });
    try {
      await openSettings(p);
      assert.equal(await evaluate(p, `[...document.querySelectorAll('#set-hostupg-how kbd')].map(k=>k.textContent).join('+')`), '⌥+Shift+U');
      await shot(p, ['.setrow:has(> #set-hostupg)'], `${OUT}/1280-zh-light-upgrade-mac.png`);
    } finally { await p.dispose(); }
  });

  // P561 追加 18: the gear's digit follows the state colour, on the chat header, five states, light / dark, then a custom theme
  for (const scheme of ['light', 'dark']) await ok('badge ' + scheme, async () => {
    const p = await paired({ w: 390, scheme, lang: 'zh', phone: true });
    try {
      for (const [name, [s, kind]] of Object.entries(STATES)) {
        await fake.send({ t: 'status', s, kind, agent: 'claude', name: 'Agent J' });
        await waitFor(p, `document.body.dataset.status==='${s}'`);
        await sleep(200);
        const m = await evaluate(p, `(()=>{const i=document.createElement('i');i.style.color='var(--state)';document.body.append(i);const state=getComputedStyle(i).color;i.remove();
          const c=getComputedStyle(document.querySelector('#setBtn [data-remote-count]'));return {state,badge:c.color,circle:c.backgroundColor}})()`);
        await shot(p, ['#setBtn'], `${OUT_BADGE}/${scheme}-${name}.png`);
        audit.push({ badge: name, scheme, phase, ...m });
        if (phase === 'after') {
          near(rgbOf(m.badge), want(name, m.state), name);
          near(rgbOf(m.circle), LOTION, name + ' circle');   // P564: Lotion in every state
        }
      }
      if (phase === 'after') {
        const custom = { idle: 'rgb(255, 226, 146)', working: 'rgb(113, 187, 222)', waiting: 'rgb(235, 201, 183)', question: 'rgb(202, 202, 219)', offline: 'rgb(133, 133, 133)' };
        await evaluate(p, `(async()=>{(await import('./js/theme.js')).applyTheme({t:'theme',v:1,id:'custom',colors:{state:{idle:'#ffe292',working:'#71bbde',waiting:'#ebc9b7',question:'#cacadb',offline:'#858585'}}})})()`);
        for (const [name, [s, kind]] of Object.entries(STATES)) {
          await evaluate(p, `document.body.dataset.status='${s}';document.body.dataset.kind='${kind}'`);
          const c = await evaluate(p, `(()=>{const c=getComputedStyle(document.querySelector('#setBtn [data-remote-count]'));return {fg:c.color,bg:c.backgroundColor}})()`);
          near(rgbOf(c.fg), want(name, custom[name]), 'custom ' + name); near(rgbOf(c.bg), LOTION, 'custom ' + name + ' circle');
          if (scheme === 'light') await shot(p, ['#setBtn'], `${OUT_BADGE}/custom-${name}.png`);
        }
      }
    } finally { await p.dispose(); }
  });
} finally {
  writeFileSync(`${OUT}/audit.json`, JSON.stringify(audit, null, 1));
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p561-settings (${phase}): ${failed} FAILED` : `p561-settings (${phase}): all passed`);
process.exit(failed ? 1 : 0);
