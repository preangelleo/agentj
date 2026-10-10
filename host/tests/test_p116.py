"""P116 (B11 + B5): owner private instructions file, shared identity loading, shared Claude model/effort/cost/slash,
status-line layer chaining and the single approval answerer. Fakes only (fake Herdr + Claude picker captured from
Claude Code 2.1.280 by Relay's ADR-035 tests); no network, no real session."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import (claude_inbound, claude_statusline, main_identity, preferences, private_instructions as pi, shared,
                    shared_hook, shared_modelswitch as ms)
from agentj.agent import Agent

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
st_path = os.environ["FAKE_TUI"]
st = json.load(open(st_path))
argv = sys.argv[1:]
st.setdefault("argv", []).append(argv)
# The picker as this account showed it on 2026-09-27: the "Opus (1M context)" row
# exists only while the session runs its default model (settings opus[1m]) —
# `session_null` — or that model itself; "Default" is the account default (Sonnet 5).
# st["static"] = the other layout seen (no server list): the 1M row always there.
def rows():
    if st.get("v289"):   # Claude Code 2.1.289 as probed by P116 (versioned labels, current row ✔)
        return [("Default (recommended)", "Opus 5.5 · Best for everyday, complex tasks", None),
                ("Opus 5.5", "For complex work and everyday tasks", "Opus 5.5"),
                ("Fable 5.1", "For your toughest challenges", "Fable 5.1"),
                ("Sonnet 5.5", "Most efficient for simpler tasks", "Sonnet 5.5"), ("Haiku 4.5", "Fastest for quick answers", "Haiku 4.5")]
    if st.get("static"):
        return [("Default (recommended)", "Use the default model (currently Opus 5.5 (1M context))", None),
                ("Opus (1M context)", "Opus 5.5 with 1M context · Best", "Opus 5.5 (1M context)"),
                ("Fable", "Fable 5.1 · Most capable", "Fable 5.1"), ("Sonnet", "Sonnet 5 · Efficient", "Sonnet 5"),
                ("Haiku", "Haiku 4.5 · Fastest", "Haiku 4.5")]
    r = [("Default (recommended)", "Sonnet 5 · Efficient for routine tasks", None),
         ("Sonnet", "Sonnet 5 · Efficient for routine tasks", "Sonnet 5"),
         ("Fable", "Fable 5.1 · Most capable", "Fable 5.1"), ("Opus", "Opus 5.5 · Best for everyday", "Opus 5.5"),
         ("Haiku", "Haiku 4.5 · Fastest", "Haiku 4.5")]
    if st.get("session_null") or st["model"] == "Opus 5.5 (1M context)":
        r.append(("Opus (1M context)", "Opus 5.5 with 1M context · Best for everyday", "Opus 5.5 (1M context)"))
    return r
RING = ["Low", "Medium", "High", "xHigh", "Max", "Ultracode"]
def save():
    json.dump(st, open(st_path, "w"))
def commit():
    lab, _, name = rows()[st["cursor"]]
    if name is None:                                   # Default: the session model is cleared
        st["session_null"] = True
        st["model"] = "Opus 5.5 (1M context)" if st.get("static") else "Sonnet 5"
    else:
        st["session_null"] = False
        st["model"] = name
    st["effort"] = RING[st["ring"]].lower()
    st["mode"] = "prompt"
    if not st.get("no_statusline"):
        d = os.path.join(os.environ["FAKE_STATE"], "claude-statusline"); os.makedirs(d, exist_ok=True)
        json.dump({"session_id": "sess-jarvis", "model": None, "model_name": st["model"], "effort": st["effort"],
                   "ctx": None, "h5": None, "week": None, "cost_usd": 0.42}, open(os.path.join(d, "sess-jarvis.json"), "w"))
def screen():
    head = st.get("above", "● earlier reply\n  1. Fable  a numbered line in the transcript\n")
    if st["mode"] == "picker":
        out = []
        for i, (lab, desc, name) in enumerate(rows()):
            mark = "❯" if i == st["cursor"] else " "
            cur = (name is None and st.get("session_null")) or (name == st["model"] and not st.get("session_null"))
            out.append(f"   {mark} {i + 1}. {lab}{' ✔' if cur else ''}    {desc}")
        return head + "   Select model\n   Switch between Claude models.\n" + "\n".join(out) + \
            f"\n   ◐ {RING[st['ring']]} effort ←/→ to adjust\n   Enter to set as default · s to use this session only · Esc to cancel\n"
    if st["mode"] == "confirm":
        return head + "  Switch model?\n  Your next response will be slower\n  ❯ 1. Yes, switch to X\n    2. No, go back\n"
    box = st.get("box", "")
    return head + box + "────\n❯ " + st.get("input", "") + "\n────\n  " + st["model"] + " " + st["effort"] + "\n"
cmd = argv[:2]
if cmd == ["agent", "list"]:
    print(json.dumps({"result": {"agents": [{"agent": "claude", "agent_session": {"value": "sess-jarvis"},
                                              "agent_status": st["status"], "pane_id": "w9:p1"}]}}))
elif cmd == ["agent", "read"]:
    print(screen())
elif cmd == ["agent", "prompt"]:
    if st["status"] == "blocked":
        save(); sys.exit(1)
    text = argv[3]
    if text != "/model":
        st["bad_text"] = text
    elif st["mode"] == "prompt":
        st["mode"] = "picker"
        rs = rows()
        st["cursor"] = 0 if st.get("session_null") else next(i for i, r in enumerate(rs) if r[2] == st["model"])
        st["ring"] = RING.index({"xhigh": "xHigh"}.get(st["effort"], st["effort"].capitalize()))
    print(json.dumps({"result": {"ok": True}}))
elif cmd == ["agent", "send-keys"]:
    for k in argv[3:]:
        if k not in ("up", "down", "left", "right", "s", "enter", "esc"):
            st["bad_key"] = k; continue
        if st["mode"] == "picker":
            n = len(rows())
            if k == "down": st["cursor"] = (st["cursor"] + 1) % n          # the real list wraps
            elif k == "up": st["cursor"] = (st["cursor"] - 1) % n
            elif k == "right": st["ring"] = (st["ring"] + 1) % len(RING)
            elif k == "left": st["ring"] = (st["ring"] - 1) % len(RING)
            elif k == "esc": st["mode"] = "prompt"
            elif k == "enter": st["default_written"] = True; commit()
            elif k == "s":
                if st.get("confirm"): st["mode"] = "confirm"
                else: commit()
        elif st["mode"] == "confirm":
            if k == "enter": commit()
            elif k == "esc": st["mode"] = "prompt"
        else:
            st.setdefault("stray", []).append(k)
    print(json.dumps({"result": {"ok": True}}))
else:
    save(); sys.exit(2)
save()
'''


class Tmp(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="p116-", dir="/var/tmp"); self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name); os.chmod(self.root, 0o700)

    def file(self, text, mode=0o600, name="private.md"):
        p = self.root / name
        p.write_bytes(text if isinstance(text, bytes) else text.encode()); os.chmod(p, mode)
        return p


class Loader(Tmp):
    def test_reasons_are_fixed_and_nothing_loads_on_failure(self):
        self.assertEqual(pi.load("")["reason"], "unset")
        self.assertEqual(pi.load("relative/x.md")["reason"], "not_absolute")
        self.assertEqual(pi.load("https://example.com/x.md")["reason"], "not_absolute")
        self.assertEqual(pi.load(str(self.root / "nope.md"))["reason"], "missing")
        self.assertEqual(pi.load(str(self.root))["reason"], "not_regular")
        self.assertEqual(pi.load(str(self.file("x", 0o666)))["reason"], "unsafe_permissions")
        self.assertEqual(pi.load(str(self.file("x" * (pi.MAX_BYTES + 1))))["reason"], "too_large")
        self.assertEqual(pi.load(str(self.file(b"\xff\xfe bad")))["reason"], "not_utf8")
        self.assertEqual(pi.load(str(self.file("a\x00b")))["reason"], "not_utf8")
        for r in ("unsafe_permissions", "too_large", "not_utf8", "missing"):
            self.assertIn(r, pi.REASONS)
        bad = self.root / "shared"; bad.mkdir(); os.chmod(bad, 0o777)
        p = bad / "f.md"; p.write_text("x"); os.chmod(p, 0o600)
        self.assertEqual(pi.load(str(p))["reason"], "unsafe_parent")

    def test_ok_utf8_bom_trimmed_and_symlink_canonical(self):
        p = self.file("\ufeff# 主人规则\n- 先读 boot set\n")
        got = pi.load(str(p))
        self.assertTrue(got["ok"]); self.assertEqual(got["text"], "# 主人规则\n- 先读 boot set")
        link = self.root / "link.md"; link.symlink_to(p)
        self.assertEqual(pi.load(str(link))["text"], got["text"])
        unsafe = self.file("x", 0o622, "unsafe.md"); link2 = self.root / "l2.md"; link2.symlink_to(unsafe)
        self.assertFalse(pi.load(str(link2))["ok"])
        self.assertEqual(pi.load(str(self.file("  \n")))["reason"], "empty")

    def test_tilde_path(self):
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            self.file("hello")
            self.assertTrue(pi.load("~/private.md")["ok"])

    def test_identity_appends_after_core_and_preferences_and_never_raises(self):
        p = self.file("PRIVATE-RULE-7")
        cfg = {"language": "zh", "instructions": "PERSONAL", "private_instructions_file": str(p)}
        text = main_identity.prompt(cfg)
        core = (main_identity.DATA / "core.zh.md").read_text()
        self.assertTrue(text.startswith(core))
        self.assertLess(text.index("PERSONAL"), text.index("PRIVATE-RULE-7"))
        self.assertIn("grants no permission", text)
        os.chmod(p, 0o666)
        self.assertNotIn("PRIVATE-RULE-7", main_identity.prompt(cfg))
        self.assertEqual(main_identity.prompt({"language": "zh"}), main_identity.prompt({"language": "zh", "private_instructions_file": ""}))

    def test_owned_claude_argv_only_changes_the_identity_text(self):
        from agentj.agent import ClaudeAgent
        host = Mock(); host.st.root = self.root
        p = self.file("PRIVATE-RULE-8")
        a = ClaudeAgent(host, {"kind": "claude", "dir": str(self.root), "language": "en"})
        b = ClaudeAgent(host, {"kind": "claude", "dir": str(self.root), "language": "en", "private_instructions_file": str(p)})
        x, y = a.argv(None), b.argv(None)
        i = x.index("--append-system-prompt")
        self.assertEqual(x[:i + 1] + x[i + 2:], y[:i + 1] + y[i + 2:])   # no flag, permission or setting added
        self.assertIn("PRIVATE-RULE-8", y[i + 1]); self.assertNotIn("PRIVATE-RULE-8", x[i + 1])

    def test_logs_and_status_carry_no_text_path_or_digest(self):
        p = self.file("SECRET-LOOKING-TEXT")
        st = Mock(); st.root = self.root; st.write_private.side_effect = lambda q, b: Path(q).write_bytes(b)
        s = pi.status({"private_instructions_file": str(p)}, st)
        self.assertEqual((s["ok"], s["reason"], s["generation"]), (True, "loaded", 1))
        blob = json.dumps(s) + json.dumps([c.kwargs for c in st.log.call_args_list])
        for leak in ("SECRET-LOOKING-TEXT", str(p), pi.load(str(p))["digest"]):
            self.assertNotIn(leak, blob)
        self.assertEqual(st.log.call_args.kwargs, {"result": "ok", "reason": "loaded", "generation": 1})
        self.assertEqual(pi.status({"private_instructions_file": str(p)}, st)["generation"], 1)   # unchanged = same gen
        p.write_text("changed"); os.chmod(p, 0o600)
        self.assertEqual(pi.status({"private_instructions_file": str(p)}, st)["generation"], 2)

    def test_config_accepts_local_paths_only(self):
        preferences.validate({"agent": {"private_instructions_file": "~/x.md"}})
        preferences.validate({"agent": {"private_instructions_file": "/abs/x.md", "shared_identity": False}})
        for bad in ("relative.md", "https://example.com/x"):
            with self.assertRaises(preferences.ConfigError):
                preferences.validate({"agent": {"private_instructions_file": bad}})
        self.assertEqual(preferences.SCHEMA["agent.private_instructions_file"]["default"], "")

    def test_edit_between_turns_reloads_identity(self):
        p = self.file("v1")
        host = Mock(); host.st.root = self.root
        a = Agent(host, {"kind": "claude", "dir": str(self.root), "private_instructions_file": str(p)})
        a.reload_identity = AsyncMock()
        async def go():
            await a._identity_refresh(); a.reload_identity.assert_not_awaited()
            await a._identity_refresh(); a.reload_identity.assert_not_awaited()
            p.write_text("v2"); os.chmod(p, 0o600)
            await a._identity_refresh(); a.reload_identity.assert_awaited_once()
        asyncio.run(go())


    def test_doctor_row_and_hosts_without_state_root(self):
        from agentj import doctor
        from agentj.state import State
        p = self.file("rule")
        st = Mock(); st.exists.return_value = True; st.agent_config.return_value = None; st.config.return_value = {}
        with patch.object(preferences, "effective", return_value={"agent": {"private_instructions_file": str(p)}}):
            rows = doctor.check_main_identity(st)
        row = next(r for r in rows if r["id"] == "private-instructions")
        self.assertEqual(row["status"], "ok"); self.assertNotIn(str(p), json.dumps(rows))
        from types import SimpleNamespace
        a = Agent(SimpleNamespace(st=SimpleNamespace(log=lambda *x, **k: None)), {"kind": "x", "dir": str(self.root)})
        asyncio.run(a._identity_refresh())                      # unconfigured: no state access at all
        a.cfg["private_instructions_file"] = str(p)
        asyncio.run(a._identity_refresh())                      # configured, host without a state root: no crash


class SharedIdentity(Tmp):
    def agent(self, **cfg):
        host = Mock(); host.st.root = self.root; host.stopped.return_value = False
        host.st.write_private.side_effect = lambda p, b: (Path(p).write_bytes(b), os.chmod(p, 0o600))
        a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": str(self.root), "language": "zh",
                                            "session_mode": "shared", **cfg})
        a.session = {"sessionId": "owner", "pid": 1}
        return a, host

    def run_(self, coro):
        return asyncio.run(coro)

    def test_session_start_injects_core_and_private_once_per_generation(self):
        p = self.file("FOUNDER-MAP-1")
        a, host = self.agent(private_instructions_file=str(p))
        out = self.run_(a.hook_event({"hook_event_name": "SessionStart", "source": "resume", "session_id": "owner"}))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("FOUNDER-MAP-1", ctx); self.assertIn("Agent J 主 Agent 宪法", ctx)
        self.assertEqual(set(out["hookSpecificOutput"]), {"hookEventName", "additionalContext"})   # no decision of any kind
        # the phone turn after that does not repeat it
        a.phone_turn, a.pending_text = True, "hi"
        up = self.run_(a.hook_event({"hook_event_name": "UserPromptSubmit", "session_id": "owner", "prompt": "hi"}))
        self.assertNotIn("FOUNDER-MAP-1", up["hookSpecificOutput"]["additionalContext"])
        # compaction drops it from the context → next start reads again; an edit (new generation) too
        self.run_(a.hook_event({"hook_event_name": "PostCompact", "session_id": "owner"}))
        up = self.run_(a.hook_event({"hook_event_name": "UserPromptSubmit", "session_id": "owner", "prompt": "hi"}))
        self.assertIn("FOUNDER-MAP-1", up["hookSpecificOutput"]["additionalContext"])
        p.write_text("FOUNDER-MAP-2"); os.chmod(p, 0o600)
        up = self.run_(a.hook_event({"hook_event_name": "UserPromptSubmit", "session_id": "owner", "prompt": "hi"}))
        self.assertIn("FOUNDER-MAP-2", up["hookSpecificOutput"]["additionalContext"])
        logged = [c.kwargs for c in host.st.log.call_args_list if c.args and c.args[0] == "shared_identity"]
        self.assertTrue(logged and all("FOUNDER" not in json.dumps(k) and str(p) not in json.dumps(k) for k in logged))

    def test_foreign_session_and_opt_out_get_nothing(self):
        a, _ = self.agent()
        self.assertEqual(self.run_(a.hook_event({"hook_event_name": "SessionStart", "source": "startup", "session_id": "other"})), {})
        b, _ = self.agent(shared_identity=False)
        self.assertEqual(self.run_(b.hook_event({"hook_event_name": "SessionStart", "source": "startup", "session_id": "owner"})), {})

    def test_long_identity_goes_to_private_file_and_transcript_marker_proves_read(self):
        a, host = self.agent(language="en", private_instructions_file=str(self.file("EN-PRIVATE")))
        out = self.run_(a.hook_event({"hook_event_name": "SessionStart", "source": "startup", "session_id": "owner"}))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        f = self.root / "shared-identity.md"
        self.assertIn(str(f), ctx); self.assertNotIn("EN-PRIVATE", ctx)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600); self.assertIn("EN-PRIVATE", f.read_text())
        marker = a.identity_marker[0]
        a._identity_seen(b'{"type":"user","toolUseResult":"<!-- ' + marker.encode() + b' -->"}\n')
        self.assertIn(("shared_identity",), [c.args for c in host.st.log.call_args_list])
        self.assertEqual(host.st.log.call_args.kwargs["result"], "read")
        self.assertIsNone(a.identity_marker)

    def test_audit_mechanism_is_hook_for_shared_claude(self):
        self.assertEqual(main_identity.expected({"language": "zh", "session_mode": "shared"}, "claude")["mechanism"], "hook.additionalContext")
        self.assertEqual(main_identity.expected({"language": "zh"}, "claude")["mechanism"], "append-system-prompt")


class Picker(Tmp):
    def setUp(self):
        super().setUp()
        self.herdr = self.root / "herdr"; self.herdr.write_text(FAKE); os.chmod(self.herdr, 0o700)
        self.tui = self.root / "tui.json"
        self.state = self.root / "st"; self.state.mkdir()
        self.set(mode="prompt", status="idle", model="Opus 5.5 (1M context)", effort="medium", input="", session_null=True)
        env = patch.dict(os.environ, {"AGENTJ_HERDR": str(self.herdr), "FAKE_TUI": str(self.tui), "FAKE_STATE": str(self.state)})
        env.start(); self.addCleanup(env.stop)
        sp = patch.object(ms, "STEP_S", 0); sp.start(); self.addCleanup(sp.stop)
        self.sess = {"pid": 4242, "sessionId": "sess-jarvis"}

    def set(self, **kw):
        st = json.loads(self.tui.read_text()) if self.tui.exists() else {}
        st.update(kw); self.tui.write_text(json.dumps(st))

    def tui_(self):
        return json.loads(self.tui.read_text())

    def test_session_only_switch_never_sets_default_or_types_text(self):
        r = ms.apply(self.sess, "fable", "high")
        self.assertEqual(r["result"], ms.SENT, r)
        t = self.tui_()
        self.assertEqual((t["model"], t["effort"]), ("Fable 5.1", "high"))
        self.assertNotIn("default_written", t); self.assertNotIn("bad_text", t); self.assertNotIn("bad_key", t)
        self.assertNotIn("stray", t)

    def test_opus_1m_reached_via_default_row(self):
        self.set(model="Sonnet 5", session_null=False)
        r = ms.apply(self.sess, "opus-1m-context", "low")
        self.assertEqual(r["result"], ms.SENT, r)
        self.assertEqual((self.tui_()["model"], self.tui_()["effort"]), ("Opus 5.5 (1M context)", "low"))

    def test_confirm_dialog_is_ours_and_answered(self):
        self.set(confirm=True)
        self.assertEqual(ms.apply(self.sess, "sonnet", None)["result"], ms.SENT)
        self.assertEqual(self.tui_()["model"], "Sonnet 5")

    def test_red_lines(self):
        self.set(status="working"); self.assertEqual(ms.apply(self.sess, "fable", "high")["result"], ms.BUSY)
        self.set(status="blocked"); self.assertEqual(ms.apply(self.sess, "fable", "high")["result"], ms.BLOCKED)
        self.set(status="idle", box="  Do you want to proceed?\n  ❯ 1. Yes\n")
        self.assertEqual(ms.apply(self.sess, "fable", "high")["result"], ms.BLOCKED)
        self.set(box="", input="owner draft")
        self.assertEqual(ms.apply(self.sess, "fable", "high")["result"], ms.BUSY)
        self.assertEqual(self.tui_()["model"], "Opus 5.5 (1M context)")
        self.set(input="")
        self.assertEqual(ms.apply(self.sess, "Bad id!", "high")["why"], "not_in_catalogue")
        self.assertEqual(ms.apply(self.sess, "gpt-9", "high")["why"], "row_missing")
        self.assertEqual(ms.apply({"pid": 1, "sessionId": "someone-else"}, "fable", None)["result"], ms.NO_PANE)
        self.assertFalse(any(a[:2] == ["agent", "send-keys"] and a[3:] != ["esc"] for a in self.tui_()["argv"]))

    def test_ids_resolve_and_match(self):
        self.assertEqual(ms.key_of("Opus 5.5 (1M context)"), "opus-5.5-1m-context")
        self.assertEqual(ms.key_of("Fable 5.1"), "fable-5.1")
        self.assertTrue(ms.matches("fable", "fable-5.1")); self.assertFalse(ms.matches("opus", "opus-5.5-1m-context"))
        self.assertTrue(ms.matches("opus-1m-context", "opus-5.5-1m-context")); self.assertFalse(ms.matches("fable-5", "fable-5.1"))
        rows = ["Default (recommended)", "Opus 5.5", "Fable 5.1", "Sonnet 5.5", "Sonnet 5"]
        self.assertEqual(ms.resolve("sonnet", rows), "sonnet-5.5"); self.assertEqual(ms.resolve("Sonnet 5", rows), "sonnet-5")
        self.assertEqual(ms.resolve("default", rows), "default"); self.assertIsNone(ms.resolve("evil; rm -rf", rows))
        self.assertIsNone(ms.resolve("fable ultra", rows))

    def test_claude_2_1_289_layout(self):
        self.set(v289=True, model="Opus 5.5", session_null=False)
        r = ms.apply(self.sess, "fable-5.1", "xhigh")
        self.assertEqual(r["result"], ms.SENT, r)
        self.assertEqual((self.tui_()["model"], self.tui_()["effort"]), ("Fable 5.1", "xhigh"))
        self.assertIn("Sonnet 5.5", r["rows"])
        self.assertEqual(ms.apply(self.sess, "sonnet", None)["why"], "row_missing")   # no version-less row exists here
        self.assertEqual(self.tui_()["mode"], "prompt")                                 # picker closed again (Esc)

    # ---- SharedClaudeAgent: receipts, queue, slash
    def agent(self):
        host = Mock(); host.st.root = self.state; host.stopped.return_value = False
        host.st.write_private.side_effect = lambda p, b: Path(p).write_bytes(b)
        a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": str(self.root), "session_mode": "shared", "_workflow_ceo": True})
        a.session = dict(self.sess); a.state.status = "idle"
        a.MODEL_WATCH = 1.0
        act = patch.object(claude_statusline, "active", return_value=True); act.start(); self.addCleanup(act.stop)
        (self.state / "claude-statusline").mkdir(exist_ok=True)
        (self.state / "claude-statusline" / "sess-jarvis.json").write_text(json.dumps(
            {"session_id": "sess-jarvis", "model_name": "Opus 5.5 (1M context)", "effort": "medium", "cost_usd": 1.5}))
        return a, host

    def test_pill_success_only_on_native_receipt(self):
        a, host = self.agent()
        why = asyncio.run(a.model_set({"model": "fable", "effort": "high"}))
        self.assertIsNone(why)
        self.assertEqual(host.meter_update.call_args.kwargs["model_name"], "Fable 5.1")
        host.set_model.assert_not_called(); host.set_effort.assert_not_called()   # no settings / config write

    def test_pill_unconfirmed_when_status_line_does_not_report(self):
        a, host = self.agent(); self.set(no_statusline=True)
        self.assertEqual(asyncio.run(a.model_set({"model": "fable", "effort": "high"})), "unconfirmed")
        self.assertEqual(host.meter_update.call_args.kwargs["model_name"], "Opus 5.5 (1M context)")   # truth, not the request

    def test_busy_desktop_queues_newest_then_applies_when_idle(self):
        a, host = self.agent(); a.state.status = "working"
        self.assertEqual(asyncio.run(a.model_set({"model": "sonnet", "effort": None})), "queued")
        self.assertEqual(asyncio.run(a.model_set({"model": "fable", "effort": "low"})), "queued")
        self.assertEqual(a.model_pending["model"], "fable")
        self.assertFalse(any(x[:2] == ["agent", "prompt"] for x in self.tui_().get("argv", [])))
        a.state.status = "idle"
        asyncio.run(a._apply_pending())
        self.assertEqual((self.tui_()["model"], self.tui_()["effort"]), ("Fable 5.1", "low"))
        self.assertIsNone(a.model_pending); host.models_changed.assert_called()

    def test_expired_queue_dropped(self):
        a, _ = self.agent(); a.model_pending = {"model": "fable", "effort": None, "at": 0}
        asyncio.run(a._apply_pending())
        self.assertIsNone(a.model_pending); self.assertNotIn("argv", self.tui_())

    def test_slash_model_with_argument_and_cost(self):
        a, _ = self.agent()
        res = asyncio.run(a.command("model", "sonnet high"))
        self.assertEqual(res.kind, "ok", res.text); self.assertIn("Sonnet 5", res.text)
        self.assertEqual(asyncio.run(a.command("model", "evil; rm -rf")).kind, "refused")
        self.assertEqual(asyncio.run(a.command("model", "fable ultra")).kind, "refused")
        cost = asyncio.run(a.command("cost", ""))
        self.assertIn("$0.42", cost.text)

    def test_model_list_and_long_tail_passthrough(self):
        a, _ = self.agent()
        asyncio.run(a.refresh_models())
        ids = [m["id"] for m in a.models_cache]
        self.assertIn("opus-1m-context", ids); self.assertTrue(all(m["efforts"] == list(ms.EFFORTS) for m in a.models_cache))
        self.assertEqual(a.cur_model(), "opus-5.5-1m-context"); self.assertEqual(a.cur_effort(), "medium")
        self.assertTrue(a.passthrough("release-notes")); self.assertTrue(a.passthrough("good-morning"))
        for builtin in ("clear", "compact", "model", "cost", "undo_clear"):
            self.assertFalse(a.passthrough(builtin))

    def test_picker_rows_learned_and_persisted(self):
        a, _ = self.agent()
        asyncio.run(a.model_set({"model": "sonnet", "effort": None}))
        rows = json.loads((self.state / "claude-picker-rows.json").read_text())
        self.assertIn("Sonnet", rows)
        asyncio.run(a.refresh_models())
        self.assertEqual(a.models_cache[0]["id"], "sonnet-5")     # the running model first, by its native name
        self.assertIn("sonnet", [m["id"] for m in a.models_cache])


class StatusLineLayers(Tmp):
    def setUp(self):
        super().setUp()
        self.home = self.root / "home"; (self.home / ".claude").mkdir(parents=True)
        self.work = self.root / "coding"; (self.work / ".claude").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / ".claude")}); env.start(); self.addCleanup(env.stop)
        self.st = self.root / "state"; self.st.mkdir()

    def test_local_older_tap_is_chained_where_it_is_effective(self):
        local = self.work / ".claude/settings.local.json"
        old = {"type": "command", "command": "bash ~/old/statusline_tap.sh"}
        local.write_text(json.dumps({"statusLine": old, "hooks": {}}))
        (self.home / ".claude/settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "user-line"}}))
        self.assertEqual(claude_statusline.effective(self.work)[0], "local")
        claude_statusline.set_enabled(True, self.st, self.work)
        doc = json.loads(local.read_text())
        self.assertIn("claude_statusline.py", doc["statusLine"]["command"])
        meta = claude_statusline.load_meta()
        self.assertEqual(meta["original"], old)                         # the older tap still runs (chained)
        self.assertTrue(claude_statusline.active())
        self.assertEqual(json.loads((self.home / ".claude/settings.json").read_text())["statusLine"]["command"], "user-line")
        claude_statusline.set_enabled(False, self.st, self.work)
        self.assertEqual(json.loads(local.read_text())["statusLine"], old)

    def test_project_layer_chained_from_private_local_layer(self):
        proj = self.work / ".claude/settings.json"
        proj.write_text(json.dumps({"statusLine": {"type": "command", "command": "team-line"}}))
        claude_statusline.set_enabled(True, self.st, self.work)
        self.assertEqual(json.loads(proj.read_text())["statusLine"]["command"], "team-line")   # shared file untouched
        local = json.loads((self.work / ".claude/settings.local.json").read_text())
        self.assertIn("claude_statusline.py", local["statusLine"]["command"])
        self.assertEqual(claude_statusline.load_meta()["original"]["command"], "team-line")
        claude_statusline.set_enabled(False, self.st, self.work)
        self.assertNotIn("statusLine", json.loads((self.work / ".claude/settings.local.json").read_text()))

    def test_user_install_reports_shadowed_when_local_layer_wins(self):
        claude_statusline.set_enabled(True, self.st, self.work)
        self.assertTrue(claude_statusline.active())
        (self.work / ".claude/settings.local.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "x"}}))
        self.assertFalse(claude_statusline.active())                    # data would never arrive: no fake meter
        self.assertIsNone(claude_statusline.read(self.st, "abc")["model"])

    def test_tap_keeps_native_cost(self):
        blob = {"session_id": "abc", "model": {"display_name": "Opus 5.5"}, "cost": {"total_cost_usd": 3.14159}}
        self.assertEqual(claude_statusline.measured(blob)["cost_usd"], 3.1416)
        self.assertIsNone(claude_statusline.measured({"session_id": "abc", "cost": {"total_cost_usd": -1}})["cost_usd"])


class SingleAnswerer(Tmp):
    def test_foreign_permission_hook_makes_agent_j_yield(self):
        work = self.root / "w"; (work / ".claude").mkdir(parents=True)
        home = self.root / "h"; (home / ".claude").mkdir(parents=True)
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home / ".claude")}):
            (work / ".claude/settings.local.json").write_text(json.dumps({"hooks": {"PermissionRequest": [
                {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "python3 relay_hook.py"}]}]}}))
            host = Mock(); host.st.root = self.root; host.stopped.return_value = False
            host.ask = AsyncMock(return_value={"behavior": "allow"})
            a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": str(work), "_workflow_ceo": True})
            a.session = {"sessionId": "owner"}
            ev = lambda tool: asyncio.run(a.hook_event({"hook_event_name": "PermissionRequest", "session_id": "owner",
                                                         "tool_name": tool, "tool_input": {"x": 1}}))
            self.assertEqual(ev("AskUserQuestion"), {})                   # the other handler answers, not both
            host.ask.assert_not_awaited(); host.agent_notice.assert_called_once()
            self.assertEqual(ev("Bash")["hookSpecificOutput"]["decision"]["behavior"], "allow")   # unmatched tools: ours
            self.assertEqual(shared_hook.foreign_answerers(work, "Bash"), [])
            shared_hook.install(work, self.root / "c.sock")                # our own entry is never "foreign"
            self.assertEqual(shared_hook.foreign_answerers(work, "Bash"), [])
            self.assertEqual(shared_hook.foreign_answerers(work, "AskUserQuestion"), ["local"])

    def test_notification_event_installed(self):
        self.assertIn("Notification", shared_hook.EVENTS)


if __name__ == "__main__":
    unittest.main()
