import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import * as snap from '../public/js/snap.js';
import * as wire from '../../protocol/wire.js';
const id = 'a'.repeat(32), other = 'b'.repeat(32);
const card = (id, tool = 'peer_question') => ({ id, tool, summary: '朋友说：\n您好😀\n草稿：好的', ttl: 120 });
beforeEach(() => {
  snap.configure({onChange() {}, onTurn() {}});
  snap.S.asks.clear(); snap.S.qs.clear(); snap.S.touched.clear(); snap.S.order = [];
  snap.S.conn = true; snap.S.handledWaiting = false; snap.setStatus({s: 'waiting'});
});
test('P396 clicking allow or deny leaves waiting before signing/queue/receipt; retry stays available', () => {
  for (const tool of ['peer_question', 'friend_request', 'Bash']) {
    snap.addAsk(card(id, tool));
    assert.equal(snap.snapshot().status, 'waiting');
    snap.touch(id);
    assert.equal(snap.snapshot().status, 'working');
    assert.equal(snap.snapshot().approval.state, 'open');
    snap.answerRefused({id, ask: {...card(id, tool), summary: '主机当前原文😀'}});
    assert.equal(snap.snapshot().status, 'working');
    assert.equal(snap.snapshot().approval.summary, '主机当前原文😀');
    assert.equal(snap.snapshot().approval.answer_failed, true);
    snap.askDone({id, result:'deny'});
    assert.equal(snap.snapshot().status, 'working', 'late host waiting status does not turn red again');
    snap.S.touched.clear();
  }
});
test('P396 another unanswered card still needs its own tap; later cards are never suppressed', () => {
  snap.addAsk(card(id)); snap.addAsk(card(other)); snap.touch(id);
  assert.equal(snap.snapshot().status, 'waiting'); snap.touch(other);
  assert.equal(snap.snapshot().status, 'working');
  snap.addAsk(card('c'.repeat(32))); assert.equal(snap.snapshot().status, 'waiting');
});
test('P396 reconnect restores a tapped card without restoring waiting colour', () => {
  snap.addAsk(card(id)); snap.touch(id); snap.S.asks.clear(); snap.S.order = [];
  snap.addAsk(card(id)); assert.equal(snap.snapshot().status, 'working');
  snap.S.conn = false; assert.equal(snap.snapshot().status, 'unknown');
});
test('P396 peer group signs an actual extra line; ctx only on friend_request; signed text cannot be altered', async () => {
  const args = ['channel','device',id,'allow','peer_question','您好😀'];
  const bytes = await wire.friendAnswerMessage(...args,'colleague');
  const expected = new TextDecoder().decode(await wire.approveMessage(...args)) + '\n' + createHash('sha256').update('group:colleague').digest('hex');
  assert.equal(new TextDecoder().decode(bytes), expected);
  const key = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']);
  const sig = await crypto.subtle.sign('Ed25519', key.privateKey, bytes);
  assert.equal(await crypto.subtle.verify('Ed25519',key.publicKey,sig,bytes),true);
  assert.equal(await crypto.subtle.verify('Ed25519',key.publicKey,sig,await wire.approveMessage(...args)),false);
  await assert.rejects(wire.friendAnswerMessage(...args,'friend'), /bad group/);
});
