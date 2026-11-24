// 0374 (chairman 11-24 07:37, iPhone Air home-screen app, live 0.30.72): the terminal's login-password field was out of sight
// with the on-screen keyboard up; it only appeared (at the top) after the first typed character.
// A journey on Safari's engine (Playwright WebKit, iPhone profile, home-screen flag) against the real page + the fake host:
// every surface that holds a text field is opened the way the owner (or the Agent J host) opens it, the field takes the
// focus, the keyboard "comes up", and the focused field must then be
//   · the active element, · wholly inside the visual viewport, · not covered (elementFromPoint at its centre and the middle of its 4 edges).
//
// HOW THE KEYBOARD IS SIMULATED (desktop WebKit never shows one): window.visualViewport is replaced before the page's
// scripts run by an EventTarget stand-in whose height / offsetTop the test sets; `resize` is dispatched on it and the layout
// viewport (innerHeight) stays the full screen — the numbers iOS reports in a home-screen app (it does not shrink the layout
// viewport, and fires no window resize). Not modelled: the keyboard animation, iOS's own scroll of the visual viewport toward
// a focused field (the panned case injects offsetTop directly), changes in the QuickType / password bar, or the
// engine's caret reveal timing. This is NOT a physical iPhone acceptance.
//
// env: P0374_PUBLIC=<dir>  another public/ (the pre-fix tree → the red run) · P0374_OUT (default reports/qa/p0374)
//      P0374_TAG (default after) · P0374_ONLY=a,b cases · P0374_SHOTS=1 screenshots in zh, light/dark, mobile/desktop
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { keysHandler } from './p352-fixture.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { approvalEnvelopeDigest, b64u } from '../../protocol/wire.js';

const { webkit, devices } = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const OUT = resolve(process.env.P0374_OUT || 'reports/qa/p0374'), TAG = process.env.P0374_TAG || 'after';
const ONLY = (process.env.P0374_ONLY || '').split(',').filter(Boolean), SHOTS = process.env.P0374_SHOTS === '1';
const SPECS = JSON.parse(readFileSync(new URL('./fixtures/p212-specs.json', import.meta.url), 'utf8'));
const hex = (n) => randomBytes(n).toString('hex');
const epk = () => generateKeyPairSync('x25519').publicKey.export({ format: 'jwk' }).x;
mkdirSync(OUT, { recursive: true });

// iPhone 13 / 14 (390 × 844) with the keyboard + its bar (380 px) · iPhone Air (420 × 912; the owner's shot: 444 px gone)
const PHONE = { w: 390, h: 844, kb: 380 }, AIR = { w: 420, h: 912, kb: 444 }, DESKTOP = { w: 1440, h: 960, kb: 0 };
const NARROW = { w: 320, h: 568, kb: 290 };
const FIELD = 'input:not([type=button]):not([type=submit]):not([type=reset]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=file]):not([type=color]):not([type=hidden]),textarea,[contenteditable="true"]';

function initScript() {
  Object.defineProperty(Navigator.prototype, 'standalone', { configurable: true, get: () => true });   // home-screen app
  const vv = new EventTarget(); let kb = 0, top = 0;
  for (const [k, get] of [['height', () => innerHeight - kb], ['width', () => innerWidth], ['offsetTop', () => top], ['offsetLeft', () => 0],
    ['pageTop', () => top], ['pageLeft', () => 0], ['scale', () => 1]]) Object.defineProperty(vv, k, { get });
  Object.defineProperty(window, 'visualViewport', { configurable: true, value: vv });
  window.__kbd = (h, offset = 0) => { kb = h; top = offset; vv.dispatchEvent(new Event('resize')); if (offset) vv.dispatchEvent(new Event('scroll')); };
  // this WebKit build has no platform authenticator: a stand-in that answers with the asked credential
  const ab = (s) => new TextEncoder().encode(s).buffer;
  if (!navigator.credentials) Object.defineProperty(navigator, 'credentials', { configurable: true, value: {} });
  if (!window.PublicKeyCredential) window.PublicKeyCredential = function PublicKeyCredential() {};
  navigator.credentials.get = (o) => Promise.resolve({ rawId: o.publicKey.allowCredentials?.[0]?.id || ab('p0374'), type: 'public-key',
    response: { clientDataJSON: ab(JSON.stringify({ type: 'webauthn.get', challenge: 'p0374' })), authenticatorData: ab('a'.repeat(37)), signature: ab('sig'), userHandle: null } });
  window.Audio = undefined;   // no audio backend on the runner
  for (const m of ['play', 'pause', 'load']) HTMLMediaElement.prototype[m] = m === 'play' ? () => Promise.resolve() : () => {};
}

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.P0374_PUBLIC ? { publicDir: resolve(process.env.P0374_PUBLIC) + '/' } : {}) });
// Use the upstream WPE harness defaults to avoid native compositor crashes on Linux.
const webkitOptions = process.platform === 'linux' ? {
  env: { ...process.env, LIBGL_ALWAYS_SOFTWARE: '1', WEBKIT_SKIA_ENABLE_CPU_RENDERING: '1' },
} : {};
const fake = await startFakeHost(), B = await webkit.launch(webkitOptions);
const PAGE = web.url.replace('127.0.0.1', 'localhost');
const rows = [];
class PairingSetupError extends Error {}

/** One fresh paired phone. */
async function phone({ geo = PHONE, theme = 'light', language = 'zh', path = '', pair = true, friends = false, contacts = false, caps = [], onApp = null } = {}) {
  fake.reset(); fake.setUp(true); fake.st.prefs = { appearance: { language, theme } }; fake.st.terminal = null; fake.st.onApp = onApp;
  fake.st.extraCaps = caps;
  if (friends) {
    const fixtures = sampleFriends();
    fixtures.devices = { n: 1, unread: 0 };
    fake.friends(fixtures);
  }
  if (contacts) {
    fake.st.caps = ['contacts'];
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'ct_list') return false;
      await send({ t: 'ct_res', r: m.r, ok: true, contacts: [], count: 0, me: [], mail: { ready: true } });
      return true;
    };
  }
  const ctx = await B.newContext({ ...(geo === DESKTOP ? {} : devices['iPhone 13']), viewport: { width: geo.w, height: geo.h }, deviceScaleFactor: geo === DESKTOP ? 1 : 2, serviceWorkers: 'block', colorScheme: theme });
  try {
    await ctx.route(/^https?:/, route => new URL(route.request().url()).origin === new URL(PAGE).origin
      ? route.continue() : route.abort());
    await ctx.addInitScript(initScript);
    const p = await ctx.newPage();
    p.geo = geo; p.theme = theme; p.language = language;
    if (!pair) { await p.goto(PAGE + path + '?lang=' + language); return p; }
    await p.goto(fake.newPairing(PAGE + path + '?lang=' + language));
    try {
      await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { polling: 100, timeout: 15000 });
    } catch (error) {
      if (error.name !== 'TimeoutError') throw error;
      const state = await p.evaluate(() => window.__ajState || 'unloaded').catch(() => 'unavailable');
      throw new PairingSetupError('fresh local pairing did not reach approval (state ' + state + ')');
    }
    await fake.approve();
    await waitStage(p, 'paired phone connects', () => window.__ajState === 'ready');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await p.evaluate(th => import('./js/settings.js').then(m => m.applyThemeLocal(th)), theme);
    await p.evaluate(() => {
      document.getElementById('phone-env-dismiss')?.click();
      if (!document.getElementById('a2hs')?.hidden) document.getElementById('a2hs-ok')?.click();
    });
    return p;
  } catch (error) {
    await ctx.close().catch(() => {});
    throw error;
  }
}
const turn = async (p, text = '示例问题', reply = '这是一段示例回复，用来把聊天页面填满。'.repeat(12)) => {
  const t = await fake.addTurn({ k: 'phone', text }, reply, 'done'); await p.waitForTimeout(400); return t;
};
// Let the existing 300ms viewport recheck run, then await native render frames.
function layoutFrames(p, delay = 0) {
  return p.evaluate(delay => new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error('WebKit did not complete the render frames')), 8000);
    setTimeout(() => requestAnimationFrame(() => requestAnimationFrame(() => {
      clearTimeout(deadline);
      resolve();
    })), delay);
  }), delay);
}
async function kbd(p, on, offset = 0) {
  await p.evaluate(([height, top]) => window.__kbd(height, top), [on ? p.geo.kb : 0, offset]);
  await layoutFrames(p, on ? 360 : 80);
}

