// YouTube keeps the ordinary paired approval sheet and its quota-line surface.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { approvalEnvelopeDigest } from '../../protocol/wire.js';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot } from './browser.mjs';

const out = process.env.AJ_YOUTUBE_UI;
const web = await startWebServer(), host = await startFakeHost(), browser = await launch();
const url = web.url.replace('127.0.0.1', 'localhost');
const tool = 'agentj google youtube upload';
const review = '上传测试文件';
const ordinary = 'Bash';
const details = '上传到你的 YouTube 频道：fixture.mp4\n标题：隔离样例\n可见性：私享';
const lotion = 'rgb(250, 249, 245)', noir = 'rgb(44, 43, 38)';

async function sendCard(id, operation, action = operation) {
  const envelope = { v: 2, channel: host.channel, session: String(host.st.epoch), request_id: id,
    revision: 1, nonce: 'fixture', source: { channel: 'phone', verified: true, text: review },
    title: operation === tool ? '确认 YouTube 上传' : '批准 Bash 命令',
    action, reason: details, details, effects: [],
    expires_at: Math.ceil(Date.now() / 1000) + 120 };
  await host.ask({ t: 'ask', id, tool: operation, summary: details, approval_v: 2, envelope,
    envelope_sha256: await approvalEnvelopeDigest(envelope), revision: 1, review_state: 'pending', ttl: 120 });
}

async function readCard(page) {
  return evaluate(page, `(() => {
    const sheet = document.getElementById('sheet'), bounds = sheet.getBoundingClientRect();
    const quote = getComputedStyle(document.getElementById('source541'));
    const row = document.getElementById('apprBtns'), rowStyle = getComputedStyle(row);
    return { fill: getComputedStyle(sheet).backgroundColor, water: getComputedStyle(document.getElementById('water')).backgroundColor,
      width: bounds.width, bottom: bounds.bottom, quota: document.getElementById('m5h').getBoundingClientRect().top,
      quote: { fill: quote.backgroundColor, ink: quote.color }, detail: document.getElementById('action541').textContent,
      row: { gap: rowStyle.gap, top: row.getBoundingClientRect().top },
      controls: ['apprAllow', 'doubt541c', 'apprDeny'].map(id => {
        const button = document.getElementById(id), r = button.getBoundingClientRect(), style = getComputedStyle(button);
        const icon = button.querySelector('svg');
        const iconStyle = getComputedStyle(icon), iconRect = icon.getBoundingClientRect();
        return { id, fill: style.backgroundColor, ink: style.color, border: style.border, radius: style.borderRadius,
          width: r.width, height: r.height, left: r.left, right: r.right, top: r.top, bottom: r.bottom,
          icon: { fill: iconStyle.fill, stroke: iconStyle.stroke, strokeWidth: iconStyle.strokeWidth,
            strokes: Array.from(icon.children, child => getComputedStyle(child).stroke),
            width: iconRect.width, height: iconRect.height },
          inset: Math.min(r.left - bounds.left, bounds.right - r.right),
          hit: button.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)) };
      }) };
  })()`);
}

function buttonStyle({ row, controls, bottom: cardBottom }) {
  return controls.map(({ top, bottom, left, right, hit, ...button }, index) => ({
    ...button, topInset: top - row.top, bottomInset: cardBottom - bottom,
    gap: index ? left - controls[index - 1].right : row.gap,
  }));
}

const comparisons = [], mismatches = [];

