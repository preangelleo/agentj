// P563 追加 6: no 「钥匙」 on user-facing Chinese copy. Face ID / fingerprint credentials are named the way the rest of the
// app names them (「Face ID / 指纹」「{bio} 记录」「通行密钥」); Apple's real menu names 「iCloud 钥匙串」「密码（钥匙串）」
// 「密码与钥匙串」 stay. Covers the web and dashboard dictionaries (source, override, built) and the built web i18n.js.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const AJ = join(HERE, '..', '..');
const read = (p) => readFileSync(join(AJ, p), 'utf8');
const flat = (o, p = '', out = {}) => {
  for (const [k, v] of Object.entries(o)) {
    const key = p ? p + '.' + k : k;
    if (v && typeof v === 'object') flat(v, key, out); else out[key] = v;
  }
  return out;
};
const DICTS = ['web/i18n/web.zh.src.json', 'web/i18n/web.zh.override.json', 'web/i18n/web.zh.json',
  // the public export ships web/ alone: the Dashboard dictionaries are checked where they exist (the private tree)
  ...['dashboard/i18n/app.zh.src.json', 'dashboard/i18n/app.zh.override.json', 'dashboard/i18n/app.zh.json'].filter((f) => existsSync(join(AJ, f)))];
const bare = (s) => String(s).replaceAll('钥匙串', '').includes('钥匙');

test('zh dictionaries: 「钥匙」 only inside 「钥匙串」', () => {
  for (const f of DICTS) {
    const bad = Object.entries(flat(JSON.parse(read(f)))).filter(([, v]) => typeof v === 'string' && bare(v)).map(([k]) => k);
    assert.deepEqual(bad, [], f);
  }
  const zhBuilt = read('web/public/i18n.js').split('\n').filter((l) => bare(l));
  assert.deepEqual(zhBuilt, [], 'web/public/i18n.js');
});

test('Apple menu names keep 钥匙串', () => {
  const zh = flat(JSON.parse(read('web/i18n/web.zh.json')));
  assert.match(zh['pk.notSaved.mac'], /密码与钥匙串/);
  assert.match(zh['pk.notSaved.ios'], /密码（钥匙串）/);
  assert.match(zh['pkp.where.ios'], /iCloud 钥匙串/);
});

test('p561.save.fail: says the system did not save, names the real button, does not repeat the title', () => {
  const zh = flat(JSON.parse(read('web/i18n/web.zh.json'))), en = flat(JSON.parse(read('web/i18n/web.en.json')));
  assert.equal(zh['p561.save.fail'], '系统没有保存，Agent J 主机什么都没收到。另一台的批准还有效，点「再试一次」就行。');
  assert.ok(zh['p561.save.fail'].includes(`「${zh['p539.fail.retry']}」`), 'quotes the real retry button');
  assert.ok(!zh['p561.save.fail'].includes(zh['p561.save.failTitle']), 'no repeat of the title');
  assert.equal(en['p561.save.fail'], "The system didn't save it, so nothing reached the Agent J host. The other device's approval still stands: tap Try again.");
  assert.ok(en['p561.save.fail'].includes(en['p539.fail.retry']));
  assert.doesNotMatch(en['p561.save.fail'], /\bkey\b/);
});
