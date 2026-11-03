import _hermetic
from _testtmp import TEST_TMP
import io
import asyncio
import sys
import json
import os
from pathlib import Path
from types import SimpleNamespace as N
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
from agentj import cli, host_diagnostics as hd


class DevicesHostRoute(unittest.TestCase):
    def test_fenced_devices_use_host_before_hidden_state(self):
        with patch.object(hd, 'request', return_value=0) as request, patch.object(cli, 'State', side_effect=AssertionError('hidden state must not be opened')):
            with self.assertRaises(SystemExit) as result:
                cli.cmd_devices(N(json=True))
        self.assertEqual(result.exception.code, 0)
        request.assert_called_once_with('devices', as_json=True)

    def test_main_devices_skip_identity_migration_in_agent(self):
        from agentj import instance, service, relay_auth, migrate
        with patch.dict(os.environ, {'AGENTJ_INSTANCE':'2'}), patch.object(instance, 'apply', return_value=2), \
             patch.object(instance, 'exists', return_value=True), patch.object(service, 'user_runtime_dir'), \
             patch.object(service, 'load_launch_binary_env'), patch.object(relay_auth, 'load'), \
             patch.object(hd, 'agent_side', return_value=True), patch.object(migrate, 'auto', side_effect=AssertionError('must not migrate')), \
             patch.object(hd, 'request', return_value=0) as request, patch.object(cli, 'cmd_devices') as command:
            with self.assertRaises(SystemExit) as result:
                cli.main(['devices', '--json'])
        self.assertEqual(result.exception.code, 0)
        command.assert_not_called()
        request.assert_called_once_with('devices', as_json=True)

    def test_instance_two_host_state_online_and_no_secret_projection(self):
        from agentj.state import State
        from agentj import names
        with tempfile.TemporaryDirectory(prefix='source-devices-', dir=TEST_TMP) as td:
            st = State(Path(td)/'.local/state/agentj-2')
            st.init()
            fake = {'dev-two':{'name':'Seat two phone', 'pk':{'private':'not-exported'}, 'resume':'not-exported', 'paired_at':1}}
            st.write_private(st.devices_path, json.dumps(fake).encode())
            with patch.dict(os.environ, {'AGENTJ_INSTANCE':'2', 'HOME':td}), patch.object(st, 'devices', side_effect=AssertionError('must use snapshot')), \
                 patch.object(st, 'migrate_device_names', side_effect=AssertionError('readonly')), \
                 patch.object(names, 'ctl_call', return_value={'ok':True,'sessions':[{'device':'dev-two','state':'ready'}]}):
                result = hd.execute(st, {'op':'devices','as_json':True,'selected_instance':2})
            self.assertEqual(result['exit'], 0)
            data = json.loads(result['output'])
            self.assertTrue(data['dev-two']['online'])
            self.assertNotIn('not-exported', result['output'])
            self.assertEqual(result['host_instance'], 2)

    def test_cross_instance_or_selector_fields_refused_before_state(self):
        state = Mock()
        for req in ({'op':'devices','as_json':True,'selected_instance':1},
                    {'op':'devices','as_json':True,'selected_instance':2,'root':'/other-seat'},
                    {'op':'devices','as_json':True,'selected_instance':2,'state':'foreign'},
                    {'op':'devices','as_json':True,'selected_instance':True}):
            with patch.dict(os.environ, {'AGENTJ_INSTANCE':'2'}):
                result = hd.execute(state, req)
            self.assertEqual(result['exit'], 1)
        state.devices.assert_not_called()

    def test_explicit_other_instance_is_refused_without_transport(self):
        from agentj import elevate
        with patch.dict(os.environ, {'AGENTJ_INSTANCE':'2', elevate.ENV:'/owned/seat-two.sock'}), patch.object(elevate, 'client_request') as transport, redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as result:
                cli.main(['--instance','1','devices','--json'])
        self.assertEqual(result.exception.code, 1)
        transport.assert_not_called()
        self.assertIn('host_state_unavailable', out.getvalue())

    def test_invalid_devices_args_do_not_run_host_bootstrap(self):
        from agentj import service
        with patch.dict(os.environ, {'AGENTJ_INSTANCE':'2'}), patch.object(hd, 'agent_side', return_value=True), \
             patch.object(service, 'user_runtime_dir', side_effect=AssertionError('bootstrap')), redirect_stdout(io.StringIO()), patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as result:
                cli.main(['devices','--json','--root','foreign'])
        self.assertEqual(result.exception.code, 2)

    def test_no_exported_endpoint_does_not_guess_other_seat_socket(self):
        from agentj import elevate
        with patch.dict(os.environ, {'AGENTJ_INSTANCE':'2'}), patch.object(hd, 'agent_side', return_value=True), patch.object(elevate, 'client_request') as transport, redirect_stdout(io.StringIO()) as out:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(elevate.ENV, None)
                self.assertEqual(hd.request('devices', as_json=True), 1)
        transport.assert_not_called()
        self.assertIn('host_state_unavailable', out.getvalue())

    def test_host_projection_does_not_redirect_global_stdout(self):
        from agentj import names
        from agentj.state import State
        with tempfile.TemporaryDirectory(prefix='source-devices-output-', dir=TEST_TMP) as td, patch.dict(os.environ, {'HOME':td}):
            st = State(Path(td)/'.local/state/agentj')
            st.init()
            outer=io.StringIO()
            original=cli.cmd_devices
            def rendering(*args, **kwargs):
                self.assertIs(sys.stdout, outer)
                print('OTHER_THREAD_FIXED_CANARY')
                return original(*args, **kwargs)
            with redirect_stdout(outer), patch.object(names, 'ctl_call', return_value={'ok':True,'sessions':[]}), patch.object(cli, 'cmd_devices', side_effect=rendering):
                result=hd.execute(st, {'op':'devices','as_json':True,'selected_instance':1})
            self.assertEqual(result['exit'], 0)
            self.assertNotIn('OTHER_THREAD_FIXED_CANARY', result['output'])
            self.assertIn('OTHER_THREAD_FIXED_CANARY', outer.getvalue())

    def test_bad_online_shapes_and_root_symlink_are_unavailable(self):
        from agentj import names
        from agentj.state import State
        with tempfile.TemporaryDirectory(prefix='source-devices-shapes-', dir=TEST_TMP) as td, patch.dict(os.environ, {'HOME':td, 'AGENTJ_INSTANCE':'2'}):
            st=State(Path(td)/'.local/state/agentj-2')
            st.init()
            for status in ({'ok':1,'sessions':[]}, {'ok':True,'sessions':{}}, {'ok':True,'sessions':[{'state':'ready'}]}):
                with patch.object(names, 'ctl_call', return_value=status):
                    result=hd.execute(st, {'op':'devices','as_json':True,'selected_instance':2})
                self.assertEqual(result['exit'], 1)
            alias=Path(td)/'.local/state/agentj-3'
            alias.symlink_to(st.root, target_is_directory=True)
            result=hd.execute(State(alias), {'op':'devices','as_json':True,'selected_instance':3})
            self.assertEqual(result['exit'], 1)

    def test_client_rejects_boolean_host_instance(self):
        from agentj import elevate
        with patch.dict(os.environ, {'AGENTJ_INSTANCE':'1', elevate.ENV:'/owned.sock'}), patch.object(elevate, 'client_request', return_value={'host_instance':True,'exit':0,'output':'PRIVATE_LIST'}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hd.request('devices', as_json=True), 1)
        self.assertNotIn('PRIVATE_LIST', out.getvalue())

    def test_custom_host_root_and_missing_devices_state_are_unavailable(self):
        from agentj import names
        from agentj.state import State
        with tempfile.TemporaryDirectory(prefix='source-devices-roots-', dir=TEST_TMP) as td, patch.dict(os.environ, {'HOME':td, 'AGENTJ_INSTANCE':'2'}):
            for root in (Path(td)/'custom', Path(td)/'.local/state/agentj-2'):
                st = State(root)
                with patch.object(names, 'ctl_call', return_value={'ok':True,'sessions':[]}):
                    result = hd.execute(st, {'op':'devices','as_json':True,'selected_instance':2})
                self.assertEqual(result['exit'], 1)
                self.assertIn('host_state_unavailable', result['output'])

    def test_unreachable_host_returns_unavailable_never_init(self):
        from agentj import elevate
        out = io.StringIO()
        with patch.dict(os.environ, {'AGENTJ_INSTANCE':'2',elevate.ENV:'/missing/seat-two.sock'}), \
             patch.object(elevate, 'client_request', side_effect=FileNotFoundError), redirect_stdout(out):
            result = hd.request('devices', as_json=True)
        self.assertEqual(result, 1)
        data=json.loads(out.getvalue())
        self.assertEqual(data['error'], 'host_state_unavailable')
        self.assertNotIn('agentj init', out.getvalue())
        self.assertNotIn('instance 1', out.getvalue())

