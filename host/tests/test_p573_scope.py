"""Signed concrete plans must never become generic or cross-task authority."""
import _hermetic  # noqa: F401
import unittest
from agentj import approvals
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

class Scope(unittest.TestCase):
    def test_scope_is_signed(self):
        key = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        args = ('c', 'd', 'r', 'allow', 'a' * 64)
        scope = '{"task":"one","actions":[]}'
        sig = key.sign(approvals.signed_message(*args, task_scope=scope))
        self.assertTrue(approvals.verify(pub, sig, *args, task_scope=scope))
        self.assertFalse(approvals.verify(pub, sig, *args, task_scope=scope.replace('one', 'two')))
        self.assertFalse(approvals.verify(pub, sig, *args))

class Audit(unittest.TestCase):
    def test_exact_scope_and_task_are_required_for_auto_audit(self):
        import json
        from unittest.mock import patch
        from agentj import wire
        key = Ed25519PrivateKey.generate()
        action = {'tool': 'Bash', 'input_sha256': 'b'*64, 'class':['shell', ['delete'], ['rm']]}
        scope = json.dumps({'task':'task-one','actions':[action],'expires':120,'uv':True})
        parent = {'id':'parent','channel':'c','device':'d','decision':'allow','reason':'device', 'task_id':'task-one',
                  'task_scope':scope,'task_uv':True,'ts':100,'tool':'Bash','shown_sha256':'a'*64,
                  'sk':wire.b64u(key.public_key().public_bytes_raw())}
        parent['sig'] = wire.b64u(key.sign(approvals.signed_message('c','d','parent','allow','a'*64,task_scope=scope)))
        child = {'id':'child','device':'d','decision':'allow','reason':'task_grant','grant':'parent', 'task_id':'task-one',
                 'task_scope':scope,'task_uv':True,'task_action':action,'ts':110,'tool':'Bash','input_sha256':'b'*64,'cats':['delete']}
        from types import SimpleNamespace
        st = SimpleNamespace(sign_key=lambda d:key.public_key().public_bytes_raw())
        with patch.object(approvals, '_signer_state', return_value='ok', create=True):
            # Parent verification may consult enrollment files; isolate only that final enrollment lookup.
            original = approvals.check_record
            def checked(st, r, index=None):
                return 'ok' if r is parent else original(st,r,index)
            with patch.object(approvals,'check_record',side_effect=checked):
                self.assertEqual(original(st,child,{'parent':parent}),'auto')
                for changes in ({'task_id':'task-two'}, {'ts':121}, {'tool':'Delete'}, {'cats':['spend']},
                                {'input_sha256':'c'*64}, {'task_scope':scope.replace('task-one','task-two')},
                                {'task_action':{'tool':'Bash','input_sha256':'b'*64,'class':[]}},
                                {'task_action':None}):
                    self.assertEqual(original(st,{**child,**changes},{'parent':parent}),'bad',changes)

    def test_signed_uv_plan_denial_does_not_need_uv(self):
        import json
        from types import SimpleNamespace
        from agentj import wire
        key=Ed25519PrivateKey.generate(); pub=key.public_key().public_bytes_raw()
        scope=json.dumps({'uv':True})
        row={'reason':'device','channel':'c','device':'d','id':'r','decision':'deny','shown_sha256':'a'*64,
             'task_scope':scope,'task_uv':False,'sk':wire.b64u(pub)}
        row['sig']=wire.b64u(key.sign(approvals.signed_message('c','d','r','deny','a'*64,task_scope=scope)))
        self.assertEqual(approvals.check_record(SimpleNamespace(sign_key=lambda d:pub),row),'ok')

    def test_uv_ceiling_rejects_a_more_demanding_audit_child(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        action={'tool':'Bash','input_sha256':'a'*64,'class':['admin',[],[]]}
        scope=json.dumps({'task':'t','uv':False,'actions':[action],'expires':20})
        parent={'decision':'allow','reason':'device','device':'d','task_scope':scope,'task_uv':False,'task_id':'t','ts':1}
        child={'decision':'allow','reason':'task_grant','device':'d','task_scope':scope,'task_uv':True,
               'task_id':'t','ts':2,'tool':'Bash','input_sha256':'a'*64,'cats':[],'task_action':action,'grant':'p'}
        original=approvals.check_record
        with patch.object(approvals,'check_record',side_effect=lambda st,r,index=None:'ok' if r is parent else original(st,r,index)):
            self.assertEqual(original(SimpleNamespace(),child,{'p':parent}),'bad')
            self.assertEqual(original(SimpleNamespace(),{**child,'task_uv':False},{'p':parent}),'auto')
            self.assertEqual(original(SimpleNamespace(),{k:v for k,v in child.items() if k!='task_uv'},{'p':parent}),'bad')

class KeyButtons(unittest.TestCase):
    def test_management_buttons_bind_operation_and_consume_nonce_without_uv(self):
        import time
        from types import SimpleNamespace
        from unittest.mock import Mock
        from agentj import keys, controls, wire
        key=Ed25519PrivateKey.generate()
        st=SimpleNamespace(sign_key=lambda d:key.public_key().public_bytes_raw(), passkey_of_device=Mock(return_value=None))
        host=SimpleNamespace(st=st, channel='c')
        session=SimpleNamespace(device='d')
        def signed(op='import'):
            tid,n=keys.issue(host,'d')
            target={'id':tid,'op':op,'name':'SYNTHETIC_API_KEY','n':n}
            if op=='import':target.update(src='source-a',keep=True,fp='ab'*8)
            ts=int(time.time()*1000)
            sig=wire.b64u(key.sign(controls.signed_message('c','d','keys_op',n,ts,controls.object_digest('keys_op',target))))
            return {**target,'ts':ts,'sig':sig}
        for op in ('import','move','unify'):
            obj=signed(op)
            self.assertIsNone(keys.check(host,session,obj))
            self.assertIsNotNone(keys.check(host,session,obj))
        st.passkey_of_device.assert_not_called()
        for changes in ({'keep':False},{'src':'other-source'},{'name':'OTHER_API_KEY'},{'op':'delete'},{'fp':'ff'*8},{'ts':0},{'sig':'AA'}):
            self.assertIsNotNone(keys.check(host,session,{**signed(),**changes}),changes)
        # Copy and deletion still need UV; a management-button signature never approves them.
        for op in ('copy','delete'):
            obj=signed(op)
            self.assertEqual(keys.check(host,session,obj),'no_passkey')
