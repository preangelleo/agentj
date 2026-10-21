"""P426 #117: `agentj friends pick` — rule order, thresholds, unreachable devices, Same owner only, task size, defaults and a
stable --json shape."""
import _hermetic  # noqa: F401
import argparse
import asyncio
import time

from test_p71_peer_service import settle
from test_p405_same_owner import SameOwnerTests
from agentj import friends_pick as fp, peer_service

NOW = 1_800_000_000.0
DAY = 86400


def status(load=0.4, cores=4, mem_gib=16, in_use=False, week=None, elapsed_days=3.0):
    q = {}
    if week is not None:
        q["week"] = {"used_pct": week, "reset_at": NOW + (7 - elapsed_days) * DAY, "used_pct_per_hour": None, "behind_pct": None}
    return {"at": NOW, "load": load * cores, "cores": cores, "memory_available": mem_gib * 2 ** 30, "agents": 0,
            "background": 0, "in_use": in_use, "harness": "codex", "model": "gpt-x", "quota": q}


def machines(**over):
    base = {"M1": status(week=31, load=0.2), "WARD": status(load=0.1, mem_gib=32), "SERVER": status(load=0.05),
            "ME": status(load=0.9, in_use=True)}
    base.update(over)
    return [{"id": k, "name": {"M1": "Mac M1", "WARD": "Warden", "SERVER": "Server", "ME": "Studio"}[k],
             "self": k == "ME", "status": v} for k, v in base.items()]


# The owner's own example (the rules model must express it; nothing of it is built into the product):
# ① Mac M1 when its weekly use is below the 14 %/day even pace and its load is low ② Warden when resources are free and
# nobody uses it ③ this computer ④ the server never takes large jobs.
OWNER = {"order": ["M1", "WARD", "self", "SERVER"], "pace": 14.0,
         "machines": {"M1": {"week": 1.0, "load": 0.5}, "WARD": {"load": 0.3, "mem": 8, "in_use": False},
                      "SERVER": {"sizes": ["small", "medium"]}}}


def pick(ms, rules=OWNER, size=None):
    pace = rules.get("pace", fp.DEFAULT_PACE) if rules else fp.DEFAULT_PACE
    ordered = fp.rank(ms, rules, size, pace, NOW)
    best = next((m for m in ordered if m["recommended"]), None)
    return best, {m["id"]: m for m in ordered}, ordered


