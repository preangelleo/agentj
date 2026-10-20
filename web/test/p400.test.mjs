// P400 item 3: the online support chat (https://support.agentj.app/ → agentj.app/support/, works without pairing) is reachable
// from the places of the phone page that are about being disconnected or stuck: Settings → 帮助和反馈, the disconnect page
// (#error-view) and Settings → 紧急修复. The disconnected card (#offDlg) keeps backlog #84's three levels (P375 guard: no
// direct site link in the card): its 「打开紧急恢复」 opens 紧急修复, whose last line is 在线客服.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const HTML = read('../public/index.html');
const SRC = JSON.parse(read('../i18n/web.zh.src.json')), ZH = JSON.parse(read('../i18n/web.zh.json')), EN = JSON.parse(read('../i18n/web.en.json'));
const DICT = (await import('../public/i18n.js')).default;
const URL_ = 'https://support.agentj.app/';
const link = (id) => { const m = HTML.match(new RegExp(`<a id="${id}"[^>]*>([^<]*)</a>`)); assert.ok(m, id); return m[0]; };
const section = (open, close = '</section>') => { const i = HTML.indexOf(open); assert.ok(i > 0, open); return HTML.slice(i, HTML.indexOf(close, i)); };

test('P400 support link: three entries, the full address, new tab, rel=noopener, nothing secret', () => {
  const ids = ['set-help-support', 'error-support', 'rs-support'];
  for (const id of ids) {
    const a = link(id);
    assert.equal(HTML.split(`id="${id}"`).length, 2, id + ' once');
    assert.ok(a.includes(`href="${URL_}"`), id + ' href');
    assert.ok(a.includes('target="_blank"') && a.includes('rel="noopener"'), id + ' target/rel');
  }
  const all = [...HTML.matchAll(/href="(https:\/\/support\.agentj\.app[^"]*)"/g)].map((m) => m[1]);
  assert.deepEqual(all, Array(3).fill(URL_), 'no query, fragment or path on the support address');
});

test('P400 support link: Settings → 帮助和反馈 carries 在线客服 next to 手机怎么用 / 写信给我们', () => {
  assert.match(HTML, /data-i18n="set\.helpDocs">手机怎么用<\/a><a href="mailto:feedback@agentj\.app" rel="noopener" data-i18n="set\.helpMail">写信给我们<\/a><a id="set-help-support" href="https:\/\/support\.agentj\.app\/" target="_blank" rel="noopener" data-i18n="p400\.link">在线客服<\/a><\/p>/);
});

test('P400 support link: the disconnected card reaches it through 紧急修复 (backlog #84: no direct site link in the card)', () => {
  const card = section('<div id="offDlg"', '<div id="share-notice"');
  assert.ok(!card.includes('support.agentj.app') && !card.includes('https://'), 'the card itself stays without a site link (P375 guard)');
  assert.ok(card.includes('id="offRescue"'));
  assert.match(read('../public/app.js'), /\$\('offRescue'\)\.addEventListener\('click', \(\) => \{ offline\.later\(\); rescue\.openBlock\(\); \}\)/);
});

test('P400 support link: the disconnect page and 紧急修复 carry it with a plain line', () => {
  const err = section('<section id="error-view"');
  assert.match(err, /id="error-help-support"[\s\S]*id="error-help-online" data-i18n="p400\.help"[^>]*>[^<]+<\/p>\n\s*<p class="helpmail"><a id="error-support"[^>]*>support\.agentj\.app<\/a><\/p>[\s\S]*id="error-help-mail"/);
  const rs = section('<section class="setcard rescue" id="set-s-rescue"');
  assert.match(rs, /<div class="rssupport" id="rs-support-box"><p class="setnote" id="rs-support-note" data-i18n="p400\.rescue">[^<]+<\/p>\n\s*<p class="setlinks"><a id="rs-support"[^>]*data-i18n="p400\.link">在线客服<\/a><\/p><\/div>/);
  assert.match(read('../public/js/rescue.js'), /const sup = el\('rs-support-box'\);[^\n]*\n\s*if \(block && sup && block\.lastElementChild !== sup\) block\.append\(sup\);/, 'stays under the cards rescue.js appends');
});

test('P400 support link: both dictionaries, exact strings, no trial words', () => {
  const want = {
    'p400.link': ['在线客服', 'Online support'],
    'p400.help': ['Agent J 不回话时，也能在网页上问在线客服，不用配对。', "If Agent J can't answer, ask online support on the web. No pairing needed."],
    'p400.rescue': ['也可以在网页上问在线客服，不用配对。', 'You can also ask online support on the web. No pairing needed.'],
  };
  for (const [k, [zh, en]] of Object.entries(want)) {
    assert.equal(SRC[k], zh, 'src ' + k); assert.equal(ZH[k], zh, 'zh ' + k); assert.equal(EN[k], en, 'en ' + k);
    assert.equal(DICT.zh[k], zh, 'built zh ' + k); assert.equal(DICT.en[k], en, 'built en ' + k);
    assert.doesNotMatch(zh + en, /试用|trial/i, k);
  }
});