/** The measurement: the element (a handle) against the visual viewport, and what is drawn over it. */
const measure = (p, h) => h.evaluate((el) => {
  const vv = window.visualViewport, r = el.getBoundingClientRect(), top = vv.offsetTop, bottom = vv.offsetTop + vv.height;
  let inside = r.height > 0 && r.width > 0 && r.top >= top - 0.5 && r.bottom <= bottom + 0.5 && r.left >= -0.5 && r.right <= innerWidth + 0.5;
  for (let box = el.parentElement; inside && box && box !== document.body; box = box.parentElement) {
    const css = getComputedStyle(box);
    if (css.overflowY !== 'visible') {
      const edge = box.getBoundingClientRect().top + box.clientTop;
      inside = r.top >= edge - 0.5 && r.bottom <= edge + box.clientHeight + 0.5;
    }
    if (css.position === 'fixed') break;
  }
  // the centre and the middle of each edge (a rounded corner is not the field)
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2, dx = Math.min(10, r.width / 4), pts = [[cx, cy], [cx, r.top + 1], [cx, r.bottom - 1], [r.left + dx, cy], [r.right - dx, cy]];
  const cover = [];
  for (const [x, y] of pts) { const hit = document.elementFromPoint(x, y); if (!(hit && (hit === el || el.contains(hit)))) cover.push(hit ? (hit.id ? '#' + hit.id : hit.tagName.toLowerCase() + '.' + String(hit.className).split(' ')[0]) : 'nothing'); }
  return { id: el.id || el.className || el.tagName, rect: [Math.round(r.top), Math.round(r.bottom)], view: [Math.round(top), Math.round(bottom)], inside, cover, focused: document.activeElement === el,
    margin: Math.round(Math.min(r.top - top, bottom - r.bottom)) };
});
function record(name, m, extra = {}) {
  const ok = m.inside && !m.cover.length && m.focused;
  rows.push({ case: name, field: m.id, ok, ...m, ...extra });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name} · ${m.id} rect ${m.rect} in ${m.view}${m.focused ? '' : ' NOT FOCUSED'}${m.cover.length ? ' covered by ' + m.cover.join(',') : ''}`);
  return ok;
}
let shotN = 0;
/** A screenshot with the area the keyboard takes painted (a labelled stand-in, drawn only for the picture). */
async function shot(p, name) {
  if (!SHOTS) return;
  if (p.geo.kb) await p.evaluate(() => {
    const k = document.createElement('div'); k.id = 'p0374-kbd'; const s = k.style, vv = window.visualViewport;
    s.position = 'fixed'; s.left = '0'; s.right = '0'; s.top = (vv.offsetTop + vv.height) + 'px'; s.bottom = '0'; s.zIndex = '2147483647';
    s.background = '#2b2a27'; s.color = '#faf9f5'; s.display = 'grid'; s.placeItems = 'center'; s.font = '600 16px sans-serif'; s.borderTop = '1px solid rgba(250,249,245,.3)';
    k.textContent = '键盘（模拟）'; document.documentElement.append(k);
  });
  await p.screenshot({ path: resolve(OUT, `${TAG}-${String(++shotN).padStart(2, '0')}-${name}-${p.geo.w}-${p.theme}.png`) });
  await p.evaluate(() => document.getElementById('p0374-kbd')?.remove());
}
/** A field the page focused itself (a card that pops with the cursor in it) or that the owner taps: keyboard up → measure. */
async function focused(p, name, sel, { focus = false, offset = 0 } = {}) {
  await waitField(p, sel);
  const h = p.locator(sel);
  if (focus) await focusField(p, h);
  else await p.waitForFunction(selector => document.activeElement === document.querySelector(selector), sel, { timeout: 4000 })
    .catch(error => { throw new Error(sel + ' did not receive autofocus before the keyboard opened: ' + error.message); });
  await kbd(p, true, offset);
  const m = await measure(p, h), ok = record(name, m);
  if (!ok && process.env.P0374_DEBUG) console.log(await h.evaluate(el => {
    const result = [];
    for (let node = el; node; node = node.parentElement) {
      const rect = node.getBoundingClientRect(), css = getComputedStyle(node);
      result.push([node.id || node.tagName, rect.top, rect.bottom, css.position, css.overflowY,
        node.scrollTop, node.clientHeight, node.scrollHeight, node.dataset.popupSystem]);
    }
    return result;
  }));
  await shot(p, name.replace(/[^a-z0-9]+/gi, '-'));
  await kbd(p, false);
  return ok;
}
/** Focus an owner-selected field with the keyboard still down, without an extra native scroll on focus. */
async function focusField(p, h) {
  await h.evaluate((el) => el.scrollIntoView({ block: 'center', inline: 'nearest' }));
  await p.waitForTimeout(80);
  await h.evaluate(el => el.focus({ preventScroll: true }));
}
/** Every visible, enabled text field under `root`, one after the other. */
async function sweep(p, name, root, { min = 1, shotFirst = false } = {}) {
  const hs = [];
  for (const h of await p.$$(`${root} :is(${FIELD})`)) {
    if (await h.evaluate((el) => !el.disabled && !el.readOnly && el.getClientRects().length > 0 && !el.closest('[hidden],[inert],details:not([open])') && getComputedStyle(el).visibility !== 'hidden')) hs.push(h);
  }
  assert.ok(hs.length >= min, `${name}: ${hs.length} fields under ${root}, expected at least ${min}`);
  let n = 0;
  for (const h of hs) {
    if (!(await h.evaluate((el) => el.isConnected && el.getClientRects().length > 0))) continue;   // a field a former one's input removed
    await focusField(p, h);
    await kbd(p, true);
    if (!record(name, await measure(p, h)) && process.env.P0374_DEBUG) {
      await p.screenshot({ path: resolve(OUT, `red-${name}-${n}.png`) });
      console.log(await h.evaluate((el) => { const out = []; for (let a = el; a && a !== document.documentElement; a = a.parentElement) { const c = getComputedStyle(a), r = a.getBoundingClientRect();
        out.push(`   ${a.tagName.toLowerCase()}${a.id ? '#' + a.id : ''}.${String(a.className).split(' ')[0]} ${Math.round(r.top)}..${Math.round(r.bottom)} ${c.position} ov=${c.overflowY} sc=${Math.round(a.scrollTop)}/${a.scrollHeight}/${a.clientHeight} ${c.display} shrink=${c.flexShrink}`); } return out.join('\n'); }));
    }
    if (shotFirst && !n) await shot(p, name.replace(/[^a-z0-9]+/gi, '-'));
    await kbd(p, false);
    await h.evaluate((el) => el.blur()).catch(() => {});
    n++;
  }
  return n;
}

async function noteShot(p, state) {
  if (!SHOTS) return;
  if (state === 'keyboard') await p.evaluate(() => {
    // A popover in the modal belongs to the browser's top layer, above the native dialog.
    // It is present only for the screenshot, after hit testing the unmodified document.
    const mask = document.createElement('div');
    mask.id = 'note-keyboard-stand-in';
    mask.popover = 'manual';
    mask.textContent = '键盘（模拟）';
    mask.style.cssText = 'position:fixed;inset:auto 0 0;margin:0;width:auto;box-sizing:border-box;display:grid;place-items:center;background:#2b2a27;color:#faf9f5;border:0;border-top:1px solid #6f6e68;font:600 16px sans-serif;pointer-events:none';
    mask.style.height = innerHeight - visualViewport.height + 'px';
    document.querySelector('.jc-dialog[open]').append(mask);
    mask.showPopover();
  });
  try {
    await p.screenshot({ path: resolve(OUT, `${TAG}-note-sheet-${p.geo.w}-${p.theme}-${state}.png`) });
  } finally {
    if (state === 'keyboard') await p.evaluate(() => document.getElementById('note-keyboard-stand-in')?.remove());
  }
}

function rgbLuminance(color) {
  const channels = color.match(/^rgb\((\d+), (\d+), (\d+)\)$/)?.slice(1);
  if (!channels) return null;
  const [red, green, blue] = channels.map(channel => {
    const value = Number(channel) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

async function noteSheetLayout(p, dialog, field, state) {
  const m = await measure(p, field);
  const layout = await dialog.evaluate(el => {
    const rect = node => {
      const { left, top, right, bottom, width, height } = node.getBoundingClientRect();
      return { left, top, right, bottom, width, height };
    };
    const actions = Array.from(el.querySelectorAll('button'), button => {
      const bounds = rect(button), { left, top, width, height } = bounds, style = getComputedStyle(button);
      const text = document.createRange();
      text.selectNodeContents(button);
      const labelBounds = rect(text);
      return { ...bounds, label: button.textContent.trim(), background: style.backgroundColor, color: style.color,
        opacity: Number(style.opacity), bordered: ['Top', 'Right', 'Bottom', 'Left'].some(side => parseFloat(style[`border${side}Width`]) > 0),
        shadow: style.boxShadow,
        enabled: !button.disabled,
        labelFits: text.getClientRects().length === 1 && labelBounds.left >= left && labelBounds.right <= bounds.right,
        hit: document.elementFromPoint(left + width / 2, top + height / 2) === button };
    });
    const top = visualViewport.offsetTop, bottom = top + visualViewport.height;
    const dialog = rect(el), css = getComputedStyle(el);
    const send = document.getElementById('send'), sendBox = rect(send), save = actions.at(-1);
    const saveX = save.left + save.width / 2, saveY = save.top + save.height / 2;
    const safeBottom = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ios-safe-bottom')) || 0;
    return { actions, dialog, gapBelow: Math.round(bottom - dialog.bottom),
      actionGap: Math.round(bottom - Math.max(...actions.map(b => b.bottom))), top, bottom,
      theme: document.documentElement.dataset.theme, dialogBackground: css.backgroundColor, safeBottom,
      coversDisabledSend: send.disabled && saveX >= sendBox.left && saveX <= sendBox.right && saveY >= sendBox.top && saveY <= sendBox.bottom,
      positioning: { top: css.top, bottom: css.bottom, height: css.height, maxHeight: css.maxHeight, transform: css.transform } };
  });
  const labels = p.language === 'zh' ? ['粘贴', '导入文本', '取消', '保存'] : ['Paste', 'Import text', 'Cancel', 'Save'];
  const { actions, top, bottom, gapBelow, actionGap } = layout;
  const sameRow = actions.every((b, i) => !i || (Math.abs(b.top - actions[0].top) < 1 && b.left - actions[i - 1].right >= 7.5));
  const hitTargets = actions.every(b => b.enabled && b.width >= 44 && b.height >= 44 && b.left >= layout.dialog.left && b.right <= layout.dialog.right
    && b.top >= top && b.bottom <= bottom && b.labelFits && b.hit);
  const argent = 'rgb(136, 136, 136)', lotion = 'rgb(250, 249, 245)', noir = 'rgb(44, 43, 38)';
  const solidColors = actions.every((b, i) => b.background === (i === 3 ? lotion : noir) && b.color === (i === 3 ? noir : lotion));
  const copy = actions.map(b => b.label).join('|') === labels.join('|');
  const keyboard = state.startsWith('keyboard') && state !== 'keyboard-dismissed';
  const panelLuminance = rgbLuminance(layout.dialogBackground), noirLuminance = rgbLuminance(noir);
  const fillContrast = panelLuminance === null ? 0 : (Math.max(panelLuminance, noirLuminance) + .05) / (Math.min(panelLuminance, noirLuminance) + .05);
  const solidSecondary = fillContrast >= 3 && actions.slice(0, 3).every(action => action.opacity === 1 && !action.bordered && action.shadow === 'none');
  const viewportPlacement = p.geo.kb
    ? gapBelow <= 1 && actionGap >= layout.safeBottom
    : Math.abs(layout.dialog.top + layout.dialog.bottom - top - bottom) <= 2;
  const ok = layout.theme === p.theme && m.inside && m.focused && !m.cover.length && actions.length === 4 && sameRow && hitTargets && solidColors && copy
    && layout.dialogBackground === argent && solidSecondary
    && actions[0].top >= m.rect[1] + 7.5
    && layout.dialog.top >= top - 0.5 && gapBelow >= 0
    && (keyboard ? gapBelow <= 32 && actionGap <= 56 : viewportPlacement);
  const name = `notebook-add-${p.geo.w}-${p.language}-${p.theme}-${state}`;
  rows.push({ case: name, field: m.id, ok, ...m, ...layout, sameRow, hitTargets, solidColors, copy, solidSecondary, fillContrast, ...(keyboard ? {} : { viewportPlacement }) });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name} · field ${m.rect} in ${m.view}; actions ${actions.map(b => Math.round(b.top)).join('/')}; action gap ${actionGap}px; ${hitTargets ? 'hit targets visible' : 'hit target failed'}; panel gap ${gapBelow}px; Noir contrast ${fillContrast.toFixed(2)}`);
  return ok;
}

