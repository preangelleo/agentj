"""P426 #57: OpenCode sub-agents back on, under the phone's rules (opencode_subagents.py, ADR-A168 "Sub-agents", G-A65).

Units: per-agent rules (a deny stays a deny, nothing looser than ours, explore's `* deny` keeps edits off), the v1 config
object answers like the list, the suffix check (trailing denies / scratch allow), plan → restart → verified, an owner's
interleaving keys or a wildcard name keep that agent denied, `task <n> allow` only for verified names, research keeps the
deny; adapter pieces with a stand-in client: a child's request (parentID) → the card names the sub-agent and the answer goes
to the child's own route (v2), a child's question / form, an unknown child adopted through its parent, a v2 child without
our rules is stopped, usage counted, the background "子 Agent 完成" line, status / doctor text.
Chains (stand-ins fakeopencode.py / fakeopencode2.py with sub-agents): high-risk mode opens general / explore, a sub-agent's
`rm` is a card naming it → reject → the file stays; a primary agent as a sub-agent is refused by the rules; default mode
routes a v2 child's request (before: dropped, the child hung). The real 1.18.34 / 2.0.31 / 2.1.1 rule probes are in
reports/qa/p426/opencode/.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import random
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import agent_opencode as oc  # noqa: E402
from agentj import agent_opencode2 as oc2  # noqa: E402
from agentj import fence, opencode_subagents as osa, provenance  # noqa: E402
from test_l1 import Phone, _owner_host as _host, _ready, _state  # noqa: E402
import fakeopencode  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
R = oc._rule
GENERAL = fakeopencode.DEFAULT_RULES[:9] + [R("todowrite", "*", "deny")]
EXPLORE = fakeopencode.DEFAULT_RULES[:9] + [R("*", "*", "deny")] + [R(t, "*", "allow") for t in ("grep", "glob", "bash", "read")]
PROBES = [("bash", "rm -rf x"), ("bash", "ls -la"), ("bash", "cat a > b"), ("read", ".env"), ("read", "README.md"),
          ("read", ".env.example"), ("edit", "a.py"), ("task", "general"), ("question", "*"), ("todowrite", "*"),
          ("webfetch", "https://x"), ("external_directory", "/etc/x"), ("my_mcp_send", "*"), ("glob", "*"), ("plan_enter", "*")]


def from_config(obj):
    """OpenCode 1.18's Permission.fromConfig: a string = (key, "*", action); an object = its patterns in order."""
    return [r for k, v in obj.items() for r in ([R(k, "*", v)] if isinstance(v, str) else [R(k, p, a) for p, a in v.items()])]


class Rules(unittest.TestCase):
    def test_task_allow_only_for_named_agents_and_never_in_research(self):
        r = oc.session_rules(fakeopencode.DEFAULT_RULES, subagents=["general"])
        self.assertEqual(oc.evaluate("task", "general", r), "allow")
        for other in ("build", "plan", "explore", "x*"):
            self.assertEqual(oc.evaluate("task", other, r), "deny", other)
        self.assertEqual(oc.evaluate("task", "general", oc.session_rules(fakeopencode.DEFAULT_RULES, True, ["general"])), "deny")
        self.assertEqual(oc.our_rules(), oc.our_rules(()))           # the default is unchanged (task * deny)

    def test_agent_rules_keep_denies_and_are_never_looser_than_ours(self):
        primary_denies = osa.denies(fakeopencode.DEFAULT_RULES + [R("bash", "cat secret*", "deny")])
        ours = oc.session_rules(fakeopencode.DEFAULT_RULES + [R("bash", "cat secret*", "deny")])
        g = osa.agent_rules_for(GENERAL, primary_denies)
        e = osa.agent_rules_for(EXPLORE, primary_denies)
        order = {"allow": 0, "ask": 1, "deny": 2}
        for perm, pat in PROBES + [("bash", "cat secret.txt")]:
            for inj, own in ((g, GENERAL), (e, EXPLORE)):
                got = oc.evaluate(perm, pat, own, inj)        # the child: its agent's rules, ours injected last
                self.assertGreaterEqual(order[got], order[oc.evaluate(perm, pat, ours)], (perm, pat, got))
                if oc.evaluate(perm, pat, own) == "deny":
                    self.assertEqual(got, "deny", ("the agent's own deny stays", perm, pat))
        ev = lambda rules, p, s: oc.evaluate(p, s, rules)  # noqa: E731
        self.assertEqual(ev(g, "bash", "rm -rf x"), "ask")
        self.assertEqual(ev(g, "bash", "ls -la"), "allow")
        self.assertEqual(ev(g, "read", ".env"), "ask")
        self.assertEqual(ev(g, "bash", "cat secret.txt"), "deny")        # the primary agent's deny reaches the child
        self.assertEqual(ev(g, "task", "general"), "deny")               # no nesting
        self.assertEqual(ev(e, "edit", "a.py"), "deny")                  # explore: `* deny` keeps edits off …
        self.assertEqual(ev(e, "bash", "rm -rf x"), "ask")               # … and its bash allow becomes ours (a card)
        self.assertEqual(ev(e, "bash", "ls"), "allow")
        self.assertEqual(ev(e, "grep", "*"), "allow")

    def test_v1_object_answers_like_the_list(self):
        rng = random.Random(426)
        for inj in (osa.agent_rules_for(GENERAL, []), osa.agent_rules_for(EXPLORE, osa.denies([R("bash", "git push *", "deny")]))):
            obj = osa.v1_object(inj)
            self.assertIsNotNone(obj)
            self.assertEqual(list(obj)[0], "*")
            for k in osa.ACTION_ONLY & set(obj):
                self.assertIsInstance(obj[k], str, k)                    # v1's schema: action only for these keys
            flat = from_config(obj)
            self.assertEqual(osa.expected(inj), [(r["permission"], r["pattern"], r["action"]) for r in flat])
            pats = ["rm -rf x", "ls", "ls -la", "git push origin", "a > b", ".env", "x.env.local", ".env.example", "*", "a.py"]
            perms = ["bash", "read", "edit", "task", "question", "todowrite", "grep", "external_directory", "zzz_mcp", "*"]
            for _ in range(400):
                p, s = rng.choice(perms), rng.choice(pats)
                self.assertEqual(oc.evaluate(p, s, flat), oc.evaluate(p, s, inj), (p, s))
        self.assertIsNone(osa.v1_object([R("*", "*", "ask"), R("mcp_*", "*", "deny")]))      # wildcard name: no exact object
        self.assertIsNone(osa.v1_object([R("*", "*", "ask"), R("webfetch", "https://x/*", "deny")]))   # action-only key

    def test_split_plan_restart_verified_conflict(self):
        prim = {"name": "build", "mode": "primary", "permission": fakeopencode.DEFAULT_RULES}
        live = lambda injected, extra=(), tail=(): [prim] + [  # noqa: E731  (1.18: natives, config, scratch allow)
            {"name": n, "mode": "subagent", "permission": r + from_config(osa.v1_object(injected[n]) if n in injected else {})
             + list(extra) + [fakeopencode.TOOL_OUT] + list(tail)} for n, r in (("general", GENERAL), ("explore", EXPLORE))]
        want, ok, why = osa.plan(live({}), "build", {})
        self.assertEqual((sorted(want), ok), (["explore", "general"], []))
        self.assertEqual(why, {"general": "pending", "explore": "pending"})
        want2, ok2, why2 = osa.plan(live(want), "build", want)
        self.assertEqual((want2, sorted(ok2), why2), (want, ["explore", "general"], {}))
        # a plugin's deny after the config's only tightens: in place, and the same own rules on every start (stable)
        plug = [R("browser", "*", "deny")]
        w1, _, _ = osa.plan(live({}, tail=plug), "build", {})
        w2, ok3, _ = osa.plan(live(w1, tail=plug), "build", w1)
        self.assertEqual((sorted(ok3), w2), (["explore", "general"], w1))
        # an allow after ours (an owner key that interleaved, a managed layer): not in place → that agent stays denied
        want4, ok4, why4 = osa.plan(live(want, [R("bash", "rm *", "allow")]), "build", want)
        self.assertEqual((ok4, why4), ([], {"general": "conflict", "explore": "conflict"}))
        self.assertEqual(want4, want, "no restart loop on a conflict")
        own, intact = osa.split(live(want)[1]["permission"], want["general"])
        self.assertTrue(intact)
        self.assertEqual(own, GENERAL + [fakeopencode.TOOL_OUT])
        # a primary agent never runs as a sub-agent; a name that could be a pattern is never written into a rule
        self.assertFalse(osa.capable({"name": "build", "mode": "primary", "permission": []}))
        self.assertFalse(osa.capable({"name": "re*", "mode": "subagent", "permission": []}))

    def test_content_keeps_the_owners_config(self):
        inj = {"general": osa.agent_rules_for(GENERAL, [])}
        c = json.loads(osa.content_with(oc.config_content('{"model":"a/b","agent":{"general":{"model":"x/y"}}}'), inj))
        self.assertEqual((c["model"], c["autoupdate"], c["agent"]["general"]["model"]), ("a/b", False, "x/y"))
        self.assertEqual(c["agent"]["general"]["permission"], osa.v1_object(inj["general"]))

    def test_texts(self):
        self.assertEqual(osa.state_text({"mode": "on", "names": ["general", "explore"]}), "OpenCode 子 Agent：已开启（general、explore）")
        self.assertIn("off", osa.state_text({"mode": "off", "why": "research"}, "en"))
        self.assertIn("OpenCode 自己的权限规则", osa.state_text({"mode": "native"}))
        self.assertEqual(osa.origin({"agent": "general", "title": "写报告"}, "zh"), "来自子 Agent：general · 写报告")
        self.assertIsNone(osa.origin(None, "zh"))


