"""Native identity contract checks; no real harnesses, network or tool shims."""
import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import main_identity as identity
from agentj.agent import ClaudeAgent
from agentj.agent_codex import CodexAgent
from agentj.agent_opencode import OpenCodeAgent
from agentj.state import State

class Core(unittest.TestCase):
    def test_packaged_bilingual_core_and_append(self):
        self.assertEqual(identity.verify_core()["version"], 7)
        for lang, marker in (("en", "chief of staff"), ("zh-CN", "董事长助理")):
            text = identity.prompt({"language":lang,"instructions":"be terse"})
            self.assertIn(marker,text)
            self.assertIn("documentation/ROLES.md",text)
            self.assertTrue(text.index(marker)<text.index("be terse"))
    def test_v2_owner_positioning_decisions_and_workflow_bible(self):
        additions = {'en': ['Your positioning to the human: "Your AI chief of staff." They only need to talk to you; you dispatch, monitor and maintain dozens, even hundreds, of Agent workflows, digest the messy details, and bring to their screen only what they need to know or decide.', "Decide what you can. Workflow CEOs bring you questions they cannot settle; answer the intermediate and technical ones yourself. Only what genuinely needs the human's decision reaches the human.", 'Every new workflow follows the Workflow Design Bible (https://github.com/preangelleo/workflow-design-bible) structure — thin entry file, constitution with red lines, the short boot-set documents and a STRUCTURE manifest — so that you can manage hundreds of them the same way.'], 'zh': ['你对用户的定位：「你的 AI 董事长助理」。用户只需要跟你一个主 Agent 说话；你替用户调度、监控、维护几十甚至上百个 Agent 工作流，把复杂的过程信息消化掉，只把需要用户知道的信息或者拍板的事送到用户的屏幕上。', '能替用户拍板的就自己拍板。各工作流 CEO 拿不准的问题先交给你，中间性、技术性的问题由你直接回答；只有真正必须由用户决定的事才送到用户面前。', '每个新工作流都按 Workflow Design Bible（https://github.com/preangelleo/workflow-design-bible）的结构搭建：薄入口文件、带红线的宪法、几份简短的开工文档和 STRUCTURE 清单——结构统一，你才能用同一种方式管好成百上千个工作流。']}
        for lang, (positioning, decisions, bible) in additions.items():
            lines = identity.prompt({"language": lang}).splitlines()
            self.assertTrue(lines[0].endswith("v7"))
            self.assertTrue(lines[1].endswith(positioning))
            self.assertEqual(lines[4], decisions)
            self.assertTrue(lines[5].endswith(bible))

    def test_v7_request_classification_and_owner_agreement(self):
        for lang, markers in {"zh": ("每条主人请求", "一次性临时活", "技能", "命令行工具", "应建立新工作流", "同意后再建", "CEO 再派子 Agent"), "en": ("Before every owner request", "temporary one-off", "skill", "command-line tool", "Should it become a workflow", "build only after agreement", "delegates concrete work")}.items():
            text = identity.prompt({"language": lang})
            for marker in markers:
                self.assertIn(marker, text)
            self.assertIn("https://github.com/preangelleo/workflow-design-bible", text)

    def test_manifest_or_asset_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            for item in identity.DATA.iterdir():
                (root/item.name).write_bytes(item.read_bytes())
            with patch.object(identity,"DATA",root):
                self.assertTrue(identity.verify_core())
                (root/"core.en.md").write_text("I am a workflow CEO")
                with self.assertRaises(identity.IdentityError): identity.prompt({})
    def test_root_and_no_private_identity(self):
        self.assertEqual(identity.working_root({"working_root":"/tmp/root", "dir":"/tmp/old"}),pathlib.Path("/tmp/root"))
        for lang in ("en","zh"):
            for private in ("Example Owner", "Example Private Workflow", str(pathlib.Path.home())):
                self.assertNotIn(private,identity.prompt({"language":lang}))
    def test_claude_start_resume_and_ceo(self):
        host=SimpleNamespace(st=Mock())
        cfg={"kind":"claude","dir":"/tmp","working_root":"/tmp/root","instructions":"my style"}
        a=ClaudeAgent(host,cfg)
        self.assertEqual(a.cfg["dir"],"/tmp/root")
        for sid in (None,"old-session"):
            argv=a.argv(sid)
            self.assertEqual(argv[argv.index("--append-system-prompt")+1],identity.prompt(cfg))
        ceo=ClaudeAgent(host,{**cfg,"dir":"/tmp","_workflow_ceo":True})
        self.assertEqual(ceo.cfg["dir"],"/tmp")
        self.assertNotIn("--append-system-prompt",ceo.argv(None))
    def test_main_model_configuration_remains_shared_ceos_isolated(self):
        host=SimpleNamespace(st=Mock())
        for adapter,kind in ((ClaudeAgent,"claude"),(CodexAgent,"codex"),(OpenCodeAgent,"opencode")):
            cfg={"kind":kind,"dir":"/tmp"}
            agent=adapter(host,cfg)
            self.assertIs(agent.cfg,cfg)
            cfg.update(model="next-model",effort="high")
            self.assertEqual(agent.cfg["model"],"next-model")
            self.assertEqual(agent.cfg["effort"],"high")
            if kind!="claude":
                ceo=adapter(host,cfg,persist=False)
                self.assertIsNot(ceo.cfg,cfg)
                self.assertNotIn("_workflow_ceo",cfg)

    def test_main_launch_root_guard_even_unfenced(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); protected=root/"protected"; protected.mkdir()
            linked=root/"alias"; linked.symlink_to(protected,target_is_directory=True)
            host=SimpleNamespace(st=Mock())
            with patch("agentj.fence.protected_paths",return_value=[str(protected)]):
                for directory in (str(protected),str(linked),str(root/"missing")):
                    a=ClaudeAgent(host,{"kind":"claude","dir":directory,"fence":False})
                    a.local_fail=Mock()
                    self.assertIsNone(a.launch_argv(["fake"]))
                    a.local_fail.assert_called_once()
                a=ClaudeAgent(host,{"kind":"claude","dir":str(root),"fence":False})
                self.assertEqual(a.launch_argv(["fake"]),["fake"])
                with self.assertRaises(identity.IdentityError):
                    identity.validate_working_root({"working_root":"relative"},host.st)

    def test_metadata_only_audit(self):
        with tempfile.TemporaryDirectory() as td:
            st=State(pathlib.Path(td)); cfg={"kind":"claude","dir":td,"instructions":"private user instruction"}
            identity.audit(cfg,"claude",st,"session")
            self.assertNotIn("private user instruction",st.log_path.read_text())
            self.assertEqual(identity.latest_audit(st,cfg),(True,"native identity injection metadata matches"))
            self.assertFalse(identity.latest_audit(st,{**cfg,"instructions":"changed"})[0])

