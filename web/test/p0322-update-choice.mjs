// Actual Settings -> Noise fake host -> preference broadcasts, in isolated Chromium only.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, key } from './browser.mjs';

const phase = process.env.AJ_UPDATE_UI_PHASE || 'after';
const out = join(process.env.OLARES_RUN_OUT || 'reports/qa/p0322-update-choice', phase);
mkdirSync(out, { recursive: true });
const web = await startWebServer(), fake = await startFakeHost(), browser = await launch();
const results = [];
const words = {
  zh: { auto: '当前：Agent J 主机自动安装已开启；升级消息自动处理。', ask: '当前：Agent J 主机自动安装已关闭；升级消息先问我。', mixed: '当前：Agent J 主机自动安装已开启；升级消息先问我。', other: '当前：Agent J 主机自动安装已关闭；升级消息自动处理。', unknown: '尚未收到完整的Agent J 主机升级设置。' },
  en: { auto: 'Current: host auto-install on; upgrade notices handled automatically.', ask: 'Current: host auto-install off; upgrade notices ask first.', mixed: 'Current: host auto-install on; upgrade notices ask first.', other: 'Current: host auto-install off; upgrade notices handled automatically.', unknown: 'Complete host upgrade settings have not arrived yet.' },
};
const text = p => evaluate(p, `document.getElementById('set-updates-state')?.textContent`);
async function tap(p, selector) {
  const point = await evaluate(p, `(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
}
async function screenshot(p, name) {
  await waitFor(p, `!document.getElementById('toast').classList.contains('on') && getComputedStyle(document.getElementById('toast')).opacity === '0'`, 8000);
  await evaluate(p, `new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
  const row = await evaluate(p, `(()=>{const e=document.getElementById('set-updates').closest('.setrow');e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return {x:Math.max(0,r.x),y:Math.max(0,r.y),width:r.width,height:r.height,scale:1};})()`);
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip: row });
  writeFileSync(join(out, name + '.png'), Buffer.from(data, 'base64'));
}
async function broadcast(p, updates, language, theme) {
  fake.st.prefs = { appearance: { language, theme }, updates };
  await fake.send(fake.prefsMsg());
}
try {
  for (const language of ['zh', 'en']) for (const theme of ['dark', 'light']) for (const width of [390, 1440]) {
    fake.reset(); delete fake.st.prefRefuse;
    fake.st.prefs = { appearance: { language, theme }, updates: { auto_install: true, mode: 'auto' } };
    const p = await newPage(browser, width, 900, theme, { touch: width === 390, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + language));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await tap(p, '#setBtn');
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await evaluate(p, `document.getElementById('set-updates').scrollIntoView({block:'center',behavior:'instant'})`);
      const name = `${language}-${theme}-${width}`;
      await screenshot(p, name + '-auto');
      if (phase === 'before') continue;
      assert.equal(await text(p), words[language].auto, 'visible host-reported current setting');
      const styleProof = await evaluate(p, `(()=>{const e=document.getElementById('set-updates').closest('.setrow');return {theme:document.documentElement.getAttribute('data-theme'),scheme:matchMedia('(prefers-color-scheme: light)').matches?'light':'dark',surface:getComputedStyle(e).backgroundColor,settingsSurface:getComputedStyle(document.getElementById('settings')).backgroundColor,ink:getComputedStyle(e).color};})()`);
      assert.equal(styleProof.theme, theme, 'capture the actual confirmed theme, not just its requested filename');
      const geometry = await evaluate(p, `(()=>{const row=document.getElementById('set-updates').closest('.setrow'),s=document.getElementById('set-updates-state'),r=s.getBoundingClientRect();return {visible:r.width>0&&r.height>0,width:r.width,rowWidth:row.getBoundingClientRect().width,role:s.getAttribute('role'),html:document.documentElement.scrollWidth,viewport:innerWidth,taps:[...row.querySelectorAll('button')].map(b=>b.getBoundingClientRect().height)};})()`);
      assert.ok(geometry.visible && geometry.width <= geometry.rowWidth + 1, JSON.stringify(geometry));
      assert.equal(geometry.role, 'status'); assert.ok(geometry.html <= geometry.viewport + 1, 'no page overflow');
      assert.ok(geometry.taps.every(h => h >= 44), 'existing touch targets retained');
      await tap(p, '[data-tw="set-updates"][data-side="right"]');
      await waitFor(p, `document.getElementById('set-updates-state').textContent === ${JSON.stringify(words[language].ask)}`);
      assert.deepEqual(fake.st.prefSets.map(m => [m.key, m.value]), [['updates.auto_install', false], ['updates.mode', 'ask']]);
      await screenshot(p, name + '-ask');
      await tap(p, '[data-tw="set-updates"][data-side="left"]');
      await waitFor(p, `document.getElementById('set-updates-state').textContent === ${JSON.stringify(words[language].auto)}`);
      assert.deepEqual(fake.st.prefSets.slice(-2).map(m => [m.key, m.value]), [['updates.auto_install', true], ['updates.mode', 'auto']]);
      // The display reports the two real fields instead of pretending every host is in a coherent compound mode.
      await broadcast(p, { auto_install: true, mode: 'ask' }, language, theme);
      await waitFor(p, `document.getElementById('set-updates-state').textContent === ${JSON.stringify(words[language].mixed)}`);
      await broadcast(p, { auto_install: false, mode: 'auto' }, language, theme);
      await waitFor(p, `document.getElementById('set-updates-state').textContent === ${JSON.stringify(words[language].other)}`);
      await broadcast(p, {}, language, theme);
      await waitFor(p, `document.getElementById('set-updates-state').textContent === ${JSON.stringify(words[language].unknown)}`);
      await broadcast(p, { auto_install: false, mode: 'ask' }, language, theme);
      await waitFor(p, `document.getElementById('set-updates-state').textContent === ${JSON.stringify(words[language].ask)}`);
      const attempts = fake.st.prefSets.length;
      fake.st.prefRefuse = 'test_refused';
      await tap(p, '[data-tw="set-updates"][data-side="left"]');
      await waitFor(p, `document.getElementById('set-updates').getAttribute('aria-checked') === 'true'`);
      assert.equal(fake.st.prefSets.length, attempts + 1, 'the fake host really received and refused the first setting');
      assert.equal(fake.st.prefs.updates.auto_install, false);
      assert.equal(await text(p), words[language].ask, 'a rejected save does not claim automatic installation is enabled');
      await key(p, 'Escape', { code: 'Escape' });
      assert.equal(await evaluate(p, `document.getElementById('settings').hidden`), true, 'Esc still closes settings');
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
      results.push({ language, theme, width, styleProof, result: 'PASS' });
    } catch (error) {
      writeFileSync(join(out, 'failure.json'), JSON.stringify({error:String(error),prefSets:fake.st.prefSets,reported:await text(p),prefs:fake.st.prefs,hit:await evaluate(p, `(()=>{const e=document.querySelector('[data-tw=\"set-updates\"][data-side=\"right\"]'),r=e.getBoundingClientRect();return {r:r.toJSON(),hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML};})()`)},null,2));
      throw error;
    } finally { await p.dispose(); }
  }
  writeFileSync(join(out, 'results.json'), JSON.stringify({ phase, results }, null, 2));
  console.log(JSON.stringify({ phase, cases: results.length, result: 'PASS' }));
} finally { await browser.close(); await fake.stop(); await web.stop(); }
