"""P569 (#374): mail one Agent J host's owner deliberately sends reaches another Agent J host's Agent.

Before: every mail we send carried a bare `X-AgentJ`, and the receiver's gate took ANY `X-AgentJ` as its own loop
(`auto` / `own`), so three letters from the official mailbox never reached a customer's Agent. Now `X-AgentJ:
<kind>; o=<the writing mailbox, hashed>`; only our own `o=` (or a bare pre-P569 mark, or another Agent J's automatic
mail — Auto-Submitted) is skipped. Allowlist + sender authentication still decide everything else.

End to end, offline: host A runs the real `agentj mail send` CLI (its serve hop `mail_send` → ContactService.agent_send,
the owner's approval answered allow) → A's fake SMTP → the bytes land, with the receiving provider's
Authentication-Results, in host B's fake IMAP → B's real check/judge/deliver → B's Agent gets one turn.
Synthetic @example.com addresses, fake servers on 127.0.0.1 (tests/mailfake.py), no credentials.
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import contextlib
import email
import email.policy
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import mail as M  # noqa: E402
from agentj.contacts_serve import ContactService  # noqa: E402
from agentj.state import State  # noqa: E402
from mailfake import FakeIMAP, FakeSMTP  # noqa: E402

CODE = "right-code"
A_ADDR, B_ADDR, B_OWNER = "office@example.com", "client@example.com", "owner@example.com"
AR = "Authentication-Results: mx.example.com; spf=pass smtp.mailfrom=example.com; dkim=pass header.d=example.com; dmarc=pass header.from=example.com\r\n"


def arrive(raw: bytes) -> bytes:
    """What the receiving provider adds on delivery: its own check result on top."""
    return AR.encode() + raw


class Host:
    """One Agent J host's mail side: its state, fake mailbox pair, the serve's ContactService and the inbox check."""

    def __init__(self, case, root: pathlib.Path, addr: str, allow: list):
        self.addr = addr
        self.imap, self.smtp = FakeIMAP(addr, CODE), FakeSMTP(addr, CODE)
        case.addCleanup(self.imap.close)
        case.addCleanup(self.smtp.close)
        self.st = State(root / "state")
        self.st.init()
        work = root / "work"
        work.mkdir()
        self.turns = []

        async def accept(session, text, sid, blobs=None):
            self.turns.append((session, text))
            return SimpleNamespace(turn=100 + len(self.turns))
        self.ask = AsyncMock(return_value={"behavior": "allow"})      # the owner taps 「发送」 on the approval card
        self.host = SimpleNamespace(
            st=self.st, lang="zh", ask=self.ask, agent=SimpleNamespace(kind="claude", native_source={"kind": "terminal", "role": "owner"}),
            agent_cfg={"dir": str(work)}, media=None, stopped=lambda: False, stopping=asyncio.Event(),
            _accept=AsyncMock(side_effect=accept), hist_add=lambda *a: None, hist=SimpleNamespace(get=lambda i: None),
            _bg=lambda coro, *_: asyncio.create_task(coro))
        self.contacts = ContactService(SimpleNamespace(host=self.host, st=self.st))
        self.mail = M.Mail(self.host)
        cfg = M.blank_config()
        cfg.update(enabled=True, address=addr, username=addr, allow=allow,
                   imap={"host": "127.0.0.1", "port": self.imap.port, "security": "plain"},
                   smtp={"host": "127.0.0.1", "port": self.smtp.port, "security": "plain"})
        self.mail.set_config(cfg)
        M.store_secret(self.st, bytearray(CODE.encode()), addr)

    def cli_send(self, loop, *argv) -> tuple[int, dict]:
        """The real `agentj mail send` CLI; its elevate hop goes to this host's serve handler (as elevate.py dispatches)."""
        from agentj import cli

        def hop(st, req, timeout=None, **_):
            assert req["t"] == "mail_send"
            return asyncio.run_coroutine_threadsafe(self.contacts.agent_send(req), loop).result(60)
        outb = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), contextlib.redirect_stdout(outb), \
                contextlib.redirect_stderr(io.StringIO()), mock.patch.object(sys, "argv", ["agentj", "mail", "send", *argv, "--json"]), \
                mock.patch("agentj.elevate.client_request", hop):
            try:
                cli.main()
                code = 0
            except SystemExit as e:
                code = e.code or 0
        return code, json.loads(outb.getvalue())

    def last_sent(self):
        frm, to, raw = self.smtp.sent[-1]
        return to, raw

    def counts(self):
        return M.read_state(self.st)["counts"]


