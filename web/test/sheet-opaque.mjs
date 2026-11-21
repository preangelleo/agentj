// Real remote and local synthetic Host. Repaint only text behind the card, then compare screenshot pixels.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { decodePng } from '../tools/png-evidence.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { evaluate, key, launch, navigate, newPage, shoot, waitFor, waitState } from './browser.mjs';

const out = process.env.SHEET_OPAQUE_OUT || 'reports/qa/sheet-opaque';
const variant = process.env.SHEET_OPAQUE_BASELINE === '1' ? 'before' : 'after';
const ios = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 Version/26.1 Mobile/15E148 Safari/605.1.15';
const configs = [
  [420, 912, 'terracotta', 'light'], [420, 912, 'terracotta', 'dark'],
  [420, 912, 'factory', 'light'], [420, 912, 'factory', 'dark'],
  [390, 844, 'factory', 'light'], [390, 844, 'factory', 'dark'],
  [1440, 960, 'factory', 'light'], [1440, 960, 'factory', 'dark'],
];
const request = '1'.repeat(32);
const words = '正在核对技能的版本与签名。安装后可在技能清单查看。';
const rows = [];
mkdirSync(out, { recursive: true });

function changedPixels(first, second, region) {
  const a = decodePng(first);
  const b = decodePng(second);
  assert.deepEqual([a.width, a.height], [b.width, b.height]);
  const x1 = Math.ceil(region.left + 30), x2 = Math.floor(region.right - 30);
  const y1 = Math.ceil(region.top + 30), y2 = Math.floor(region.bottom - 12);
  assert.ok(x1 >= 0 && x2 <= a.width && y1 >= 0 && y2 <= a.height && x1 < x2 && y1 < y2,
    'a visible interior region must be sampled inside the card');
  let changed = 0;
  for (let y = y1; y < y2; y++) {
    for (let x = x1; x < x2; x++) {
      const i = (y * a.width + x) * 4;
      if (a.rgba.readUInt32BE(i) !== b.rgba.readUInt32BE(i)) changed++;
    }
  }
  return { changed, area: (x2 - x1) * (y2 - y1) };
}

