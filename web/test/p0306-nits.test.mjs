// 0.30.6: Jarvis's 0.30.5 screenshot review (INCOMING-0306-from-jarvis-ui-nits-0305review) — the three page-side nits.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const HTML = read('../public/index.html'), MAIL = read('../public/js/mail.js'), FR = read('../public/js/friends.js'), SRC = read('../public/js/approval-source572.js');
const ZH = JSON.parse(read('../i18n/web.zh.json')), EN = JSON.parse(read('../i18n/web.en.json'));

test('0306 ①: 校验强度 shows short names (no cut-off on 390 px); the full meaning is the line under it', () => {
  const sel = HTML.slice(HTML.indexOf('<select id="mail-auth"'), HTML.indexOf('</select>', HTML.indexOf('<select id="mail-auth"')));
  assert.deepEqual([...sel.matchAll(/data-i18n="([^"]+)"/g)].map(m => m[1]), ['p0306.mail.authStdShort', 'p0306.mail.authStrictShort', 'p0306.mail.authOffShort']);
  for (const k of ['p0306.mail.authStdShort', 'p0306.mail.authStrictShort', 'p0306.mail.authOffShort']) {
    assert.ok(ZH[k].length <= 8, `${k} fits a narrow select: ${ZH[k]}`);
    assert.ok(EN[k].length <= 24, `${k} (en) fits: ${EN[k]}`);
  }
  assert.match(HTML, /<p class="setnote" id="mail-auth-why"><\/p>/);
  assert.match(MAIL, /const AUTH_WHY = \{ standard: 'mail\.authStd', strict: 'mail\.authStrict', off: 'mail\.authOff' \};/);
  assert.match(MAIL, /el\('mail-auth-off'\)\.hidden = el\('mail-auth'\)\.value !== 'off'; authWhy\(\);/);
  assert.match(MAIL, /onLang\(authWhy\)/);
});

test('0306 ②: a friend card has no 「Agent 说明：Agent 没说明理由」 filler line', () => {
  assert.doesNotMatch(FR, /p572\.noReason|p572\.label|box\.append\(note\(\)\)/);
});

test('0306 ③: a card without a v2 source record says so neutrally; an unverified record still blocks', () => {
  assert.match(SRC, /t\(p\.approval_invalid \? 'p572source\.invalid' : 'p0306\.sourceNone'\)/);
  assert.doesNotMatch(ZH['p0306.sourceNone'], /未提供|签名|风险|警告/);
  assert.match(SRC, /document\.getElementById\('apprAllow'\)\.disabled=true;/, 'invalid / unverified provenance still disables 批准');
});
