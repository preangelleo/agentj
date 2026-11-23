"""2026-11-23: Volcano Ark stops serving `kimi-k3` on the Agent Plan and the Coding Plan (personal) on 2026-11-25 14:00
UTC+8; the replacement Ark names is `kimi-k3.1-pro` (ark.json `retired`). Nothing new is written with the old id, the
wizard refuses it by name, and a saved selection moves once with one line for the owner — any other model stays exactly
as it was. Pay-as-you-go and Kimi's own platform are not covered by the notice. No network, keys only by name."""
import _hermetic  # noqa: F401
import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agentj import ai_route, ark, opencode_provider as op, provider_profiles as pp  # noqa: E402
from agentj.core import presets  # noqa: E402
from agentj.state import State  # noqa: E402

OLD, NEW = "kimi-k3", "kimi-k3.1-pro"


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ark-retired-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {"XDG_CONFIG_HOME": str(self.root / ".config"), "HOME": str(self.root)}
        e = mock.patch.dict("os.environ", self.env)
        e.start()
        self.addCleanup(e.stop)
        p = mock.patch("agentj.service.env_file", lambda name: "/svc/agentj.env")
        p.start()
        self.addCleanup(p.stop)

    def state(self, kind="opencode", model=None):
        st = State(Path(tempfile.mkdtemp(dir=self.root)) / "state")
        st.init(relay="ws://127.0.0.1:9")
        (self.root / "work").mkdir(exist_ok=True)
        st.set_agent_config(kind, str(self.root / "work"), model, fence=True, docker=False, record_root=False)
        return st

    def profile(self, pid="ark-agent-claude", model=OLD, used=True, preset="ark-agent"):
        pp.define(pid, "claude", pp.preset_url(preset, "anthropic"), model)
        if used:
            b = pp.profile(pid) / "backups" / "1"
            b.mkdir(parents=True, mode=0o700)
            (b / "record.json").write_text("{}")
        return pp.profile(pid) / "profile.json"


class Preset(Home):
    def test_no_plan_offers_the_retired_model_and_both_have_its_replacement(self):
        self.assertEqual(ark.data()["retired"], {OLD: NEW})
        for plan in ("agent", "coding"):
            ms = ark.plan_data(plan)["block"]["models"]
            self.assertNotIn(OLD, ms)
            self.assertEqual(ms[NEW]["modalities"]["input"], ["text", "image"])
        self.assertTrue(ark.vision(NEW))

    def test_every_default_and_hint_names_a_model_that_is_still_served(self):
        d = ark.data()
        served = set(d["block"]["models"])
        self.assertEqual(d["model_strong"], NEW)
        for name in (d["model"], d["model_strong"], d["model_text"], *d["pro_suggested"], *d["agent"]["medium_up"],
                     *d["retired"].values()):
            self.assertIn(name, served)
        self.assertFalse(served & set(d["retired"]))

    def test_new_block_written_to_opencode_has_no_retired_model(self):
        for plan, pid in (("agent", "ark-agent-plan"), ("coding", "ark-coding-plan")):
            r = ark.install(environ=self.env, plan=plan)
            self.assertEqual(r["model"], pid + "/doubao-seed-2.1-pro")          # the default did not change
            self.assertIn(f"{pid}/{NEW}", r["vision_models"])
            self.assertNotIn(f"{pid}/{OLD}", r["models"])
            self.assertNotIn(OLD, json.loads(Path(r["file"]).read_text())["provider"][pid]["models"])

    def test_builtin_harness_coding_plan_row(self):
        self.assertEqual(presets.resolve("ark-coding-plan/" + NEW, environ={}).context, 1024000)
        with self.assertRaises(presets.PresetError) as e:
            presets.resolve("ark-coding-plan/" + OLD, environ={})
        self.assertEqual(str(e.exception), "unknown_model")
        row = presets.providers()["ark-coding-plan"]
        self.assertNotIn(OLD, [*row["fallback"], *row["roles"].values()])
        for m in row["fallback"]:                                                 # the chain only names models of the row
            self.assertIn(m, row["models"])


