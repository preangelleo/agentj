"""P40: a shim trap must never be executed, including in fake HOME."""
import _hermetic
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import binaries, harness, agent, service

class BinaryResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p40-", dir="/var/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = {"HOME":str(self.home),"PATH":str(self.home/".local/bin")}

    def executable(self, rel, content):
        p=self.home/rel; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(content); p.chmod(0o755); return p

    def test_all_harnesses_bypass_manager_and_capture_real_path(self):
        for name in binaries.NAMES:
            trap=self.home/"SHIM_EXECUTED"
            self.executable(f".local/bin/{name}", f"#!/bin/sh\n# mise wrapper\ntouch {trap}\nexit 99\n")
            real=self.executable(f".local/share/mise/installs/{name}/1.18.34/{name}", "#!/bin/sh\nprintf 'test-1.18.34\\n'\n")
            e={**self.env}
            with patch.dict(os.environ,e,clear=True):
                self.assertEqual(harness.agent_bin(name),str(real))
                self.assertEqual(agent._bin(binaries.ENV[name],name),str(real))
                self.assertEqual(harness.version_of(harness.agent_bin(name)),"test-1.18.34")
            captured,_=service.service_env(e)
            self.assertEqual(captured[binaries.ENV[name]],str(real))
            self.assertFalse(trap.exists())

    def test_explicit_override_keeps_users_workaround_and_lists_both(self):
        one=self.executable(".local/share/mise/installs/opencode/1.18.34/opencode","#!/bin/sh\nexit 0\n")
        two=self.executable(".opencode/bin/opencode","#!/bin/sh\nexit 0\n")
        e={**self.env,"AGENTJ_OPENCODE_BIN":str(one),"PATH":str(two.parent)}
        self.assertEqual(binaries.resolve("opencode",e)["path"],str(one))
        self.assertEqual(set(binaries.installations("opencode",e)),{str(one),str(two)})

    def test_ambiguous_wrapper_is_never_executed(self):
        self.executable(".local/bin/opencode","#!/bin/sh\nexec mise x opencode -- opencode\n")
        for ver in ("1.18.34","2.0.22"):
            self.executable(f".local/share/mise/installs/opencode/{ver}/opencode","#!/bin/sh\nexit 0\n")
        self.assertIsNone(binaries.resolve("opencode",self.env)["path"])
        cfg=self.home/".config/mise/config.toml";cfg.parent.mkdir(parents=True)
        cfg.write_text('[tools]\nopencode = "1.18.34"\n')
        self.assertIn("1.18.34",binaries.resolve("opencode",self.env)["path"])

    def test_selected_alias_resolves_without_running_mise(self):
        self.executable(".local/bin/opencode","#!/bin/sh\nexec mise x opencode -- opencode\n")
        real=self.executable(".local/share/mise/installs/opencode/1.18.34/opencode","#!/bin/sh\nexit 0\n")
        (real.parent.parent/"latest").symlink_to(real.parent, target_is_directory=True)
        cfg=self.home/".config/mise/config.toml";cfg.parent.mkdir(parents=True)
        cfg.write_text('[tools]\nopencode = "latest"\n')
        self.assertEqual(binaries.resolve("opencode",self.env)["path"],str(real))

    def test_asdf_tool_versions(self):
        self.executable(".asdf/shims/codex","#!/bin/sh\nexec asdf exec codex\n")
        real=self.executable(".asdf/installs/codex/1.2/bin/codex","#!/bin/sh\nexit 0\n")
        (self.home/".tool-versions").write_text("codex 1.2\n")
        self.assertEqual(binaries.resolve("codex",{**self.env,"PATH":str(self.home/".asdf/shims")})["path"],str(real))

    def test_desktop_linger_matches_doctor(self):
        for e,needed in (({"WAYLAND_DISPLAY":"wayland-1"},False),({"DISPLAY":":0"},False),({},True),({"WAYLAND_DISPLAY":"x","SSH_CONNECTION":"x"},True)):
            self.assertEqual(service.linger_needed(e),needed)

    def test_install_feedback_version_comes_from_front_matter(self):
        doc=Path(__file__).resolve().parents[3]/"documentation/product/install.md"
        if not doc.exists():self.skipTest("private canonical install guide absent from public export")
        s=doc.read_text()
        self.assertNotIn('"install_md_version": "0.15.0"',s)
        self.assertIn('"install_md_version": "<version from this file’s front matter>"',s)

    def test_tilde_override_respects_supplied_service_environment(self):
        real=self.executable(".opencode/bin/opencode","#!/bin/sh\nexit 0\n")
        e={**self.env,"AGENTJ_OPENCODE_BIN":"~/.opencode/bin/opencode"}
        self.assertEqual(binaries.resolve("opencode",e)["path"],str(real))
        self.assertIn(str(real),binaries.installations("opencode",e))

