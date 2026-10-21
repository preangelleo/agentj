// P424 (#152): wire.friendAnswer builds the friend-card `answer` AND its signed bytes in one place. 0.24.0a1 sent
// peer_question group:colleague but signed without the group line → the host refused it on every device (bad_signature).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as wire from '../../protocol/wire.js';

const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
const sign = async (m) => new Uint8Array(await crypto.subtle.sign('Ed25519', k.privateKey, m));
const verify = (sig, m) => crypto.subtle.verify('Ed25519', k.publicKey, wire.unb64u(sig), m);
const pq = { id: 'ab'.repeat(16), tool: 'peer_question', summary: '小鹿 说：\n报价？😀\n\n草稿：\n先问主人' };
const fr = { id: 'cd'.repeat(16), tool: 'friend_request', summary: '好友请求：小鹿' };

test('every field the answer carries is in the signed text (group, ctx), and only then', async () => {
  for (const [card, ok, opts] of [[pq, true, {}], [pq, false, {}], [pq, true, { group: 'colleague' }], [pq, true, { group: 'main' }],
    [fr, true, { group: 'friend', ctx: '老客户\n先确认' }], [fr, true, {}], [fr, false, {}]]) {
    const ans = await wire.friendAnswer('ch', 'dev', card, ok, opts, sign);
    assert.deepEqual(Object.keys(ans).sort(), ['dg', 'id', 'ok', 'sig', 't', ...(ans.group ? ['group'] : []), ...(ans.ctx ? ['ctx'] : [])].sort());
    const msg = await wire.friendAnswerMessage('ch', 'dev', card.id, ok ? 'allow' : 'deny', card.tool, card.summary, ans.group, ans.ctx);
    assert.equal(await verify(ans.sig, msg), true);
    assert.equal(ans.group ?? null, opts.group ?? null);
    assert.equal(ans.dg, (await wire.shownDigest(card.tool, card.summary)).slice(0, 16));
  }
});

test('a field that would travel unsigned throws instead of being dropped from the signature', async () => {
  for (const [card, ok, opts] of [[pq, false, { group: 'colleague' }], [pq, true, { ctx: 'x' }], [pq, true, { group: 'friend' }],
    [fr, false, { ctx: 'x' }], [fr, false, { group: 'friend' }]]) {
    await assert.rejects(wire.friendAnswer('ch', 'dev', card, ok, opts, sign), /unsigned field|bad group/);
  }
});

test('friends.js builds no friend answer by hand: every card button goes through wire.friendAnswer', () => {
  const src = readFileSync(new URL('../public/js/friends.js', import.meta.url), 'utf8').replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(src, /t: 'answer'/);
  assert.doesNotMatch(src, /friendAnswerMessage|\bapprove\(/);
  assert.match(src, /friendAnswer\(channel\(\)/);
  assert.equal(readFileSync(new URL('../public/proto/wire.js', import.meta.url), 'utf8'), readFileSync(new URL('../../protocol/wire.js', import.meta.url), 'utf8'));
});
