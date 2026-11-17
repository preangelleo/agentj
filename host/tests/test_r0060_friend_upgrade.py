"""R-0060 (security r2): an upgrade asked for by a friend — a Same owner device included — never runs on the friend's authority.

Through the real serve control socket (Host.on_ctl), the real elevate.sock (Elevate.on_client) and the real CLI
(`cli.cmd_update` → `upgrade_friend.guard`):
- every mutating entrypoint in a friend turn (apply, --yes, --reinstall, --wheel, --authorization, rollback): the underlying
  call never runs; `reason: friend_request` (exit 10) with the fixed `reply_friend:` line; ONE owner card + notice;
- the version shown is the host's own record only, never the CLI's / Agent's; unknown → "?";
- fail closed for an Agent caller: no socket, AGENTJ_STATE_DIR elsewhere, timeout, exception, malformed answers, fence
  socket failure, AGENTJ_PERM_* marker only → `host_unverified`; no marker + no host → the old path (owner terminal);
- 10 concurrent requests → 1 card + 1 notice; the owner's own live P203 card is reused, not replaced; per-friend rate limit;
- the owner's signed /update answers the card (phone upgrade stub); 「先不升」 → nothing; same_owner ≡ friend_task;
- fenced: elevate op `friend` answers; pending → service, prepare, rollback, marker, clear refused during a friend turn,
  allowed otherwise; ping still answers;
- r3: the caller is decided by process ancestry (the owner's separate harness session is not the friend); wechat_task and
  official_assist are gated like friend_task, scheduled_task is not.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import cli, elevate, names, provenance, serve, update, upgrade_friend  # noqa: E402

from test_l1 import Phone, _owner_host, _ready, _state  # noqa: E402
from test_controls import sign  # noqa: E402

AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "OPENCODE",
             "AGENTJ_PERM_TOKEN", "AGENTJ_PERM_SOCK", "AGENTJ_FENCED")
LATEST = "99.0.1"
NEWER = {"status": "newer", "latest": LATEST, "current": update.__version__, "command": "x"}
FID = "AJ-TEST-0000-0000"
MODES = {   # entrypoint → (argparse namespace overrides, the underlying call that must not run)
    "apply": ({}, "agentj.update.apply"),
    "apply --yes": ({"yes": True}, "agentj.update.apply"),
    "apply --reinstall": ({"reinstall": True}, "agentj.update.repair_install"),
    "apply --wheel": ({"wheel": "/x.whl", "version": LATEST}, "agentj.local_update.stage"),
    "apply --authorization": ({"authorization": "AJUP-AAAAA-AAAAA-AAAAA-AAAAA"}, "agentj.update.authorized_apply"),
    "rollback": ({"mode": "rollback"}, "agentj.reset.run_rollback"),
}


class FakeAgent:
    kind, status, halting = "claude", "idle", False
    native_source = {"kind": "terminal", "role": "owner"}

    def __init__(self, send=None):
        self.q = NS(qsize=lambda: 0, empty=lambda: True)
        self.cur_send = send

    def submit(self, x):
        pass


def same_owner_send():
    return NS(source=provenance.HostDelegated({"kind": "same_owner", "role": "task"}), fid=FID)


def friend_task_send():          # serve._friend_main: the owner handed a friend's message to the main Agent
    return NS(source={"kind": "friend_task", "role": "task"}, fid=FID)


def owner_send():
    return NS(source=provenance.phone(), fid=None)


def ns(**over):
    a = dict(mode="apply", yes=False, version=None, authorization=None, from_email=None, wheel=None, reinstall=False,
             json=False, switch=None)
    a.update(over)
    return NS(**a)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.st = _state(self.tmp.name)
        work = pathlib.Path(self.tmp.name) / "work"
        work.mkdir()
        self.env = mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root),
                                                "CLAUDE_CONFIG_DIR": str(pathlib.Path(self.tmp.name) / "cc"),
                                                "XDG_CONFIG_HOME": str(pathlib.Path(self.tmp.name) / "config")})
        self.env.start()
        self.st.set_agent_config("claude", str(work))
        self.known(LATEST)
        self.sent = []
        self.host = _owner_host(self.st, self.sent)
        self.host.agent = FakeAgent()
        # the CLI under test runs in this process: by default it stands for a process inside the served Agent's tree
        # (r3 F1: serve decides by kernel ancestry when the peer pid is known; the harness marker alone does not count)
        self.agent_tree = {os.getpid()}
        self.host._agent_pids = lambda: set(self.agent_tree)
        self.host.peers = NS(store=NS(get=lambda fid: {"card": {"name": "Air"}} if fid == FID else None))
        self.notices = []
        real = self.host.agent_notice
        self.host.agent_notice = lambda text, **kw: (self.notices.append(text), real(text, **kw))[1]
        self.ph = Phone(self.st)
        self.s = _ready(self.host, self.ph)
        self.s.p33 = True

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def known(self, latest):
        """The host's own daily-check record (update.json)."""
        update._rec_path(self.st).write_text(json.dumps({"latest": latest} if latest else {}))

    def got(self, t):
        return [o for _, o in self.sent if o.get("t") == t]

    async def serve_ctl(self):
        path = str(self.st.sock_path)
        if os.path.exists(path):
            os.unlink(path)
        return await asyncio.start_unix_server(self.host.on_ctl, path=path)

    def run_cli(self, *, agent="CLAUDECODE", check=NEWER, env=None, **over):
        """The real `agentj update …` (cmd_update) with every mutating call stubbed. → (exit, block, {call: n})"""
        calls = {}

        def rec(name, value):
            def f(*a, **kw):
                calls[name] = calls.get(name, 0) + 1
                return value
            return f
        ok = {"result": "ok", "reason": "upgraded", "from": update.__version__, "to": LATEST, "service": "unchanged", "exit": 0}
        out = io.StringIO()
        saved = {k: os.environ.pop(k, None) for k in AGENT_ENV}
        if agent:
            os.environ[agent] = "1"
        try:
            with mock.patch.dict(os.environ, env or {}), \
                 mock.patch.object(update, "check", return_value=dict(check)), \
                 mock.patch.object(update, "market_managed", return_value=False), \
                 mock.patch("agentj.update.apply", side_effect=rec("agentj.update.apply", ok)), \
                 mock.patch("agentj.update.repair_install", side_effect=rec("agentj.update.repair_install", ok)), \
                 mock.patch("agentj.update.authorized_apply", side_effect=rec("agentj.update.authorized_apply", ok)), \
                 mock.patch("agentj.local_update.stage", side_effect=rec("agentj.local_update.stage", "/x")), \
                 mock.patch("agentj.local_update.launch", side_effect=rec("agentj.local_update.launch", None)), \
                 mock.patch("agentj.local_update.wait_result", return_value={"result": "ok"}), \
                 mock.patch("agentj.reset.run_rollback", side_effect=rec("agentj.reset.run_rollback", {"ok": False, "why": "x"})), \
                 mock.patch("agentj.cli._upgrade_gate", return_value=None), \
                 mock.patch("sys.stdout", out):
                try:
                    cli.cmd_update(ns(**over))
                    code = None
                except SystemExit as e:
                    code = e.code
        finally:
            for k in AGENT_ENV:
                os.environ.pop(k, None)
            os.environ.update({k: v for k, v in saved.items() if v is not None})
        block = "\n".join(ln for ln in out.getvalue().splitlines() if not ln.startswith('{"ev"'))   # host events="jsonl"
        return code, block, calls

    def with_serve(self, fn):
        async def go():
            srv = await self.serve_ctl()
            try:
                return await asyncio.to_thread(fn)
            finally:
                await asyncio.sleep(0.05)
                srv.close()
                await srv.wait_closed()
        return asyncio.run(go())


