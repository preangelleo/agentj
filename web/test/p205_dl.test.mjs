// P205 item 6 (Leo 10-13, Mac Chrome: 「这个音频你发过来我下载不了」 on 张教授的思念.mp3): the file card's 下载 no longer depends on the
// card's player URL (revoked when the page re-renders without its file list while the inline card stays — Chromium then
// cancels the download silently), says what happened, and on an iPhone home-screen app uses the share sheet.
// The behaviour in real browsers: p205_dl.mjs (Chromium) and p205_dl_webkit.mjs (WebKit).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const src = read('../public/js/media.js');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const zhSrc = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));
const fn = (name) => { const i = src.indexOf(`function ${name}(`); assert.ok(i >= 0, name); return src.slice(i, src.indexOf('\n}\n', i)); };

test('download: its own octet-stream URL, an anchor on <body>, kept a minute, a toast with the name', () => {
  const d = fn('download');
  assert.match(d, /URL\.createObjectURL\(new Blob\(\[blob\], \{ type: 'application\/octet-stream' \}\)\)/);
  assert.doesNotMatch(d, /urlOf\(/, 'never the card\'s player URL');
  assert.match(d, /document\.body\.appendChild\(a\); a\.click\(\); a\.remove\(\);/);
  assert.match(d, /setTimeout\(\(\) => URL\.revokeObjectURL\(url\), DL_KEEP_MS\)/);
  assert.match(src, /export const DL_KEEP_MS = 60_000;/);
  assert.match(d, /toast\(t\('media\.dlStarted', \{ name: m\.name \}\)/);
});
test('download: an iPhone / iPad home-screen app saves through the share sheet', () => {
  const d = fn('download');
  assert.match(d, /if \(IOS && standalone\(\) && shareable\(m\)\)/);
  assert.match(d, /navigator\.share\(\{ files: \[file\], title: m\.name \}\)/);
});
test('the 下载 icon: breathes while fetching, marks a failure and says so (no silent nothing)', () => {
  const d = fn('dlTap');
  assert.match(d, /b\.dataset\.state = 'sending'/);
  assert.match(d, /b\.dataset\.state = 'failed'/);
  assert.match(d, /'media\.dlFailed'/);
  assert.match(src, /bDl\.addEventListener\('click', \(e\) => \{ e\.stopPropagation\(\); dlTap\(m, c, load, bDl\); \}\);/);
});
test('a card still on the page keeps its object URL; unpair revokes everything', () => {
  const c = fn('clearScope');
  assert.match(c, /if \(!all && x\.c\.isConnected\) live\.push\(x\); else URL\.revokeObjectURL\(x\.u\);/);
  assert.match(src, /c\._sc\.urls\.push\(\{ u: c\._url, c \}\)/);
  assert.match(fn('forgetAll'), /clearShown\(true\)/);
});
test('dictionaries: started / failed / again (zh = src = override, en)', () => {
  for (const k of ['media.dlStarted', 'media.dlFailed', 'media.dlAgain']) {
    assert.ok(zh[k] && en[k], k);
    assert.equal(zhSrc[k], zh[k], k); assert.equal(ov[k], zh[k], k);
  }
  assert.match(zh['media.dlStarted'], /\{name\}/); assert.match(en['media.dlStarted'], /\{name\}/);
});
// 追加 12:2x: a [label](local file) on a finished page that never got a file list (a 「后台任务完成」 page the host did not scan
// before 0.20.2) kept its raw Markdown. Now, after a grace, the label and one line; a list that comes later still fills it.
test('an unsent local link: the label and a note once the page is over (grace, or at once for an old page); both views', () => {
  const u = fn('unsent');
  assert.match(u, /if \(!p \|\| p\.end === 'open'\) return;/);
  assert.match(u, /\.mslot\[data-link="1"\]/);
  assert.match(u, /if \(s\.dataset\.mid \|\| s\.dataset\.skip \|\| s\.dataset\.unsent\) continue;/);
  assert.match(u, /mk\('span', 'mskip', t\('media\.unsent'\)\)/);
  assert.match(src, /export const UNSENT_MS = 6000;/);
  assert.match(fn('renderPage'), /^function renderPage\(words, p\) \{\n  unsent\(words, p\);/);
  assert.match(fn('renderReader'), /^function renderReader\(words, p\) \{\n  unsent\(words, p\);/);
  const k = 'media.unsent';
  assert.ok(zh[k] && en[k]); assert.equal(zhSrc[k], zh[k]); assert.equal(ov[k], zh[k]);
});
