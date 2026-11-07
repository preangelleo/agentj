"""v2 synthetic device for #541 tests; the released v1 fixture stays unchanged."""
import copy
from unittest import mock
from test_l1 import Phone as LegacyPhone, _owner_host as legacy_owner_host
from agentj import approval_review, passkey, wire

class Phone(LegacyPhone):
    def answer(self, ask, ok, *, tool=None, summary=None, rid=None):
        if ask.get('approval_v') != 2:
            return super().answer(ask, ok, tool=tool, summary=summary, rid=rid)
        envelope = copy.deepcopy(ask['envelope'])
        if summary is not None:
            envelope['details'] = summary
        if tool is not None:
            envelope['tool'] = tool
        digest = approval_review.digest(envelope)
        rid = rid or ask['id']
        message = approval_review.decision_message(self.channel,self.did,rid,'allow' if ok else 'deny',digest,task_scope=ask.get('task_scope'))
        obj = {'t':'answer','id':rid,'ok':ok,'sig':wire.b64u(self.sk.sign(message)),
               'approval_v':2,'revision':envelope['revision'],'envelope_sha256':digest}
        if ok and ask.get('passkey_required') and hasattr(self,'auth'):
            a = self.policy_host.asks[ask['id']]
            challenge = passkey.elevate_challenge(self.channel,self.did,rid,a.pk_kind,rid,digest)
            obj['fa'] = self.auth.assert_(challenge,b'0',nonce=b'0')
        return obj

def _owner_host(st, sent):
    host = legacy_owner_host(st, sent)
    host.agent.submit = lambda item: None
    with mock.patch.object(host,'hist_emit'):
        host.desktop_input('Synthetic authenticated owner task for approval review.')
    return host
