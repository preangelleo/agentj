// Real mobile WebKit, loopback paired host and refused resume handshakes.
// AJ_TEST_WEB_PUBLIC may point to the staged/released web bundle.
import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {existsSync} from 'node:fs';
import {mkdir, writeFile} from 'node:fs/promises';
import {userInfo} from 'node:os';
import {join} from 'node:path';
import {startFakeHost} from './fakehost.mjs';
import {startWebServer} from './serve.mjs';

const out = process.env.AJ_RECONNECT_SHOTS || join(process.env.AJ_SHOTS || 'reports/qa', 'p0341-reconnect');
// The gate supplies an isolated HOME, but uses the same installed WebKit as the release runner.
const installedBrowsers = join(userInfo().homedir, '.cache/ms-playwright');
if (!process.env.PLAYWRIGHT_BROWSERS_PATH && existsSync(installedBrowsers)) {
  process.env.PLAYWRIGHT_BROWSERS_PATH = installedBrowsers;
}
const {webkit, devices} = createRequire(import.meta.url)(
  process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');

function measure(stress) {
  const row = document.querySelector('#connRow');
  const card = row.closest('.sq');
  const buttons = [...row.querySelectorAll('button')].filter(b => b.getClientRects().length);
  const originals = buttons.map(b => b.textContent);
  try {
    if (stress) {
      for (const b of buttons) b.textContent = b.textContent + ' ' + 'W'.repeat(105);
    }
    const rect = node => node.getBoundingClientRect().toJSON();
    return {
      width: innerWidth, lang: document.documentElement.lang,
      dark: matchMedia('(prefers-color-scheme: dark)').matches,
      row: rect(row), card: rect(card), background: getComputedStyle(card).backgroundColor,
      buttons: buttons.map(b => {
        const style = getComputedStyle(b), range = document.createRange();
        range.selectNodeContents(b);
        const lines = [...range.getClientRects()].map(r => ({...r.toJSON(), text:''}));
        // Associate each displayed character with the actual WebKit line rectangle.
        const character = document.createRange();
        for (let i = 0; i < b.firstChild.length; i++) {
          character.setStart(b.firstChild, i);
          character.setEnd(b.firstChild, i + 1);
          const box = character.getBoundingClientRect();
          const line = lines.find(line => Math.abs(line.top - box.top) < 0.5);
          if (line) line.text += character.toString();
        }
        const biometricNames = [];
        for (const match of b.textContent.matchAll(/(?:Face|Touch)\sID/g)) {
          const name = document.createRange();
          name.setStart(b.firstChild, match.index);
          name.setEnd(b.firstChild, match.index + match[0].length);
          biometricNames.push({text:match[0], lines:[...name.getClientRects()].map(r => r.toJSON())});
        }
        return {
          id: b.id, text: b.textContent, biometricNames, rect: rect(b), foreground: style.color, background: style.backgroundColor,
          paddingLeft: style.paddingLeft, paddingRight: style.paddingRight,
          borders: ['Top','Right','Bottom','Left'].map(side => style['border'+side+'Width']),
          lines,
        };
      }),
    };
  } finally {
    if (stress) buttons.forEach((b,i) => { b.textContent = originals[i]; });
  }
}

function verify(data, width, lang, theme, stress) {
  const {card, row, buttons} = data;
  assert.equal(data.width, width);
  assert.ok(data.lang.startsWith(lang));
  assert.equal(data.dark, theme === 'dark');
  assert.equal(data.background, 'rgb(44, 43, 38)');
  assert.equal(buttons.length, 2);
  const primary = buttons.find(b => b.id === 'connRetry'), later = buttons.find(b => b.id === 'connLater');
  const expected = lang === 'zh' ? '用 Face ID/Touch ID 继续' : 'Continue with Face ID/Touch ID';
  const normalize = text => text.replaceAll('\u00a0', ' ');
  assert.equal(normalize(primary.text), stress ? expected + ' ' + 'W'.repeat(105) : expected);
  if (!stress && lang === 'en' && width === 360) {
    assert.ok(primary.lines.length <= 2, '360 English label exceeds two lines');
    if (primary.lines.length === 2) {
      const secondLine = normalize(primary.lines[1].text).trim();
      assert.ok(secondLine && secondLine !== 'ID', 'ID may not stand alone on the second line');
    }
  } else if (!stress) {
    assert.equal(primary.lines.length, 1, width+' '+lang+' '+theme+' primary label wraps');
  }
  assert.deepEqual(primary.biometricNames.map(name => normalize(name.text)), ['Face ID','Touch ID']);
  for (const name of primary.biometricNames) {
    assert.equal(name.lines.length, 1, name.text+' must fit on a single line');
  }
  assert.match(later.text, lang === 'zh' ? /^稍后/ : /^Later/);
  assert.ok(primary.rect.bottom <= later.rect.top, 'primary above Later');
  assert.ok(Math.abs(primary.rect.left-row.left) <= 1 && Math.abs(primary.rect.right-row.right) <= 1, 'full-width primary');
  assert.ok(card.left >= -1 && card.right <= width+1, 'card fits phone');
  for (const b of buttons) {
    assert.equal(b.background, 'rgb(250, 249, 245)');
    assert.equal(b.foreground, 'rgb(44, 43, 38)');
    assert.deepEqual(b.borders, ['0px','0px','0px','0px']);
    const r = b.rect;
    assert.ok(r.left >= card.left-1 && r.right <= card.right+1 && r.height >= 44, b.id+' fits card');
    assert.ok(b.lines.length >= (stress ? 2 : 1), 'text is visible and long words wrap');
    for (const line of b.lines) {
      assert.ok(line.left >= r.left-1 && line.right <= r.right+1 && line.top >= r.top-1 && line.bottom <= r.bottom+1,
        b.id+' contains each line of text');
    }
  }
}

test('reconnect passkey actions fit all mobile widths, languages and themes', async t => {
  await mkdir(out, {recursive:true});
  const web = await startWebServer({publicDir:process.env.AJ_TEST_WEB_PUBLIC});
  t.after(() => web.stop());
  const host = await startFakeHost();
  t.after(() => host.stop());
  const browser = await webkit.launch();
  t.after(() => browser.close());
  const receipt = {engine:'webkit', version:browser.version(), cases:[]};
  const failures = [];
  for (const lang of ['zh', 'en']) {
    host.reset();
    host.st.prefs = {appearance:{language:lang}};
    const context = await browser.newContext({...devices['iPhone 13'], viewport:{width:390,height:844},
      deviceScaleFactor:1, locale:lang, serviceWorkers:'block'});
    try {
      await context.addInitScript(() => {
        // WPE lacks a system credential store. Only report availability; no assertion is requested.
        const P = window.PublicKeyCredential ?? (window.PublicKeyCredential = class {});
        P.isUserVerifyingPlatformAuthenticatorAvailable = async () => true;
        if (!navigator.credentials) Object.defineProperty(navigator,'credentials',{value:{
          create(){throw Error('unexpected enrollment');}, get(){throw Error('unexpected authentication');}
        }});
        window.Audio = undefined;
        for (const method of ['play','pause','load']) HTMLMediaElement.prototype[method] =
          method === 'play' ? () => Promise.resolve() : () => {};
      });
      const pairing = await context.newPage();
      await pairing.goto(host.newPairing(web.url+'?lang='+lang));
      await pairing.waitForFunction(() => window.__ajState === 'awaiting-approval');
      await host.approve();
      await pairing.waitForFunction(() => window.__ajState === 'ready' && document.body.dataset.sync === 'ready');
      await pairing.locator('#a2hs-ok').tap();
      await pairing.close();
      // A returning iPhone opens the normal URL with a durable pairing. Its very first resume is refused.
      host.refuseNextResumes(100);
      const page = await context.newPage();
      await page.goto(web.url+'?lang='+lang);
      await page.locator('#connRetry[data-i18n="revoked.takeoverPk"]').waitFor({timeout:45000});
      await page.evaluate(() => document.fonts.ready);
      for (const width of [390,360,412]) {
        await page.setViewportSize({width,height:844});
        for (const theme of ['light','dark']) {
          await page.emulateMedia({colorScheme:theme});
          await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
          const name = [width,lang,theme].join('-');
          await page.screenshot({path:join(out,name+'.png')});
          for (const stress of [false,true]) {
            const data = await page.evaluate(measure,stress);
            receipt.cases.push({name,stress,...data});
            try {
              verify(data,width,lang,theme,stress);
            } catch (error) {
              failures.push(name + (stress ? ' stress' : '') + ' — ' + error.message);
            }
          }
        }
      }
      await page.locator('#connLater').tap();
      await page.locator('#connDlg').waitFor({state:'hidden'});
    } finally {
      await context.close();
      await writeFile(join(out,'browser.json'), JSON.stringify(receipt,null,2)+'\n');
    }
  }
  assert.deepEqual(failures, [], 'reconnect screenshot gate');
});
