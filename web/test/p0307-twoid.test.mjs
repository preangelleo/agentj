// 0.30.7 (preview-frset): Safari + the Home Screen app are two remotes on iOS and push each other out. The side that was
// pushed out (`removed {why:'replaced'}`) says so once, in both languages, inside the revoked view (no sheet, no redirect).
// Shots: test/p0307-twoid.mjs → .jarvis/out/0307-approval-round2/twoid/.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { shouldShow } from '../public/js/twoid.js';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

test('the rule: iOS, replaced by its sibling (moved), not shown before', () => {
  assert.equal(shouldShow({ ios: true, why: 'replaced', moved: true, already: false }), true);
  assert.equal(shouldShow({ ios: true, why: 'replaced', moved: true, already: true }), false, 'one time only');
  assert.equal(shouldShow({ ios: false, why: 'replaced', moved: true, already: false }), false, 'Android / desktop share one storage');
  assert.equal(shouldShow({ ios: true, why: 'revoked', moved: true, already: false }), false, 'a human removed it: not this hint');
  assert.equal(shouldShow({ ios: true, why: 'replaced', moved: false, already: false }), false, 'a full computer evicted it: not this hint');
  assert.equal(shouldShow({ ios: true, why: 'replaced', already: false }), false, 'an older host without `moved`: no hint');
});

test('the host flag travels: removed.moved → session → revoked(why, moved) → stored record', () => {
  const se = read('../public/js/session.js'), app = read('../public/app.js');
  assert.match(se, /s\.removedMoved = s\.removed === 'replaced' && m\.moved === true;/);
  assert.match(se, /H\.onRevoked\(s\.removed, s\.removedMoved === true\)/);
  assert.match(app, /return revoked\(host\.removed, host\.removedMoved === true\);/);
  assert.match(app, /removed: why, removedMoved: moved \}/);
});

test('wired into the revoked view, remembered in localStorage', () => {
  const app = read('../public/app.js'), mod = read('../public/js/twoid.js'), html = read('../public/index.html');
  assert.match(app, /import \* as twoid from '\.\/js\/twoid\.js';/);
  const fn = app.slice(app.indexOf('async function revoked('), app.indexOf('let takeoverWith'));
  assert.match(fn, /twoid\.paint\(\$\('revoked-twoid'\), why, moved\);/);
  assert.match(mod, /localStorage\.setItem\(KEY, '1'\)/);
  assert.match(html, /<div id="revoked-twoid" class="twoid" role="note" hidden[^>]*><p class="twoid__t" data-i18n="p0307\.twoid\.title"/);
  assert.match(html, /data-i18n="p0307\.twoid\.text"/);
  assert.match(read('../public/app.css'), /\.state-view \.twoid\{/);
});

test('strings: zh + en, the recommendation, glossary-safe', () => {
  for (const [f, home] of [['../i18n/web.zh.json', '主屏幕图标'], ['../i18n/web.zh.src.json', '主屏幕图标'], ['../i18n/web.en.json', 'Home Screen icon']]) {
    const d = JSON.parse(read(f));
    for (const k of ['p0307.twoid.title', 'p0307.twoid.text']) assert.ok(d[k], f + ' ' + k);
    assert.ok(d['p0307.twoid.text'].includes('Safari') && d['p0307.twoid.text'].includes(home), f);
    assert.doesNotMatch(d['p0307.twoid.title'] + d['p0307.twoid.text'], /试用|trial|「|」/i, f);
  }
  const zh = JSON.parse(read('../i18n/web.zh.json'))['p0307.twoid.text'];
  assert.doesNotMatch(zh, /[A-Za-z]{4,} [a-z]{3,}/, 'zh carries no English sentence');
});
