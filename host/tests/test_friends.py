"""P71 / PROTOCOL §17.5–§17.7 + Group: friends.FriendStore — storage, policy groups, the fixed-window ledger, history."""
import datetime as dt
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _hermetic  # noqa: E402,F401
from agentj import friends  # noqa: E402
from agentj.friends import BUILTIN_GROUPS, NEVER_TELL, FriendStore, validate_group  # noqa: E402
from agentj.state import State  # noqa: E402

TOKYO = ZoneInfo("Asia/Tokyo")
NY = ZoneInfo("America/New_York")
A = "AJ-7KQ2-M9XA-4TPE-W3HC"
B = "AJ-0000-0000-0000-0001"


def ts(y, mo, d, h=0, mi=0, s=0, tz=TOKYO):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=tz).timestamp()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ajfr-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.st = State(self.tmp / "state")
        self.st.root.mkdir(mode=0o700)
        self.now = ts(2026, 10, 7, 12, 0, 0)
        self.s = FriendStore(self.st, clock=lambda: self.now, tz=TOKYO)
        self.s.add_friend(A, "xA", "pkA", "mboxA", {"name": "小明", "owner": "王", "intro": "咖啡"})


class Builtins(unittest.TestCase):
    def test_adr_numbers(self):
        g = {x["id"]: x for x in BUILTIN_GROUPS}
        self.assertEqual(g["default"]["limits"], {"msg": {"min": 3, "hour": 20, "day": 50, "month": 500},
                                                  "tok": {"min": 20000, "hour": 60000, "day": 150000, "month": 1500000},
                                                  "min_interval_s": 10, "max_len": 2000})
        self.assertEqual(g["default"]["auto"], {"mode": "scoped", "allow": ["寒暄", "名片和公开资料里的信息"],
                                                "ask": ["报价、付款、合作条款", "约时间", "任何需要承诺的事"], "max_auto_rounds": 6})
        self.assertEqual(g["friend"]["limits"]["msg"], {"min": 10, "hour": 100, "day": 300, "month": 3000})
        self.assertEqual(g["friend"]["limits"]["tok"], {"min": 100000, "hour": 300000, "day": 600000, "month": 6000000})
        self.assertEqual((g["friend"]["limits"]["min_interval_s"], g["friend"]["limits"]["max_len"]), (3, 8000))
        self.assertIn("日常协作、技术问题", g["friend"]["auto"]["allow"])
        self.assertEqual((g["friend"]["auto"]["mode"], g["friend"]["auto"]["max_auto_rounds"]), ("scoped", 12))
        self.assertEqual(g["colleague"]["limits"]["msg"], {"min": 30, "hour": 500, "day": 2000, "month": None})
        self.assertEqual(g["colleague"]["limits"]["tok"], {"min": 500000, "hour": 2000000, "day": 4000000, "month": 40000000})
        self.assertEqual((g["colleague"]["limits"]["min_interval_s"], g["colleague"]["limits"]["max_len"]), (1, 20000))
        self.assertEqual((g["colleague"]["auto"]["mode"], g["colleague"]["auto"]["max_auto_rounds"]), ("all", 30))
        self.assertTrue(any("付款" in x for x in g["colleague"]["auto"]["ask"]))
        self.assertTrue(any("承诺" in x for x in g["colleague"]["auto"]["ask"]))
        for x in BUILTIN_GROUPS:
            self.assertIsNone(validate_group(x), x["id"])
        self.assertEqual(set(friends.BUILTIN_NAMES_EN), {"default", "friend", "colleague"})
        self.assertEqual(len(NEVER_TELL), 6)


