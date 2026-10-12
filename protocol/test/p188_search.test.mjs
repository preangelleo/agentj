// P188 (PROTOCOL §28): protocol/search.js against protocol/vectors/p188-search.json (host/tests/test_p188_search.py checks
// the Python mirror against the same file). AJ_REGEN=1 rewrites the vectors from this implementation.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import * as S from '../search.js';

const VEC = new URL('../vectors/p188-search.json', import.meta.url);
const FOLDS = ['ＡＢＣ１２３', 'Hello World', '說話這個們', '搜尋記錄', 'ＡｇｅｎｔＪ　設定', 'ﬁle', 'Straße', 'ΑΣ', '妳好，世界！', 'ｶﾀｶﾅ'];
const QUERIES = ['  說話 ABC　ＡＢＣ abc ', 'deploy', '部署  上线', 'a b c d e f g h i j', '', '   ', 'x'.repeat(80), '設定 设定'];
const TURNS = [
  { id: 1, ts: 1000, src: { k: 'phone', dev: 'd1', text: '帮我部署一下网站' }, reply: { text: '好的，已经部署上线。' }, end: 'done' },
  { id: 2, ts: 2000, src: { k: 'phone', dev: 'd1', text: 'Deploy the site', att: [{ name: 'deploy-log.txt', mime: 'text/plain', bytes: 10, kind: 'file' }] }, reply: { text: 'Deployed. deploy deploy' }, end: 'done' },
  { id: 3, ts: 3000, src: { k: 'agent', text: '' }, reply: { text: '部署失败，回滚了。上线推迟。' }, end: 'done' },
  { id: 4, ts: 4000, src: { k: 'phone', dev: 'd2', name: 'iPad', text: '部署 上线', quote: { id: 1, text: '已经部署上线' } }, reply: { text: '收到' }, end: 'done' },
  { id: 5, ts: 5000, src: { k: 'phone', text: '部署' }, reply: { text: '〔不回群〕' }, end: 'done' },
  { id: 6, ts: 6000, src: { k: 'phone', text: '部署上线' }, reply: { text: '' }, card: { withdrawn: true }, end: 'done' },
  { id: 7, ts: 7000, src: { k: 'telegram', text: 'Telegram owner private chat.\n說話 測試' }, reply: { text: 'ok' }, end: 'done' },
];
const QS = ['部署', '部署 上线', 'deploy', 'DEPLOY.', '說話', 'telegram', 'nothing-here', '部署 回滚'];
const TG = /^Telegram owner private chat\.\n/;
const fieldsOf = (t) => S.fieldsOf(t.src.k === 'telegram' ? { ...t.src, text: t.src.text.replace(TG, '') } : t.src, t.reply);

function derive() {
  return {
    note: 'P188 chat search vectors (PROTOCOL §28): protocol/search.js and host/agentj/hsearch.py must agree on fold, terms and score. '
      + 'A Telegram page is scored without its source line (snap.js / hsearch.fields_of). Silent and withdrawn pages are never hits.',
    fold: FOLDS.map((s) => ({ s, f: S.fold(s) })),
    terms: QUERIES.map((q) => ({ q, t: S.terms(q) })),
    turns: TURNS,
    scores: QS.map((q) => { const ts = S.terms(q); return { q, terms: ts, turns: TURNS.map((t) => ({ id: t.id, r: S.scoreFields(fieldsOf(t), ts) })) }; }),
  };
}
if (process.env.AJ_REGEN) writeFileSync(VEC, JSON.stringify(derive(), null, 1) + '\n');
const vec = JSON.parse(readFileSync(VEC, 'utf8'));

test('fold, terms and scores match the vectors', () => { assert.deepEqual(derive(), vec); });
test('fold is case / width / Traditional insensitive and idempotent', () => {
  assert.equal(S.fold('ＡＢＣ說話'), 'abc说话');
  for (let i = 0; i < S.T2S.length; i += 2) assert.equal(S.fold(S.T2S[i + 1]), S.T2S[i + 1], 'a simplified target must fold to itself');
  assert.equal(S.T2S.length % 2, 0);
});
test('AND: every term must be found', () => {
  assert.equal(S.scoreFields({ in: '部署', reply: '' }, S.terms('部署 回滚')), null);
  assert.ok(S.scoreFields({ in: '部署', reply: '回滚' }, S.terms('部署 回滚')));
});
test('weights: the input beats the reply; the exact input beats both; the phrase bonus needs order', () => {
  const ts = S.terms('部署 上线');
  const exact = S.scoreFields({ in: '部署 上线' }, ts).score, inp = S.scoreFields({ in: '先部署再说上线' }, ts).score, rep = S.scoreFields({ reply: '先部署再说上线' }, ts).score;
  assert.ok(exact > inp && inp > rep, `${exact} > ${inp} > ${rep}`);
  assert.ok(S.scoreFields({ reply: 'x 部署 上线' }, ts).score > S.scoreFields({ reply: 'x 上线 部署' }, ts).score);
});
test('recency is small and decays', () => {
  const now = 1e9;
  assert.ok(S.recency(now, now) <= 1.5 && S.recency(now - 7 * 86400, now) === 0.75 && S.recency(null, now) === 0);
});
test('snippet: ≤ 160 code points around the first hit, ellipses at the cut ends; marks cover every occurrence', () => {
  const s = S.snippet('x'.repeat(100) + '說話' + 'y'.repeat(300), ['说话']);
  assert.ok(s.startsWith('…') && s.endsWith('…') && s.includes('說話'));
  assert.equal(Array.from(s).length, 162);
  assert.equal(S.snippet('short 說話', ['说话']), 'short 說話');
  assert.deepEqual(S.marks('Hello 說話 world HELLO', ['说话', 'hello']), [['Hello', true], [' ', false], ['說話', true], [' world ', false], ['HELLO', true]]);
  assert.deepEqual(S.marks('ＡＢＣ', ['abc']), [['ＡＢＣ', true]]);
  assert.deepEqual(S.marks('😀abc', ['abc']), [['😀', false], ['abc', true]]);
});
