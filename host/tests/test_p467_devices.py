"""P467 public device diagnostics; private records never become CLI output."""
import _hermetic  # noqa: F401
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import cli


class Diagnostics(unittest.TestCase):
    def test_local_source_and_unknown_nested_values(self):
        from agentj.devnames import public_device
        row = public_device('remote-1', {'source': 'local', 'name': {'sk': 'fixture-nested'},
                                        'sk': 'fixture-device-private', 'rt': {'k': 'fixture-ticket'}})
        self.assertEqual(row['source'], 'local')
        self.assertEqual(row['name'], '')
        for value in ('fixture-nested', 'fixture-device-private', 'fixture-ticket'):
            self.assertNotIn(value, json.dumps(row))

    def test_devices_public_projection_and_human_output(self):
        private = {'sk': 'fixture-private-device', 'rt': {'k': 'fixture-private-resume'},
                   'pk': {'x': 'fixture-passkey-material'}, 'future_secret': 'fixture-future-secret'}
        record = dict(private, name='Phone', self_name='Safari', owner_name='Phone', paired_at=123,
                      seen=456, source='account')
        st = mock.Mock()
        st.devices.return_value = {'remote-1': record}
        with mock.patch.object(cli, 'State', return_value=st), mock.patch.object(cli, '_need_init'), \
                mock.patch.object(cli, '_ctl_quiet', return_value={'sessions': [{'device': 'remote-1', 'state': 'ready'}]}), \
                mock.patch.object(cli, '_name_or_unset', return_value='Agent'), mock.patch.object(cli, '_link_line', return_value='Linked'):
            for as_json in (True, False):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cli.cmd_devices(SimpleNamespace(json=as_json))
                text = out.getvalue()
                for value in ('fixture-private-device', 'fixture-private-resume', 'fixture-passkey-material', 'fixture-future-secret'):
                    self.assertNotIn(value, text)
                if as_json:
                    row = json.loads(text)['remote-1']
                    self.assertFalse(set(private) & set(row))
                    self.assertEqual((row['id'], row['name'], row['online'], row['has_passkey']),
                                     ('remote-1', 'Phone', True, True))
                    self.assertEqual(row['seen'], 456)

    def test_friends_json_uses_view_not_private_record(self):
        import tempfile
        from agentj import friends, peer_service
        from test_l1 import _state
        with tempfile.TemporaryDirectory() as root:
            st = _state(root)
            store = friends.FriendStore(st)
            private = {'sk': 'fixture-friend-sk', 'rt': {'k': 'fixture-friend-ticket'}, 'pk': 'fixture-friend-pk'}
            store._write('friends.json', {'friend-1': dict(private, id='friend-1', card={'name': 'Friend'})})
            view = {'ok': True, **store.list_view()}
            out = io.StringIO()
            # Same producer as PeerService.list_view -> cli(list), with no peer connection.
            with mock.patch.object(peer_service, 'ask', return_value=view), contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                peer_service.cmd_friends(SimpleNamespace(fcmd='list', json=True, json_sub=False))
            result = json.loads(out.getvalue())
            self.assertEqual(result['friends'][0]['name'], 'Friend')
            for k in private:
                self.assertNotIn(k, result['friends'][0])
            for value in ('fixture-friend-sk', 'fixture-friend-ticket', 'fixture-friend-pk'):
                self.assertNotIn(value, out.getvalue())

    def test_instances_json_counts_devices_without_expanding_them(self):
        import tempfile
        from agentj import instance, names, service
        from test_l1 import _state
        with tempfile.TemporaryDirectory() as root:
            st = _state(root)
            st.devices_path.write_text(json.dumps({'remote-1': {'name': 'Phone', 'sk': 'fixture-device-sk',
                                         'rt': {'k': 'fixture-device-ticket'}, 'pk': {'x': 'fixture-device-pk'}}}))
            out = io.StringIO()
            with mock.patch.object(instance, 'numbers', return_value=[1]), \
                    mock.patch.object(instance, 'state_root', return_value=st.root), \
                    mock.patch.object(instance, 'label', return_value='Instance'), \
                    mock.patch.object(service, 'status', return_value={'active': 'active'}), \
                    mock.patch.object(names, 'ctl_call', return_value={'relay_up': True, 'sessions': []}), contextlib.redirect_stdout(out):
                cli.cmd_instances(SimpleNamespace(action='list', json=True))
            self.assertEqual(json.loads(out.getvalue())[0]['remotes'], 1)
            for value in ('fixture-device-sk', 'fixture-device-ticket', 'fixture-device-pk'):
                self.assertNotIn(value, out.getvalue())
