"""P253 offline contracts: real phone handler, packaged skills, owner intent and local support transport."""
import _hermetic
import argparse
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import compose, feedback_father, main_identity, personalize, slash, support
from test_p238_menu import _host, _ask, MODES, KINDS, OWN

NAME = "feedback-to-agentj-father"

class Father(unittest.TestCase):
    def test_real_phone_menu_all_modes_harnesses_and_owner_lists(self):
        with tempfile.TemporaryDirectory() as d:
            host, sent = _host(d)
            work = pathlib.Path(d) / "w"; work.mkdir()
            for mode in MODES:
                for kind in KINDS:
                    for own in OWN:
                        for lang in ("zh", "en"):
                            with self.subTest(mode=mode,kind=kind,own=own,lang=lang):
                                rows = _ask(host,sent,mode,kind,own,lang,work)["items"]
                                found=[r for r in rows if r["cmd"] == "/"+NAME]
                                self.assertEqual(len(found),1)
                                self.assertEqual(found[0]["desc"], "反馈给 Agent J Father" if lang=="zh" else "Feedback to Agent J Father")
        self.assertIn(NAME,slash.AGENT_CMDS)

    def test_skill_installed_in_all_native_locations(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(personalize.ensure(home=d,force=True)["conflicts"],0)
            targets=[t for n,s,t in personalize.links(d) if n==NAME]
            self.assertEqual(len(targets),4)
            for target in targets:
                self.assertTrue(target.is_symlink())
                text=(target/"SKILL.md").read_text()
                for marker in ("no second confirmation","only after agreement","customer data","ticket ID","Read this explanation"):
                    self.assertIn(marker,text)

    def test_owner_command_and_empty_command_through_compose(self):
        for lang in ("zh","en"):
            long="报错详情 " * 120
            text=compose.render("/"+NAME+" "+long,[],lang)
            self.assertFalse(text.startswith("/"))
            self.assertIn(long.strip(),text)  # not slash.ARG_MAX's 200-char truncation
            self.assertIn("without a second confirmation" if lang=="en" else "不再二次确认",text)
            empty=compose.render("/"+NAME,[],lang)
            self.assertIn("do not send anything yet" if lang=="en" else "现在不要发送",empty)
        self.assertIsNone(feedback_father.agent_text("/feedback-to-agentj-father-other"))

    def test_cli_general_feedback_uses_existing_report_kind_without_prompt(self):
        p=argparse.ArgumentParser();support.add_parser(p.add_subparsers())
        for extra,kind in (([],"report"),(["--kind","bug"],"bug")):
            a=p.parse_args(["support","feedback","an idea","--attach-doctor","--wait","0",*extra])
            with patch.object(support,"run_send",return_value=5) as send, patch("builtins.input",side_effect=AssertionError("must not ask twice")):
                with self.assertRaises(SystemExit) as ex:support.cmd(a)
                self.assertEqual(ex.exception.code,5)
                self.assertEqual(send.call_args.args,(kind,"an idea"))
                self.assertTrue(send.call_args.kwargs["attach_doctor"])

    def test_general_feedback_reaches_fake_transport_without_confirmation(self):
        import test_support as fixture
        case=fixture.Support();case.setUp()
        try:
            api=fixture.FakeAPI(case.st)
            output=io.StringIO()
            with patch("builtins.input",side_effect=AssertionError("no second confirmation")):
                rc=support.run_send("report","Please add a shortcut",ch=case.chan(api),env={},wait=0,out=output,
                                    attach_doctor=True,doctor_fn=lambda:"doctor ok /srv/Alice/customer.txt")
            self.assertEqual(rc,5)
            self.assertEqual(len(api.sent),1)
            self.assertEqual(api.sent[0]["kind"],"report")
            self.assertNotIn("customer.txt",api.sent[0]["attach"])
            self.assertIn(fixture.TID,output.getvalue())
        finally:
            case.doCleanups();case.tmp.cleanup()

    def test_strict_local_redaction_body_and_doctor(self):
        key="sk-"+"K"*48
        body=f"Problem {key} user@private.invalid +8613812345678 /srv/客户甲/秘密合同.docx C:\\Users\\Alice\\private.txt ~/客户乙/财报.txt"
        draft,hits=support.prepare(body,body,identity=("host-private","Alice"))
        for text in draft.values():
            for raw in (key,"user@private.invalid","13812345678","客户甲","客户乙","private.txt","财报"):
                self.assertNotIn(raw,text)
            self.assertIn("<redacted-path>",text)

        public, _ = support.prepare("Typing /model then /clear fails; file /model/private-customer.txt", None, identity=("host-private", "Alice"))
        self.assertIn("/model then /clear", public["body"])
        self.assertNotIn("private-customer", public["body"])

    def test_pinned_identity_consent_in_all_harnesses(self):
        self.assertEqual(main_identity.verify_core()["version"],16)
        for lang in ("zh","en"):
            for harness in KINDS:
                text=main_identity.prompt({"language":lang},harness)
                for word in ("Agent J Father",NAME,"feedback@agentj.app","agentj support ask"):
                    self.assertIn(word,text)

if __name__=="__main__":unittest.main()
