// P366 · Settings → Webhook — the real page against the local fake host (real Noise session), own headless Chromium (never
// :9222). Checks:
//   · the 「Webhook」 row under 「Agent 的功能」 opens its own sub-page (title, back, Esc), Noir canvas in both themes
//   · today's count + 在线; live rows (resident first, pending 「等你批准」, paused + 恢复), ended greyed without buttons, 最近投递
//   · every button ≥ 44 px tall, buttons in a row ≥ 8 px apart, no horizontal overflow at 390 px
//   · 暂停 → hook_set pause (the row redraws with 恢复); 撤销 → the confirm card names the hook, 取消 sends nothing, 撤销 sends
//     hook_set revoke and the row moves to 已结束
//   · 复制地址 puts the URL in the clipboard (read back); a token hook copies nothing and says the full address is on the computer
//   · `hook_changed` from the host → one re-read while open, none after leaving
//   · the empty state (one paragraph + the two routes), English
// Shots (made-up hooks only: no account, token or pairing link; no address bar): reports/qa/p366/after/<lang>-<theme>-<w>x<h>.png
// at 390×844 and 1440×900, light + dark (+ en). P366_BEFORE=<a public/ dir of the base tree> shoots Settings → 「Agent 的功能」
// of the old page into reports/qa/p366/before/ instead (no Webhook row there).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key, rect } from './browser.mjs';
import { hooksHandler, HID } from './p366-fixture.mjs';

const BEFORE = process.env.P366_BEFORE || '';
const OUT = new URL(`../../../reports/qa/p366/${BEFORE ? 'before' : 'after'}/`, import.meta.url);
mkdirSync(OUT, { recursive: true });
const SIZES = [[390, 844], [1440, 900]];
let n = 0;
const ok = (c, name) => { assert.ok(c, name); n++; console.log('PASS ' + name); };
const $ = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const row = (hid) => `#hooks-box .whrow[data-id="${hid}"]`;

const web = await startWebServer(BEFORE ? { publicDir: BEFORE } : {});
const fake = await startFakeHost();
fake.st.extraCaps = [...(fake.st.extraCaps || []), 'hooks1'];   // P366: this computer answers hook_list / hook_set
const B = await launch();
async function click(p, sel) {
  await evaluate(p, `${$(sel)}.scrollIntoView({block:'center'})`); await sleep(80);
  const r = await rect(p, sel);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
}
let lang = 'zh';
async function view(p, w, h, scheme) {
  await p.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
  await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme }] });
  await fake.send({ t: 'preferences', value: { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false } }, problem: null });
  await waitFor(p, `document.documentElement.getAttribute('data-theme')===${JSON.stringify(scheme)}`);
  await sleep(250);
}
const save = async (p, name) => writeFileSync(new URL(name + '.png', OUT), await shoot(p, ''));
const scroller = `document.querySelector('#settings:not([hidden]) .setsub:not([hidden]), #settings .setmain:not([hidden])')`;
async function openSettings(p) {
  await evaluate(p, `document.getElementById('settings').hidden && document.getElementById('setBtn').click()`); await sleep(250);
}
async function openHooks(p) {
  await openSettings(p);
  await click(p, '#set-hooks-nav');
  await waitFor(p, `document.getElementById('settings').dataset.sub==='hooks'`);
}
async function shots(p, prefix) {
  for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) {
    await view(p, w, h, scheme);
    await evaluate(p, `${scroller}.scrollTop=0`); await sleep(120);
    await save(p, `${prefix}${scheme}-${w}x${h}`);
    if (w === 390) {
      await evaluate(p, `(()=>{const s=${scroller};s.scrollTop=s.scrollHeight})()`); await sleep(150);
      await save(p, `${prefix}${scheme}-${w}x${h}-bottom`);
      await evaluate(p, `${scroller}.scrollTop=0`);
    }
  }
  await view(p, 390, 844, 'light');
}