async function notebookAdd(p) {
  await p.evaluate(async () => {
    const cards = await (await import('./js/jcards.js')).refresh();
    if (!cards.some(card => card.id === 'notes' && card.official)) throw new Error('Official notebook fixture was not received');
    (await import('./js/five-pages.js')).openTarget('notes');
  });
  const zh = p.language === 'zh', trigger = '.jc-body[data-card=notes] [data-act=add]';
  const dialog = p.locator('.jc-dialog[open]'), title = dialog.locator('input[type=text]'), content = dialog.locator('textarea');
  const save = dialog.getByRole('button', { name: zh ? '保存' : 'Save', exact: true });
  const paste = dialog.getByRole('button', { name: zh ? '粘贴' : 'Paste', exact: true });
  const cancel = dialog.getByRole('button', { name: zh ? '取消' : 'Cancel', exact: true });
  const activate = target => p.geo.kb ? target.tap() : target.click();
  const open = async (selector = trigger) => {
    await activate(p.locator(selector));
    await title.waitFor();
    await content.waitFor();
    await layoutFrames(p, 120);
  };
  await open();
  let healthy = await noteSheetLayout(p, dialog, content, 'no-keyboard');
  await noteShot(p, 'no-keyboard');
  if (p.geo.kb) {
    await kbd(p, true);
    healthy = await noteSheetLayout(p, dialog, content, 'keyboard') && healthy;
    await noteShot(p, 'keyboard');
    await kbd(p, true, 72);
    healthy = await noteSheetLayout(p, dialog, content, 'keyboard-panned') && healthy;
    // Do not let Playwright scroll the card to make the field reachable.
    const { x, y, width, height } = await title.boundingBox();
    if (healthy) await p.touchscreen.tap(x + width / 2, y + height / 2);
    else await title.evaluate(el => el.focus({ preventScroll: true }));
    await layoutFrames(p, 120);
    healthy = await noteSheetLayout(p, dialog, title, 'keyboard-title') && healthy;
    await kbd(p, false);
    healthy = await noteSheetLayout(p, dialog, title, 'keyboard-dismissed') && healthy;
  }
  // The negative control still measures every state and captures comparison images.
  if (!healthy) return;
  await title.fill('WebKit journey');
  const text = 'Typed note';
  await content.fill(text);
  if (p.geo.kb) {
    await kbd(p, true, 72);
    assert.ok(await noteSheetLayout(p, dialog, content, 'keyboard-before-save'));
  }
  // Direct touch coordinates preserve the measured position while the keyboard is up.
  const { x, y, width, height } = await save.boundingBox();
  if (p.geo.kb) await p.touchscreen.tap(x + width / 2, y + height / 2);
  else await p.mouse.click(x + width / 2, y + height / 2);
  await dialog.waitFor({ state: 'detached', timeout: 6000 }).catch(error => {
    throw new Error(`Save at ${x + width / 2},${y + height / 2} left the dialog open with ${notebookWrites.length} fixture writes`, { cause: error });
  });
  assert.deepEqual(notebookWrites.map(({ id, op, title, text }) => ({ id, op, title, text })), [
    { id: 'notes', op: 'add', title: 'WebKit journey', text },
  ]);
  await p.locator('.jc-entry[data-item=journey-note]').waitFor({ timeout: 6000 });
  await kbd(p, false);
  await open('#cardPlus');
  await p.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
    configurable: true, value: { readText: async () => 'Clipboard text' },
  }));
  await activate(paste);
  await p.waitForFunction(() => document.querySelector('.jc-dialog textarea').value === 'Clipboard text');
  const [picker] = await Promise.all([
    p.waitForEvent('filechooser'),
    activate(dialog.getByRole('button', { name: zh ? '导入文本' : 'Import text', exact: true })),
  ]);
  await picker.setFiles({ name: 'memo.txt', mimeType: 'text/plain', buffer: Buffer.from('\n导入样文') });
  await p.waitForFunction(() => document.querySelector('.jc-dialog textarea').value === 'Clipboard text\n导入样文');
  await activate(cancel);
  await dialog.waitFor({ state: 'detached', timeout: 6000 }).catch(error => {
    throw new Error('Cancel left the dialog open', { cause: error });
  });
  assert.equal(notebookWrites.length, 1, 'cancel sends no note mutation');
  assert.equal(fake.st.says.length + fake.st.slashes.length, 0, 'notes send no agent message');
  rows.push({ case: `notebook-add-${p.geo.w}-${p.language}-${p.theme}-journey`, ok: true, field: 'save/cancel' });
}