async function capture(p, selector, label, stem, keepScreenshot = false) {
  const visible = `(() => {
    const card = document.querySelector(${JSON.stringify(selector)});
    if (!card || getComputedStyle(card).visibility !== 'visible' || !card.getClientRects().length) return false;
    const rect = card.getBoundingClientRect();
    return rect.width > 100 && rect.height > 50 && card.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  })()`;
  await waitFor(p, visible);
  const state = await evaluate(p, `(() => {
    const card = document.querySelector(${JSON.stringify(selector)});
    const rect = card.getBoundingClientRect();
    // Freeze the visible timeout while changing the backdrop, then let the next request settle normally.
    const fixtureTime = Date.now();
    const now = Date.now;
    Date.now = () => fixtureTime;
    const backing = document.createElement('div');
    backing.id = 'opacity-fixture-underlay';
    backing.addEventListener('fixture-done', () => { Date.now = now; }, { once: true });
    backing.textContent = Array(44).fill(${JSON.stringify(words)}).join(' ');
    Object.assign(backing.style, {
      position: 'fixed', pointerEvents: 'none', zIndex: '0',
      left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px',
      color: '#faf9f5', opacity: '.7', padding: '5px 16px', boxSizing: 'border-box',
      overflow: 'hidden', font: '700 18px/1.75 sans-serif', overflowWrap: 'anywhere'
    });
    // Put the text below the whole popup layer, including any transparent wrapper.
    const layer = card.closest('.aj-shared-popup,.cmdmenu') || card;
    layer.before(backing);
    backing.style.zIndex = Math.max(0, (Number(getComputedStyle(layer).zIndex) || 0) - 1);
    const css = getComputedStyle(card), parent = getComputedStyle(document.body);
    const canvas = document.createElement('canvas').getContext('2d');
    canvas.fillStyle = css.backgroundColor;
    canvas.fillRect(0, 0, 1, 1);
    const palette = Object.fromEntries(['apprAllow','apprDeny'].map(id => [id, getComputedStyle(document.getElementById(id)).backgroundColor]));
    return {
      rect: rect.toJSON(), background: css.backgroundColor, rgba: [...canvas.getImageData(0, 0, 1, 1).data],
      palette, bodyBackground: parent.backgroundColor, bodyImage: parent.backgroundImage,
      system: (card.closest('[data-popup-system]') || card).dataset.popupSystem || null
    };
  })()`);
  const underlay = color => evaluate(p, `document.querySelector('#opacity-fixture-underlay').style.color = ${JSON.stringify(color)}`);
  const bright = await shoot(p);
  if (keepScreenshot) writeFileSync(join(out, `0.30.65-sheet-opaque-${variant}-${stem}.png`), bright);
  await underlay('#141414');
  const dim = await shoot(p);
  const { changed, area } = changedPixels(bright, dim, state.rect);
  // Positive control proves the underlay actually intersects the foreground region at this layer.
  await evaluate(p, `document.querySelector(${JSON.stringify(selector)}).style.setProperty('background-color', 'transparent', 'important')`);
  const transparentDim = await shoot(p);
  await underlay('#faf9f5');
  const transparentBright = await shoot(p);
  const control = changedPixels(transparentBright, transparentDim, state.rect).changed;
  await evaluate(p, `(() => {
    const underlay = document.querySelector('#opacity-fixture-underlay');
    underlay.dispatchEvent(new Event('fixture-done'));
    underlay.remove();
    document.querySelector(${JSON.stringify(selector)}).style.removeProperty('background-color');
  })()`);
  assert.ok(control > 100, `${stem}/${label}: exposed text must change pixels (observed ${control})`);
  rows.push({ scene: label, stem, changed, area, control, ...state });
  console.log(`${stem}/${label}: ${changed}/${area} changed pixels; exposed text changes ${control}; fill ${state.background}`);
  if (label === 'approval') {
    assert.equal(state.system, 'grow');
    assert.deepEqual(state.palette, { apprAllow: 'rgb(250, 249, 245)', apprDeny: 'rgb(44, 43, 38)' });
  }
  if (label === 'approval' && stem.endsWith('-420')) {
    assert.equal(state.bodyBackground, 'rgba(0, 0, 0, 0)', 'iOS Home Screen body keeps transparent under-page paint');
    assert.match(state.bodyImage, /^linear-gradient\(/);
  }
}

const web = await startWebServer(process.env.SHEET_OPAQUE_PUBLIC ? { publicDir: process.env.SHEET_OPAQUE_PUBLIC } : {});
const fake = await startFakeHost();
const browser = await launch();
try {
  for (const [width, height, theme, scheme] of configs) {
    const phone = width < 500;
    const stem = `${theme}-${scheme}-${width}`;
    fake.reset();
    fake.st.prefs = { appearance: { language: 'zh', theme: scheme } };
    fake.st.menu = { source: 'config', cmds: [], items: [
      { cmd: '/review', desc: '核对技能', group: '命令' },
      { cmd: '/status', desc: '查看状态', group: '命令' },
    ], skills: [] };
    await fake.addTurn({ k: 'phone', dev: 'fixture', name: '测试遥控器', text: '请检查示例技能再安装。' }, words.repeat(12), 'done');
    const p = await newPage(browser, width, height, scheme, { ua: phone ? ios : undefined, touch: phone, allow: [web.url, fake.relay + '/'] });
    try {
      if (phone) await p.send('Page.addScriptToEvaluateOnNewDocument', {
        source: 'Object.defineProperty(navigator, "standalone", { value: true });',
      });
      await navigate(p, fake.newPairing(web.url + '?lang=zh'));
      await waitState(p, 'awaiting-approval');
      await fake.approve();
      await waitState(p, 'ready');
      await evaluate(p, `(() => {
        document.getElementById('a2hs-ok')?.click();
        document.getElementById('phone-env-dismiss')?.click();
        document.activeElement?.blur();
        const sheet = new CSSStyleSheet();
        sheet.replaceSync('*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}');
        document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
      })()`);
      if (phone) {
        assert.equal(await evaluate(p, 'document.documentElement.dataset.ios'), '1');
        assert.equal(await evaluate(p, 'document.documentElement.dataset.standalone'), '1');
      }
      // The waiting color is sampled from the unaffected header edge of the owner's Terracotta screenshot.
      await fake.send(theme === 'terracotta'
        ? { t: 'theme', v: 1, id: 'terracotta', colors: { state: { waiting: '#a96953' } } }
        : { t: 'theme', v: 1, factory: 'robots' });
      await waitFor(p, `document.documentElement.dataset.look === ${JSON.stringify(theme === 'terracotta' ? 'terracotta' : 'robots')}`);
      await fake.send({ t: 'status', s: 'waiting', kind: 'permission', agent: 'claude', name: 'Olares' });
      await fake.ask({ id: request, tool: 'Bash', summary: 'agentj plaza', cmd: 'agentj plaza install example-skill', ttl: 600 });
      await waitFor(p, `document.body.dataset.sheet === '1' && document.getElementById('sheet').dataset.popupSystem === 'grow'`);
      await capture(p, '#sheet', 'approval', stem, true);
      await fake.askDone(request, 'deny');
      await waitFor(p, `document.body.dataset.sheet !== '1'`);

      if (width === 420) {
        await evaluate(p, `import('./js/ui.js').then(m => {
          void m.confirmSheet('确认安装', '已核对示例技能的版本与签名。', '确认', false, '取消', '', 'grow');
        })`);
        await waitFor(p, `document.getElementById('confirm').dataset.popupSystem === 'grow'`);
        await capture(p, '#confirm > .box', 'confirm', stem);
        await key(p, 'Escape');
        await waitFor(p, `document.getElementById('confirm').hidden`);
      }
      if (width === 420 && theme === 'factory' && scheme === 'dark') {
        await fake.send({ t: 'question', id: request, ttl: 600, qs: [
          { q: '先核对哪一个版本？', h: '', m: false, o: [{ l: '当前版本' }, { l: '最新版本' }] },
        ] });
        await waitFor(p, `!!document.querySelector('#qs .opt')`);
        await capture(p, '#sheet', 'question', stem);
        await fake.send({ t: 'question_done', id: request, result: 'gone' });
        await waitFor(p, `document.body.dataset.sheet !== '1'`);

        await evaluate(p, `document.getElementById('slashBtn').click()`);
        await waitFor(p, `document.querySelector('#menuList').childElementCount && !document.getElementById('menu').hidden`);
        await capture(p, '#menuSheet', 'commands', stem);
        await key(p, 'Escape');
        await evaluate(p, `import('./js/hotkey.js').then(m => m.openHot())`);
        await capture(p, '#hotSheet', 'shortcuts', stem);
        await key(p, 'Escape');

        const noticeFill = await evaluate(p, `import('./js/ui.js').then(m => {
          const notice = m.notify({ kind: 'info', shape: 'rect', title: '核对完成', text: '示例技能的版本与签名已核对。', ms: 6000 });
          return getComputedStyle(notice).backgroundColor;
        })`);
        assert.equal(noticeFill, 'rgb(44, 43, 38)', 'notifications retain their Noir ground');
      }
    } finally { await p.dispose(); }
  }
  const leaks = rows.filter(row => row.changed > 0 || row.rgba[3] !== 255);
  if (variant === 'before') {
    assert.ok(leaks.some(row => row.scene === 'approval' && row.stem.startsWith('terracotta')), 'baseline must paint the reported leak');
  }
  assert.deepEqual(leaks.map(row => row.stem + '/' + row.scene), [], 'text behind any bottom card must leave its pixels unchanged');
  console.log(`PASS sheet opacity (${rows.length} painted scenarios)`);
} finally {
  writeFileSync(join(out, variant + '-pixels.json'), JSON.stringify({ variant, rows }, null, 2) + '\n');
  await browser.close();
  await fake.stop();
  await web.stop();
}
