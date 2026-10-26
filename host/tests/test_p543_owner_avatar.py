"""P543 (#307 / #308): owner-channel avatars get the main conversation's tools and approvals; friends stay tool-less.

- eligibility: a shared plan on a host that can approve → full; a saved avatar plan / a stand-in host → the old executor.
- Claude Code: the main argv (no `--tools ""`, the permission tool, the danger hook, the owner's settings and MCP) plus the
  avatar text that says "search first, cite sources, a channel never approves"; a one-time side claim, dropped after.
- ① a live-news question: the WebSearch tool use is not a channel violation; the answer (with its source link) is replied.
- ② a step that needs approval: the card goes up with the avatar's origin line, the channel gets 「已转到 Agent J 网页」 once,
  the allow is a signed phone decision; the full level / batch grants never pass an avatar's step; AskUserQuestion denied.
- the side claim never cuts the main Agent's permission connection and is spent by its first use.
- ③ friends of another owner keep `--tools ""`; ④ same-owner tasks run in the main conversation (main tools, its cards).
- #308: the 「来自微信」 page carries the words and attachments, the manifest path only in the card's paths.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import channel_card, owner_avatar, peer_session, wechat  # noqa: E402

from test_p336_approval_level import Base  # noqa: E402


def stream(*events):
    return "".join(json.dumps(e) + "\n" for e in events)


NEWS = stream({"type": "system", "subtype": "init", "session_id": "sess-1", "tools": ["Bash", "WebSearch", "WebFetch"],
               "mcp_servers": [{"name": "agentj"}]},
              {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch",
                                                             "input": {"query": "越疆科技 今天 上涨 原因"}}]}},
              {"type": "result", "session_id": "sess-1", "result": json.dumps(
                  {"decision": "reply", "text": "越疆科技今天上涨主要因为……来源：https://news.example.com/a", "topic": "news"},
                  ensure_ascii=False), "usage": {"input_tokens": 10, "output_tokens": 5}})


class Store:
    def __init__(self, avatar=None):
        self.data = {"avatar": avatar or {}}
        self.sids = {}
        self.path = pathlib.Path("/x/wechat.json")

    def session_id(self, fid, kind):
        return self.sids.get((fid, kind))

    def set_session_id(self, fid, kind, sid):
        self.sids[(fid, kind)] = sid


class FakeHost:
    def __init__(self, root, kind="claude"):
        self.st = NS(root=root, perm_dir=str(root), perm_sock_path=root / "perm.sock", log=lambda *a, **k: None,
                     agent_config=lambda: {})
        (root / "work").mkdir(exist_ok=True)
        self.agent_cfg = {"kind": kind, "dir": str(root / "work"), "fence": False}
        self.lang, self.ask_ttl, self.preferences = "zh", 5, {}
        self.sides, self.dropped = [], []

    async def ask(self, *a, **k):
        return {"behavior": "deny", "message": "x"}

    def side_perm_env(self, label, on_card=None):
        side = {"token": "tok", "label": label, "on_card": on_card, "claimed": asyncio.Event()}
        side["claimed"].set()
        self.sides.append(side)
        return {"AGENTJ_PERM_SOCK": "s", "AGENTJ_PERM_TOKEN": "tok", "AGENTJ_PERM_WAIT": "9"}, side

    def side_perm_drop(self, side):
        self.dropped.append(side)


def sessions(host, avatar=None):
    s = wechat.Sessions(host, Store(avatar), max_active=1)
    return s


class Eligibility(unittest.TestCase):
    def test_shared_plan_on_a_real_host_is_full(self):
        with tempfile.TemporaryDirectory() as d:
            host = FakeHost(pathlib.Path(d))
            self.assertTrue(owner_avatar.eligible(sessions(host)))
            self.assertFalse(owner_avatar.eligible(sessions(host, {"config": {"kind": "codex", "model": "m"}})))
            no_side = NS(st=host.st, agent_cfg=host.agent_cfg, ask=host.ask)
            self.assertFalse(owner_avatar.eligible(sessions(no_side)))       # Claude needs the side permission channel
            stand_in = NS(st=host.st, agent_cfg=host.agent_cfg)
            self.assertFalse(owner_avatar.eligible(sessions(stand_in)))      # no approval path → never tools
            host.agent_cfg = {**host.agent_cfg, "kind": "kimi"}
            self.assertFalse(owner_avatar.eligible(sessions(host)))


class ClaudeFull(unittest.TestCase):
    def run_turn(self, out, text="越疆科技今天为什么涨", on_card=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        host = FakeHost(pathlib.Path(self.tmp.name))
        s = sessions(host)
        seen = {}

        async def fake_stream(self_, argv, cwd, env, line, side, gen):
            seen.update(argv=argv, cwd=cwd, env=env, line=line, side=side)
            return out
        with mock.patch.object(owner_avatar.OwnerAvatar, "_stream", fake_stream), \
                mock.patch("agentj.agent.hooks_blocked", return_value=None):
            s.source, s.on_card = "wechat", on_card
            res = asyncio.run(s.turn({"id": "wechat-owner"}, {"auto": {"mode": "on"}}, text))
        return host, s, res, seen

    def test_main_argv_not_toolless(self):
        host, s, res, seen = self.run_turn(NEWS)
        a = seen["argv"]
        self.assertNotIn("--tools", a)
        self.assertNotIn("--strict-mcp-config", a)
        self.assertNotIn("--safe-mode", a)
        self.assertIn("--permission-prompt-tool", a)
        settings = json.loads(a[a.index("--settings") + 1])
        self.assertIn("agentj.danger hook", settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        system = a[a.index("--append-system-prompt") + 1]
        self.assertEqual(system, owner_avatar.SYSTEM)
        self.assertIn("search the web first", system)
        self.assertIn("source links", system)
        self.assertIn("never approves", system)
        self.assertEqual(seen["cwd"], str(pathlib.Path(self.tmp.name) / "work"))
        # the side claim: a 0600 one-time file path, never the token itself in the environment
        self.assertNotIn("AGENTJ_PERM_TOKEN", seen["env"])
        self.assertTrue(seen["env"]["AGENTJ_PERM_CLAIM"].split("/")[-1].startswith("task-claim-"))
        self.assertFalse(pathlib.Path(seen["env"]["AGENTJ_PERM_CLAIM"]).exists())     # removed after the turn
        self.assertEqual(host.dropped, host.sides)
        self.assertEqual(host.sides[0]["label"], "来自微信分身")

    def test_live_news_search_is_answered_with_its_source(self):
        host, s, res, seen = self.run_turn(NEWS)
        self.assertEqual(res.decision, "reply")
        self.assertIsNone(res.error)
        self.assertEqual(res.tools, [])
        self.assertEqual(res.used, ["WebSearch"])
        self.assertIn("https://news.example.com/a", res.text)
        self.assertEqual(s.store.sids[("wechat-owner", "owner-claude")], "sess-1")

    def test_resume_and_failed_harness_reset(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        host = FakeHost(pathlib.Path(self.tmp.name))
        s = sessions(host)
        s.store.sids[("wechat-owner", "owner-claude")] = "old"
        seen = {}

        async def fake_stream(self_, argv, cwd, env, line, side, gen):
            seen["argv"] = argv
            return stream({"type": "result", "is_error": True, "result": "boom"})
        with mock.patch.object(owner_avatar.OwnerAvatar, "_stream", fake_stream), \
                mock.patch("agentj.agent.hooks_blocked", return_value=None):
            res = asyncio.run(s.turn({"id": "wechat-owner"}, {}, "hi"))
        self.assertEqual(seen["argv"][seen["argv"].index("--resume") + 1], "old")
        self.assertEqual(res.error, "harness")
        self.assertIsNone(s.store.sids[("wechat-owner", "owner-claude")])

    def test_two_cards_one_channel_note(self):
        """Two steps waiting in one turn: the channel hears 「已转到 Agent J 网页」 once."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        host = FakeHost(pathlib.Path(self.tmp.name))
        s = sessions(host)
        notes = []

        async def fake_stream(self_, argv, cwd, env, line, side, gen):
            await side["on_card"]()
            await side["on_card"]()
            return NEWS

        async def note():
            notes.append(1)
        with mock.patch.object(owner_avatar.OwnerAvatar, "_stream", fake_stream), \
                mock.patch("agentj.agent.hooks_blocked", return_value=None):
            s.source, s.on_card = "telegram", note
            res = asyncio.run(s.turn({"id": "external-owner"}, {}, "删掉 build 目录"))
        self.assertEqual(notes, [1])
        self.assertTrue(res.carded)
        self.assertEqual(host.sides[0]["label"], "来自 Telegram 分身")

    def test_assistant_replies_after_tool_use(self):
        """WeChat.assistant: a full avatar's tool use is not `res.tools` (which would hold the reply)."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        host = FakeHost(pathlib.Path(self.tmp.name))
        ch = wechat.WeChat.__new__(wechat.WeChat)
        ch.h, ch.sessions, ch.index = host, sessions(host), None

        async def fake_stream(self_, *a):
            return NEWS
        ch.model = ch.sessions.turn
        ch.store = NS(path=pathlib.Path("/x/wechat.json"))
        with mock.patch.object(owner_avatar.OwnerAvatar, "_stream", fake_stream), \
                mock.patch("agentj.agent.hooks_blocked", return_value=None), \
                mock.patch("agentj.peer_guard.SecretIndex.build", return_value=None):
            reply = asyncio.run(ch.assistant("越疆科技今天为什么涨"))
        self.assertIn("https://news.example.com/a", reply)


class CodexFull(unittest.TestCase):
    def test_thread_resumes_with_avatar_instructions_and_approvals_are_the_avatars(self):
        calls, asks = [], []

        class H(FakeHost):
            async def ask(self, tool, inp, gone=None, **kw):
                asks.append((tool, kw))
                return {"behavior": "allow"}
        with tempfile.TemporaryDirectory() as d:
            host = H(pathlib.Path(d), "codex")
            proxy = owner_avatar._Host(host, "来自邮件分身", None)
            runner = owner_avatar._codex(proxy, {**host.agent_cfg, "_workflow_ceo": True}, "thr-1")

            async def call(method, params, *a):
                calls.append((method, params))
                return {"thread": {"id": params.get("threadId", "new")}, "model": "m"}
            runner.call = call
            runner.policy = lambda: {"approvalPolicy": "untrusted", "sandbox": "danger-full-access"}
            asyncio.run(runner._thread())
            self.assertEqual(calls[0][0], "thread/resume")
            self.assertEqual(calls[0][1]["developerInstructions"], owner_avatar.SYSTEM)
            self.assertEqual(runner.tid, "thr-1")
            # the runner's approvals and failures: the avatar's, never the main page's
            asyncio.run(proxy.ask("Bash", {"command": "ls"}, None, batch=True))
            self.assertEqual(asks, [("Bash", {"avatar": "来自邮件分身"})])
            proxy.turn_failed()
            proxy.agent_notice("Codex 登录过期")
            self.assertEqual(proxy.notes, ["Codex 登录过期"])


class Approvals(Base):
    """serve.ask(avatar=…): the main floor and cards; never a main-conversation pass."""

    def test_full_level_never_passes_an_avatar_step_and_card_names_it(self):
        self.full()
        self.ask("rm -rf build")
        self.assertEqual(self.cards("Bash"), [])                    # the main conversation under `full`: no card
        res = self.ask("rm -rf build", avatar="来自微信分身", answer=True)
        self.assertEqual(res["behavior"], "allow")
        card = self.cards("Bash")[-1]
        self.assertIn("来自微信分身", card["summary"])

    def test_denied_card_is_a_deny_and_question_cards_are_refused(self):
        res = self.ask("rm -rf build", avatar="来自微信分身", answer=False)
        self.assertEqual(res["behavior"], "deny")
        res = self.ask({"questions": []}, tool="AskUserQuestion", avatar="来自微信分身")
        self.assertEqual(res["behavior"], "deny")

    def test_ask_for_tells_the_channel_once_and_waits_for_the_device(self):
        told = []

        async def go():
            t = asyncio.ensure_future(owner_avatar.ask_for(self.host, "来自微信分身", lambda: told.append(1), "Bash",
                                                           {"command": "rm -rf build"}))
            for _ in range(300):
                await asyncio.sleep(0.01)
                if told:
                    break
            self.assertFalse(t.done())                              # the channel's note never answers the card
            open_ = [o for o in self.cards() if o["id"] in self.host.asks]
            await self.host._answer(self.s, self.phone.answer(open_[-1], True))
            return await t
        with mock.patch.object(owner_avatar, "CARD_NOTICE_AFTER", 0.05):
            self.host.ask_ttl = 5
            res = asyncio.run(go())
        self.assertEqual(told, [1])
        self.assertEqual(res["behavior"], "allow")


class SideClaim(Base):
    class W:
        def __init__(self):
            self.out = []
            self.closed = False

        def write(self, b):
            self.out += [json.loads(x) for x in b.decode().splitlines() if x]

        async def drain(self):
            pass

        def close(self):
            self.closed = True

    def test_side_claim_is_one_time_and_never_cuts_the_main_connection(self):
        main_w = object()
        self.host.perm_w = main_w
        env, side = self.host.side_perm_env("来自微信分身")
        self.assertNotEqual(env["AGENTJ_PERM_TOKEN"], self.host.perm_token)

        async def go(tok, asks=()):
            r = asyncio.StreamReader()
            r.feed_data((json.dumps({"t": "claim", "token": tok}) + "\n").encode())
            for i, cmd in enumerate(asks):
                r.feed_data((json.dumps({"t": "ask", "id": i, "tool": "Bash", "input": {"command": cmd}}) + "\n").encode())
            r.feed_eof()
            w = self.W()
            with mock.patch.object(owner_avatar, "ask_for", new=mock.AsyncMock(return_value={"behavior": "deny", "message": "no"})) as af:
                await self.host.on_perm(r, w)
                await asyncio.sleep(0.05)
            return w, af
        w, af = asyncio.run(go(env["AGENTJ_PERM_TOKEN"], ["ls"]))
        self.assertEqual(w.out[0], {"t": "claimed"})
        self.assertTrue(side["claimed"].is_set())
        self.assertIs(self.host.perm_w, main_w)                     # the main Agent's connection is untouched
        self.assertEqual(af.await_args.args[1], "来自微信分身")
        w2, _ = asyncio.run(go(env["AGENTJ_PERM_TOKEN"]))            # spent
        self.assertEqual(w2.out, [])
        self.host.side_perm_drop(side)


class FriendsStayToolless(unittest.TestCase):
    def test_other_owner_friend_claude_has_no_tools(self):
        ps = peer_session.PeerSessions(NS(st=NS()), NS())
        with mock.patch.object(peer_session, "claude_flags", return_value=set(peer_session.CLAUDE_REQUIRED)):
            a = ps._claude_argv("claude", "sys", None, {})
        self.assertEqual(a[a.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", a)
        self.assertEqual(peer_session.CLAUDE_REQUIRED, ("--tools", "--strict-mcp-config", "--mcp-config", "--system-prompt"))

    def test_friend_tool_event_is_held(self):
        class P(peer_session.PeerSessions):
            async def _turn(self, *a):
                return peer_session.TurnResult("reply", "x", "", None, None, tools=["Bash"])
        ps = P(NS(st=NS(log=lambda *a, **k: None)), NS())
        res = asyncio.run(ps.turn({"id": "f1"}, {}, "run ls"))
        self.assertEqual((res.decision, res.error), ("ask_owner", "tool_call"))

    def test_same_owner_task_runs_in_the_main_conversation(self):
        from agentj import same_owner
        self.assertIn("host.agent.submit(send)", pathlib.Path(same_owner.__file__).read_text())


class ChannelCard(unittest.TestCase):
    def test_card_shows_words_not_path(self):
        fallback, card, files = channel_card.build("wechat", "你好，帮我看看这张图", ["/in/m.json", "/in/a.jpg"], "/in/m.json")
        self.assertEqual(fallback, "来自微信：你好，帮我看看这张图")
        self.assertNotIn("/in/", fallback)
        self.assertEqual(card["channel"]["text"], "你好，帮我看看这张图")
        self.assertEqual(card["channel"]["files"], ["a.jpg"])
        self.assertEqual(files, ["/in/a.jpg"])
        self.assertEqual(card["channel"]["paths"], ["/in/m.json", "/in/a.jpg"])
        fb, card, _ = channel_card.build("mail", "x" * 5000, [], "/m.json", "en")
        self.assertTrue(card["channel"]["cut"])
        self.assertTrue(fb.startswith("From email: ") and fb.endswith("…"))

    def test_fallback_text_makes_no_links_on_older_phones(self):
        fb, card, _ = channel_card.build("wechat", "看 [这里](https://x.example) <b>`x`</b>", [], "/m.json")
        self.assertNotIn("[", fb)
        self.assertNotIn("<", fb)
        self.assertEqual(card["channel"]["text"], "看 [这里](https://x.example) <b>`x`</b>")   # the card: plain text as sent

    def test_stand_in_host_gets_a_plain_notice(self):
        got = []
        channel_card.notice(NS(lang="zh", agent_notice=got.append), "telegram", "", ["/a/b.pdf"], "/m.json")
        self.assertEqual(got, ["来自 Telegram：附件 1 个"])


class WeChatInterim(unittest.TestCase):
    def test_confirm_text(self):
        self.assertEqual(wechat.confirm_text(NS(lang="zh")), "已转到 Agent J 网页，请在那里确认。")
        self.assertIn("Agent J Web App", wechat.confirm_text(NS(lang="en")))


if __name__ == "__main__":
    unittest.main()
