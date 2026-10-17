// P287: the 「Mac 重启后请登录」 words are the phone's own offline line, never a message from a computer that is online.
// Service restart / upgrade (back within the threshold) → nothing; an OS restart (offline past it) → the platform's line.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const { hintKey, hostKind, HINT_AFTER_MS } = await import('../public/js/offhint.js');
const dict = (f) => JSON.parse(readFileSync(new URL('../i18n/' + f, import.meta.url), 'utf8'));
const zh = dict('web.zh.json'), en = dict('web.en.json');

test('P287 offhint: a short outage (service restart ~10 s, upgrade ~40 s) says nothing beyond 「Agent J 主机离线」', () => {
  for (const ms of [0, 10_000, 40_000, HINT_AFTER_MS - 1, NaN, undefined])
    assert.equal(hintKey({ plat: 'macos', boot: 'login' }, ms), null, String(ms));
  assert.ok(HINT_AFTER_MS >= 60_000, 'an upgrade restart must fit inside the threshold');
});

test('P287 offhint: an OS restart picks the line by what the Agent J host said while it was up', () => {
  const at = HINT_AFTER_MS;
  assert.equal(hintKey({ plat: 'macos', boot: 'login' }, at), 'offhint.macos');
  assert.equal(hintKey({ plat: 'macos' }, at), 'offhint.macos');
  assert.equal(hintKey({ plat: 'windows', boot: 'login' }, at), 'offhint.windows');
  assert.equal(hintKey({ plat: 'linux', boot: 'login' }, at), 'offhint.linuxLogin');
  assert.equal(hintKey({ plat: 'linux', boot: 'auto' }, at), 'offhint.linuxAuto');
  for (const k of [{ plat: 'linux' }, { plat: 'other' }, {}, null, { plat: 'beos', boot: 'auto' }])
    assert.equal(hintKey(k, at), 'offhint.generic', 'unsure → the general line, never a wrong guess: ' + JSON.stringify(k));
  assert.deepEqual(hostKind({ plat: '<b>', boot: 7 }), { plat: null, boot: null });
});

test('P287 offhint: every line exists in both languages, plain, no system words', () => {
  for (const k of ['macos', 'windows', 'linuxLogin', 'linuxAuto', 'generic']) {
    for (const [name, d] of [['zh', zh], ['en', en]]) {
      const v = d.offhint?.[k];
      assert.ok(typeof v === 'string' && v.length > 10, `${name} offhint.${k}`);
      assert.ok(!/快速切换|Fast User|launchd|LaunchAgent|linger|lgg|\{/.test(v), `${name} offhint.${k}: ${v}`);
    }
  }
  assert.match(zh.offhint.macos, /Mac.*重启.*登录/);
  assert.ok(zh.offhint.macos.length <= 60);
});

test('P287 offhint: wired only into the grey state, host text never involved', () => {
  const app = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
  assert.match(app, /state === 'waiting-host' && statusKey\[0\] === 'st\.hostDown' && view === 'chat'/);
  assert.match(app, /box\.textContent = key \? plain\(t\(key\)\)/);
  const css = readFileSync(new URL('../public/app.css', import.meta.url), 'utf8');
  assert.match(css, /body\[data-conn=on\] \.offhint/);
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.equal(html.split('id="offHint"').length, 2);
});