try {
  const H = hooksHandler(fake.st);
  const p = await newPage(B, 390, 844, 'light', { touch: false, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);   // the headless-browser hint is not under test
  if (BEFORE) {
    await openSettings(p);
    await evaluate(p, `document.getElementById('set-s-tools').scrollIntoView({block:'start'})`);
    for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) {
      await view(p, w, h, scheme);
      await evaluate(p, `document.getElementById('set-s-tools').scrollIntoView({block:'start'})`); await sleep(150);
      await save(p, `zh-${scheme}-${w}x${h}-settings`);
    }
    console.log('before shots → ' + OUT.pathname);
  } else {
    // ---- via: hook — the main Agent's answer to a delivery is a page whose source card says 「Webhook」
    await fake.addTurn({ k: 'agent', via: 'hook', text: '' }, 'Stripe：收到 $20（测试数据）。正常收款，不用你处理。', 'done');
    await waitFor(p, `document.getElementById('omLabel').textContent==='Webhook'`);
    ok(await evaluate(p, `document.getElementById('om').dataset.src==='agent'`), 'via: hook page — source card 「Webhook」');
    for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) { await view(p, w, h, scheme); await save(p, `zh-${scheme}-${w}x${h}-via-hook`); }
    await view(p, 390, 844, 'light');
    // ---- the entry, in Settings → Agent 的功能
    await openSettings(p);
    ok(await evaluate(p, `!!document.querySelector('#set-s-tools #set-hooks-nav') && document.querySelector('#set-hooks-nav b').textContent==='Webhook'`), 'Settings → Agent 的功能 has the 「Webhook」 row');
    for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) {
      await view(p, w, h, scheme);
      await evaluate(p, `document.getElementById('set-hooks-nav').scrollIntoView({block:'center'})`); await sleep(150);
      await save(p, `zh-${scheme}-${w}x${h}-settings`);
    }
    await view(p, 390, 844, 'light');
    await click(p, '#set-hooks-nav');
    await waitFor(p, `document.querySelectorAll('#hooks-box .whrow').length>0`);
    ok(await evaluate(p, `document.getElementById('set-title').textContent==='Webhook' && !document.getElementById('set-back').hidden`), 'own sub-page: title 「Webhook」 + back');
    ok(H.calls.filter((c) => c.t === 'hook_list').length === 1, 'one hook_list on open');
    for (const scheme of ['light', 'dark']) {
      await view(p, 390, 844, scheme);
      ok(await evaluate(p, `getComputedStyle(document.querySelector('#settings .setbox')||document.getElementById('settings')).backgroundColor`) === 'rgb(44, 43, 38)', 'Noir canvas in ' + scheme);
    }
    await view(p, 390, 844, 'light');
    ok(await evaluate(p, `document.querySelector('#hooks-box .whused').textContent==='今天 3 / 1000 条' && document.querySelector('#hooks-box .whup').textContent==='在线'`), 'header: 今天 3 / 1000 条 · 在线');
    const names = await evaluate(p, `[...document.querySelectorAll('#hooks-box .whlist--live .whname')].map(n=>n.textContent)`);
    ok(names.join() === '线上告警,Stripe 收款,视频-123,表单', 'live rows: resident first');
    ok(await evaluate(p, `${$(row(HID.alert) + ' .whchip--pending')}.textContent==='等你批准'`), 'pending: 等你批准');
    ok(await evaluate(p, `${$(row(HID.form) + ' .whtoggle')}.textContent==='恢复' && ${$(row(HID.form) + ' .whchip--paused')}.textContent==='已暂停'`), 'paused: 已暂停 + 恢复');
    ok(await evaluate(p, `document.querySelectorAll('#hooks-box .whlist--ended .whrow').length===1 && !document.querySelector('#hooks-box .whlist--ended button')`), 'ended (24 h): one greyed row, no buttons');
    ok(await evaluate(p, `[...document.querySelectorAll('#hooks-box .whdel-name')].map(n=>n.textContent).join()==='已删除的 Webhook,Stripe 收款,昨天的回调'`), '最近投递: newest first, names looked up');
    const geo = await evaluate(p, `(()=>{const out={small:[],close:[],wide:document.documentElement.scrollWidth>innerWidth};
      for(const b of document.querySelectorAll('#hooks-box button')){const r=b.getBoundingClientRect();if(r.height<44)out.small.push(b.textContent+':'+r.height)}
      for(const a of document.querySelectorAll('#hooks-box .whacts')){const bs=[...a.children].map(x=>x.getBoundingClientRect());
        for(let i=1;i<bs.length;i++){const p=bs[i-1],c=bs[i];const gap=Math.abs(c.top-p.top)<2?c.left-p.right:c.top-p.bottom;if(gap<8)out.close.push(gap)}}
      return out})()`);
    ok(!geo.small.length && !geo.close.length && !geo.wide, 'buttons ≥ 44 px, ≥ 8 px apart, no horizontal overflow ' + JSON.stringify(geo));
    // DESIGN_TOKENS.md (P302): text never closer than 8 px to the line under its row, nor to the line above it
    const tight = await evaluate(p, `(()=>{const bad=[];for(const li of document.querySelectorAll('#hooks-box .whrow, #hooks-box .whdel')){
      const r=li.getBoundingClientRect(),k=[...li.children].map(c=>c.getBoundingClientRect()).filter(x=>x.height>0);
      const lo=Math.max(...k.map(x=>x.bottom)),hi=Math.min(...k.map(x=>x.top));if(r.bottom-lo<8||hi-r.top<8)bad.push([li.dataset.id,Math.round(r.bottom-lo),Math.round(hi-r.top)])}return bad})()`);
    ok(!tight.length, 'rows: text ≥ 8 px from the dividing lines ' + JSON.stringify(tight));
    ok(!(await evaluate(p, `document.getElementById('hooks-box').textContent.includes('https://')||document.getElementById('hooks-box').textContent.includes('<令牌')`)), 'no URL or placeholder printed on the page');
    await shots(p, 'zh-');

    // ---- 暂停
    await click(p, row(HID.stripe) + ' .whtoggle');
    await waitFor(p, `${$(row(HID.stripe) + ' .whtoggle')}.textContent==='恢复'`);
    ok(H.calls.some((c) => c.t === 'hook_set' && c.id === HID.stripe && c.op === 'pause'), '暂停 → hook_set pause, the row redraws');
    // ---- 撤销: cancel, then confirm
    await click(p, row(HID.once) + ' .whrevoke');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    ok(await evaluate(p, `document.getElementById('confirm-title').textContent==='撤销「视频-123」？'`), 'revoke: the confirm card names the hook');
    await view(p, 390, 844, 'dark'); await save(p, 'zh-dark-390x844-revoke'); await view(p, 390, 844, 'light');
    await key(p, 'Escape'); await sleep(250);
    ok(await evaluate(p, `document.getElementById('confirm').hidden && document.getElementById('settings').dataset.sub==='hooks'`), 'Esc closes the confirm card only');
    ok(!H.calls.some((c) => c.t === 'hook_set' && c.op === 'revoke'), 'cancel: nothing sent');
    await click(p, row(HID.once) + ' .whrevoke');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await click(p, '#confirm-yes');
    await waitFor(p, `${$(row(HID.once))}?.dataset.state==='revoked'`);
    ok(H.calls.some((c) => c.t === 'hook_set' && c.id === HID.once && c.op === 'revoke') && await evaluate(p, `!!document.querySelector('#hooks-box .whlist--ended ${row(HID.once).split(' ').pop()}')`), '撤销 → hook_set revoke, the row moves to 已结束');
    // ---- 复制地址
    await evaluate(p, `navigator.clipboard.writeText('p366-before')`);
    await click(p, row(HID.stripe) + ' .whcopy');
    await waitFor(p, `navigator.clipboard.readText().then(t=>t===${JSON.stringify('https://hook.agentj.app/hb/' + HID.stripe)})`, 5000);
    ok(await evaluate(p, `${$(row(HID.stripe) + ' .whcopy')}.dataset.done==='1'`), '复制地址: visible shared completed state');
    ok(true, '复制地址: the URL is in the clipboard');
    await evaluate(p, `navigator.clipboard.writeText('p366-before')`);
    await click(p, row(HID.form) + ' .whcopy');
    await waitFor(p, `document.getElementById('toast').textContent.includes('完整地址在 Agent J 主机上')`);
    ok(await evaluate(p, `navigator.clipboard.readText().then(t=>t==='p366-before')`), 'token hook: nothing copied, 「完整地址在电脑上」');
    await save(p, 'zh-light-390x844-token');
    // ---- hook_changed
    const before = H.calls.filter((c) => c.t === 'hook_list').length;
    await fake.send({ t: 'hook_changed' }); await fake.send({ t: 'hook_changed' });
    await sleep(700);
    ok(H.calls.filter((c) => c.t === 'hook_list').length === before + 1, 'hook_changed: one re-read while open');
    // ---- Esc: back to Settings, then a change does not re-read
    await evaluate(p, `document.activeElement?.blur()`);
    await key(p, 'Escape'); await sleep(300);
    ok(await evaluate(p, `document.getElementById('settings').dataset.sub==='main'`), 'Esc → back to Settings');
    await fake.send({ t: 'hook_changed' }); await sleep(600);
    ok(H.calls.filter((c) => c.t === 'hook_list').length === before + 1, 'hook_changed after leaving: no read');
    // ---- English
    await evaluate(p, `window.AJLang.set('en')`); lang = 'en'; await sleep(300);
    await click(p, '#set-hooks-nav');
    await waitFor(p, `document.querySelector('#hooks-box .whused')?.textContent==='Today 3 / 1000'`);
    ok(await evaluate(p, `document.getElementById('set-title').textContent==='Webhooks' && document.querySelector('#hooks-box .setg').textContent==='In use'`), 'en: title and sections');
    await view(p, 390, 844, 'dark'); await save(p, 'en-dark-390x844'); await view(p, 1440, 900, 'light'); await save(p, 'en-light-1440x900'); await view(p, 390, 844, 'light');
    await evaluate(p, `window.AJLang.set('zh')`); lang = 'zh'; await sleep(200);
    await key(p, 'Escape'); await sleep(250);
    // ---- the empty state
    hooksHandler(fake.st, { empty: true, calls: H.calls });
    await click(p, '#set-hooks-nav');
    await waitFor(p, `!!document.querySelector('#hooks-box .whempty')`);
    ok(await evaluate(p, `document.querySelector('#hooks-box .whempty').textContent.startsWith('还没有 Webhook。跟 Agent 说「做完了回调我」') && !!document.querySelector('#hooks-box .whroutes')`), 'empty: the friendly paragraph + the two routes');
    for (const scheme of ['light', 'dark']) for (const [w, h] of SIZES) { await view(p, w, h, scheme); await save(p, `zh-${scheme}-${w}x${h}-empty`); }
    await view(p, 390, 844, 'light');
    await key(p, 'Escape'); await sleep(250); await key(p, 'Escape'); await sleep(250);
    ok(await evaluate(p, `document.getElementById('settings').hidden`), 'Esc twice leaves Settings');
    ok(p.problems.length === 0, 'no page errors ' + JSON.stringify(p.problems));
    ok(p.offsite.length === 0, 'nothing off-site ' + JSON.stringify(p.offsite));
    console.log(`p366 chromium: ${n} checks passed; shots → ${OUT.pathname}`);
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }
