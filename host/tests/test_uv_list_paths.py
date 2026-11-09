"""Fixed control-file operands and actual UV routing; synthetic isolated paths."""
import _hermetic
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import uv_list, danger, shared_hook, policy_registry
from agentj.shared_risk import RiskGuard
import test_uv_list as uv_fixture

class Targets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name); self.project = self.home / 'project'; self.project.mkdir()
        self.state = self.home / 'state'; self.state.mkdir()
        self.env = {'HOME':str(self.home), 'AGENTJ_STATE_DIR':str(self.state), 'XDG_CONFIG_HOME':str(self.home/'.config')}
        self.patch = mock.patch.dict(os.environ, self.env); self.patch.start(); self.addCleanup(self.patch.stop)
        self.settings = self.state / 'config.json'; self.settings.write_text('{}')
        self.hook = self.home / '.claude/settings.json'; self.hook.parent.mkdir(); self.hook.write_text('{}')
    def hit(self, tool, inp):
        return uv_list.protected_targets(tool, inp, cwd=str(self.project))
    def test_direct_file_tools(self):
        for tool in ('Edit','Write','MultiEdit','Delete'):
            self.assertTrue(self.hit(tool, {'file_path':str(self.settings)}),tool)
        self.assertFalse(self.hit('Read',{'file_path':str(self.settings)}))
    def test_shell_positive_operands(self):
        for cmd in (f'echo x > {self.settings}', f'printf x >> {self.settings}',
                    f"sed -i 's/x/y/' {self.settings}", f'mv /tmp/new {self.hook}',
                    f'ln -sf /tmp/new {self.hook}', f'rm {self.settings}',
                    f'mv {self.state} /tmp/backup', f'cp /tmp/settings.json {self.hook.parent}',
                    f'cd {self.state}; echo x > config.json',
                    f"P='{self.settings}'; echo x > \"$P\""):
            self.assertTrue(self.hit('Bash',{'command':cmd}),cmd)
    def test_negative_operands_and_mentions(self):
        for cmd in (f'cat {self.settings}', f'echo {self.settings}',
                    'echo x > notes.md', 'sed -i s/x/y/ notes.md',
                    'echo x > ~/.config/systemd/user/demo.service',
                    f'echo x >> {self.state}/host.log', f'cp /tmp/report {self.home}',
                    'python -c "print(42)"', 'echo x > "$UNKNOWN"'):
            self.assertFalse(self.hit('Bash',{'command':cmd}),cmd)
    def test_python_literal_filesystem_operands(self):
        for program in (f"open({str(self.settings)!r}, 'w').write('x')",
                        f"open(file={str(self.settings)!r}, mode='w').write('x')",
                        f"from pathlib import Path; Path({str(self.hook)!r}).open('w').write('x')",
                        f"from pathlib import Path; Path({str(self.hook)!r}).write_text('x')",
                        f"import os; os.rename('/tmp/replacement', {str(self.hook)!r})"):
            import shlex
            self.assertTrue(self.hit('Bash',{'command':'python3 -c '+shlex.quote(program)}),program)
        self.assertFalse(self.hit('Bash',{'command':"python3 -c \"open('/tmp/ordinary','w').write('x')\""}))
    def test_real_glob_targets_and_quoted_home(self):
        self.assertTrue(self.hit('Bash',{'command':'echo x > ~/.claude/sett*.json'}))
        self.assertFalse(self.hit('Bash',{'command':"echo x > '$HOME/.claude/settings.json'"}))
        self.assertTrue(self.hit('Bash',{'command':'echo x > "$HOME/.claude/settings.json"'}))
        (self.project/'ordinary.txt').write_text('x')
        self.assertFalse(self.hit('Bash',{'command':'echo x > ordinary.*'}))
    def test_native_explicit_workdir(self):
        self.assertTrue(self.hit('functions.exec_command', {'cmd':'echo x > settings.json', 'workdir':str(self.hook.parent)}))
        self.assertFalse(self.hit('functions.exec_command', {'cmd':'echo x > settings.json', 'workdir':str(self.project)}))
    def test_aliases_patch(self):
        self.assertTrue(self.hit('functions.exec_command',{'cmd':f'echo x > {self.settings}'}))
        self.assertTrue(self.hit('functions.apply_patch',{'input':f'*** Begin Patch\n*** Update File: {self.settings}\n@@\n-x\n+y\n*** End Patch'}))
    def test_symlink_and_hardlink_identity(self):
        for name, link in (('sym', os.symlink),('hard',os.link)):
            dest = self.project/name; link(self.settings,dest)
            self.assertTrue(self.hit('Edit',{'file_path':str(dest)}))
    def test_loaded_package_tree_and_ancestor(self):
        root = Path(uv_list.__file__).resolve().parent
        self.assertTrue(self.hit('Edit',{'file_path':str(root/'uv_list.py')}))
        self.assertTrue(self.hit('Bash',{'command':f'rm -rf {root}'}))
    def test_fallback_refuses_protected_only(self):
        e={'tool_name':'Edit','tool_input':{'file_path':str(self.settings)},'cwd':str(self.project)}
        self.assertEqual(shared_hook.fallback('PreToolUse','codex',e,True)['permissionDecision'],'deny')
        e['tool_name']='Read'; self.assertIsNone(shared_hook.fallback('PreToolUse','codex',e,True))
    def test_independent_hook_routes_to_native_ask(self):
        event={'tool_name':'Edit','tool_input':{'file_path':str(self.settings)},'cwd':str(self.project)}
        with mock.patch('sys.stdin',io.StringIO(json.dumps(event))), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(danger.hook_main([]),0)
        self.assertEqual(json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'],'ask')

class Guard(unittest.IsolatedAsyncioTestCase):
    async def test_shared_guard_asks_only_for_fixed_target(self):
        host=SimpleNamespace(stopped=lambda:False, ask=mock.AsyncMock(return_value={'behavior':'deny','message':'denied'}))
        adapter=SimpleNamespace(host=host,cfg={'dir':'/synthetic/project'},kind='opencode',proc=SimpleNamespace(pid=123),sid='synthetic-session')
        guard=RiskGuard(adapter)
        self.assertFalse(await guard.check('Edit',{'file_path':str(Path(uv_list.__file__).resolve())}))
        host.ask.assert_awaited_once()
        host.ask.reset_mock()
        self.assertTrue(await guard.check('Bash',{'command':'systemctl --user restart ordinary'}))
        host.ask.assert_not_awaited()

class SharedHookWiring(unittest.IsolatedAsyncioTestCase):
    async def test_shared_claude_pretool_reaches_host_verification(self):
        from agentj import shared
        host=mock.Mock(); host.stopped.return_value=False
        host.ask=mock.AsyncMock(return_value={'behavior':'deny','message':'Owner verification required'})
        with tempfile.TemporaryDirectory() as tmp:
            host.st.root=Path(tmp)/'state'
            adapter=shared.SharedClaudeAgent(host,{'kind':'claude','dir':tmp,'_workflow_ceo':True,'high_risk_warnings':False,'approvals':'native'})
            adapter.session={'sessionId':'owner'}
            adapter.native_source={'kind':'terminal','role':'owner'}
            result=await adapter.hook_event({'session_id':'owner','hook_event_name':'PreToolUse','tool_name':'Edit',
                'tool_input':{'file_path':str(host.st.root/'devices.json')}})
            self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
            host.ask.assert_awaited_once()
    async def test_opencode_v2_pretool_is_guarded(self):
        host=mock.Mock();host.stopped.return_value=False
        host.ask=mock.AsyncMock(return_value={'behavior':'deny','message':'Owner verification required'})
        adapter=SimpleNamespace(host=host,cfg={'dir':'/synthetic','shared_session_id':'owner'},kind='opencode',v2=True)
        guard=RiskGuard(adapter)
        with mock.patch('agentj.main_thread.of') as main:
            main.return_value.check.return_value={}
            result=await guard.hook_event({'session_id':'owner','hook_event_name':'PreToolUse','tool_name':'Edit',
                'tool_input':{'file_path':str(Path(uv_list.__file__).resolve())}})
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
        host.ask.assert_awaited_once()

class ProtectedApproval(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = uv_fixture.ListApproval.asyncSetUp
    open = uv_fixture.ListApproval.open
    close = uv_fixture.ListApproval.close
    async def test_actual_host_state_target_requires_uv(self):
        task,a=await self.open('Edit',{'file_path':str(self.st.devices_path)})
        self.assertTrue(a.require_passkey)
    async def test_native_protected_file_card_requires_uv(self):
        task,a=await self.open('Edit',{'file_path':str(Path(uv_list.__file__).resolve())})
        self.assertTrue(a.require_passkey)
        obj=self.ph.answer(self.h._ask_msg(a,self.s),True);obj.pop('fa')
        await self.h._answer(self.s,obj);self.assertFalse(a.fut.done())
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        self.assertEqual((await task)['behavior'],'allow')

class NativeGuard(unittest.IsolatedAsyncioTestCase):
    def make(self):
        from agentj.shared_risk import NativePathGuard
        host=SimpleNamespace(st=SimpleNamespace(root='/synthetic/state'),
                             ask=mock.AsyncMock(return_value={'behavior':'allow'}))
        adapter=SimpleNamespace(host=host,cfg={'dir':'/synthetic/project'},kind='codex',tid='owner',proc=SimpleNamespace(pid=123))
        return NativePathGuard(adapter),host
    async def test_native_authenticated_protected_ask_preserves_harness(self):
        guard,host=self.make()
        event={'hook_event_name':'PreToolUse','tool_name':'Edit','tool_input':{'file_path':'/synthetic/state/devices.json'},
               'cwd':'/synthetic/project','session_id':'owner','_agentj_chain':[456,123]}
        self.assertTrue(guard.authentic(event));self.assertTrue(event['_agentj_path_authenticated'])
        self.assertEqual(await guard.hook_event(event),{})
        host.ask.assert_awaited_once()
    async def test_native_foreign_session_and_process_deny(self):
        for change in ({'session_id':'foreign'}, {'_agentj_chain':[456]}, {'cwd':'/foreign'}):
            guard,host=self.make()
            event={'hook_event_name':'PreToolUse','tool_name':'Edit','tool_input':{'file_path':'/synthetic/state/devices.json'},
                   'cwd':'/synthetic/project','session_id':'owner','_agentj_chain':[456,123],**change}
            guard.authentic(event)
            self.assertEqual((await guard.hook_event(event))['hookSpecificOutput']['permissionDecision'],'deny')
            host.ask.assert_not_awaited()
    async def test_native_ordinary_tool_keeps_native_permissions(self):
        guard,host=self.make()
        event={'hook_event_name':'PreToolUse','tool_name':'Edit','tool_input':{'file_path':'/synthetic/project/notes.md'}}
        self.assertEqual(await guard.hook_event(event),{})
        host.ask.assert_not_awaited()