// ------------------------------------------------------------------------------------------------ the host side of the terminal
function terminalHost({ v2 = true } = {}) {
  const H = { fa: b64u(randomBytes(16)), id: null };
  fake.st.onApp = async (c, m, send) => {
    if (m.t === 'term_open' && !m.sig) {
      await send({ t: 'ctl_res', r: m.r, action: 'term_open', ok: false, why: 'passkey_required', n: hex(16), fa: H.fa, fa_ch: b64u(randomBytes(32)), salt: b64u(randomBytes(32)), epk: epk() });
      return true;
    }
    if (m.t === 'term_open') {
      await send({ t: 'ctl_pending', r: m.r, action: 'term_open', timeout_ms: 180000 });
      if (H.ready) { H.id = hex(8); await send({ t: 'term_ready', r: m.r, id: H.id, expires_at: Math.floor(Date.now() / 1000) + 900, idle_s: 300, sudo: 'local', cwd: '~/work' });
        await send({ t: 'term_out', id: H.id, seq: 1, data: '~/example $ ', prompt: true }); return true; }
      const msg = { t: 'elev', id: hex(16), kind: 'login', n: hex(16), epk: epk(), ttl: 120, tries: 3, term: m.r, fa: H.fa };
      if (v2) {
        const e = { v: 2, request_id: msg.id, channel: fake.channel, revision: 1, session: '1', special: { tool: 'TerminalUnlock' } };
        Object.assign(msg, { approval_v: 2, envelope: e, envelope_sha256: await approvalEnvelopeDigest(e), revision: 1, review_state: 'pending' });
      }
      await send(msg);
      return true;
    }
    return ['term_in', 'term_prefill', 'term_resize', 'term_line', 'term_close', 'term_keys'].includes(m.t);
  };
  return H;
}
const TERM = (x = {}) => ({ mobile: true, resume: true, prefill: true, kind: 'phone', on: true, os: true, me: false, web_ok: true, sudo: null, minutes: 15, idle_s: 300, open: false, modes: ['line', 'pty'], ...x });
async function openTerminal(p, block) {
  await p.evaluate(() => import('./js/settings.js').then((m) => { m.open(); }));
  await p.waitForFunction(() => !document.getElementById('settings').hidden);
  fake.st.terminal = block; await fake.send(fake.prefsMsg()); await p.waitForTimeout(300);
  await p.evaluate(() => import('./js/settings.js').then((m) => { m.render(); }));
  const via = await p.evaluate(() => { const e = document.getElementById('set-term-enable'); return e && e.getClientRects().length ? 'set-term-enable' : 'set-term-open'; });
  await press(p, '#' + via);
}
// Playwright's Linux WebKit can crash on pointer input. Invoke the real DOM control handler here.
const press = async (p, sel) => {
  await waitField(p, sel);
  await p.locator(sel).evaluate(node => node.click());
};
async function waitStage(p, label, predicate) {
  try {
    await p.waitForFunction(predicate, null, { polling: 100, timeout: 25000 });
  } catch (error) {
    throw new Error(label + ': ' + error.message);
  }
}
const waitIntro = p => waitStage(p, 'terminal passkey confirmation ready',
  () => document.getElementById('term-sheetwrap')?.dataset.kind === 'intro' && !document.getElementById('term-sh-yes').disabled);
const waitField = async (p, selector) => p.locator(selector).waitFor({ state: 'visible', timeout: 8000 });
const openSettings = async (p) => { await p.evaluate(() => import('./js/settings.js').then((m) => { m.open(); })); await p.waitForFunction(() => !document.getElementById('settings').hidden); await p.waitForTimeout(300); };

