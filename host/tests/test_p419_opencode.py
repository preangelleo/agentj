import _hermetic
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import agent_opencode as oc, agent_opencode2 as oc2
from test_l1 import _state, _host

class Upgrade(unittest.IsolatedAsyncioTestCase):
    async def test_same_major_upgrade_ends_actual_owned_child_preserves_session(self):
        with tempfile.TemporaryDirectory() as root:
            st = _state(root)
            host = _host(st, [])
            host.lang = 'en'
            a = oc.OpenCodeAgent(host, {'dir':root,'fence':False,'model':'fixture/model'})
            st.set_agent_session('opencode', 'ses_keep')
            exe = Path(root)/'opencode'; exe.write_text('one')
            with mock.patch.object(oc, '_bin', return_value=str(exe)), mock.patch('agentj.harness.version_of', return_value='1.1.0'):
                await a._protocol()
            a._running_version = '1.1.0'
            proc = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time;time.sleep(300)', start_new_session=True)
            a.proc = proc; a.client = object(); a.sid = 'ses_keep'
            try:
                exe.write_text('new binary')
                with mock.patch.object(oc, '_bin', return_value=str(exe)), mock.patch('agentj.harness.version_of', return_value='1.2.0') as ver:
                    self.assertTrue(await a._protocol())
                    self.assertIsNotNone(proc.returncode)
                    self.assertIsNone(a.proc)
                    self.assertIsNone(a.client)
                    self.assertEqual(st.agent_session('opencode'), 'ses_keep')
                    self.assertFalse(await a._protocol())
                    self.assertEqual(ver.call_count, 1)
            finally:
                if proc.returncode is None: proc.kill()
                await proc.wait()

    async def test_cross_major_switch_and_failed_version_probe(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.host = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()))
        a.proc = object(); a.client = object(); a.sid = 'ses_keep'; a._running_version = '1.1.0'
        a._kill = mock.AsyncMock()
        with tempfile.NamedTemporaryFile() as exe, mock.patch.object(oc, '_bin', return_value=exe.name), mock.patch('agentj.harness.version_of', return_value=None) as ver:
            self.assertFalse(await a._protocol())
            a._kill.assert_not_called()
            self.assertFalse(hasattr(a, '_proto_key'))
            ver.return_value='2.0.30'
            self.assertTrue(await a._protocol())
            self.assertIs(type(a), oc2.OpenCodeV2Agent)
            a._kill.assert_awaited_once()
            self.assertEqual(a._upgrade_notice, '2.0.30')

    async def test_attached_owner_server_is_never_restarted(self):
        a=object.__new__(oc.OpenCodeAgent)
        a._own_protocol=lambda:False
        a._kill=mock.AsyncMock()
        with mock.patch.object(oc,'_bin',return_value='/unused'), mock.patch('agentj.harness.version_of') as ver:
            self.assertFalse(await a._protocol())
            ver.assert_not_called();a._kill.assert_not_called()


import test_p60_opencode as baseline
class Message(baseline.V2Chain):
    def test_next_message_uses_new_child_and_reports_version(self):
        async def script(c):
            await c.say('first')
            await c.wait(lambda: 'ECHO: first' in c.msgs()); await c.idle()
            old=c.host.agent.proc
            exe=Path(self.env['AGENTJ_OPENCODE_BIN'])
            exe.write_text(exe.read_text()+'# upgraded binary\n')
            with mock.patch('agentj.harness.version_of',return_value='2.0.30'):
                await c.say('after automatic upgrade')
                await c.wait(lambda: 'ECHO: after automatic upgrade' in c.msgs()); await c.idle()
            self.assertIsNot(old,c.host.agent.proc)
            self.assertIsNotNone(old.returncode)
            facts=await c.host.agent.authentication()
            self.assertEqual(facts['running_version'],'2.0.30')
            self.assertTrue(any('2.0.30' in o.get('text','') for _,o in c.sent))
        self.run_chain(script)


class Doctor(unittest.TestCase):
    def test_running_installed_mismatch_is_visible(self):
        from agentj import doctor
        with tempfile.TemporaryDirectory() as root:
            st=_state(root)
            cfg={'kind':'opencode','dir':root,'model':'fixture/model'}
            with mock.patch.object(st,'agent_config',return_value=cfg), mock.patch('agentj.service.effective_binary_environment',return_value={}), mock.patch('agentj.binaries.resolve',return_value={'path':'/var/tmp/opencode','wrapper':False,'reason':'test'}), mock.patch('agentj.doctor._version_of',return_value='2.0.30'), mock.patch('agentj.doctor._agent_bin',return_value='/var/tmp/opencode'), mock.patch('agentj.harness.opencode_login',return_value=('ok','fixture')), mock.patch('agentj.names.ctl_call',return_value={'known':True,'selected_connected':True,'running_version':'2.0.29'}):
                row=doctor.check_agent_cli(st,{})
            self.assertIn('running OpenCode 2.0.29 / installed 2.0.30',row['summary'])
            self.assertIn('version differs',row['summary'])
