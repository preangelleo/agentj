"""Isolated explicit-function UV boundary; no live service or real commands."""
import argparse
import asyncio
import json
import os
import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import functions


class State:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.key = 'test-public-signing-key'
        self.events = []

    def sign_key(self, device):
        return self.key if device == 'paired' else None

    def check_perms(self):
        pass

    def log(self, *args, **kw):
        self.events.append((args, kw))


class Functions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aj-fn-')
        self.addCleanup(self.temp.cleanup)
        self.st = State(self.temp.name)
        self.host = SimpleNamespace(st=self.st, stopped=lambda: False, stopping=threading.Event())
        self.receipt = {'behavior': 'allow', 'grant_info': {'uv': True, 'rid': 'once', 'device': 'paired', 'sign_pub': self.st.key}}
        self.host.ask = mock.AsyncMock(side_effect=lambda *args, **kwargs: self.receipt)
        self.req = {'t': 'fn', 'op': 'add', 'name': 'payments', 'argv': ['echo', 'example'], 'cwd': self.temp.name}

    async def call(self, **changes):
        return await functions.handle(self.host, {**self.req, **changes}, peer=123)

    async def register(self):
        res = await self.call()
        self.assertTrue(res['ok'], res)
        return res

    async def test_register_run_remove_require_fresh_uv(self):
        await self.register()
        self.assertEqual(os.stat(self.st.root / 'functions.json').st_mode & 0o777, 0o600)
        run = await functions.handle(self.host, {'t': 'fn', 'op': 'run', 'name': 'payments'}, peer=123)
        self.assertTrue(run['ok'], run)
        self.assertEqual(run['function']['argv'], ['echo', 'example'])
        remove = await functions.handle(self.host, {'t': 'fn', 'op': 'remove', 'name': 'payments'}, peer=123)
        self.assertTrue(remove['ok'], remove)
        self.assertEqual(self.host.ask.await_count, 3)
        for call in self.host.ask.await_args_list:
            self.assertTrue(call.kwargs['require_passkey'])
            self.assertTrue(call.kwargs['always_ask'])
            self.assertTrue(call.kwargs['grant_info'])
            self.assertFalse(call.kwargs['batch'])
        self.assertEqual(functions._load(self.st), {})

    async def test_negative_receipts_never_register(self):
        for answer in ({'behavior': 'allow'}, {'behavior': 'deny', 'reason': 'timeout'},
                       {'behavior': 'deny', 'reason': 'no_device'},
                       {'behavior': 'allow', 'grant_info': {'uv': False}},
                       {'behavior': 'allow', 'grant_info': {'uv': True, 'rid': 'once', 'device': 'forged', 'sign_pub': self.st.key}}):
            self.receipt = answer
            self.assertFalse((await self.call())['ok'])
            self.assertFalse((self.st.root / 'functions.json').exists())

    async def test_revocation_after_uv_fails(self):
        async def revoked(*args, **kw):
            old = dict(self.receipt['grant_info'])
            self.st.key = 'replacement'
            return {'behavior': 'allow', 'grant_info': old}
        self.host.ask.side_effect = revoked
        self.assertFalse((await self.call())['ok'])

    async def test_registry_change_during_uv_fails(self):
        async def changed(*args, **kw):
            functions._save(self.st, {'payments': functions.record('payments', ['echo', 'changed'], self.temp.name)})
            return self.receipt
        self.host.ask.side_effect = changed
        res = await self.call()
        self.assertEqual(res['why'], 'function_changed_during_approval')

    async def test_run_changed_during_uv_fails(self):
        await self.register()
        async def changed(*args, **kw):
            functions._save(self.st, {'payments': functions.record('payments', ['echo', 'changed'], self.temp.name)})
            return self.receipt
        self.host.ask.side_effect = changed
        res = await functions.handle(self.host, {'t': 'fn', 'op': 'run', 'name': 'payments'}, peer=123)
        self.assertFalse(res['ok'])

    async def test_expired_or_device_denied_run_never_returns_argv(self):
        await self.register()
        for reason in ('timeout', 'device', 'estop', 'serve_stop'):
            self.receipt = {'behavior': 'deny', 'reason': reason}
            res = await functions.handle(self.host, {'t': 'fn', 'op': 'run', 'name': 'payments'}, peer=123)
            self.assertFalse(res['ok'])
            self.assertNotIn('function', res)

    async def test_list_readonly_duplicate_and_shape(self):
        await self.register()
        self.host.ask.reset_mock()
        res = await functions.handle(self.host, {'t': 'fn', 'op': 'list'}, peer=123)
        self.assertEqual(res['functions'][0]['name'], 'payments')
        self.host.ask.assert_not_awaited()
        self.assertEqual((await self.call())['why'], 'already_registered_remove_first')
        self.assertFalse((await self.call(origin='owner'))['ok'])
        self.assertFalse((await functions.handle(self.host, self.req, peer=None))['ok'])

    async def test_secret_argv_refused_without_persistence(self):
        res = await self.call(argv=['program', '--password', 'actual-password-value'])
        self.assertFalse(res['ok'])
        self.host.ask.assert_not_awaited()
        self.assertFalse((self.st.root / 'functions.json').exists())

    async def test_estop_after_uv_fails(self):
        self.host.stopping.set()
        self.assertFalse((await self.call())['ok'])

    async def test_symlink_registry_refused(self):
        target = self.st.root / 'other.json'
        target.write_text('{}')
        (self.st.root / 'functions.json').symlink_to(target)
        self.assertFalse((await self.call())['ok'])
        self.assertEqual(target.read_text(), '{}')

    def test_command_executes_only_approved_argv_in_caller(self):
        a = SimpleNamespace(action='run', name='payments')
        approved = functions.record('payments', ['echo', 'a; rm example'], self.temp.name)
        fake_elevate = SimpleNamespace(client_request=mock.Mock(return_value={'ok': True, 'function': approved}))
        fake_names = SimpleNamespace(elevate_sock=lambda st: None)
        fake_state = SimpleNamespace(State=lambda: self.st)
        with mock.patch.dict('sys.modules', {'agentj.elevate': fake_elevate, 'agentj.names': fake_names, 'agentj.state': fake_state}), \
             mock.patch.multiple('agentj', elevate=fake_elevate, names=fake_names, create=True), \
             mock.patch('subprocess.run', return_value=SimpleNamespace(returncode=7)) as run:
            self.assertEqual(functions.command(a), 7)
            run.assert_called_once_with(approved['argv'], cwd=self.temp.name, shell=False)

    def test_uv_list_reports_registered_and_offline_truthfully(self):
        import contextlib
        import io
        fake_state = SimpleNamespace(State=lambda: self.st)
        fake_names = SimpleNamespace(elevate_sock=lambda st: None)
        fake_uv = SimpleNamespace(entries=lambda: [{'command': 'agentj fn run', 'reason': 'registered command'}],
                                  protected_paths=lambda **kw: ['synthetic-protected-control'])
        for response, expected in (({'ok': True, 'functions': [{'name': 'payments', 'digest': 'test-digest'}]}, 0),
                                   ({'result': 'unavailable'}, 2)):
            out = io.StringIO()
            fake_elevate = SimpleNamespace(client_request=lambda *args, **kw: response)
            with mock.patch.dict('sys.modules', {'agentj.elevate': fake_elevate, 'agentj.names': fake_names,
                                                 'agentj.state': fake_state, 'agentj.uv_list': fake_uv}), \
                 mock.patch.multiple('agentj', elevate=fake_elevate, names=fake_names, uv_list=fake_uv, create=True), contextlib.redirect_stdout(out):
                self.assertEqual(functions.uv_command(None), expected)
            value = json.loads(out.getvalue())
            self.assertEqual(value['protected_paths'], ['synthetic-protected-control'])
            self.assertEqual(value['registered_functions_available'], expected == 0)
            if expected == 0:
                self.assertEqual(value['entries'][-1]['command'], 'agentj fn run payments')

    def test_parser_run_does_not_accept_extra_parameters(self):
        p = argparse.ArgumentParser()
        functions.add_parser(p.add_subparsers())
        a = p.parse_args(['fn', 'add', 'payments', '--', 'echo', 'hello'])
        self.assertEqual(a.argv, ['echo', 'hello'])
        with self.assertRaises(SystemExit):
            p.parse_args(['fn', 'run', 'payments', 'extra'])


if __name__ == '__main__':
    unittest.main()
