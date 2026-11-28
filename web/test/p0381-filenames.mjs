// Reply-card filenames through a paired client and byte-verified downloads.
// WebKit uses an iPhone viewport and a share sink for the Home Screen branch.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const engine = process.env.AJ_FILENAME_ENGINE || 'chromium';
const publicDir = process.env.AJ_FILENAME_PUBLIC;
const evidence = process.env.AJ_FILENAME_SCREENSHOTS;
const legacy = process.env.AJ_FILENAME_LEGACY === '1';
const wireCompat = process.env.AJ_FILENAME_COMPAT === '1';
const width = Number(process.env.AJ_FILENAME_WIDTH || 390);
const mobile = width === 390;
const stem = '项目复盘会议 年度演示（最终版）_' + 'Quarterly-review-and-next-quarter-planning_'.repeat(3);
const suffix = '.pptx';
const original = stem + suffix;
const compatible = stem.slice(0, 128 - suffix.length) + suffix;
const name = wireCompat ? compatible : original;
const expected = legacy ? name.slice(0, 128) : compatible;
const bytes = Buffer.from('Synthetic presentation attachment for testing save bytes.\n');
const sha = data => createHash('sha256').update(data).digest('hex');
const mime = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
const item = { mid: 'F'.repeat(22), name, mime, kind: 'file', bytes: bytes.length, sha256: sha(bytes), ref: 'out/' + original };
const ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1';
const web = await startWebServer({publicDir});
const host = await startFakeHost();
let browser;

async function testSave(theme, standalone) {
  host.reset();
  host.st.prefs = { appearance: { language: 'zh', theme }, voice: { speak_replies: false } };
  host.st.onApp = async (_session, message, send) => {
    if (message.t !== 'media_get') return false;
    assert.equal(message.mid, item.mid);
    assert.equal(message.o, 0);
    await send({ t: 'media_chunk', mid: item.mid, o: 0, d: bytes.toString('base64url'), last: true });
    return true;
  };
  const ctx = await browser.newContext({
    viewport: { width, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile,
    userAgent: mobile ? ua : undefined, acceptDownloads: true,
  });
  try {
    ctx.setDefaultTimeout(15000);
    if (standalone) await ctx.addInitScript(() => {
      Object.defineProperty(navigator, 'standalone', { get: () => true });
      navigator.canShare = () => true;
      navigator.share = ({ files: [file] }) => {
        window.sharedFile = { name: file.name, type: file.type, size: file.size };
        const reader = new FileReader();
        return new Promise((resolve, reject) => {
          reader.onload = () => {
            window.savedFile = { name: file.name, type: file.type, bytes: Array.from(new Uint8Array(reader.result)) };
            resolve();
          };
          reader.onerror = reject;
          reader.readAsArrayBuffer(file);
        });
      };
    });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(host.newPairing(web.url + '?lang=zh'));
    await page.waitForFunction(() => window.__ajState === 'awaiting-approval');
    await host.approve();
    await page.waitForFunction(() => window.__ajState === 'ready');
    await host.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await page.evaluate(() => {
      for (const id of ['phone-env-dismiss', 'a2hs-ok', 'confirm-no']) {
        const button = document.getElementById(id);
        if (button?.offsetParent) button.click();
      }
    });
    const turn = await host.addTurn({ k: 'phone', text: '请把年度演示文稿发给我。' }, '这是整理好的演示文稿。');
    await host.updateTurn(turn.id, { media: [item], media_skip: [] });
    const card = page.locator('.mcard');
    await page.locator('.mcard[data-state="ready"]').waitFor();
    assert.equal(await card.getAttribute('aria-label'), expected);
    assert.equal(await card.locator('.mname').textContent(), expected);
    if (!legacy) {
      const position = await card.locator('.mname').evaluate(label => {
        const stem = label.querySelector('.mname-stem'), suffix = label.querySelector('.mname-suffix');
        return { ellipsized: stem.scrollWidth > stem.clientWidth, right: suffix.getBoundingClientRect().right,
          bound: label.getBoundingClientRect().right, suffix: suffix.textContent, color: getComputedStyle(label).color,
          sizeColor: getComputedStyle(label.nextElementSibling).color,
          buttonFill: getComputedStyle(label.closest('.mcard').querySelector('.dl')).backgroundColor };
      });
      if (mobile) assert.equal(position.ellipsized, true);
      assert.ok(position.right <= position.bound + .5);
      assert.equal(position.suffix, suffix);
      assert.equal(position.color, 'rgb(250, 249, 245)');
      console.log('file label', theme, JSON.stringify(position));
    }
    if (evidence && !standalone) await page.screenshot({ path: join(evidence, theme + '.png') });
    const button = card.locator('.dl');
    if (standalone) {
      let downloadEvents = 0;
      page.on('download', () => downloadEvents++);
      await button.tap();
      await page.waitForFunction(() => Boolean(window.savedFile), null, { timeout: 12000 }).catch(async error => {
        const browserState = await page.evaluate(() => ({
          standalone: navigator.standalone, shareCalled: Boolean(window.sharedFile),
          ios: /iPhone|iPad|iPod/.test(navigator.userAgent),
          toast: document.getElementById('toast').textContent,
        }));
        throw new Error('Home Screen did not share: ' + JSON.stringify({browserState, downloadEvents, pageErrors}), { cause: error });
      });
      const saved = await page.evaluate(() => window.savedFile);
      assert.equal(saved.name, expected);
      assert.equal(saved.type, mime);
      assert.equal(sha(Buffer.from(saved.bytes)), item.sha256);
    } else {
      const [saved] = await Promise.all([page.waitForEvent('download'), mobile ? button.tap() : button.click()]);
      assert.equal(saved.suggestedFilename(), expected);
      assert.equal(sha(readFileSync(await saved.path())), item.sha256);
    }
    console.log('PASS', engine, theme, standalone ? 'iPhone Home Screen share/save branch' : 'save to disk',
      expected.length, expected.endsWith(suffix) ? suffix : 'extension missing as expected');
  } finally { await ctx.close(); }
}

try {
  if (evidence) mkdirSync(evidence, { recursive: true });
  browser = await pw[engine].launch(engine === 'chromium' ? { executablePath: '/usr/bin/chromium' } : {});
  for (const theme of ['light', 'dark']) await testSave(theme, false);
  if (engine === 'webkit' && mobile) await testSave('dark', true);
} finally {
  await browser?.close();
  await host.stop();
  await web.stop();
}