class Wizard(Home):
    def test_asking_for_the_retired_model_is_refused_with_its_replacement(self):
        for plan in ("agent", "coding"):
            with self.assertRaises(op.ProviderError) as e:
                ark.install(OLD, environ=self.env, plan=plan)
            self.assertIn(NEW, str(e.exception))
            self.assertIn(OLD, str(e.exception))
        self.assertFalse(Path(op.config_file(self.env)).exists())                 # nothing written

    def test_cli_and_switch_say_so(self):
        a = SimpleNamespace(action="ark", model=[OLD], plan=None, harness=None, lang="zh", dry_run=False, json=True)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ark.cmd(a), 2)
        self.assertIn(NEW, json.loads(out.getvalue())["error"])
        r = ark.switch(None, "agent", "ark-agent-plan/" + OLD, request=lambda *a, **k: self.fail("no switch"))
        self.assertEqual((r["ok"], r["reason"]), (False, "bad_model"))
        self.assertIn(NEW, ark.say_switch(r, "zh"))

    def test_pay_as_you_go_ids_are_not_touched(self):
        self.assertEqual(ark._model(OLD, "payg"), OLD)          # the notices are about the two plans only

    def test_recommendation_and_notes_no_longer_tie_a_tier_to_the_old_model(self):
        for lang in ("zh", "en"):
            text = "\n".join(ai_route.ark_steps(lang) + list(ai_route.PICK[lang == "en"]))
            self.assertNotIn("Kimi K3 ", text + " ")
            self.assertNotRegex(text, r"Kimi K3(?!\.1)")
            for plan, pid in (("agent", "ark-agent-plan"), ("coding", "ark-coding-plan")):
                note = ark.model_note(f"{pid}/{NEW}", lang)
                self.assertNotIn("Medium", note)
                self.assertNotIn("Pro 套餐", note)
                said = ark.render(ark.install(environ=self.env, plan=plan, dry_run=True), lang)
                self.assertIn(NEW, said)
                self.assertNotRegex(said, r"kimi-k3 (从|扣|uses|needs)")
        self.assertIn("ark-coding-plan/" + NEW, ark.model_note("ark-coding-plan/glm-5.3", "zh"))   # the image-reading hint


class Moved(Home):
    def test_only_the_two_plans_and_only_retired_ids(self):
        self.assertEqual(ark.moved("ark-agent-plan/" + OLD), "ark-agent-plan/" + NEW)
        self.assertEqual(ark.moved("ark-coding-plan/" + OLD), "ark-coding-plan/" + NEW)
        for kept in (None, "", OLD, "ark-api/" + OLD, "ark/" + OLD, "moonshotai/" + OLD, "opencode-go/" + OLD,
                     "ark-agent-plan/" + NEW, "ark-agent-plan/glm-5.3", "ark-agent-plan/kimi-k3x"):
            self.assertIsNone(ark.moved(kept), kept)

    def test_saved_selection_moves_once_and_the_owner_gets_one_line(self):
        for kind, pid in (("opencode", "ark-agent-plan"), ("opencode", "ark-coding-plan"), ("core", "ark-coding-plan")):
            st = self.state(kind, f"{pid}/{OLD}")
            before = st.config()["agent"]
            lines = ark.migrate(st)
            self.assertEqual(len(lines), 1)
            self.assertIn(f"{pid}/{OLD}", lines[0])
            self.assertIn(f"{pid}/{NEW}", lines[0])
            self.assertEqual(lines[0].count(" / "), 1)                            # text.one() picks the owner's language
            self.assertEqual(st.config()["agent"], {**before, "model": f"{pid}/{NEW}"})   # kind, folder, fence: as they were
            self.assertEqual(ark.migrate(st), lines)                              # never sent (serve restarted first): again
            ark.told(st)
            self.assertEqual(ark.migrate(st), [])                                 # sent: once

    def test_any_other_choice_is_left_alone(self):
        for kind, model in (("opencode", "ark-agent-plan/glm-5.3"), ("opencode", "ark-api/" + OLD), ("opencode", None),
                            ("opencode", "moonshotai/" + OLD), ("claude", "opus"), ("core", "ark/" + OLD)):
            st = self.state(kind, model)
            raw = st.config_path.read_bytes()
            self.assertEqual(ark.migrate(st), [])
            self.assertEqual(st.config_path.read_bytes(), raw)

    def test_no_agent_or_unreadable_config_never_raises(self):
        st = State(self.root / "bare")
        st.init(relay="ws://127.0.0.1:9")
        self.assertEqual(ark.migrate(st), [])
        st.config_path.write_text("{not json")
        self.assertEqual(ark.migrate(st), [])
        self.assertEqual(ark.migrate(SimpleNamespace()), [])


