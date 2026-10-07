"""P75: default compact pairing and custom-state elevation socket from a fenced client."""
import _hermetic
import asyncio
import json
import os
import pathlib
import tempfile
import unittest
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from agentj import elevate, serve, wire
from agentj.state import State
from test_l1 import _host, _state

class P75(unittest.IsolatedAsyncioTestCase):
    async def test_pair_defaults_compact_with_explicit_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            h = _host(st, [])
            h.relay_up = True
            links = []
            async def capture(w, obj):
                if obj.get('ev') == 'link':
                    links.append(obj['link'])
            h._ctl_send = capture
            for value in (None, '1', '0'):
                env = dict(os.environ)
                env.pop('AGENTJ_PAIR_COMPACT', None)
                if value is not None:
                    env['AGENTJ_PAIR_COMPACT'] = value
                r = asyncio.StreamReader(); r.feed_eof()
                with patch.dict(os.environ, env, clear=True):
                    await h._ctl_pair(r, None)
                self.assertEqual(wire.parse_pairing_link(links[-1])['channel'], h.channel)
                self.assertEqual(links[-1].split('#p=')[1].isdigit(), value != '0')

    async def test_custom_socket_export_client_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            h = _host(st, [])
            e = elevate.Elevator(h)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(elevate.ENV, None)
                await e.start()
                try:
                    self.assertEqual(os.environ[elevate.ENV], str(e.sock_path))
                    # The fence unsets STATE_DIR: the CLI's State() is now a different path.
                    default = State(pathlib.Path(d) / 'fence-default')
                    result = await asyncio.to_thread(elevate.client_request, default, {'t':'invalid'}, 2)
                    self.assertNotEqual(result.get('result'), 'unavailable')
                finally:
                    await e.stop()
                self.assertNotIn(elevate.ENV, os.environ)
                self.assertFalse(e.sock_path.exists())

    async def test_stop_preserves_another_hosts_socket_export(self):
        with tempfile.TemporaryDirectory() as d:
            e = elevate.Elevator(_host(_state(d), []))
            with patch.dict(os.environ, {}, clear=False):
                await e.start()
                os.environ[elevate.ENV] = '/another/agentperm/elevate.sock'
                await e.stop()
                self.assertEqual(os.environ[elevate.ENV], '/another/agentperm/elevate.sock')
