"""Relay parity (PROTOCOL §10, PROMPT-33) — chains: serve with a p33 phone and the stand-in harnesses inside the fence
(tests/fakeclaude.py, fakecodex.py, fakeopencode.py). Per harness: `say` → queued → delivered → the reply as a history page
(never a `msg` to a p33 phone, while an older phone still gets `msg`), attachments uploaded as blobs and named in the Agent's
message with their inbox path, quote / excerpt, withdraw (cancelled / already_delivered / not_found, attachments staged
again), a slash command typed as a say, /clear → archive + new epoch → 「撤销清空」 → restored, a restart keeps the pages,
meters (exact numbers only), the model / effort pill (Claude Code restarts with --effort, Codex turn/start effort, OpenCode no
effort), questions (AskUserQuestion / requestUserInput / question.asked) answered by a signed pick, cancelled, timed out,
stopped, refused when the signature is wrong — each a line in approvals.log without the text; things only the computer can
answer as `local` pages; voice (asr blob → asr_res, a long recording transcribed at send time) with a stand-in engine.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import agent_opencode as oc  # noqa: E402
from agentj import approvals, compose, fence, serve, uploads, wire  # noqa: E402

from test_l1 import Phone, _host, _state  # noqa: E402
from test_p33 import b64, png, wav  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
QS = [{"question": "Which color?", "header": "Color", "options": [{"label": "Red", "description": "r"},
                                                                    {"label": "Green", "description": "g"}],
       "multiSelect": False},
      {"question": "Which days?", "header": "Days", "options": [{"label": "Mon", "description": ""}, {"label": "Wed"},
                                                                  {"label": "Fri"}], "multiSelect": True}]


class FakeASR:
    """A stand-in engine with the asr.py API (the real one: sherpa-onnx / voxtype, tested by its own worker)."""
    def __init__(self, state="ready"):
        self.state, self.calls = state, []

    def ready_state(self, state_dir=None, engine_override=None):
        return self.state

    def transcribe(self, path, *, timeout_s, state_dir=None, engine_override=None):
        self.calls.append((path, timeout_s))
        size = os.path.getsize(path)
        return {"ok": True, "text": f"听到了 {size} 字节", "engine": "fake", "ms": 3}


def ready33(host, phone, cid=11):
    s = serve.Session(cid=cid, state="ready", device=phone.did, name="手机", pub=phone.pub, p33=True)
    host.sessions[cid] = s
    return s


class _Chain(unittest.TestCase):
    KIND = "?"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.log = self.work / ".fake.jsonl"
        fake = {"claude": "fakeclaude.py", "codex": "fakecodex.py", "opencode": "fakeopencode.py"}[self.KIND]
        wrapper = pathlib.Path(self.tmp.name) / self.KIND
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / fake} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = {"claude": {"AGENTJ_CLAUDE_BIN": str(wrapper), "FAKE_CLAUDE_LOG": str(self.log)},
                    "codex": {"AGENTJ_CODEX_BIN": str(wrapper), "FAKE_CX_LOG": str(self.log)},
                    "opencode": {"AGENTJ_OPENCODE_BIN": str(wrapper), "FAKE_OC_LOG": str(self.log),
                                 "AGENTJ_TEST_OC_WATCH": "1"}}[self.KIND]
        os.environ.update(self.env)
        fenced = fence.problem(self.st, str(self.work)) is None
        if sys.platform.startswith("linux") and shutil.which("bwrap"):
            self.assertTrue(fenced)
        self.st.set_agent_config(self.KIND, str(self.work), fence=fenced)
        self.q_ttl = serve.Q_TTL

    def tearDown(self):
        serve.Q_TTL = self.q_ttl
        for k in self.env:
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def logged(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_chain(self, script, ttl=6, asr=None, old_phone=False):
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        host.ask_ttl = ttl
        if asr is not None:
            host.asr = asr
        oc.WATCH = 1.0

        def of(t, cid=None):
            return [o for c, o in sent if o["t"] == t and (cid is None or c == cid)]

        def turns():
            d = {}
            for _, o in sent:
                if o["t"] == "hist_turn":
                    d[o["turn"]["id"]] = o["turn"]
            return d

        async def wait(pred, ms=20000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; sent={sent[-8:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.02)

        async def go():
            run = asyncio.create_task(host.run())
            await wait(lambda: host.agent is not None)
            s = ready33(host, ph)
            old = None
            if old_phone:
                ph2 = Phone(self.st, "旧手机")
                old = serve.Session(cid=12, state="ready", device=ph2.did, name="旧手机", pub=ph2.pub)
                host.sessions[12] = old

            async def say(text, **kw):
                sid = wire.b64u(os.urandom(16))
                await host._app(s, {"t": "say", "sid": sid, "text": text, "ts": 0, **kw})
                return sid

            def res(sid):
                r = [o for o in of("say_res") if o["sid"] == sid]
                return r[-1] if r else None

            async def said(text, **kw):
                """say and wait for its page to end; → (say_res, the page)."""
                sid = await say(text, **kw)
                await wait(lambda: res(sid) is not None)
                r = res(sid)
                if not r["ok"]:
                    return r, None
                await wait(lambda: turns().get(r["turn"], {}).get("end") not in (None, "open"))
                return r, turns()[r["turn"]]

            async def upload(data, mime="image/png", purpose="att", origin="file", secs=None, name="x.png"):
                bid = wire.b64u(os.urandom(16))
                m = {"t": "blob_open", "bid": bid, "purpose": purpose, "name": name, "mime": mime, "size": len(data),
                     "sha256": hashlib.sha256(data).hexdigest(), "origin": origin, **({"secs": secs} if secs else {})}
                await host._app(s, m)
                for o in range(0, len(data), uploads.CHUNK):
                    await host._app(s, {"t": "blob_chunk", "bid": bid, "o": o, "d": b64(data[o:o + uploads.CHUNK])})
                await host._app(s, {"t": "blob_end", "bid": bid})
                done = [o for o in of("blob_done") if o["bid"] == bid]
                self.assertTrue(done and done[-1]["ok"], (done, of("blob_err")[-2:]))
                return bid

            async def answer_q(q, picks=None, cancel=False, key=None):
                act = "cancel" if cancel else "answer"
                msg = approvals.question_message(ph.channel, ph.did, q["id"], act, q["qs"], None if cancel else picks)
                sig = wire.b64u((key or ph.sk).sign(msg))
                await host._app(s, {"t": "q_answer", "id": q["id"], **({"cancel": True} if cancel else {"pick": picks}),
                                    "sig": sig})

            async def idle():
                await wait(lambda: host.agent.status == "idle" and host.agent.q.empty())
            ctx = dict(host=host, sent=sent, of=of, turns=turns, wait=wait, say=say, said=said, res=res, upload=upload,
                       answer_q=answer_q, idle=idle, s=s, ph=ph, old=old)
            try:
                await script(ctx)
            finally:
                host.stopping.set()
                await run
        asyncio.run(go())

    # ------------------------------------------------------------ shared scenario parts
    async def basic(self, c, echo):
        r, t = await c["said"]("你好")
        self.assertEqual((r["ok"], r["state"]), (True, "queued"))
        await c["wait"](lambda: any(o["s"] == "delivered" and o["sid"] == r["sid"] for o in c["of"]("say_state")))
        self.assertEqual((t["src"]["k"], t["src"]["text"], t["end"]), ("phone", "你好", "done"))
        self.assertIn(echo("你好"), t["reply"]["text"])
        self.assertFalse(c["of"]("msg", 11), "a p33 phone gets pages, never §8 msg")
        return r, t

    async def attach_quote(self, c, t_prev, echo):
        data = png(70_000)                               # two chunks
        bid = await c["upload"](data, name="照片.png")
        r, t = await c["said"]("看这张", att=[bid], reply_to=t_prev["id"], excerpt="你好")
        self.assertTrue(r["ok"], r)
        rep = t["reply"]["text"]
        self.assertIn("附件 1 个", rep)
        self.assertIn(f"【回复 #{t_prev['id']}", rep)
        self.assertIn("> 你好\n> （摘录）", rep)
        files = list((self.work / ".agentj" / "inbox").rglob("*.png"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), data)
        self.assertIn(str(files[0]), rep, "the Agent is told the absolute path inside its own folder")
        self.assertEqual(t["src"]["att"], [{"name": "照片.png", "mime": "image/png", "bytes": len(data), "kind": "image"}])
        self.assertEqual((t["src"]["quote"]["id"], t["src"]["quote"]["ex"]), (t_prev["id"], True))
        r2 = await c["said"]("再发一次", att=[bid])
        self.assertEqual((r2[0]["ok"], r2[0]["why"], r2[0]["att"]), (False, "att_gone", [bid]), "delivered ids are spent")
        bad = await c["said"]("引用不存在", reply_to=999999)
        self.assertEqual(bad[0]["why"], "reply_unknown")
        for kw, why in (({"text": "x" * 20001}, "too_long"), ({"text": "a\x07"}, "shape"), ({"text": ""}, "shape"),
                        ({"text": "x", "excerpt": "y"}, "shape")):
            sid = wire.b64u(os.urandom(16))
            await c["host"]._app(c["s"], {"t": "say", "sid": sid, "ts": 0, **kw})
            self.assertEqual(c["res"](sid)["why"], why, kw)
        sid = await c["say"]("同一个 sid")
        await c["wait"](lambda: c["res"](sid) is not None)
        await c["host"]._app(c["s"], {"t": "say", "sid": sid, "text": "again", "ts": 0})
        self.assertEqual(c["res"](sid)["why"], "dup")
        await c["idle"]()

    async def withdraw(self, c, slow, echo):
        a = await c["say"](slow)
        await c["wait"](lambda: c["host"].agent.status == "working")
        bid = await c["upload"](png(500))
        b = await c["say"]("撤回我", att=[bid])
        await c["wait"](lambda: c["res"](b) is not None)
        await c["host"]._app(c["s"], {"t": "say_cancel", "sid": b})
        await c["host"]._app(c["s"], {"t": "say_cancel", "sid": wire.b64u(os.urandom(16))})
        rs = c["of"]("say_cancel_res")
        self.assertEqual([x["r"] for x in rs[-2:]], ["cancelled", "not_found"])
        await c["wait"](lambda: c["turns"]().get(c["res"](b)["turn"], {}).get("end") == "stopped")
        tb = c["turns"]()[c["res"](b)["turn"]]
        self.assertEqual((tb["end"], tb.get("card")), ("stopped", {"withdrawn": True}))
        await c["idle"]()
        await c["host"]._app(c["s"], {"t": "say_cancel", "sid": a})
        self.assertEqual(c["of"]("say_cancel_res")[-1]["r"], "already_delivered")
        self.assertFalse(any(echo("撤回我") in t["reply"]["text"] for t in c["turns"]().values()), "never reached the Agent")
        r, t = await c["said"]("附件还在", att=[bid])
        self.assertTrue(r["ok"], "the withdrawn say's attachment is staged again under the same id")

    async def slash_clear_undo(self, c):
        sid = await c["say"]("/context")
        await c["wait"](lambda: c["res"](sid) is not None)
        r = c["res"](sid)
        self.assertEqual((r["ok"], r["state"]), (True, "delivered"))
        await c["wait"](lambda: c["turns"]().get(r["turn"], {}).get("end") == "done")
        t = c["turns"]()[r["turn"]]
        self.assertEqual((t["src"]["k"], t["src"]["text"], t["card"]["cmd"]), ("cmd", "/context", "context"))
        h = c["host"].hist
        ep, ids = h.epoch, set(h.turns)
        await c["host"]._app(c["s"], {"t": "slash", "cmd": "clear", "confirm": True})
        await c["wait"](lambda: h.epoch == ep + 1)
        await c["wait"](lambda: any(m["epoch"] == ep + 1 for m in c["of"]("hist_meta")))
        await c["wait"](lambda: any(t["src"]["k"] == "cmd" and t.get("card", {}).get("undo") for t in h.turns.values()))
        self.assertFalse(set(h.turns) & ids, "the new epoch starts empty but for the clear card")
        await c["say"]("清空后")
        await c["idle"]()
        await c["host"]._app(c["s"], {"t": "slash", "cmd": "undo_clear"})
        await c["wait"](lambda: h.epoch == ep + 2)
        self.assertTrue(ids <= set(h.turns), "「撤销清空」 restores every page")

    async def question_flow(self, c, prompt, answered, cancelled):
        """A two-question card (single + multi); a bad signature and bad picks change nothing; the good answer wins."""
        serve.Q_TTL = 30
        sid = await c["say"](prompt)
        await c["wait"](lambda: c["of"]("question"))
        q = c["of"]("question")[-1]
        self.assertEqual(len(q["qs"]), len(json.loads(prompt[5:])))
        await c["wait"](lambda: any(o.get("kind") == "question" for o in c["of"]("status")))
        await c["answer_q"](q, [[2], [1, 3]][:len(q["qs"])], key=__import__("cryptography.hazmat.primitives.asymmetric.ed25519",
                                                                           fromlist=["x"]).Ed25519PrivateKey.generate())
        await c["host"]._app(c["s"], {"t": "q_answer", "id": q["id"], "pick": [[3]], "sig": "AA"})
        self.assertFalse(c["of"]("question_done"))
        await c["answer_q"](q, [[2], [1, 3]][:len(q["qs"])])
        await c["wait"](lambda: c["of"]("question_done"))
        self.assertEqual(c["of"]("question_done")[-1], {"t": "question_done", "id": q["id"], "result": "answered"})
        r = c["res"](sid)
        await c["wait"](lambda: c["turns"]().get(r["turn"], {}).get("end") == "done")
        self.assertIn(answered, c["turns"]()[r["turn"]]["reply"]["text"])
        # cancel
        sid = await c["say"](prompt)
        await c["wait"](lambda: len(c["of"]("question")) == 2)
        await c["answer_q"](c["of"]("question")[-1], cancel=True)
        r = c["res"](sid)
        await c["wait"](lambda: c["turns"]().get(r["turn"], {}).get("end") == "done")
        self.assertIn(cancelled, c["turns"]()[r["turn"]]["reply"]["text"])
        self.assertEqual(c["of"]("question_done")[-1]["result"], "cancelled")
        # timeout
        serve.Q_TTL = 1
        sid = await c["say"](prompt)
        await c["wait"](lambda: len(c["of"]("question")) == 3)
        await c["wait"](lambda: len(c["of"]("question_done")) == 3, 8000)
        self.assertEqual(c["of"]("question_done")[-1]["result"], "timeout")
        await c["idle"]()
        recs = [r for r in approvals.read_log(self.st) if r.get("kind") == "question"]
        self.assertEqual([r["decision"] for r in recs], ["answer", "cancel", "timeout"])
        self.assertEqual([approvals.check_record(self.st, r) for r in recs], ["ok", "ok", "unsigned"])
        line = self.st.approvals_path.read_text()
        for text in ("Which color", "Green", "Mon"):
            self.assertNotIn(text, line)

    async def meters_and_models(self, c, want_ctx, want_h5, want_week):
        await c["wait"](lambda: c["host"].meter_state.get("ctx") == want_ctx and c["host"].meter_state.get("h5") == want_h5
                        and c["host"].meter_state.get("week") == want_week)
        await c["wait"](lambda: c["of"]("meter") and c["of"]("meter")[-1].get("ctx") == want_ctx, 6000)
        m = c["of"]("meter")[-1]
        follow = m.get("shared_follow")
        if follow is not None:
            self.assertEqual(set(follow), {"agent", "id"})
            self.assertIn(follow["agent"], ("claude", "codex", "opencode"))
            self.assertRegex(follow["id"], r"^[a-f0-9]{32}$")
        self.assertIsNone(m.get("shared_status"))
        self.assertEqual(set(m) - {"t", "shared_follow"}, {"model", "model_name", "effort", "ctx", "h5", "week", "at", "shared_status", "shared_writer"})


# ------------------------------------------------------------------ Claude Code
class ClaudeChain(_Chain):
    KIND = "claude"
    ECHO = staticmethod(lambda t: f"ECHO: {t}")

    def test_say_history_attach_quote_withdraw_slash_clear_restart(self):
        async def script(c):
            r, t = await self.basic(c, self.ECHO)
            self.assertTrue(any(m.get("from") == "you" or m.get("from") == "agent" for m in c["of"]("msg", 12)),
                            "the older phone still gets §8 msg")
            await self.attach_quote(c, t, self.ECHO)
            await self.withdraw(c, "SLOW", self.ECHO)
            await self.slash_clear_undo(c)
        self.run_chain(script, old_phone=True)
        # a restart: the pages are still there (and a p33 phone that knows them gets nothing twice)
        sent = []
        host = _host(self.st, sent)
        self.assertGreater(host.hist.meta()["count"], 3)
        self.assertTrue(any("你好" == t["src"].get("text") for t in host.hist.turns.values()))
        ph = Phone(self.st, "第三台")

        async def go():
            s = ready33(host, ph, 21)
            s.hist = {"epoch": host.hist.epoch, "last": host.hist.meta()["last"]}
            await host.on_ready(s, None)
            s2 = ready33(host, ph, 22)
            await host.on_ready(s2, None)
        asyncio.run(go())
        a = [o for c, o in sent if c == 21 and o["t"] == "hist_turn"]
        b = [o for c, o in sent if c == 22 and o["t"] == "hist_turn"]
        self.assertEqual((len(a), len(b) > 3), (0, True))
        self.assertTrue(any(o["t"] == "hist_meta" for c, o in sent if c == 21))
        self.assertFalse([o for _, o in sent if o["t"] == "msg"])

    def test_meters_models_effort(self):
        async def script(c):
            await self.basic(c, self.ECHO)
            await self.meters_and_models(c, {"used": 20761, "max": 200000}, {"pct": 45.0, "reset": 1790946000},
                                         {"pct": 47.0, "reset": 1791140400})
            await c["wait"](lambda: c["host"].agent.models_cache)
            await c["wait"](lambda: c["of"]("models") and c["of"]("models")[-1]["models"])
            mm = c["of"]("models")[-1]
            self.assertEqual([m["id"] for m in mm["models"]], ["default", "haiku"])
            self.assertEqual(mm["models"][0]["efforts"], ["low", "medium", "high", "xhigh", "max"])
            for body, why in (({"model": "nope"}, "unknown_model"), ({"effort": "huge"}, "unknown_effort")):
                await c["host"]._app(c["s"], {"t": "model_set", "r": "r" + why[8:13], **body})
                await c["wait"](lambda: any(o["r"] == "r" + why[8:13] for o in c["of"]("model_res")))
                self.assertEqual([o for o in c["of"]("model_res") if o["r"] == "r" + why[8:13]][-1]["why"], why)
            await c["host"]._app(c["s"], {"t": "model_set", "r": "r1", "model": "haiku", "effort": "high"})
            await c["wait"](lambda: any(o["r"] == "r1" for o in c["of"]("model_res")))
            self.assertTrue([o for o in c["of"]("model_res") if o["r"] == "r1"][-1]["ok"])
            self.assertEqual((self.st.agent_config()["model"], self.st.agent_config()["effort"]), ("haiku", "high"))
            await c["wait"](lambda: c["of"]("meter")[-1]["effort"] == "high", 6000)
            await c["said"]("用新的")
            await c["host"]._app(c["s"], {"t": "model_set", "r": "r2", "default": True})
            await c["wait"](lambda: any(o["r"] == "r2" for o in c["of"]("model_res")))
            self.assertEqual((self.st.agent_config()["model"], self.st.agent_config()["effort"]), (None, None))
            await c["said"]("回到默认")
        self.run_chain(script)
        starts = [x["argv"] for x in self.logged() if "argv" in x]
        controls = [x["control"] for x in self.logged() if "control" in x]
        self.assertEqual(len(starts), 3, "effort = a restart of the idle process (no control request can set it)")
        self.assertNotIn("--effort", starts[0])
        self.assertEqual(starts[1][starts[1].index("--effort") + 1], "high")
        self.assertIn("--resume", starts[1])
        self.assertNotIn("--effort", starts[2])
        self.assertNotIn("--model", starts[2])
        self.assertIn("set_model", controls)
        self.assertIn("get_context_usage", controls)
        self.assertTrue(set(controls) <= set(__import__("agentj.agent", fromlist=["x"]).CONTROL_SUBTYPES))

    def test_ask_user_question(self):
        async def script(c):
            ask = "ASK: " + json.dumps(QS, ensure_ascii=False)
            await self.question_flow(c, ask, '"Which color?": "Green", "Which days?": "Mon, Fri"} (input kept)',
                                     "DENIED: 用户在手机上取消了这个问题")
            # the stop switch cancels an open question at once
            serve.Q_TTL = 30
            await c["say"](ask)
            await c["wait"](lambda: len(c["of"]("question")) == 4)
            await c["host"].do_estop("terminal", "终端")
            await c["wait"](lambda: c["of"]("question_done")[-1]["result"] == "stopped")
            sid = await c["say"]("急停中")
            self.assertEqual(c["res"](sid)["why"], "stopped")
            await c["host"].do_resume("terminal", "终端")
        self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertNotIn("Which color", log)

    def test_voice(self):
        asr = FakeASR()

        async def script(c):
            take = wav(1.0)
            bid = await c["upload"](take, mime="audio/wav", purpose="asr", origin="recording", secs=1, name="take.wav")
            await c["wait"](lambda: c["of"]("asr_res"))
            self.assertEqual(c["of"]("asr_res")[-1], {"t": "asr_res", "bid": bid, "ok": True, "text": f"听到了 {len(take)} 字节",
                                                      "engine": "fake", "ms": 3})
            self.assertFalse(list((self.work / ".agentj").rglob("*.wav")) if (self.work / ".agentj").exists() else [],
                             "an asr take never reaches the inbox")
            self.assertFalse(list((self.st.root / "uploads").rglob("*.part")), "and is deleted after")
            stereo = wav(1.0, ch=2)
            await c["upload"](stereo, mime="audio/wav", purpose="asr", origin="recording", secs=1, name="t2.wav")
            await c["wait"](lambda: len(c["of"]("asr_res")) == 2)
            self.assertEqual((c["of"]("asr_res")[-1]["ok"], c["of"]("asr_res")[-1]["why"]), (False, "bad_audio"))
            # a long take as an attachment: transcribed at send time, the words in the Agent's message
            long = wav(2.0)
            att = await c["upload"](long, mime="audio/wav", purpose="att", origin="recording", secs=2, name="long.wav")
            sid = await c["say"]("", att=[att])
            await c["wait"](lambda: any(o["sid"] == sid and o["s"] == "transcribing" for o in c["of"]("say_state")))
            r = c["res"](sid)
            await c["wait"](lambda: c["turns"]().get(r["turn"], {}).get("end") == "done")
            rep = c["turns"]()[r["turn"]]["reply"]["text"]
            self.assertIn("语音转写（2 秒，", rep)
            self.assertIn(f"听到了 {len(long)} 字节", rep)
            asr.state = "not_installed"
            await c["upload"](take, mime="audio/wav", purpose="asr", origin="recording", secs=1, name="t3.wav")
            await c["wait"](lambda: len(c["of"]("asr_res")) == 3)
            self.assertEqual(c["of"]("asr_res")[-1]["why"], "not_installed")
        self.run_chain(script, asr=asr)
        self.assertEqual(len(asr.calls), 2)
        self.assertTrue(all(60 < t <= compose.SAY_ASR_BUDGET for _, t in asr.calls))


# ------------------------------------------------------------------ Codex
class CodexChain(_Chain):
    KIND = "codex"
    ECHO = staticmethod(lambda t: f"ECHO: {t}")

    def test_say_attach_withdraw_meters_effort_questions_local(self):
        async def script(c):
            r, t = await self.basic(c, self.ECHO)
            await self.attach_quote(c, t, self.ECHO)
            await self.withdraw(c, "SLEEP: 1.5", self.ECHO)
            await self.meters_and_models(c, {"used": 21000, "max": 258400}, {"pct": 7.0, "reset": 1790950000},
                                         {"pct": 20.0, "reset": 1791046812})
            await c["wait"](lambda: c["of"]("models") and c["of"]("models")[-1]["models"])
            mm = c["of"]("models")[-1]
            self.assertEqual([(m["id"], m["efforts"]) for m in mm["models"]],
                             [("gpt-fake", ["low", "medium", "high"]), ("gpt-fake-mini", None)])
            self.assertEqual(mm["default"], {"model": "gpt-fake", "effort": "medium"})
            await c["host"]._app(c["s"], {"t": "model_set", "r": "e1", "effort": "high"})
            await c["wait"](lambda: any(o["r"] == "e1" for o in c["of"]("model_res")))
            await c["said"]("高一点")
            await c["host"]._app(c["s"], {"t": "model_set", "r": "e2", "default": True})
            await c["wait"](lambda: any(o["r"] == "e2" for o in c["of"]("model_res")))
            await c["said"]("回到默认")
            qs = [{"id": "q1", "header": "H", "question": "Pick one", "isOther": False, "isSecret": False,
                   "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}]
            serve.Q_TTL = 30
            sid = await c["say"]("ASK: " + json.dumps(qs))
            await c["wait"](lambda: c["of"]("question"))
            q = c["of"]("question")[-1]
            self.assertEqual(q["qs"], [{"q": "Pick one", "h": "H", "m": False, "o": [{"l": "A", "d": "a"}, {"l": "B", "d": "b"}]}])
            await c["answer_q"](q, [[2]])
            rr = c["res"](sid)
            await c["wait"](lambda: c["turns"]().get(rr["turn"], {}).get("end") == "done")
            self.assertIn('ANSWERS: {"q1": {"answers": ["B"]}}', c["turns"]()[rr["turn"]]["reply"]["text"])
            secret = [{**qs[0], "isSecret": True}]
            r2, t2 = await c["said"]("ASK: " + json.dumps(secret))
            self.assertIn("ANSWERS: {}", t2["reply"]["text"])
            self.assertTrue(any(t["src"].get("local") for t in c["turns"]().values()), "a 「在电脑上处理」 page")
            r3, t3 = await c["said"]("FORM")
            self.assertIn('"action": "decline"', t3["reply"]["text"])
        self.run_chain(script)
        starts = [x["params"] for x in self.logged() if x.get("method") == "turn/start"]
        eff = [p.get("effort") for p in starts]
        self.assertIn("high", eff)
        i = eff.index("high")
        self.assertEqual(eff[i + 1], "medium", "default → the human's own configured effort, once")
        self.assertIsNone(eff[0])


# ------------------------------------------------------------------ OpenCode
class OpenCodeChain(_Chain):
    KIND = "opencode"
    ECHO = staticmethod(lambda t: f"ECHO: {t}")

    def test_say_attach_meters_model_questions(self):
        async def script(c):
            r, t = await self.basic(c, self.ECHO)
            await self.attach_quote(c, t, self.ECHO)
            await c["wait"](lambda: c["host"].meter_state.get("ctx") == {"used": 8124, "max": 200000})
            self.assertEqual((c["host"].meter_state["h5"], c["host"].meter_state["week"]), (None, None), "vendor quotas: —")
            await c["host"]._app(c["s"], {"t": "model_set", "r": "o1", "effort": "high"})
            await c["wait"](lambda: any(o["r"] == "o1" for o in c["of"]("model_res")))
            self.assertEqual([o for o in c["of"]("model_res") if o["r"] == "o1"][-1]["why"], "unsupported")
            await c["host"]._app(c["s"], {"t": "model_set", "r": "o2", "model": "opencode/fake-two"})
            await c["wait"](lambda: any(o["r"] == "o2" for o in c["of"]("model_res")))
            self.assertTrue([o for o in c["of"]("model_res") if o["r"] == "o2"][-1]["ok"])
            self.assertEqual(self.st.agent_config()["model"], "opencode/fake-two")
            qs = [{"question": "Pick", "header": "H", "options": [{"label": "A", "description": "a"},
                                                                  {"label": "B", "description": "b"}], "multiple": True}]
            serve.Q_TTL = 30
            sid = await c["say"]("ASK: " + json.dumps(qs))
            await c["wait"](lambda: c["of"]("question"))
            q = c["of"]("question")[-1]
            self.assertTrue(q["qs"][0]["m"])
            await c["answer_q"](q, [[1, 2]])
            rr = c["res"](sid)
            await c["wait"](lambda: c["turns"]().get(rr["turn"], {}).get("end") == "done")
            self.assertIn('ANSWERS: [["A", "B"]]', c["turns"]()[rr["turn"]]["reply"]["text"])
            sid = await c["say"]("ASK: " + json.dumps(qs))
            await c["wait"](lambda: len(c["of"]("question")) == 2)
            await c["answer_q"](c["of"]("question")[-1], cancel=True)
            rr = c["res"](sid)
            await c["wait"](lambda: c["turns"]().get(rr["turn"], {}).get("end") == "done")
            self.assertIn("REJECTED", c["turns"]()[rr["turn"]]["reply"]["text"])
        self.run_chain(script)
        prompts = [x["body"] for x in self.logged() if x.get("path", "").endswith("/prompt_async")]
        self.assertEqual(prompts[-1]["model"], {"providerID": "opencode", "modelID": "fake-two"})
        self.assertTrue(any(r == {"permission": "question", "pattern": "*", "action": "allow"} for r in oc.our_rules()))
        self.assertTrue(any(r == {"permission": "question", "pattern": "*", "action": "deny"}
                            for r in oc.session_rules([], research=True)), "a read-only scheduled run asks nobody")


if __name__ == "__main__":
    unittest.main()