class TwoHosts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="p569m-", dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "a").mkdir()
        (root / "b").mkdir()
        # A = the official mailbox; B = a customer whose owner put A on the allowlist (a guest) and themself (the owner)
        self.a = Host(self, root / "a", A_ADDR, [{"addr": B_ADDR, "role": "guest"}])
        self.b = Host(self, root / "b", B_ADDR, [{"addr": A_ADDR, "role": "guest"}, {"addr": B_OWNER, "role": "owner"}])
        for h in (self.a, self.b):        # the first check is a baseline
            self.assertTrue((await h.mail.check()).get("baseline"))

    async def send(self, host, *argv):
        code, res = await asyncio.to_thread(host.cli_send, asyncio.get_running_loop(), *argv)
        self.assertEqual((code, res.get("ok")), (0, True), res)
        host.ask.assert_awaited()
        self.assertEqual(host.ask.await_args.args[0], "agentj mail send", "the owner approved this send on the phone")
        return host.last_sent()

    async def test_owner_sent_mail_reaches_the_other_hosts_agent(self):
        to, raw = await self.send(self.a, "--to", B_ADDR, "--subject", "Your Agent J order", "--body", "Hello from the Agent J office: order 42 shipped.")
        self.assertEqual(to, [B_ADDR])
        self.b.imap.add(arrive(raw))
        res = await self.b.mail.check()
        self.assertEqual((res["new"], res["delivered"]), (1, 1), self.b.counts())
        head = email.message_from_bytes(raw, policy=email.policy.default)
        self.assertIsNone(head["Auto-Submitted"], "a deliberate send is not automatic mail")
        self.assertRegex(str(head[M.MARK]), r"^agent; o=[0-9a-f]{12}$")
        self.assertNotIn(A_ADDR, str(head[M.MARK]), "the marker carries no address")
        [(session, text)] = self.b.turns
        self.assertEqual(session.source_kind, "email")
        self.assertIn("order 42 shipped", text)
        self.assertIn(A_ADDR, text)
        # B's Agent answers: the reply is automatic (auto-replied) → A's gate skips it, no ping-pong between two Agents
        await self.b.mail.reply(101, "Thanks, received.")
        _, back = self.b.last_sent()
        self.assertEqual(email.message_from_bytes(back, policy=email.policy.default)["Auto-Submitted"], "auto-replied")
        self.a.imap.add(arrive(back))
        res = await self.a.mail.check()
        self.assertEqual((res["new"], res["delivered"]), (1, 0))
        self.assertEqual(self.a.turns, [])
        self.assertEqual(self.a.counts().get("auto"), 1)

    async def test_own_sent_mail_back_in_the_own_inbox_is_still_skipped(self):
        # B mails its own mailbox address (and the owner): the copy lands in B's inbox → own, never a task
        _, raw = await self.send(self.b, "--to", B_ADDR, "--subject", "note to self", "--body", "keep this")
        self.b.imap.add(arrive(raw))
        res = await self.b.mail.check()
        self.assertEqual((res["new"], res["delivered"]), (1, 0))
        self.assertEqual(self.b.turns, [])
        self.assertEqual(self.b.counts().get("auto"), 1)
        self.assertEqual(M.judge(email.message_from_bytes(raw, policy=email.policy.default), M.read_config(self.b.st), []).detail, "own")

    async def test_meeting_notes_and_auto_replies_from_another_host_stay_skipped(self):
        cfg = M.read_config(self.a.st)
        for msg in (M.build_notes(cfg, [B_ADDR], "Meeting notes", "minutes", "Agent J"),
                    M.build_reply(cfg, {"to": B_ADDR, "subject": "hi", "msgid": "<x@example.com>"}, "auto answer", "Agent J", "en")):
            await asyncio.to_thread(M.send_many, cfg, CODE, msg, [B_ADDR])
            self.b.imap.add(arrive(self.a.last_sent()[1]))
        res = await self.b.mail.check()
        self.assertEqual((res["new"], res["delivered"]), (2, 0))
        self.assertEqual(self.b.turns, [], "an allowlisted Agent J's automatic mail is not even a read-only notice")
        self.assertEqual(self.b.counts().get("auto"), 2)

    async def test_old_format_marker_does_not_crash_and_stays_skipped(self):
        # a pre-P569 host stamps a bare `X-AgentJ: agent` — indistinguishable from our own pre-upgrade mail → own (as before)
        _, raw = await self.send(self.a, "--to", B_ADDR, "--subject", "old host", "--body", "from an older Agent J")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        for value in ("agent", "", "agent; o=", "agent; o=" + "f" * 200, "中; o=zz"):
            del msg[M.MARK]
            msg[M.MARK] = value
            del msg["Message-ID"]
            msg["Message-ID"] = f"<old{len(value)}@example.com>"
            self.b.imap.add(arrive(msg.as_bytes()))
        res = await self.b.mail.check()
        self.assertEqual(res["new"], 5)
        self.assertEqual(res["delivered"], 4, "a foreign o= (even empty or junk) is a letter, an empty header as before")
        self.assertEqual(self.b.counts().get("auto"), 1, "only the bare pre-P569 mark is taken as own")


if __name__ == "__main__":
    unittest.main()