// ------------------------------------------------------------------------------------------------ the cases
const CASES = {
  // the owner's report: 「开启并打开网页终端」 → Face ID → the host's login-password card over the chat (approval_v 2)
  'terminal-password': async (p) => {
    terminalHost(); await turn(p);
    await openTerminal(p, TERM());
    await waitIntro(p);
    await press(p, '#term-sh-yes');
    await p.waitForFunction(() => document.getElementById('term-sheetwrap')?.dataset.kind === 'login' && !document.getElementById('term-sheetwrap').hidden, null, { timeout: 10000 });
    await p.waitForTimeout(600);
    await focused(p, 'terminal-password', '#term-sh-pw');
  },
  // a host before approval_v 2: the same card inside the terminal page
  'terminal-password-panned': async (p) => {
    terminalHost(); await turn(p);
    await openTerminal(p, TERM());
    await waitIntro(p);
    await press(p, '#term-sh-yes');
    await waitField(p, '#term-sh-pw');
    await focused(p, 'terminal-password-panned', '#term-sh-pw', { offset: 48 });
  },
  'terminal-password-page': async (p) => {
    terminalHost({ v2: false }); await turn(p);
    await openTerminal(p, TERM());
    await waitIntro(p);
    await press(p, '#term-sh-yes');
    await p.waitForFunction(() => document.getElementById('term-sheetwrap')?.dataset.kind === 'login' && !document.getElementById('term-sheetwrap').hidden, null, { timeout: 10000 });
    await p.waitForTimeout(600);
    await focused(p, 'terminal-password-page', '#term-sh-pw');
  },
  'terminal-pty': async (p) => {
    terminalHost().ready = true;
    await openTerminal(p, TERM({ me: true }));
    await waitIntro(p);
    await press(p, '#term-sh-yes');
    const helper = p.locator('#term .xterm-helper-textarea');
    await helper.waitFor({ state: 'attached', timeout: 8000 });
    await helper.evaluate(node => node.focus({ preventScroll: true }));
    await kbd(p, true);
    const geometry = await helper.evaluate(node => {
      const rect = node.closest('.term-xt').getBoundingClientRect();
      return { id: 'terminal-canvas', rect: [rect.top, rect.bottom], view: [0, visualViewport.height],
        inside: rect.top >= 0 && rect.bottom <= visualViewport.height, focused: document.activeElement === node, cover: [] };
    });
    record('terminal-pty', geometry);
  },
  // the admin-password card (sudo) and the key card (secret paste)
  'sudo-card': async (p) => {
    await turn(p, '把 8443 端口在防火墙放行', '好的，这一步要用管理员密码运行，卡片在下面。'.repeat(8));
    await fake.send({ t: 'elev', id: hex(16), kind: 'sudo', n: hex(16), epk: epk(), ttl: 120, tries: 3, cmd: 'ufw allow 8443/tcp', why: '放行 8443 端口', effect: '改防火墙规则',
      cls: 'irreversible', why_scope: '你让我放行端口，但改系统防火墙要管理员密码，Agent J 不保存它。' });
    await p.waitForFunction(() => document.getElementById('elev') && !document.getElementById('elev').hidden); await p.waitForTimeout(400);
    await sweep(p, 'sudo-card', '#elev', { shotFirst: true });
  },
  'secret-card': async (p) => {
    await turn(p);
    await fake.send({ t: 'elev', id: hex(16), kind: 'secret', n: hex(16), epk: epk(), ttl: 120, tries: 3, name: 'EXAMPLE_API_KEY', purpose: '天气查询服务', dest: 'EXAMPLE_API_KEY', verify: '' });
    await p.waitForFunction(() => document.getElementById('elev') && !document.getElementById('elev').hidden); await p.waitForTimeout(400);
    await sweep(p, 'secret-card', '#elev', { shotFirst: true });
  },
  // a question card with the free-text answer (the approval / question sheet)
  'question-free-text': async (p) => {
    await turn(p);
    await fake.send({ t: 'question', id: hex(16), ttl: 180, text_max: 2000, qs: [{ q: '报告用哪种格式？如有别的要求请写下来。', h: '格式', m: false,
      o: [{ l: 'PDF', d: '适合打印和转发，版式固定' }, { l: 'Markdown', d: '适合继续编辑' }, { l: '网页', d: '手机上直接看' }, { l: '幻灯片', d: '开会时投屏' }] }] });
    await p.waitForFunction(() => document.body.dataset.sheet === '1' && !!document.querySelector('#sheet .qfree textarea')); await p.waitForTimeout(400);
    await sweep(p, 'question-free-text', '#sheet', { shotFirst: true });
  },
  // forms: the short form inside the reply card, and the full-screen long form
  'form-reply': async (p) => {
    const sp = SPECS['event-signup.zh'], t = await turn(p, '周五的活动我报名', '帮你填报名表：');
    await fake.updateTurn(t.id, { form: { id: 'f_0000000000000001', mode: sp.mode, title: sp.title, n: sp.n, state: 'open', exp: Date.now() + 864e5, secs: sp.sections.length, spec: sp } });
    await p.waitForFunction(() => !!document.querySelector('#fstrip .fopt')); await p.waitForTimeout(300);
    // show_if questions: pick the options that reveal the number / text fields
    await p.evaluate(() => { for (const q of document.querySelectorAll('#fstrip .fq')) q.querySelector('.fopt')?.click(); });
    await p.waitForTimeout(300);
    await sweep(p, 'form-reply', '#fstrip', { min: 1 });
  },
  'form-deep': async (p) => {
    const sp = SPECS['bug-report.zh'], id = 'f_0000000000000002';
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'form_get') { await send({ t: 'form_res', r: m.r, ok: true, id: m.id, state: 'open', spec: sp, exp: Date.now() + 864e5 }); return true; }
      if (m.t === 'form_draft') { await send({ t: 'form_draft_res', id: m.id, ok: true, at: Date.now() }); return true; }
      return false;
    };
    const t = await turn(p, '登录页有个问题', '把问题说清楚，我来修：');
    await fake.updateTurn(t.id, { form: { id, mode: sp.mode, title: sp.title, n: sp.n, state: 'open', exp: Date.now() + 864e5, secs: sp.sections.length } });
    await p.waitForFunction(() => !!document.querySelector('#fstrip .fdeepin .fgo'));
    await p.evaluate(() => document.querySelector('#fstrip .fdeepin .fgo').click());
    await p.waitForFunction(() => !document.getElementById('fdeep').hidden && !!document.querySelector('#fdeep .fq')); await p.waitForTimeout(300);
    let n = await sweep(p, 'form-deep', '#fdeep', { min: 0, shotFirst: true });
    for (let k = 0; k < sp.sections.length - 1 && n < 3; k++) {   // the text fields are in the later sections
      await p.evaluate(() => { for (const q of document.querySelectorAll('#fdeep .fq')) { if (!q.querySelector('.fopt.on')) q.querySelector('.fopt')?.click(); } for (const i of document.querySelectorAll('#fdeep textarea,#fdeep input.fin')) { if (!i.value) { i.value = '示例'; i.dispatchEvent(new Event('input', { bubbles: true })); } } document.querySelector('#fdeep .fdnext')?.click(); });
      await p.waitForTimeout(300);
      n += await sweep(p, 'form-deep', '#fdeep', { min: 0 });
    }
    assert.ok(n >= 1, 'form-deep: at least one text field measured');
  },
  // the contact card the Agent J host raises (its fields)
  'contact-card': async (p) => {
    await turn(p);
    await fake.send({ t: 'ct_card', id: hex(16), n: hex(8), mode: 'owner', contact: { emails: [{ v: 'owner@example.com', label: '' }] }, base: null, why: '', ttl: 600 });
    await p.waitForFunction(() => !!document.getElementById('ctCard') && !document.getElementById('ctCard').hidden); await p.waitForTimeout(400);
    await sweep(p, 'contact-card', '#ctCard', { shotFirst: true });
  },
  // owner-opened dialogs
  'rename-remote': async (p) => {
    await turn(p);
    await p.evaluate(() => import('./js/dev-rename.js').then((m) => (m.open || m.openRename || m.show)?.()));
    await p.waitForFunction(() => !document.getElementById('renameDlg').hidden, null, { timeout: 5000 }); await p.waitForTimeout(300);
    await focused(p, 'rename-remote', '#renameInput');
  },
  'agent-name': async (p) => {
    await turn(p);
    await p.evaluate(() => import('./js/agentname.js').then((m) => { m.open(); }));
    await p.waitForFunction(() => !document.getElementById('nameDlg').hidden); await p.waitForTimeout(300);
    await focused(p, 'agent-name', '#nameInput');
  },
  'chat-search': async (p) => {
    await turn(p);
    await p.evaluate(() => import('./js/search.js').then((m) => m.open()));
    await p.waitForFunction(() => !document.getElementById('srch').hidden); await p.waitForTimeout(300);
    await focused(p, 'chat-search', '#srchInput');
  },
  'command-search': async (p) => {
    await turn(p);
    await p.evaluate(() => import('./js/hotkey.js').then((m) => { m.openHot(); }));
    await p.waitForFunction(() => document.getElementById('hotQ')?.getClientRects().length > 0, null, { timeout: 5000 }); await p.waitForTimeout(300);
    await focused(p, 'command-search', '#hotQ', { focus: true });
  },
  'full-editor': async (p) => {
    await turn(p);
    await p.evaluate(() => document.getElementById('tFull').click());
    await p.waitForFunction(() => document.getElementById('fsText')?.getClientRects().length > 0); await p.waitForTimeout(300);
    await focused(p, 'full-editor', '#fsText', { focus: true });
  },
  'editor-search': async (p) => {
    await p.locator('#tFull').evaluate(button => button.click());
    await p.locator('#fsSlash').evaluate(button => button.click());
    await waitField(p, '#fsCmdQ');
    await focused(p, 'editor-search', '#fsCmdQ', { focus: true });
  },
  'text-editor': async (p) => {
    await p.evaluate(() => import('./js/fsedit.js').then(m => m.openText({ title: '示例编辑', value: '示例内容' })));
    await waitField(p, '#fsText');
    await focused(p, 'text-editor', '#fsText', { focus: true });
  },
  'assist-terminal': async (p) => {
    const gid = hex(16);
    const data = { kind: 'login', id: hex(16), n: hex(16), assist_gid: gid, term: gid.slice(0, 16), epk: epk(), fa: b64u(randomBytes(16)), ttl: 120 };
    await p.evaluate(async ({ gid, data }) => {
      const m = await import('./js/assist-terminal.js');
      m.onState({ scope: 'terminal', active: true, id: gid, until: Date.now() + 600000 });
      await m.onElev(data);
    }, { gid, data });
    await focused(p, 'assist-terminal', '#ast-login-password');
  },
  'reply-relogin-code': async (p) => {
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'auth_rescue') return false;
      await send({ t: 'auth_rescue', r: m.r, view: { agent: 'claude', step: 'url', url: 'https://example.invalid/authorize', paste: true } });
      return true;
    };
    await p.evaluate(() => import('./js/loginsheet.js').then(m => m.open('claude')));
    await waitField(p, '#lgs-code');
    await focused(p, 'reply-relogin-code', '#lgs-code', { focus: true });
  },
  'settings-rescue-code': async (p) => {
    const auth = { kind: 'claude', url: 'https://claude.com/cai/oauth/authorize', code: '', round: 2, rounds: 3,
      expires_at: Math.floor(Date.now() / 1000) + 600 };
    const view = { harnesses: [], flow: { ...auth, stage: 'url', route: 'setup_token' }, dot: true };
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'auth_get') return false;
      await send({ t: 'auth_res', r: m.r, ok: true, view });
      return true;
    };
    await p.evaluate(() => import('./js/settings.js').then(m => m.openAuth()));
    await fake.send({ t: 'auth_rescue', view });
    await fake.send({ t: 'elev', id: hex(16), n: hex(16), kind: 'secret', name: 'CLAUDE_AUTH_CODE',
      purpose: '粘贴示例授权码', dest: '本机', verify: 'live', ttl: 600, epk: epk(), auth });
    await waitField(p, '#elev-input');
    await focused(p, 'settings-rescue-code', '#elev-input', { focus: true });
  },
  'mail-password': async (p) => {
    await p.evaluate(() => import('./js/settings.js').then(m => m.openMail()));
    await fake.send({ t: 'elev', id: hex(16), n: hex(16), kind: 'secret', name: 'AGENTJ_MAIL_PASSWORD', ttl: 600,
      purpose: '邮箱应用专用密码\nmail@example.invalid\n用本地测试邮箱', dest: '本机', epk: epk() });
    await waitField(p, '#elev-input');
    await focused(p, 'mail-password', '#elev-input', { focus: true });
  },
  'help-card-answer': async (p) => {
    // One synthetic pixel, no captured screen.
    await fake.send({ t: 'help_card', id: hex(16), n: hex(16), kind: 'screen', reply: 'text', ttl: 600, mime: 'image/png',
      img: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQImWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==' });
    await waitField(p, '#hlp-input');
    await focused(p, 'help-card-answer', '#hlp-input', { focus: true });
  },
  'wechat-code': async (p) => {
    const img = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQImWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
    await p.evaluate(async img => {
      const m = await import('./js/wechat.js');
      m.show();
      m.route({ t: 'wechat_state', bound: false, qr: { img, status: 'need_verifycode' } });
    }, img);
    await waitField(p, '#wc-code');
    await focused(p, 'wechat-code', '#wc-code', { focus: true });
  },
  'passkey-pairing-password': async (p) => {
    const fixture = { id: hex(16), code: '123456', needs_setup: true, bootstrap: true, fa_ch: 'YQ', name: '示例遥控器' };
    await p.evaluate(data => import('./js/pkpeer.js').then(m => m.onAsk(data)), fixture);
    await waitField(p, '#pkp input[type=password]');
    await focused(p, 'passkey-pairing-password', '#pkp input[type=password]', { focus: true });
  },
  'mail-servers': async (p) => {
    await p.evaluate(async () => {
      const m = await import('./js/mail-servers.js');
      void m.mailServers({});
    });
    await waitField(p, '#mail-dialog-imap-host');
    await sweep(p, 'mail-servers', '#mail-servers', { min: 4 });
  },
  'keys-search': async (p) => {
    await openSettings(p);
    await press(p, '#keysBtn');
    await press(p, '#keysFindBtn');
    await focused(p, 'keys-search', '#keysFindInput');
  },
  'contact-forward': async (p) => {
    const turnData = await turn(p);
    await p.evaluate(n => import('./js/contacts.js').then(m => m.openForward({ turn: n, whole: true })), turnData.id);
    await press(p, '#ctFwd-other');
    await press(p, '#ctFwd-add summary');
    await sweep(p, 'contact-forward', '#ctFwd', { min: 3 });
    await p.locator('#ctFwd-q').evaluate(node => node.focus({ preventScroll: true }));
    await kbd(p, true);
    const next = await p.locator('#ctFwd-nick').elementHandle();
    await next.evaluate(node => node.focus({ preventScroll: true }));
    await layoutFrames(p);
    record('focus-while-keyboard-open', await measure(p, next));
  },
  'longtask-setup': async (p) => {
    const id = hex(16);
    await fake.send({ t: 'lt_card', id, ttl: 600, brief: { goal: '示例任务' }, asks: { topic: { type: 'text' } },
      card: { card_id: id, nonce: hex(16), optional: [], required: [{ id: 'topic', why: '主题', action_kind: 'choose' }] } });
    await waitField(p, '#lt textarea');
    await sweep(p, 'longtask-setup', '#lt', { min: 1 });
  },
  'composer': async (p) => {
    await turn(p);
    await focused(p, 'composer', '#input', { focus: true });
    await p.locator('#input').fill('/help ');
    await waitField(p, '#workflow-compose');
    await focused(p, 'composer-with-command-chip', '#workflow-compose', { focus: true });
  },
  'settings-search': async (p) => {
    await openSettings(p);
    await p.evaluate(() => import('./js/settings.js').then(m => m.toggleSearch()));
    await focused(p, 'settings-search', '#set-find-input');
  },
  'key-manager-name': async (p) => {
    keysHandler(fake.st, { fa: 'cDM3NC1maXh0dXJl' });
    await openSettings(p);
    await press(p, '#open-models');
    await press(p, '#keys-add');
    await focused(p, 'key-manager-name', '#keys-add-name');
  },
  'card-colors': async (p) => {
    await turn(p);
    await p.evaluate(async () => {
      const { cardColor } = await import('./js/jcard-color.js');
      const card = document.getElementById('rm');
      cardColor(card, 'example');
      const rect = card.getBoundingClientRect();
      card.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: rect.right - 26, clientY: rect.top + 26 }));
    });
    await waitField(p, '.jc-color-fields input[aria-label=HEX]');
    await sweep(p, 'card-colors', '.jc-color-dialog', { min: 5 });
  },
  'settings-remotes': async (p) => {
    fake.remotes({ limit: 10, items: [{ id: 'ME', name: '示例遥控器', me: true },
      { id: 'fixture1update01', name: '示例设备', online: false }], link: 'https://example.invalid/' });
    await p.evaluate(() => import('./js/settings.js').then(m => m.open('remotes')));
    await press(p, '#rm-list [data-id=fixture1update01] [data-act=rename]');
    await focused(p, 'settings-remote-rename', '.rmrename input');
    await press(p, '.rmrename button[type=button]');
    await press(p, '#rm-add');
    await waitField(p, '#rm-addbox');
    await fake.remotePending('fixture2update01', '示例设备二');
    await focused(p, 'settings-remote-pairing-code', '#rm-code');
  },
  'settings-backup-account': async (p) => {
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'acct_get') return false;
      await send({ t: 'acct_res', r: m.r, ok: true, view: { claude: { items: [] }, codex: { items: [] } } });
      return true;
    };
    await openSettings(p);
    await press(p, '#set-accts-nav');
    await press(p, '#acct-list [data-kind=claude] .acctadd');
    await focused(p, 'settings-backup-account', '#acct-name-claude');
  },
  'settings-file-path': async (p) => {
    await openSettings(p);
    await press(p, '#set-local-files-nav');
    await focused(p, 'settings-file-path', '#local-files-path', { focus: true });
  },
  'friend-report-reply': async (p) => {
    await fake.addTurn({ k: 'friend_report', name: '示例好友' }, '请给一个日期。', 'done',
      { friend_report: { ids: [hex(32)] } });
    await press(p, '#cmdx button[type=button].p505-report-action');
    await focused(p, 'friend-report-reply', '.p498-reply textarea');
  },
  'notebook-editor': async (p) => {
    await p.evaluate(async () => {
      const cards = await (await import('./js/jcards.js')).refresh();
      if (!cards.some(card => card.id === 'notes' && card.official)) throw new Error('Official notebook fixture was not received');
      (await import('./js/five-pages.js')).openTarget('notes');
    });
    await press(p, '[data-note-op=edit]');
    await waitField(p, '.jc-dialog[open][data-note-fields="1"] input[type=text]');
    await waitField(p, '.jc-dialog[open][data-note-fields="1"] textarea');
    await sweep(p, 'notebook-editor', '.jc-dialog[open]', { min: 2 });
  },
  'notebook-add': notebookAdd,
  'settings-owner-hosts': async (p) => {
    await openSettings(p);
    await p.evaluate(async () => {
      const m = await import('./js/owner-hosts.js');
      m.capability({ owner_hosts: 1 });
      m.onState({ rows: [], candidates: [], rev: 'example' });
    });
    await press(p, '#owner-hosts-add');
    await sweep(p, 'settings-owner-hosts', '#owner-hosts-form', { min: 8 });
  },
  'settings-newsletter': async (p) => {
    fake.st.onApp = async (c, m, send) => {
      if (!['news_get', 'news_email'].includes(m.t)) return false;
      await send({ t: 'news_res', r: m.r, ok: true, on: false, need_email: true });
      return true;
    };
    await openSettings(p);
    await waitField(p, '#set-news-email');
    await focused(p, 'newsletter-email', '#set-news-email', { focus: true });
    await p.locator('#set-news-email').fill('sample@example.invalid');
    await press(p, '#set-news-send');
    await waitField(p, '#set-news-code');
    await focused(p, 'newsletter-code', '#set-news-code');
  },
  'settings-hooks': async (p) => {
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'hook_list') return false;
      await send({ t: 'hook_list_res', r: m.r, ok: true, up: true, hooks: [], recent: [], connectors: [] });
      return true;
    };
    await openSettings(p);
    await press(p, '#set-hooks-nav');
    await waitField(p, '#mcp474-name');
    await focused(p, 'settings-mcp-name', '#mcp474-name', { focus: true });
    await press(p, '#mcp474-origins + button');
    await focused(p, 'settings-mcp-origins-editor', '#fsText', { focus: true });
  },
  'page-find': async (p) => {
    await p.evaluate(async () => {
      const m = await import('./js/pagefind.js');
      m.openIn(document.getElementById('friends-view'));
    });
    await focused(p, 'page-find', '#pfind-friends-view-input');
  },
  'settings-mail': async (p) => {
    await p.evaluate(() => import('./js/settings.js').then(m => m.openMail()));
    await p.waitForFunction(() => document.querySelector('#mail-preset option[value=custom]'));
    await p.locator('#mail-preset').selectOption('custom');
    await press(p, '#mail-allow-card > summary');
    await sweep(p, 'settings-mail', '#set-mail', { min: 7 });
  },
  'settings-main': async (p) => {
    const prefs = fake.prefsMsg({
      ...fake.st.prefs, heartbeat: { enabled: true, interval_minutes: 53, daily_at: '08:00' },
      voice: { asr: { mode: 'custom', base_url: 'https://example.invalid/v1', model: 'example' } },
    });
    prefs.host.settable.push('agent.main_thread_guard', 'ui.remote_servers');
    prefs.value.ui = { remote_servers: [{ host: 'example.invalid', name: '示例显示名' }] };
    await fake.send(prefs);
    await openSettings(p);
    if (await p.locator('#set-asr').evaluate(node => node.tagName === 'DETAILS')) await press(p, '#set-asr > summary');
    await press(p, '#set-remote-names > summary');
    await sweep(p, 'settings-main', '#set-main', { min: 6 });
  },
  'contacts-edit': async (p) => {
    await openSettings(p);
    await press(p, '#set-contacts-nav');
    await waitField(p, '#ctm-q');
    await focused(p, 'contacts-search', '#ctm-q', { focus: true });
    await press(p, '#ctm-add');
    await waitField(p, '#ctm-e-name');
    await sweep(p, 'contacts-edit', '#ctmEdit', { min: 4, shotFirst: true });
  },
  // the friends page: add a friend, my card
  'friend-request-context': async (p) => {
    await fake.ask({ id: hex(16), tool: 'friend_request', summary: '示例好友请求', ttl: 600, cat: ['friend'],
      fr: { id: 'AJ-PH8E-AJT4-26GJ-ACQ9', name: '示例好友', note: '示例备注', groups: [{ id: 'default', name: '默认' }] } });
    await press(p, '#frAskCtxBox > summary');
    await focused(p, 'friend-request-context', '#frAskCtx', { focus: true });
  },
  'friends': async (p) => {
    await p.waitForFunction(() => document.body.dataset.view === 'friends', null, { timeout: 8000 });
    await p.evaluate(() => import('./js/friends.js').then((m) => m.open('add')));
    await p.waitForFunction(() => !!document.getElementById('fr-add-id')); await p.waitForTimeout(300);
    await sweep(p, 'friends-add', '#friends-view', { min: 2, shotFirst: true });
    await p.evaluate(() => import('./js/friends.js').then((m) => m.open('card')));
    await p.waitForTimeout(500);
    await sweep(p, 'friends-my-card', '#friends-view', { min: 2 });
    await p.evaluate(() => import('./js/friends.js').then(m => m.open('group')));
    await waitField(p, '#fr-g-name');
    await press(p, '#fr-g-more summary');
    await sweep(p, 'friends-group', '#friends-view', { min: 3 });
    await p.evaluate(id => import('./js/friends.js').then(m => m.open('detail', id)), fake.st.friends.friends[0].id);
    await p.locator('#fr-ctx:not(:disabled)').waitFor({ state: 'visible', timeout: 8000 });
    await sweep(p, 'friends-detail', '#friends-view', { min: 2 });
    await p.evaluate(() => import('./js/friends.js').then(m => m.open('devices')));
    await waitField(p, '#fr-gsay');
    await focused(p, 'friends-devices-message', '#fr-gsay', { focus: true });
  },
  // the unpaired page: the pairing-link box
  'pair-link': async (p) => {
    await p.waitForFunction(() => document.body.dataset.view === 'pair' || !document.getElementById('pair-view').hidden, null, { timeout: 8000 });
    await p.evaluate(() => { for (const d of document.querySelectorAll('#pair-view details')) d.open = true; for (const b of document.querySelectorAll('#pair-view [aria-expanded="false"]')) b.click(); });
    await p.waitForTimeout(400);
    await sweep(p, 'pair-link', '#pair-view', { min: 1, shotFirst: true });
  },
};
const notebookWrites = [];
const notebookHost = async (c, m, send) => {
  if (m.t === 'jcard_list') {
    await send({ t: 'jcard_items', r: m.r, cards: [{ id: 'notes', name: { zh: '示例记事本', en: 'Notebook' }, on: true, official: true }] });
    return true;
  }
  if (m.t === 'jcard_note') {
    notebookWrites.push(m);
    await send({ t: 'jcard_note', r: m.r, id: m.id, ok: true, item: { id: 'journey-note' } });
    return true;
  }
  if (m.t !== 'jcard_get') return false;
  await send({ t: 'jcard_get', r: m.r, id: m.id, ok: true, noteFields: true, template: 'notes', entries: [
    ...notebookWrites.map(({ title, text }) => ({ id: 'journey-note', title, text, date: '2026-11-24', editable: true })),
    { id: 'sample-note', title: '示例', text: '示例正文', date: '2026-10-22', editable: true },
  ], revision: 'example' });
  return true;
};
const SETUP = {
  friends: { path: 'friends', friends: true, caps: ['friendreport1'] }, 'page-find': { path: 'friends', friends: true }, 'pair-link': { pair: false },
  'contacts-edit': { contacts: true }, 'contact-forward': { contacts: true },
  'settings-hooks': { caps: ['hooks1', 'externalmcp1'] }, 'card-colors': { caps: ['jpalette1'] },
  'settings-backup-account': { caps: ['accts1'] }, 'friend-report-reply': { caps: ['friendreport1'] },
  'notebook-editor': { caps: ['jcards1'], onApp: notebookHost },
  'notebook-add': { caps: ['jcards1'], onApp: notebookHost },
};
// screenshots for the review: the reported card and the riskiest neighbours
const SHOT_CASES = ['terminal-password', 'sudo-card', 'question-free-text', 'contact-card'];

