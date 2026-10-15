"""P320: `agentj reload` (config tiers hot / reload / restart:serve, no serve restart), the phone's /reload, the automatic
reload after an Agent's `agentj config set`, and the upgrade receipt that checks the web page's version and names the computer.
Local fake HOME only: no harness, no network, never the developer's real ~/.config/agentj."""
import _hermetic  # noqa: F401
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agentj import cli, phone_update, preferences, service, slash, update
from agentj.state import State
from _testtmp import TEST_TMP  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p320-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'coding').mkdir()
        env = patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config'),
                                     'AGENTJ_STATE_DIR': str(self.home / 'state'), 'PATH': '/usr/bin:/bin'})
        env.start(); self.addCleanup(env.stop)
        os.environ.pop('AGENTJ_SERVICE_ENV_FILE', None)
        self.st = State()
        ctl = patch('agentj.names.ctl_call', return_value=None)
        ctl.start(); self.addCleanup(ctl.stop)


class Tiers(unittest.TestCase):
    def test_schema_tiers(self):
        for k in ('channels.items', 'agent.instructions', 'agent.isolation', 'agent.allow_docker', 'agent.high_risk_warnings',
                  'agent.approvals'):
            self.assertEqual(preferences.SCHEMA[k]['apply'], 'reload', k)
        for k in ('agent.working_root', 'agent.session_mode'):
            self.assertIn(preferences.SCHEMA[k]['apply'], ('restart', 'restart:serve'), k)
        from agentj import serve
        self.assertTrue(set(serve.RELOAD_KEYS).isdisjoint(serve.RESTART_SERVE_KEYS))
        self.assertEqual({k for k in serve.RELOAD_KEYS}, {k for k, m in preferences.SCHEMA.items() if m['apply'] == 'reload'})

    def test_cli_and_help(self):
        p = cli.build_parser() if hasattr(cli, 'build_parser') else None
        if p is None:
            with patch('sys.argv', ['agentj', 'reload', '--help']), patch('sys.stdout', new_callable=io.StringIO) as out, \
                    self.assertRaises(SystemExit):
                cli.main()
            self.assertIn('reload', out.getvalue())
        self.assertIn('/reload', slash.HELP)
        self.assertIn('/reload', slash.REFUSE)


class ServiceEnv(Base):
    def test_reload_service_env_reads_known_names_only(self):
        f = self.home / 'svc.env'
        f.write_text('TELEGRAM_BOT_TOKEN="new-token"\nRANDOM_THING=x\n')
        os.chmod(f, 0o600)
        os.environ['AGENTJ_SERVICE_ENV_FILE'] = str(f)
        os.environ.pop('TELEGRAM_BOT_TOKEN', None)
        changed = service.reload_service_env({})
        self.assertEqual(changed, ['TELEGRAM_BOT_TOKEN'])
        self.assertEqual(os.environ.get('TELEGRAM_BOT_TOKEN'), 'new-token')
        self.assertNotIn('RANDOM_THING', os.environ)
        self.assertEqual(service.reload_service_env({}), [])          # unchanged → nothing reported
        os.environ.pop('TELEGRAM_BOT_TOKEN', None)

    def test_bad_mode_refused(self):
        f = self.home / 'svc.env'
        f.write_text('TELEGRAM_BOT_TOKEN=x\n'); os.chmod(f, 0o644)
        os.environ['AGENTJ_SERVICE_ENV_FILE'] = str(f)
        with self.assertRaises(service.ServiceError):
            service.reload_service_env({})

    def test_missing_file_changes_nothing(self):
        os.environ['AGENTJ_SERVICE_ENV_FILE'] = str(self.home / 'nope.env')
        self.assertEqual(service.reload_service_env({}), [])


