"""#118: forgot the remote pairing password, keep every remote. `agentj passphrase reset --phone`: a paired remote's signed
allow (+ its passkey when it has one) gives the owner's terminal a one-use ticket; the new password is typed there and only
its hash is stored; every remote stays paired; approvals.log + host.log keep the record; the phones get a notice.
Deny / timeout / forged or unpaired answers change nothing. Without a paired remote `agentj passphrase reset` is unchanged.
An Agent (no terminal, Agent harness environment, a process under serve) can neither ask nor type the new value."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
from _testtmp import TEST_TMP  # noqa: E402
import argparse
import asyncio
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import approvals, cli, ctl_auth, gate, pass_reset, passkey, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p176_terminal_pair import TTYIn  # noqa: E402

OLD = "old-passphrase-393"
NEW = "new-passphrase-393x"
CODE = "482913"
PID = 4242


def _raw(sk):
    return sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _logs(st):
    out = ""
    for p in pathlib.Path(st.root).rglob("*"):
        if p.is_file() and p.suffix in (".log", ".jsonl", ""):
            with contextlib.suppress(OSError, UnicodeDecodeError):
                out += p.read_text()
    return out


class Flow(unittest.TestCase):
    """serve's side, in process: the card, the signed answer, the ticket, the replace."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p393pp-", dir=TEST_TMP)
        st = serve.State(pathlib.Path(self.tmp.name) / "s")
        st.init(relay="ws://127.0.0.1:1")
        gate.set_passphrase(st, OLD)
        self.st = st
        self.host = serve.Host(st, events="jsonl", read_stdin=False)
        self.host.relay_up = True
        self.host.agent_notice = Mock()
        self.sent = []

        async def send_app(s, obj):
            if self.host.sessions.get(s.cid) is not s:
                return False
            self.sent.append((s.cid, obj))
            return True
        self.host.send_app = send_app
        self.keys = {}
        self.A = self._remote(10, "网页 · Linux Chrome")
        self.B = self._remote(11, "网页 · iOS Safari")
        self.before = set(st.devices())

    def tearDown(self):
        self.tmp.cleanup()

    def _remote(self, cid, name):
        pub, sk = os.urandom(32), Ed25519PrivateKey.generate()
        did = self.st.pair_device(pub, name, _raw(sk))["device"]
        self.keys[did] = sk
        self.host.sessions[cid] = serve.Session(cid=cid, state="ready", device=did, pub=pub, name=name, p33=True, pair1=True)
        return did

    def to(self, cid, t=None):
        return [o for c, o in self.sent if c == cid and (t is None or o.get("t") == t)]

    def _answer(self, did, card, ok=True, key=None):
        msg = approvals.signed_message(self.host.channel, did, card["id"], "allow" if ok else "deny",
                                       approvals.shown_digest(card["tool"], card["summary"]))
        return {"t": "answer", "id": card["id"], "ok": ok, "sig": wire.b64u((key or self.keys[did]).sign(msg))}

    async def _settle(self):
        for _ in range(20):
            await asyncio.sleep(0)

    async def _card(self, code=CODE, pid=PID):
        task = asyncio.ensure_future(self.host._pass_consent({"code": code}, pid))
        await self._settle()
        cards = self.to(11, "ask")
        self.assertEqual(len(cards), 1, "one card on each ready paired remote")
        self.assertEqual(len(self.to(10, "ask")), 1)
        return task, cards[0]

    def assert_unchanged(self):
        gate.verify(self.st, OLD)
        self.assertEqual(set(self.st.devices()), self.before)

    # ------------------------------------------------------------------ the target flow
    def test_paired_remote_allows_hash_changes_every_remote_stays_paired(self):
        async def go():
            task, card = await self._card()
            self.assertEqual(card["tool"], pass_reset.TOOL)
            self.assertIn("482 913", card["summary"], "the match code from the terminal is on the card")
            self.assertGreater(card["ttl"], 200)
            await self.host._app(self.host.sessions[11], self._answer(self.B, card))
            res = await asyncio.wait_for(task, 5)
            self.assertTrue(res["ok"], res)
            self.assertIsInstance(res["ticket"], str)
            out = await self.host._pass_replace({"ticket": res["ticket"], "new": NEW}, PID)
            self.assertEqual(out, {"ok": True, "kept": 2})
            again = await self.host._pass_replace({"ticket": res["ticket"], "new": NEW + "2"}, PID)
            self.assertEqual(again["error"], "ticket", "the ticket is single use")
            await self._settle()
        asyncio.run(go())
        gate.verify(self.st, NEW)
        with self.assertRaises(gate.GateError):
            gate.verify(self.st, OLD)
        self.assertEqual(set(self.st.devices()), self.before, "every remote stays paired")
        log = self.st.root.joinpath("approvals.log").read_text()
        rec = [json.loads(x) for x in log.splitlines() if pass_reset.TOOL in x]
        self.assertEqual(rec[-1]["decision"], "allow")
        self.assertEqual(rec[-1]["device"], self.B)
        self.assertTrue(rec[-1].get("sig"), "the approving remote's signature is on record")
        host_log = self.st.log_path.read_text()
        self.assertIn('"passphrase_reset"', host_log)
        self.assertIn('"by": "remote"', host_log)
        self.host.agent_notice.assert_called_once()
        self.assertIn("iOS Safari", self.host.agent_notice.call_args.args[0], "the phones hear who confirmed")
        for cid in (10, 11):
            self.assertIn("allow", [o.get("result") for o in self.to(cid, "ask_done")], "the card folds on every remote")
        everything = _logs(self.st) + json.dumps([o for _, o in self.sent], ensure_ascii=False)
        self.assertNotIn(NEW, everything, "the new password is never logged or sent to a phone")
        self.assertNotIn(OLD, everything)

    def test_ticket_belongs_to_the_asking_process(self):
        async def go():
            task, card = await self._card()
            await self.host._app(self.host.sessions[10], self._answer(self.A, card))
            res = await asyncio.wait_for(task, 5)
            other = await self.host._pass_replace({"ticket": res["ticket"], "new": NEW}, PID + 1)
            self.assertEqual(other["error"], "ticket")
            late = await self.host._pass_replace({"ticket": res["ticket"], "new": NEW}, PID)
            self.assertEqual(late["error"], "ticket", "a ticket tried by another process is gone")
            forged = await self.host._pass_replace({"ticket": "x" * 32, "new": NEW}, PID)
            self.assertEqual(forged["error"], "ticket")
        asyncio.run(go())
        self.assert_unchanged()

    def test_a_too_short_password_can_be_typed_again(self):
        async def go():
            task, card = await self._card()
            await self.host._app(self.host.sessions[11], self._answer(self.B, card))
            res = await asyncio.wait_for(task, 5)
            short = await self.host._pass_replace({"ticket": res["ticket"], "new": "short"}, PID)
            self.assertEqual(short, {"ok": False, "error": "passphrase", "reason": "too_short"})
            gate.verify(self.st, OLD)
            ok = await self.host._pass_replace({"ticket": res["ticket"], "new": NEW}, PID)
            self.assertTrue(ok["ok"])
        asyncio.run(go())
        gate.verify(self.st, NEW)

    # ------------------------------------------------------------------ nothing changes
    def test_denied_on_the_phone_changes_nothing(self):
        async def go():
            task, card = await self._card()
            await self.host._app(self.host.sessions[11], self._answer(self.B, card, ok=False))
            return await asyncio.wait_for(task, 5)
        res = asyncio.run(go())
        self.assertEqual(res, {"ok": False, "error": "denied"})
        self.assert_unchanged()
        self.host.agent_notice.assert_not_called()
        self.assertIn('"decision": "deny"', self.st.root.joinpath("approvals.log").read_text())

    def test_timeout_changes_nothing(self):
        async def go():
            with patch.object(pass_reset, "CARD_TTL", 0.3):
                task, _card = await self._card()
                return await asyncio.wait_for(task, 5)
        res = asyncio.run(go())
        self.assertEqual(res, {"ok": False, "error": "timeout"})
        self.assert_unchanged()
        self.assertIn('"reason": "timeout"', self.st.root.joinpath("approvals.log").read_text())

    def test_forged_and_unpaired_answers_are_refused(self):
        async def go():
            with patch.object(pass_reset, "CARD_TTL", 0.6):
                task, card = await self._card()
                bad = self._answer(self.B, card, key=Ed25519PrivateKey.generate())        # B's id, another key
                await self.host._app(self.host.sessions[11], bad)
                pub = os.urandom(32)                                                       # a session nobody paired
                stranger = serve.Session(cid=30, state="ready", device=wire.device_id(pub), pub=pub, name="x", p33=True)
                self.host.sessions[30] = stranger
                await self.host._app(stranger, self._answer(stranger.device, card, key=Ed25519PrivateKey.generate()))
                await self._settle()
                self.assertFalse(task.done(), "neither answer decided anything")
                return await asyncio.wait_for(task, 5)
        res = asyncio.run(go())
        self.assertEqual(res["error"], "timeout")
        self.assert_unchanged()
        log = self.st.log_path.read_text()
        self.assertIn('"reason": "bad_signature"', log)
        self.assertTrue('"reason": "no_key"' in log or '"cid": 30, "device": "' in log and '"reason": "revoked"' in log,
                        "an unpaired session is cut off (or its answer refused), never counted")

    def test_a_remote_with_a_passkey_must_use_it(self):
        self.st.set_passkey(self.host.sessions[11].pub,
                            {"id": wire.b64u(b"\x07" * 16), "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
                             "rp": "m.agentj.app", "at": int(time.time())})

        async def go():
            with patch.object(pass_reset, "CARD_TTL", 0.6):
                task, card = await self._card()
                self.assertTrue(card.get("passkey_required"))
                want = passkey.elevate_challenge(self.host.channel, self.B, card["id"], pass_reset.PK_KIND, card["id"],
                                                 approvals.shown_digest(card["tool"], card["summary"]))
                self.assertEqual(card["fa_ch"], wire.b64u(want))
                self.assertFalse(self.to(10, "ask")[0].get("passkey_required"), "a remote without a passkey signs only")
                await self.host._app(self.host.sessions[11], self._answer(self.B, card))     # no Face ID
                return await asyncio.wait_for(task, 5)
        self.assertEqual(asyncio.run(go())["error"], "timeout")
        self.assert_unchanged()
        self.assertIn('"reason": "passkey_required"', self.st.log_path.read_text())

    def test_no_paired_remote_no_card(self):
        for did in list(self.st.devices()):
            self.st.remove_device(did)
        res = asyncio.run(self.host._pass_consent({"code": CODE}, PID))
        self.assertEqual(res, {"ok": False, "error": "no_device"})
        self.assertEqual(self.sent, [])
        gate.verify(self.st, OLD)

    def test_bad_code_and_unset_password(self):
        self.assertEqual(asyncio.run(self.host._pass_consent({"code": "12345"}, PID))["error"], "shape")
        self.assertEqual(asyncio.run(self.host._pass_consent({}, PID))["error"], "shape")
        gate.reset(self.st)
        self.assertEqual(asyncio.run(self.host._pass_consent({"code": CODE}, PID))["error"], "not_set")
        self.assertEqual(self.sent, [])