# ------------------------------------------------------------------ adapter pieces (stand-in client, no process)
class Evidence(unittest.TestCase):
    EV = HERE.parents[2] / "reports" / "qa" / "p426" / "opencode"     # the repository's evidence (not in the export)

    @unittest.skipUnless(EV.is_dir(), "evidence not in this tree")
    def test_evidence_names_no_home_directory(self):
        """Security review N7: the probe outputs say ~/…, never this machine's home directory."""
        import re
        files = sorted(self.EV.glob("*.json"))
        self.assertTrue(files)
        for f in files:
            self.assertIsNone(re.search(r"(?:/home|/Users|/root)/[^/\"]*", f.read_text()), f.name)
            self.assertTrue(json.loads(f.read_text())["bin"].startswith(("~/", "opencode")), f.name)
        for f in ("plan_probe.py", "live_subagent.py"):
            self.assertIn('"bin": tilde(BIN)', (self.EV / f).read_text(), f)


class Client:
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    async def request(self, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        for (m, prefix), ans in self.answers.items():
            if m == method and path.startswith(prefix):
                return ans
        return 200, {"data": {}} if path.startswith("/api/") else {}


def agent(cls, cfg=None, answers=None):
    a = object.__new__(cls)
    asks, notices = [], []

    async def ask(tool, inp, gone=None, **kw):
        asks.append((tool, inp, kw))
        return {"behavior": "deny", "message": "no"}

    async def question(card, gone=None, task=None):
        asks.append(("Q", card, task))
        return "answer", [[1]]
    a.cfg = {"dir": "/w", "high_risk_warnings": True, **(cfg or {})}
    a.host = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()), ask=ask, question=question, agent_notice=notices.append,
                             lang="zh", agent_text=notices.append)
    a.client, a.sid, a.root, a.research, a.persist = Client(answers), "ses_main", "/w", False, True
    a.pending, a.questions, a.q_sent, a.ours, a.tasks = {}, {}, {}, set(), set()
    a.turn_done, a.rules, a.proc = None, oc2.rules_to_v2(oc.session_rules([], subagents=["general"])) if cls.v2 else [], None
    a.cfg.setdefault("model", None)
    a.cur_send = SimpleNamespace(source=provenance.phone())
    a._note_turn()
    a.cur_send = None
    return a, asks, notices