class DeviceSocket(unittest.IsolatedAsyncioTestCase):
    async def test_real_host_socket_child_hidden_state_instance_two(self):
        from agentj import elevate, diskq
        from agentj.state import State
        with tempfile.TemporaryDirectory(prefix='d-', dir=TEST_TMP) as td, patch.dict(os.environ, {'HOME':td, 'AGENTJ_INSTANCE':'2'}):
            st = State(Path(td)/'.local/state/agentj-2')
            st.init()
            st.write_private(st.devices_path, json.dumps({'dev-two':{'name':'phone','paired_at':1,'pk':{'fake':'not-exported'}}}).encode())
            await diskq.aflush()
            el = elevate.Elevator(N(st=st))
            await el.start()
            async def ctl(reader, writer):
                await reader.readline()
                writer.write(b'{"ok":true,"sessions":[{"device":"dev-two","state":"ready"}]}\n')
                await writer.drain()
                writer.close()
            server = await asyncio.start_unix_server(ctl, path=str(st.sock_path))
            try:
                code = "from agentj.state import State; State.exists=lambda self: (_ for _ in ()).throw(AssertionError('client State')); from agentj import cli; cli.main(['devices','--json'])"
                proc = await asyncio.create_subprocess_exec(sys.executable, '-c', code, env=dict(os.environ), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(proc.communicate(), 15)
                self.assertEqual(proc.returncode, 0, err.decode())
                self.assertTrue(json.loads(out)['dev-two']['online'])
                self.assertNotIn('not-exported', out.decode())
            finally:
                server.close()
                await server.wait_closed()
                await el.stop()

if __name__ == '__main__': unittest.main()