class Store(Base):
    def test_files_private(self):
        self.s.hist_add(A, {"mid": "m1", "dir": "in", "text": "hi", "ts": 1})
        self.s.set_profile("我喜欢咖啡")
        self.s.record_in(A)
        self.s.set_setting("intro", "你好")
        self.s.add_pending_out("r1", B)
        d = self.st.root / "peer"
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        files = [p for p in d.rglob("*") if p.is_file()]
        self.assertGreaterEqual(len(files), 6)
        for p in files:
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
        self.assertEqual(stat.S_IMODE(os.stat(d / "hist").st_mode), 0o700)

    def test_settings_defaults_and_validation(self):
        s = self.s.settings()
        self.assertEqual(s, {"on": True, "discoverable": True, "owner": "", "intro": "", "global_tok_day": 300000,
                             "never_tell_extra": []})
        self.assertIsNone(self.s.set_setting("on", False))
        self.assertFalse(self.s.on)
        self.assertEqual(self.s.set_setting("intro", "x" * 141), "bad_intro")
        self.assertEqual(self.s.set_setting("never_tell_extra", ["ok", ""]), "bad_never_tell")
        self.assertIsNone(self.s.set_setting("never_tell_extra", [" 项目代号X "]))
        self.assertEqual(self.s.never_tell(), list(NEVER_TELL) + ["项目代号X"])
        self.assertEqual(self.s.set_setting("nope", 1), "unknown_setting")

    def test_add_find_remove(self):
        self.s.add_friend(B, "xB", "pkB", "mboxB", {"name": "小红"})
        self.assertEqual(self.s.find("aj-7kq2 m9xa-4tpe-w3hc"), A)
        self.assertEqual(self.s.find("7KQ2M9XA4TPEW3HC"), A)
        self.assertEqual(self.s.find("AJ-0000-OOOO-0000-000I"), B)    # O → 0, I → 1
        self.assertEqual(self.s.find("小红"), B)
        self.assertIsNone(self.s.find("小"))                            # ambiguous prefix
        self.assertEqual(self.s.matches("小"), sorted([A, B]))
        self.assertIsNone(self.s.find("nobody"))
        self.s.set_group(A, "friend")
        self.s.hist_add(A, {"mid": "m", "dir": "in", "text": "x", "ts": 5})
        self.s.add_friend(A, "xA2", "pkA2", "mboxA", {"name": "小明2"})  # refresh keeps group / history
        self.assertEqual((self.s.get(A)["x"], self.s.get(A)["group"]), ("xA2", "friend"))
        self.assertEqual(len(self.s.hist_page(A)[0]), 1)
        self.s.set_session_id(A, "codex", "thread-1")
        self.assertTrue(self.s.remove(A))
        self.assertIsNone(self.s.get(A))
        self.assertEqual(self.s.hist_page(A), ([], False))
        self.assertIsNone(self.s.session_id(A, "codex"))
        self.assertFalse(self.s.remove(A))

    def test_block_keeps_mbox(self):
        self.assertTrue(self.s.block(A))
        self.assertTrue(self.s.is_blocked(A))
        self.assertTrue(self.s.is_blocked_mbox("mboxA"))
        self.assertTrue(self.s.get(A)["blocked"])
        # a requester who is not a friend: its pending request goes, its mbox is remembered
        self.assertTrue(self.s.add_pending_in("r1", {"id": B, "mbox": "mboxB", "card": {}, "note": "hi", "ts": 1}))
        self.assertTrue(self.s.block(B))
        self.assertEqual(self.s.pending_in(), {})
        self.assertTrue(self.s.is_blocked_mbox("mboxB"))
        self.assertFalse(self.s.add_pending_in("r2", {"id": B, "mbox": "mboxB"}))   # silently dropped
        self.assertFalse(self.s.add_pending_in("r3", {"id": "AJ-NEW", "mbox": "mboxB"}))  # same mailbox, other ID
        self.assertFalse(self.s.block("AJ-UNKNOWN"))
        self.assertTrue(self.s.unblock(B))
        self.assertFalse(self.s.is_blocked_mbox("mboxB"))
        self.assertTrue(self.s.unblock(A))
        self.assertFalse(self.s.get(A)["blocked"])
        self.s.block(B, mbox="mboxB")
        self.s.add_friend(B, "x", "pk", "mboxB", {})                     # re-adding a blocked ID keeps it blocked
        self.assertTrue(self.s.get(B)["blocked"])

    def test_pending(self):
        self.assertFalse(self.s.add_pending_in("r0", {"id": A, "mbox": "mboxA"}))   # already a friend
        self.assertTrue(self.s.add_pending_in("r1", {"id": B, "mbox": "mboxB", "card": {"name": "红"}, "note": "n", "ts": 3}))
        self.assertFalse(self.s.add_pending_in("r2", {"id": B, "mbox": "mboxB"}))   # one per ID
        self.s.set_pending_ask("r1", "ask-9")
        self.assertEqual(self.s.pending_in()["r1"]["ask_id"], "ask-9")
        self.s.add_pending_out("o1", "AJ-OUT", "hello")
        self.s.set_pending_out_state("o1", "sent")
        v = self.s.list_view()
        self.assertEqual(sorted((p["dir"], p["id"], p["state"]) for p in v["pending"]),
                         [("in", B, "pending"), ("out", "AJ-OUT", "sent")])
        self.now += 7 * 86400 + 1                                        # 7 days
        self.assertEqual(self.s.pending_in(), {})
        self.assertEqual(self.s.pending_out(), {})
        self.now -= 7 * 86400 + 1
        self.assertEqual(self.s.take_pending_in("r1")["id"], B)          # accepted / refused: taken out
        self.assertEqual(self.s.pending_in(), {})
        for i in range(friends.MAX_PENDING_IN + 5):
            self.s.add_pending_in(f"q{i}", {"id": f"AJ-Q{i}", "mbox": f"m{i}"}, now=self.now + i)
        self.assertEqual(len(self.s.pending_in(now=self.now + 100)), friends.MAX_PENDING_IN)

    def test_auto_round_and_unread(self):
        self.assertEqual([self.s.auto_round(A) for _ in range(3)], [1, 2, 3])
        self.assertEqual(self.s.auto_round(A, reset=True), 0)
        for _ in range(12):
            self.s.auto_round(A)
        self.assertEqual(self.s.list_view()["friends"][0]["state"], "paused")   # new friends group: 12 rounds
        self.s.hist_add(A, {"mid": "1", "dir": "in", "text": "a", "ts": 1})
        self.s.hist_add(A, {"mid": "2", "dir": "out", "text": "b", "ts": 2})
        self.assertEqual(self.s.get(A)["unread"], 1)
        self.s.mark_read(A)
        self.assertEqual(self.s.get(A)["unread"], 0)

    def test_list_view_shape(self):
        v = self.s.list_view()
        self.assertEqual(set(v), {"friends", "pending", "groups", "global"})
        self.assertEqual(set(v["friends"][0]), {"id", "name", "owner", "intro", "group", "state", "blocked", "last", "unread"})
        self.assertEqual(v["friends"][0]["name"], "小明")
        self.assertEqual([g["id"] for g in v["groups"]], ["default", "friend", "colleague"])
        self.assertEqual(v["global"], {"used": 0, "limit": 300000})

    def test_concurrent_writers(self):
        def work(i):
            for j in range(20):
                self.s.record_in(A, now=self.now)
        ts_ = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        for t in ts_:
            t.start()
        for t in ts_:
            t.join()
        self.assertEqual(self.s.usage(A)["used"]["msg"]["month"], 80)


