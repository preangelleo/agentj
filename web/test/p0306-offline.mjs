// 0.30.6 (chairman 11-10 12:14 「上面不允许插入通知栏，所有的通知和按钮都是中间弹出来的那种组件」) on the real page against the
// local fake host (real Noise session, never the production relay):
//   · a drop: the header link dot (#linkDot, visible, green while connected) turns grey at once; no row is inserted between the header and the stage; no popup before 10 s
//   · down (relay answers, host offline) → #connDlg[data-state=down] after ≥ 10 s; reconnect → data-state=up, gone by itself
//   · reconnecting (this device offline: CDP network offline) → #connDlg[data-state=reconnecting]
//   · needs update (a page older than version.json) → #connDlg[data-state=update] with 更新并刷新 inside; #version-notice yields
// zh × light / dark × 390 / 1440 (+ en 390 dark). Shots → reports/qa/p0306-offline/{before,after}/ (no token / link / address bar).
// P0306_BEFORE=<old public dir> = the before shots of the old top row (down state only).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, cpSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep } from './browser.mjs';

const BEFORE = process.env.P0306_BEFORE || '';
const OUT = join(process.env.P0306_OUT || 'reports/qa/p0306-offline', BEFORE ? 'before' : 'after');
mkdirSync(OUT, { recursive: true });
// the needs-update page: this page with an older version.js; version.json (the newer one) unchanged
const OLD = join(homedir(), '.cache/0306-ui/old-page');
if (!BEFORE) {
  if (existsSync(OLD)) rmSync(OLD, { recursive: true });
  cpSync(PUBLIC_DIR, OLD, { recursive: true });
  const vjs = join(OLD, 'version.js');
  writeFileSync(vjs, readFileSync(vjs, 'utf8').replace(/"version": "[^"]+"/, '"version": "0.0.1"'));
}
const web = await startWebServer(BEFORE ? { publicDir: BEFORE } : {}), old = BEFORE ? null : await startWebServer({ publicDir: OLD });
const fake = await startFakeHost(), browser = await launch();
let checks = 0;
const check = (ok, label) => { assert.ok(ok, label); checks++; console.log('PASS', label); };
const STILL = `(()=>{const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);return true})()`;
// visible things between the header and the stage (the forbidden 「row under the top bar」)
const ROWS = `[...document.querySelectorAll('#chat-view > *')].filter(n=>n.id!=='stage'&&!n.classList.contains('meter')&&n.getClientRects().length&&n.getBoundingClientRect().height>0&&n.getBoundingClientRect().bottom<=document.getElementById('stage').getBoundingClientRect().top+1).map(n=>n.id||n.className)`;
// fix1 (QA 1): the header link dot itself — must be on screen (non-zero size, inside the header, not covered at its centre)
const DOT = `(()=>{const d=document.querySelector('header.top #linkDot');if(!d)return {vis:false,why:'none'};const r=d.getBoundingClientRect(),h=d.closest('header').getBoundingClientRect(),cs=getComputedStyle(d);`
  + `const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);const o=document.getElementById('orb').getBoundingClientRect();`
  + `return {vis:r.width>=12&&r.height>=12&&cs.visibility!=='hidden'&&+cs.opacity>0.9&&r.top>=h.top&&r.bottom<=h.bottom&&r.left>=0&&r.right<=innerWidth,`
  + `onOrb:r.left<o.right&&r.right>o.left&&r.top<o.bottom&&r.bottom>o.top,top:!!hit&&(hit===d||hit===document.getElementById('orb')||hit.closest('header')===d.closest('header')),`
  + `w:r.width,bg:cs.backgroundColor,ring:cs.borderTopColor}})()`;
const colorOf = (v) => `(()=>{const c=document.createElement('i');c.style.color=getComputedStyle(document.body).getPropertyValue(${JSON.stringify(v)}).trim();document.body.append(c);const x=getComputedStyle(c).color;c.remove();return x})()`;
const shown = (st) => `(()=>{const d=document.getElementById('connDlg');return !!d&&!d.hidden&&d.dataset.state===${JSON.stringify(st)}&&d.getClientRects().length>0})()`;

async function paired(url, width, theme, lang) {
  fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language: lang } };
  const p = await newPage(browser, width, 844, theme, { allow: [url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();true`);
  await evaluate(p, STILL);
  await fake.say(lang === 'zh' ? '这一轮正常跑完了，您不用处理。' : 'This round finished normally; nothing to do.', 'agent');
  await sleep(300);
  return p;
}

try {
  const combos = process.env.P0306_QUICK ? [['zh', 'dark', 390]] : [['zh', 'light', 390], ['zh', 'dark', 390], ['zh', 'light', 1440], ['zh', 'dark', 1440], ['en', 'dark', 390]];
  for (const [lang, theme, width] of combos) {
    const tag = `${lang}-${theme}-${width}`;
    // ---- down → reconnected
    let p = await paired(web.url, width, theme, lang);
    try {
      if (BEFORE) {
        fake.setUp(false); await waitFor(p, `document.body.dataset.conn==='off'`, 15000); await sleep(400);
        writeFileSync(`${OUT}/${tag}-down.png`, await shoot(p)); console.log('shot before', tag); continue;
      }
      const live = await evaluate(p, DOT), green = await evaluate(p, colorOf('--c-shield')), offC = await evaluate(p, colorOf('--c-offline'));
      check(live.vis && live.onOrb && live.top, `the header link dot is on screen, on the logo ${tag} ${JSON.stringify(live)}`);
      check(live.bg === green, `connected = Shield green (${live.bg}) ${tag}`);
      if (lang === 'zh') writeFileSync(`${OUT}/${tag}-connected.png`, await shoot(p));
      const t0 = Date.now(); fake.setUp(false);
      await waitFor(p, `document.body.hasAttribute('data-relink')`, 3000);
      const grey = await evaluate(p, DOT);
      check(grey.vis && grey.bg === offC && grey.bg !== live.bg, `the visible dot turns grey at once (${live.bg} → ${grey.bg}) ${tag}`);
      if (lang === 'zh') writeFileSync(`${OUT}/${tag}-dot.png`, await shoot(p));
      check((await evaluate(p, ROWS)).length === 0, `no row under the header during the drop ${tag}`);
      check(await evaluate(p, `document.getElementById('connDlg').hidden`), `no popup in the first seconds ${tag}`);
      await waitFor(p, `document.body.dataset.conn==='off'`, 15000);
      await sleep(Math.max(0, 8000 - (Date.now() - t0)));
      check(await evaluate(p, `document.getElementById('connDlg').hidden`) && (await evaluate(p, ROWS)).length === 0, `still no popup and no row at 8 s ${tag}`);
      await waitFor(p, shown('down'), 15000);
      const ms = Date.now() - t0;
      check(ms >= 9800, `popup only after 10 s (${ms} ms) ${tag}`);
      check(!(await evaluate(p, `document.getElementById('connection-help')`)), `the old #connection-help row is gone ${tag}`);
      check((await evaluate(p, ROWS)).length === 0, `still no row under the header ${tag}`);
      const geo = await evaluate(p, `(()=>{const s=document.querySelector('#connDlg .sq').getBoundingClientRect();const b=[...document.querySelectorAll('#connDlg button')].filter(b=>!b.hidden).map(b=>{const r=b.getBoundingClientRect();return{h:r.height,w:r.width}});return{cx:s.x+s.width/2,cy:s.y+s.height/2,vw:innerWidth,vh:innerHeight,b,focus:document.activeElement?.closest?.('#connDlg')?1:0}})()`);
      check(Math.abs(geo.cx - geo.vw / 2) < 2 && Math.abs(geo.cy - geo.vh / 2) < 40, `centred ${tag} ${JSON.stringify(geo)}`);
      check(geo.b.length === 2 && geo.b.every(x => x.h >= 44), `two ≥ 44 px buttons (稍后 · 立即重试) ${tag}`);
      check(geo.focus === 0, `the popup does not take focus ${tag}`);
      writeFileSync(`${OUT}/${tag}-down.png`, await shoot(p));
      fake.setUp(true); await waitState(p, 'ready', 15000);
      await waitFor(p, shown('up'), 3000);
      writeFileSync(`${OUT}/${tag}-reconnected.png`, await shoot(p));
      await waitFor(p, `document.getElementById('connDlg').hidden`, 4000);
      check(true, `reconnected says so, then closes by itself ${tag}`);
      // Esc = later; it stays away for the rest of that outage
      fake.setUp(false); await waitFor(p, shown('down'), 25000);
      await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
      check(await evaluate(p, `document.getElementById('connDlg').hidden`), `Esc puts it away ${tag}`);
      await sleep(1500);
      check(await evaluate(p, `document.getElementById('connDlg').hidden`), `…for the rest of this outage ${tag}`);
      fake.setUp(true); await waitState(p, 'ready', 15000); await sleep(300);
      check(await evaluate(p, `document.getElementById('connDlg').hidden`), `a dismissed outage ends quietly ${tag}`);
      // ---- reconnecting: this device is offline (the relay itself is unreachable)
      await p.send('Network.enable');
      await p.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
      fake.kick(1001);
      await waitFor(p, shown('reconnecting'), 30000);
      check(await evaluate(p, `window.__ajState`) === 'connecting', `reconnecting = this device cannot reach the relay ${tag}`);
      writeFileSync(`${OUT}/${tag}-reconnecting.png`, await shoot(p));
      await p.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
      await evaluate(p, `dispatchEvent(new Event('online'));true`);
      await waitState(p, 'ready', 20000);
      check(true, `back online → ready ${tag}`);
      assert.equal(p.problems.filter((x) => !/WebSocket|ERR_|net::/i.test(x)).length, 0, 'no page errors ' + tag);
    } finally { await p.dispose(); }
    if (BEFORE) continue;
    // ---- needs update: this page is older than version.json
    p = await paired(old.url, width, theme, lang);
    try {
      await waitFor(p, `!document.getElementById('version-notice').hidden`, 15000);
      await evaluate(p, `document.getElementById('version-dismiss').click();true`);
      await evaluate(p, `document.getElementById('version-notice').hidden=false;true`);   // as if not dismissed: the popup must make it yield
      fake.setUp(false);
      await waitFor(p, shown('update'), 25000);
      check(await evaluate(p, `!document.getElementById('connRefresh').hidden && document.getElementById('connRetry').hidden && document.getElementById('version-notice').hidden`), `needs update: 更新并刷新 inside the popup, the version dialog yields ${tag}`);
      check((await evaluate(p, ROWS)).length === 0, `no row under the header (update) ${tag}`);
      writeFileSync(`${OUT}/${tag}-update.png`, await shoot(p));
      await evaluate(p, `window.__beforeRefresh=1;document.getElementById('connRefresh').click();true`);
      await waitFor(p, `!window.__beforeRefresh`, 15000);
      check(true, `更新并刷新 reloads the page ${tag}`);
    } finally { await p.dispose(); }
  }
  console.log(`PASS p0306-offline: ${checks} checks`);
} finally { await browser.close(); await fake.stop(); await web.stop(); await old?.stop(); }
