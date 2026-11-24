// Real Playwright touch input against the shipped app and a loopback Noise host.
// PAGE_COUNTER_PUBLIC_DIR can point at an extracted release tag's public tree.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';

const playwright = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || 'playwright');
const source = process.env.PAGE_COUNTER_PUBLIC_DIR;
const label = process.env.PAGE_COUNTER_LABEL || 'candidate';
const out = resolve(process.env.PAGE_COUNTER_OUT || 'reports/qa/page-counter-touch', label);
mkdirSync(out, { recursive: true });
const web = await startWebServer(source ? { publicDir: resolve(source) } : {});
const fake = await startFakeHost();
const rows = [];
const tasks = [
  { id: 'synthetic-design', who: '设计', label: '检查合成截图', c: 0 },
  { id: 'synthetic-web', who: '前端', label: '回放触摸旅程', c: 1 },
  { id: 'synthetic-qa', who: '测试', label: '核对最新消息', c: 2 },
];
const historical = '4 / 6';
const newest = '6 / 6';

function snapshot() {
  const rect = element => {
    const { x, y, width, height, left, right, top, bottom } = element.getBoundingClientRect();
    return { x, y, width, height, left, right, top, bottom };
  };
  const select = selector => document.querySelector(selector);
  const style = select('#busyDots');
  return {
    page: select('#pg').textContent,
    unread: select('#pgCorner').dataset.unread === '1',
    busy: !select('#busyDlg').hidden,
    load: !select('#loadDlg').hidden,
    reader: !select('#rd').hidden,
    sourceOpen: select('#om').classList.contains('open'),
    copied: select('#omCopy').dataset.done || null,
    source: select('#omText').textContent,
    reply: select('#words').textContent,
    scale: visualViewport.scale,
    target: {
      pager: rect(select('#pgLatest')),
      badge: rect(select('#pg')),
      robots: rect(style),
      icons: [...style.children].filter(node => node.getClientRects().length).map(rect),
      more: rect(select('#omMore')),
      copy: rect(select('#omCopy')),
      first: rect(select('#omFirst')),
      unread: rect(select('#omUnread')),
      load: rect(select('#loadHit')),
    },
  };
}

function point(rect, horizontal = 0.5, vertical = 0.5) {
  return { x: Math.floor(rect.x + rect.width * horizontal), y: Math.floor(rect.y + rect.height * vertical) };
}

function measured(point) {
  const target = document.elementFromPoint(point.x, point.y);
  return { hit: target?.closest('button')?.id || target?.id || target?.localName || null, ...point };
}

async function waitPage(page, counter, unread) {
  await page.waitForFunction(({ counter, unread }) => {
    const current = document.getElementById('pg').textContent;
    const badge = document.getElementById('pgCorner').dataset.unread === '1';
    return current === counter && (unread === undefined || unread === badge);
  }, { counter, unread });
}

async function addTurns(device, first, last) {
  for (let i = first; i <= last; i++) {
    await fake.addTurn(
      { k: 'phone', dev: device, text: i === 4 ? '好了，弹登记卡' : `合成主人消息 ${i}` },
      i === 4
        ? '请从右上角的页码回到最新一条消息。\n\n这是一条旧回复，用来检查两个卡片的触摸命中区。\n\n```sh\nagentj status\n```\n\n回复卡右上还有后台任务机器人。'
        : `合成 Agent 回复 ${i}。`,
    );
  }
}

async function previous(page, counter) {
  for (let i = 0; i < 2; i++) await page.keyboard.press('j');
  await waitPage(page, counter);
}