class Groups(Base):
    def custom(self, gid="vip", **kw):
        g = json.loads(json.dumps(BUILTIN_GROUPS[0]))
        g.update(id=gid, name="贵宾", builtin=False, **kw)
        return g

    def test_validation(self):
        good = self.custom()
        self.assertIsNone(validate_group(good))
        bad = [({"id": "Bad_ID"}, "bad_id"), ({"name": ""}, "bad_name"), ({"name": "x" * 33}, "bad_name"),
               ({"extra": 1}, "bad_keys")]
        for patch, err in bad:
            self.assertEqual(validate_group({**good, **patch}), err, patch)
        g = self.custom()
        g["limits"]["msg"]["min"] = -1
        self.assertEqual(validate_group(g), "bad_limits")
        g = self.custom()
        g["limits"]["msg"]["min"] = None                                   # null = no limit
        self.assertIsNone(validate_group(g))
        g = self.custom()
        g["auto"]["mode"] = "sometimes"
        self.assertEqual(validate_group(g), "bad_mode")
        g = self.custom()
        g["auto"]["allow"] = ["x"] * 21
        self.assertEqual(validate_group(g), "bad_topics")
        g = self.custom()
        g["auto"]["allow"] = ["x" * 81]
        self.assertEqual(validate_group(g), "bad_topics")
        for r, err in ((0, "bad_rounds"), (101, "bad_rounds"), (True, "bad_rounds")):
            g = self.custom()
            g["auto"]["max_auto_rounds"] = r
            self.assertEqual(validate_group(g), err)
        g = self.custom()
        g["limits"]["max_len"] = 20001
        self.assertEqual(validate_group(g), "bad_max_len")
        self.assertEqual(validate_group({**good, "builtin": True}), "bad_builtin")

    def test_builtin_editable_not_deletable(self):
        g = self.s.group("default")
        g["limits"]["msg"]["min"] = 7
        g["builtin"] = False                                               # forced back
        self.assertIsNone(self.s.set_group_def(g))
        self.assertEqual(self.s.group("default")["limits"]["msg"]["min"], 7)
        self.assertTrue(self.s.group("default")["builtin"])
        self.assertEqual(self.s.del_group("default"), "builtin")
        self.assertEqual(self.s.del_group("colleague"), "builtin")
        self.assertEqual([g["id"] for g in self.s.groups()][:3], ["default", "friend", "colleague"])

    def test_custom_group_lifecycle(self):
        self.assertIsNone(self.s.set_group_def(self.custom()))
        self.assertEqual(self.s.set_group(A, "vip"), None)
        self.assertEqual(self.s.set_group(A, "nope"), "no_group")
        self.assertEqual(self.s.group_of(A)["id"], "vip")
        self.assertIsNone(self.s.del_group("vip"))
        self.assertEqual(self.s.get(A)["group"], "default")                # moved
        self.assertEqual(self.s.del_group("vip"), "no_group")
        for i in range(friends.MAX_GROUPS - 3):
            self.assertIsNone(self.s.set_group_def(self.custom(f"g{i}")))
        self.assertEqual(self.s.set_group_def(self.custom("one-more")), "too_many_groups")


