"""P239 host backstop for the phone's model · effort pill (the phone itself waits 5 s after the last tap).

A lone switch runs at once; a switch that follows another within MODEL_SET_COALESCE is held, a newer one inside the window
replaces it (the replaced request is answered ok + "superseded"), and only the last runs, MODEL_SET_COALESCE after it came.
Host.on_model_set / _model_set_go / _model_set_res run on a small stand-in (no relay, no Agent process).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import types
import unittest

from agentj import serve


class FakeAgent:
    def __init__(self):
        self.cmds = []

    def submit(self, cmd):
        self.cmds.append(cmd.data)


class FakeSt:
    def log(self, *a, **k):
        pass


def stand_in():
    h = types.SimpleNamespace(agent=FakeAgent(), st=FakeSt(), model_sets=0, model_set_at=0.0, model_set_held=None, sent=[])
    h.stopped = lambda: False

    async def send_app(s, obj):
        h.sent.append(obj)
        return True
    h.send_app = send_app
    for name in ("on_model_set", "_model_set_res", "_model_set_go", "_model_set_flush"):
        setattr(h, name, types.MethodType(getattr(serve.Host, name), h))
    return h


def session(cid=1):
    return types.SimpleNamespace(cid=cid, device="d", name="phone", state="ready")


class P239Coalesce(unittest.TestCase):
    def setUp(self):
        self.old = serve.MODEL_SET_COALESCE
        serve.MODEL_SET_COALESCE = 0.2

    def tearDown(self):
        serve.MODEL_SET_COALESCE = self.old

    def test_lone_switch_runs_at_once(self):
        async def go():
            h = stand_in()
            await h.on_model_set(session(), {"r": "r1", "model": "opus", "effort": "high"})
            self.assertEqual([c["model"] for c in h.agent.cmds], ["opus"])
            self.assertIsNone(h.model_set_held)
        asyncio.run(go())

    def test_six_fast_switches_run_first_and_last_only(self):
        async def go():
            h = stand_in()
            s = session()
            for i, m in enumerate(["a", "b", "c", "d", "e", "f"]):
                await h.on_model_set(s, {"r": f"r{i}", "model": m, "effort": None})
                await asyncio.sleep(0.02)
            self.assertEqual([c["model"] for c in h.agent.cmds], ["a"], "the burst's tail is held")
            await asyncio.sleep(0.3)
            self.assertEqual([c["model"] for c in h.agent.cmds], ["a", "f"], "only the last of the burst runs")
            sup = [o["r"] for o in h.sent if o.get("why") == "superseded"]
            self.assertEqual(sup, ["r1", "r2", "r3", "r4"])
            self.assertTrue(all(o["ok"] for o in h.sent if o.get("why") == "superseded"))
        asyncio.run(go())

    def test_held_switch_rechecks_stopped(self):
        async def go():
            h = stand_in()
            await h.on_model_set(session(), {"r": "r1", "model": "a"})
            await h.on_model_set(session(), {"r": "r2", "model": "b"})
            h.stopped = lambda: True
            await asyncio.sleep(0.3)
            self.assertEqual([c["model"] for c in h.agent.cmds], ["a"])
            self.assertEqual(h.sent[-1], {"t": "model_res", "r": "r2", "ok": False, "why": "stopped"})
        asyncio.run(go())

    def test_after_the_window_a_switch_runs_at_once_again(self):
        async def go():
            h = stand_in()
            await h.on_model_set(session(), {"r": "r1", "model": "a"})
            await asyncio.sleep(0.25)
            await h.on_model_set(session(), {"r": "r2", "model": "b"})
            self.assertEqual([c["model"] for c in h.agent.cmds], ["a", "b"])
        asyncio.run(go())

    def test_a_message_flushes_the_held_switch_first(self):
        # P240 (parity pill-long-press-default on the real host): hold = default right after a tap's switch is held by
        # the backstop; the next message must run on the default, so the held switch goes to the Agent ahead of it, once
        async def go():
            h = stand_in()
            await h.on_model_set(session(), {"r": "r1", "model": "a", "effort": "high"})
            await h.on_model_set(session(), {"r": "r2", "default": True})
            self.assertIsNotNone(h.model_set_held)
            await h._model_set_flush()
            h.agent.cmds.append("message")
            self.assertEqual(h.agent.cmds, [{"r": "r1", "cid": 1, "model": "a", "effort": "high"},
                                            {"r": "r2", "cid": 1, "default": True}, "message"])
            self.assertIsNone(h.model_set_held)
            await asyncio.sleep(0.3)                       # the cancelled timer does not run it twice
            self.assertEqual(len(h.agent.cmds), 3)
            await h._model_set_flush()                     # nothing held: nothing sent
            self.assertEqual(len(h.agent.cmds), 3)
        asyncio.run(go())

    def test_default_window_is_two_seconds(self):
        self.assertEqual(self.old, 2.0)


if __name__ == "__main__":
    unittest.main()
