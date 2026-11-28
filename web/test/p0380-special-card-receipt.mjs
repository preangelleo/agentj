// Exercise specialized-card rendering and delivery-only receipts on an isolated paired Web App.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { b64u } from '../../protocol/wire.js';
import { launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep } from './browser.mjs';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';

const before = process.env.AJ_J1_BEFORE === '1';
const out = process.env.AJ_J1_UI;
const web = await startWebServer({publicDir: process.env.AJ_J1_PUBLIC});
const host = await startFakeHost();
const browser = await launch();
const url = web.url.replace('127.0.0.1', 'localhost');
const receipts = card => host.log.filter(m => m.t === 'card_receipt' && m.kind === 'special' && m.id === card.id);
const delivery = { n: 'b'.repeat(32), ttl: 30 };
const sudo = { t: 'elev', id: '1'.repeat(32), kind: 'sudo', n: 'c'.repeat(32), ttl: 90, delivery,
  epk: b64u(new Uint8Array(32).fill(7)), cmd: 'sudo /usr/bin/true', why: 'Check a test prompt',
  effect: 'No changes to this fixture', tries: 3 };
const update = { t: 'upg_card', id: '2'.repeat(32), items: [{ k: 'queue', n: 1 }], from: '0.30.79',
  to: '0.30.80', ttl: 90, delivery };

async function capture(page, width, theme, kind) {
  if (!out) return;
  mkdirSync(out, { recursive: true });
  await evaluate(page, 'document.fonts.ready.then(()=>true)');
  writeFileSync(join(out, `${kind}-${width}-${theme}.png`), await shoot(page));
}

async function assertActions(page, surface, allow, deny, system) {
  const actions = await evaluate(page, `(() => {
    const m = document.querySelector(${JSON.stringify(surface)});
    const box = m.getBoundingClientRect();
    const controls = ${JSON.stringify([allow, deny])}.map(id => {
      const node = document.getElementById(id), r = node.getBoundingClientRect(), style = getComputedStyle(node);
      return { left: r.left, right: r.right, bottom: r.bottom, height: r.height,
        fill: style.backgroundColor, ink: style.color,
        reachable: [.2, .5, .8].every(y => node.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height * y))) };
    });
    const target = selector => {
      const node = document.querySelector(selector), style = getComputedStyle(node);
      return {fill: style.backgroundColor, ink: style.color, visible: node.getBoundingClientRect().height > 0};
    };
    return { left: box.left, right: box.right, bottom: box.bottom, width: box.width, viewport: innerWidth,
      fill: getComputedStyle(m).backgroundColor, system: m.parentElement.dataset.popupSystem, controls,
      quota: document.getElementById('m5h').getBoundingClientRect().top, water: target('#water').fill,
      ...(m.id === 'elev-form' ? {input: target('#elev-input'), command: target('#elev-rows dd.mono')} : {}) };
  })()`);
  assert.equal(actions.system, system, JSON.stringify(actions));
  assert.equal(actions.fill, system === 'grow' ? actions.water : 'rgb(44, 43, 38)', JSON.stringify(actions));
  if (system === 'grow') {
    assert.ok(Math.abs(actions.width - actions.viewport) <= 1 && Math.abs(actions.bottom - actions.quota) <= 2, JSON.stringify(actions));
    const ink = 'rgb(250, 249, 245)';
    assert.deepEqual(actions.input, { fill: 'rgb(44, 43, 38)', ink, visible: true });
    assert.deepEqual(actions.command, {
      fill: 'color(srgb 0.980392 0.976471 0.960784 / 0.09)', ink, visible: true,
    });
  }
  const [a, b] = actions.controls;
  assert.equal(a.fill, 'rgb(250, 249, 245)', allow);
  assert.equal(b.fill, system === 'grow' ? 'rgb(44, 43, 38)' : 'rgb(250, 249, 245)', deny);
  for (const button of [a, b]) {
    assert.equal(button.ink, button.fill === 'rgb(250, 249, 245)' ? 'rgb(44, 43, 38)' : 'rgb(250, 249, 245)');
    assert.ok(button.reachable && button.height >= 44, JSON.stringify(actions));
    assert.ok(button.left >= actions.left + 8 && button.right <= actions.right - 8 && button.bottom <= actions.bottom - 8,
      JSON.stringify(actions));
  }
  const [left, right] = [...actions.controls].sort((first, second) => first.left - second.left);
  assert.ok(right.left - left.right >= 8, JSON.stringify(actions));
}

async function expect(card, phases) {
  for (let i = 0; i < 50 && receipts(card).length < phases.length; i++) await sleep(60);
  assert.deepEqual(receipts(card), phases.map(phase => ({
    t: 'card_receipt', kind: 'special', id: card.id, n: delivery.n, phase,
  })));
}

try {
  for (const [width, theme, supported] of [[390, 'light', true], [390, 'dark', true], [390, 'dark', false],
    [1440, 'light', true], [1440, 'dark', true]]) {
    host.reset();
    host.st.extraCaps = supported ? ['card_receipt1', 'card_special_receipt1'] : ['card_receipt1'];
    host.st.prefs = { appearance: { language: 'en', theme } };
    const page = await newPage(browser, width, width === 390 ? 844 : 900, theme,
      { touch: width === 390, allow: [url, host.relay + '/'] });
    try {
      await navigate(page, host.newPairing(url + '?lang=en'));
      await waitState(page, 'awaiting-approval');
      await host.approve();
      await waitState(page, 'ready');
      await page.send('Page.bringToFront');
      await evaluate(page, "for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b?.offsetParent)b.click()}");
      await host.send({ t: 'status', s: 'idle', name: 'Agent J' });
      await waitFor(page, "import('./js/api.js').then(m=>m.canSign())");
      await host.send(sudo);
      await waitFor(page, "document.getElementById('elev')?.hidden===false");
      await sleep(200);
      if (!before) await assertActions(page, '#elev-form', 'elev-allow', 'elev-deny', 'grow');
      if (supported) await capture(page, width, theme, 'sudo');
      await expect(sudo, supported && !before ? ['received', 'visible'] : []);
      await host.send({t: 'elev_done', id: sudo.id, result: 'denied'});
      await waitFor(page, "document.getElementById('elev').hidden");
      await waitFor(page, "!document.querySelector('.toast.on[data-aj-owner=\"aj-notice\"]')");

      await evaluate(page, `(() => {
        const veil = document.createElement('div');
        veil.id = 'receipt-test-veil';
        veil.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#202020;pointer-events:auto';
        document.body.append(veil);
      })()`);
      await host.send(update);
      await waitFor(page, "document.getElementById('confirm')?.dataset.kind==='upg' && !document.getElementById('confirm').hidden");
      assert.equal(await evaluate(page, `(() => {
        const r = document.getElementById('confirm-title').getBoundingClientRect();
        return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.id;
      })()`), 'receipt-test-veil');
      await sleep(450);
      await expect(update, supported && !before ? ['received'] : []);
      await evaluate(page, "document.getElementById('receipt-test-veil').remove()");
      await expect(update, supported && !before ? ['received', 'visible'] : []);
      if (!before) await assertActions(page, '#confirm .box', 'confirm-yes', 'confirm-no', 'center');
      if (supported) await capture(page, width, theme, 'upgrade');
      assert.equal(host.log.filter(m => m.t === 'elev_answer' || m.t === 'upgrade_decision' || m.t === 'upg_card_off').length, 0);
    } finally {
      await page.dispose();
    }
  }
  console.log('PASS sudo/upgrade display receipts, covered card and legacy client negatives');
} finally {
  await browser.close();
  await host.stop();
  await web.stop();
}
