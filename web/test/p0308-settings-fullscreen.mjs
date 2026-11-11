#!/usr/bin/env node
// 0308 hotfix (chairman 11-11): Settings is a PAGE that follows the whole screen (as in 0.30.6), not a fixed centred panel.
// Only the interactions Settings starts (secret / auth-code / QR / confirm cards) are centred popups.
// Size regression: at 1440 × 900 the Settings page is ≥ 90 % of the viewport wide (and at 390 too); every sub-page —
// in-Settings (遥控器管理, 邮件 …) and the pages Settings hands off to (模型和 API Key, 记忆 …) — is the same full-screen size.
// Plus one settings-initiated secret card (Settings → 邮件 → 「设置邮箱授权码」): visible, centred, Settings still full behind it.
// zh + en × light + dark × 390 × 844 and 1440 × 900. Local fake host + formal built client; no pairing, no external requests.
// Shots → $AJ_P0308_OUT (default <repo>/.jarvis/out/p0308-settings-fullscreen/). AJ_P0308_SIZE_ONLY=1 skips screenshots.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { randomBytes, webcrypto } from 'node:crypto';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, key, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = (process.env.AJ_P0308_OUT || join(ROOT, '.jarvis/out/p0308-settings-fullscreen')).replace(/\/?$/, '/');
const SIZE_ONLY = process.env.AJ_P0308_SIZE_ONLY === '1';
const ONLY = process.env.AJ_P0308_ONLY || '';
mkdirSync(OUT, { recursive: true });
const J = JSON.stringify;
const hex = () => randomBytes(16).toString('hex');
const b64u = (b) => Buffer.from(b).toString('base64url');
let checks = 0;
// AJ_P0308_SOFT=1 (baseline capture on an older tree only): record failed checks instead of stopping.
const SOFT = process.env.AJ_P0308_SOFT === '1', soft = [];
const check = (v, label) => { if (SOFT && !v) { soft.push(label); console.log('SOFT-FAIL', label); return; } assert.ok(v, label); checks++; };

// Sub-pages inside Settings (showSub) and the pages Settings hands off to (fromSettings → leavePage returns to Settings).
const INNER = [['remotes', 'set-remotes-open'], ['ios', 'set-a2hs'], ['mail', 'set-mail-nav'], ['contacts', 'set-contacts-nav'],
  ['auth', 'set-auth-nav'], ['imessage', 'set-imsg-nav'], ['cards', 'set-cards-nav'], ['notify', 'set-notify-nav'],
  ['hooks', 'set-hooks-nav'], ['files', 'set-local-files-nav'], ['accts', 'set-accts-nav']];
const OUTER = [['models', 'open-models'], ['memory', 'open-mem'], ['activity', 'open-act'], ['tasks', 'open-tasks'],
  ['friends', 'open-friends'], ['bots', 'open-bots'], ['setup', 'open-setup'], ['terminal', 'set-term-open']];

// The page on screen = outermost fixed / top-layer ancestor of whatever is at the viewport centre.
const PAGE = `(()=>{const vw=innerWidth,vh=innerHeight;let e=document.elementFromPoint(vw/2,vh/2),pg=null;
  for(;e&&e!==document.documentElement;e=e.parentElement){const cs=getComputedStyle(e);if(cs.position==='fixed'||(e.tagName==='DIALOG'&&e.open))pg=e;}
  if(!pg)return null;const r=pg.getBoundingClientRect();return {id:pg.id||pg.className,x:r.x,y:r.y,w:r.width,h:r.height,vw,vh};})()`;
const SET = `(()=>{const o=document.getElementById('settings'),b=document.querySelector('#settings>.setbox'),r=b.getBoundingClientRect(),
  cs=getComputedStyle(b);return {open:!o.hidden,x:r.x,y:r.y,w:r.width,h:r.height,vw:innerWidth,vh:innerHeight,mode:getComputedStyle(o).display,
  radius:cs.borderTopLeftRadius,bg:cs.backgroundColor,pageBg:getComputedStyle(document.body).backgroundColor,overlayBg:getComputedStyle(o).backgroundColor};})()`;
