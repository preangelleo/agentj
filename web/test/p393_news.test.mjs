// P393 #128 「Daily AI News」 e-mail step — static rules (web-unit): the hidden form inside the row (e-mail field + 「发验证码」,
// 6-digit code + 「确认」, 「重新发送」 after 60 s, 「换一个邮箱」), the wire exactly as the contract (news_email {email, lang} /
// news_verify {email, code} → news_res), host text only via textContent, errors stay in the row, both dictionaries,
// no trial wording / internal names / colour names in the copy, palette-only CSS.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const html = read('../public/index.html');
const news = read('../public/js/news.js');
const menuCss = read('../public/menu.css');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const src = JSON.parse(read('../i18n/web.zh.src.json'));
const KEYS = ['need', 'emailLab', 'emailPh', 'send', 'sentTo', 'codeLab', 'verify', 'resend', 'resendIn', 'change', 'verified', 'badEmail',
  'invalidCode', 'accountEmail', 'mailFailed', 'rateLimited', 'noAnswer'].map((k) => 'p393news.' + k);

test('markup: one hidden form inside #set-news-row, existing input / button looks, code field = 6 digits', () => {
  const row = html.slice(html.indexOf('id="set-news-row"'), html.indexOf('id="set-news-links"'));
  const form = row.slice(row.indexOf('<form'), row.indexOf('</form>'));
  assert.match(form, /^<form class="newsmail" id="set-news-mail" novalidate hidden>/);
  assert.ok(row.indexOf('id="set-news-to"') < row.indexOf('<form') && row.indexOf('</form>') < row.indexOf('id="set-news-msg"'), 'between 「发到」 and the message line');
  assert.match(form, /<input id="set-news-email" class="aj-input" type="email" inputmode="email"[^>]*maxlength="254"/);
  assert.match(form, /<input id="set-news-code" class="aj-input" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="\[0-9\]\{6\}"/);
  assert.match(form, /<button type="submit" id="set-news-send" class="aj-btn aj-btn--sm aj-btn--primary" data-i18n="p393news\.send">/);
  assert.match(form, /<button type="submit" id="set-news-verify" class="aj-btn aj-btn--sm aj-btn--primary" data-i18n="p393news\.verify" disabled>/);
  assert.match(form, /<button type="button" id="set-news-resend" class="setlink" disabled>/);
  assert.match(form, /<button type="button" id="set-news-change" class="setlink" data-i18n="p393news\.change">/);
  assert.match(form, /id="set-news-step2" hidden/); assert.match(form, /id="set-news-again" hidden/); assert.match(form, /id="set-news-sent" hidden/);
});

test('wire = contract: news_email {email, lang} / news_verify {email, code} → news_res; the problems are whitelisted', () => {
  assert.match(news, /ask1\(\{ t: 'news_email', email, lang: lang\(\) \}, 'news_res', WAIT_MS\)/);
  assert.match(news, /ask1\(\{ t: 'news_verify', email: M\.to, code \}, 'news_res', WAIT_MS\)/);
  for (const p of ['need_email', 'invalid_code', 'bad_email', 'account_email', 'mail_failed']) assert.match(news, new RegExp(`const PROBLEMS = \\[[^\\]]*'${p}'`));
  assert.match(news, /M\.need = m\.need_email === true && !S\.on;/, 'need_email only from the host answer');
  assert.match(news, /sw\.disabled = S\.state !== 'ok' \|\| S\.busy \|\| M\.need;/, 'no address → the switch cannot be used');
  assert.match(news, /if \(!cleanEmail\(email\)\) \{ M\.note = 'bad_email';/, 'a bad-looking address never leaves the phone');
  assert.match(news, /!\/\^\\d\{6\}\$\/\.test\(code\)/, 'exactly six digits before news_verify');
  assert.match(news, /export const RESEND_MS = 60000/, '「重新发送」 after 60 s');
  assert.match(news, /if \(why === 'need_email'\) \{ M\.need = true;/, 'a refused switch-on asks in the row, no card');
  assert.match(news, /sent\.textContent = /, 'the typed address back as text only');
  assert.doesNotMatch(news, /innerHTML|insertAdjacentHTML|outerHTML|fillText/);
  assert.match(news, /S\.problem === 'account_email'\)\)/, 'account_email → the existing account link');
});

test('dictionaries: zh + en twins, same placeholders, plain words (no emoji, no trial words, no internal names, no colour names)', () => {
  for (const k of KEYS) {
    for (const [n, d] of [['zh', zh], ['en', en], ['src', src]]) assert.equal(typeof d[k], 'string', `${n} ${k}`);
    assert.equal([...zh[k].matchAll(/\{(\w+)\}/g)].join(), [...en[k].matchAll(/\{(\w+)\}/g)].join(), k);
    assert.doesNotMatch(zh[k].replace(/Agent J|\{\w+\}/g, ''), /[A-Za-z]{2,}/, `zh ${k}: only the current language`);
    for (const s of [zh[k], en[k]]) {
      assert.doesNotMatch(s, /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u, k);
      assert.doesNotMatch(s, /试用|体验版|trial/i, k);
      assert.doesNotMatch(s, /news_|need_email|account_email|P393|#128|seat_|Noir|Lotion|Sooty|Emerald|Strawberry/i, k);
    }
  }
  assert.equal(zh['p393news.need'], '这台 Agent J 的新闻发到你自己的邮箱。填一个邮箱，我们发验证码确认。');
  assert.equal(zh['p393news.send'], '发验证码'); assert.equal(zh['p393news.verify'], '确认'); assert.equal(zh['p393news.resend'], '重新发送');
  assert.equal(zh['p393news.sentTo'], '验证码已发到 {email}，10 分钟内有效。');
});

test('palette only: the e-mail step CSS names no colour', () => {
  const a = menuCss.indexOf('/* P393 (backlog 128)'), b = menuCss.indexOf('/* P380', a);
  assert.ok(a > 0 && b > a);
  assert.doesNotMatch(menuCss.slice(a, b), /#[0-9a-f]{3,8}\b|rgb\(|hsl\(/i);
});