class Rules(__import__("unittest").TestCase):
    def test_owner_example_first_rule_wins_with_plain_reason(self):
        self.assertTrue(fp.valid(OWNER))
        best, by, _ = pick(machines())
        self.assertEqual(best["id"], "M1")
        self.assertEqual(fp.why(best, "zh"), "Mac M1：本周用量 31%，低于均速进度 42%，负载 0.2/核，可用内存 16 GB，没人在用 → 推荐")
        self.assertIn("below even pace 42%", fp.why(best, "en"))
        self.assertEqual(by["WARD"]["rank"], 2)

    def test_threshold_filters_move_down_the_order(self):
        best, by, _ = pick(machines(M1=status(week=50, load=0.2)))           # ahead of even pace
        self.assertEqual(best["id"], "WARD")
        self.assertEqual(by["M1"]["reasons"], ["week"])
        self.assertIn("高于均速进度", fp.why(by["M1"], "zh"))
        best, by, _ = pick(machines(M1=status(week=50), WARD=status(load=0.1, in_use=True)))
        self.assertEqual(best["id"], "ME")                                   # ③ this computer; its own turn is not "in use"
        self.assertEqual(by["WARD"]["reasons"], ["in_use"])
        best, by, _ = pick(machines(M1=status(week=30, load=0.8), WARD=status(load=0.1, mem_gib=4)))
        self.assertEqual((by["M1"]["reasons"], by["WARD"]["reasons"]), (["load"], ["mem"]))

    def test_unknown_numbers_never_pass_a_threshold(self):
        best, by, _ = pick(machines(M1=status(week=None, load=0.1)))
        self.assertEqual(by["M1"]["reasons"], ["week_unknown"])
        self.assertEqual(best["id"], "WARD")

    def test_unreachable_is_skipped(self):
        best, by, _ = pick(machines(M1=None))
        self.assertFalse(by["M1"]["reachable"])
        self.assertEqual(by["M1"]["reasons"], ["offline"])
        self.assertIsNone(by["M1"]["rank"])
        self.assertEqual(fp.why(by["M1"], "zh"), "Mac M1：连不上（没回状态） → 不选")
        self.assertEqual(best["id"], "WARD")

    def test_task_size_filter(self):
        rules = {**OWNER, "order": ["SERVER", "M1"]}
        best, by, _ = pick(machines(), rules, "large")
        self.assertEqual(by["SERVER"]["reasons"], ["size"])
        self.assertEqual(best["id"], "M1")
        best, _, _ = pick(machines(), rules, "small")
        self.assertEqual(best["id"], "SERVER")

    def test_default_without_rules_lowest_load_not_in_use_self_fallback(self):
        best, by, ordered = pick(machines(SERVER=status(load=0.05, in_use=True)), {})
        self.assertEqual(best["id"], "WARD")                                 # lowest load among the free ones
        self.assertEqual(by["SERVER"]["reasons"], ["in_use"])
        self.assertEqual(ordered[-1]["id"], "ME")                            # this computer last
        best, _, _ = pick(machines(M1=status(load=2.0), WARD=None, SERVER=status(in_use=True)), {})
        self.assertEqual(best["id"], "ME")                                   # fallback
        best, _, _ = pick(machines(M1=status(load=0.3, week=10), WARD=status(load=0.3, week=40)), {})
        self.assertEqual(best["id"], "SERVER")
        best, _, _ = pick(machines(SERVER=None, M1=status(load=0.3, week=10), WARD=status(load=0.3, week=40)), {})
        self.assertEqual(best["id"], "M1")                                   # same load → more weekly headroom

    def test_unlisted_devices_come_after_the_order(self):
        best, by, _ = pick(machines(M1=status(week=60), WARD=None, ME=status(load=0.1)), {**OWNER, "order": ["M1"]})
        self.assertEqual(best["id"], "SERVER")                               # default ranking, before this computer
        self.assertFalse(by["SERVER"]["listed"])

    def test_validation(self):
        for bad in ({"order": "M1"}, {"order": ["a", "a"]}, {"pace": 0}, {"machines": {"M1": {"week": -1}}},
                    {"machines": {"M1": {"sizes": ["huge"]}}}, {"machines": {"M1": {"in_use": 1}}}, {"other": 1},
                    {"machines": {"M1": {"load": float("nan")}}}):
            self.assertFalse(fp.valid(bad), bad)
        self.assertTrue(fp.valid({}))