class NeverStopsServe(Home):
    """Security review V1: before this change a saved model that is not a string was tolerated (state.agent_config)."""

    def odd(self, model):
        st = self.state("opencode", "ark-agent-plan/glm-5.3")
        cfg = st.config()
        cfg["agent"]["model"] = model
        st.config_path.write_text(json.dumps(cfg))
        return st, st.config_path.read_bytes()

    def host(self, st):
        from agentj.serve import Host

        async def go():
            h = Host(st, events="quiet", read_stdin=False)
            return h.agent_cfg, list(h.model_moved)
        return asyncio.run(go())

    def test_saved_model_that_is_not_a_string(self):
        for model in (5, ["ark-agent-plan/" + OLD], {"id": OLD}, True):
            self.assertIsNone(ark.moved(model))
            st, raw = self.odd(model)
            self.assertEqual(ark.migrate(st), [])
            cfg, lines = self.host(st)
            self.assertEqual((cfg["model"], lines), (None, []))
            self.assertEqual(st.config_path.read_bytes(), raw)

    def test_config_that_cannot_be_written(self):
        st = self.state("opencode", "ark-agent-plan/" + OLD)
        raw, events = st.config_path.read_bytes(), []
        st.log = lambda ev, **kw: events.append((ev, kw))
        with mock.patch.object(State, "set_agent_model", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(ark.migrate(st), [])
            cfg, lines = self.host(st)
        self.assertEqual((cfg["model"], lines), ("ark-agent-plan/" + OLD, []))    # still the owner's saved choice
        self.assertEqual(st.config_path.read_bytes(), raw)
        failed = [kw for ev, kw in events if ev == "model_retired"]
        self.assertTrue(failed)
        for kw in failed:                                                         # a fixed word and a class name, no content
            self.assertEqual(kw, {"result": "failed", "exception_type": "OSError"})
        self.assertFalse((st.root / "model-moved.json").exists())                 # nothing moved: nothing to tell later


class ClaudeProfile(Home):
    def test_profile_in_use_is_named_once_and_never_rewritten(self):
        for pid, preset, flag in (("ark-agent-claude", "ark-agent", ""), ("ark-claude", "ark-coding", " --plan coding")):
            f = self.profile(pid, preset=preset)
            raw = f.read_bytes()
            lines = ark.migrate(self.state("claude"))
            self.assertEqual(len(lines), 1, pid)
            self.assertIn(pid, lines[0])
            self.assertIn(f"agentj provider ark --harness claude{flag} --model {NEW}`", lines[0])
            self.assertEqual(lines[0].count(" / "), 1)
            self.assertEqual(f.read_bytes(), raw)
            st = self.state("claude")
            self.assertEqual(ark.migrate(st), lines)                              # not sent yet: named again
            ark.told(st)
            self.assertEqual(ark.migrate(self.state("claude")), [])
            self.assertEqual(f.read_bytes(), raw)

    def test_unused_other_model_or_other_service_profiles_say_nothing(self):
        self.profile("ark-agent-claude", used=False)                              # saved, never switched to
        self.profile("ark-claude", model=NEW, preset="ark-coding")
        pp.define("other", "claude", "https://api.example.com/v1", OLD)
        (pp.profile("other") / "backups" / "1").mkdir(parents=True)
        (pp.profile("other") / "backups" / "1" / "record.json").write_text("{}")
        self.assertEqual(ark.migrate(self.state("claude")), [])

    def test_the_command_in_that_line_works_beside_the_old_profile(self):
        old = self.profile()
        raw = old.read_bytes()
        a = SimpleNamespace(action="ark", model=[NEW], plan=None, harness="claude", lang="zh", dry_run=False, json=True)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ark.cmd(a), 0)
        steps = json.loads(out.getvalue())["next"]
        new = "ark-agent-claude-kimi-k3-1-pro"
        self.assertIn(f"profile save {new} --harness claude", steps[0])
        self.assertIn(str(pp.profile(new) / "key"), steps[1])
        self.assertTrue(steps[2].endswith("profile use " + new))
        self.assertEqual(json.loads((pp.profile(new) / "profile.json").read_text())["model"], NEW)
        self.assertEqual(old.read_bytes(), raw)

    def test_the_same_model_again_keeps_the_fixed_profile(self):
        a = SimpleNamespace(action="ark", model=[NEW], plan=None, harness="claude", lang="zh", dry_run=False, json=True)
        for _ in range(2):
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(ark.cmd(a), 0)
            self.assertTrue(json.loads(out.getvalue())["next"][2].endswith("profile use ark-agent-claude"))
        self.assertEqual([p["id"] for p in pp.listing()], ["ark-agent-claude"])