async function setup(browser, width) {
  fake.reset();
  fake.st.prefs = { appearance: { theme: 'dark', language: 'zh' }, voice: { speak_replies: false } };
  fake.st.meter = { model: 'opus', model_name: 'Opus 5.5', effort: 'medium', at: Math.floor(Date.now() / 1000) };
  const context = await browser.newContext({
    viewport: { width, height: 844 },
    isMobile: true,
    hasTouch: true,
    colorScheme: 'dark',
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
  });
  await context.route('**/*', route => {
    const location = new URL(route.request().url());
    return ['localhost', '127.0.0.1'].includes(location.hostname) && location.port === String(web.port)
      ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  await page.addInitScript(() => {
    window.AudioContext = undefined;
    window.webkitAudioContext = undefined;
    window.Audio = function () { return { play: async () => {}, pause() {}, addEventListener() {}, removeEventListener() {} }; };
    window.__counterTouch = [];
    for (const type of ['pointerdown', 'pointerup', 'touchstart', 'touchend', 'click', 'dblclick']) {
      document.addEventListener(type, event => {
        const target = event.target;
        const coordinates = event.changedTouches?.[0] || event;
        window.__counterTouch.push({
          type, target: target?.closest?.('button')?.id || target?.id || target?.localName || null,
          trusted: event.isTrusted, pointer: event.pointerType ?? null,
          canceled: event.defaultPrevented, time: event.timeStamp,
          x: coordinates.clientX ?? null, y: coordinates.clientY ?? null,
        });
      }, { capture: true, passive: true });
    }
    document.addEventListener('pointerdown', event => {
      const target = event.target?.closest?.('button');
      if (target) window.__counterTouch.push({ type: 'pointerdown-bubble', target: target.id, canceled: event.defaultPrevented });
    }, { passive: true });
  });
  try {
    await page.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=zh'));
    await page.waitForFunction(() => window.__ajState === 'awaiting-approval');
    await fake.approve();
    await page.waitForFunction(() => window.__ajState === 'ready');
    await page.evaluate(() => {
      for (const id of ['phone-env-dismiss', 'a2hs-ok']) document.getElementById(id)?.click();
    });
    // The actual input controls below are driven by the browser, not DOM click().
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    const device = await page.evaluate(() => import('./js/api.js').then(api => api.myDeviceId()));
    await addTurns(device, 1, 6);
    await waitPage(page, newest);
    await fake.send({ t: 'busy', tasks, task_count: tasks.length, load: 0.25 });
    await page.waitForFunction(() => document.querySelectorAll('#busyDots i.rb').length === 3);
    await page.evaluate(() => document.fonts.ready);
    return { context, page, device };
  } catch (error) {
    await context.close();
    throw error;
  }
}

async function journey(browser, engine, width) {
  const { context, page, device } = await setup(browser, width);
  const row = { engine, browser: browser.version(), width, height: 844, cases: [] };
  rows.push(row);
  const shot = name => page.screenshot({ path: resolve(out, `${engine}-${width}-${name}.png`) });
  async function probe(name, at, check, repeat = 1, mouse = false) {
    const hit = await page.evaluate(measured, at);
    await page.evaluate(() => { window.__counterTouch.length = 0; });
    if (mouse) {
      await page.mouse.dblclick(at.x, at.y);
    } else {
      for (let i = 0; i < repeat; i++) await page.touchscreen.tap(at.x, at.y);
    }
    await page.waitForTimeout(name === 'single visible robot tap' ? 900 : 430);
    const state = await page.evaluate(snapshot);
    const events = await page.evaluate(() => window.__counterTouch.slice());
    const errors = [];
    try {
      if (!mouse) {
        assert.equal(events.filter(event => event.type === 'touchstart' && event.trusted).length, repeat, 'trusted touchstart count');
        assert.equal(events.filter(event => event.type === 'touchend' && event.trusted).length, repeat, 'trusted touchend count');
      }
      check(state, events, hit);
    } catch (error) { errors.push(error.message); }
    row.cases.push({ name, hit, events, page: state.page, busy: state.busy, reader: state.reader, load: state.load, errors });
    return state;
  }
  function latest(state, counter = newest) {
    assert.equal(state.page, counter, 'page counter jumps to the latest message');
    assert.equal(state.busy, false, 'page counter does not open the robot list');
    assert.equal(state.reader, false, 'page counter does not open the reader');
    assert.equal(state.scale, 1, 'tapping does not zoom the page');
  }
  async function closeRobot() {
    if (await page.locator('#busyDlg').isVisible()) {
      const box = await page.locator('#busyClose').boundingBox();
      const at = point(box);
      await page.touchscreen.tap(at.x, at.y);
      await page.locator('#busyDlg').waitFor({ state: 'hidden' });
    }
  }
  try {
    await previous(page, historical);
    const initial = await page.evaluate(snapshot);
    row.geometry = initial.target;
    await shot('historical');
    const { pager, badge, robots, icons } = initial.target;
    const geometryErrors = [];
    for (const [name, rect] of Object.entries({ pager, robots })) {
      if (rect.width < 44 || rect.height < 44) geometryErrors.push(`${name} touch target is smaller than 44×44 CSS px`);
    }
    if (pager.left < robots.right && robots.left < pager.right && pager.top < robots.bottom && robots.top < pager.bottom) {
      geometryErrors.push('pager and robot target rectangles overlap');
    }
    row.cases.push({ name: 'target geometry', errors: geometryErrors });

    await probe('single page badge tap, read history', point(badge), state => latest(state));
    await shot('single-page-tap');
    await closeRobot();
    if ((await page.evaluate(snapshot)).page === newest) await previous(page, historical);

    await probe('double page badge tap', point(badge), state => latest(state), 2);
    await closeRobot();
    if ((await page.evaluate(snapshot)).page === newest) await previous(page, historical);
    // The history double-click recognizer ignores compatibility events for 700 ms after touch.
    await page.waitForTimeout(750);
    await probe('desktop-style mouse double click on the page badge', point(badge), state => latest(state), 2, true);
    await closeRobot();
    if ((await page.evaluate(snapshot)).page === newest) await previous(page, historical);

    const { copy, more } = initial.target;
    const yNearTop = Math.ceil(pager.top + 2);
    const yNearBottom = Math.floor(pager.bottom - 2);
    for (const [name, at] of [
      ['upper edge of the page target', { x: point(badge).x, y: yNearTop }],
      ['lower edge of the page target above copy', { x: point(copy).x, y: yNearBottom }],
      ['lower edge of the page target above more', { x: point(more).x, y: yNearBottom }],
    ]) {
      await probe(name, at, state => {
        latest(state);
        assert.equal(state.sourceOpen, false, 'page target does not expand the owner card');
        assert.equal(state.copied, null, 'page target does not copy the owner message');
      });
      const state = await page.evaluate(snapshot);
      if (state.sourceOpen) {
        const at = point(state.target.more);
        await page.touchscreen.tap(at.x, at.y);
        await page.waitForFunction(() => !document.getElementById('om').classList.contains('open'));
      }
      if (state.page === newest) await previous(page, historical);
      if (state.copied) await page.waitForFunction(() => !document.getElementById('omCopy').dataset.done);
    }

    const robot = point(icons.at(-1));
    await probe('single visible robot tap', robot, state => {
      assert.equal(state.page, historical, 'robot does not page the conversation');
      assert.equal(state.busy, true, 'robot opens the task list');
    });
    await shot('robot-tap');
    await closeRobot();
    await probe('double visible robot tap', robot, (state, events) => {
      assert.equal(state.page, historical, 'double robot tap does not page');
      assert.equal(state.busy, true, 'double robot tap leaves the task list open');
      assert.equal(state.reader, false, 'double robot tap does not open reader');
      assert.equal(state.load, false, 'double robot tap does not open load');
      assert.ok(events.some(event => event.type === 'touchstart' && event.target === 'busyDots'), 'double tap starts on the robot');
    }, 2);
    await closeRobot();

    const left = Math.min(...icons.map(icon => icon.left));
    const blank = { x: Math.floor(left - 9), y: Math.floor(robots.top + robots.height / 2) };
    await probe('reply header just left of the visible robot group', blank, state => {
      assert.equal(state.page, historical, 'outside tap does not page');
      assert.equal(state.busy, false, 'outside tap does not open the robot list');
    });
    await closeRobot();
    const blankAbove = { x: robot.x, y: Math.floor(robots.top - 1) };
    await probe('reply header just above the robot target', blankAbove, state => {
      assert.equal(state.page, historical, 'outside tap does not page');
      assert.equal(state.busy, false, 'outside tap above does not open the robot list');
    });
    await closeRobot();

    await fake.send({ t: 'busy', tasks: tasks.slice(0, 1), task_count: 1, load: 0.25 });
    await page.waitForFunction(() => document.querySelectorAll('#busyDots i.rb').length === 1);
    const singleRobot = (await page.evaluate(snapshot)).target;
    const minTargetErrors = [];
    if (singleRobot.robots.width < 44 || singleRobot.robots.height < 44) {
      minTargetErrors.push('one visible robot needs at least a 44×44 CSS px target');
    }
    row.cases.push({ name: 'one robot target geometry', errors: minTargetErrors });
    await probe('one visible robot opens the list', point(singleRobot.icons[0]), state => {
      assert.equal(state.page, historical);
      assert.equal(state.busy, true);
    });
    await closeRobot();
    await fake.send({ t: 'busy', tasks, task_count: tasks.length, load: 0.25 });
    await page.waitForFunction(() => document.querySelectorAll('#busyDots i.rb').length === 3);

    await probe('visible source copy', point(copy), state => {
      assert.equal(state.page, historical, 'copy does not page');
      assert.equal(state.copied, '1', 'source copy shows success feedback');
      assert.equal(state.busy, false, 'copy does not open the robot list');
    });
    await page.waitForFunction(() => !document.getElementById('omCopy').dataset.done);
    await probe('visible source expand', point(more), state => {
      assert.equal(state.page, historical, 'expanding source does not page');
      assert.equal(state.sourceOpen, true, 'visible expand button opens the source card');
      assert.equal(state.busy, false, 'expand does not open the robot list');
    });
    const expanded = await page.evaluate(snapshot);
    await probe('visible source collapse', point(expanded.target.more), state => {
      assert.equal(state.page, historical, 'collapsing source does not page');
      assert.equal(state.sourceOpen, false, 'visible collapse button closes the source card');
    });

    const load = point((await page.evaluate(snapshot)).target.load);
    await probe('reply load line', load, state => {
      assert.equal(state.page, historical);
      assert.equal(state.load, true, 'load line opens its own dialog');
      assert.equal(state.busy, false, 'load line does not open robots');
    });
    if (await page.locator('#loadDlg').isVisible()) {
      await page.locator('#loadClose').tap();
      await page.locator('#loadDlg').waitFor({ state: 'hidden' });
    }

    await probe('source first corner still requires double tap', point(initial.target.first), state => {
      assert.equal(state.page, historical);
    });
    await probe('source first corner double tap', point(initial.target.first), state => {
      assert.equal(state.page, '1 / 6');
    }, 2);
    if ((await page.evaluate(snapshot)).page === '1 / 6') {
      for (let i = 0; i < 3; i++) await page.keyboard.press('k');
      await waitPage(page, historical);
    }

    await page.locator('#readBtn').tap();
    await page.locator('#rd').waitFor({ state: 'visible' });
    const readerFirst = point(await page.locator('#rdWho').boundingBox());
    const readerLast = point(await page.locator('#rdPgBtn').boundingBox());
    await probe('reader latest still requires double tap', readerLast, state => {
      assert.equal(state.page, historical);
      assert.equal(state.reader, true);
    });
    await probe('reader latest double tap', readerLast, state => {
      assert.equal(state.page, newest);
      assert.equal(state.reader, true);
    }, 2);
    await probe('reader first double tap', readerFirst, state => {
      assert.equal(state.page, '1 / 6');
      assert.equal(state.reader, true);
    }, 2);
    await page.keyboard.press('Escape');
    await page.locator('#rd').waitFor({ state: 'hidden' });
    const current = (await page.evaluate(snapshot)).page;
    if (current === '1 / 6') {
      for (let i = 0; i < 3; i++) await page.keyboard.press('k');
      await waitPage(page, historical);
    } else if (current === newest) {
      await previous(page, historical);
    }

    await addTurns(device, 7, 7);
    await waitPage(page, '4 / 7', true);
    const unread = await page.evaluate(snapshot);
    await shot('unread-badge');
    const edge = { x: Math.floor(unread.target.badge.right - 2), y: point(unread.target.badge).y };
    await probe('single tap inside right edge of unread page badge', edge, state => {
      latest(state, '7 / 7');
      assert.equal(state.unread, false, 'latest clears unread badge');
      assert.equal(state.reply.trim(), '合成 Agent 回复 7。', 'latest reply is actually rendered');
    });
    await shot('unread-badge-tap');
    if ((await page.evaluate(snapshot)).page === '7 / 7') {
      await previous(page, '5 / 7');
      await addTurns(device, 8, 9);
      await waitPage(page, '5 / 9', true);
      const { target } = await page.evaluate(snapshot);
      await probe('first unread badge', point(target.unread), state => {
        assert.equal(state.page, '8 / 9');
        assert.equal(state.busy, false);
      });
    }
  } finally {
    await context.close();
  }
}

const engines = (process.env.PAGE_COUNTER_ENGINES || 'webkit,chromium').split(',');
const widths = (process.env.PAGE_COUNTER_WIDTHS || '390,420').split(',').map(Number);
try {
  for (const engine of engines) {
    const browser = await playwright[engine].launch();
    try {
      for (const width of widths) await journey(browser, engine, width);
    } finally {
      await browser.close();
    }
  }
} finally {
  await fake.stop();
  await web.stop();
  writeFileSync(resolve(out, 'result.json'), JSON.stringify({ label, input: 'Playwright touchscreen.tap', rows }, null, 2) + '\n');
}
for (const { engine, width, cases } of rows) {
  for (const { name, hit, page, busy, errors } of cases) {
    const verdict = errors.length ? 'FAIL' : 'PASS';
    console.log(`${verdict} ${label} ${engine} ${width} ${name}`, JSON.stringify({ hit, page, busy, errors }));
    if (errors.length) process.exitCode = 1;
  }
}
