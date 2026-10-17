"""P362: the keys page finds keys this computer already has (agentj/keys_found.py + keys.py op "import").

Fake sources in a temp dir (never the real environment.d, systemd manager or ~/projects/.env): an environment.d file holding a
subscription token, an extra .env file the owner registered (`api_keys.extra_env_files`). The page lists names, source labels
and at most a tail — never a value; dangerous paths (a symlinked file or folder, a group-writable file or folder, another
user's folder, Agent J's state folder) are skipped with one reason and nothing is read. The Claude / Codex rows say which
login is really used (CLAUDE_CODE_OAUTH_TOKEN before ~/.claude/.credentials.json). 「收进 Agent J 管理」 copies the value into
the key file after a Face ID assertion over (NAME, source, keep / comment); the old source is kept or its line commented out.
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from agentj import harness, keys, keys_found, passkey, preferences  # noqa: E402

import test_p352_keys as p352  # noqa: E402
from test_p57_passkey import pk_record  # noqa: E402

OAUTH = "p362-fake-oauth-token-DO-NOT-LEAK-" + "k7Qz"     # ≥ 24: tail k7Qz
ELEVEN = "p362-fake-eleven-value-DO-NOT-LEAK-" + "e1E2"
SHORTV = "p362short"
EXTRA = ("# my keys\n"
         f"ELEVENLABS_API_KEY={ELEVEN}\n"
         f"export MY_TOOL_TOKEN='{SHORTV}'\n"
         "REF_API_KEY=${OTHER}\n"
         "PLAIN=1\n")
SUB = "CLAUDE_CODE_" + "OAUTH_TOKEN"   # assembled: no NAME=value literal for the secret scan
AUTH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CODEX_API_KEY", "OPENAI_API_KEY",
            *keys_found.CLOUD_ENV)


class Base(p352.Base):
    def setUp(self):
        super().setUp()
        for k in AUTH_ENV:                          # the developer's own tokens never decide a test
            os.environ.pop(k, None)
        self.envf.write_text("# Agent J host env\nDEEPSEEK_API_KEY=p362-deepseek-value-DO-NOT-LEAK-dddd\n")
        os.chmod(self.envf, 0o600)
        self.cfg = pathlib.Path(os.path.realpath(self.d / "cfg"))
        os.environ["XDG_CONFIG_HOME"] = str(self.cfg)    # a macOS temp dir sits behind /var → /private/var (a symlink)
        self.envd = self.cfg / "environment.d"
        self.envd.mkdir(parents=True)
        self.oauthf = self.envd / "50-claude-oauth.conf"
        self.oauthf.write_text(f"{SUB}={OAUTH}\n")
        os.chmod(self.oauthf, 0o600)
        self.proj = pathlib.Path(os.path.realpath(self.d / "home")) / "coding"
        self.proj.mkdir()
        self.extra = self.proj / ".env"
        self.extra.write_text(EXTRA)
        os.chmod(self.extra, 0o644)
        self.host.preferences = {"api_keys": {"extra_env_files": [str(self.extra)]}}
        self.id_envd = keys_found._sid("envd", str(self.oauthf))
        self.id_extra = keys_found._sid("file", str(self.extra))

    def leak_check(self, blob):
        for v in (OAUTH, ELEVEN, SHORTV):
            self.assertNotIn(v, blob)

    def row(self, page, name, fp=False):
        """The row of NAME; its found copies without the per-page value fingerprint unless `fp`."""
        for r in [*(r for v in page["base"] for r in v["rows"]), *page["third"]]:
            if r["name"] == name:
                r = json.loads(json.dumps(r))                      # never change the page itself
                if not fp and "found" in r:
                    for f in r["found"]:
                        self.assertTrue(r["set"] or len(f.pop("fp")) == 16, "an importable copy carries its fingerprint")
                return r
        return None

    def fp(self, page, name, src):
        return next(f["fp"] for f in self.row(page, name, fp=True)["found"] if f["src"] == src)

    def oauth(self, page, which):
        return [r for v in page["base"] for r in v["rows"] if r["type"] == "oauth" and r["name"] == which][0]


class Digest(unittest.TestCase):
    def test_vectors_shared_with_the_phone(self):          # the same hex is asserted in web/test/p362.test.mjs
        fp = "0123456789abcdef"
        self.assertEqual(keys.digest("import", "CLAUDE_CODE_OAUTH_TOKEN", keys.import_extra("s0123456789abcdef", True, fp)),
                         "5bf1c2379b2969133661a8e67816d3f04538e380219367636051a8b94cba0212")
        self.assertEqual(keys.digest("import", "CLAUDE_CODE_OAUTH_TOKEN", keys.import_extra("s0123456789abcdef", False, fp)),
                         "0b81b419b49fa8b4f050dd38ef7ed662918a2b38690957aa2c7d218577e508a8")
        self.assertEqual(keys.digest("copy", "OPENAI_API_KEY"),
                         "1cb0bc7cf7fa3363d9e82d9ff3ce15f43ac543493a4b748b524d6bbb7f08f7a2", "P352 digests unchanged")


class Listing(Base):
    def test_found_names_sources_tails_never_values(self):
        page = self.get()
        src = {s["id"]: s for s in page["src"]}
        self.assertEqual(src[self.id_envd], {"id": self.id_envd, "label": str(self.oauthf), "kind": "envd", "count": 1})
        self.assertEqual((src[self.id_extra]["kind"], src[self.id_extra]["count"]), ("file", 3))
        sub = self.row(page, "CLAUDE_CODE_OAUTH_TOKEN")
        self.assertEqual((sub["set"], sub["found"]), (False, [{"src": self.id_envd, "tail": "k7Qz"}]))
        el = self.row(page, "ELEVENLABS_API_KEY")
        self.assertEqual((el["set"], el["found"]), (False, [{"src": self.id_extra, "tail": "e1E2"}]))
        self.assertEqual(self.row(page, "MY_TOOL_TOKEN")["found"], [{"src": self.id_extra}], "short value: no tail")
        self.assertIsNone(self.row(page, "REF_API_KEY"), "a $reference is never offered")
        self.assertIsNone(self.row(page, "PLAIN"), "not secret-looking")
        self.assertNotIn("found", self.row(page, "DEEPSEEK_API_KEY"))
        self.leak_check(json.dumps(page))

    def test_same_and_different_copies(self):
        with open(self.envf, "a") as f:
            f.write(f"ELEVENLABS_API_KEY={ELEVEN}\nMY_TOOL_TOKEN=p362-other\n")
        page = self.get()
        self.assertEqual(self.row(page, "ELEVENLABS_API_KEY")["found"], [{"src": self.id_extra, "tail": "e1E2", "same": True}])
        self.assertEqual(self.row(page, "MY_TOOL_TOKEN")["found"], [{"src": self.id_extra, "same": False}])

    def test_no_extra_files_by_default(self):
        self.host.preferences = preferences.defaults()
        page = self.get()
        self.assertEqual([s["kind"] for s in page["src"]], ["envd"])
        self.assertIsNone(self.row(page, "ELEVENLABS_API_KEY"))
        self.assertEqual(preferences.get(preferences.defaults(), "api_keys.extra_env_files"), [])

    def test_systemd_manager_only_unexplained_names(self):
        with mock.patch.object(keys_found, "manager_env", lambda: {SUB: OAUTH,
                                                                   "GITHUB_TOKEN": "p362-gh-value-DO-NOT-LEAK-ghgh"}):
            page = self.get()
        sysd = [s for s in page["src"] if s["kind"] == "systemd"]
        self.assertEqual(sysd, [{"id": "systemd", "label": "systemd", "kind": "systemd", "count": 1}])
        self.assertEqual(self.row(page, "GITHUB_TOKEN")["found"], [{"src": "systemd", "tail": "ghgh"}])
        self.assertEqual(self.row(page, "CLAUDE_CODE_OAUTH_TOKEN")["found"], [{"src": self.id_envd, "tail": "k7Qz"}])

    def test_manager_env_off_under_tests(self):
        self.assertEqual(keys_found.manager_env(), {}, "a test never reaches the real systemd user manager")

    def test_a_failing_scan_keeps_the_page(self):
        with mock.patch.object(keys_found, "sources", mock.Mock(side_effect=RuntimeError("boom"))):
            page = self.get()
        self.assertEqual((page["src"], self.row(page, "DEEPSEEK_API_KEY")["set"]), ([], True))


class ActiveLogin(Base):
    def test_claude_env_token_beats_credentials_file(self):
        with mock.patch.dict(os.environ, {SUB: OAUTH}):
            self.host.agent_cfg = {"kind": "claude", "dir": str(self.d)}
            page = self.get()
        o = self.oauth(page, "claude")
        self.assertEqual((o["state"], o["use"], o["use_name"], o["use_src"]), ("in", "sub", "CLAUDE_CODE_OAUTH_TOKEN", self.id_envd))
        self.assertNotIn("pending", o)
        sub = self.row(page, "CLAUDE_CODE_OAUTH_TOKEN")
        self.assertEqual(sub["found"], [{"src": self.id_envd, "tail": "k7Qz", "live": True}])
        self.assertTrue(sub["in_use"])
        self.leak_check(json.dumps(page))

    def test_claude_login_when_no_env(self):
        o = self.oauth(self.get(), "claude")
        self.assertEqual((o["use"], o.get("use_name")), ("login", None), "credentials file (stub) only")
        with mock.patch.object(harness, "claude_login", lambda environ=None: ("warn", "no login found")):
            self.assertEqual(self.oauth(self.get(), "claude")["use"], "none")

    def test_claude_order(self):
        cases = [({SUB: "x", "ANTHROPIC_API_KEY": "y"}, ("key", "ANTHROPIC_API_KEY")),
                 ({"ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_API_KEY": "y"}, ("key", "ANTHROPIC_AUTH_TOKEN")),
                 ({"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_API_KEY": "y"}, ("cloud", "CLAUDE_CODE_USE_BEDROCK")),
                 ({SUB: "x"}, ("sub", SUB)),
                 ({}, ("login", None))]
        for env, want in cases:
            self.assertEqual(keys_found.claude_active({"HOME": os.environ["HOME"], **env}), want, env)

    def test_key_file_pending_until_restart(self):
        with open(self.envf, "a") as f:
            f.write(f"{SUB}={OAUTH}\n")
        o = self.oauth(self.get(), "claude")
        self.assertEqual((o["use"], o["use_src"], o.get("pending")), ("sub", "agentj", True))

    def test_codex(self):
        with mock.patch.dict(os.environ, {"CODEX_API_KEY": "p362-codex"}):
            self.assertEqual(self.oauth(self.get(), "codex")["use"], "key")
        with mock.patch.object(harness, "codex_login", lambda: ("ok", "login ~/.codex/auth.json")):
            self.assertEqual(self.oauth(self.get(), "codex")["use"], "login")
        self.assertEqual(self.oauth(self.get(), "codex")["use"], "none")


class Dangerous(Base):
    def why(self, page, sid):
        return next(s for s in page["src"] if s["id"] == sid).get("why")

    def test_symlinked_file_and_folder(self):
        real = self.proj / "real.env"
        real.write_text(EXTRA)
        os.chmod(real, 0o600)
        link = self.proj / "link.env"
        link.symlink_to(real)
        ldir = self.proj.parent / "linkdir"
        ldir.symlink_to(self.proj)
        via = ldir / "real.env"
        self.host.preferences = {"api_keys": {"extra_env_files": [str(link), str(via)]}}
        page = self.get()
        self.assertEqual(self.why(page, keys_found._sid("file", str(link))), "symlink")
        self.assertEqual(self.why(page, keys_found._sid("file", str(via))), "symlink")
        self.assertIsNone(self.row(page, "ELEVENLABS_API_KEY"), "nothing read through a symlink")

    def test_group_writable_file_and_folder(self):
        os.chmod(self.extra, 0o664)
        page = self.get()
        self.assertEqual(self.why(page, self.id_extra), "unsafe_permissions")
        os.chmod(self.extra, 0o600)
        os.chmod(self.proj, 0o775)
        try:
            self.assertEqual(self.why(self.get(), self.id_extra), "unsafe_parent")
        finally:
            os.chmod(self.proj, 0o700)

    def test_other_users_folder(self):
        real = os.getuid()
        with mock.patch.object(keys_found.os, "getuid", lambda: real + 4242):
            with self.assertRaises(keys_found.Unsafe) as e:
                keys_found.read_file(str(self.extra))
        self.assertEqual(e.exception.why, "foreign_owner")

    def test_state_dir_and_relative_refused(self):
        inside = pathlib.Path(os.path.realpath(self.st.root)) / "x.env"
        inside.write_text(EXTRA)
        self.host.preferences = {"api_keys": {"extra_env_files": [str(inside), "rel/.env"]}}
        page = self.get()
        whys = sorted(s.get("why") for s in page["src"] if s["kind"] == "file")
        self.assertEqual(whys, ["not_absolute", "state_dir"])
        self.assertIsNone(self.row(page, "ELEVENLABS_API_KEY"))

    def test_symlinked_environment_d(self):
        self.oauthf.unlink()
        self.envd.rmdir()
        other = self.cfg / "elsewhere"
        other.mkdir()
        (other / "x.conf").write_text(f"{SUB}={OAUTH}\n")
        self.envd.symlink_to(other)
        page = self.get()
        self.assertEqual([s.get("why") for s in page["src"] if s["kind"] == "envd"], ["symlink"])
        self.assertNotIn("found", self.row(page, "CLAUDE_CODE_OAUTH_TOKEN"))

    def test_tty_and_system_dirs_never_opened(self):
        self.host.preferences = {"api_keys": {"extra_env_files": ["/dev/tty", "/proc/self/environ", "/sys/kernel/x"]}}
        page = self.get()
        self.assertEqual(sorted(s.get("why") for s in page["src"] if s["kind"] == "file"), ["system_dir"] * 3)

    def test_a_pty_never_becomes_the_hosts_terminal(self):
        if not hasattr(os, "openpty") or not os.path.isdir("/proc/self"):
            self.skipTest("Linux pty + /proc only")
        import subprocess
        import textwrap
        m, sl = os.openpty()
        try:
            child = textwrap.dedent(f"""
                import os, sys
                sys.path.insert(0, {str(pathlib.Path(keys_found.__file__).parents[1])!r})
                from agentj import keys_found as kf
                os.setsid()
                p = os.ttyname({sl})
                d = kf.open_dir(os.path.dirname(p))
                try:
                    kf._open_file(d, os.path.basename(p))
                except kf.Unsafe as e:
                    print(e.why)
                print(open("/proc/self/stat").read().rsplit(")", 1)[1].split()[4])
            """)
            r = subprocess.run([sys.executable, "-c", child], pass_fds=(sl,), capture_output=True, text=True, timeout=30)
            self.assertEqual(r.stdout.split(), ["not_regular", "0"], r.stderr)
        finally:
            os.close(m)
            os.close(sl)

    def test_fifo_and_hard_link(self):
        fifo = self.proj / "pipe.env"
        os.mkfifo(fifo, 0o600)
        hard = self.proj / "hard.env"
        os.link(self.extra, hard)
        self.host.preferences = {"api_keys": {"extra_env_files": [str(fifo), str(hard)]}}
        page = self.get()                                         # a FIFO must not block the listing
        self.assertEqual(self.why(page, keys_found._sid("file", str(fifo))), "not_regular")
        self.assertEqual(self.why(page, keys_found._sid("file", str(hard))), "hard_link")

    def test_settings_json_only_key_keeps_the_page(self):
        cl = pathlib.Path(os.environ["HOME"]) / ".claude"
        cl.mkdir()
        (cl / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "p362-settings-DO-NOT-LEAK"}}))
        os.chmod(cl / "settings.json", 0o600)
        with open(self.extra, "a") as f:
            f.write("ANTHROPIC_API_KEY=p362-other-DO-NOT-LEAK\n")
        o = self.oauth(self.get(), "claude")
        self.assertEqual((o["use"], o["use_name"]), ("key", "ANTHROPIC_API_KEY"))
        self.assertNotIn("use_src", o)

    def test_config_validation(self):
        ok = preferences.validate({"api_keys": {"extra_env_files": ["~/projects/.env", "/opt/x.env"]}})
        self.assertEqual(preferences.get(ok, "api_keys.extra_env_files"), ["~/projects/.env", "/opt/x.env"])
        for bad in (["rel/.env"], ["https://x/.env"], ["/a\n"], ["/x"] * 9, [3]):
            with self.assertRaises(preferences.ConfigError):
                preferences.validate({"api_keys": {"extra_env_files": bad}})


class Import(Base):
    def setUp(self):
        super().setUp()
        self.st.set_passkey(self.ph.pub, pk_record(self.a))

    def fa_imp(self, page, name, src, keep, n=None, fp=None):
        fp = fp or self.fp(page, name, src)
        ch = passkey.elevate_challenge(self.ph.channel, self.ph.did, page["id"], "key_import", n or page["n"],
                                       keys.digest("import", name, keys.import_extra(src, keep, fp)))
        x = self.a.assert_(ch, b"\0", nonce=b"\0")
        return {k: x[k] for k in ("id", "cd", "ad", "sig")}

    def imp(self, page, name, src, keep, fa=None, n=None, fp=None):
        try:
            fp = fp or self.fp(page, name, src)
        except (StopIteration, TypeError, KeyError):
            fp = "0" * 16
        self.app({"t": "keys_op", "r": "r2", "op": "import", "name": name, "id": page["id"], "n": n or page["n"], "src": src,
                  "keep": keep, "fp": fp, "fa": fa if fa is not None else self.fa_imp(page, name, src, keep, n, fp)})
        return self.of("keys_res")[-1]

    def blob(self):
        return self.audit_text() + (self.st.log_path.read_text() if self.st.log_path.exists() else "")

    def test_import_keep(self):
        page = self.get()
        res = self.imp(page, "ELEVENLABS_API_KEY", self.id_extra, True)
        self.assertEqual((res["ok"], res["op"], res["old"]), (True, "import", "kept"))
        self.assertNotIn("value", res)
        self.assertIn(f"ELEVENLABS_API_KEY={ELEVEN}\n", self.envf.read_text())
        self.assertEqual(stat.S_IMODE(self.envf.stat().st_mode), 0o600)
        self.assertEqual(self.extra.read_text(), EXTRA, "the old source kept byte for byte")
        rec = [json.loads(x) for x in self.audit_text().splitlines()][-1]
        self.assertEqual((rec["op"], rec["name"], rec["result"], rec["src"], rec["old"]),
                         ("import", "ELEVENLABS_API_KEY", "imported", keys_found._tilde(str(self.extra)), "kept"))
        self.leak_check(self.blob())
        page = self.get()
        r = self.row(page, "ELEVENLABS_API_KEY")
        self.assertEqual((r["set"], r["tail"], r["found"]), (True, "e1E2", [{"src": self.id_extra, "tail": "e1E2", "same": True}]))
        res = self.imp(page, "ELEVENLABS_API_KEY", self.id_extra, True)
        self.assertEqual((res["ok"], res["why"]), (False, "exists"), "never overwrites the managed value")

    def test_import_comment_out(self):
        page = self.get()
        res = self.imp(page, "MY_TOOL_TOKEN", self.id_extra, False)
        self.assertEqual((res["ok"], res["old"]), (True, "commented"))
        self.assertIn(f"MY_TOOL_TOKEN={SHORTV}\n", self.envf.read_text())
        self.assertEqual(self.extra.read_text(), EXTRA.replace("export MY_TOOL_TOKEN", "# export MY_TOOL_TOKEN"))
        self.assertEqual(stat.S_IMODE(self.extra.stat().st_mode), 0o644, "the source keeps its mode")
        self.leak_check(self.blob())

    def test_import_subscription_token_from_environment_d(self):
        page = self.get()
        res = self.imp(page, "CLAUDE_CODE_OAUTH_TOKEN", self.id_envd, True)
        self.assertTrue(res["ok"])
        self.assertEqual(self.oauthf.read_text(), f"{SUB}={OAUTH}\n")
        o = self.oauth(self.get(), "claude")
        self.assertEqual((o["use"], o["use_src"]), ("sub", "agentj"), "agentj.env is the one that counts now")

    def test_face_id_binds_source_and_choice(self):
        page = self.get()
        wrong = self.fa_imp(page, "ELEVENLABS_API_KEY", self.id_extra, True)     # signed "keep", sent "comment"
        res = self.imp(page, "ELEVENLABS_API_KEY", self.id_extra, False, fa=wrong)
        self.assertEqual((res["ok"], res["why"]), (False, "passkey"))
        fp = self.fp(page, "ELEVENLABS_API_KEY", self.id_extra)
        wrong = self.fa_imp(page, "ELEVENLABS_API_KEY", self.id_envd, True, n=res["n"], fp=fp)   # signed another source
        res = self.imp(page, "ELEVENLABS_API_KEY", self.id_extra, True, fa=wrong, n=res["n"])
        self.assertEqual((res["ok"], res["why"]), (False, "passkey"))
        self.assertEqual(self.extra.read_text(), EXTRA)
        self.assertNotIn("ELEVENLABS", self.envf.read_text())

    def test_shapes_and_gone(self):
        page = self.get()
        for name, src, keep, fp in (("ELEVENLABS_API_KEY", "../etc", True, "0" * 16), ("ELEVENLABS_API_KEY", self.id_extra, "yes", "0" * 16),
                                    ("ELEVENLABS_API_KEY", None, True, "0" * 16), ("ELEVENLABS_API_KEY", self.id_extra, True, "xyz"),
                                    ("LD_PRELOAD", self.id_extra, True, "0" * 16), ("AGENTJ_SERVICE_TOKEN", self.id_extra, True, "0" * 16)):
            self.app({"t": "keys_op", "r": "r2", "op": "import", "name": name, "id": page["id"], "n": page["n"],
                      "src": src, "keep": keep, "fp": fp, "fa": {}})
            self.assertEqual(self.of("keys_res")[-1]["why"], "shape", name)
        self.assertEqual(keys.do_import(self.host, "LD_PRELOAD", self.id_extra, True)[1], "shape", "the host refuses it too")
        res = self.imp(page, "ELEVENLABS_API_KEY", "s" + "0" * 16, True, n=self.of("keys_res")[-1]["n"])   # a shape reply re-arms
        self.assertEqual((res["ok"], res["why"]), (False, "gone"))
        res = self.imp(page, "NOT_THERE_KEY", self.id_extra, True, n=res["n"])
        self.assertEqual((res["ok"], res["why"]), (False, "missing"))

    def test_source_turned_symlink_before_import(self):
        page = self.get()
        real = self.proj / "moved.env"
        self.extra.rename(real)
        self.extra.symlink_to(real)
        res = self.imp(page, "ELEVENLABS_API_KEY", self.id_extra, True)
        self.assertEqual((res["ok"], res["why"]), (False, "gone"))
        self.assertNotIn("ELEVENLABS", self.envf.read_text())

    def test_value_changed_after_listing_refused(self):
        page = self.get()
        self.extra.write_text(EXTRA.replace(ELEVEN, "p362-swapped-by-someone-DO-NOT-LEAK-zzzz"))
        os.chmod(self.extra, 0o644)
        res = self.imp(page, "ELEVENLABS_API_KEY", self.id_extra, True)
        self.assertEqual((res["ok"], res["why"]), (False, "changed"))
        self.assertNotIn("ELEVENLABS", self.envf.read_text())

    def test_fingerprint_is_per_page(self):
        a, b = self.get(), self.get()
        self.assertNotEqual(self.fp(a, "ELEVENLABS_API_KEY", self.id_extra), self.fp(b, "ELEVENLABS_API_KEY", self.id_extra))
        res = self.imp(b, "ELEVENLABS_API_KEY", self.id_extra, True, fp=self.fp(a, "ELEVENLABS_API_KEY", self.id_extra))
        self.assertEqual((res["ok"], res["why"]), (False, "changed"), "another page's fingerprint")

    def test_systemd_cannot_be_commented(self):
        with mock.patch.object(keys_found, "manager_env", lambda: {"GITHUB_TOKEN": "p362-gh-value-DO-NOT-LEAK-ghgh"}):
            page = self.get()
            res = self.imp(page, "GITHUB_TOKEN", "systemd", False)
            self.assertEqual((res["ok"], res["why"]), (False, "shape"))
            res = self.imp(page, "GITHUB_TOKEN", "systemd", True, n=res["n"])
            self.assertTrue(res["ok"])
        self.assertIn("GITHUB_TOKEN=p362-gh-value-DO-NOT-LEAK-ghgh\n", self.envf.read_text())

    def test_without_passkey_refused(self):
        other = p352.Phone(self.st, name="平板", with_key=False)
        s2 = p352._ready(self.host, other, cid=12)
        s2.p33 = True
        import asyncio
        asyncio.run(self.host._app(s2, {"t": "keys_get", "r": "r1"}))
        page = self.of("keys")[-1]
        asyncio.run(self.host._app(s2, {"t": "keys_op", "r": "r2", "op": "import", "name": "ELEVENLABS_API_KEY", "id": page["id"],
                                        "n": page["n"], "src": self.id_extra, "keep": True, "fp": "0" * 16}))
        self.assertEqual(self.of("keys_res")[-1]["why"], "passkey")
        self.assertNotIn("ELEVENLABS", self.envf.read_text())


if __name__ == "__main__":
    unittest.main()