async function runCase(name, opts) {
  let p = null;
  try {
    notebookWrites.length = 0;
    const phoneOptions = { ...opts, ...(SETUP[name] || {}) };
    try {
      p = await phone(phoneOptions);
    } catch (error) {
      if (!(error instanceof PairingSetupError)) throw error;
      console.log(`RETRY ${name} · ${error.message}; fresh browser context`);
      p = await phone(phoneOptions);
    }
    p.on('pageerror', (e) => console.log(`  pageerror ${name}: ${String(e).slice(0, 160)}`));
    await CASES[name](p);
  } catch (e) {
    rows.push({ case: name, field: '(setup)', ok: false, error: String(e && e.message || e).slice(0, 400) });
    console.log(`FAIL ${name} · could not be driven: ${String(e && e.message || e).slice(0, 300)}`);
  } finally { await p?.context().close().catch(() => {}); }
}
try {
  assert.ok(ONLY.every(name => Object.hasOwn(CASES, name)), `unknown case in P0374_ONLY: ${ONLY.join(', ')}`);
  const names = Object.keys(CASES).filter((n) => !ONLY.length || ONLY.includes(n));
  if (SHOTS) {
    for (const geo of [PHONE, AIR, NARROW, DESKTOP]) for (const theme of ['light', 'dark']) {
      const selected = geo === NARROW ? ['notebook-add'] : geo === DESKTOP ? ['terminal-password', 'notebook-add']
        : geo === AIR ? [...SHOT_CASES, 'notebook-add'] : SHOT_CASES;
      for (const n of names.filter(x => selected.includes(x))) await runCase(n, { geo, theme });
    }
  }
  else {
    for (const n of names) await runCase(n, { geo: PHONE });
    if (names.includes('terminal-password')) await runCase('terminal-password', { geo: AIR });   // the owner's phone
    if (names.includes('notebook-add')) {
      await runCase('notebook-add', { geo: AIR, theme: 'dark' });
      await runCase('notebook-add', { geo: NARROW, theme: 'dark' });
      await runCase('notebook-add', { geo: NARROW, language: 'en' });
    }
  }
} finally { await B.close(); await fake.stop(); await web.stop(); }

