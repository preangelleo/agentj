"""Launch contract regressions; no real harness, account or credential is used."""
import _hermetic
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock, AsyncMock
from agentj.agent import ClaudeAgent
from agentj.agent_codex import CodexAgent
from test_slash import _Chain
from agentj import approval_level, approvals, preferences
from test_p336_approval_level import FA, face_id, sign


class NativeFloor(unittest.TestCase):
    def test_claude_hook_cannot_be_disabled_by_preferences(self):
        for cfg in ({}, {'high_risk_warnings':False}, {'approvals':'native'}, {'approval_level':'full'}, {'_workflow_ceo':True}):
            with self.subTest(cfg=cfg):
                a=ClaudeAgent(None, {'kind':'claude','dir':'/tmp',**cfg})
                argv=a.argv(None)
                settings=json.loads(argv[argv.index('--settings')+1])
                self.assertFalse(settings.get('disableAllHooks', True))
                self.assertEqual(settings['hooks']['PreToolUse'][0]['matcher'],'*')
                self.assertIn('agentj.danger hook',settings['hooks']['PreToolUse'][0]['hooks'][0]['command'])
                self.assertNotIn('--dangerously-skip-permissions',argv)

    def test_codex_policy_floor_preserves_each_inherited_sandbox(self):
        for sandbox in ('read-only','workspace-write','danger-full-access'):
            for policy in ('never','on-request','untrusted',{'granular':{'rules':False}}):
                for cfg in ({}, {'high_risk_warnings':False}, {'approvals':'native'}, {'approval_level':'full'}):
                    with self.subTest(sandbox=sandbox,policy=policy,cfg=cfg):
                        a=CodexAgent(None,{'kind':'codex','dir':'/tmp',**cfg})
                        a.human={'sandbox_mode':sandbox,'approval_policy':policy,'approvals_reviewer':'auto_review'}
                        got=a.policy()
                        self.assertEqual(got.get('approvalPolicy'),'untrusted')
                        self.assertEqual(got.get('approvalsReviewer'),'user')
                        self.assertEqual(got['sandbox'],sandbox)

    def test_research_keeps_read_only(self):
        a=CodexAgent(None,{'kind':'codex','dir':'/tmp'},research=True)
        a.human={'sandbox_mode':'danger-full-access','approval_policy':'never'}
        self.assertEqual(a.policy()['sandbox'],'read-only')
        a=ClaudeAgent(None,{'kind':'claude','dir':'/tmp','_research':True})
        argv=a.argv(None,research=True)
        settings=json.loads(argv[argv.index('--settings')+1])
        self.assertIn(' research',settings['hooks']['PreToolUse'][0]['hooks'][0]['command'])


class BlockedHooks(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_hook_refuses_before_launch_even_with_warnings_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'.claude').mkdir()
            (root/'.claude/settings.json').write_text('{"disableAllHooks":true}')
            host=SimpleNamespace(st=SimpleNamespace(log=Mock(),agent_session=Mock(return_value=None)))
            a=ClaudeAgent(host,{'kind':'claude','dir':tmp,'high_risk_warnings':False})
            a.local_fail=Mock()
            with patch('agentj.agent._bin',return_value='/fixture/claude'), \
                 patch('agentj.agent._MANAGED',()), \
                 patch.dict('os.environ',{'CLAUDE_CONFIG_DIR':str(root/'empty-config')}), \
                 patch.object(a,'authorized_launch_argv',new_callable=AsyncMock,side_effect=AssertionError('disabled hooks reached launch authorization')) as launch:
                self.assertFalse(await a._spawn())
                self.assertTrue(a.failed_start)
                launch.assert_not_awaited()
                self.assertIsNone(a.proc)
                self.assertIn('disableAllHooks',a.local_fail.call_args.args[0])


