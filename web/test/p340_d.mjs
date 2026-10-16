// P340 D (FEEDBACK #42, Leo 10-16 定稿): the WEEKLY (top) and 5H (bottom) rules are page dividers — always on screen, whatever
// the harness or provider. No data hides only the label and percentage (no 「—」, no explanation); a provider's own windows
// (Kimi Coding / Zhipu GLM Coding Plan via the host) fill them, 5 h at the bottom. Switching harness never moves the page.
// Real client against fakehost.mjs, own Chromium (never :9222). zh / en × light / dark × 390 → reports/qa/p340/d/*.png + checks.json.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const parity = parityRecorder('web/test/p340_d.mjs');
const root = fileURLToPath(new URL('../../../', import.meta.url)), out = root + 'reports/qa/p340/d/';
mkdirSync(out, {recursive: true});
const now = () => Math.floor(Date.now() / 1000);
const GEOM = `(()=>{const r=id=>{const b=document.getElementById(id).getBoundingClientRect();return [b.top,b.left,b.width,b.height].map(v=>Math.round(v*10)/10)};
  return {week:r('mWeek'),h5:r('m5h'),deck:r('deck'),rm:r('rm'),tools:r('tools'),input:r('input')};})()`;
const BARS = `(()=>['mWeek','m5h'].map(id=>{const e=document.getElementById(id),cs=getComputedStyle(e),b=e.getBoundingClientRect();
  return {id,hidden:e.hidden,display:cs.display,visibility:cs.visibility,h:b.height,w:b.width,known:e.dataset.known,lab:e.querySelector('.lab').textContent,text:e.textContent};}))()`;
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const rows = [];
try {
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
    const tag = `${lang}-${theme}-390`;
    fake.reset();
    fake.st.prefs = {appearance: {language: lang, theme}, voice: {speak_replies: false, wake_enabled: false}};
    const p = await newPage(B, 390, 844, theme, {touch: true, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : ''))); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await fake.send({t: 'status', s: 'idle', agent: 'claude', name: 'Agent J'});
    const dev = await evaluate(p, `import('./js/api.js').then(api=>api.myDeviceId())`);
    await fake.addTurn({k: 'phone', dev, text: lang === 'zh' ? '切到 OpenCode 以后上面的额度线还在吗？' : 'Are the quota rules still there after switching to OpenCode?'},
      lang === 'zh' ? '在的：两条线一直都在，没有数据时只是不显示文字。' : 'Yes: both rules stay; without data only the text is hidden.');
    await waitFor(p, `document.getElementById('pg').textContent==='1 / 1'`);
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);

    // 0) before any meter: both rules already on screen, no label
    await sleep(300);
    const b0 = await evaluate(p, BARS);
    for (const b of b0) { assert.equal(b.hidden, false, `${tag} ${b.id} initial`); assert.equal(b.lab, '', `${tag} ${b.id} initial label ${b.lab}`); }

    // 1) Claude Code with its own windows
    await fake.send({t: 'meter', model: 'claude-opus-5-5', week: {pct: 42, reset: now() + 3 * 86400}, h5: {pct: 17, reset: now() + 7200}, ctx: {pct: 33}});
    await waitFor(p, `document.querySelector('#mWeek .lab').textContent.includes('42%') && document.querySelector('#m5h .lab').textContent.includes('17%')`);
    await sleep(300);
    const g1 = await evaluate(p, GEOM);
    writeFileSync(out + `${tag}-claude.png`, await shoot(p, ''));

    // 2) switch to OpenCode + a provider with no usage interface (Ark Coding Plan): both rules stay, no text, nothing moves
    await fake.send({t: 'status', s: 'idle', agent: 'opencode', name: 'Agent J'});
    await fake.send({t: 'meter', model: 'ark-coding-plan/doubao-seed-2.1-pro', model_name: 'Doubao Seed 2.1 Pro', quota_windows: [], ctx: {used: 32000, max: 1048576}});
    await waitFor(p, `document.getElementById('mWeek').dataset.known==='0' && document.getElementById('m5h').dataset.known==='0'`);
    await sleep(300);
    const b2 = await evaluate(p, BARS), g2 = await evaluate(p, GEOM);
    for (const b of b2) {
      assert.equal(b.hidden, false, `${tag} ${b.id} not hidden`); assert.notEqual(b.display, 'none', `${tag} ${b.id} displayed`);
      assert.equal(b.visibility, 'visible'); assert.ok(b.h >= 2 && b.w > 300, `${tag} ${b.id} drawn ${b.w}×${b.h}`);
      assert.equal(b.lab, '', `${tag} ${b.id} label hidden`); assert.ok(!b.text.includes('—'), `${tag} ${b.id} no dash`);
    }
    assert.deepEqual(g2, g1, `${tag} geometry unchanged on harness switch`);
    writeFileSync(out + `${tag}-opencode-nodata.png`, await shoot(p, ''));

    // 3) a provider whose windows the host reads (Kimi Coding: weekly + 5 h, host order 5 h first) — 5 h lands on the bottom rule
    await fake.send({t: 'meter', model: 'kimi-code-plan-cn/k3', model_name: 'Kimi K3',
      quota_windows: [{window: '5h', used: 12, limit: 100, pct: 12, reset: now() + 3 * 3600}, {window: 'weekly', used: 30, limit: 100, pct: 30, reset: now() + 4 * 86400}],
      ctx: {used: 32000, max: 262144}});
    await waitFor(p, `document.querySelector('#mWeek .lab').textContent.startsWith('weekly 30%') && document.querySelector('#m5h .lab').textContent==='5h 12%'`);
    await sleep(300);
    const g3 = await evaluate(p, GEOM);
    assert.deepEqual(g3, g1, `${tag} geometry unchanged with provider windows`);
    const wl = await evaluate(p, `document.querySelector('#mWeek .lab').textContent`);
    assert.ok(/（第 \d 天）|\(day \d\)|day/i.test(wl) || wl.length > 'weekly 30%'.length, `${tag} weekly day from the provider reset: ${wl}`);
    writeFileSync(out + `${tag}-opencode-kimi.png`, await shoot(p, ''));

    // 4) back to no data (model switch clears the windows): labels go, rules stay
    await fake.send({t: 'meter', model: 'bailian-coding-plan/qwen3.7-plus', quota_windows: [], ctx: null});
    await waitFor(p, `document.querySelector('#mWeek .lab').textContent==='' && document.querySelector('#m5h .lab').textContent===''`);
    await sleep(200);
    assert.deepEqual(await evaluate(p, GEOM), g1, `${tag} geometry unchanged after clearing`);
    rows.push({tag, ok: true, claude: g1, nodata: b2, kimi_weekly_label: wl});
    parity.record(tag, true);
    await p.dispose();
  }
  writeFileSync(out + 'checks.json', JSON.stringify({ok: true, rows}, null, 1) + '\n');
  console.log(`p340_d: ${rows.length} combos ok → ${out}`);
  if (process.env.AJ_PARITY_OUT) parity.write();
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
