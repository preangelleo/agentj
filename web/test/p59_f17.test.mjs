// P59 / F17 收尾 (ADR-A163, PROTOCOL §16.1): the page's Face ID challenge for a sudo / secret card must equal the host's
// (shared vector with host/tests/test_p59_f17.py), and must change with every bound part.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const { elevateChallenge, ELEVATE_LABEL } = await import('../public/js/faceid.js');
const hex = (u) => Buffer.from(u).toString('hex');
const VEC = 'd09334b6a4712179d9f5d26193e55039f115fbe3714d497906fc4a3c0165b44b';

test('§16.1 elevate challenge = the host\'s (shared vector)', async () => {
  assert.equal(ELEVATE_LABEL, 'agentjarvis/passkey/elevate/v1');
  assert.equal(hex(await elevateChallenge('AAECAwQFBgcICQoLDA0ODw', 'dev-1', '0'.repeat(32), 'secret', 'f'.repeat(32), 'e'.repeat(64))), VEC);
});

test('§16.1 elevate challenge binds every part, refuses empty / multi-line parts', async () => {
  const base = ['ch', 'dev', 'a'.repeat(32), 'sudo', 'n'.repeat(32), 'd'.repeat(64)];
  const c = hex(await elevateChallenge(...base));
  for (let i = 0; i < base.length; i++) {
    const o = [...base]; o[i] += 'x';
    assert.notEqual(hex(await elevateChallenge(...o)), c, String(i));
  }
  await assert.rejects(elevateChallenge('ch', '', 'a', 'sudo', 'n', 'd'));
  await assert.rejects(elevateChallenge('ch\nx', 'dev', 'a', 'sudo', 'n', 'd'));
});