assert.ok(rows.length, 'at least one field was measured');
const bad = rows.filter((r) => !r.ok), red = [...new Set(bad.map((r) => r.case))].sort();
writeFileSync(resolve(OUT, `${TAG}.json`), JSON.stringify({ engine: 'WebKit (Playwright)', physical: false, keyboard: 'visualViewport stand-in (see the file header)', public: process.env.P0374_PUBLIC || 'tree',
  cases: Object.keys(CASES).length, fields: rows.length, failed: bad.length, red, rows }, null, 1) + '\n');
console.log(`P0374 ${TAG}: ${rows.length - bad.length}/${rows.length} checks passed; red cases: ${red.join(', ') || 'none'} → ${OUT}/${TAG}.json`);
if (!SHOTS && !ONLY.length && !process.env.P0374_PUBLIC) {
  const parity = parityRecorder('web/test/p0374-ios-keyboard-webkit.mjs');
  parity.record('Focused phone inputs stay visible when the visual viewport shrinks', bad.length === 0);
  parity.write();
}
if (SHOTS && !ONLY.includes('notebook-add')) assert.equal(rows.filter(row => row.field === '(setup)').length, 0, 'all screenshot surfaces opened');
else assert.equal(bad.length, 0, `${bad.length} mobile layout or interaction checks failed: ${bad.map((r) => r.case + ' ' + r.field).join('; ')}`);