class Adapter(unittest.TestCase):
    def test_v2_child_request_is_labelled_and_answered_on_its_own_route(self):
        async def go():
            a, asks, notices = agent(oc2.OpenCodeV2Agent)
            with mock.patch("agentj.approval_jev.low_risk_pass", return_value=False):
                a.on_event({"type": "session.created", "data": {"sessionID": "ses_kid", "parentID": "ses_main", "agent": "general",
                                                                "title": "写报告", "permissions": a.rules}})
                a.on_event({"type": "permission.asked", "data": {"id": "per_1", "sessionID": "ses_kid", "action": "shell",
                                                                 "resources": ["rm -rf x"]}})
                await asyncio.sleep(0.05)
            self.assertEqual(asks[0][0], "Bash")
            self.assertEqual(asks[0][2], {"origin": "来自子 Agent：general · 写报告"})
            self.assertIn(("POST", "/api/session/ses_kid/permission/per_1/reply", {"decision": "reject", "message": "no"}),
                          a.client.calls)
            # a request from a session that is not ours (no parent of ours): never a card
            a.on_event({"type": "permission.asked", "data": {"id": "per_2", "sessionID": "ses_other", "action": "shell",
                                                             "resources": ["rm x"]}})
            await asyncio.sleep(0.05)
            self.assertEqual(len(asks), 1)
            self.assertEqual(notices, [])
        asyncio.run(go())

    def test_v2_unknown_child_adopted_form_labelled_and_child_without_our_rules_stopped(self):
        async def go():
            a, asks, notices = agent(oc2.OpenCodeV2Agent, answers={
                ("GET", "/api/session/ses_late"): (200, {"data": {"id": "ses_late", "parentID": "ses_main", "agent": "explore"}})})
            a.on_event({"type": "form.created", "data": {"form": {"id": "frm_1", "sessionID": "ses_late", "fields": [
                {"key": "c", "type": "string", "title": "Which?", "options": [{"value": "r", "label": "Red"}]}]}}})
            await asyncio.sleep(0.05)
            self.assertIn("ses_late", a.children)
            self.assertEqual(asks[0][0], "Q")
            self.assertTrue(asks[0][1][0]["q"].endswith("\n来自子 Agent：explore"))
            self.assertIn(("POST", "/api/session/ses_late/form/frm_1/reply", {"answer": {"c": "r"}}), a.client.calls)
            # an OpenCode that does not hand our rules down to a child: the child is stopped, sub-agents off from now on
            a.on_event({"type": "session.created", "data": {"sessionID": "ses_bare", "parentID": "ses_main", "agent": "general",
                                                            "permissions": None}})
            await asyncio.sleep(0.05)
            self.assertIn(("POST", "/api/session/ses_bare/interrupt", None), a.client.calls)
            self.assertTrue(a.sub_broken)
            self.assertIn("已停下这个子 Agent", notices[-1])
            a._sub_plan([{"name": "general", "mode": "subagent", "permission": []}], "build", [])
            self.assertEqual(a.sub_ok, [])
        asyncio.run(go())

    def test_v1_child_question_labelled_and_background_done_line(self):
        async def go():
            a, asks, notices = agent(oc.OpenCodeAgent, {"high_risk_warnings": False})
            a.on_event({"type": "session.created", "properties": {"info": {"id": "ses_k", "parentID": "ses_main",
                                                                           "title": "查资料 (@general subagent)"}}})
            self.assertEqual(a.children["ses_k"], {"agent": "general", "title": "查资料", "src": provenance.phone()})
            a.on_event({"type": "question.asked", "properties": {"id": "que_1", "sessionID": "ses_k", "questions": [
                {"question": "哪个？", "header": "", "options": [{"label": "A"}, {"label": "B"}]}]}})
            await asyncio.sleep(0.05)
            self.assertEqual(asks[0][1][0]["q"], "哪个？\n来自子 Agent：general · 查资料")
            self.assertIn(("POST", "/question/que_1/reply", {"answers": [["A"]]}), a.client.calls)
            # a sub-agent finishing while a turn of ours runs: nothing; while none runs (background): one quiet line
            a.turn_done = asyncio.Event()
            a.on_event({"type": "session.idle", "properties": {"sessionID": "ses_k"}})
            self.assertEqual(notices, [])
            a.turn_done.set()
            a.on_event({"type": "session.idle", "properties": {"sessionID": "ses_k"}})
            a.on_event({"type": "session.idle", "properties": {"sessionID": "ses_k"}})
            self.assertEqual(len(notices), 1)
            self.assertTrue(notices[0].startswith("子 Agent 完成：general · 查资料"))
        asyncio.run(go())

    def test_usage_counts_children_and_state(self):
        async def go():
            a, _, _ = agent(oc2.OpenCodeV2Agent, answers={("GET", "/api/session?parentID="): (200, {"data": [
                {"id": "k1", "cost": 0.02, "tokens": {"input": 400, "output": 9}},
                {"id": "k2", "cost": 0.03, "tokens": {"input": 100, "output": 1}}]})})
            line = await a._children_line(0.10)
            self.assertIn("子 Agent（2 个）：$0.0500", line)
            self.assertIn("输入 500", line)
            self.assertIn("合计（含子 Agent）：$0.1500", line)
            a.sub_ok = ["general"]
            self.assertEqual(a.subagent_state(), {"mode": "on", "names": ["general"]})
            a.cfg["high_risk_warnings"] = False
            self.assertEqual(a.subagent_state(), {"mode": "native"})
            a.research = True
            self.assertEqual(a.subagent_state()["why"], "research")
        asyncio.run(go())


