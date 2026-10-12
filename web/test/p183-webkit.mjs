// P183 · the document previews in Safari's engine (Playwright WebKit), on the shipped page under the Worker's real CSP
// (serve.mjs): pdf.js (module script + module worker from 'self') draws the fixture PDF's text, SheetJS reads the xlsx,
// mammoth turns the docx into the empty-sandbox frame. Not physical iPhone acceptance (that stays with Leo's device).
//   node web/test/p183-webkit.mjs
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { startWebServer } from './serve.mjs';
const require = createRequire(import.meta.url);
const { webkit } = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const b64 = (n) => readFileSync(new URL(`./fixtures/p183/${n}`, import.meta.url)).toString('base64');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const browser = await webkit.launch();
let failed = 0;
const problems = [];
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.on('pageerror', (e) => problems.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/WebSocket|relay|ws:\/\//i.test(m.text())) problems.push(m.text()); });
  await page.goto(web.url);
  const run = async (name, mime, file, check) => {
    await page.evaluate(async ({ name, mime, data }) => {
      const { renderDoc } = await import('./js/preview.js');
      const { previewKind } = await import('./js/mediawire.js');
      const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const m = { name, mime, kind: mime === 'application/pdf' ? 'pdf' : 'file', bytes: bytes.length };
      document.querySelector('.mdoc.t183')?.remove();
      const box = document.createElement('div'); box.className = 'mdoc t183'; box.style.cssText = 'position:fixed;inset:0;z-index:99;overflow:auto';
      document.body.appendChild(box);
      await renderDoc(previewKind(m), m, new Blob([bytes], { type: mime }), box);
    }, { name, mime, data: b64(file) });
    const h = await page.waitForFunction(check, null, { timeout: 15000 });
    return h.jsonValue();
  };
  const C = async (n, fn) => { try { await fn(); console.log('PASS ' + n); } catch (e) { failed++; console.log('FAIL ' + n + ': ' + (e.stack || e)); } };
  await C('webkit pdf.js draws the text', async () => {
    const ink = await run('report.pdf', 'application/pdf', 'report.pdf', () => { const c = document.querySelector('.t183 .mpage[data-drawn=ok] canvas'); if (!c) return 0;
      const x = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < x.length; i += 4) if (x[i] < 100) n++; return n; });
    assert.ok(ink > 200, 'ink ' + ink);
  });
  await C('webkit SheetJS xlsx → table', async () => {
    const cells = await run('sales.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'sales.xlsx',
      () => { const td = document.querySelectorAll('.t183 tbody td'); return td.length ? [...td].map((c) => c.textContent).join('|') : ''; });
    assert.equal(cells, '卡片|120|图标|80');
  });
  await C('webkit mammoth docx → sandboxed frame', async () => {
    const r = await run('plan.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'plan.docx',
      () => { const f = document.querySelector('.t183 iframe'); return f ? f.getAttribute('sandbox') + '|' + f.srcdoc.includes('<strong>季度计划</strong>') : ''; });
    assert.equal(r, '|true');
  });
  await C('no page errors / CSP violations', async () => { const left = problems.filter((p) => !/Refused to connect|WebSocket|interactive-widget/.test(p)); if (left.length) console.log(left); assert.deepEqual(left, []); });
} finally { await browser.close(); await web.stop(); }
console.log(failed ? `p183-webkit: ${failed} failed` : 'p183-webkit: all passed');
process.exit(failed ? 1 : 0);
