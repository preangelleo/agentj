"""P467 real phone slash dispatch during an indefinitely busy OpenCode turn."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from unittest import mock
from test_p60_opencode import V2Chain


class BusyOpenCode(V2Chain):
    # Only P467 cases, not a second copy of the inherited suite.
    def test_model_and_status_do_not_wait_for_turn(self):
        async def script(c):
            await c.say('SLEEP: 99999')
            await c.wait(lambda: c.host.agent.sid is not None and c.host.agent.turn_done is not None)
            a = c.host.agent
            # The HTTP stand-in keeps the execution open; no completion event is sent.
            self.assertEqual(a.status, 'working')
            before = a.sid
            # Read the real host status over its control socket: devices carry synthetic secrets.
            import contextlib, io, json
            from types import SimpleNamespace
            from agentj import cli
            devs = self.st.devices()
            for rec in devs.values():
                rec.update(sk='fixture-status-sk', rt={'k': 'fixture-status-ticket'}, pk={'x': 'fixture-status-pk'})
            self.st.devices_path.write_text(json.dumps(devs))
            out = io.StringIO()
            with mock.patch.object(cli, 'State', return_value=self.st), contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as completed:
                    await asyncio.to_thread(cli.cmd_status, SimpleNamespace(json=True))
                self.assertEqual(completed.exception.code, 0)
            status = json.loads(out.getvalue())
            self.assertTrue(status['ok'])
            self.assertEqual(status['agent_status'], 'working')
            self.assertNotIn('sessions', status)  # P468: the Agent-facing diagnostic is a public summary
            for value in ('fixture-status-sk', 'fixture-status-ticket', 'fixture-status-pk'):
                self.assertNotIn(value, out.getvalue())
            for name in ('model', 'status', 'context', 'help'):
                n = len(c.sent)
                await c.host.on_slash(c.s, name, '', False, True)
                await c.wait(lambda: any(x.get('from') == 'cmd' and x.get('cmd') == name and x.get('kind') in ('ok', 'info')
                                         for _, x in c.sent[n:]), ms=1500)
                if name == 'model':
                    cards = [x for _, x in c.sent[n:] if x.get('from') == 'cmd' and x.get('models')]
                    self.assertTrue(cards)
            self.assertEqual(a.sid, before)
            self.assertFalse(a.turn_done.is_set())
        self.run_chain(script)

    def test_queued_model_change_expires_and_never_runs(self):
        from agentj import command_queries as cq
        async def script(c):
            await c.say('SLEEP: 99999')
            await c.wait(lambda: c.host.agent.sid is not None and c.host.agent.turn_done is not None)
            a = c.host.agent
            with mock.patch.object(cq, 'QUEUE_WAIT', .05):
                await c.host.on_slash(c.s, 'model', 'opencode/chat', False, True)
                await c.wait(lambda: any(x.get('from') == 'cmd' and 'Stop' in x.get('text', '') for _, x in c.sent), ms=1500)
            queued = list(a.q._queue)
            self.assertEqual(len(queued), 1)
            self.assertTrue(queued[0].cancelled)
            await c.host.stop_turn('test')
            a.start()
            await asyncio.sleep(.1)
            self.assertEqual(a.cfg.get('model'), 'deepseek/chat')
            self.assertFalse([r for r in self.requests() if r['m'] == 'POST' and r['p'].endswith('/model')])
        self.run_chain(script)


class BoundedQueries(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_query_and_execution_are_bounded(self):
        from types import SimpleNamespace
        from agentj import command_queries as cq
        async def stuck(*args):
            await asyncio.Event().wait()
        a = SimpleNamespace(status='working', proc=object(), command=stuck, host=SimpleNamespace(lang='en'))
        with mock.patch.object(cq, 'QUERY_WAIT', .01), mock.patch.object(cq, 'COMMAND_WAIT', .01):
            for call in (cq.query, cq.execute):
                res = await asyncio.wait_for(call(a, 'model', ''), .3)
                self.assertEqual(res.kind, 'info')
                self.assertIn('Stop', res.text)

    async def test_acp_model_list_never_reopens_active_session(self):
        from types import SimpleNamespace
        from agentj import acp
        a = acp.AcpAgent(SimpleNamespace(st=SimpleNamespace(), lang='zh'), {'dir': '/tmp'})
        a.sid, a.status = 'session-fixture', 'working'
        a.session = {'configOptions': [{'id': 'model', 'currentValue': 'fixture/model',
                                       'options': [{'value': 'fixture/model', 'name': 'Fixture'}]}]}
        a._ready = mock.AsyncMock(side_effect=AssertionError('must not reopen'))
        res = await a.cmd_model('')
        self.assertEqual(res.models[0]['id'], 'fixture/model')
        a._ready.assert_not_called()


# Avoid unittest collecting base fixture tests on the derived class.
for _name in list(vars(V2Chain)):
    if _name.startswith('test_'):
        setattr(BusyOpenCode, _name, None)
del V2Chain
