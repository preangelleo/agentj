// P562 (#337): the owner's WeChat message is a main-conversation page `src.k = 'wechat'`; its label is the host's 「微信」 /
// "WeChat" (src.name), in the quiet channel colour Telegram and email use. Real browsers: p562.mjs (390, light / dark).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {toPage} from '../public/js/snap.js';
const relay = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');

test('a wechat page keeps its source kind, name and words', () => {
  const p = toPage({id: 3, ts: 1, src: {k: 'wechat', dev: 'wechat:owner', name: '微信', text: '明天东京天气怎么样？'}, reply: {text: '晴'}});
  assert.deepEqual([p.source.k, p.source.name, p.source.text], ['wechat', '微信', '明天东京天气怎么样？']);
  assert.equal(toPage({id: 4, src: {k: 'wechat-x'}}).source.k, 'sys', 'unknown kinds stay system pages');
});

test('the source card names it with the host label, in the channel colour', () => {
  assert.match(relay, /src\.k === "wechat"\) return \{kind:"tg",label:src\.name \|\| "WeChat"\}/);
});
