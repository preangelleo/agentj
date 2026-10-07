// P73 (ADR-A176) without a browser: the fr_ctx control object, the 「同意」 + 「补充设定」 answer line, the 4000-character
// limit (code points, as the host counts), both wire.js copies identical, and the page-side rules in the sources —
// /add-friend is intercepted and sent only as the signed fr_add, fr_ctx is a signed write (no fr_send appeared), the
// in-place link handler accepts only this page's own friends screens. The behaviour itself runs in p73_friends.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import * as wire from '../../protocol/wire.js';

const sha = (s) => createHash('sha256').update(s).digest('hex');
const dec = new TextDecoder();
const src = (p) => readFileSync(new URL(p, import.meta.url), 'utf8').replace(/^\s*\/\/.*$/gm, '');

test('fr_ctx: a control action; object text = friend \\n text ("" for null); the control message signs its SHA-256', async () => {
  assert.ok(wire.CONTROL_ACTIONS.includes('fr_ctx'));
  assert.equal(wire.controlObject('fr_ctx', { friend: 'AJ-PH8E-AJT4-26GJ-ACQ9', text: '王姐\n老客户' }), 'AJ-PH8E-AJT4-26GJ-ACQ9\n王姐\n老客户');
  assert.equal(wire.controlObject('fr_ctx', { friend: 'AJ-1', text: null }), 'AJ-1\n');
  assert.equal(wire.controlObject('fr_ctx', { friend: 'AJ-1', text: '' }), 'AJ-1\n');
  const m = dec.decode(await wire.controlMessage('CH', 'DEV', 'fr_ctx', 'f'.repeat(32), 7, { friend: 'AJ-1', text: 'x' }));
  assert.equal(m, `agentjarvis-control-v1\nCH\nDEV\nfr_ctx\n${'f'.repeat(32)}\n7\n${sha('AJ-1\nx')}`);
});

test('friendAnswerMessage + ctx: one more line hex(SHA-256("ctx:"+ctx)) after the group line; only on a friend_request allow', async () => {
  const args = ['CH', 'DEV', 'a'.repeat(32)];
  const base = dec.decode(await wire.approveMessage(...args, 'allow', 'friend_request', 's'));
  const g = sha('group:friend'), c = sha('ctx:老客户，报价先问我');
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'friend_request', 's', 'friend', '老客户，报价先问我')), `${base}\n${g}\n${c}`);
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'friend_request', 's', null, '老客户，报价先问我')), `${base}\n${c}`);
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'friend_request', 's', 'friend', '')), `${base}\n${g}`, 'empty ctx = no line');
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'friend_request', 's', 'friend')), `${base}\n${g}`, 'P71 callers unchanged');
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'deny', 'friend_request', 's', null, 'x')),
    dec.decode(await wire.approveMessage(...args, 'deny', 'friend_request', 's')), 'a deny never carries ctx');
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'peer_question', 's', null, 'x')),
    dec.decode(await wire.approveMessage(...args, 'allow', 'peer_question', 's')));
  assert.equal(wire.FRIEND_CTX_MAX, 4000);
  await wire.friendAnswerMessage(...args, 'allow', 'friend_request', 's', null, '😀'.repeat(4000));     // 4000 code points = OK
  await assert.rejects(wire.friendAnswerMessage(...args, 'allow', 'friend_request', 's', null, '中'.repeat(4001)), /ctx too long/);
});

test('the page ships the protocol file byte for byte', () => {
  assert.equal(readFileSync(new URL('../public/proto/wire.js', import.meta.url), 'utf8'), readFileSync(new URL('../../protocol/wire.js', import.meta.url), 'utf8'));
});

test('/add-friend is intercepted by the page and only ever leaves as the signed fr_add', () => {
  const relay = src('../public/js/relay.js'), fr = src('../public/js/friends.js');
  assert.match(relay, /add\[-_\]friend/, 'say() intercepts /add-friend (and /add_friend)');
  assert.match(relay, /addFriendCmd\(/);
  const fn = fr.slice(fr.indexOf('export async function addFriendCmd'), fr.indexOf('export function openFromCmd'));
  assert.ok(fn.length > 100);
  assert.match(fn, /sendAdd\(/, 'the command uses the page button\'s own send');
  assert.doesNotMatch(fn, /sendApp|t: '/, 'no message of its own');
  const send = fr.slice(fr.indexOf('async function sendAdd'), fr.indexOf('export function splitAddArg'));
  assert.match(send, /write\('fr_add'/, 'sendAdd = the signed fr_add');
  assert.doesNotMatch(fr, /fr_send|fr_msg|'pmsg'/);
  assert.match(fr, /fr_ctx: \(o\) => \(\{ t: 'fr_ctx', friend: o\.friend, text: o\.text \}\)/, 'fr_ctx goes through the signed write table');
});

test('the in-place link handler accepts only this page\'s own friends screens', () => {
  const app = src('../public/app.js');
  const m = /const OWN_FRIENDS = (\/.+\/);/.exec(app);
  assert.ok(m, 'OWN_FRIENDS present');
  const re = eval(m[1]);   // eslint-disable-line no-eval — the literal from the shipped file
  for (const ok of ['https://m.agentj.app/friends#card', 'https://m.agentj.app/friends#add=AJ-PH8E-AJT4-26GJ-ACQ9', 'https://m.agentj.app/friends?lang=en#card']) assert.ok(re.test(ok), ok);
  for (const no of ['https://m.agentj.app/friends#add=<x>', 'https://evil.example/friends#card', 'https://m.agentj.app/friends#p=abc', 'https://m.agentj.app.evil/friends#card',
    'javascript:alert(1)', 'https://m.agentj.app/friends#card/../x']) assert.ok(!re.test(no), no);
});