class StartupProgress(unittest.IsolatedAsyncioTestCase):
    async def test_output_progress_extends_startup_but_silence_times_out(self):
        import asyncio
        from unittest.mock import AsyncMock,Mock
        from agentj import agent_opencode as oc
        from types import SimpleNamespace
        async def scenario(progress):
            proc=SimpleNamespace(stdout=asyncio.StreamReader(),stderr=asyncio.StreamReader(),returncode=None)
            host=Mock();host.st=Mock()
            a=oc.OpenCodeAgent(host,{"dir":"/var/tmp","_workflow_ceo":True})
            a.launch_argv=Mock(return_value=['fake']);a._kill=AsyncMock()
            a._prepare=AsyncMock(side_effect=ValueError('stop after proven startup'))
            # Keep the same 80ms idle / 600ms total policy, but drive an adapter-local
            # clock and real StreamReader explicitly: CPU scheduling is not provider silence.
            clock = [0.0]; lines = [b'loading\n'] * 4 + [b'listening on http://127.0.0.1:12345\n']
            async def waited(tasks, timeout):
                if progress:
                    clock[0] += .025
                    proc.stdout.feed_data(lines.pop(0))
                    await asyncio.sleep(0)  # the actual readline task consumes the real buffer
                else:
                    clock[0] += timeout
                done = {t for t in tasks if t.done()}
                return done, set(tasks) - done
            native = SimpleNamespace(**vars(asyncio))
            native.wait = waited
            native.create_subprocess_exec = AsyncMock(return_value=proc)
            try:
                with patch.object(oc,'START_WAIT',.08),patch.object(oc,'START_MAX',.6),patch.object(oc,'time',SimpleNamespace(monotonic=lambda:clock[0])),patch.object(oc,'asyncio',native),patch.object(oc,'free_port',return_value=12345),patch.object(oc,'_bin',return_value='/fake'):
                    self.assertFalse(await a._spawn())
                self.assertEqual(a._prepare.await_count,1 if progress else 0)
                self.assertGreater(clock[0], .08) if progress else self.assertEqual(clock[0], .08)
                self.assertLess(clock[0], .6)
                if not progress:
                    self.assertTrue(any('agentj doctor' in str(c) for c in host.agent_notice.call_args_list))
            finally:
                for bg in list(a.tasks):bg.cancel()
                await asyncio.gather(*a.tasks,return_exceptions=True)
        await scenario(True);await scenario(False)

    async def test_turn_without_progress_ends_with_actionable_notice(self):
        import asyncio,time
        from unittest.mock import AsyncMock,Mock
        from agentj import agent_opencode as oc
        a=oc.OpenCodeAgent(Mock(),{'dir':'/var/tmp','_workflow_ceo':True})
        a.turn_done=asyncio.Event();a.turn_progress=time.monotonic()-10;a.interrupt_request=AsyncMock()
        with patch.object(oc,'WATCH',.001),patch.object(oc,'TURN_IDLE',.01):await a._wait_turn()
        self.assertTrue(a.turn_done.is_set());a.interrupt_request.assert_awaited_once()
        self.assertIn('Models & Key',str(a.host.agent_notice.call_args))
        self.assertNotIn('opencode auth login',str(a.host.agent_notice.call_args))

    async def test_provider_errors_are_classified_without_echo_and_not_duplicated(self):
        from unittest.mock import Mock
        from agentj import agent_opencode as oc
        a=oc.OpenCodeAgent(Mock(),{'dir':'/var/tmp','_workflow_ceo':True});a.sid='test-session'
        for code,reason in [(401,'login'),(402,'balance'),(429,'rate_limit'),(404,'model')]:
            a.provider_fail_noted=False;a.host.reset_mock()
            ev={'type':'session.error','properties':{'sessionID':a.sid,'error':{'name':'APIError','data':{'statusCode':code,'message':'secret-must-not-appear'}}}}
            a.on_event(ev);a.on_event(ev)
            self.assertEqual(a.host.agent_notice.call_count,1)
            self.assertNotIn('secret-must-not-appear',str(a.host.mock_calls))
            self.assertEqual(a.host.st.log.call_args.kwargs['reason'],reason)
