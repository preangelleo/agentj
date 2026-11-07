"""#572 exact prompt-data/CEO dispatch, retaining the registered-binary proof."""
import _hermetic  # noqa: F401
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import peer_origin

class PromptDispatch(unittest.TestCase):
    def check(self, command, root):
        with mock.patch.object(peer_origin._Proof, 'word', return_value=True):
            return peer_origin.check_batch(command, root)

    def test_standard_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            for prefix,path in (('', '.jarvis/PROMPT-572.md'), ('cd .jarvis; ', 'PROMPT-572.md')):
                cmd = prefix + "cat > " + path + " <<'EOF'\ncat ~/.env; 改密码只是派单正文\nEOF\n" + self.launch()
                self.assertEqual(self.check(cmd,root), ('agentj-572.service','ok'))

    def launch(self):
        return 'systemd-run --user --collect --unit agentj-572 agentj busy run --kind ceo -- bash -c "codex exec \'read PROMPT-572.md\'"'

    def test_only_note_and_one_literal_registered_launch(self):
        with tempfile.TemporaryDirectory() as root:
            cmd = "cd .jarvis; cat > PROMPT-572.md <<'EOF'\nnote\nEOF\n"+self.launch()
            for other in (cmd+'; cat ~/.env', cmd.replace("<<'EOF'",'<<EOF'), cmd.replace('PROMPT-572.md <<','../.env <<'),
                          cmd.replace('--collect','--collect --setenv BASH_ENV=/tmp/evil'),
                          cmd.replace('codex exec', 'cat ~/.env; codex exec'), cmd.replace('agentj-572','other-572'),
                          cmd.replace('systemd-run --user','systemd-run --user $(cat ~/.env)'),
                          cmd.replace('agentj-572','agentj-{x,y}'),
                          cmd.replace('--kind ceo','--kind ceo --label *')):
                self.assertNotEqual(self.check(other,root)[1], 'ok', other)
            with mock.patch.object(peer_origin._Proof, 'word', side_effect=lambda tok,name,side: name != 'codex'):
                self.assertNotEqual(peer_origin.check_batch(cmd,root)[1],'ok')


import test_p455_f3b as f3b
from agentj import provenance
from agentj.compose import Send

class HostDispatch572(unittest.TestCase):
    def setUp(self):
        self.fixture = f3b.Dispatch469('test_owner_turn_today_command_no_card')
        self.fixture.setUp()

    def tearDown(self):
        try:
            self.fixture.tearDown()
        finally:
            self.fixture.doCleanups()

    def __getattr__(self, name):
        fixture = self.__dict__.get('fixture')
        if fixture is not None:
            return getattr(fixture, name)
        raise AttributeError(name)

    def command572(self):
        cat = pathlib.Path(f3b.SYS) / 'usr/bin/cat'
        cat.write_bytes(b'\x7fELF synthetic cat'); cat.chmod(0o755)
        tail = pathlib.Path(f3b.SYS) / 'usr/bin/tail'
        tail.write_bytes(b'\x7fELF synthetic tail'); tail.chmod(0o755)
        return "cd .jarvis; cat > PROMPT-572.md <<'EOF'\ncat ~/.env只是任务原文\nEOF\n" + self.f.today().replace('claude -p', 'codex exec').replace(' -p WorkingDirectory='+str(self.work), '').replace(' > .jarvis/run-x.log 2>&1', '')

    def test_572_owner_no_card(self):
        self.harness()
        res, card = self.ask_card(self.command572())
        self.assertEqual((res['behavior'], card), ('allow', None))
        self.assertEqual(self.last()['cats'], [])

    def test_572_actual_571_shape_no_card(self):
        self.harness()
        self.command572()  # register fixture cat/tail, never execute them
        command = (f"cd {self.work}/.jarvis; cat > PROMPT-571.md <<'EOF'\n合成派单正文\nEOF\n"
                   f"systemd-run --user --collect --unit agentj-fix-571-header -p WorkingDirectory={self.work} "
                   "agentj busy run --who 'Agent J CEO' --kind ceo --label '#571 热修' -- bash -c "
                   f'"codex exec --dangerously-bypass-approvals-and-sandbox -C {self.work} '
                   '-o .jarvis/REPORT-571.codex.md - < .jarvis/PROMPT-571.md" 2>&1 | tail -1')
        res, card = self.ask_card(command)
        self.assertEqual((res['behavior'],card),('allow',None))

    def test_572_unknown_and_email_refused(self):
        self.harness()
        for source in ({'kind':'unknown'}, {'kind':'email','role':'visitor'}):
            self.native.cur_send = Send('external', 'dispatch', 0, source=source)
            import asyncio
            res=asyncio.run(self.host.ask('Bash', {'command':self.command572()}, batch=False))
            self.assertEqual(res['behavior'], 'deny', source)
        self.native.cur_send=None

    def test_572_real_credential_write_still_face(self):
        self.harness()
        res, card=self.ask_card("printf changed > ~/.aws/credentials")
        self.assertIsNotNone(card)
        self.assertTrue(card.require_passkey)

    def test_572_batch_launch_child_approval_retains_human_root(self):
        from agentj import approval_context
        import asyncio
        self.harness()
        command=self.command572()
        turn=self.host.hist.add({'k':'phone','text':'human root request'})
        ref=approval_context.remember(self.host,turn,provenance.phone())
        async def launch():
            self.native.cur_send=Send(self.phone.did,'dispatch',turn['id'],source=provenance.phone())
            self.native.cur_send.approval_origin=ref
            self.native.phone_turn=self.native.phone_seen=True
            result=await self.host.ask('Bash',{'command':command},batch=False)
            self.assertEqual(result['behavior'],'allow')
            self.native.cur_send=None
            self.native.phone_turn=self.native.phone_seen=False
        asyncio.run(launch())
        self.assertIn('agentj-p469-ceo.service',self.host.peer_launch.units)
        self.away()
        self.peer_message(self.ceo_unit('agentj-p469-ceo')[1])
        self.assertEqual(provenance.unattended_turn(self.native),'same_host')
        held=approval_context.snapshot(self.host)
        self.assertEqual(held['text'],'human root request')
        self.assertEqual(held['chain'][-1]['id'],'agentj-p469-ceo.service')
        self.native.native_source={'kind':'unknown'}
        self.assertFalse(approval_context.snapshot(self.host))