class ThreadRequests(unittest.IsolatedAsyncioTestCase):
    async def test_new_and_resumed_threads_send_same_review_floor(self):
        for stored in (None, 'fixture-thread'):
            with self.subTest(stored=stored):
                host=SimpleNamespace(st=SimpleNamespace(agent_session=Mock(return_value=stored)))
                a=CodexAgent(host, {'kind':'codex','dir':'/tmp','_workflow_ceo':True})
                a.human={'sandbox_mode':'workspace-write','approval_policy':'never','approvals_reviewer':'auto_review'}
                a.call=AsyncMock(return_value={'thread':{'id':'fixture-thread'},'sandbox':{'type':'workspaceWrite'}})
                a.record_session=Mock();a.cur_model=Mock(return_value=None);a.meter=Mock();a.models_cache=[{"id":"fixture-model"}]
                self.assertTrue(await a._thread())
                method,fields=a.call.call_args.args
                self.assertEqual(method,'thread/resume' if stored else 'thread/start')
                self.assertEqual(fields['sandbox'],'workspace-write')
                self.assertEqual(fields['approvalPolicy'],'untrusted')
                self.assertEqual(fields['approvalsReviewer'],'user')

    async def test_restricted_sandbox_cannot_be_widened_by_phone_review(self):
        from agentj.agent_codex import beyond_sandbox
        sb={'type':'workspaceWrite','writableRoots':['/fixture/work'],'networkAccess':False}
        self.assertIsNotNone(beyond_sandbox(sb,'item/commandExecution/requestApproval','Bash',{'command':'touch x'},{},None,'/fixture/work'))
        self.assertIsNotNone(beyond_sandbox(sb,'item/permissions/requestApproval','CodexPermissions',{}, {},None,'/fixture/work'))
        self.assertIsNone(beyond_sandbox(sb,'item/commandExecution/requestApproval','Bash',{'command':'pwd'},{},None,'/fixture/work'))


class StatusReview(unittest.IsolatedAsyncioTestCase):
    async def test_independent_status_does_not_claim_granular_is_effective(self):
        host=SimpleNamespace(st=SimpleNamespace(agent_session=Mock(return_value='fixture-thread')))
        a=CodexAgent(host,{'kind':'codex','dir':'/tmp'})
        a.proc=object()
        a.human={'sandbox_mode':'workspace-write','approval_policy':{'granular':{'rules':False}}}
        with patch('agentj.codex_perm.config_info',return_value={}):
            text=(await a.cmd_status('')).text
        self.assertIn('untrusted',text)
        self.assertNotIn('agentj 没改',text)
        self.assertEqual(a.human['approval_policy'],{'granular':{'rules':False}})
        for lang, wanted, absent in (
            ('en', 'Agent J requests user review; native sandbox boundaries remain in force', '请求用户审查'),
            ('zh', 'Agent J 请求用户审查；保留原生沙箱边界', 'requests user review'),
            ('en', 'Agent J requests user review; native sandbox boundaries remain in force', '请求用户审查'),
        ):
            host.lang = lang
            with patch('agentj.codex_perm.config_info', return_value={}):
                text = (await a.cmd_status('')).text
            self.assertIn(wanted, text)
            self.assertNotIn(absent, text)


