"""P174 (Leo 10-12): a new install runs an independent session by default; shared is the owner's opt-in.
Installs from before P174 (shared factory default) are never moved: migration 202610120174 writes an explicit "shared",
and until it has run the runtime keeps them on shared. Explicit values (either way) and an owner's recorded choice are
kept byte for byte. Every test uses a throw-away HOME / XDG_CONFIG_HOME / CLAUDE_CONFIG_DIR / state dir.
"""
import _hermetic  # noqa: F401,I001
import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE.parent))
from agentj import cli, config_migrations, doctor, preferences, session_choice  # noqa: E402
from agentj.state import State  # noqa: E402

M = session_choice.LEGACY_MIGRATION
OLD = ("202610040001", "202610050047")


def _approve_preferences(test, st, raw):
    """Actual config controller and device UV; deliberately not a CLI transport mock."""
    import asyncio
    from _p541_device import Phone, _owner_host
    from test_l1 import _ready
    async def apply():
        sent, replies = [], []
        host = _owner_host(st, sent)
        phone = Phone(st, with_passkey=True)
        session = _ready(host, phone)
        host.ask_ttl = 2
        class Writer:
            def write(self, data): replies.append(json.loads(data))
            async def drain(self): pass
            def is_closing(self): return False
        before = preferences.path().read_bytes()
        try:
            await host._ctl_config_from_agent(Writer(), raw)
            if replies[-1].get('ok') is True:
                return
            test.assertEqual(replies[-1]['code'], 'owner_approval')
            for _ in range(200):
                if host.asks: break
                await asyncio.sleep(.005)
            ask = next(iter(host.asks.values()))
            test.assertTrue(ask.require_passkey)
            answer = phone.answer(host._ask_msg(ask, session), True)
            unsigned_uv = dict(answer); unsigned_uv.pop('fa')
            await host._answer(session, unsigned_uv)
            test.assertFalse(ask.fut.done())
            test.assertEqual(preferences.path().read_bytes(), before)
            await host._answer(session, answer)
            await asyncio.wait_for(asyncio.gather(*list(host._ctl_bg)), 3)
            test.assertEqual(preferences.validate(preferences.read()[1]), preferences.validate(preferences.parse(raw)))
        finally:
            for ask in list(host.asks.values()):
                if not ask.fut.done(): ask.fut.set_result(('deny',None,None,None))
            await asyncio.gather(*list(getattr(host,'_ctl_bg',())), return_exceptions=True)
            host.elevate.cancel_all('stopped')
            await asyncio.gather(*list(host.elevate.review_requests), return_exceptions=True)
    asyncio.run(apply())


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p174-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"),
                                           "CLAUDE_CONFIG_DIR": str(self.tmp / "cc"), "AGENTJ_STATE_DIR": str(self.tmp / "st"),
                                           "AGENTJ_SKILL_LINK": "off"})
        env.start()
        self.addCleanup(env.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")

    def approve_mode(self, value):
        if not self.st.config().get("agent"):
            self.st.set_agent_config("claude", str(self.work))
        before = self.st.agent_config()["session_mode"]
        raw = preferences.edit(preferences.read()[0], "agent.session_mode", value, remove=value is None)
        with mock.patch("agentj.claude_auth.available", return_value=True):
            _approve_preferences(self, self.st, raw)
        return session_choice.explain(value or "independent", before, "zh")

    # an install as an earlier version left it
    def old_install(self, config_text=None, markers=OLD, log_modes=(), agent=True):
        if agent:
            self.st.set_agent_config("claude", str(self.work))
        preferences.ensure()
        if config_text is not None:
            preferences.path().write_text(config_text)
        if markers is not None:
            d = self.st.root / "config-migrations"
            d.mkdir(exist_ok=True)
            for m in markers:
                (d / (m + ".json")).write_text(json.dumps({"id": m, "version": 1}))
        for mode in log_modes:
            with open(self.st.log_path, "a") as f:
                f.write(json.dumps({"ts": 1, "ev": "agent_on", "agent": "claude", "isolation_requested": True,
                                    "session_mode": mode, "phase": "configured"}) + "\n")

    def migrate(self):
        return config_migrations.run(self.st)

    def file_mode(self):
        return preferences.get(preferences.read()[1], "agent.session_mode")

    def session_rows(self):
        return [r for r in doctor.run(offline=True) if r["id"] == "session"]


class NewInstall(_Env):
    def test_fresh_init_is_independent_and_writes_nothing(self):
        r = self.migrate()                               # what `agentj init` runs
        self.assertIn(M, r["applied"])
        self.assertIsNone(self.file_mode(), "the factory default is not written into the owner's file")
        self.st.set_agent_config("claude", str(self.work))
        self.assertEqual(self.st.agent_config()["session_mode"], "independent")
        self.assertFalse(session_choice.legacy_shared(self.st))
        self.assertFalse(self.session_rows())

    def test_every_harness_defaults_to_independent(self):
        self.migrate()
        for kind in ("claude", "codex", "opencode"):
            self.st.set_agent_config(kind, str(self.work))
            self.assertEqual(self.st.agent_config()["session_mode"], "independent", kind)

    def test_independent_never_touches_the_owners_claude_settings(self):
        from agentj import claude_inbound, claude_statusline
        self.migrate()
        self.st.set_agent_config("claude", str(self.work))
        self.assertFalse(claude_inbound.ensure_shared_default(self.st))
        self.assertFalse(claude_statusline.ensure_default(self.st))
        self.assertFalse((self.tmp / "cc" / "settings.json").exists(), "no ingress / status-line write in independent mode")
        self.assertIsNone(doctor.check_shared_inbound(self.st))
        self.assertIsNone(doctor.check_shared_statusline(self.st))
        self.assertIsNone(doctor.check_shared_hooks(self.st))


class Upgrade(_Env):
    def test_implicit_shared_install_is_pinned_to_shared(self):
        raw = "// owner comment kept\n{version: 1, appearance: {theme: 'dark'}}\n"
        self.old_install(raw)
        self.assertEqual(self.st.agent_config()["session_mode"], "shared", "before the migration: still shared")
        rows = self.session_rows()
        self.assertEqual([r["status"] for r in rows], [doctor.WARN], "doctor names the pending write")
        r = self.migrate()
        self.assertEqual(r["applied"], [M])
        self.assertEqual(self.file_mode(), "shared", "written down explicitly")
        text = preferences.path().read_text()
        self.assertIn("// owner comment kept", text)
        self.assertEqual(preferences.get(preferences.read()[1], "appearance.theme"), "dark")
        self.assertEqual(self.st.agent_config()["session_mode"], "shared")
        self.assertEqual(session_choice.recorded(self.st), "shared")
        self.assertFalse(session_choice.legacy_shared(self.st))
        self.assertFalse(self.session_rows())
        before = preferences.path().read_bytes()
        self.assertEqual(self.migrate()["applied"], [], "idempotent")
        self.assertEqual(preferences.path().read_bytes(), before)

    def test_explicit_values_are_kept_byte_for_byte(self):
        for value in ("shared", "independent"):
            with self.subTest(value=value):
                shutil.rmtree(self.st.root / "config-migrations", True)
                raw = '{version: 1, agent: {session_mode: "%s"}}\n' % value
                self.old_install(raw)
                self.migrate()
                self.assertEqual(preferences.path().read_text(), raw)
                self.assertEqual(self.st.agent_config()["session_mode"], value)

    def test_last_agent_start_decides_when_logged(self):
        self.old_install(log_modes=("shared", "independent"))       # ran independent last (e.g. an older default)
        self.assertEqual(self.st.agent_config()["session_mode"], "independent")
        self.migrate()
        self.assertIsNone(self.file_mode())
        shutil.rmtree(self.st.root / "config-migrations")
        self.st.log_path.unlink()
        self.old_install(markers=(), log_modes=("independent", "shared"))
        self.assertEqual(self.st.agent_config()["session_mode"], "shared")
        self.migrate()
        self.assertEqual(self.file_mode(), "shared")

    def test_recorded_independent_choice_is_not_overridden(self):
        self.old_install()
        session_choice.record(self.st, "independent", "config")
        self.assertEqual(self.st.agent_config()["session_mode"], "independent")
        self.migrate()
        self.assertIsNone(self.file_mode())

    def test_pre_migration_install_had_the_independent_default(self):
        self.old_install(markers=None)                              # ≤0.11: no config migrations, no log
        self.assertEqual(self.st.agent_config()["session_mode"], "independent")
        self.migrate()
        self.assertIsNone(self.file_mode())

    def test_no_agent_selected_follows_the_new_default(self):
        self.old_install(agent=False)
        self.migrate()
        self.assertIsNone(self.file_mode())

    def test_failed_migration_still_keeps_shared(self):
        self.old_install()
        with mock.patch.object(preferences, "commit_files", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            self.migrate()
        self.assertFalse((self.st.root / "config-migrations" / (M + ".json")).exists())
        self.assertEqual(self.st.agent_config()["session_mode"], "shared", "the runtime guard holds until it succeeds")


class Switching(_Env):
    def agent(self, *args):
        out = io.StringIO()
        with mock.patch("agentj.claude_auth.available", return_value=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            try:
                cli.main(["agent", *args])
            except SystemExit as e:
                self.assertIn(e.code, (0, None), out.getvalue())
        return out.getvalue()

    def config(self, *args):
        out = io.StringIO()
        with mock.patch("agentj.claude_auth.available", return_value=True), contextlib.redirect_stdout(out):
            code = preferences.command(list(args))
        return code, json.loads(out.getvalue())

    def test_config_set_explains_and_names_the_undo(self):
        self.migrate()
        self.st.set_agent_config("claude", str(self.work))
        for value, old in (("shared","independent"),("independent","shared")):
            before = preferences.path().read_bytes()
            code, res = self.config("set", "agent.session_mode", value)
            self.assertEqual(code, 3, res)
            self.assertEqual(preferences.path().read_bytes(), before)
            self.assertIn("fresh paired-device UV", res["error"])
            notice = self.approve_mode(value)
            self.assertEqual(notice["rollback"], "agentj config set agent.session_mode " + old)
            self.assertEqual(notice["restart"], "agentj service restart")
            self.assertIn("advanced" if value=="shared" else "Independent", notice["text_en"])
            self.assertEqual(self.st.agent_config()["session_mode"], value)
        code, res = self.config("set", "agent.session_mode", "independent")
        self.assertEqual(code, 0, res)
        self.assertNotIn("notice", res, "no change, nothing to explain")

    def test_alias_and_bad_value(self):
        self.migrate()
        code, res = self.config("set", "会话模式", "shared")
        self.assertEqual((code, res["key"]), (3, "agent.session_mode"))
        self.assertIsNone(self.file_mode())
        self.approve_mode("shared")
        code, res = self.config("set", "agent.session_mode", "attached")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.file_mode(), "shared", "a rejected value changes nothing")

    def test_agent_flags_print_the_meaning_once(self):
        self.migrate()
        self.st.set_agent_config("claude", str(self.work))
        # A protected mode switch now requires owner UV even for a CLI flag.
        for value in ("shared", "independent"):
            before = preferences.path().read_bytes()
            out = io.StringIO()
            with mock.patch("agentj.claude_auth.available", return_value=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                with self.assertRaises(SystemExit) as refused:
                    cli.main(["agent", "claude", "--dir", str(self.work), "--"+value])
            self.assertIn("fresh paired-device UV", str(refused.exception.code)+out.getvalue())
            self.assertEqual(preferences.path().read_bytes(), before)
            notice = self.approve_mode(value)
            self.assertIn("agentj config set agent.session_mode", notice["text"])
            text = self.agent("claude", "--dir", str(self.work), "--"+value)
            self.assertNotIn("agentj config set agent.session_mode", text, "already selected: no repeated notice")
        self.assertEqual(self.file_mode(), "independent")

    def test_migrated_owner_can_switch_and_back(self):
        self.old_install()
        self.migrate()
        code, res = self.config("unset", "agent.session_mode")
        self.assertEqual(code, 3, res)
        self.assertEqual(self.st.agent_config()["session_mode"], "shared")
        notice = self.approve_mode(None)
        self.assertEqual(self.st.agent_config()["session_mode"], "independent", "unset = the new default, no legacy guard")
        self.assertEqual(notice["rollback"], "agentj config set agent.session_mode shared")
        code, res = self.config("set", "agent.session_mode", "shared")
        self.assertEqual(code, 3, res)
        self.approve_mode("shared")
        self.assertEqual(self.st.agent_config()["session_mode"], "shared")


if __name__ == "__main__":
    unittest.main()
