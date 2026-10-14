// P207 (追加 2 修订 + 追加 3): the input card's header — source icon + label on the message text's left edge, the page number's
// right edge on the 「…」 pill's, three equal tap zones (left = earliest, middle = first unread, right = newest), the round
// unread badge (count, the page number's pulse) only when there is something unread; logo hold and `u` = first unread
// (「没有未读」 when there is none); the unread mark is this device's and survives a reload. Plus the new keys of 追加 3
// (n rename · w load · ? the sheet drawn from interactions.json). Real page + local fake host; zh/en × 390 / 1440, dark.
// Shots → reports/qa/p207/unread/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../reports/qa/p207/unread/', import.meta.url));
const $ = (id) => `document.getElementById('${id}')`;
const pg = (p) => evaluate(p, `${$('pg')}.textContent`);
const badge = (p) => evaluate(p, `(${$('om')}.dataset.unread === '1') ? +${$('omUnread')}.dataset.n : 0`);
const box = (p, id) => evaluate(p, `(()=>{const r=${$(id)}.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,r:r.right}})()`);
async function touchAt(p, x, y, hold = 0) {
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  if (hold) await sleep(hold);
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
async function touch(p, id, count = 1) {
  const b = await box(p, id);
  for (let i = 0; i < count; i++) { await touchAt(p, b.x + b.w / 2, b.y + b.h / 2); if (i + 1 < count) await sleep(70); }
}
async function hide(p, on) {
  await evaluate(p, on ? `Object.defineProperty(document,'visibilityState',{get:()=>'hidden',configurable:true});true`
    : `delete document.visibilityState;document.dispatchEvent(new Event('visibilitychange'));true`);
}
const L = {
  zh: { ask: (i) => `第 ${i} 件事怎么样了？`, ans: (i) => `第 ${i} 件事做完了。`, none: '没有未读', badge: /条未读/, keysTitle: '键盘快捷键' },
  en: { ask: (i) => `How is item ${i}?`, ans: (i) => `Item ${i} is done.`, none: 'No unread', badge: /unread/, keysTitle: 'Keyboard shortcuts' },
};

export async function runP207Unread({ B, web, fake, out = OUT }) {
  mkdirSync(out, { recursive: true });
  const results = {};
  for (const lang of ['zh', 'en']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const s = L[lang], phone = w < 600;
    fake.reset();
    const p = await newPage(B, w, h, 'dark', { touch: phone, allow: [web.url, fake.relay + '/'] });
    const tag = `${lang}-${w}`;
    const save = async (name) => writeFileSync(resolve(out, `${tag}-${name}.png`), await shoot(p, ''));
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' });
      const dev = await evaluate(p, `import('./js/api.js').then(api=>api.myDeviceId())`);
      for (let i = 1; i <= 3; i++) await fake.addTurn({ k: 'phone', dev, text: s.ask(i) }, s.ans(i));
      await waitFor(p, `${$('pg')}.textContent==='3 / 3'`);
      await sleep(200);
      // first run: what is already here counts as read — no badge, the middle third does nothing
      assert.equal(await badge(p), 0, 'first run: nothing unread');
      assert.equal(await evaluate(p, `getComputedStyle(document.querySelector('#omUnread .unb')).display`), 'none', 'no badge without unread');
      await touch(p, 'omUnread'); await sleep(150);
      assert.equal(await pg(p), '3 / 3', 'middle third without unread: no reaction');

      // geometry: three equal zones; icon on the text's left edge; page number's right edge on the 「…」 pill's right edge
      const g = await evaluate(p, `(()=>{const r=id=>document.getElementById(id).getBoundingClientRect();const hd=document.querySelector('.omhd').getBoundingClientRect();
        const pill=document.querySelector('#omMore i').getBoundingClientRect(),cap=document.querySelector('#pg').getBoundingClientRect(),ic=document.querySelector('#omIcon svg')||document.getElementById('omIcon');
        return {hd:hd.width,first:r('omFirst').width,mid:r('omUnread').width,right:r('pgCorner').width,midX:r('omUnread').left+r('omUnread').width/2,hdMid:hd.left+hd.width/2,
          icon:ic.getBoundingClientRect().left,text:r('omText').left,cap:cap.right,pill:pill.right,firstH:r('omFirst').height,overflow:document.documentElement.scrollWidth>innerWidth}})()`);
      for (const k of ['first', 'mid', 'right']) assert.ok(Math.abs(g[k] - g.hd / 3) <= 1.5, `${k} zone = 1/3 of the header (${g[k]} of ${g.hd})`);
      assert.ok(Math.abs(g.midX - g.hdMid) <= 1.5, 'the badge zone is centred');
      assert.ok(Math.abs(g.icon - g.text) <= 1, `source icon on the text's left edge (${g.icon} vs ${g.text})`);
      assert.ok(Math.abs(g.cap - g.pill) <= 1, `page number's right edge on the 「…」 pill's (${g.cap} vs ${g.pill})`);
      assert.ok(g.firstH >= 44, '44 px targets');
      assert.equal(g.overflow, false);
      await save('header-none');

      // away: five replies arrive while the page is in the background (the newest page is shown but not seen)
      await hide(p, true);
      for (let i = 4; i <= 8; i++) await fake.addTurn({ k: 'phone', dev, text: s.ask(i) }, s.ans(i));
      await waitFor(p, `${$('pg')}.textContent==='8 / 8'`);
      assert.equal(await badge(p), 5, 'hidden: none of the five is seen');
      await hide(p, false);
      await waitFor(p, `+${$('omUnread')}.dataset.n===4`);   // back in front: the newest page is on screen = seen
      assert.match(await evaluate(p, `${$('omUnread')}.getAttribute('aria-label')`), s.badge);
      const pulse = await evaluate(p, `getComputedStyle(document.querySelector('#omUnread .unb')).animationName`);
      assert.equal(pulse, 'new-message-pulse', 'the badge flashes like the page number');
      const bb = await evaluate(p, `(()=>{const r=document.querySelector('#omUnread .unb').getBoundingClientRect();return {w:r.width,h:r.height}})()`);
      assert.ok(Math.abs(bb.w - bb.h) <= 0.5, `a round badge for one digit (${bb.w}×${bb.h})`);
      await save('badge');

      // the mark is this device's and survives a reload
      await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
      await waitFor(p, `${$('pg')}.textContent==='8 / 8' && +${$('omUnread')}.dataset.n===4`);

      // middle third → the first unread (page 4), then page forward; each page read lowers the count
      if (phone) await touch(p, 'omUnread'); else await evaluate(p, `${$('omUnread')}.click()`);
      await waitFor(p, `${$('pg')}.textContent==='4 / 8'`);
      assert.equal(await badge(p), 3);
      await save('first-unread');
      for (const n of [5, 6, 7]) { await evaluate(p, `${$('pgNext')}.click()`); await waitFor(p, `${$('pg')}.textContent==='${n} / 8'`); }
      assert.equal(await badge(p), 0, 'read to the end: the badge is gone');

      // `u` and the logo hold with nothing unread: 「没有未读」
      await evaluate(p, `document.activeElement?.blur()`);
      await key(p, 'u'); await waitFor(p, `document.getElementById('toast').textContent===${JSON.stringify(s.none)}`);
      // new arrivals while reading an older page → badge again; u jumps there; logo hold too
      await evaluate(p, `${$('pgPrev')}.click()`); await waitFor(p, `${$('pg')}.textContent==='6 / 8'`);
      await fake.addTurn({ k: 'phone', dev, text: s.ask(9) }, s.ans(9));
      await fake.addTurn({ k: 'phone', dev, text: s.ask(10) }, s.ans(10));
      await waitFor(p, `+${$('omUnread')}.dataset.n===2 && ${$('pg')}.textContent==='6 / 10'`);
      await key(p, 'u'); await waitFor(p, `${$('pg')}.textContent==='9 / 10'`);
      await evaluate(p, `${$('pgPrev')}.click()`); await waitFor(p, `${$('pg')}.textContent==='8 / 10'`);
      const orb = await box(p, 'orb');
      if (phone) await touchAt(p, orb.x + orb.w / 2, orb.y + orb.h / 2, 650);
      else {
        await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: orb.x + orb.w / 2, y: orb.y + orb.h / 2 });
        await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: orb.x + orb.w / 2, y: orb.y + orb.h / 2, button: 'left', clickCount: 1 });
        await sleep(650);
        await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: orb.x + orb.w / 2, y: orb.y + orb.h / 2, button: 'left', clickCount: 1 });
      }
      await waitFor(p, `${$('pg')}.textContent==='10 / 10'`);   // 9 was seen above: the first unread is 10
      // the left / right thirds keep their double taps
      if (phone) {
        await touch(p, 'omFirst', 2); await waitFor(p, `${$('pg')}.textContent==='1 / 10'`);
        await touch(p, 'pgLatest', 2); await waitFor(p, `${$('pg')}.textContent==='10 / 10'`);
      }

      // 追加 3: the new keys — n renames the Agent, w opens the load card, ? opens the sheet drawn from interactions.json
      await evaluate(p, `document.activeElement?.blur()`);
      await key(p, 'n'); await waitFor(p, `!${$('nameDlg')}.hidden`);
      await key(p, 'Escape'); await waitFor(p, `${$('nameDlg')}.hidden`);
      await evaluate(p, `document.activeElement?.blur()`);
      await key(p, 'w'); await waitFor(p, `!${$('loadDlg')}.hidden`);
      await key(p, 'Escape'); await waitFor(p, `${$('loadDlg')}.hidden`);
      await evaluate(p, `document.activeElement?.blur()`);
      await key(p, '?', { code: 'Slash', shift: true, text: '?' }); await waitFor(p, `!${$('keys')}.hidden`);
      const sheet = await evaluate(p, `import('./js/interactions.gen.js').then(g=>({rows:g.ROWS.length,dt:document.querySelectorAll('#keys dt').length,dd:document.querySelectorAll('#keys dd').length,
        u:[...document.querySelectorAll('#keys dt[data-ix="page.first_unread"] kbd')].map(k=>k.textContent).join(''),title:document.getElementById('keysTitle').textContent,
        groups:document.querySelectorAll('#keys section.menucard').length,ges:document.querySelectorAll('#keys .keyges').length}))`);
      assert.equal(sheet.dt, sheet.rows); assert.equal(sheet.dd, sheet.rows);
      assert.equal(sheet.u, 'u'); assert.equal(sheet.title, s.keysTitle);
      assert.ok(sheet.groups >= 8 && sheet.ges > 40, JSON.stringify(sheet));
      await save('keys');
      await key(p, 'Escape'); await waitFor(p, `${$('keys')}.hidden`);
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      results[tag] = { result: 'pass', geometry: g, sheet };
      console.log('PASS P207 unread ' + tag);
    } finally { await p.dispose(); }
  }
  writeFileSync(resolve(out, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  return results;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  const rec = parityRecorder('web/test/p207_unread.mjs');
  let ok = false;
  try { await runP207Unread({ B, web, fake }); ok = true; }
  finally { rec.record('runP207Unread', ok); console.log('parity results → ' + rec.write()); await B.close(); await fake.stop(); await web.stop(); }
}
