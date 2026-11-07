"""Offline commands metadata contract, using the existing signed-table fixture."""
import _hermetic
import contextlib
import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from test_owner_hosts_signatures import SignedTable
from agentj import owner_hosts as oh


class Commands(SignedTable):
    def request(self, **kw):
        return oh.handle_commands(SimpleNamespace(owner_hosts=self.desk),
                                  {'cmd': 'owner_hosts_commands', **kw})

    def test_active_read_only(self):
        self.fixture()
        before = [p.read_bytes() for p in self.desk._paths()]
        with patch.object(oh, 'discover_config', side_effect=AssertionError('discovery')):
            out = self.request(alias='devbox', remote='rm -rf /srv/code')
        self.assertTrue(out['ok'])
        self.assertEqual(out['scope'], self.scope)
        self.assertEqual(out['rows'][0]['ssh'], oh.command(self.target['rows'][0], 'rm -rf /srv/code'))
        self.assertEqual(out['rows'][0]['status'], 'active')
        self.assertNotIn('sig', json.dumps(out))
        self.assertIsNone(self.desk._receipt)
        self.assertEqual(before, [p.read_bytes() for p in self.desk._paths()])

    def test_invalid_and_inactive(self):
        self.fixture()
        for kw in ({'alias':'unknown'}, {'alias':[]}, {'remote':True}, {'remote':'x'*4097},
                   {'remote':''}, {'extra':1}, {'source':'-x'}, {'source':'a:b'},
                   {'destination':'code/'}, {'source':'a', 'remote':'pwd'},
                   {'source':'a', 'destination':'~/.ssh'}, {'alias':'x;pwd'}):
            with self.subTest(kw=kw): self.assertFalse(self.request(**kw)['ok'])
        with patch.object(oh.time, 'time', return_value=self.target['rows'][0]['expires']+1):
            self.assertFalse(self.request()['ok'])
        self.keys.clear()
        self.assertFalse(self.request()['ok'])

    def test_peer_and_scp(self):
        self.target['rows'][0]['peer'] = 'peer'
        self.fixture()
        peer = SimpleNamespace(same_owner=SimpleNamespace(active=lambda p: False))
        self.desk.host.peers = peer
        self.assertFalse(self.request()['ok'])
        peer.same_owner.active = lambda p: True
        out = self.request(source='./code.py', destination='code/')
        self.assertTrue(out['ok'])
        self.assertEqual(out['rows'][0]['scp'], oh.command(self.target['rows'][0], source='./code.py', destination='code/'))

    def test_cli_real_parser_json(self):
        from agentj import cli
        self.fixture()
        def call(st, req, timeout):
            return oh.handle_commands(SimpleNamespace(owner_hosts=self.desk), req)
        output = io.StringIO()
        with patch.object(cli.names, 'ctl_call', side_effect=call), patch.object(cli, 'State'), contextlib.redirect_stdout(output):
            cli.main(['owner-hosts', 'commands', 'devbox', '--json'])
        self.assertTrue(json.loads(output.getvalue())['ok'])

    def test_cli_uses_selected_instance_without_starting_service(self):
        from agentj import cli, instance, service
        self.fixture()
        output = io.StringIO()
        with patch.object(instance, 'apply', return_value=2) as select, \
                patch.object(cli.names, 'ctl_call', return_value={'ok': True, 'rows': []}) as call, \
                patch.object(cli, 'State'), patch.object(service, 'load_launch_binary_env') as load, \
                contextlib.redirect_stdout(output):
            cli.main(['--instance', '2', 'owner-hosts', 'commands', '--json'])
        select.assert_called_once_with(2)
        call.assert_called_once()
        load.assert_not_called()

    def test_cli_errors_and_parser_rejection(self):
        from agentj import cli
        for reply in (None, {'error':'unsupported'}, {'ok':False, 'error':'no_active_match', 'rows':[]}):
            output = io.StringIO()
            with patch.object(cli.names, 'ctl_call', return_value=reply), patch.object(cli, 'State'), contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as exc:
                    cli.main(['owner-hosts', 'commands', '--json'])
            self.assertEqual(exc.exception.code, 1)
            self.assertFalse(json.loads(output.getvalue())['ok'])
        with patch.object(cli.names, 'ctl_call') as call, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(['owner-hosts', 'commands', '--unknown'])
            call.assert_not_called()

    def test_hint_reachable(self):
        from pathlib import Path
        text = (Path(oh.__file__).parent / 'skills/agentj-config/SKILL.md').read_text()
        self.assertIn('agentj owner-hosts commands', text)