class HostReload(Base):
    def host(self):
        from agentj.serve import Host
        self.st.init(); preferences.ensure()
        os.environ['AGENTJ_SERVICE_ENV_FILE'] = str(self.home / 'none.env')
        h = Host(self.st, read_stdin=False)
        h._send_ready = AsyncMock()
        h.configure_claude_inbound = MagicMock()
        return h

    def test_apply_marks_reload_tier(self):
        async def run():
            h = self.host()
            raw = preferences.edit(preferences.read()[0], 'agent.isolation', False)
            r = await h.apply_preferences(raw)
            self.assertTrue(r['ok'])
            self.assertEqual(r['reload'], ['agent.isolation'])
            self.assertEqual(r['needs'], ['reload'])
            self.assertFalse(r['applied'])
            self.assertIn('agent.isolation', h.reload_pending)
        asyncio.run(run())

    def test_reload_applies_file_and_restarts_owned_harness(self):
        async def run():
            h = self.host()
            h.agent_cfg = {'kind': 'claude', 'dir': str(self.home / 'coding'), 'instructions': '', 'fence': True, 'docker': False,
                           'high_risk_warnings': False, 'approvals': 'standard'}
            agent = MagicMock(); agent.cfg = h.agent_cfg; agent.owns_harness.return_value = True
            agent.request_restart.return_value = 'next_turn'
            h.agent = agent
            raw = preferences.edit(preferences.read()[0], 'agent.instructions', 'be brief')
            preferences.commit_files(self.st, raw, preferences.validate(preferences.parse(raw)))   # edited on disk, not applied
            r = await h.reload_config(trusted=True)
            self.assertTrue(r['ok'], r)
            self.assertIn('agent.instructions', r['changed'])
            self.assertEqual(h.agent_cfg['instructions'], 'be brief')
            agent.identity_changed.assert_called()
            agent.request_restart.assert_called_with('reload')
            self.assertEqual(r['harness'], {'ok': True, 'when': 'next_turn'})
            self.assertEqual(r['restart_serve'], [])
            self.assertIn('已重新加载', r['text'])
            # nothing changed the second time: no restart asked
            agent.request_restart.reset_mock()
            r2 = await h.reload_config(trusted=True)
            self.assertEqual(r2['changed'], [])
            agent.request_restart.assert_not_called()
            self.assertIn('没有新的改动', r2['text'])
        asyncio.run(run())

    def test_untrusted_reload_sends_security_keys_to_the_owner_card(self):
        async def run():
            h = self.host()
            h.ask = AsyncMock(return_value={'behavior': 'deny'})
            h.agent_cfg = {'kind': 'claude', 'dir': str(self.home / 'coding'), 'instructions': '', 'fence': True, 'docker': False,
                           'high_risk_warnings': False, 'approvals': 'standard'}
            agent = MagicMock(); agent.cfg = h.agent_cfg; agent.owns_harness.return_value = True
            h.agent = agent
            before = h.preferences
            raw = preferences.edit(preferences.read()[0], 'agent.allow_docker', True)
            raw = preferences.edit(raw, 'agent.isolation', False)
            preferences.commit_files(self.st, raw, preferences.validate(preferences.parse(raw)))
            r = await h.reload_config(trusted=False, by='agent')
            # security review F1: nothing of the unapproved file reaches the running harness while the card waits
            self.assertEqual((h.agent_cfg['fence'], h.agent_cfg['docker']), (True, False))
            agent.request_restart.assert_not_called()
            self.assertIsNone(r['harness'])
            self.assertTrue(r['ok'])
            self.assertEqual(r['pending'], ['agent.allow_docker', 'agent.isolation'])
            self.assertEqual(r['changed'], [])
            self.assertIs(h.preferences, before)                  # not applied behind the owner's back
            await asyncio.sleep(0)
            for t in list(getattr(h, '_ctl_bg', ())):
                await t
            h.ask.assert_awaited()
            self.assertFalse(preferences.get(h.preferences, 'agent.allow_docker'))
        asyncio.run(run())

    def test_approved_card_commits_the_text_it_showed(self):
        async def run():
            h = self.host()
            gate = asyncio.Event()

            async def ask(*a, **k):
                await gate.wait()
                return {'behavior': 'allow'}
            h.ask = ask
            raw = preferences.edit(preferences.read()[0], 'agent.allow_docker', True)
            preferences.commit_files(self.st, raw, preferences.validate(preferences.parse(raw)))
            r = await h.reload_config(trusted=False, by='agent')
            self.assertEqual(r['pending'], ['agent.allow_docker'])
            # the Agent edits the file again while the card waits: the approval must not carry this one in
            raw2 = preferences.edit(raw, 'agent.isolation', False)
            preferences.commit_files(self.st, raw2, preferences.validate(preferences.parse(raw2)))
            gate.set()
            for t in list(getattr(h, '_ctl_bg', ())):
                await t
            # P324 integration (P318 replay): the file now also holds an unapproved security edit → the approval applies
            # nothing (never rides along, never reverts the file) and the Agent is told to ask again
            self.assertFalse(preferences.get(h.preferences, 'agent.allow_docker'))
            self.assertTrue(preferences.get(h.preferences, 'agent.isolation'), 'the unapproved edit never lands')
        asyncio.run(run())

    def test_approved_card_applies_when_the_file_is_unchanged(self):
        async def run():
            h = self.host()
            h.ask = AsyncMock(return_value={'behavior': 'allow'})
            raw = preferences.edit(preferences.read()[0], 'agent.allow_docker', True)
            preferences.commit_files(self.st, raw, preferences.validate(preferences.parse(raw)))
            r = await h.reload_config(trusted=False, by='agent')
            self.assertEqual(r['pending'], ['agent.allow_docker'])
            await asyncio.sleep(0)
            for t in list(getattr(h, '_ctl_bg', ())):
                await t
            self.assertTrue(preferences.get(h.preferences, 'agent.allow_docker'))
            self.assertTrue(preferences.get(h.preferences, 'agent.isolation'))
        asyncio.run(run())

    def test_restart_serve_keys_named(self):
        async def run():
            h = self.host()
            other = self.home / 'other'; other.mkdir()
            raw = preferences.edit(preferences.read()[0], 'agent.working_root', str(other))
            preferences.commit_files(self.st, raw, preferences.validate(preferences.parse(raw)))
            r = await h.reload_config(trusted=True)
            self.assertEqual(r['restart_serve'], ['agent.working_root'])
            self.assertIn('agentj service restart', r['text'])
        asyncio.run(run())

    def test_env_change_rebinds_telegram(self):
        async def run():
            h = self.host()
            h.telegram = MagicMock(); h.telegram.bot_id = 42
            with patch.object(service, 'reload_service_env', return_value=['TELEGRAM_BOT_TOKEN']):
                r = await h.reload_config(trusted=True)
            self.assertEqual(r['env_changed'], 1)
            self.assertIsNone(h.telegram.bot_id)
            self.assertNotIn('TELEGRAM_BOT_TOKEN', json.dumps(r))    # counts only, no names or values to the phone
        asyncio.run(run())

    def test_phone_slash_reload_is_the_hosts_answer(self):
        async def run():
            h = self.host()
            h.cmd_turn = MagicMock(return_value=7)
            h.cmd_card = MagicMock()
            s = MagicMock(); s.name = 'Pad'; s.device = 'd1'; s.cid = 'c1'
            turn = await h.on_slash(s, 'reload', '', confirm=False, typed=True)
            self.assertEqual(turn, 7)
            name, res = h.cmd_card.call_args[0][:2]
            self.assertEqual(name, 'reload')
            self.assertEqual(res.kind, 'ok')
        asyncio.run(run())


