import _hermetic  # noqa: F401
import asyncio
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from agentj import file_show, provenance, shared_risk
from agentj.peer_service import PeerService
from test_l1 import _host, _state


class CodexFileAdmission(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'work'
        self.root.mkdir()
        (self.root / 'fixture.txt').write_text('isolated fixture')
        st = _state(temp.name)
        st.set_agent_config('codex', str(self.root), fence=False, record_root=False)
        self.host = _host(st, [])
        self.host.lang = 'en'
        self.addCleanup(self.host.stopping.set)
        page = self.host.hist_add({'k': 'you'}, '', 'open')
        self.host.cur_turn = page['id']
        self.host.peers = PeerService(self.host)
        send = NS(turn=page['id'], source=provenance.phone())
        proc = NS(pid=1000, returncode=None)
        self.agent = self.host.agent = NS(kind='codex', cfg={'dir': str(self.root)}, tid='thread', turn_id='turn',
                                         proc=proc, turn_proc=proc, host=self.host, cur_send=send)
        self.host.peers.xfer.begin_execution(send)
        self.broker = self.host.peers.xfer.broker
        self.guard = shared_risk.NativePathGuard(self.agent)
        self.notices = []
        self.host.agent_notice = self.notices.append

    def event(self, tool='Bash', inp=None, **kw):
        return {'hook_event_name': 'PreToolUse', 'tool_name': tool,
                'tool_input': inp if inp is not None else {'command': 'agentj file show fixture.txt --json'},
                'session_id': 'thread', 'turn_id': 'turn', 'tool_use_id': 'exec-fixture',
                'cwd': str(self.root), '_agentj_path_authenticated': True, **kw}

    def receipt(self):
        return self.broker.receipt(file_show.request('fixture.txt', cwd=str(self.root)))

    def native_started(self):
        self.broker.observe_shell_start('exec-fixture', ('thread', 'turn'),
                                        ('agentj file show fixture.txt --json', str(self.root)))

    async def test_native_child_shape_and_exact_successful_completion(self):
        self.assertEqual(self.receipt()['why'], 'native_admission_missing')
        self.assertEqual(await self.guard.hook_event(self.event()), {})
        self.assertNotEqual(self.receipt()['result'], 'queued')
        self.native_started()
        receipt = self.receipt()
        self.assertEqual(receipt['result'], 'queued')
        self.broker.complete('exec-fixture', True, receipt, native=('thread', 'wrong'),
                             native_shell=('agentj file show fixture.txt --json', str(self.root)))
        await self.broker.drain()
        self.assertEqual(self.notices, [])
        self.broker.complete('exec-fixture', True, receipt, native=('thread', 'turn'),
                             native_shell=('agentj file show fixture.txt --json', str(self.root)))
        await self.broker.drain()
        self.assertEqual(len(self.notices), 1)
        self.assertIn('attached_to_page', self.notices[0])

    async def test_missing_wrong_call_and_compound_are_never_admitted(self):
        cmd = 'agentj file show fixture.txt --json'
        for event in (self.event('exec', {'code': 'text(await tools.exec_command({cmd: "' + cmd + '"}))'}),
                      self.event(inp={'command': cmd + ' | cat'}),
                      self.event(inp={'command': cmd + ' > saved'}),
                      self.event(inp={'command': cmd + ' && true'}),
                      self.event(**{'_agentj_path_authenticated': False}),
                      self.event(**{'turn_id': 'old'}),
                      self.event(parent_tool_use_id='child')):
            await self.guard.hook_event(event)
            self.assertEqual(self.receipt()['why'], 'native_admission_missing')

    async def test_exact_completion_requires_same_command_and_cwd(self):
        await self.guard.hook_event(self.event())
        self.native_started()
        receipt = self.receipt()
        self.assertEqual(receipt['result'], 'queued')
        self.broker.complete('exec-fixture', True, receipt, native=('thread', 'turn'),
                             native_shell=('agentj file show another.txt --json', str(self.root)))
        await self.broker.drain()
        self.assertEqual(self.notices, [])

    async def test_socket_receipt_waits_for_the_exact_native_start(self):
        event = self.event()
        await self.guard.hook_event(event)
        self.assertEqual((await self.broker.execute('Bash', event['tool_input']))['behavior'], 'deny')
        req = file_show.request('fixture.txt', cwd=str(self.root))
        receipt = asyncio.create_task(self.broker.file_receipt(req))
        await asyncio.sleep(0)
        self.assertFalse(receipt.done())
        self.broker.observe_shell_start('other-call', ('thread', 'turn'),
                                        ('agentj file show fixture.txt --json', str(self.root)))
        await asyncio.sleep(0)
        self.assertFalse(receipt.done())
        self.native_started()
        self.assertEqual((await asyncio.wait_for(receipt, 1))['result'], 'queued')

    async def test_unstarted_call_cannot_hold_up_a_valid_retry(self):
        await self.guard.hook_event(self.event(tool_use_id='not-run'))
        await self.guard.hook_event(self.event())
        req = file_show.request('fixture.txt', cwd=str(self.root))
        receipt = asyncio.create_task(self.broker.file_receipt(req))
        await asyncio.sleep(0)
        self.native_started()
        self.assertEqual((await asyncio.wait_for(receipt, 1))['result'], 'queued')

    async def test_wechat_inbound_keeps_existing_file_source_boundary(self):
        self.agent.cur_send.source = provenance.wechat_owner()
        verdict = await self.guard.hook_event(self.event())
        self.assertEqual(verdict['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(self.receipt()['why'], 'native_admission_missing')

    async def test_generated_image_hint_is_owner_only(self):
        generated_root = self.root.parent / '.codex' / 'generated_images' / 'thread'
        generated_root.mkdir(parents=True)
        self.agent.image_home = str(generated_root.parent.parent)
        command = f'agentj file send {shlex.quote(str(generated_root / "fixture.png"))} --to wechat --json'
        event = self.event(inp={'command': command})
        answer = await self.guard.hook_event(event)
        self.assertIn('copy', answer['hookSpecificOutput']['permissionDecisionReason'])
        self.assertNotIn(str(generated_root), str(answer))
        self.agent.cur_send.source = provenance.wechat_task('fixture')
        event['tool_use_id'] = 'another-call'
        answer = await self.guard.hook_event(event)
        self.assertNotIn('copy', answer['hookSpecificOutput']['permissionDecisionReason'])