class Serve(Home):
    def test_host_starts_on_the_replacement_and_tells_the_owner_once(self):
        from agentj.serve import Host
        st = self.state("opencode", "ark-agent-plan/" + OLD)

        async def go():
            host = Host(st, events="quiet", read_stdin=False)
            self.assertEqual(host.agent_cfg["model"], "ark-agent-plan/" + NEW)
            told = []
            with mock.patch.object(host, "agent_notice", told.append):
                host.tell_model_moved()
                host.tell_model_moved()
            for task in list(getattr(host, "_bg_tasks", ())):
                task.cancel()
            return told

        told = asyncio.run(go())
        self.assertEqual(len(told), 1)
        self.assertIn("ark-agent-plan/" + NEW, told[0])
        self.assertEqual(st.agent_config()["model"], "ark-agent-plan/" + NEW)

        async def again():
            host = Host(st, events="quiet", read_stdin=False)
            return list(host.model_moved)
        self.assertEqual(asyncio.run(again()), [])

    def test_line_survives_a_restart_before_the_agent_was_up_or_a_failed_send(self):
        """Security review V5: the selection has moved, so only the kept line can still tell the owner."""
        from agentj.serve import Host
        st = self.state("opencode", "ark-agent-plan/" + OLD)

        async def start(send):
            host = Host(st, events="quiet", read_stdin=False)
            lines = list(host.model_moved)
            if send:
                with mock.patch.object(host, "agent_notice", send):
                    try:
                        host.tell_model_moved()
                    except RuntimeError:
                        pass
            return lines

        def broken(text):
            raise RuntimeError("history is not writable")
        first = asyncio.run(start(None))                      # serve stopped before the Agent started
        self.assertEqual(len(first), 1)
        self.assertEqual(asyncio.run(start(broken)), first)   # sending failed: kept
        got = []
        self.assertEqual(asyncio.run(start(got.append)), first)
        self.assertEqual(got, first)
        self.assertEqual(asyncio.run(start(got.append)), [])  # delivered: never again
        self.assertEqual(got, first)


if __name__ == "__main__":
    unittest.main()
