"""A workflow receives only its host State's existing fixed report socket selector."""
import _hermetic
import ast
import os
import pathlib
import socket
import tempfile
import unittest
from unittest.mock import patch
from _testtmp import TEST_TMP
from agentj import fence
from agentj.state import State


class WorkflowEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP);self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        self.st=State(self.root/'seat-one')
        self.st.perm_dir.mkdir(parents=True,mode=0o700)
        self.endpoint=self.st.perm_dir/'elevate.sock'
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.addCleanup(self.sock.close)
        self.sock.bind(str(self.endpoint));self.endpoint.chmod(0o600)
        self.base={'HOME':str(self.root),'PATH':'/usr/bin:/bin','AGENTJ_ELEVATE_SOCK':'/tmp/forged-foreign.sock','AGENTJ_CAPABILITY_SOCK':'/tmp/foreign-capability.sock','AGENTJ_RECALL_SOCK':'/tmp/foreign-recall.sock','AGENTJ_FRIENDS_SOCK':'/tmp/foreign-friends.sock','UNRELATED_SECRET_CANARY_API_KEY':'synthetic-unused'}

    def environment(self,st):
        return fence.auxiliary_environment(self.base,'claude',host_state=st)

    def test_default_context_keeps_prior_filtering(self):
        self.assertNotIn('AGENTJ_ELEVATE_SOCK',fence.auxiliary_environment(self.base,'claude'))

    def test_each_workflow_harness_gets_only_canonical_socket_through_both_layers(self):
        for kind in ('claude','codex','opencode'):
            with self.subTest(kind=kind):
                aux=fence.auxiliary_environment(self.base,kind,host_state=self.st)
                final=fence.launch_environment(aux,False,kind)
                self.assertEqual(final['AGENTJ_ELEVATE_SOCK'],str(self.endpoint))
                for name in ('AGENTJ_CAPABILITY_SOCK','AGENTJ_RECALL_SOCK','AGENTJ_FRIENDS_SOCK','UNRELATED_SECRET_CANARY_API_KEY'):
                    self.assertNotIn(name,aux)
                    self.assertNotIn(name,final)

    def test_wrong_seat_or_non_state_never_inherits_other_endpoint(self):
        for st in (State(self.root/'wrong-seat'),object(),str(self.st.root)):
            with self.subTest(kind=type(st).__name__):self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(st))

    def test_forged_selector_for_another_valid_same_uid_seat_is_replaced(self):
        other=State(self.root/'seat-two');other.perm_dir.mkdir(parents=True,mode=0o700)
        endpoint=other.perm_dir/'elevate.sock'
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.addCleanup(sock.close)
        sock.bind(str(endpoint));endpoint.chmod(0o600)
        self.base['AGENTJ_ELEVATE_SOCK']=str(endpoint)
        self.assertEqual(self.environment(self.st)['AGENTJ_ELEVATE_SOCK'],str(self.endpoint))

    def test_missing_socket_wrong_type_and_modes_refuse(self):
        for mode in (0o644,0o660,0o000):
            self.endpoint.chmod(mode)
            self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(self.st))
        self.endpoint.chmod(0o600)
        self.st.perm_dir.chmod(0o755)
        self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(self.st))
        self.st.perm_dir.chmod(0o700)
        self.endpoint.unlink()
        self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(self.st))
        self.endpoint.write_text('not a socket');self.endpoint.chmod(0o600)
        self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(self.st))

    def test_foreign_socket_or_directory_uid_refuses(self):
        original=os.lstat
        for target in (self.endpoint,self.st.perm_dir):
            def foreign(path,*a,**kw):
                result=original(path,*a,**kw)
                if pathlib.Path(path)==target:
                    values=list(result);values[4]=os.getuid()+1
                    return os.stat_result(values)
                return result
            with self.subTest(target=target.name),patch.object(os,'lstat',side_effect=foreign):
                self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(self.st))

    def test_socket_directory_and_root_symlinks_refuse(self):
        real=self.endpoint.with_name('real.sock');self.endpoint.rename(real);self.endpoint.symlink_to(real)
        self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(self.st))
        self.endpoint.unlink();real.rename(self.endpoint)
        directory=self.st.perm_dir.with_name('real-agentperm');self.st.perm_dir.rename(directory);self.st.perm_dir.symlink_to(directory,target_is_directory=True)
        self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(self.st))
        self.st.perm_dir.unlink();directory.rename(self.st.perm_dir)
        alias=self.root/'seat-alias';alias.symlink_to(self.st.root,target_is_directory=True)
        self.assertNotIn('AGENTJ_ELEVATE_SOCK',self.environment(State(alias)))

    def test_four_host_workflow_callers_pass_their_own_state(self):
        folder=pathlib.Path(fence.__file__).parent
        for name in ('tasks.py','agent.py','agent_codex.py','agent_opencode.py'):
            tree=ast.parse((folder/name).read_text())
            calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='auxiliary_environment']
            self.assertEqual(len(calls),1,name)
            state=next((kw.value for kw in calls[0].keywords if kw.arg=='host_state'),None)
            expected='host.st' if name=='tasks.py' else 'self.host.st'
            self.assertIsNotNone(state,name)
            self.assertEqual(ast.unparse(state),expected,name)