class _LaunchReview(_Chain):
    """Real Host lifecycle, child process, hook/RPC and signed phone refusal.

    Only the vendor executable/relay transport is a local stand-in. Full's
    credential red line is used because ordinary deletion is allowed by Full.
    """
    def setUp(self):
        super().setUp()
        pref = preferences.path()
        original = pref.read_text()
        self.addCleanup(pref.write_text, original)
        text = preferences.edit(original, "agent.high_risk_warnings", False)
        pref.write_text(preferences.edit(text, "agent.approvals", "native"))
        (self.work / ".env").write_text("synthetic fixture only\n")
        if self.KIND == "claude":
            rules = self.work / "allow.json"
            rules.write_text(json.dumps({"permissions": {"allow": ["Bash(rm:*)"]}}))
            self.env["FAKE_CLAUDE_USER_SETTINGS"] = str(rules)
            os.environ.update(self.env)
        else:
            self.env.update(FAKE_CX_POLICY='"never"', FAKE_CX_SANDBOX="danger-full-access")
            os.environ.update(self.env)

    def check_launch_and_resume(self):
        for level in ("standard", "full"):
            self.st.set_agent_session(self.KIND, None)
            for resumed in (False, True):
                with self.subTest(level=level, resumed=resumed):
                    stored = self.st.agent_session(self.KIND)
                    if resumed:
                        self.assertTrue(stored)
                        if self.KIND == "codex":
                            os.environ["FAKE_CX_THREADS"] = json.dumps([stored])
                    before = len(self.logged())

                    async def script(c):
                        host = c["host"]
                        self.assertFalse(host.agent.cfg["high_risk_warnings"])
                        self.assertEqual(host.agent.cfg["approvals"], "native")
                        if level == "full":
                            c["s"].p33 = True
                            with face_id(host, c["ph"].did):   # P550: widening needs this remote's own Face ID
                                await host._app(c["s"], {**sign(c["ph"], {"key": "approval.level", "value": "harness"}), "fa": FA})   # P552
                            self.assertEqual(approval_level.level(host.preferences, self.st), "harness")   # P552: one switch
                            c["s"].p33 = False
                        # Positive control: normal owner input starts/resumes without a card.
                        await c["say"]("hello")
                        await c["wait"](lambda: any(m["text"] == "ECHO: hello" for m in c["msgs"]()))
                        await c["idle"]()
                        self.assertEqual(c["asks"](), [])
                        self.assertTrue(self.st.agent_session(self.KIND))
                        if resumed:
                            self.assertEqual(self.st.agent_session(self.KIND), stored)
                        # An allow rule / native never must not bypass mandatory review.
                        await c["say"]("RUN: rm .env")
                        await c["wait"](lambda: len(c["asks"]()) == 1)
                        ask = c["asks"]()[0]
                        self.assertIn("credentials", ask["cat"])
                        self.assertEqual((self.work / ".env").read_text(), "synthetic fixture only\n")
                        await host._app(c["s"], c["ph"].answer(ask, False))
                        denied = "被拒绝：" if self.KIND == "claude" else "declined: rm .env"
                        await c["wait"](lambda: any(m["text"].startswith(denied) for m in c["msgs"]()))
                        await c["idle"]()
                        self.assertEqual((self.work / ".env").read_text(), "synthetic fixture only\n")
                        record = approvals.read_log(self.st)[-1]
                        self.assertEqual(record["decision"], "deny")
                        self.assertEqual(approvals.check_record(self.st, record), "ok")

                    self.run_chain(script)
                    launches = self.logged()[before:]
                    if self.KIND == "claude":
                        # P569 integration: since P567 (#355, REPORT-567:13) Settings' host_info refreshes the installed-harness list
                        # in the background (`claude --version` probe); the session launch is the first non-probe argv
                        argv = next(x["argv"] for x in launches if "argv" in x and x["argv"] != ["--version"])
                        self.assertEqual("--resume" in argv, resumed)
                        if resumed:
                            self.assertEqual(argv[argv.index("--resume") + 1], stored)
                    else:
                        request = next(x for x in launches if x.get("method") in ("thread/start", "thread/resume"))
                        self.assertEqual(request["method"], "thread/resume" if resumed else "thread/start")
                        self.assertEqual(request["params"]["approvalPolicy"], "untrusted")
                        self.assertEqual(request["params"]["approvalsReviewer"], "user")
                        self.assertEqual(request["params"]["sandbox"], "danger-full-access")
                        if resumed:
                            self.assertEqual(request["params"]["threadId"], stored)


class ClaudeLaunchReview(_LaunchReview):
    KIND = "claude"

    def test_hook_reviews_after_real_start_and_resume(self):
        self.check_launch_and_resume()


class CodexLaunchReview(_LaunchReview):
    KIND = "codex"

    def test_rpc_reviews_after_real_start_and_resume(self):
        self.check_launch_and_resume()