FRIEND = {"kind": "friend_task", "role": "task"}
GROUP = {"kind": "telegram", "role": "group", "sender": 7, "chat": -100, "profile": "proxy", "owner_id": False}


class Source(unittest.TestCase):
    """Security review S3: a sub-agent started in a non-owner turn keeps that turn's source after the turn ends (cur_send
    None is not the owner), and so does the main session OpenCode wakes by itself when it finishes."""

    def spy(self, cls):
        a, asks, notices = agent(cls)
        seen = []

        async def ask(tool, inp, gone=None, **kw):       # what Host.ask would judge by, at the moment it is asked
            seen.append((tool, inp.get("command"), provenance.untrusted_turn(a), provenance.delegated_turn(a)))
            return {"behavior": "deny", "message": "no"}
        a.host.ask = ask
        return a, asks, seen

    def turn(self, a, src):
        a.cur_send = SimpleNamespace(source=src)
        a._note_turn()
        a.turn_done = asyncio.Event()

    def end(self, a):
        a.cur_send = None
        a.turn_done.set()

    def created(self, a, cid, parent="ses_main"):
        if a.v2:
            a.on_event({"type": "session.created", "data": {"sessionID": cid, "parentID": parent, "agent": "general",
                                                            "title": "t", "permissions": a.rules}})
        else:
            a.on_event({"type": "session.created", "properties": {"info": {"id": cid, "parentID": parent,
                                                                           "title": "t (@general subagent)"}}})

    def perm(self, a, rid, sid, cmd):
        if a.v2:
            a.on_event({"type": "permission.asked", "data": {"id": rid, "sessionID": sid, "action": "shell",
                                                             "resources": [cmd]}})
        else:
            a.on_event({"type": "permission.asked", "properties": {"id": rid, "sessionID": sid, "permission": "bash",
                                                                   "patterns": [cmd], "metadata": {"command": cmd}}})

    def done(self, a, cid):
        a.on_event({"type": "session.execution.succeeded", "data": {"sessionID": cid}} if a.v2
                   else {"type": "session.idle", "properties": {"sessionID": cid}})

    def check_friend_child_after_turn(self, cls):
        async def go():
            a, asks, seen = self.spy(cls)
            with mock.patch.object(a, "_sub_mode", return_value=False):
                self.turn(a, FRIEND)
                self.created(a, "ses_f")
                self.created(a, "ses_ff", parent="ses_f")              # nested: its parent's source
                self.end(a)                                            # the friend's turn is over: cur_send None
                self.turn(a, provenance.phone())                                     # the owner's next turn does not launder it
                self.perm(a, "per_1", "ses_f", "rm -rf x")
                self.perm(a, "per_2", "ses_ff", "ls")                  # low risk: still a card (no P198 pass)
                await asyncio.sleep(0.05)
                self.assertEqual(sorted(seen), [("Bash", "ls", True, True), ("Bash", "rm -rf x", True, True)])
                self.end(a)
                self.perm(a, "per_3", "ses_f", "rm -rf y")             # and with no turn of ours at all
                await asyncio.sleep(0.05)
                self.assertEqual(seen[-1], ("Bash", "rm -rf y", True, True))
                self.assertFalse(provenance.untrusted_turn(a))        # outside the request: nothing leaks
                # a group member's sub-agent: refused (untrusted, not delegated) — its question never reaches the phone
                self.turn(a, GROUP)
                self.created(a, "ses_g")
                self.end(a)
                self.perm(a, "per_4", "ses_g", "rm z")
                await asyncio.sleep(0.05)
                self.assertEqual(seen[-1], ("Bash", "rm z", True, False))
                n = len(asks)
                if a.v2:
                    a.on_event({"type": "form.created", "data": {"form": {"id": "frm_g", "sessionID": "ses_g", "fields": [
                        {"key": "c", "type": "string", "title": "Which?", "options": [{"value": "r", "label": "Red"}]}]}}})
                else:
                    a.on_event({"type": "question.asked", "properties": {"id": "que_g", "sessionID": "ses_g", "questions": [
                        {"question": "哪个？", "header": "", "options": [{"label": "A"}]}]}})
                await asyncio.sleep(0.05)
                self.assertEqual(len(asks), n, "no question card for a group member's sub-agent")
                self.assertTrue(any(c[0] in ("POST", "DELETE") and ("/reject" in c[1] or c[0] == "DELETE")
                                    for c in a.client.calls))
            # a child started in the owner's turn stays the owner's (P198 / goal unchanged)
            a2, _, seen2 = self.spy(cls)
            with mock.patch.object(a2, "_sub_mode", return_value=False):
                self.turn(a2, provenance.phone())
                self.created(a2, "ses_o")
                self.end(a2)
                with mock.patch("agentj.approval_jev.low_risk_pass", return_value=False):
                    self.perm(a2, "per_9", "ses_o", "rm -rf x")
                    await asyncio.sleep(0.05)
            self.assertEqual(seen2, [("Bash", "rm -rf x", False, False)], "explicit owner child keeps its source")
        asyncio.run(go())

    def test_v1_friend_child_keeps_its_source_after_the_turn(self):
        self.check_friend_child_after_turn(oc.OpenCodeAgent)

    def test_v2_friend_child_keeps_its_source_after_the_turn(self):
        self.check_friend_child_after_turn(oc2.OpenCodeV2Agent)

    def test_p198_low_risk_pass_refused_for_a_non_owner_request(self):
        from agentj.approval_jev import low_risk_pass
        host = SimpleNamespace(agent=SimpleNamespace(cur_send=SimpleNamespace(source=provenance.phone())), goals=None, st=SimpleNamespace(log=mock.Mock()),
                               _classify=lambda t, i: SimpleNamespace(danger=False, cats=[]))
        cfg = {"high_risk_warnings": True}
        self.assertTrue(low_risk_pass(host, cfg, "Bash", {"command": "ls"}))
        for src in (FRIEND, GROUP, {"kind": "same_owner", "role": "task"}):
            with provenance.request_source(src):
                self.assertFalse(low_risk_pass(host, cfg, "Bash", {"command": "ls"}), src)
        with provenance.request_source(None):                 # missing source is never owner
            self.assertFalse(low_risk_pass(host, cfg, "Bash", {"command": "ls"}))

    def check_wake_not_goal_approved(self, cls):
        """The main session OpenCode wakes after a friend's background sub-agent finished: a real Host.ask under a live
        goal whose tier says "low" — a card (delegated), never goal-approved; the owner's own wake still is."""
        from agentj import approval_jev, approvals, goal
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = _state(tmp.name)
        (pathlib.Path(tmp.name) / "w").mkdir()
        st.set_agent_config("opencode", str(pathlib.Path(tmp.name) / "w"))
        sent = []
        host = _host(st, sent)
        host.ask_ttl = 1
        _ready(host, Phone(st))
        gd = {"grant": {"rid": "g1"}, "grants": [], "preauth": False}

        def run(src):
            a, _, _ = agent(cls)
            a.host, a.kind = host, cls.kind
            host.agent = a

            async def go():
                with mock.patch.object(a, "_sub_mode", return_value=False):
                    self.turn(a, src)
                    self.created(a, "ses_bg")
                    self.end(a)                                       # the turn ends; the sub-agent runs on (background)
                    self.done(a, "ses_bg")                            # … finishes; OpenCode wakes the main session itself
                    self.perm(a, "per_w", "ses_main", "rm -rf build")
                    for _ in range(300):
                        await asyncio.sleep(0.01)
                        if "per_w" in a.ours:
                            break
            asyncio.run(go())
            return [r for r in approvals.read_log(st) if r.get("tool") == "Bash"][-1]

        with mock.patch.object(approval_jev, "available", return_value=False), \
                mock.patch.object(host.goals, "current", return_value=gd), mock.patch.object(host.goals, "count"), \
                mock.patch.object(goal, "tier", return_value=("low", None)):
            rec = run(FRIEND)
            self.assertNotEqual(rec.get("reason"), "goal", rec)
            self.assertTrue([o for _, o in sent if o["t"] == "ask" and o["tool"] == "Bash"], "a card reached the phone")
            rec = run(GROUP)
            self.assertEqual(rec.get("reason"), "group_source", rec)
            rec = run(None)
            self.assertEqual(rec.get("reason"), "group_source")
            rec = run(provenance.phone())
            self.assertEqual((rec.get("decision"), rec.get("reason")), ("allow", "goal"), "explicit owner wake still passes")

    def test_v1_wake_turn_not_goal_approved(self):
        self.check_wake_not_goal_approved(oc.OpenCodeAgent)

    def test_v2_wake_turn_not_goal_approved(self):
        self.check_wake_not_goal_approved(oc2.OpenCodeV2Agent)


