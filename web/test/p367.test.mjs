// P367 · Settings → 账号和帮助 offers 「点数」 next to 「邀请与积分」: a plain new-tab link to the account page's credits block
// (/account/#credits — no script, no token in the URL). The look is shot by p367.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dictionaries } from '../build.mjs';

const HTML = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');

test('P367: Settings links 「点数」 to https://agentj.app/account/#credits, right after 「邀请与积分」, in both languages', () => {
  const a = HTML.match(/<a [^>]*data-i18n="menu\.credits"[^>]*>([^<]*)<\/a>/);
  assert.ok(a, 'menu credits entry present');
  assert.match(a[0], /\bhref="https:\/\/agentj\.app\/account\/#credits"/);
  assert.match(a[0], /\btarget="_blank"/);
  assert.match(a[0], /\brel="noopener"/);
  const d = dictionaries();
  assert.equal(d.zh['menu.credits'], '点数');
  assert.equal(d.en['menu.credits'], 'Credits');
  assert.equal(a[1], d.zh['menu.credits'], 'zh fallback text in the page = the dictionary');
  // same row as the account links: 打开账号后台 · 邀请与积分 · 点数
  const row = HTML.match(/<p class="setlinks"><a id="set-account"[\s\S]*?<\/p>/)[0];
  assert.deepEqual([...row.matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1]), ['set.accountOpen', 'menu.invite', 'menu.credits']);
});
