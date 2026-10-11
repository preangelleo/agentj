"""Shared slash controls: isolated registry, real PTY bytes and native hook receipts."""
import _hermetic
import asyncio
import os
from pathlib import Path
import pty
import tempfile
import tty
import unittest
from unittest.mock import Mock, patch, AsyncMock
from agentj import shared, shared_controls, shared_codex, claude_statusline
from agentj.serve import Host
from agentj.history import History
from agentj.agent_opencode import OpenCodeAgent
from agentj.agent_opencode2 import OpenCodeV2Agent
from agentj.shared_opencode2 import OwnerOpenCodeV2Agent
from agentj.slash import Result
from agentj.state import State

OLD = '11111111-1111-1111-1111-111111111111'
NEW = '22222222-2222-2222-2222-222222222222'

class Controls(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = Mock(); self.host.st.root = self.root
        self.host.st.agent_session.return_value = None
        self.a = shared.SharedClaudeAgent(self.host, {'kind':'claude', 'dir':str(self.root), 'session_mode':'shared','_workflow_ceo':True})
        self.a.session = {'sessionId':OLD,'pid':123,'status':'idle'}
        self.a.read_statusline = Mock()
        self.registry = patch.object(shared, 'claude_sessions', side_effect=lambda d, sid='': [{**self.a.session,'sessionId': sid or OLD}])
        self.registry.start(); self.addCleanup(self.registry.stop)
        master, slave = pty.openpty(); tty.setraw(slave)
        self.a.master = master; self.a.native = Mock(pid=123)
        self.slave = slave; os.set_blocking(slave,False)
        self.addCleanup(os.close,master); self.addCleanup(os.close,slave)
    def read(self):
        try: return os.read(self.slave,1000)
        except BlockingIOError: return b''
    async def run_control(self,name,source):
        task=asyncio.create_task(self.a.command(name,''))
        await asyncio.sleep(.15)
        self.assertEqual(self.read(),('/'+name+'\r').encode())
        self.assertFalse(task.done(), 'PTY write is not success')
        await self.a.hook_event({'hook_event_name':source,'source':name,'session_id':NEW if name=='clear' else OLD})
        res=await task
        self.assertEqual(res.kind,'ok');self.assertFalse(res.undo)
        return res
    async def test_phone_clear_changes_session_and_invalidates_meter_on_receipt(self):
        await self.run_control('clear','SessionStart')
        self.assertEqual(self.a.session['sessionId'],NEW)
        self.assertEqual(self.a.transcript.name,NEW+'.jsonl')
        self.host.shared_clear.assert_called_once()
        self.host.meter_update.assert_called_with(ctx=None,source_at=None)
        self.a.read_statusline.assert_called_once()
    async def test_compact_requires_postcompact_not_precompact_or_session_start(self):
        task=asyncio.create_task(self.a.command('compact','')); await asyncio.sleep(.15);self.read()
        for ev in ['PreCompact','SessionStart']:
            await self.a.hook_event({'hook_event_name':ev,'source':'compact','session_id':OLD})
            self.assertFalse(task.done())
        await self.a.hook_event({'hook_event_name':'PostCompact','session_id':OLD})
        self.assertEqual((await task).kind,'ok');self.host.shared_clear.assert_not_called()
    async def test_p127_precompact_is_compacting_on_the_phone_at_once(self):
        # P127: shared mode used to keep the phone's /compact page at 「已送达，等待电脑接手…」 and the water still for the
        # whole compaction — PreCompact only reached AgentState. It is the phone's `compacting` status now, until the end.
        task=asyncio.create_task(self.a.command('compact','')); await asyncio.sleep(.15); self.read()
        self.assertEqual(self.a.status,'idle')
        await self.a.hook_event({'hook_event_name':'PreCompact','trigger':'manual','session_id':OLD})
        self.assertEqual(self.a.status,'compacting'); self.host.agent_status.assert_called_with('compacting')
        await self.a.hook_event({'hook_event_name':'SessionStart','source':'compact','session_id':OLD})
        self.assertEqual(self.a.status,'idle')
        await self.a.hook_event({'hook_event_name':'PostCompact','session_id':OLD})
        self.assertEqual((await task).kind,'ok'); self.assertEqual(self.a.status,'idle')
        self.host.meter_update.assert_any_call(ctx=None,source_at=None)   # the real reading comes back afterwards
    async def test_p127_desktop_and_auto_compaction_restore_the_previous_status(self):
        await self.a.hook_event({'hook_event_name':'PreCompact','trigger':'manual','session_id':OLD})   # typed on the desktop
        self.assertEqual(self.a.status,'compacting')
        await self.a.hook_event({'hook_event_name':'PostCompact','session_id':OLD})
        self.assertEqual(self.a.status,'idle')
        self.a.status='working'                                   # auto-compaction inside a phone turn
        await self.a.hook_event({'hook_event_name':'PreCompact','trigger':'auto','session_id':OLD})
        self.assertEqual(self.a.status,'compacting')
        self.a.state._end_compact()                               # the stale timer / statusline json ends it, no hook
        self.a.sync_compacting()
        self.assertEqual(self.a.status,'working')
        await self.a.hook_event({'hook_event_name':'PreCompact','session_id':'33333333-3333-3333-3333-333333333333'})
        self.assertEqual(self.a.status,'working', 'a foreign session never moves this status')
    async def test_busy_desktop_queues_without_esc(self):
        self.a.state.status='working'; self.a.session['status']='busy'
        task=asyncio.create_task(self.a.command('clear',''));await asyncio.sleep(.15)
        self.assertEqual(self.read(),b'')
        self.a.state.status='idle'; self.a.session['status']='idle';await asyncio.sleep(.2)
        self.assertEqual(self.read(),b'/clear\r')
        await self.a.hook_event({'hook_event_name':'SessionStart','source':'clear','session_id':NEW})
        self.assertEqual((await task).kind,'ok')
    async def test_timeout_keeps_history_and_water(self):
        with patch.object(shared,'CONTROL_WAIT',.01):
            res=await self.a.command('clear','')
        self.assertEqual(res.kind,'error');self.assertIn('没清成',res.text)
        self.host.shared_clear.assert_not_called();self.host.meter_update.assert_not_called()
    async def test_stop_cancels_waiting_control_without_typing(self):
        self.a.state.status='working';self.a.session['status']='busy'
        task=asyncio.create_task(self.a.command('clear',''));await asyncio.sleep(.05)
        self.a.state.status='idle';self.a.session['status']='idle'
        await self.a.halt(clear_queue=False)
        self.assertEqual((await task).kind,'error');self.assertEqual(self.read(),b'')
        self.host.shared_clear.assert_not_called()
    async def test_foreign_clear_does_not_reset(self):
        with patch.object(shared,'claude_sessions',return_value=[{'pid':999}]),patch.object(shared.asyncio,'sleep',new=AsyncMock()):
            await self.a.hook_event({'hook_event_name':'SessionStart','source':'clear','session_id':NEW})
        self.host.shared_clear.assert_not_called();self.assertEqual(self.a.session['sessionId'],OLD)
    async def test_desktop_clear_reset_once_and_new_transcript(self):
        ev={'hook_event_name':'SessionStart','source':'clear','session_id':NEW}
        await self.a.hook_event(ev);await self.a.hook_event(ev)
        self.host.shared_clear.assert_called_once()
    async def test_no_terminal_route_gives_specific_alternative(self):
        self.a.native=None
        with patch.object(shared_controls,'send',return_value='no_exact_pane'):
            res=await self.a.command('clear','')
        self.assertIn('no_exact_pane',res.text);self.assertEqual(res.kind,'error')
        self.host.shared_clear.assert_not_called()
    async def test_explicit_new_selector_replaces_an_older_clear_redirect(self):
        self.a.clear_pin=OLD;self.host.st.agent_session.return_value='previous-selection'
        await self.a.hook_event({'hook_event_name':'SessionStart','source':'clear','session_id':NEW})
        self.host.st.set_agent_session.assert_any_call('claude.clear_from',OLD)
    async def test_control_injection_refused(self):
        for name,arg in [('clear','anything'),('compact','\n/logout'),('model','unsafe')]:
            self.assertEqual((await self.a.command(name,arg)).kind,'refused')
        self.assertEqual(self.read(),b'')
    async def test_codex_clear_never_claims_to_clear_desktop_thread(self):
        a=shared_codex.SharedCodexAgent(self.host,{'kind':'codex','dir':str(self.root),'session_mode':'shared','_workflow_ceo':True})
        self.assertEqual((await a.command('clear','')).kind,'refused')
        self.host.st.set_agent_session.assert_not_called();self.host.meter_update.assert_not_called()

class OpenCodeControls(unittest.IsolatedAsyncioTestCase):
    async def test_attached_v1_v2_compact_and_reads_use_native_api_without_switch(self):
        for cls,base in [(shared.SharedOpenCodeAgent,OpenCodeAgent),(OwnerOpenCodeV2Agent,OpenCodeV2Agent)]:
            host=Mock();host.st.agent_session.return_value='sesOwner'
            a=cls(host,{'kind':'opencode','dir':'/var/tmp/work','session_mode':'shared','_workflow_ceo':True,'high_risk_warnings':False})
            a.owned_server=False;a.sid='sesOwner';host.reset_mock()
            if hasattr(a,'_initial'):a._initial=AsyncMock(return_value=True)
            with patch.object(base,'command',new=AsyncMock(return_value=Result('已压缩'))) as native,patch.object(a,'context_meter',new=AsyncMock()) as read:
                res=await a.command('compact','')
                self.assertEqual(res.kind,'ok');self.assertEqual(native.await_count,1);self.assertEqual(native.await_args.args[-2:],('compact',''));read.assert_awaited_once_with(include_quota=False)
                host.meter_update.assert_called_with(ctx=None)
                for cmd in ['clear','undo_clear','model']:
                    self.assertEqual((await a.command(cmd,'')).kind,'error')
                self.assertEqual(a.sid,'sesOwner');host.st.set_agent_session.assert_not_called()
    async def test_attached_failed_compact_keeps_water(self):
        host=Mock();a=shared.SharedOpenCodeAgent(host,{'kind':'opencode','dir':'/var/tmp/work','session_mode':'shared','_workflow_ceo':True})
        host.reset_mock()
        with patch.object(OpenCodeAgent,'command',new=AsyncMock(return_value=Result('failed','error'))):
            self.assertEqual((await a.command('compact','')).kind,'error');host.meter_update.assert_not_called()

class HistoryReset(unittest.TestCase):
    def test_native_clear_archives_epoch_and_page_count(self):
        with tempfile.TemporaryDirectory() as root:
            st=State(Path(root));hist=History(st);hist.add({'k':'host','text':'old'},'answer','done')
            old=hist.epoch;h=Mock(hist=hist);Host.shared_clear(h)
            self.assertEqual(hist.meta()['count'],0);self.assertEqual(hist.epoch,old+1)
            self.assertTrue(hist.archives());self.assertTrue(hist.undo_reset())
    def test_archive_failure_does_not_claim_phone_clear(self):
        h=Mock();h.hist.meta.return_value={'count':59};h.hist.reset.return_value=None
        self.assertFalse(Host.shared_clear(h));h.agent_notice.assert_called_once()
    def test_exact_session_cache_survives_idle_and_missing_context_after_compact(self):
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/'claude-statusline'/f'{OLD}.json';file.parent.mkdir()
            file.write_text(__import__('json').dumps({'session_id':OLD,'h5':{'pct':1},'ctx':{'pct':50}}));os.utime(file,(1000,1000))
            with patch.object(claude_statusline,'load_meta',return_value={'enabled':True,'installed':{'type':'command'}}),patch('agentj.claude_inbound.read',return_value={'statusLine':{'type':'command'}}):
                self.assertEqual(claude_statusline.read(root,OLD)['h5']['pct'],1)
                self.assertIsNone(claude_statusline.read(root,OLD,1001)['ctx'])
                self.assertIsNone(claude_statusline.read(root,NEW)['h5'])

class ExactPane(unittest.TestCase):
    def test_only_exact_idle_pane_can_receive_controls(self):
        with patch.object(shared_controls,'herdr_bin',return_value='herdr'),patch.object(shared_controls,'locate',return_value=('pane','blocked')),patch.object(shared_controls,'_run') as run:
            self.assertEqual(shared_controls.send({'sessionId':OLD},'/clear'),'not_idle');run.assert_not_called()
            self.assertEqual(shared_controls.send({},'/clear\n/logout'),'refused')