class FriendRequest(Base):
    def check_refused(self, code, block, calls, to=LATEST):
        self.assertEqual(calls, {}, "no mutating call on a friend's request")
        self.assertEqual(code, 10)
        self.assertIn("UPGRADE_RESULT refused", block)
        self.assertIn("reason: friend_request", block)
        self.assertIn(f"reply_friend: 这台 Agent J 主机现在是 {update.__version__}，最新版是 {to}。", block)
        self.assertIn("已请主人在遥控器确认", block)
        self.assertIn("waiting for the owner to confirm on the remote", block)
        self.assertIn("next: reply to the friend with the reply_friend line", block)
        self.assertNotIn(FID, block, "no friend id in the block")
        self.assertNotIn("Air", block, "no friend-supplied text in the Agent's block")

    def test_every_mutating_entrypoint_is_gated(self):
        self.host.agent.cur_send = same_owner_send()
        res = self.with_serve(lambda: {m: self.run_cli(**over) for m, (over, _) in MODES.items()})
        for m, r in res.items():
            with self.subTest(entrypoint=m):
                self.check_refused(*r)
        self.assertEqual(len(self.got("upg_card")), 1, "ONE card for all of them")
        self.assertEqual(self.got("upg_card")[0]["to"], LATEST)
        self.assertEqual(self.host.upg["friend"], ("same_owner", FID))
        self.assertEqual(len(self.notices), 1)
        self.assertIn("你的另一台设备「Air」请这台 Agent J 主机升级到 99.0.1", self.notices[0])
        self.assertIn("/update", self.notices[0])
        self.assertNotIn(FID, self.notices[0])

    def test_version_is_the_hosts_record_only(self):
        self.known(None)
        self.host.agent.cur_send = same_owner_send()
        code, block, calls = self.with_serve(lambda: self.run_cli(check={**NEWER, "latest": "1000000000.0.0"}))
        self.check_refused(code, block, calls, to="?")
        self.assertNotIn("1000000000", block)
        self.assertNotIn("1000000000", "".join(self.notices))
        self.assertEqual(self.got("upg_card")[0]["to"], "")

    def test_ordinary_friend_handover_behaves_the_same(self):
        self.host.agent.cur_send = same_owner_send()
        a = self.with_serve(lambda: self.run_cli())
        self.host.agent.cur_send = friend_task_send()
        b = self.with_serve(lambda: self.run_cli())
        self.assertEqual(a, b, "identical block and calls")
        self.assertEqual(len(self.got("upg_card")), 1, "the live card is reused for the next friend")

    def test_owner_confirm_runs_the_phone_upgrade_deny_does_nothing(self):
        self.host.agent.cur_send = same_owner_send()
        self.with_serve(lambda: self.run_cli())
        rid = self.host.upg["id"]
        launched = []

        async def fake_phone_upgrade(self_, turn, by):
            launched.append(by)

        async def deny():
            await self.host._app(self.s, {"t": "upg_card_off", "id": rid})
            await asyncio.sleep(0.05)
        with mock.patch.object(serve.Host, "phone_upgrade", new=fake_phone_upgrade):
            asyncio.run(deny())
        self.assertIsNone(self.host.upg)
        self.assertEqual(self.got("upg_card_done")[-1]["result"], "cancelled")
        self.assertEqual(launched, [], "「先不升」 starts nothing")
        # within the rate limit the same friend gets no second card / notice, is still refused, and is told so (r3)
        code, block, calls = self.with_serve(lambda: self.run_cli())
        self.assertEqual((code, calls), (10, {}))
        v = update.__version__
        self.assertIn("reason: friend_request", block)
        self.assertIn("\nreply_friend: " + f"这台 Agent J 主机现在是 {v}，最新版是 {LATEST}。好友的请求不能代替主人授权升级；主人最近已经被问过，"
                      "这次没有再发确认卡，主人可以随时在遥控器发 /update。 / " + f"This computer runs {v}; the latest is {LATEST}. "
                      "A friend cannot authorise an upgrade; the owner was asked recently, so no new card was sent — the owner "
                      "can send /update in the remote at any time.\n", block + "\n")
        self.assertIn("why: " + update.FRIEND_RECENT_WHY[0] + " / " + update.FRIEND_RECENT_WHY[1], block)
        self.assertIn("next: " + update.FRIEND_RECENT_NEXT, block)
        for stale in ("已请主人在遥控器确认", "waiting for the owner to confirm", "主人的遥控器上已出升级确认卡", "An upgrade card is on",
                      "put the card on the remote"):
            self.assertNotIn(stale, block, "no card is on the remote now")
        self.assertIsNone(self.host.upg)
        self.assertEqual(len(self.got("upg_card")), 1)
        self.assertEqual(len(self.notices), 1)
        # after the window: a new card, which the owner's signed /update answers
        self.host.__dict__["_upg_friend_seen"].clear()
        self.with_serve(lambda: self.run_cli())
        rid2 = self.host.upg["id"]
        self.assertNotEqual(rid, rid2)

        async def confirm():       # 「现在升级」 = the page's api.upgradeNow() = this phone's own signed /update
            await self.host._app(self.s, sign(self.ph, "update", {}, {"t": "slash", "cmd": "update"}))
            await asyncio.sleep(0.05)
        with mock.patch("agentj.phone_update.reserve", return_value=True), \
             mock.patch.object(serve.Host, "phone_upgrade", new=fake_phone_upgrade):
            asyncio.run(confirm())
        self.assertEqual(len(launched), 1, "the owner's confirm starts the normal signed upgrade job")
        done = self.got("upg_card_done")[-1]
        self.assertEqual((done["id"], done["result"]), (rid2, "started"))

    def test_terminal_owner_and_owner_turn_are_not_gated(self):
        self.host.agent.cur_send = same_owner_send()
        self.agent_tree = set()                  # the owner's terminal: outside serve's and the Agent's process tree
        terminal = self.with_serve(lambda: self.run_cli(agent=None))
        self.agent_tree = {os.getpid()}
        self.host.agent.cur_send = owner_send()
        owner = self.with_serve(lambda: self.run_cli())
        for code, block, calls in (terminal, owner):
            self.assertEqual(calls, {"agentj.update.apply": 1}, "the ordinary apply path")
            self.assertNotIn("friend_request", block)
        self.assertEqual(self.got("upg_card"), [])
        self.assertEqual(self.notices, [])


    def test_owners_separate_harness_session_during_a_friend_turn(self):
        """r3 F1: the owner's own other Claude session (CLAUDECODE set, outside the served Agent's tree) is not the friend."""
        self.agent_tree = set()
        for send in (friend_task_send(), same_owner_send()):
            self.host.agent.cur_send = send
            code, block, calls = self.with_serve(lambda: self.run_cli(agent="CLAUDECODE"))
            self.assertEqual(calls, {"agentj.update.apply": 1})
            self.assertNotIn("friend_request", block)
            self.assertNotIn("host_unverified", block)
        self.assertEqual(self.got("upg_card"), [])
        self.assertEqual(self.notices, [], "no false notice")
        self.agent_tree = {os.getpid()}             # … while the served Agent itself still is gated
        self.check_refused(*self.with_serve(lambda: self.run_cli(agent="CLAUDECODE")))

    def test_third_party_task_kinds_and_scheduled_task(self):
        """r3 F2: WeChat requests and official assist carry third-party data like friend_task; scheduled_task is the
        owner's own automation and is not gated."""
        for kind, line in (("wechat_task", "一条微信消息请这台 Agent J 主机升级"), ("official_assist", "一个官方远程协助任务请这台 Agent J 主机升级")):
            with self.subTest(kind=kind):
                self.host.agent.cur_send = NS(source=provenance.HostDelegated({"kind": kind, "role": "task"}))
                self.host.upg = None
                self.check_refused(*self.with_serve(lambda: self.run_cli()))
                self.assertIn(line, self.notices[-1])
        self.host.agent.cur_send = NS(source=provenance.HostDelegated({"kind": "scheduled_task", "role": "task"}))
        code, block, calls = self.with_serve(lambda: self.run_cli())
        self.assertEqual(calls, {"agentj.update.apply": 1})
        self.assertEqual(upgrade_friend.KINDS, ("same_owner", "friend_task", "wechat_task", "official_assist"))