class PickService(SameOwnerTests):
    async def pick(self, **req):
        job = asyncio.create_task(self.svc.cli({"t": "pick", **req}))
        deadline = time.monotonic() + 20
        while not job.done() and time.monotonic() < deadline:
            await self.pump()
            await asyncio.wait([job], timeout=0.2)
        return await asyncio.wait_for(job, 5)

    async def test_only_bilateral_same_owner_devices_take_part(self):
        stranger = self.friend(name="陌生人").id
        res = await self.pick()
        self.assertEqual([m["self"] for m in res["machines"]], [True])          # nobody confirmed yet: this computer only
        self.assertTrue(res["pick"]["self"])
        await self.choose(self.svc, self.b, self.did, self.dsk)                  # one side only = pending
        await self.pump()
        self.assertEqual(len((await self.pick())["machines"]), 1)
        await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm')
        await self.choose(self.svc, self.b, self.did, self.dsk, 'confirm')
        await self.pump()
        res = await self.pick(size="medium")
        ids = [m["id"] for m in res["machines"]]
        self.assertIn(self.b, ids)
        self.assertNotIn(stranger, ids)
        self.assertEqual(set(res), {"ok", "task_size", "rules", "pace", "pick", "machines", "summary", "next", "lang"})
        for m in res["machines"]:
            self.assertEqual(set(m), {"id", "name", "self", "reachable", "eligible", "recommended", "rank", "listed", "rule",
                                      "reasons", "facts", "why"})
            self.assertEqual(set(m["facts"]), {"week_used_pct", "week_expected_pct", "load_per_core", "memory_available_gib",
                                               "in_use", "agents", "background", "harness", "model"})
            self.assertEqual(set(m["why"]), {"zh", "en"})
        self.assertEqual(res["task_size"], "medium")
        self.assertEqual(res["rules"], "default")
        self.assertEqual(len(self.sends) + len(self.other_sends), 0)              # a recommendation sends nothing

    async def test_unreachable_device_is_marked_and_not_picked(self):
        await self.confirm()

        async def offline(fid):
            return {"ok": False, "why": "offline", "code": 504}
        self.svc.same_owner.status = offline
        res = await self.svc.cli({"t": "pick"})
        dev = next(m for m in res["machines"] if m["id"] == self.b)
        self.assertEqual((dev["reachable"], dev["eligible"], dev["reasons"]), (False, False, ["offline"]))
        self.assertTrue(res["pick"]["self"])
        self.assertIn("连不上", dev["why"]["zh"])

    async def test_bad_size_and_rules_editing(self):
        self.assertEqual((await self.svc.cli({"t": "pick", "size": "huge"}))["why"], "bad_size")
        cli = self.svc.cli
        self.assertEqual((await cli({"t": "pick-rules", "action": "order", "args": ["nobody"]}))["why"], "not_friend")
        self.assertEqual((await cli({"t": "pick-rules", "action": "set", "args": ["self", "load=abc"]}))["why"], "bad_value")
        self.assertEqual((await cli({"t": "pick-rules", "action": "set", "args": ["self", "color=red"]}))["why"], "bad_key")
        res = await cli({"t": "pick-rules", "action": "order", "args": [self.b, "本机"]})
        self.assertEqual([o["id"] for o in res["rules"]["order"]], [self.b, "self"])
        res = await cli({"t": "pick-rules", "action": "set", "args": [self.b, "week=1", "load=0.5", "mem=4GiB", "in_use=deny",
                                                                       "sizes=small,medium"]})
        self.assertEqual(res["rules"]["machines"][0], {"id": self.b, "name": self.svc.store.name_of(self.b), "week": 1.0,
                                                       "load": 0.5, "mem": 4.0, "in_use": False, "sizes": ["small", "medium"]})
        self.assertEqual((await cli({"t": "pick-rules", "action": "pace", "args": ["14"]}))["rules"]["pace"], 14.0)
        self.assertEqual(self.svc.store.settings()["pick_rules"]["pace"], 14.0)
        self.assertEqual((await cli({"t": "pick", "rules": True}))["rules"]["source"], "owner")
        self.assertEqual((await cli({"t": "pick-rules", "action": "reset"}))["rules"]["source"], "default")
        self.assertEqual(self.svc.store.set_setting("pick_rules", {"order": 3}), "bad_value")

    async def test_rules_without_serve_use_the_same_settings_file(self):
        res = peer_service.local_cmd(self.host.st, self.svc.store, {"t": "pick-rules", "action": "pace", "args": ["20"]})
        self.assertTrue(res["ok"])
        self.assertEqual(fp.rules_of(self.svc.store)["pace"], 20.0)

    def test_parser(self):
        ap = argparse.ArgumentParser(prog="agentj")
        peer_service.add_parser(ap.add_subparsers(dest="cmd"))
        a = ap.parse_args(["friends", "pick", "--task-size", "large", "--explain", "--json"])
        self.assertEqual(peer_service._req(a), {"t": "pick", "size": "large"})
        a = ap.parse_args(["friends", "pick-rules", "set", "Mac M1", "week=1"])
        self.assertEqual(peer_service._req(a), {"t": "pick-rules", "action": "set", "args": ["Mac M1", "week=1"]})


for _n in dir(SameOwnerTests):            # reuse the two-host set-up, not P405's own tests
    if _n.startswith("test") and _n not in PickService.__dict__:
        setattr(PickService, _n, None)
del SameOwnerTests
