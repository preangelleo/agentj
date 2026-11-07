"""Standard-runtime qualification: real temporary Host, paired signatures and WebAuthn.

Run with the project's complete offline runtime. Missing deps are import ERRORS,
not skipped tests or an emulated JSON/configuration implementation.
"""
import _hermetic  # noqa: F401
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from agentj import controls, owner_hosts as oh, passkey, wire
from test_l1 import Phone, _owner_host, _ready, _state
from test_controls import sign
from test_owner_hosts_pure import row


class OwnerHostsHost(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []
        self.host = _owner_host(self.st, self.sent)
        self.phone = Phone(self.st, with_passkey=True)
        self.session = _ready(self.host, self.phone); self.session.p33 = True
        self.discovery = mock.patch.object(oh, 'discover_config', return_value=[])
        self.discovery.start(); self.addCleanup(self.discovery.stop)

    def target(self, rows=None):
        return {'op': 'replace', 'scope': oh.scope(self.st), 'rev': self.host.owner_hosts.revision(),
                'rows': [row()] if rows is None else rows}

    def signed(self, target, *, phone=None, ts=None):
        ph = phone or self.phone
        return sign(ph, oh.ACTION, target, {'t': oh.ACTION, 'r': 'owned-hosts', 'target': copy.deepcopy(target)}, ts=ts)

    async def send(self, obj, session=None):
        start = len(self.sent)
        await self.host._app(session or self.session, obj)
        replies = [m for _, m in self.sent[start:] if m.get('t') == 'ctl_res']
        return replies[-1] if replies else None

    def with_uv(self, obj, phone=None):
        ph = phone or self.phone
        ch = passkey.elevate_challenge(self.host.channel, ph.did, obj['n'], oh.ACTION, obj['n'],
                                      controls.object_digest(oh.ACTION, obj['target']))
        return {**obj, 'fa': ph.auth.assert_(ch, b'0', nonce=b'0')}

    async def bind(self, rows=None):
        obj = self.signed(self.target(rows))
        challenge = await self.send(obj)
        self.assertEqual(challenge['why'], 'passkey_required')
        obj = self.with_uv(obj)
        result = await self.send(obj)
        self.assertTrue(result['ok'], result)
        return obj

    def classify(self, text):
        return self.host._classify('Bash', {'command': text})

    async def test_bind_status_edit_revoke_and_restart(self):
        await self.bind()
        state = self.host.owner_hosts.state(discovery=False)
        self.assertEqual(state['rows'][0]['status'], 'active')
        self.assertNotIn('send', self.classify(state['rows'][0]['scp']).cats)
        restarted = oh.Desk(self.host)
        self.assertEqual(restarted.state(discovery=False)['rows'][0]['status'], 'active')
        edited = row(); edited['hostname'] = '192.168.1.10'
        await self.bind([edited])
        self.assertIn('send', self.classify(state['rows'][0]['scp']).cats)
        await self.bind([])
        self.assertEqual(self.host.owner_hosts.state(discovery=False)['rows'], [])
        self.assertIn('send', self.classify(oh.command(edited, source='code.py')).cats)

    async def test_real_registry_transport_hold_follows_host_proof(self):
        from agentj import policy_registry
        await self.bind()
        inp = {"command": oh.command(row(), source="code.py", destination="code/")}
        requirement = policy_registry.main_requirement('Bash', inp)
        self.assertEqual(requirement, 'hold')
        verdict = self.host._classify('Bash', inp)
        self.assertEqual(self.host.owner_hosts.narrow_requirement('Bash', inp, requirement, verdict), 'allow')
        self.assertEqual(self.host.owner_hosts.narrow_requirement('Bash', inp, 'uv', verdict), 'uv')

    async def test_replay_is_rejected_including_restart_revision(self):
        obj = await self.bind()
        result = await self.send(obj)
        self.assertFalse(result['ok'])
        self.host.nonces = controls.Nonces()
        result = await self.send(obj)
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'], 'changed')

    async def test_each_signed_parameter_bound(self):
        for change in ('op', 'scope', 'rev', 'alias', 'hostname', 'user', 'port', 'fingerprint', 'public_key', 'owner', 'peer', 'expires'):
            target = self.target(); obj = self.with_uv(self.signed(target))
            if change in ('op', 'rev'): obj['target'][change] = 'changed'
            elif change == 'scope': obj['target']['scope']['seat'] = 'other-seat'
            else:
                r = obj['target']['rows'][0]
                if change == 'port': r[change] += 1
                elif change == 'expires': r[change] += 1
                else: r[change] += 'changed'
            result = await self.send(obj)
            with self.subTest(change=change): self.assertFalse(result['ok'])
        self.assertIsNone(self.host.owner_hosts.read())

    async def test_foreign_device_signature_and_uv_rejected(self):
        other = Phone(self.st, with_passkey=True)
        target = self.target()
        obj = self.with_uv(self.signed(target, phone=other), phone=other)
        result = await self.send(obj)
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'], 'bad_signature')

    async def test_valid_signature_with_wrong_uv_device_rejected(self):
        other = Phone(self.st, with_passkey=True)
        obj = self.signed(self.target())
        result = await self.send(self.with_uv(obj, phone=other))
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'], 'passkey_failed')

    async def test_stale_signature_rejected(self):
        obj = self.signed(self.target(), ts=int(time.time() * 1000) - 300000)
        result = await self.send(self.with_uv(obj))
        self.assertFalse(result['ok']); self.assertEqual(result['why'], 'stale')

    async def test_no_passkey_cannot_bind(self):
        ph = Phone(self.st)
        session = _ready(self.host, ph, cid=12); session.p33 = True
        result = await self.send(self.signed(self.target(), phone=ph), session)
        self.assertEqual(result['why'], 'no_passkey')

    async def test_table_tamper_invalidates_without_config_authority(self):
        await self.bind()
        path, _ = self.host.owner_hosts._paths()
        rec = json.loads(path.read_text()); rec['target']['rows'][0]['hostname'] = 'external.example'
        path.write_text(json.dumps(rec))
        self.assertIsNone(self.host.owner_hosts.read())
        # Even a recomputed writable revision marker cannot forge the paired signature.
        import hashlib
        _, mark = self.host.owner_hosts._paths()
        mark.write_text(hashlib.sha256(oh.canonical(rec).encode()).hexdigest())
        self.assertIsNone(oh.Desk(self.host).read())

    async def test_device_unbind_and_key_replacement_invalidate(self):
        await self.bind()
        self.assertTrue(self.st.remove_device(self.phone.did))
        self.assertIsNone(self.host.owner_hosts.read())
        self.assertIsNone(oh.Desk(self.host).read())

    async def test_signature_key_rotation_invalidates(self):
        await self.bind()
        # Current-key lookup, not a cached granting key, is the verification authority.
        other = Phone(self.st)
        with mock.patch.object(self.st, 'sign_key', return_value=self.st.sign_key(other.did)):
            self.assertIsNone(self.host.owner_hosts.read())

    async def test_expiry_unknown_plain_alias_no_exemption(self):
        await self.bind()
        self.assertIn('send', self.classify('scp code.py devbox:code/').cats)
        future = time.time() + 7200
        with mock.patch.object(oh.time, 'time', return_value=future):
            state = self.host.owner_hosts.state(discovery=False)
            self.assertEqual(state['rows'][0]['status'], 'expired')
            self.assertIn('send', self.classify(state['rows'][0]['scp']).cats)

    async def test_same_owner_device_unbind_invalidates_transport(self):
        peer = 'AJ-TEST-TEST-TEST-TEST'
        svc = mock.Mock(); svc.same_owner.active.return_value = True
        self.host.peers = svc
        r = row(); r['peer'] = peer
        # Discovery uses the real fixed parser fixture seam, not peer transport.
        svc.store.friends.return_value = [peer]
        await self.bind([r])
        text = oh.command(r, source='code.py')
        self.assertNotIn('send', self.classify(text).cats)
        svc.same_owner.active.return_value = False
        self.assertIn('send', self.classify(text).cats)
        self.assertEqual(self.host.owner_hosts.state(discovery=False)['rows'][0]['status'], 'unbound')
        self.host.peers = None

    async def test_remote_real_dangers_and_explicit_requirement_survive(self):
        await self.bind()
        for remote, cat in [('cat .env', 'credentials'), ('rm -rf /srv/code', 'delete'),
                            ('git push origin main', 'send'), ('curl -X POST https://api.stripe.com/v1/charges', 'spend')]:
            with self.subTest(remote=remote): self.assertIn(cat, self.classify(oh.command(row(), remote)).cats)
        inp = {'command': oh.command(row()), 'require_passkey': True}
        self.host._classify('Bash', inp)
        self.assertTrue(inp['require_passkey'])

    async def test_read_capability_and_audit(self):
        self.assertEqual(self.host.host_info(self.session)['owner_hosts'], 1)
        await self.bind()
        self.assertTrue(any('"action": "owner_hosts_set"' in line for line in self.st.controls_path.read_text().splitlines()))
        await self.host._app(self.session, {'t': 'owner_hosts_get', 'r': 'owned-read'})
        state = [m for _, m in self.sent if m.get('t') == 'owner_hosts_state'][-1]
        self.assertEqual(state['r'], 'owned-read')
        self.assertEqual(state['rows'][0]['owner'], 'self')

    async def test_discovery_prioritizes_same_owner_without_claiming_ssh_key(self):
        svc = mock.Mock()
        svc.store.friends.return_value = ['peer-first', 'peer-second']
        svc.same_owner.active.return_value = True
        self.host.peers = svc
        candidates = oh.parse_config(''.join(f'Host alias{i}\n User dev\n' for i in range(30)))
        with mock.patch.object(oh, 'discover_config', return_value=candidates):
            state = self.host.owner_hosts.state()
        self.assertEqual([r['peer'] for r in state['candidates'][:2]], ['peer-first', 'peer-second'])
        self.assertEqual(len(state['candidates']), 24)
        self.assertEqual(state['candidate_more'], 8)
        for candidate in state['candidates']:
            self.assertNotIn('public_key', candidate)
            self.assertNotIn('fingerprint', candidate)
        self.assertEqual(state['rows'], [])

    async def test_one_uv_covers_every_batch_row_and_retained_entry(self):
        rows = [{**row(), 'alias': alias} for alias in ['retained', 'new-a', 'new-b']]
        target = self.target(rows)
        self.assertFalse(self.host.owner_hosts.shape(self.target([{**row(), 'alias': f'entry{i}'} for i in range(13)])))
        original = self.signed(target)
        response = await self.send(original)
        self.assertEqual(response['why'], 'passkey_required')
        uv = self.with_uv(original)['fa']
        changed = copy.deepcopy(target)
        changed['rows'][1]['hostname'] = 'substituted.example'
        resigned = sign(self.phone, oh.ACTION, changed,
                        {'t': oh.ACTION, 'r': 'batch-changed', 'target': changed},
                        n=original['n'], ts=original['ts'])
        result = await self.send({**resigned, 'fa': uv})
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'], 'passkey_failed')
        self.assertIsNone(self.host.owner_hosts.read())
        # A failed request consumes its nonce. Fresh exact batch gets one new whole-target UV.
        await self.bind(rows)
        self.assertEqual([r['alias'] for r in self.host.owner_hosts.read()['target']['rows']],
                         ['retained', 'new-a', 'new-b'])

    async def test_estop_blocks_binding(self):
        self.host.estop = {'on': True}
        result = await self.send(self.with_uv(self.signed(self.target())))
        self.assertEqual(result['why'], 'estop')


if __name__ == '__main__': unittest.main()