class FailClosed(Base):
    def check_unverified(self, r):
        code, block, calls = r
        self.assertEqual(calls, {})
        self.assertEqual(code, 10)
        self.assertIn("reason: host_unverified", block)
        self.assertIn("/update", block)
        self.assertNotIn("reply_friend", block)

    def test_agent_without_a_host_answer_is_refused(self):
        self.host.agent.cur_send = owner_send()
        for m, (over, _) in MODES.items():
            with self.subTest(entrypoint=m, case="no socket"):
                self.check_unverified(self.run_cli(**over))
        with self.subTest(case="AGENTJ_STATE_DIR elsewhere"):
            empty = pathlib.Path(self.tmp.name) / "elsewhere"
            empty.mkdir()
            self.check_unverified(self.with_serve(lambda: self.run_cli(env={"AGENTJ_STATE_DIR": str(empty)})))
        with self.subTest(case="AGENTJ_PERM_SOCK only"):
            self.check_unverified(self.run_cli(agent="AGENTJ_PERM_SOCK"))
        for label, effect in (("timeout", names.ServeBusy("timeout")), ("exception", OSError("x")),
                              ("bad json", ValueError("bad_answer"))):
            with self.subTest(case=label), mock.patch("agentj.names.ctl_call", side_effect=effect):
                self.check_unverified(self.run_cli())
        for answer in ({}, {"ok": True}, {"ok": False, "friend": None}, {"ok": True, "friend": "someone"}, {"ok": "yes", "friend": None}):
            with self.subTest(answer=answer), mock.patch("agentj.names.ctl_call", return_value=answer):
                self.check_unverified(self.run_cli())
        self.assertEqual(self.got("upg_card"), [])

    def test_no_host_and_no_agent_marker_keeps_the_old_path(self):
        self.host.agent.cur_send = same_owner_send()
        code, block, calls = self.run_cli(agent=None)
        self.assertEqual(calls, {"agentj.update.apply": 1}, "the owner at a terminal without a running host: unchanged")

    def test_fence_socket_failure_is_refused(self):
        with mock.patch("agentj.fence.inside", return_value=True), \
             mock.patch.dict(os.environ, {elevate.ENV: str(pathlib.Path(self.tmp.name) / "none" / "elevate.sock")}):
            self.check_unverified(self.run_cli())
            with mock.patch.object(update, "_host", return_value={"ok": True}):
                self.check_unverified(self.run_cli())