const full = (r) => r && r.w >= 0.9 * r.vw && r.h >= 0.85 * r.vh;

const S = { lang: 'zh', card: null };
const VIEW = { config: { address: 'agent@example.com', provider: 'qq', imap_host: 'imap.qq.com', smtp_host: 'smtp.qq.com', enabled: false },
  presets: [], has_password: false, today: {}, blocked: [] };
const handler = async (c, m, send) => {
  if (typeof m.t !== 'string' || !m.t.startsWith('mail_')) return false;
  if (m.t === 'mail_key') {
    const pair = await webcrypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    S.card = { t: 'elev', id: hex(), n: hex(), kind: 'secret', epk: b64u(new Uint8Array(await webcrypto.subtle.exportKey('raw', pair.publicKey))),
      ttl: 600, tries: 3, name: 'AGENTJ_MAIL_PASSWORD', purpose: S.lang === 'zh' ? '邮件通道登录 QQ 邮箱用的授权码' : 'the app password the mail channel uses for QQ Mail',
      dest: S.lang === 'zh' ? 'Agent J 邮件通道（本机加密）' : 'Agent J mail channel (encrypted on the host)', verify: 'IMAP + SMTP login', request: m.r };
    await send(S.card);
    return true;
  }
  await send({ t: 'mail_res', r: m.r, ok: true, view: VIEW });
  return true;
};

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const index = [];
const save = async (p, name, note) => { if (SIZE_ONLY) return; const f = name + '.png'; writeFileSync(OUT + f, await shoot(p, '')); index.push([f, note]); };
const openSettings = async (p) => {
  if (await evaluate(p, `!document.getElementById('settings').hidden`)) return;
  await evaluate(p, `document.getElementById('setBtn').click();true`);
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await sleep(150);
};
try {
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const tag = `${lang}-${theme}-${w}`;
    if (ONLY && ONLY !== tag) continue;
    fake.reset(); fake.setUp(true); fake.st.onApp = handler; S.lang = lang;
    fake.st.prefSet = true; fake.st.prefs = { appearance: { language: lang, theme } };
    const p = await newPage(B, w, h, theme, { touch: w < 500, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();true`);
      await sleep(200);
      await openSettings(p);
      // ---- the size regression: Settings follows the whole screen, in the page's own theme (not a Noir panel) ----
      const s = await evaluate(p, SET);
      check(s.mode === 'block', `${tag}: Settings is a page (display:block), got ${s.mode}`);
      check(s.w >= 0.9 * s.vw, `${tag}: Settings width ${s.w} ≥ 90 % of viewport ${s.vw}`);
      check(s.h >= 0.9 * s.vh && s.x <= 1 && s.y <= 1, `${tag}: Settings fills the height from the top-left ${J(s)}`);
      check(s.radius === '0px', `${tag}: no panel corners ${s.radius}`);
      check(s.bg === s.overlayBg, `${tag}: no panel-on-backdrop (frame and page share one background) ${J(s)}`);
      await save(p, `${tag}-00-settings`, 'Settings main page');
      const sizes = { settings: { w: s.w, h: s.h, bg: s.bg } };
      for (const [name, id] of INNER) {
        await openSettings(p);
        if (!await evaluate(p, `(()=>{const b=document.getElementById(${J(id)});return !!b&&!b.closest('[hidden]')&&b.getClientRects().length>0})()`)) { sizes[name] = 'not shown'; continue; }
        await evaluate(p, `document.getElementById(${J(id)}).click();true`);
        await sleep(300);
        const r = await evaluate(p, SET);
        check(r.open && full(r) && Math.abs(r.w - s.w) < 2 && Math.abs(r.h - s.h) < 2, `${tag} ${name}: same full-screen frame as Settings ${J(r)}`);
        sizes[name] = { w: r.w, h: r.h };
        await save(p, `${tag}-10-${name}`, `Settings → ${name}`);
        if (await evaluate(p, `!document.getElementById('set-back').hidden`)) await evaluate(p, `document.getElementById('set-back').click();true`);
        await sleep(120);
      }
      for (const [name, id] of OUTER) {
        await openSettings(p);
        if (!await evaluate(p, `(()=>{const b=document.getElementById(${J(id)});return !!b&&!b.closest('[hidden]')&&b.getClientRects().length>0})()`)) { sizes[name] = 'not shown'; continue; }
        await evaluate(p, `document.getElementById(${J(id)}).click();true`);
        await sleep(450);
        const r = await evaluate(p, PAGE);
        if (!r) { sizes[name] = 'nothing opened'; await key(p, 'Escape'); continue; }
        check(full(r), `${tag} ${name}: full-screen page ${J(r)}`);
        sizes[name] = { w: r.w, h: r.h, page: r.id };
        await save(p, `${tag}-20-${name}`, `Settings → ${name} (own page)`);
        await key(p, 'Escape'); await sleep(250);
        if (!await evaluate(p, `document.getElementById('settings').hidden`)) continue;
        await key(p, 'Escape'); await sleep(150);   // a page with an inner layer: second Esc
      }
      index.push([`${tag}-sizes`, J(sizes)]);
      // ---- requirement 2 kept: a settings-initiated secret card is a centred popup over the full Settings page ----
      await openSettings(p);
      await evaluate(p, `document.getElementById('set-mail-nav').click();true`);
      await waitFor(p, `!!document.getElementById('mail-key') && !document.getElementById('mail-key').disabled`, 10000);
      await evaluate(p, `document.getElementById('mail-key').click();true`);
      await waitFor(p, `!!document.getElementById('elev') && !document.getElementById('elev').hidden`, 15000);
      await sleep(400);
      const c = await evaluate(p, `(()=>{const e=document.getElementById('elev'),b=(e.querySelector('.box,.sheet,.card')||e).getBoundingClientRect(),
        s=document.querySelector('#settings>.setbox').getBoundingClientRect();
        return {sys:e.dataset.popupSystem||'',ctx:e.dataset.popupContext||'',x:b.x,y:b.y,w:b.width,h:b.height,vw:innerWidth,vh:innerHeight,setW:s.width,
          setOpen:!document.getElementById('settings').hidden}})()`);
      check(c.setOpen && c.setW >= 0.9 * c.vw, `${tag}: Settings stays full behind the secret card ${J(c)}`);
      check(c.ctx === 'settings' && c.sys === 'center', `${tag}: secret card is the centred popup system in Settings, not grown from the quota line ${J(c)}`);
      check(Math.abs(c.x + c.w / 2 - c.vw / 2) < 3, `${tag}: secret card horizontally centred ${J(c)}`);
      check(Math.abs(c.y + c.h / 2 - c.vh / 2) < 3 || (c.y >= 0 && c.y + c.h <= c.vh + 1 && c.h > 0.85 * c.vh), `${tag}: secret card vertically centred (or fills a short screen) ${J(c)}`);
      await save(p, `${tag}-30-secret-card`, 'Settings → 邮件 → 设置邮箱授权码: centred secret card over the full-screen Settings');
      check(p.offsite.length === 0, `${tag}: local-only requests`);
      console.log('ok', tag);
    } finally { await p.dispose(); }
  }
  writeFileSync(OUT + 'browser.json', J({ pass: soft.length === 0, checks, soft, index }, null, 2));
  console.log(`p0308-settings-fullscreen: ${checks} checks PASS → ${OUT}`);
} catch (e) {
  console.error('p0308-settings-fullscreen FAILED:', e && e.stack || e);
  process.exitCode = 1;
} finally { await B.close(); await fake.stop(); await web.stop(); }
