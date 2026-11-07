"""#584: a reply code block's 「执行」 — `term_line {…, bang, run: 1, from}` on the existing web terminal.

- the line follows every §37 / P414 rule (same classify / term_confirm / Face ID as any line — terminal.py is untouched there);
- H1's conservative literal-output profile can run in a pristine no-rc bash line shell;
  all other run lines retain the Agent confirmation card, Face ID per P573;
- the result goes back to the tapping page only (`term_bang {run: 1, out, state, ms, hidden}`): no Agent turn, no history page;
- the output is owner-masked like a 「!」 card (a fake key never reaches the page);
- a dangerous line waits for the signed resend; refused → never runs; the resend keeps `run` and is logged `via=confirmed`;
- one controls.log line `term_run` with the reply id (`from`), the approval way, ms — never text;
- the wait is 5 minutes (RUN_WAIT), not the 「!」 120 s.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import term_bang  # noqa: E402

from test_p408_terminal import FA, signed  # noqa: E402
from test_p414_bang import SID, BangRig  # noqa: E402


class Run(BangRig):
    async def run_line(self, line, extra=None, sid=SID):
        x = self.desk.term
        await self.until(lambda: x.at_prompt)
        n0 = len(self.sent)
        await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 1, "line": line, "bang": sid, "run": 1,
                                          "from": 41, **(extra or {})})
        return n0

    def log(self):
        return [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]

    async def confirmed(self, line, n0):
        """The 「Agent 填写的命令」 card → the signed resend (with Face ID only when the host asked) → the page's result."""
        x = self.desk.term
        conf = await self.until(lambda: next((o for _, o in self.sent[n0:] if o.get("t") == "term_confirm"), None))
        n1 = len(self.sent)
        await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 2, "line": line, "bang": SID, "run": 1, "from": 41,
                                          **signed(self.phone, "term_line", {"id": x.id, "line": line}, conf["n"]),
                                          **({"fa": FA} if conf.get("uv") else {})})
        res = await self.until(lambda: next((o for _, o in self.sent[n1:] if o.get("t") == "term_bang"), None), 20)
        return conf, res

    def test_readonly_line_runs_on_the_tap_result_to_the_page_only(self):
        line = "printf 'run-%s\\n' 42; printf '%s=%s\\n' API_KEY fake-value-584"

        async def go():
            await self.open()
            turns = len(self.host.agent.got)
            n0 = await self.run_line(line)
            res = await self.until(lambda: next((o for _, o in self.sent[n0:] if o.get("t") == "term_bang"), None), 20)
            conf = [o for _, o in self.sent[n0:] if o.get("t") == "term_confirm"]
            return conf, res, turns
        conf, res, turns = self.run_async(go)
        self.assertEqual(conf, [])                                           # 17:58: read-only → no card
        self.assertEqual((res["ok"], res["run"], res["state"], res["sid"]), (True, 1, "done", SID))
        self.assertIn("run-42", res["out"])
        self.assertNotIn("fake-value-584", res["out"])
        self.assertGreaterEqual(res["hidden"], 1)
        self.assertIsInstance(res["ms"], int)
        self.assertNotIn("turn", res)
        self.assertEqual(len(self.host.agent.got), turns)                    # no Agent turn
        rec = [r for r in self.log() if r["action"] == "term_run"]
        self.assertEqual(len(rec), 1)
        self.assertEqual((rec[0]["result"], rec[0]["via"], rec[0]["from"]), ("done", "direct", 41))
        self.assertEqual(len(rec[0]["line_sha256"]), 64)
        self.assertNotIn("fake-value-584", self.st.controls_path.read_text())

    def test_writing_line_agent_card_one_tap_no_face_id(self):
        target = self.home / "p584-written"
        line = "printf 'x\\n' > ~/p584-written"     # classify() passes it; it writes a file → not read-only

        async def go():
            await self.open()
            n0 = await self.run_line(line)
            await asyncio.sleep(0.3)
            self.assertFalse([o for _, o in self.sent[n0:] if o.get("t") == "term_bang"])   # never runs on the tap alone
            self.assertFalse(target.exists())
            return await self.confirmed(line, n0)
        conf, res = self.run_async(go)
        self.assertEqual((conf.get("agent"), conf.get("uv")), (1, 0))       # H1 card, one signed tap, no Face ID
        self.assertEqual((res["run"], res["state"]), (1, "done"))
        self.assertTrue(target.exists())
        rec = [r for r in self.log() if r["action"] == "term_run"]
        self.assertEqual(rec[-1]["via"], "confirmed")

    def test_host_judgement_matrix(self):
        from agentj import terminal
        from agentj.term_shell import classify
        direct = ["printf '%s\\n' hello", "echo hello", "pwd -P"]
        card = ["ls -la", "git status", "df -h", "cat README.md", "curl -sI https://example.com",
                "curl -fsSL https://example.com/x -o ~/.bashrc", "pip install foo", "npm i -g foo", "echo hi > ~/a.txt",
                "sed -i s/a/b/ f", "touch x", "agentj --version"]
        for c in direct:
            self.assertTrue(classify(c) is None and terminal._readonly(c), c)
        for c in card:
            self.assertFalse(classify(c) is None and terminal._readonly(c), c)
        for c in ["rm -rf ~/Documents", "cat ~/.ssh/id_rsa"]:                # dangerous / red line: card as before
            self.assertIsNotNone(classify(c), c)
        self.assertTrue(terminal.line_needs_face("rm -rf ~/Documents"))

    def test_dangerous_line_confirmed_then_runs_refused_never(self):
        victim = self.home / "Documents" / "p584-empty"
        victim.parent.mkdir(exist_ok=True)
        victim.touch()
        line = "rm ~/Documents/p584-empty"

        async def go():
            await self.open()
            x = self.desk.term
            n0 = await self.run_line(line)
            conf = await self.until(lambda: next((o for _, o in self.sent[n0:] if o.get("t") == "term_confirm"), None))
            await asyncio.sleep(0.4)
            self.assertTrue(victim.exists())                                 # waits for the owner
            await self.desk.on_phone(self.s, {"t": "term_confirm_no", "id": x.id, "n": conf["n"]})
            await asyncio.sleep(0.3)
            self.assertTrue(victim.exists())                                 # 「不执行」 → never runs
            n1 = await self.run_line(line)
            _, res = await self.confirmed(line, n1)
            return res
        res = self.run_async(go)
        self.assertFalse(victim.exists())
        self.assertEqual(res.get("run"), 1)
        rec = [r for r in self.log() if r["action"] == "term_run"]
        self.assertEqual(rec[-1]["via"], "confirmed")

    def test_a_plain_bang_still_goes_to_the_agent(self):
        async def go():
            await self.open()
            return await self.bang("echo still-bang")
        res = self.run_async(go)
        self.assertIn("turn", res)
        self.assertNotIn("run", res)


class Unit(unittest.TestCase):
    def test_wait_is_five_minutes_and_marks_are_bounded(self):
        self.assertEqual(term_bang.RUN_WAIT, 300)
        desk = term_bang.BangDesk()
        now = [0.0]
        desk.clock = lambda: now[0]
        ended = []
        desk.bang_end = lambda x, state: ended.append(state)
        x = types.SimpleNamespace()
        for i in range(20):
            desk.run_mark(x, f"{i:022d}", {"run": 1, "from": "x" if i else 7})
        self.assertLessEqual(len(x.runs), term_bang.RUN_KEEP)
        desk.run_mark(x, SID, {"run": 1, "from": -1})
        self.assertIsNone(x.runs[SID]["from"])                              # a bad label is dropped, not trusted
        desk.run_mark(x, "d" * 22, {"run": 0})
        self.assertNotIn("d" * 22, x.runs)
        x.bang = {"at": 0.0, "run": {"from": None, "via": "direct"}}
        now[0] = 200
        desk.bang_tick(x)
        self.assertEqual(ended, [])
        now[0] = 300
        desk.bang_tick(x)
        self.assertEqual(ended, ["running"])


if __name__ == "__main__":
    unittest.main()