class Cards(Base):
    def test_ten_concurrent_requests_one_card_one_notice(self):
        self.host.agent.cur_send = same_owner_send()

        async def go():
            return await asyncio.gather(*[upgrade_friend.answer(self.host, caller="agent") for _ in range(10)])
        res = asyncio.run(go())
        self.assertTrue(all(r["ok"] and r["friend"] == "same_owner" for r in res))
        self.assertEqual(len(self.got("upg_card")), 1)
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(sorted(r["card"] for r in res), ["new"] + ["reused"] * 9)

    def test_the_owners_live_card_is_reused_not_replaced(self):
        async def go():
            own = self.host.upg_open()            # the owner's own ≡ /update card (P203)
            self.host.agent.cur_send = same_owner_send()
            r = await upgrade_friend.answer(self.host, caller="agent")
            await asyncio.sleep(0.02)
            return own, r
        own, r = asyncio.run(go())
        self.assertEqual(r["card"], "reused")
        self.assertEqual(self.host.upg["id"], own["id"])
        self.assertEqual([d for d in self.got("upg_card_done") if d.get("result") == "replaced"], [])
        self.assertEqual(len(self.got("upg_card")), 1)

    def test_rate_limit_window(self):
        self.host.agent.cur_send = same_owner_send()
        key = ("same_owner", FID)

        async def ask():
            return await upgrade_friend.answer(self.host, caller="agent")

        async def end():
            self.host.upg_end("cancelled")
            await asyncio.sleep(0.01)
        self.assertEqual(asyncio.run(ask())["card"], "new")
        asyncio.run(end())
        seen = self.host.__dict__["_upg_friend_seen"]
        seen[key] -= upgrade_friend.RATE - 5          # (the stored monotonic stamp, aged; asyncio's clock left alone)
        self.assertEqual(asyncio.run(ask())["card"], "rate_limited")
        seen[key] -= 10
        self.assertEqual(asyncio.run(ask())["card"], "new")
        self.assertEqual(len(self.notices), 2)

    def test_no_friend_no_card(self):
        self.host.agent.cur_send = owner_send()
        self.assertEqual(asyncio.run(upgrade_friend.answer(self.host, caller="agent")), {"ok": True, "friend": None})
        self.assertEqual(self.got("upg_card"), [])


