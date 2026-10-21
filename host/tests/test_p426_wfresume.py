"""P426 #181-5: multi-step automations keep a checkpoint per node and continue from the failed node (wfrun.py).

Leo (10-20): 「工作流失败局部修复：自动化中途失败从失败节点续跑，不整条重来。」 Fake harness only (Scheduler._exec replaced)."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import capability, goal, taskspec, tasks, wfrun  # noqa: E402
from agentj.state import State  # noqa: E402
from test_controls import task_dir  # noqa: E402

STEPS = [{"id": "fetch", "prompt_file": "S1.md", "outputs": ["reports/data.csv"]},
         {"id": "draft", "prompt_file": "S2.md", "title": {"zh": "写草稿", "en": "Draft"}},
         {"id": "publish", "prompt_file": "S3.md"}]


class Host:
    def __init__(self, root):
        self.root = Path(root)
        self.st = State(self.root / "st")
        self.st.root.mkdir(exist_ok=True)
        self.agent_cfg = {"dir": str(self.root / "work"), "kind": "fake"}
        self.turn_lock = asyncio.Lock()
        self.agent = None
        self.task_label = self.brief_scope = None
        self.notices, self.asked = [], []
        self.answer = "allow"
        self.gate = None
        self.paused = False
        self.lang = "zh"
        self.capabilities = capability.Capabilities(self)
        self.scheduler = tasks.Scheduler(self)

    def stopped(self):
        return self.paused

    def activity(self, *args, **kwargs):
        pass

    def task_finished(self, res):
        self.notices.append(res)

    async def ask(self, tool, tool_input, **kw):
        self.asked.append((tool, tool_input, kw))
        if self.gate is not None:
            await self.gate
        return {"behavior": self.answer}


def make_wf(root, steps=STEPS, mode="normal"):
    d = task_dir(Path(root) / "work", "daily", "overall instructions\n", mode=mode, steps=steps)
    for s in steps:
        (d / s["prompt_file"]).write_text(f"do {s['id']}\n")
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p426wf-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wf = make_wf(self.root)
        self.h = Host(self.root)
        self.calls = []
        self.failing = {"draft"}

    def fake(self):
        async def execute(kind, e, research, prompt=None):
            sid = next(s["id"] for s in e["task"]["steps"] if f"（{s['prompt_file']}）" in (prompt or ""))
            self.calls.append(sid)
            if sid == "fetch":
                (Path(e["dir"]) / "reports").mkdir(exist_ok=True)
                (Path(e["dir"]) / "reports" / "data.csv").write_text("a,b\n")
            if sid in self.failing:
                return f"tried {sid}\nVERDICT: fail — 网站登录失效", 0
            return f"did {sid}\nVERDICT: ok — {sid} 完成", 0
        self.h.scheduler._exec = execute

    def run_once(self, trigger="manual"):
        self.fake()
        sch = self.h.scheduler
        if trigger != "resume":
            sch.request("daily", trigger)
        tid, trig = sch.waiting[-1]
        return asyncio.run(sch.run(tid, trig))

    def resume(self, ref, yes=False, trusted=False):
        return asyncio.run(wfrun.resume(self.h, ref, yes, trusted))


class Checkpoint(Base):
    def test_second_node_fails_record_and_notice(self):
        res = self.run_once()
        self.assertEqual(self.calls, ["fetch", "draft"])            # node 3 never started
        rec = wfrun.load(self.h.st, res["run_id"])
        self.assertEqual([n["state"] for n in rec["steps"]], ["done", "failed", "pending"])
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["failed"]["step"], "draft")
        self.assertIn("网站登录失效", rec["failed"]["why"])
        self.assertEqual({a["path"] for a in rec["steps"][0]["artifacts"]},
                         {"reports/data.csv", rec["steps"][0]["report"]})
        self.assertEqual(oct(os.stat(wfrun.path(self.h.st, res["run_id"])).st_mode & 0o777), "0o600")
        self.assertEqual(res["verdict"], "fail")
        self.assertIn("第 2 步「写草稿」没做成", res["line"])
        self.assertEqual(res["resume"]["run_id"], res["run_id"])
        hint = wfrun.hint(res, "zh")
        self.assertIn("从失败处继续", hint)
        self.assertIn("agentj automation resume " + res["run_id"][:12], hint)
        self.assertIn("continue daily from where it failed", wfrun.hint(res, "en"))
        self.assertIn("agentj automation resume", capability.readback_prompt("daily", res, "zh"))
        self.assertEqual(tasks.load(self.h.st)["runs"][res["run_id"]]["verdict"], "fail")

    def test_all_nodes_ok(self):
        self.failing = set()
        res = self.run_once()
        self.assertEqual(self.calls, ["fetch", "draft", "publish"])
        self.assertEqual(res["verdict"], "ok")
        self.assertNotIn("resume", res)
        self.assertEqual(wfrun.load(self.h.st, res["run_id"])["status"], "done")

    def test_idempotent_retry_within_the_run(self):
        steps = [dict(STEPS[0]), dict(STEPS[1], idempotent=True, retry=1), dict(STEPS[2])]
        make_wf(self.root, steps)
        seen = []

        async def execute(kind, e, research, prompt=None):
            sid = next(s["id"] for s in e["task"]["steps"] if f"（{s['prompt_file']}）" in prompt)
            seen.append(sid)
            ok = sid != "draft" or seen.count("draft") > 1
            return f"VERDICT: {'ok' if ok else 'fail'} — {sid}", 0
        self.h.scheduler._exec = execute
        self.h.scheduler.request("daily", "manual")
        res = asyncio.run(self.h.scheduler.run("daily", "manual"))
        self.assertEqual(seen, ["fetch", "draft", "draft", "publish"])
        self.assertEqual(res["verdict"], "ok")
        self.assertEqual(wfrun.load(self.h.st, res["run_id"])["steps"][1]["attempts"], 2)

    def test_spec(self):
        self.assertIsNone(taskspec.steps_problem(STEPS, "normal"))
        self.assertIn("idempotent", taskspec.steps_problem([dict(STEPS[0], retry=1), STEPS[1]], "normal"))
        self.assertIsNone(taskspec.steps_problem([dict(STEPS[0], retry=1), STEPS[1]], "research"))
        self.assertIn("2–12", taskspec.steps_problem(STEPS[:1]))
        self.assertIn("unique", taskspec.steps_problem([STEPS[0], STEPS[0]]))
        self.assertIn("nothing else", taskspec.steps_problem([dict(STEPS[0], cmd="rm"), STEPS[1]]))
        before = tasks.find(self.h.agent_cfg["dir"], "daily")["tsha"]
        (self.wf / "S2.md").write_text("changed\n")
        self.assertNotEqual(tasks.find(self.h.agent_cfg["dir"], "daily")["tsha"], before)   # node files join the contract
        (self.wf / "S3.md").unlink()
        self.assertIn("S3.md", tasks.find(self.h.agent_cfg["dir"], "daily")["problems"][0])


class Resume(Base):
    def test_resume_runs_only_the_failed_and_later_nodes(self):
        first = self.run_once()
        self.calls.clear()
        self.failing = set()
        out = self.resume("daily", yes=True, trusted=True)            # task id → its newest failed run
        self.assertEqual(out["result"], "queued", out)
        self.assertEqual((out["start"], out["reuse"], out["rerun"]), (2, ["fetch"], ["draft", "publish"]))
        res = self.run_once("resume")
        self.assertEqual(self.calls, ["draft", "publish"])           # node 1 is not executed again
        self.assertEqual(res["verdict"], "ok")
        rec = wfrun.load(self.h.st, out["run_id"])
        self.assertEqual([n["state"] for n in rec["steps"]], ["reused", "done", "done"])
        self.assertEqual(rec["from"], first["run_id"])
        self.assertEqual(wfrun.load(self.h.st, first["run_id"])["resumed_by"]["run"], out["run_id"])
        again = self.resume(first["run_id"], yes=True, trusted=True)
        self.assertEqual(again["why"], "claimed")                      # one resume per run

    def test_reused_output_reaches_the_next_node(self):
        self.run_once()
        prompts = []
        self.resume("daily", yes=True, trusted=True)

        async def execute(kind, e, research, prompt=None):
            prompts.append(prompt)
            return "VERDICT: ok — x", 0
        self.h.scheduler._exec = execute
        asyncio.run(self.h.scheduler.run(*self.h.scheduler.waiting[-1]))
        self.assertIn("fetch: fetch 完成", prompts[0])
        self.assertIn("这是分步自动化的第 2/3 步", prompts[0])

    def test_upstream_output_changed_reruns_from_upstream(self):
        self.run_once()
        (self.wf / "reports" / "data.csv").write_text("tampered\n")
        self.calls.clear()
        self.failing = set()
        out = self.resume("daily", yes=True, trusted=True)
        self.assertEqual(out["start"], 1)
        self.assertIn("reports/data.csv", out["why"][0])
        self.run_once("resume")
        self.assertEqual(self.calls, ["fetch", "draft", "publish"])

    def test_changed_instructions_rerun_that_node(self):
        self.run_once()
        (self.wf / "S1.md").write_text("new instructions\n")
        out = self.resume("daily", yes=True, trusted=True)
        self.assertEqual(out["start"], 1)
        self.assertIn("说明改过了", out["why"][0])

    def test_non_idempotent_needs_the_owner(self):
        first = self.run_once()
        out = self.resume("daily", trusted=True)                       # terminal without --yes
        self.assertEqual(out["result"], "needs_yes")
        self.assertEqual(out["confirm"], ["draft"])
        self.assertEqual(self.h.asked, [])
        self.h.answer = "deny"                                          # an Agent's request → the phone card
        out = asyncio.run(self.h.capabilities.handle({"t": "automation_resume", "run": first["run_id"][:8]}))
        self.assertEqual(out["result"], "declined")
        tool, inp, kw = self.h.asked[0]
        self.assertEqual((tool, kw.get("batch")), ("agentj automation resume", False))
        self.assertIn("「写草稿」", inp["summary"])
        self.assertIn(tool, goal.SOFT_EXEMPT)
        self.assertEqual(self.h.scheduler.waiting, [])
        self.assertNotIn("resumed_by", wfrun.load(self.h.st, first["run_id"]))   # claim given back
        self.h.answer = "allow"
        out = self.resume("daily", yes=True)                            # an Agent's --yes is not the owner's
        self.assertEqual(len(self.h.asked), 2)
        self.assertEqual(out["result"], "queued")

    def test_idempotent_failed_node_needs_no_confirmation(self):
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True), STEPS[2]])
        self.run_once()
        out = self.resume("daily")
        self.assertEqual(out["result"], "queued")
        self.assertEqual(self.h.asked, [])

    def test_damaged_checkpoint_is_refused_safely(self):
        first = self.run_once()
        p = wfrun.path(self.h.st, first["run_id"])
        for junk in (b"{not json", json.dumps({"v": 1, "run_id": first["run_id"], "id": "daily", "steps": [{"id": 3}]}).encode()):
            p.write_bytes(junk)
            out = self.resume(first["run_id"], yes=True, trusted=True)
            self.assertEqual(out["why"], "corrupt")
            self.assertIn("agentj tasks run daily", out["detail"])
            self.assertEqual(self.h.scheduler.waiting, [])
        self.assertEqual(wfrun.status(self.h.st, first["run_id"])["why"], "corrupt")
        self.assertEqual(self.resume("ffffffffffff")["why"], "unknown")

    def test_concurrent_resumes_are_exclusive(self):
        first = self.run_once()

        async def both():
            self.h.gate = asyncio.get_running_loop().create_future()
            a = asyncio.create_task(wfrun.resume(self.h, first["run_id"]))   # waits on its card
            await asyncio.sleep(0.05)
            b = await wfrun.resume(self.h, first["run_id"], True, True)
            self.h.gate.set_result(None)
            return await a, b
        a, b = asyncio.run(both())
        self.assertEqual(b["why"], "claimed")
        self.assertEqual(a["result"], "queued")
        self.assertEqual(len(self.h.scheduler.waiting), 1)

    def test_stale_claim_from_a_previous_serve_is_ignored(self):
        first = self.run_once()
        wfrun._claim(self.h.st, first["run_id"], "t0", "old-instance")
        self.assertEqual(self.resume(first["run_id"], yes=True, trusted=True)["result"], "queued")

    def test_restart_cut_marks_the_running_node(self):
        self.fake()
        sch = self.h.scheduler
        rid = sch.request("daily", "manual")
        e = tasks.find(self.h.agent_cfg["dir"], "daily")
        rec = wfrun.new_record(rid, e, None)
        rec["steps"][0].update(state="done", artifacts=[], prompt_sha=wfrun.prompt_sha(e, STEPS[0]))
        rec["steps"][1].update(state="running")
        wfrun.save(self.h.st, rec)
        tasks.update(self.h.st, lambda d: d["runs"][rid].update(status="running"))
        h2 = Host(self.root)
        h2.scheduler.recover()
        cut = wfrun.load(h2.st, rid)
        self.assertEqual((cut["status"], cut["failed"]["step"]), ("interrupted", "draft"))
        self.assertEqual(h2.notices[0]["resume"]["run_id"], rid)
        out = asyncio.run(wfrun.resume(h2, rid, True, True))
        self.assertEqual(out["why"], "uncertain")                      # the owner checks effects first (existing rule)
        tasks.set_enabled(h2.st, h2.agent_cfg["dir"], "daily", True, "terminal")
        self.assertEqual(asyncio.run(wfrun.resume(h2, rid, True, True))["start"], 2)

    def test_usage_limit_rerun_never_repeats_a_side_effect_unasked(self):
        first = self.run_once()
        self.calls.clear()
        self.failing = set()
        res = self.run_once("quota_resume")                              # P314's automatic rerun after the reset
        self.assertEqual(self.calls, [])
        self.assertIn("等你确认", res["line"])
        self.assertEqual(res["resume"]["run_id"], first["run_id"])
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True), STEPS[2]])   # S5: an edit after the failure
        res = self.run_once("quota_resume")
        self.assertEqual(self.calls, [])
        self.assertIn("等你确认", res["line"])

    def test_usage_limit_rerun_continues_an_idempotent_failure(self):
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True), STEPS[2]])
        first = self.run_once()
        self.calls.clear()
        self.failing = set()
        res = self.run_once("quota_resume")
        self.assertEqual(self.calls, ["draft", "publish"])
        self.assertEqual(wfrun.load(self.h.st, first["run_id"])["resumed_by"]["run"], res["run_id"])
        self.assertEqual(self.resume(first["run_id"])["why"], "claimed")

    def test_stop_everything_refuses(self):
        first = self.run_once()
        self.h.paused = True
        self.assertEqual(self.resume(first["run_id"], yes=True, trusted=True)["why"], "stopped")


class ContractSnapshot(Base):
    """Security review S5: the facts that decide "needs the owner" are snapshotted in the hidden checkpoint at run start; an
    Agent editing task.json after the failure (idempotent: true, mode research) can not skip the owner's card."""

    def agent_resume(self, ref):
        return asyncio.run(self.h.capabilities.handle({"t": "automation_resume", "run": ref}))

    def test_snapshot_is_written_at_run_start(self):
        first = self.run_once()
        rec = wfrun.load(self.h.st, first["run_id"])
        self.assertEqual(rec["contract"]["mode"], "normal")
        self.assertEqual(rec["contract"]["steps"]["draft"], {"idempotent": False, "retry": 0})
        self.assertEqual(rec["tsha"], tasks.find(self.h.agent_cfg["dir"], "daily")["tsha"])

    def test_agent_marks_the_failed_step_idempotent(self):
        first = self.run_once()
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True, retry=3), STEPS[2]])   # the Agent rewrites task.json
        self.h.answer = "deny"
        out = self.agent_resume(first["run_id"][:8])
        self.assertEqual(out["result"], "declined")                   # still the phone card, and the owner said no
        summary = self.h.asked[0][1]["summary"]
        self.assertIn("「写草稿」", summary)
        self.assertIn("自动化内容（task.json 或步骤说明）在失败后被改过", summary)
        self.assertEqual(self.h.scheduler.waiting, [])
        t = self.resume(first["run_id"], trusted=True)                   # the terminal says the same in plain words
        self.assertEqual((t["result"], t["confirm"], t["edited"]), ("needs_yes", ["draft"], True))
        self.assertIn("在失败后被改过", t["detail"])

    def test_agent_switches_to_research_mode(self):
        first = self.run_once()
        make_wf(self.root, mode="research")
        self.h.answer = "deny"
        self.assertEqual(self.agent_resume(first["run_id"][:8])["result"], "declined")
        self.assertEqual(self.resume(first["run_id"], trusted=True)["confirm"], ["draft"])

    def test_stricter_survives_the_original_hash(self):
        """Even with the contract hash put back to the run's own (no 'changed' signal), the snapshot alone decides."""
        first = self.run_once()
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True), STEPS[2]])
        p = wfrun.path(self.h.st, first["run_id"])
        rec = json.loads(p.read_bytes())
        rec["tsha"] = tasks.find(self.h.agent_cfg["dir"], "daily")["tsha"]
        p.write_bytes(json.dumps(rec).encode())
        out = self.resume(first["run_id"], trusted=True)
        self.assertEqual((out["result"], out["confirm"], out["edited"]), ("needs_yes", ["draft"], False))

    def test_changed_contract_alone_needs_the_owner(self):
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True), STEPS[2]])
        first = self.run_once()
        self.assertEqual(self.resume(first["run_id"], trusted=True)["result"], "queued")   # unchanged: no card (as before)
        self.h.scheduler.waiting.clear()
        self.h.scheduler.plans.clear()
        rec = wfrun.load(self.h.st, first["run_id"])
        rec.pop("resumed_by")
        wfrun.save(self.h.st, rec)
        (self.wf / "S3.md").write_text("now also email everyone\n")       # a later step's instructions changed
        self.h.answer = "deny"
        out = self.agent_resume(first["run_id"][:8])
        self.assertEqual(out["result"], "declined")
        self.assertIn("在失败后被改过", self.h.asked[0][1]["summary"])
        t = self.resume(first["run_id"], trusted=True)
        self.assertEqual((t["result"], t["confirm"], t["edited"]), ("needs_yes", [], True))
        self.assertEqual(self.resume(first["run_id"], yes=True, trusted=True)["result"], "queued")

    def test_old_checkpoint_without_snapshot_is_conservative(self):
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True), STEPS[2]])
        first = self.run_once()
        p = wfrun.path(self.h.st, first["run_id"])
        rec = json.loads(p.read_bytes())
        rec.pop("contract")
        p.write_bytes(json.dumps(rec).encode())
        out = self.resume(first["run_id"], trusted=True)
        self.assertEqual((out["result"], out["confirm"]), ("needs_yes", ["draft"]))

    def test_resumed_run_keeps_the_stricter_snapshot(self):
        """Owner OKs one resume after the Agent's edit; if that run fails again, the next resume still asks."""
        first = self.run_once()
        make_wf(self.root, [STEPS[0], dict(STEPS[1], idempotent=True, retry=2), STEPS[2]])
        out = self.resume(first["run_id"], yes=True, trusted=True)
        self.assertEqual(out["result"], "queued")
        self.calls.clear()
        second = self.run_once("resume")
        self.assertEqual(self.calls, ["draft"])                          # no in-run retry: the snapshot says retry 0
        rec = wfrun.load(self.h.st, second["run_id"])
        self.assertEqual(rec["contract"]["steps"]["draft"], {"idempotent": False, "retry": 0})
        self.h.answer = "deny"
        self.assertEqual(self.agent_resume(second["run_id"][:8])["result"], "declined")
        self.assertEqual(len(self.h.asked), 1)


if __name__ == "__main__":
    unittest.main()