class Ledger(Base):
    def setUp(self):
        super().setUp()
        self.s.set_group(A, "default")  # these boundary tests exercise the unchanged Default policy

    def test_length_and_interval(self):
        self.assertEqual(self.s.precheck(A, "x" * 2001), ("length", 0))
        self.assertIsNone(self.s.precheck(A, "x" * 2000))
        self.s.record_in(A, now=self.now)
        self.assertEqual(self.s.precheck(A, "hi", now=self.now + 4), ("interval", 6))
        self.assertIsNone(self.s.precheck(A, "hi", now=self.now + 10))

    def test_minute_window_and_boundary(self):
        t = ts(2026, 10, 7, 12, 0, 5)
        for i in range(3):
            self.assertIsNone(self.s.precheck(A, "hi", now=t + 10 * i))
            self.s.record_in(A, now=t + 10 * i)
        self.assertEqual(self.s.precheck(A, "hi", now=t + 40), ("msg_min", 15))   # 12:00:45 → next minute in 15 s
        self.assertIsNone(self.s.precheck(A, "hi", now=ts(2026, 10, 7, 12, 1, 0)))  # a new minute: fixed window resets

    def test_hour_day_month_boundaries(self):
        g = self.s.group("default")
        g["limits"]["min_interval_s"] = 0
        g["limits"]["msg"] = {"min": None, "hour": 2, "day": 3, "month": 4}
        self.s.set_group_def(g)
        self.s.record_in(A, now=ts(2026, 10, 31, 22, 10))
        self.s.record_in(A, now=ts(2026, 10, 31, 22, 20))
        self.assertEqual(self.s.precheck(A, "x", now=ts(2026, 10, 31, 22, 59, 30)), ("msg_hour", 30))
        self.s.record_in(A, now=ts(2026, 10, 31, 23, 0))
        self.assertEqual(self.s.precheck(A, "x", now=ts(2026, 10, 31, 23, 30)), ("msg_day", 1800))
        self.assertIsNone(self.s.precheck(A, "x", now=ts(2026, 11, 1, 0, 0)))  # new day AND new month (Tokyo)
        u = self.s.usage(A, now=ts(2026, 10, 31, 23, 30))
        self.assertEqual(u["used"]["msg"], {"min": 0, "hour": 1, "day": 3, "month": 3})
        self.s.record_in(A, now=ts(2026, 10, 31, 23, 40))
        g["limits"]["msg"] = {"min": None, "hour": None, "day": None, "month": 4}
        self.s.set_group_def(g)
        # month: 4 in October → the 5th waits for November (10 min away)
        self.assertEqual(self.s.precheck(A, "x", now=ts(2026, 10, 31, 23, 50)), ("msg_month", 600))
        self.s.record_in(A, now=ts(2026, 11, 1, 1, 0))
        self.assertIsNone(self.s.precheck(A, "x", now=ts(2026, 11, 1, 2, 0)))

    def test_time_zone_decides_the_day(self):
        utc = FriendStore(self.st, clock=lambda: self.now, tz=dt.timezone.utc)
        tok = FriendStore(self.st, clock=lambda: self.now, tz=TOKYO)
        g = tok.group("default")
        g["limits"]["min_interval_s"] = 0
        g["limits"]["msg"] = {"min": None, "hour": None, "day": 1, "month": None}
        tok.set_group_def(g)
        t = ts(2026, 10, 7, 23, 30)                         # Tokyo 23:30 = UTC 14:30
        tok.record_in(A, now=t)
        nxt = t + 3600                                      # Tokyo 00:30 next day, UTC 15:30 same day
        self.assertIsNone(tok.precheck(A, "x", now=nxt))
        self.assertEqual(utc.precheck(A, "x", now=nxt), ("msg_day", int(8.5 * 3600)))

    def test_dst_day_length(self):
        s = FriendStore(self.st, clock=lambda: self.now, tz=NY)
        t = dt.datetime(2026, 11, 1, 0, 30, tzinfo=NY).timestamp()   # the 25-hour day (DST ends at 02:00)
        key, end = s._window(t, "day")
        self.assertEqual(key, "2026-11-01")
        self.assertEqual(end - t, 24.5 * 3600)
        key, end = s._window(dt.datetime(2026, 12, 31, 23, 59, tzinfo=NY).timestamp(), "month")
        self.assertEqual((key, dt.datetime.fromtimestamp(end, NY).date()), ("2026-12", dt.date(2027, 1, 1)))

    def test_tokens_windows_and_global(self):
        self.s.record_in(A, now=self.now)
        self.s.record_turn(A, 19999, now=self.now)
        self.assertIsNone(self.s.precheck(A, "x", now=self.now + 11))
        self.s.record_turn(A, 1, now=self.now + 11)
        self.assertEqual(self.s.precheck(A, "x", now=self.now + 12)[0], "tok_min")
        u = self.s.usage(A, now=self.now + 12)
        self.assertEqual(u["used"]["tok"], {"min": 20000, "hour": 20000, "day": 20000, "month": 20000})
        self.assertEqual(u["tok_source"], "harness")
        # account-wide daily total over all friends
        self.s.set_setting("global_tok_day", 30000)
        self.s.add_friend(B, "x", "pk", "mboxB", {"name": "b"}, group="colleague")
        self.s.record_turn(B, 10000, now=self.now + 20)
        self.assertEqual(self.s.global_usage(now=self.now + 20), {"used": 30000, "limit": 30000})
        scope, retry = self.s.precheck(B, "x", now=self.now + 20)
        self.assertEqual(scope, "global")
        self.assertEqual(retry, int(ts(2026, 10, 8) - (self.now + 20)))
        self.assertIsNone(self.s.precheck(B, "x", now=ts(2026, 10, 8, 0, 0, 1)))

    def test_tokens_none(self):
        self.s.record_in(A, now=self.now)
        self.s.record_turn(A, 25000, now=self.now)
        self.s.record_turn(A, None, now=self.now + 1)
        u = self.s.usage(A, now=self.now + 1)
        self.assertEqual(u["used"]["tok"], {"min": None, "hour": None, "day": None, "month": None})
        self.assertIsNone(u["tok_source"])
        self.assertIsNone(self.s.precheck(A, "x", now=self.now + 11))   # token limits off, message limits only
        self.s.record_turn(A, 5, now=self.now + 12)                     # usage reported again
        self.assertEqual(self.s.usage(A, now=self.now + 12)["tok_source"], "harness")
        self.assertEqual(self.s.precheck(A, "x", now=self.now + 22)[0], "tok_min")

    def test_limited_once_per_window(self):
        t = ts(2026, 10, 7, 12, 0, 1)
        self.assertTrue(self.s.should_send_limited(A, "msg_min", t))
        self.assertFalse(self.s.should_send_limited(A, "msg_min", t + 30))
        self.assertTrue(self.s.should_send_limited(A, "msg_hour", t + 30))   # another scope
        self.assertTrue(self.s.should_send_limited(A, "msg_min", t + 60))    # next minute
        self.assertTrue(self.s.should_send_limited(A, "global", t))
        self.assertFalse(self.s.should_send_limited(A, "global", t + 3600))
        self.assertTrue(self.s.should_send_limited(A, "global", ts(2026, 10, 8, 0, 0, 1)))
        self.assertTrue(self.s.should_send_limited(B, "msg_min", t))         # per friend

    def test_blocked_count_and_usage_shape(self):
        self.s.note_blocked(A)
        self.s.note_blocked(A)
        u = self.s.usage(A)
        self.assertEqual(set(u), {"friend", "group", "used", "limits", "tok_source", "blocked"})
        self.assertEqual((u["blocked"], u["group"], u["limits"]["max_len"]), (2, "default", 2000))