class Fenced(Base):
    """The real elevate.sock (serve's Elevate.on_client) and the fenced CLI's update._host."""
    def run_fenced(self, fn):
        async def go():
            await self.host.elevate.start()
            try:
                with mock.patch.dict(os.environ, {elevate.ENV: str(self.st.perm_dir / elevate.SOCK_NAME)}):
                    return await asyncio.to_thread(fn)
            finally:
                await asyncio.sleep(0.05)
                await self.host.elevate.stop()
        return asyncio.run(go())

    def test_fenced_friend_request_over_elevate(self):
        self.host.agent.cur_send = same_owner_send()
        with mock.patch("agentj.fence.inside", return_value=True):
            code, block, calls = self.run_fenced(lambda: self.run_cli(agent="AGENTJ_FENCED"))
        self.assertEqual((code, calls), (10, {}))
        self.assertIn("reason: friend_request", block)
        self.assertIn("主人在遥控器发 /update 即可", block)
        self.assertIn("the owner sends /update in the remote", block)
        self.assertEqual(len(self.got("upg_card")), 1)
        self.assertIn("在隔离里运行，不能自己升级", self.notices[0])

    def test_fenced_owner_turn_goes_on(self):
        self.host.agent.cur_send = owner_send()
        with mock.patch("agentj.fence.inside", return_value=True):
            code, block, calls = self.run_fenced(lambda: self.run_cli(agent="AGENTJ_FENCED"))
        self.assertEqual(calls, {"agentj.update.apply": 1})
        self.assertEqual(self.got("upg_card"), [])

    def test_mutating_host_ops_refused_in_a_friend_turn(self):
        view = NS(perm_dir=self.st.perm_dir)
        ops = [("pending", {"to": "9.9.9"}), ("service", {"to": "9.9.9"}), ("prepare", {"to": "9.9.9"}),
               ("rollback", {"to": "9.9.9"}), ("marker", {"to": "9.9.9"}), ("clear", {})]
        reached = []
        real = update.host_op

        def host_op(st, req, mem, run=None):
            reached.append(req["op"])
            return {"ok": True, "op": req["op"]} if req["op"] in dict(ops) else real(st, req, mem, run)

        def seq():
            return [elevate.client_request(view, {"t": "upgrade", "op": op, **kw}, 10)
                    for op, kw in ops]
        with mock.patch.object(update, "host_op", side_effect=host_op):
            self.host.agent.cur_send = same_owner_send()
            friend = self.run_fenced(seq)
            self.host.agent.cur_send = owner_send()
            owner = self.run_fenced(seq)
        self.assertEqual([r.get("why") for r in friend], ["friend_request"] * 6)
        self.assertTrue(all(r.get("ok") is False for r in friend))
        self.assertEqual([r.get("op") for r in owner], [op for op, _ in ops], "owner turns unchanged")
        self.assertEqual(reached, [op for op, _ in ops], "host_op ran only in the owner turn")
        self.host.agent.cur_send = same_owner_send()
        pong = self.run_fenced(lambda: elevate.client_request(view, {"t": "upgrade", "op": "ping"}, 10))
        self.assertEqual(pong.get("ok"), True, "read-only ops are not refused in a friend turn")


if __name__ == "__main__":
    unittest.main()