# ------------------------------------------------------------------ the control socket: only the owner's terminal
CLIENT = r"""
import json, os, pathlib, sys
if os.fork():
    os._exit(0)
os.setsid()
if os.fork():
    os._exit(0)
sys.path.insert(0, sys.argv[1])
from agentj import names
from agentj.state import State
out = []
for req in json.loads(sys.argv[3]):
    try:
        out.append(names.ctl_call(State(pathlib.Path(sys.argv[2])), req, 10))
    except Exception as e:
        out.append({"exc": type(e).__name__})
tmp = sys.argv[4] + ".tmp"
open(tmp, "w").write(json.dumps({"pid": os.getpid(), "out": out}))
os.replace(tmp, sys.argv[4])
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ControlSocket(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        t = tempfile.TemporaryDirectory(prefix="p393c-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        env = patch.dict(os.environ, {"HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / ".config"),
                                      "AGENTJ_STATE_DIR": str(self.root / "s")})
        env.start()
        self.addCleanup(env.stop)
        from agentj import preferences
        self.st = State()
        self.st.init()
        preferences.ensure()
        gate.set_passphrase(self.st, OLD)
        self.host = serve.Host(self.st, read_stdin=False)
        self.host._pass_consent = AsyncMock(return_value={"ok": True, "ticket": "t", "by": "x"})
        self.host._pass_replace = AsyncMock(return_value={"ok": True, "kept": 1})
        self.server = await asyncio.start_unix_server(self.host.on_ctl, str(self.st.sock_path))
        self.pids = []

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        for pid in self.pids:
            for _ in range(50):
                if not alive(pid):
                    break
                await asyncio.sleep(.1)

    async def raw(self, *reqs):
        r, w = await asyncio.open_unix_connection(str(self.st.sock_path))
        out = []
        for req in reqs:
            w.write((json.dumps(req) + "\n").encode())
            await w.drain()
            out.append(json.loads(await r.readline()))
        w.close()
        await w.wait_closed()
        return out

    async def outside(self, reqs, extra_env=None):
        res = self.root / "client.json"
        res.unlink(missing_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ctl_auth.AGENT_ENV}
        env.update(extra_env or {})
        p = await asyncio.create_subprocess_exec(sys.executable, "-c", CLIENT, str(pathlib.Path(ctl_auth.__file__).parents[1]),
                                                 str(self.st.root), json.dumps(reqs), str(res), env=env)
        await p.wait()
        for _ in range(200):
            if res.exists():
                break
            await asyncio.sleep(.05)
        d = json.loads(res.read_text())
        self.pids.append(d["pid"])
        return d["out"]

    async def test_bare_socket_write_is_refused(self):
        for cmd in ({"cmd": "passphrase_consent", "code": CODE}, {"cmd": "passphrase_replace", "ticket": "t", "new": NEW}):
            out, = await self.raw(cmd)
            self.assertEqual(out, {"ok": False, "error": "claim_required"})
        self.host._pass_consent.assert_not_awaited()
        self.host._pass_replace.assert_not_awaited()

    async def test_agent_harness_environment_cannot_ask(self):
        with patch.object(ctl_auth, "has_tty", return_value=True):
            out = await self.outside([{"cmd": "passphrase_consent", "code": CODE}], {"CLAUDECODE": "1"})
        self.assertEqual(out[0]["error"], "claim_required")
        self.host._pass_consent.assert_not_awaited()

    async def test_agent_process_cannot_claim(self):
        self.host.agent = Mock(session={"pid": os.getpid()}, proc=None, native=None)
        claim, res = await self.raw({"cmd": "claim"}, {"cmd": "passphrase_replace", "ticket": "t", "new": NEW})
        self.assertEqual(claim["why"], "agent")
        self.assertEqual(res["error"], "claim_required")
        self.host._pass_replace.assert_not_awaited()

    async def test_no_terminal_is_refused_owner_terminal_passes(self):
        with patch.object(ctl_auth, "has_tty", return_value=False):
            out = await self.outside([{"cmd": "passphrase_consent", "code": CODE}])
        self.assertEqual(out[0], {"ok": False, "error": "claim_required", "why": "terminal"})
        self.host._pass_consent.assert_not_awaited()
        with patch.object(ctl_auth, "has_tty", return_value=True):
            out = await self.outside([{"cmd": "passphrase_consent", "code": CODE}])
        self.assertTrue(out[0]["ok"], out)
        self.host._pass_consent.assert_awaited_once()
        self.assertEqual(self.host._pass_consent.await_args.args[0]["code"], CODE)
        gate.verify(self.st, OLD)


# ------------------------------------------------------------------ the CLI
class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p393cli-", dir=TEST_TMP)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init()
        gate.set_passphrase(self.st, OLD)
        self.env = {k: v for k, v in os.environ.items() if k not in ctl_auth.AGENT_ENV}

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, stdin, phone=False, secrets=(), ctl=None, env=None):
        out, pw = io.StringIO(), list(secrets)
        ctl = ctl or Mock(return_value=None)
        a = argparse.Namespace(mode="reset", json=False, phone=phone)
        with patch.object(cli, "State", return_value=self.st), patch("sys.stdin", stdin), \
                patch.dict(os.environ, env if env is not None else self.env, clear=True), \
                patch.object(cli.names, "ctl_call", ctl), \
                patch("getpass.getpass", side_effect=lambda prompt="": pw.pop(0) if pw else ""), contextlib.redirect_stdout(out):
            try:
                cli.cmd_passphrase(a)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), ctl

    def _pair(self):
        sk = Ed25519PrivateKey.generate()
        return self.st.pair_device(os.urandom(32), "网页 · iOS Safari", _raw(sk))["device"]

    def test_no_paired_phone_reset_is_unchanged(self):
        code, text, ctl = self.run_cli(io.StringIO("RESET\n"))
        self.assertEqual(code, 0)
        self.assertEqual(text.splitlines()[0], "忘了遥控器配对密码：重置会删除它，并吊销本机全部 0 台遥控器（之后用新密码重新配对）。")
        self.assertIn("确认重置？输入 RESET 回车：", text)
        self.assertIn("✓ 已重置，所有手机都已解除配对。运行 `agentj pair` 重新配对", text)
        self.assertNotIn("--phone", text, "no phone: exactly the old wording")
        self.assertFalse(gate.is_set(self.st))
        ctl.assert_not_called()

    def test_no_paired_phone_without_confirmation_keeps_it(self):
        code, text, _ = self.run_cli(io.StringIO("no\n"))
        self.assertEqual(code, "没有重置。")
        gate.verify(self.st, OLD)

    def test_plain_reset_with_phones_hints_then_unpairs_as_before(self):
        self._pair()
        code, text, _ = self.run_cli(io.StringIO("RESET\n"))
        self.assertEqual(code, 0)
        self.assertIn("agentj passphrase reset --phone", text)
        self.assertEqual(self.st.devices(), {})
        self.assertFalse(gate.is_set(self.st))

    def test_phone_reset_types_the_new_password_here(self):
        self._pair()
        ctl = Mock(side_effect=[{"ok": True, "ticket": "tk", "by": "网页 · iOS Safari"}, {"ok": True, "kept": 1}])
        code, text, ctl = self.run_cli(TTYIn([]), phone=True, secrets=[NEW, NEW], ctl=ctl)
        self.assertEqual(code, 0, text)
        first, second = (c.args[1] for c in ctl.call_args_list)
        self.assertEqual(first["cmd"], "passphrase_consent")
        self.assertRegex(first["code"], r"^[0-9]{6}$")
        self.assertIn(f"{first['code'][:3]} {first['code'][3:]}", text, "the terminal shows the card's match code")
        self.assertEqual(second, {"cmd": "passphrase_replace", "ticket": "tk", "new": NEW})
        self.assertNotIn(NEW, text)
        self.assertIn("保持配对", text)
        self.assertEqual(len(self.st.devices()), 1)

    def test_phone_denied_prompts_for_nothing(self):
        self._pair()
        code, text, ctl = self.run_cli(TTYIn([]), phone=True, ctl=Mock(return_value={"ok": False, "error": "denied"}))
        self.assertIn("没变", code)
        self.assertNotIn("新的遥控器配对密码", text, "never asked for a new one")
        self.assertEqual(ctl.call_count, 1)
        gate.verify(self.st, OLD)

    def test_agent_cannot_use_the_phone_path(self):
        self._pair()
        code, _, ctl = self.run_cli(io.StringIO(NEW + "\n" + NEW + "\n"), phone=True)      # no terminal (a pipe)
        self.assertIn("Agent", code)
        ctl.assert_not_called()
        code, _, ctl = self.run_cli(TTYIn([]), phone=True, secrets=[NEW, NEW], env={**self.env, "AGENTJ_FENCED": "1"})
        self.assertIn("Agent", code)
        ctl.assert_not_called()
        gate.verify(self.st, OLD)

    def test_phone_path_without_paired_phone_points_to_plain_reset(self):
        code, _, ctl = self.run_cli(TTYIn([]), phone=True)
        self.assertIn("agentj passphrase reset", code)
        ctl.assert_not_called()
        gate.verify(self.st, OLD)

    def test_cli_parser_knows_the_flag_once(self):
        src = (pathlib.Path(cli.__file__)).read_text()
        self.assertEqual(src.count('sub.add_parser("passphrase"'), 1)
        self.assertIn('pp.add_argument("--phone"', src)


class Copy(unittest.TestCase):
    def test_where_it_is_kept_and_how_to_reset(self):
        for text in (cli.PASS_WHERE,):
            self.assertIn("只存在这台Agent J 主机上", text)
            self.assertIn("kept only on this Agent J host", text)
            self.assertIn("席位卡", text)
            self.assertIn("agentj passphrase reset --phone", text)
        self.assertIn("agentj passphrase reset --phone", gate.MESSAGES["exists"])
        from agentj import handover
        src = pathlib.Path(handover.__file__).read_text()
        self.assertIn("agentj passphrase reset --phone", src)
        self.assertIn("只存在这台Agent J 主机上", src)


if __name__ == "__main__":
    unittest.main()
