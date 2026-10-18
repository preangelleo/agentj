// P374 「Daily AI News」 — static rules (web-unit): the row (one switch, default off, in 账号和帮助, searchable), the wire
// messages exactly as contract §5 (news_get / news_set {on} → news_res), get once per opening, revert + the P302 card on any
// failure, unlinked → the existing account link, the e-mail line only when on (textContent), the sample link https-only in a
// new tab with rel=noopener, an older computer (no news_res) → disabled + hint, both dictionaries (contract copy), palette only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const html = read('../public/index.html');
const news = read('../public/js/news.js');
const settings = read('../public/js/settings.js');
const menuCss = read('../public/menu.css');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const src = JSON.parse(read('../i18n/web.zh.src.json'));
const row = html.slice(html.indexOf('id="set-news-row"'), html.indexOf('</div>', html.indexOf('id="set-news-row"')));

test('the row: one switch, default off, in 账号和帮助 right after 账号和订阅, findable by the settings search', () => {
  assert.ok(row.length > 200);
  const help = html.slice(html.indexOf('id="set-s-help"'), html.indexOf('</section>', html.indexOf('id="set-s-help"')));
  assert.ok(help.includes('id="set-news-row"'), 'lives in 账号和帮助');
  assert.ok(help.indexOf('id="set-account"') < help.indexOf('id="set-news-row"'), 'after the account row');
  assert.equal([...row.matchAll(/role="switch"/g)].length, 1, 'one switch, nothing else to set');
  assert.equal([...row.matchAll(/<(button|select|input)\b/g)].length, 1, 'no other control');
  assert.match(row, /id="set-news" disabled/, 'disabled until the computer answers');
  assert.match(row, /role="switch" aria-checked="false"/, 'default off');
  assert.match(row, /data-paired/, 'only with a paired computer');
  assert.match(html, /<div class="setrow" id="set-news-row"/, 'a .setrow → searchItems indexes it');
  assert.match(row, /data-keywords="[^"]*\bnewsletter\b[^"]*订阅/);
  assert.match(row, /data-i18n="p374\.label"/); assert.match(row, /data-i18n="p374\.help"/);
  assert.match(row, /<a id="set-news-sample" target="_blank" rel="noopener" data-i18n="p374\.sample" hidden>/, 'sample: new tab, noopener, no href until the host gives one');
  assert.match(row, /<a id="set-news-acct" href="https:\/\/agentj\.app\/account\/" target="_blank" rel="noopener" data-i18n="set\.accountOpen" hidden>/, 'the existing account link');
  assert.match(row, /id="set-news-msg" role="status" aria-live="polite" hidden/);
  assert.match(row, /id="set-news-to" hidden/);
});

test('wire = contract §5: news_get / news_set {on} → news_res; asked once per opening; ~8 s host budget', () => {
  assert.match(news, /ask1\(\{ t: 'news_get' \}, 'news_res', WAIT_MS\)/);
  assert.match(news, /ask1\(\{ t: 'news_set', on: want \}, 'news_res', WAIT_MS\)/);
  assert.equal([...news.matchAll(/ask1\(/g)].length, 2, 'no other message');
  assert.match(news, /export const WAIT_MS = 10000;/);
  assert.match(news, /const PROBLEMS = \['unlinked', 'not_bound', 'unreachable', 'rate_limited', 'fail'\];/);
  assert.match(news, /if \(S\.state === 'wait' \|\| S\.busy\) return;/, 'a second open while one is out sends nothing');
  assert.match(settings, /import \* as news from '\.\/news\.js';/);
  assert.match(settings, /if \(H\.paired\(\)\) news\.load\(\);/, 'open() asks');
  assert.match(settings, /news\.configure\(\);/);
  assert.match(settings, /news\.render\(\);/, 'language change / every render repaints it');
});

test('behaviour: revert + P302 card on failure, unlinked → account link, e-mail only when on, older computer → hint', () => {
  assert.match(news, /if \(m\.t === 'news_res' && m\.ok === true\) \{ take\(m\); render\(\); return true; \}/, 'ok → the host answer');
  assert.match(news, /S\.busy = false; S\.want = null;/, 'the asked-for position is dropped …');
  assert.match(news, /sw\.setAttribute\('aria-checked', String\(S\.busy \? S\.want === true : S\.state === 'ok' && S\.on\)\)/, '… so the switch shows what the account has');
  assert.match(news, /noticeSheet\(t\('p302\.prefTitle'\), text, t\('p302\.ok'\)\)/, 'the P302 centred card');
  assert.match(news, /else if \(m\.t === 'timeout'\) S\.state = 'old';/, 'no news_res on open = a computer before P374');
  assert.match(news, /why = m\.t === 'timeout' \? 'noanswer' : 'offline'/, 'no answer to a write = reverted + said');
  assert.match(news, /el\('set-news-acct'\)\.hidden = !\(S\.state === 'problem' && \(S\.problem === 'unlinked' \|\| S\.problem === 'not_bound'\)\)/);
  assert.match(news, /to\.hidden = !\(S\.state === 'ok' && S\.on && \(S\.email \|\| queued\)\);/, 'the e-mail / queue line only when on');
  assert.match(news, /S\.queued = S\.on \? posInt\(m\.queued\) : 0;/, 'a queue place only while on, a positive integer');
  assert.match(news, /queued \? t\('p374\.queued', \{ n: String\(S\.queued\), seats: String\(S\.seats\) \}\) : t\('p374\.sentTo'/, 'all seats taken → the queue line');
  assert.match(news, /to\.textContent = /, 'host text is text, never markup');
  assert.doesNotMatch(news, /innerHTML|insertAdjacentHTML|outerHTML|fillText/);
  assert.match(news, /return x\.protocol === 'https:' \? x\.href : '';/, 'sample_url: https only (never javascript:)');
});

test('dictionaries: contract copy, both languages, same placeholders, no emoji', () => {
  const KEYS = ['label', 'help', 'sample', 'sentTo', 'unlinked', 'notBound', 'unreachable', 'rateLimited', 'fail', 'noAnswer', 'old', 'offline', 'queued'].map((k) => 'p374.' + k);
  for (const k of KEYS) {
    for (const [n, d] of [['zh', zh], ['en', en], ['src', src]]) assert.equal(typeof d[k], 'string', `${n} ${k}`);
    assert.equal([...zh[k].matchAll(/\{(\w+)\}/g)].join(), [...en[k].matchAll(/\{(\w+)\}/g)].join(), k);
    for (const s of [zh[k], en[k]]) assert.doesNotMatch(s, /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u, k);
  }
  assert.equal(zh['p374.label'], 'Daily AI News'); assert.equal(en['p374.label'], 'Daily AI News');
  assert.equal(zh['p374.help'], '每天早上把 Agent J 的 Daily Agent 整理的《Musk + AI 24h》（中文）发到你的账号邮箱，看看一个每日自动化能做成什么样。随时可关。');
  assert.equal(en['p374.help'], 'Every morning, the “Musk + AI 24h” digest our Daily Agent writes (in Chinese) lands in your account email — see what a daily automation can do. Turn it off any time.');
  assert.equal(zh['p374.sample'], '看一封样例'); assert.equal(en['p374.sample'], 'See a sample');
  assert.equal(zh['p374.sentTo'], '发到 {email}'); assert.equal(en['p374.sentTo'], 'Sent to {email}');
  assert.equal(zh['p374.unlinked'], '先把这台 Agent J 主机连上 Agent J 账号。');
  assert.equal(zh['p374.queued'], '名额 {seats} 个已满，你在候补第 {n} 位。有人退订会自动补上，并发邮件告诉你。');
});

test('palette only: the P374 CSS names no colour', () => {
  const block = menuCss.slice(menuCss.indexOf('/* P374'));
  assert.ok(block.length > 50);
  assert.doesNotMatch(block, /#[0-9a-f]{3,8}\b|rgb\(|hsl\(/i);
});