class Native(unittest.IsolatedAsyncioTestCase):
    async def test_codex_start_resume_and_ceo(self):
        for sid in (None,"old"):
            host=SimpleNamespace(st=Mock());host.st.agent_session.return_value=sid
            a=CodexAgent(host,{"kind":"codex","dir":"/tmp"})
            a.call=AsyncMock(return_value={"thread":{"id":"new"}});a.meter=Mock();a.models_cache=[{"id":"model"}]
            await a._thread()
            method,body=a.call.call_args.args
            self.assertEqual(method,"thread/resume" if sid else "thread/start")
            self.assertEqual(body["developerInstructions"],identity.prompt(a.cfg))
            host.st.log.assert_called_once()
        a=CodexAgent(host,{"kind":"codex","dir":"/tmp/ceo","working_root":"/tmp/root"},persist=False)
        a.call=AsyncMock(return_value={"thread":{"id":"ceo"}})
        await a._thread()
        self.assertNotIn("developerInstructions",a.call.call_args.args[1])
        self.assertEqual(a.cfg["dir"],"/tmp/ceo")
    async def test_opencode_system_on_each_turn_ceo_omitted(self):
        for persist in (True,False):
            host=SimpleNamespace(st=Mock())
            a=OpenCodeAgent(host,{"kind":"opencode","dir":"/tmp"},persist=persist)
            a.proc=object();a.sid="session";a.client=SimpleNamespace(request=AsyncMock(return_value=(204,None)))
            a._session_ok=AsyncMock(return_value=True);a._wait_turn=AsyncMock();a._catch_up=AsyncMock();a.context_meter=AsyncMock()
            async def deliver(fn): return await fn()
            a.deliver=deliver
            for _ in range(2): await a.turn("task")
            prompts=[call for call in a.client.request.call_args_list if call.args[0] == 'POST']
            self.assertEqual(len(prompts),2)
            for call in prompts:
                method,path,body=call.args
                self.assertEqual(path,"/session/session/prompt_async")
                self.assertEqual("system" in body,persist)
                if persist:self.assertEqual(body["system"],identity.prompt(a.cfg))

if __name__ == "__main__": unittest.main()
