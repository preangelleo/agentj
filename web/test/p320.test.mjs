// P320: Reload without restarting serve (≡ row, /reload, Alt+Shift+L, the settings page's Reload notice) and the global reply
// key Alt+Shift+A (works while typing: physical code, no 「Å」, the focus stays in the message box).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {KEYMAP, GLOBAL, dispatchFunctional} from '../public/js/interactions.gen.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const relay = read('../public/js/relay.js');
const settings = read('../public/js/settings.js');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));

test('Alt+Shift+A replies to the current page from anywhere, also inside the message box', () => {
  assert.deepEqual(KEYMAP['alt+shift:KeyA'], ['card.reply', 0]);
  assert.ok(GLOBAL.includes('card.reply'));
  assert.equal(KEYMAP.r[0], 'card.reply', 'the single key r stays');
  for (const tagName of ['TEXTAREA', 'INPUT']) {
    let calls = 0, prevented = 0;
    const e = {key: 'Å', code: 'KeyA', altKey: true, shiftKey: true, target: {tagName}, preventDefault() { prevented++; }, stopImmediatePropagation() {}};
    assert.equal(dispatchFunctional(e, {'card.reply': () => calls++}), true);
    assert.equal(calls, 1); assert.equal(prevented, 1, 'Option+Shift+A never types Å');
  }
  // the action quotes the current page and leaves the caret at the end of the field
  assert.match(relay, /"card\.reply": \(\) => startReply\(currentPage\(\)\)/);
  assert.match(relay, /function startReply\(p\)\{[\s\S]{0,200}focusComposer\(\);/);
});

test('Reload: ≡ row only for a host that answers /reload, Alt+Shift+L, the same action', () => {
  assert.deepEqual(KEYMAP['alt+shift:KeyL'], ['app.reload_host', 0]);
  assert.ok(GLOBAL.includes('app.reload_host'));
  assert.match(relay, /if \(hostMenu\?\.reload\) item\("\/reload", t\('p320\.reloadDesc'\), \{run: "reload"\}\)\.id = "reloadEntry";/);
  assert.match(relay, /reload: j\.reload === true/);
  assert.match(relay, /"app\.reload_host": \(\) => \{ reloadHost\(\); \}/);
  assert.match(relay, /export async function reloadHost\(\)\{[\s\S]{0,200}runCmd\("reload"\)/);
});

test('settings: a saved key that needs a reload offers 「重新加载」 on the Noir confirm card', () => {
  assert.match(settings, /if \(m\.reload\) offerReload\(\);/);
  assert.match(settings, /confirmSheet\(t\('p320\.needTitle'\), t\('p320\.needText'\), t\('p320\.reloadBtn'\), false, t\('p320\.later'\)\)/);
  assert.match(settings, /sendApp\(\{ t: 'slash', cmd: 'reload', arg: '' \}\)/);
});

test('texts exist in both languages', () => {
  for (const k of ['p320.reloadDesc', 'p320.reloadOld', 'p320.needTitle', 'p320.needText', 'p320.reloadBtn', 'p320.later', 'p320.reloading']) {
    assert.ok(zh[k] && en[k], k);
  }
  assert.match(zh.ix?.card?.reply || '', /Alt\+Shift\+A/);
  assert.match(en.ix?.card?.reply || '', /Alt\+Shift\+A/);
});
