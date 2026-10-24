"""P205 item 2: `agentj mail send --to-owner` resolves to the owner's own addresses (slot 0 + the 「我本人」 allowlist rows).

- `--to-owner` alone → the owner's addresses; the phone approval card first, the same owner-direct filter as `--to <owner>`;
- `--to` + `--to-owner` → the union, deduped, ≤ 10 in all (more → `to`);
- no owner address → `no_owner_address` with a human sentence (zh / en from the CLI);
- the real CLI → elevate.sock → serve path (the 0.20.1a1 bug: the CLI dropped the flag and serve answered "1–10 recipients").
Local fake SMTP only (tests/mailfake.py).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
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
from agentj import contacts as C, mail as M  # noqa: E402
from mailfake import FakeSMTP  # noqa: E402
from test_p194_owner_direct import AGENT, ALLOW_ME, CODE, ME, _Svc, body_of  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk


class ToOwner(_Svc):
    def summary(self):
        if self.ask.call_args is None:          # P296: owner-direct goes without a card; the same text is in activity
            return [kw for k, kw in self.acts if k == "auto"][-1]["summary"]
        return self.ask.call_args.args[1]["summary"]

    async def test_allowlist_owner_only(self):
        res = await self.svc.agent_send({"to": [], "to_owner": True, "subject": "x", "body": "电话 13812345678"})
        self.assertTrue(res["ok"] and res["owner_direct"], res)
        self.assertEqual(res["sent"], [ALLOW_ME])
        self.assertEqual(self.smtp.sent[-1][1], [ALLOW_ME])
        self.assertIn("13812345678", body_of(self.smtp.sent[-1][2]), "owner-direct: as written, same as --to <owner>")
        self.ask.assert_awaited_once()
        self.assertTrue(self.ask.call_args.kwargs["require_passkey"])
        self.assertIn("发给你自己：原样发出", self.summary())

    async def test_slot0_and_allowlist_deduped_same_as_explicit(self):
        self.set_me()
        res = await self.svc.agent_send({"to_owner": True, "subject": "x", "body": "b"})
        self.assertEqual(res["sent"], [ME, ALLOW_ME])
        # --to <owner> + --to-owner: no duplicate
        res = await self.svc.agent_send({"to": [ME.upper()], "to_owner": True, "subject": "x", "body": "b"})
        self.assertEqual(res["sent"], [ME, ALLOW_ME])
        self.assertTrue(res["owner_direct"])

    async def test_union_with_someone_else_is_masked_and_approved(self):
        await self.svc.agent({"op": "add", "contact": {"name": "张三", "emails": ["zs@qq.com"]}})
        res = await self.svc.agent_send({"to": ["张三"], "to_owner": True, "subject": "x", "body": "电话 13812345678"})
        self.assertEqual(res["sent"], ["zs@qq.com", ALLOW_ME])
        self.assertFalse(res["owner_direct"])
        self.assertNotIn("13812345678", body_of(self.smtp.sent[-1][2]))
        self.assertIn("发给外人", self.summary())

    async def test_denied_on_phone_sends_nothing(self):
        await self.svc.agent({"op": "add", "contact": {"name": "张三", "emails": ["zs@qq.com"]}})
        self.ask.return_value = {"behavior": "deny"}
        res = await self.svc.agent_send({"to": ["张三"], "to_owner": True, "subject": "x", "body": "b"})
        self.assertEqual(res["why"], "denied")
        self.assertEqual(self.smtp.sent, [])

    async def test_cap_counts_the_union(self):
        to = [f"p{i}@x.cn" for i in range(10)]
        res = await self.svc.agent_send({"to": to, "to_owner": True, "subject": "x", "body": "b"})
        self.assertEqual(res["why"], "to")
        self.ask.assert_not_called()
        res = await self.svc.agent_send({"to": to[:9], "to_owner": True, "subject": "x", "body": "b"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(res["sent"]), 10)

    async def test_no_owner_address(self):
        cfg = M.read_config(self.st)
        cfg["allow"] = []
        M.save_config(self.st, M.validate(cfg))
        self.assertEqual(C.owner_emails(self.st), [])
        res = await self.svc.agent_send({"to_owner": True, "subject": "x", "body": "b"})
        self.assertEqual(res["why"], "no_owner_address")
        self.assertIn("0 号位", res["detail"])
        self.ask.assert_not_called()
        # nothing at all → still the old shape error
        self.assertEqual((await self.svc.agent_send({"subject": "x", "body": "b"}))["why"], "to")


class RealCli(unittest.IsolatedAsyncioTestCase):
    """`agentj mail send --to-owner` through the real CLI parser → elevate.sock → serve's ContactService."""

    async def run_cli(self, d, argv):
        from agentj import cli
        buf = io.StringIO()

        def go():
            with mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(pathlib.Path(d) / "s")}), \
                    contextlib.redirect_stdout(buf), mock.patch.object(sys, "argv", ["agentj", *argv]):
                try:
                    cli.main()
                except SystemExit as e:
                    return e.code
            return 0
        code = await asyncio.to_thread(go)
        import json
        return code, json.loads(buf.getvalue())

    async def test_cli_to_owner_end_to_end(self):
        from test_l1 import _owner_host as _host, _state
        smtp = FakeSMTP(AGENT, CODE)
        self.addCleanup(smtp.close)
        with tempfile.TemporaryDirectory(prefix="p205m-", dir=TEST_TMP) as d:
            st = _state(d)
            cfg = M.blank_config()
            cfg.update(address=AGENT, username=AGENT, allow=[{"addr": ALLOW_ME, "role": "owner"}],
                       imap={"host": "127.0.0.1", "port": 1, "security": "plain"},
                       smtp={"host": "127.0.0.1", "port": smtp.port, "security": "plain"})
            M.save_config(st, M.validate(cfg))
            M.store_secret(st, bytearray(CODE.encode()), AGENT)
            host = _host(st, [])
            host.ask = AsyncMock(return_value={"behavior": "allow"})
            await host.elevate.start()
            self.addAsyncCleanup(host.elevate.stop)
            code, res = await self.run_cli(d, ["mail", "send", "--to-owner", "--subject", "周报", "--body", "hello", "--json"])
            self.assertEqual((code, res["ok"], res["sent"], res["owner_direct"]), (0, True, [ALLOW_ME], True), res)
            self.assertEqual(smtp.sent[-1][1], [ALLOW_ME])
            self.assertEqual(host.ask.await_count, 1) # P503: every final SMTP send requires UV
            self.assertTrue(host.ask.call_args.kwargs["require_passkey"])
            code, res = await self.run_cli(d, ["mail", "send", "--to", "zs@qq.com", "--to-owner", "--subject", "s",
                                               "--body", "b", "--json"])
            self.assertEqual(res["sent"], ["zs@qq.com", ALLOW_ME], res)
            self.assertEqual(host.ask.await_count, 2, "one exact-effect approval per send")
            cfg["allow"] = []
            M.save_config(st, M.validate(cfg))
            code, res = await self.run_cli(d, ["mail", "send", "--to-owner", "--subject", "s", "--body", "b", "--en", "--json"])
            self.assertEqual((code, res["why"]), (1, "no_owner_address"))
            self.assertIn("slot 0", res["detail"])
            self.assertEqual(host.ask.await_count, 2)


if __name__ == "__main__":
    unittest.main()
