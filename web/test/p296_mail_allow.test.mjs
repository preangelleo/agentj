// P296: the mail card's 「以后发给 X 不用再确认」 picks ride in the allow signature — wire.js mailAllowMessage must produce the
// same bytes as host approvals.signed_message('allow') + "\n" + approvals.allow_add_line(picks) (values below from the host).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as wire from '../../protocol/wire.js';

const dec = new TextDecoder();
const BASE = 'agentjarvis-approve-v1\nCH\nDEV\n' + 'r'.repeat(32) + '\nallow\n78f8cbedd97d55e3f4395b70473703faef01f62d598715ad31c43100a91319cc';
const LINE = '7fc5469644189d25fb353f4f4b39068552d327936f596bba0eef8b6e4d3b2b3b';   // allow_add_line(['a@x.example', 'b@x.example'])

test('mailAllowMessage = the allow text + one line hex(SHA-256("allow:" + picks.join("\\n"))), same as the host', async () => {
  const args = ['CH', 'DEV', 'r'.repeat(32), 'agentj mail send', 'S'];
  assert.equal(dec.decode(await wire.approveMessage(args[0], args[1], args[2], 'allow', args[3], args[4])), BASE);
  assert.equal(dec.decode(await wire.mailAllowMessage(...args, ['a@x.example', 'b@x.example'])), `${BASE}\n${LINE}`);
  assert.notEqual(dec.decode(await wire.mailAllowMessage(...args, ['b@x.example', 'a@x.example'])), `${BASE}\n${LINE}`, 'order is signed');
  await assert.rejects(wire.mailAllowMessage(...args, []), /no picks/);
});
