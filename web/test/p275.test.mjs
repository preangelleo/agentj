// P275: Settings → 「下载 Agent J Brand Kit」 near the bottom (a link to agentj.app, new tab; no big file in the app) and the
// bot card's 「换成 Agent J 头像」 (signed bots_write op telegram_avatar).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
test('settings has the Brand Kit link at the bottom of the main panel', () => {
  const html = read('../public/index.html');
  const main = html.slice(html.indexOf('<div id="set-main"'), html.indexOf('<div id="set-ios"'));
  const a = main.match(/<a id="set-brandkit"[^>]*>[\s\S]*?<\/a>/);
  assert.ok(a, 'link present');
  assert.match(a[0], /href="https:\/\/agentj\.app\/docs\/robots\/#brand-kit"/);
  assert.match(a[0], /target="_blank" rel="noopener"/);
  assert.match(a[0], /<svg [^>]*stroke="currentColor"[^>]*aria-hidden="true">/);   // a line icon
  assert.match(a[0], /data-i18n="set.brandKit">下载 Agent J Brand Kit</);
  assert.ok(main.lastIndexOf('set-brandkit') > main.lastIndexOf('set.helpDocs'), 'after Help, the last item');
  assert.doesNotMatch(main, /\.zip/);                                                  // no file shipped inside the app
});
test('both dictionaries carry the entry', () => {
  for (const [f, label] of [['web.zh.json', '下载 Agent J Brand Kit'], ['web.en.json', 'Download Agent J Brand Kit'], ['web.zh.src.json', '下载 Agent J Brand Kit']]) {
    const d = JSON.parse(read('../i18n/' + f));
    assert.equal(d.set.brandKit, label, f);
    assert.match(d.set.brandKitNote, /bot/, f);
  }
  const mod = read('../public/i18n.js');
  assert.ok(mod.includes('"set.brandKit": "Download Agent J Brand Kit"') && mod.includes('"set.brandKit": "下载 Agent J Brand Kit"'));
});
test('bot card offers the Agent J avatar only once a Telegram token is configured', () => {
  const js = read('../public/js/bots.js');
  assert.match(js, /if\(detail\.telegram\?\.configured\)main\.append\(button\(copy\('换成 Agent J 头像','Use the Agent J avatar'\)/);
  assert.match(js, /await write\(\{op:'telegram_avatar',id:c\.id\}\)/);
  assert.match(js, /confirmSheet\(copy\('换成 Agent J 头像'/);
});