class AutoReload(Base):
    def test_config_set_reloads_by_itself(self):
        self.st.init(); preferences.ensure()
        calls = []

        def ctl(st, req, timeout):
            calls.append(req['cmd'])
            if req['cmd'] == 'config_apply':
                return {'ok': True, 'applied': False, 'needs': ['reload'], 'reload': ['agent.isolation']}
            return {'ok': True, 'text': 'reloaded'}
        with patch('agentj.names.ctl_call', side_effect=ctl):
            r = preferences.transact(self.st, preferences.edit(preferences.read()[0], 'agent.isolation', False))
        self.assertEqual(calls, ['config_apply', 'reload'])
        self.assertTrue(r['reloaded']['ok'])
        self.assertEqual(r['needs'], [])


class Receipt(unittest.TestCase):
    def _served(self, v):
        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return patch.object(update, 'open_download', return_value=R(json.dumps({'version': v}).encode()))

    def test_web_state(self):
        with self._served('0.22.0a1'):
            self.assertEqual(update.web_state('https://m.agentj.app', '0.22.0a1'), 'same')
        with self._served('0.21.0'):
            self.assertEqual(update.web_state('https://m.agentj.app', '0.22.0a1'), 'older')
        with patch.object(update, 'open_download', side_effect=OSError('down')):
            self.assertIsNone(update.web_state('https://m.agentj.app', '0.22.0a1'))
        self.assertIsNone(update.web_state('http://evil.example', '0.22.0a1'))
        self.assertIsNone(update.web_state(None))

    def test_completion_text(self):
        rec = {'to': '0.22.0a1', 'from': '0.21.0'}
        zh = update.completion_text(rec, (10, 0, 0), 'zh', web='same', machine='office-pc')
        self.assertIn('电脑「office-pc」', zh)
        self.assertIn('刷新页面即可', zh)
        self.assertNotIn('稍后上线', zh)
        en = update.completion_text(rec, (10, 0, 0), 'en', web='same', machine='mac-mini')
        self.assertIn('"mac-mini"', en)
        self.assertIn('just refresh', en)
        self.assertIn('稍后上线', update.completion_text(rec, (10, 0, 0), 'zh', web='older'))
        unknown = update.completion_text(rec, (10, 0, 0), 'zh', web=None)
        self.assertNotIn('稍后上线', unknown)
        self.assertIn('电脑上的 Agent J 程序已升级', update.completion_text(rec, (10, 0, 0), 'zh'))   # no name known: as before
        self.assertIn('office-pc', phone_update.text({'reason': 'upgraded', **rec, 'counts': (1, 0, 0)}, 'zh', 'same', 'office-pc'))


if __name__ == '__main__':
    unittest.main()