try {
  if (out) mkdirSync(out, { recursive: true });
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    host.reset();
    host.st.prefs = { appearance: { language: 'zh', theme } };
    const page = await newPage(browser, width, width === 390 ? 844 : 900, theme,
      { touch: width === 390, allow: [url, host.relay + '/'] });
    try {
      await page.send('Emulation.setEmulatedMedia', { features: [
        { name: 'prefers-color-scheme', value: theme },
        { name: 'prefers-reduced-motion', value: 'reduce' },
      ] });
      await navigate(page, host.newPairing(url + '?lang=zh'));
      await waitState(page, 'awaiting-approval');
      await host.approve();
      await waitState(page, 'ready');
      await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
      await evaluate(page, `import('./js/settings.js').then(m=>m.applyThemeLocal('${theme}'))`);
      await host.addTurn({ k: 'phone', dev: host.devs.at(-1).id, name: '你', text: review }, '');
      await host.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J' });
      await waitFor(page, "document.body.dataset.status==='working' && document.body.dataset.sheet==='0'");
      if (out) writeFileSync(join(out, `before-${width}-${theme}.png`), await shoot(page));

      const ordinaryId = '0'.repeat(32);
      await sendCard(ordinaryId, ordinary, 'printf fixture');
      await waitFor(page, `document.body.dataset.sheet==='1' && document.querySelector('#sheet[data-approval-v="2"][data-popup-system=grow]:not([data-youtube-upload])')`);
      const ordinaryView = await readCard(page);
      assert.ok(ordinaryView.controls.every(button => button.hit), JSON.stringify(ordinaryView));
      assert.deepEqual(ordinaryView.controls.map(({ fill, ink, icon }) => [fill, ink, icon.stroke]),
        [[lotion, noir, noir], [noir, lotion, lotion], [noir, lotion, lotion]], '0.30.79 ordinary approval colors');
      if (out) writeFileSync(join(out, `ordinary-${width}-${theme}.png`), await shoot(page));
      await host.askDone(ordinaryId, 'gone');
      await waitFor(page, "document.body.dataset.sheet==='0'");

      const id = '1'.repeat(32);
      await sendCard(id, tool);
      await waitFor(page, "document.body.dataset.sheet==='1' && document.querySelector('#sheet[data-youtube-upload][data-popup-system=grow]')");
      const view = await readCard(page);
      assert.equal(view.fill, view.water, JSON.stringify(view));
      assert.ok(Math.abs(view.width - width) <= 1 && Math.abs(view.bottom - view.quota) <= 2, JSON.stringify(view));
      assert.equal(view.quote.ink, lotion);
      assert.notEqual(view.quote.fill, view.fill);
      for (const label of ['fixture.mp4', '隔离样例', '私享']) assert.ok(view.detail.includes(label), view.detail);
      assert.ok(view.controls.every(button => button.height >= 44 && button.inset >= 8 && button.hit), JSON.stringify(view));
      for (let i = 1; i < view.controls.length; i++) assert.ok(view.controls[i].left - view.controls[i - 1].right >= 8);
      const actual = buttonStyle(view);
      const ordinaryStyle = buttonStyle(ordinaryView);
      comparisons.push({ width, theme, ordinary: ordinaryView, youtube: view });
      try {
        assert.deepEqual(actual, ordinaryStyle, 'YouTube and ordinary approval buttons in the same view');
      } catch (error) {
        mismatches.push({ width, theme, message: error.message });
      }
      if (out) writeFileSync(join(out, `after-${width}-${theme}.png`), await shoot(page));

      await host.askDone(id, 'gone');
      await waitFor(page, "document.body.dataset.sheet==='0'");
      await sendCard('2'.repeat(32), ordinary, tool);
      await waitFor(page, "document.body.dataset.sheet==='1'");
      assert.equal(await evaluate(page, "document.getElementById('sheet').hasAttribute('data-youtube-upload')"), false);
      assert.equal(host.st.answers.length, 0);
    } finally {
      await page.dispose();
    }
  }
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
  if (out) writeFileSync(join(out, 'button-readback.json'), JSON.stringify(comparisons, null, 2) + '\n');
}
assert.deepEqual(mismatches, [], 'YouTube and ordinary approval button readbacks match in all four views');
console.log('PASS all three YouTube controls match ordinary approval controls in the same viewport and theme');
console.log('PASS YouTube approval card layout at 390/1440 in both themes, with ordinary-card isolation');
