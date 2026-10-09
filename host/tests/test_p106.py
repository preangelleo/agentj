import _hermetic
import json
import unittest
from unittest.mock import patch
from agentj import harness, agent
from agentj.bots.store import BotError

class ProbeTests(unittest.TestCase):
 def rows(self):
  return {'harnesses':[{'name':k,'installed':True,'supported':True,'executable':'/fixture/'+k,'logged_in':True} for k in harness.NAMES]}
 def test_real_call_required_not_file_hint_and_no_raw_return(self):
  with patch.object(harness,'detect',side_effect=self.rows),patch('agentj.bots.native.command',return_value=['fixture']),patch('agentj.bots.native.run',side_effect=[BotError('fixture_failure'), 'native-secret-canary', 'native-secret-canary']),patch('agentj.bots.native.parse',return_value=('Hello',3)):
   result=harness.probe()
  self.assertEqual(result['usable'],['codex','opencode']);self.assertEqual(result['decision'],'ask_owner')
  self.assertNotIn('native-secret-canary',json.dumps(result));self.assertFalse(result['harnesses'][0]['callable'])
 def test_empty_reply_cannot_qualify(self):
  with patch.object(harness,'detect',side_effect=self.rows),patch('agentj.bots.native.command',return_value=['fixture']),patch('agentj.bots.native.run',return_value=''),patch('agentj.bots.native.parse',return_value=('',None)):
   self.assertEqual(harness.probe()['usable'],[])
 def test_missing_never_starts_model(self):
  with patch.object(harness,'detect',return_value={'harnesses':[{'name':'codex','installed':False,'supported':True}]}),patch('agentj.bots.native.run',side_effect=AssertionError('spawn')):
   self.assertEqual(harness.probe()['decision'],'none')

class LoginNotices(unittest.TestCase):
 def test_new_login_and_only_prior_login_401_expired(self):
  for lang in ['en','zh']:
   with patch.object(harness,'codex_login',return_value=('warn','no login')):
    text=agent.codex_failure('401 Unauthorized',None,lang)[1]
    self.assertIn('not logged in yet' if lang=='en' else '还没登录 Codex',text)
   with patch.object(harness,'codex_login',return_value=('ok','file exists')):
    self.assertIn('expired' if lang=='en' else '已失效',agent.codex_failure('401 Unauthorized',None,lang)[1])
    self.assertNotIn('expired' if lang=='en' else '已失效',agent.codex_failure('Authentication required',None,lang)[1])

class OfficialRelayEnvironment(unittest.TestCase):
 def test_safe_file_only_names_owner_env_wins_and_shell_never_runs(self):
  import tempfile
  from pathlib import Path
  from agentj import relay_auth
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'.config/agentsrelay/env.sh';p.parent.mkdir(parents=True)
   p.write_text("export AGENTSRELAY_OPENAI_KEY='fixture-only'\nexport NOT_ALLOWED='no'\nexport AGENTSRELAY_CLAUDE_KEY='$(touch forbidden)'\n");p.chmod(0o600)
   e=relay_auth.environment({'HOME':d,'AGENTSRELAY_OPENAI_KEY':'owner-env'})
   self.assertEqual(e['AGENTSRELAY_OPENAI_KEY'],'owner-env');self.assertNotIn('NOT_ALLOWED',e)
   self.assertFalse((Path(d)/'forbidden').exists())
   p.chmod(0o644);self.assertNotIn('AGENTSRELAY_CLAUDE_KEY',relay_auth.environment({'HOME':d}))
   p.unlink();p.symlink_to('/nonexistent');self.assertNotIn('AGENTSRELAY_OPENAI_KEY',relay_auth.environment({'HOME':d}))

class ClaudeNativeProviderHint(unittest.TestCase):
 def test_private_native_relay_settings_are_available_without_echo(self):
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'settings.json';p.write_text(json.dumps({'env':{'ANTHROPIC_AUTH_TOKEN':'fixture-secret-only','ANTHROPIC_BASE_URL':'https://agentsrelay.net'}}));p.chmod(0o600)
   value=harness.claude_login({'CLAUDE_CONFIG_DIR':d})
   self.assertEqual(value[0],'ok');self.assertNotIn('fixture-secret-only',str(value))
   p.chmod(0o644);self.assertEqual(harness.claude_login({'CLAUDE_CONFIG_DIR':d})[0],'warn')

class NativeLauncherResolution(unittest.TestCase):
 def test_promise_in_npm_node_launcher_is_not_mise_wrapper(self):
  import tempfile
  from pathlib import Path
  from agentj import binaries
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'codex';p.write_text('#!/usr/bin/env node\n// Return a Promise for the child process.\n');p.chmod(0o700)
   self.assertFalse(binaries.wrapper(p))
   self.assertEqual(binaries.resolve('codex',{'HOME':d,'PATH':d})['path'],str(p))