# ------------------------------------------------------------------ chains (stand-in serve inside the fence)
class ChainBase:
    FAKE, LOGNAME = "", ""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        self.log = self.work / self.LOGNAME
        wrapper = root / "opencode"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / self.FAKE} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = {"AGENTJ_OPENCODE_BIN": str(wrapper), "FAKE_OC_LOG": str(self.log), "AGENTJ_TEST_OC_WATCH": "1",
                    "FAKE_OC_SUBAGENTS": "1", "FAKE_OC2_SUBAGENTS": "1", "FAKE_OC2_CONNECTED": "opencode",
                    "XDG_DATA_HOME": str(root / "data"), "XDG_CONFIG_HOME": str(root / "cfg")}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.st.set_agent_config("opencode", str(self.work), model="opencode/big-pickle",
                                 fence=fence.problem(self.st, str(self.work)) is None)
        oc._SUB_CACHE.clear()

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def high_risk(self, on=True):
        from agentj import preferences
        preferences.path().parent.mkdir(parents=True, exist_ok=True)
        preferences.path().write_text(preferences.edit(preferences.read()[0], "agent.high_risk_warnings", on))

    def run_chain(self, script, ttl=6):
        ph = Phone(self.st)
        sent = []
        from test_l1 import _host as startup_host
        host = startup_host(self.st, sent)
        host.ask_ttl = ttl
        oc.WATCH = 1.0

        def msgs():
            return [o["text"] for _, o in sent if o["t"] == "msg" and o.get("from") in ("agent", "notice")]

        async def wait(pred, ms=20000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; sent={sent[-6:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.02)

        async def go():
            run = asyncio.create_task(host.run())
            await wait(lambda: host.agent is not None)
            s = _ready(host, ph)
            n = [0]

            async def say(text):
                n[0] += 1
                await host._app(s, {"t": "msg", "id": f"{n[0]:016x}", "text": text, "ts": 0})
            c = SimpleNamespace(host=host, sent=sent, msgs=msgs, wait=wait, say=say, s=s, ph=ph,
                                asks=lambda: [o for _, o in sent if o["t"] == "ask"],
                                idle=lambda: wait(lambda: host.agent.status == "idle"))
            try:
                await script(c)
            finally:
                host.stopping.set()
                await run
        asyncio.run(go())


class V1Chain(ChainBase, unittest.TestCase):
    FAKE, LOGNAME = "fakeopencode.py", ".oc.jsonl"

    def test_high_risk_opens_verified_subagents_cards_name_them(self):
        self.high_risk()
        (self.work / "victim.txt").write_text("keep me")

        async def script(c):
            await c.say("TASK: general :: RUN: rm victim.txt")
            await c.wait(lambda: len(c.asks()) == 1)
            ask = c.asks()[-1]
            self.assertEqual(ask["tool"], "Bash")
            self.assertTrue(ask["summary"].endswith("\n来自子 Agent：general · do it"), ask["summary"])
            await c.host._app(c.s, c.ph.answer(ask, False))
            await c.wait(lambda: any(m.startswith("TASK DONE: 被拒绝") for m in c.msgs()))
            self.assertTrue((self.work / "victim.txt").exists())
            await c.idle()
            await c.say("TASK: plan :: RUN: ls")              # a primary agent as a sub-agent: refused by the rules
            await c.wait(lambda: "task denied: plan" in c.msgs())
            await c.idle()
            st = c.host.agent.subagent_state()
            self.assertEqual((st["mode"], sorted(st["names"])), ("on", ["explore", "general"]))
            self.assertIn("子 Agent（1 个）", (await c.host.agent.cmd_cost("")).text)
        self.run_chain(script)
        starts = [json.loads(x) for x in self.st.log_path.read_text().splitlines() if '"agent_start"' in x]
        self.assertEqual(len(starts), 2, "one restart: the first start learns the agents, the second carries their rules")
        made = [json.loads(x) for x in self.log.read_text().splitlines()]
        sess = [r for r in made if r.get("method") in ("POST", "PATCH") and r.get("path", "").startswith("/session")
                and isinstance((r.get("body") or {}).get("permission"), list)][-1]["body"]["permission"]
        self.assertIn(R("task", "general", "allow"), sess)
        self.assertNotIn(R("task", "plan", "allow"), sess)

    def test_default_mode_injects_nothing(self):
        async def script(c):
            await c.say("你好")
            await c.wait(lambda: "ECHO: 你好" in c.msgs())
            self.assertEqual(c.host.agent.subagent_state(), {"mode": "native"})
        self.run_chain(script)
        starts = [x for x in self.st.log_path.read_text().splitlines() if '"agent_start"' in x]
        self.assertEqual(len(starts), 1)


class V2Chain(ChainBase, unittest.TestCase):
    FAKE, LOGNAME = "fakeopencode2.py", ".oc2.jsonl"

    def reqs(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_high_risk_child_inherits_card_names_it_and_background_wakes(self):
        self.high_risk()
        (self.work / "victim.txt").write_text("keep me")

        async def script(c):
            await c.say("TASK: general :: RUN: truncate -s 0 victim.txt")   # (the stand-in's build agent denies rm itself)
            await c.wait(lambda: len(c.asks()) == 1)
            self.main = c.host.agent.sid
            ask = c.asks()[-1]
            self.assertTrue(ask["summary"].endswith("\n来自子 Agent：general · do it"), ask["summary"])
            await c.host._app(c.s, c.ph.answer(ask, False))
            await c.wait(lambda: "TASK DONE: REJECTED" in c.msgs())
            self.assertEqual((self.work / "victim.txt").read_text(), "keep me")
            await c.idle()
            await c.say("TASK: general :: RUN: rm victim.txt")     # the primary agent's deny reaches the child (inherited)
            await c.wait(lambda: "TASK DONE: DENIED" in c.msgs())
            self.assertEqual(len(c.asks()), 1)
            await c.idle()
            await c.say("TASKBG: explore :: RUN: echo bg-ok")      # low risk: answered without a card (P198), on its route
            await c.wait(lambda: any(m.startswith("子 Agent 完成：explore") for m in c.msgs()))
            await c.wait(lambda: "BG DONE: OUT: bg-ok" in c.msgs())
            self.assertEqual(c.host.agent.subagent_state(), {"mode": "on", "names": ["general", "explore"]})
        self.run_chain(script)
        replies = [r["p"] for r in self.reqs() if r["p"].endswith("/reply") and "/permission/" in r["p"]]
        self.assertEqual(len(replies), 2)
        self.assertFalse([p for p in replies if p.startswith(f"/api/session/{self.main}/")], "answered on the child's route")
        created = [r for r in self.reqs() if r["p"] == "/api/session" and r["m"] == "POST"][-1]["b"]["permissions"]
        self.assertIn({"action": "subagent", "resource": "general", "effect": "allow"}, created)
        starts = [x for x in self.st.log_path.read_text().splitlines() if '"agent_start"' in x]
        self.assertEqual(len(starts), 1, "v2: nothing injected, no restart")

    def test_default_mode_routes_a_child_request(self):
        async def script(c):
            await c.say("TASK: general :: RUN: echo kid")     # default mode: no rules of ours; the fake child still asks
            await c.wait(lambda: len(c.asks()) == 1, 8000)
            await c.host._app(c.s, c.ph.answer(c.asks()[-1], True))
            await c.wait(lambda: "TASK DONE: OUT: kid" in c.msgs())
        os.environ.update(FAKE_OC2_NO_INHERIT="1", FAKE_OC2_GENERAL_RULES='[{"action":"*","resource":"*","effect":"ask"}]')
        try:
            self.run_chain(script)
        finally:
            os.environ.pop("FAKE_OC2_NO_INHERIT", None)
            os.environ.pop("FAKE_OC2_GENERAL_RULES", None)


if __name__ == "__main__":
    unittest.main()