class History(Base):
    def test_pages_newest_first(self):
        for i in range(120):
            self.s.hist_add(A, {"mid": f"m{i}", "dir": "in" if i % 2 else "out", "text": f"t{i}", "ts": 1000 + i,
                                "s": "got", "auto": i % 3 == 0})
        items, more = self.s.hist_page(A)
        self.assertEqual((len(items), more, items[0]["mid"], items[-1]["mid"]), (50, True, "m119", "m70"))
        items, more = self.s.hist_page(A, before=items[-1]["ts"])
        self.assertEqual((len(items), more, items[0]["mid"]), (50, True, "m69"))
        items, more = self.s.hist_page(A, before=items[-1]["ts"])
        self.assertEqual((len(items), more, items[-1]["mid"]), (20, False, "m0"))
        self.assertEqual(set(items[0]), {"mid", "dir", "text", "ts", "s", "auto"})
        self.assertEqual(len(self.s.hist_page(A, n=500)[0]), 50)
        self.assertTrue(self.s.hist_set_status(A, "m0", "replied"))
        self.assertEqual(self.s.hist_page(A, before=1001)[0][0]["s"], "replied")
        self.assertFalse(self.s.hist_set_status(A, "nope", "x"))
        p = self.st.root / "peer" / "hist"
        self.assertEqual([stat.S_IMODE(os.stat(f).st_mode) for f in p.iterdir()], [0o600])

    def test_profile(self):
        self.assertEqual(self.s.profile_text(), "")
        self.assertIsNone(self.s.set_profile("我在东京做咖啡。"))
        self.assertEqual(self.s.profile_text(), "我在东京做咖啡。")
        self.assertEqual(self.s.set_profile("x" * 8001), "too_long")
        self.assertEqual(stat.S_IMODE(os.stat(self.st.root / "peer" / "profile.md").st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
