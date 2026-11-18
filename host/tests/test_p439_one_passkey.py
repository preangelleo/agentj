"""Regression inputs retained from P439 under the current paired-remote contract."""
import _hermetic  # noqa: F401
from agentj import passkey, wire
from test_p352_keys import Base
from test_p57_passkey import pk_record


class AccountCredentialScope(Base):
    def test_account_rp_assertion_does_not_replace_the_paired_credential(self):
        self.st.set_passkey(self.ph.pub, pk_record(self.a))
        page = self.get()
        name = 'ANTHROPIC_API_KEY'
        from agentj import keys
        challenge = passkey.elevate_challenge(self.ph.channel, self.ph.did, page['id'],
                                               'key_delete', page['n'], keys.digest('delete', name))
        assertion = self.a.assert_(challenge, b'\x00', rp='agentj.app', nonce=b'\x00')
        fields = {k: assertion[k] for k in ('id', 'cd', 'ad', 'sig')}
        previous = self.envf.read_bytes()
        result = self.op(page, 'delete', name, fa=fields)
        self.assertFalse(result['ok'])
        self.assertEqual(previous, self.envf.read_bytes())
        self.assertEqual(self.st.devices()[self.ph.did]['pk']['id'], wire.b64u(self.a.id))

    def test_paired_credential_needs_a_new_assertion_for_the_next_card(self):
        self.st.set_passkey(self.ph.pub, pk_record(self.a))
        page = self.get()
        name = 'ANTHROPIC_API_KEY'
        first = self.fa(page, 'copy', name)
        self.assertTrue(self.op(page, 'copy', name, fa=first)['ok'])
        second = self.get()
        previous = self.envf.read_bytes()
        self.assertFalse(self.op(second, 'delete', name, fa=first)['ok'])
        self.assertEqual(previous, self.envf.read_bytes())
        third = self.get()
        self.assertTrue(self.op(third, 'delete', name, fa=self.fa(third, 'delete', name))['ok'])
        self.no_leak()
