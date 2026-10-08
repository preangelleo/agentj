"""P80 shared Claude default, explicit owner policy and one-time phone notice."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import claude_inbound as inbound, preferences, cli
from agentj.state import State
from agentj.serve import Host, Session

class Defaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {'HOME': str(self.home), 'CLAUDE_CONFIG_DIR': str(self.home/'cc'),
              'XDG_CONFIG_HOME': str(self.home/'config'), 'AGENTJ_STATE_DIR': str(self.home/'st')})
        env.start(); self.addCleanup(env.stop)
        self.st = State(); self.st.init(); self.st.set_agent_config('claude', str(self.home))
        preferences.ensure()
        # Root bootstrap installs hooks; start these ingress cases with no native setting.
        if inbound.path().exists(): inbound.path().unlink()
    def test_default_backup_once_and_pending_notice_survives_restart(self):
        p = inbound.path(); p.parent.mkdir(exist_ok=True); raw = b'{"other":1}\n'; p.write_bytes(raw)
        self.assertTrue(inbound.ensure_shared_default(self.st)); self.assertEqual(inbound.value(),'accept')
        self.assertEqual(inbound.read()['other'], 1)
        backups = list(p.parent.glob('settings.json.agentj-backup-*'))
        self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_bytes(),raw)
        self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)
        self.assertFalse(inbound.ensure_shared_default(State()))
        self.assertIn('已开启',inbound.default_notice(State()))
        inbound.notice_delivered(self.st)
        self.assertIsNone(inbound.default_notice(State()))
        self.assertEqual(len(list(p.parent.glob('settings.json.agentj-backup-*'))),1)
    def test_explicit_values_preserved_including_previous_off(self):
        for val in ('hold','refuse','accept','invalid',None):
            p=inbound.path();p.parent.mkdir(exist_ok=True);p.write_text(json.dumps({'crossSessionInbound':val}))
            self.assertFalse(inbound.ensure_shared_default(self.st))
            self.assertEqual(inbound.read()['crossSessionInbound'],val)
            self.assertIsNone(inbound.default_notice(self.st))
        inbound.set_enabled(False); self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertEqual(inbound.value(),'hold')
    def test_only_shared_claude_touches_settings(self):
        for cfg in ({'kind':'codex','session_mode':'shared'}, {'kind':'opencode','session_mode':'shared'},
                    {'kind':'claude','session_mode':'independent'},None):
            with patch.object(self.st,'agent_config',return_value=cfg):
                self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertFalse(inbound.path().exists())
    def test_unsafe_settings_preserved(self):
        p=inbound.path();p.parent.mkdir(exist_ok=True);p.write_text('invalid')
        with self.assertRaises(inbound.SettingsError):inbound.ensure_shared_default(self.st)
        self.assertEqual(p.read_text(),'invalid')
        p.unlink();target=p.parent/'owner';target.write_text('{}');p.symlink_to(target)
        with self.assertRaises(inbound.SettingsError):inbound.ensure_shared_default(self.st)
        self.assertEqual(target.read_text(),'{}')
    def test_off_after_migration_suppresses_stale_enabled_notice(self):
        inbound.ensure_shared_default(self.st);inbound.set_enabled(False)
        self.assertIsNone(inbound.default_notice(self.st))
        self.assertFalse(json.loads((self.st.root/'claude-inbound-notice.json').read_text())['pending'])
    def test_cli_selection_initializes_unset_without_prompt(self):
        args=SimpleNamespace(mode='claude',dir=str(self.home),model=None,unfenced=False,allow_docker=False)
        with patch('agentj.wizard.bootstrap_root'),patch('agentj.fence.problem',return_value=None),patch('builtins.print'):
            cli.cmd_agent(args)
        self.assertEqual(inbound.value(),'accept');self.assertIsNotNone(inbound.default_notice(self.st))
    def test_offline_switch_automatically_enables(self):
        with patch('agentj.names.ctl_call',return_value=None),patch('agentj.claude_auth.available',return_value=True):
            preferences.transact(self.st, preferences.edit(preferences.read()[0], 'agent.session_mode','independent'))
            self.assertFalse(inbound.path().exists())
            preferences.transact(self.st, preferences.edit(preferences.read()[0], 'agent.session_mode','shared'))
        self.assertEqual(inbound.value(),'accept')

class Runtime(unittest.IsolatedAsyncioTestCase):
    setUp=Defaults.setUp
    async def test_startup_migration_notice_is_history_once_in_either_language(self):
        for lang in ('zh','en'):
            if inbound.path().exists():inbound.path().unlink()
            h=Host(self.st,events='quiet');h.lang=lang;h.send_app=AsyncMock()
            h._push_key=Mock();h._post=Mock()
            with patch('agentj.serve.webpush.vapid_public',return_value=b'fixture'):
                h.configure_claude_inbound()
                self.assertEqual(inbound.value(),'accept')
                s=Session(1,state='ready',device='fixture',pub=b'fixture');h.sessions[1]=s
                await h.on_ready(s,0)
                turns,_=h.hist.page(); notices=[t for t in turns if 'claude-inbound off' in str(t)]
                self.assertEqual(len(notices),1)
                self.assertEqual('已开启' in str(notices[0]),lang=='zh')
                before=len(turns);await h.on_ready(s,0)
                self.assertEqual(len(h.hist.page()[0]),before)
                # next host preserves the same history and does not append another announcement
                again=Host(self.st,events='quiet');again.configure_claude_inbound()
                self.assertIsNone(inbound.default_notice(self.st))
                h.hist.purge()
    async def test_live_switch_shared_and_native_override(self):
        h=Host(self.st,events='quiet');h._send_ready=AsyncMock();h.host_info=Mock(return_value={})
        with patch('agentj.claude_auth.available',return_value=True):
            independent=preferences.edit(preferences.read()[0],'agent.session_mode','independent')
            self.assertTrue((await h.apply_preferences(independent))['ok'])
            self.assertFalse(inbound.path().exists())
            shared=preferences.edit(independent,'agent.session_mode','shared')
            self.assertTrue((await h.apply_preferences(shared))['ok'])
        self.assertEqual(inbound.value(),'accept')
        inbound.set_enabled(False);h.configure_claude_inbound();self.assertEqual(inbound.value(),'hold')
    async def test_unreadable_settings_emit_safe_fallback(self):
        p=inbound.path();p.parent.mkdir(exist_ok=True);p.write_text('private invalid contents')
        h=Host(self.st,events='quiet');h.agent_notice=Mock();h.configure_claude_inbound()
        self.assertIn('claude-inbound on',h.agent_notice.call_args.args[0])
        self.assertNotIn('private invalid contents',h.agent_notice.call_args.args[0])
        self.assertEqual(p.read_text(),'private invalid contents')
