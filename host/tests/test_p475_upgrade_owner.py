"""#475 paid ticket (macOS, 0.25.6a1 → 0.26.8a1): ① the uv tool folder belonged to root → read_only with no hint;
② after the owner fixed it, download_failed — no retry, nothing reached support.

Mac-shaped and fake: a /Users/<name> home under TEST_TMP with uv's real layout (~/.local/share/uv/tools/agentj,
~/.local/bin/agentj → the venv) and an lstat seam that reports root (uid 0) for the uv tree and the entry point. No real
sudo, no chown, no network, no installer: fake opener, fake support channel, fake Jev verdict."""
import _hermetic  # noqa: F401,I001
import contextlib
import errno
import io
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

from agentj import auto_update, cli, doctor, privacy, support, update, upgrade_diag, upgrade_owner
from agentj.state import State

NEW = "9.9.9"
ME = os.getuid()
USER = "catherine"


class _Mac(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p475-", dir=os.environ.get("TEST_TMP"))
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(os.path.realpath(self.tmp.name))
        self.home = root / "Users" / USER
        self.uv = self.home / ".local/share/uv"
        self.tool = self.uv / "tools/agentj"
        (self.tool / "bin").mkdir(parents=True)
        (self.tool / "lib/python3.12/site-packages/agentj").mkdir(parents=True)
        (self.tool / "uv-receipt.toml").write_text('[tool]\nrequirements = [{ name = "agentj" }]\n')
        (self.tool / "bin/agentj").write_text("#!/bin/sh\n")
        (self.home / ".local/bin").mkdir(parents=True)
        self.entry = self.home / ".local/bin/agentj"
        self.entry.symlink_to(self.tool / "bin/agentj")
        self.root_owned = {str(self.uv), str(self.entry)}      # what `sudo uv tool install` leaves behind (and below)
        self.root_exact = set()                                   # root owns this folder only (e.g. /Users itself)
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "AGENTJ_STATE_DIR": str(self.home / ".agentj-state"), "XDG_CACHE_HOME": str(self.home / ".cache"),
               upgrade_diag.REPORT_ENV: ""}
        for k in ("UV_TOOL_BIN_DIR", "XDG_BIN_HOME", "UV_CACHE_DIR", "UV_TOOL_DIR", "XDG_DATA_HOME"):
            env[k] = ""
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ("UV_TOOL_BIN_DIR", "XDG_BIN_HOME", "UV_CACHE_DIR", "UV_TOOL_DIR", "XDG_DATA_HOME"):
            os.environ.pop(k, None)
        p = mock.patch.object(upgrade_owner, "LSTAT", self.lstat)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(upgrade_owner, "_user", return_value=USER)
        p.start()
        self.addCleanup(p.stop)
        self.pw_home = str(self.home)        # A-M1: home comes from the account database, not $HOME
        p = mock.patch.object(upgrade_owner, "HOME_OF", lambda uid: self.pw_home)
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")

    def lstat(self, path):
        real = os.lstat(path)
        path = os.path.normpath(str(path))          # the kernel resolves `..`; so does this fake
        foreign = path in self.root_exact or any(path == r or path.startswith(r + os.sep) for r in self.root_owned)
        fields = list(real)
        fields[4] = 0 if foreign else ME                                # st_uid
        return os.stat_result(fields)

    def apply(self, *, writable=False, run=None, plan_fn=None, fenced=False):
        with mock.patch.object(update, "writable", return_value=writable), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]):
            return update.apply(self.st, NEW, prefix=str(self.tool), check_fn=lambda: {"status": "newer", "latest": NEW},
                                run=run or mock.Mock(), svc_on=False, rollback_fn=lambda st: None, fenced=fenced,
                                plan_fn=plan_fn or (lambda st, info, target: update.commands(info, target)))


class OwnerDetection(_Mac):
    def test_root_owned_uv_tree_and_entry_point(self):
        own = upgrade_owner.blocked(str(self.tool))
        self.assertEqual(own["owner_kind"], "root")
        self.assertEqual(sorted(own["paths"]), sorted([str(self.uv), str(self.entry)]))
        argv = shlex.split(own["fix"])
        self.assertEqual(argv[:2], ["agentj", "sudo"])                 # the phone admin card, never a silent sudo
        self.assertEqual(argv[argv.index("--") + 1:argv.index("--") + 5], ["chown", "-R", "-h", USER])
        self.assertEqual(sorted(argv[argv.index("--") + 5:]), sorted([str(self.uv), str(self.entry)]))

    def test_only_files_inside_root_owned(self):
        self.root_owned = {str(self.tool / "lib/python3.12/site-packages/agentj")}
        own = upgrade_owner.blocked(str(self.tool))
        self.assertEqual(own["paths"], [str(self.tool)])

    def test_fine_when_everything_is_mine_or_outside_home(self):
        self.root_owned = set()
        self.assertIsNone(upgrade_owner.blocked(str(self.tool)))
        self.root_owned = {str(self.uv)}
        self.assertIsNone(upgrade_owner.blocked("/opt/agentj"))
        self.assertIsNone(upgrade_owner.blocked(str(self.tool), uid=0))

    def assert_inside_home(self, own):
        for t in own["paths"]:
            self.assertEqual(os.path.commonpath([t, str(self.home)]), str(self.home))
            self.assertNotEqual(t, str(self.home))

    def test_a_m1_dotdot_cache_never_chowns_the_homes_parent(self):
        """Security A-M1: UV_CACHE_DIR=~/.cache/../.. resolves to /Users (root-owned on every Mac): never a chown target."""
        self.root_owned, self.root_exact = set(), {str(self.home.parent)}       # /Users: root-owned on every Mac
        for cache in (str(self.home / ".cache/../.."), str(self.home / ".cache/.."), str(self.home), "/"):
            with self.subTest(cache=cache), mock.patch.dict(os.environ, {"UV_CACHE_DIR": cache}):
                self.assertIsNone(upgrade_owner.blocked(str(self.tool)))
        self.root_owned = {str(self.uv)}
        with mock.patch.dict(os.environ, {"UV_CACHE_DIR": str(self.home / ".cache/../..")}):
            own = upgrade_owner.blocked(str(self.tool))
        self.assertEqual(own["paths"], [str(self.uv)])
        self.assert_inside_home(own)
        self.assertNotIn(shlex.quote(str(self.home.parent)) + " ", own["fix"] + " ")

    def test_a_m1_home_from_passwd_not_env(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.home.parent)}):
            own = upgrade_owner.blocked(str(self.tool))
        self.assert_inside_home(own)

    def test_a_m1_root_or_empty_home_gives_no_fix_and_no_hang(self):
        for home in ("/", ""):
            with self.subTest(home=home):
                self.pw_home = home
                self.root_owned = {"/"}
                self.assertIsNone(upgrade_owner.blocked(str(self.tool)))
                self.assertIsNone(upgrade_owner.blocked("/usr/lib/x"))
        foreign = lambda p: os.stat_result([0o40755, 1, 1, 1, 0, 0, 0, 0, 0, 0])   # everything root, any depth
        self.assertEqual(upgrade_owner._top("/a/b/c/d", "/a", ME, foreign), "/a/b")
        self.assertEqual(upgrade_owner._top("/a/b", "/", ME, foreign), "/a/b")          # never climbs to /

    def test_a_m1_symlinked_folder_out_of_home_is_refused(self):
        outside = pathlib.Path(self.tmp.name).resolve() / "elsewhere"
        outside.mkdir()
        cache = self.home / ".cache"
        cache.mkdir(exist_ok=True)
        (cache / "uv").symlink_to(outside)
        self.root_owned = {str(outside)}
        self.assertIsNone(upgrade_owner.blocked(str(self.tool)))

    def test_walk_is_bounded(self):
        self.root_owned = {str(self.tool / "lib/python3.12/site-packages/agentj")}
        self.assertIsNone(upgrade_owner.blocked(str(self.tool), limit=1))


class Refusal(_Mac):
    def test_apply_names_root_and_the_one_fix_step(self):
        res = self.apply()
        self.assertEqual((res["result"], res["reason"], res["exit"], res["owner_kind"]), ("refused", "not_owner", 2, "root"))
        upgrade_diag.after_failure(self.st, res, lang="zh")
        self.assertNotIn("support", res)                               # a local fix; nothing sent anywhere
        block = update.result_block(res)
        self.assertIn("reason: not_owner", block)
        self.assertIn("fix: agentj sudo --why", block)
        self.assertIn("why: 程序目录属于另一个账号", block)
        self.assertIn("next: run the fix line now", block)
        self.assertIn("管理员密码卡", res["tell_owner"])
        self.assertIn("root", res["tell_owner"])
        self.assertIn("one administrator-password card", upgrade_diag.owner_sentence(res, {}, "en"))

    def test_cli_prints_tell_owner(self):
        out = io.StringIO()
        res = self.apply()
        with contextlib.redirect_stdout(out):
            cli._upgrade_failed(self.st, res)
            print(update.result_block(res))
        self.assertIn("tell_owner: Agent J 的程序目录属于 root", out.getvalue())

    def test_fence_without_foreign_owner_keeps_the_fence_message(self):
        self.root_owned = set()
        res = self.apply(fenced=True)
        self.assertEqual(res["reason"], "read_only")
        self.assertNotIn("fix", res)

    def test_installer_permission_error_gets_the_fix(self):
        def run(argv, **kw):
            if "install" in argv:
                return subprocess.CompletedProcess(argv, 2, "", "error: failed to remove file: Permission denied (os error 13)\n")
            return subprocess.CompletedProcess(argv, 0, "agentj 0", "")
        res = self.apply(writable=True, run=run)
        self.assertEqual(res["reason"], "uv_failed")                   # the manual path keeps its P277 category
        self.assertTrue(res["fix"].startswith("agentj sudo"))
        rep = upgrade_diag.owner_sentence(res, {"status": "off"}, "zh")
        self.assertIn("管理员密码卡", rep)
        body = upgrade_diag.body(res, "uv")
        self.assertIn("owner_kind: root", body)
        self.assertNotIn(USER, body)
        self.assertNotIn(str(self.home), body)

    def test_doctor_row(self):
        with mock.patch.object(sys, "prefix", str(self.tool)), mock.patch.object(doctor, "_fenced", return_value=False):
            row = doctor.check_install_dir()
            self.assertEqual((row["id"], row["status"]), ("install-dir", "warn"))
            self.assertTrue(row["hint"].startswith("agentj sudo --why"))
            self.root_owned = set()
            self.assertIsNone(doctor.check_install_dir())
        with mock.patch.object(sys, "prefix", str(self.tool)), mock.patch.object(doctor, "_fenced", return_value=True):
            self.root_owned = {str(self.uv)}
            self.assertIsNone(doctor.check_install_dir())


class Opener:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, req, *, timeout):
        self.calls.append(timeout)
        o = self.outcomes.pop(0)
        if isinstance(o, BaseException):
            raise o

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R(o)


def http(code):
    return urllib.error.HTTPError("https://agentj.app/dl/x", code, "x", {}, None)


class Download(unittest.TestCase):
    def test_retries_transport_failures_with_backoff(self):
        slept, op = [], Opener(urllib.error.URLError("down"), TimeoutError(), b"wheel")
        self.assertEqual(auto_update.download("https://agentj.app/dl/x", 100, sleep=slept.append, opener=op), b"wheel")
        self.assertEqual(slept, list(auto_update.DOWNLOAD_BACKOFF))
        self.assertEqual(op.calls, [auto_update.DOWNLOAD_TIMEOUT] * 3)
        self.assertGreater(auto_update.DOWNLOAD_TIMEOUT, 30)

    def test_gives_up_after_three_and_says_how_many(self):
        op = Opener(*[urllib.error.URLError("down")] * 3)
        with self.assertRaises(urllib.error.URLError) as cm:
            auto_update.download("https://agentj.app/dl/x", 100, sleep=lambda s: None, opener=op)
        self.assertEqual(cm.exception.download_attempts, 3)
        op = Opener(http(503), http(502), b"ok")
        self.assertEqual(auto_update.download("https://agentj.app/dl/x", 10, sleep=lambda s: None, opener=op), b"ok")

    def test_no_retry_for_answers_that_will_not_change(self):
        for exc in (http(404), http(403), ValueError("insecure_update_redirect")):
            op = Opener(exc, b"never")
            with self.subTest(exc=exc), self.assertRaises(type(exc)) as cm:
                auto_update.download("https://agentj.app/dl/x", 10, sleep=lambda s: None, opener=op)
            self.assertEqual(len(op.calls), 1)
            self.assertEqual(cm.exception.download_attempts, 1)
        with self.assertRaises(ValueError):
            auto_update.download("https://agentj.app/dl/x", 2, sleep=lambda s: None, opener=Opener(b"too large"))

    def test_attempts_reach_the_plan_fields(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")

            def fetch(url, limit):
                e = urllib.error.URLError("down")
                e.download_attempts = 3
                raise e
            with self.assertRaises(urllib.error.URLError) as cm:
                auto_update.cached_wheel(st, NEW, fetch=fetch)
        self.assertEqual(upgrade_diag.plan_reason(cm.exception), "download_failed")
        self.assertEqual(upgrade_diag.plan_fields(cm.exception)["download_attempts"], 3)


class Ch:
    signed, st = True, None

    def __init__(self):
        self.sent = []

    def send(self, f):
        self.sent.append(f)
        return 200, {"thread": "st_" + "M" * 22, "seq": 1}


class MetadataReport(_Mac):
    def failed_download(self):
        def plan(st, info, target):
            e = urllib.error.URLError("down")
            e.download_attempts = 3
            e.artifact_diagnostic = {"artifact_stage": "wheel", "cache_status": "miss"}
            raise e
        with mock.patch.object(update, "install_kind", return_value={"kind": "uv", "where": str(self.tool), "legacy": False}):
            return self.apply(writable=True, plan_fn=plan)

    def test_download_failed_reaches_support_without_jev(self):
        ch = Ch()
        res = self.failed_download()
        self.assertEqual((res["reason"], res["step"], res["download_attempts"]), ("download_failed", "download", 3))
        with mock.patch.object(support, "channel_for", return_value=ch), mock.patch.object(support, "remember"), \
                mock.patch.object(support, "doctor_text", return_value="doctor " + str(self.home)), \
                mock.patch.object(privacy, "layer2", return_value=privacy.Verdict("unavailable", reason="jev.mode=unset")), \
                mock.patch("agentj.privacy.local_identity", return_value=("Catherines-MacBook", USER)), \
                mock.patch.object(update, "install_kind", return_value={"kind": "uv", "where": str(self.tool), "legacy": False}):
            upgrade_diag.after_failure(self.st, res, lang="zh")
        self.assertEqual(len(ch.sent), 1)                              # the full report stayed held (P573)
        f = ch.sent[0]
        self.assertNotIn("attach", f)
        lines = f["body"].split("\n")
        self.assertEqual(lines[0], upgrade_diag.META_HEAD)
        for need in ("reason: download_failed", "step: download", f"to: {NEW}", "artifact_stage: wheel"):
            self.assertIn(need, lines)
        for leak in (USER, "Catherines", str(self.home), "stderr", "/"):
            self.assertNotIn(leak, f["body"])
        self.assertEqual(res["support"], "sent (st_" + "M" * 22 + ") metadata only")
        self.assertIn("不含日志", res["tell_owner"])

    def test_doctor_failure_id_survives_jev_unavailable_without_free_text(self):
        ch = Ch()
        res = {"from": "0.26.8a1", "to": NEW, "result": "rolled_back", "rollback": "ok",
               "reason": "doctor_failed", "step": "health",
               "failed_checks": ["serve", "state", "serve", "private_owner_note", "private/owner/key"],
               "detail": "doctor failed at private/owner/key", "stderr_tail": "secret private/owner/key"}
        with mock.patch.object(support, "channel_for", return_value=ch), mock.patch.object(support, "remember"), \
                mock.patch.object(support, "doctor_text", return_value="doctor private/owner/key"), \
                mock.patch.object(privacy, "layer2", return_value=privacy.Verdict("unavailable", reason="jev.mode=unset")):
            upgrade_diag.after_failure(self.st, res, lang="zh")
        self.assertEqual(len(ch.sent), 1)
        sent = ch.sent[0]
        self.assertNotIn("attach", sent)
        self.assertIn("failed_checks: serve, state", sent["body"])
        self.assertNotIn("private_owner_note", sent["body"])
        self.assertNotIn("catherine", sent["body"])
        self.assertNotIn("secret", sent["body"])
        self.assertTrue(support.metadata_ok(sent["body"]))
        self.assertFalse(support.metadata_ok(sent["body"].replace("reason: doctor_failed", "reason: download_failed")))

    def test_metadata_only_is_a_closed_form(self):
        good = upgrade_diag.metadata_body({"from": "0.26.8a1", "to": NEW, "result": "failed", "reason": "download_failed",
                                           "step": "download", "detail": "URLError"}, "uv")
        self.assertTrue(support.metadata_ok(good))
        for bad in (good + "\nnote: my key is in /Users/x", good + "\nfree text without key", good.replace("[auto]", "[x]"),
                    upgrade_diag.META_HEAD):
            with self.subTest(bad=bad[-30:]):
                self.assertFalse(support.metadata_ok(bad))
        self.assertFalse(support.metadata_ok(good, attach_doctor=True))
        self.assertFalse(support.metadata_ok(good, thread="st_" + "A" * 22))
        ch = Ch()
        with self.assertRaises(support.SupportError):
            support.run_send("bug", good + "\nfree text", wait=0, ch=ch, metadata_only=True, out=io.StringIO())
        self.assertEqual(ch.sent, [])

    # 贾维斯 ruling (reports/decisions.jsonl ts 1793803157.003, P573 amended): Jev unavailable → only fixed-field metadata:
    # version, stage, error code, hash, fixed doctor check IDs — no path, user name, body or attachment.
    LOCKED = frozenset({"from", "to", "result", "reason", "step", "failure_stage", "artifact_stage", "subreason",
                        "cache_status", "exception_type", "errno", "artifact_sha256", "stated_sha256",
                        # #0307 (贾维斯 dispatch 2026-11-10): closed words for the cache folder kind / level / owner kind
                        "cache_path", "cache_level", "cache_owner", "cache_fallback", "failed_checks"})

    def test_ruling_locks_the_exact_field_whitelist(self):
        self.assertEqual(frozenset(upgrade_diag.META_FIELDS), self.LOCKED)
        everything = {"from": "0.26.8a1", "to": NEW, "result": "failed", "reason": "download_failed", "step": "download",
                      "rollback": "ok", "detail": "URLError", "failed_checks": ["serve"], "owner_kind": "root",
                      "download_attempts": 3, "command": "uv tool install --force <wheel>", "exit": 2,
                      "exception": "URLError", "exception_type": "URLError", "errno": errno.ETIMEDOUT, "failure_stage": "download",
                      "failure_at": 1793800000, "artifact_stage": "wheel", "subreason": "internal_error",
                      "cache_status": "miss", "artifact_sha256": "a" * 64, "stated_sha256": "b" * 64,
                      "cache_path": "home", "cache_level": "upgrade_root", "cache_owner": "root", "cache_fallback": "tmp",
                      "stderr_tail": "Permission denied /Users/catherine", "fix": "agentj sudo …"}
        body = upgrade_diag.metadata_body(everything, "uv")
        keys = {ln.split(": ", 1)[0] for ln in body.split("\n")[1:]}
        self.assertEqual(keys, self.LOCKED - {"failed_checks"})
        self.assertTrue(support.metadata_ok(body))
        for leak in ("catherine", "Permission", "agentj sudo", "platform", "python", "uv tool"):
            self.assertNotIn(leak, body)

    def test_a_n2_lines_outside_the_closed_set_are_refused(self):
        good = upgrade_diag.metadata_body({"from": "0.26.8a1", "to": NEW, "result": "failed", "reason": "download_failed",
                                           "step": "download"}, "uv")
        self.assertTrue(support.metadata_ok(good))
        for line in ("platform: linux x86_64", "body: hello there", "note: x", "reason: catherine", "reason: a.b",
                     "to: catherine", "from: 1.0 /Users/x", "step: download@host", "exception_type: Mine",
                     "errno: 99999", "artifact_sha256: " + "A" * 64, "result: catherine", "subreason: catherine",
                     "detail: URLError", "install: uv", "owner_kind: root", "failed_checks: private_owner_note",
                     "failed_checks: serve, private/owner/key", "failed_checks: serve, serve",
                     "failed_checks: " + ", ".join(["serve"] * 11), "failed_checks: serve,unknown"):
            with self.subTest(line=line):
                self.assertFalse(support.metadata_ok(good + "\n" + line))

    def test_free_text_detail_never_enters_metadata(self):
        body = upgrade_diag.metadata_body({"to": NEW, "reason": "download_failed", "step": "download",
                                           "detail": "failed at /Users/catherine/x"}, "uv")
        self.assertNotIn("detail", body)
        self.assertNotIn("catherine", body)

    def test_off_preference_still_wins(self):
        ch = Ch()
        res = self.failed_download()
        with mock.patch.dict(os.environ, {upgrade_diag.REPORT_ENV: "off"}), \
                mock.patch.object(support, "channel_for", return_value=ch):
            upgrade_diag.after_failure(self.st, res, lang="en")
        self.assertEqual(ch.sent, [])
        self.assertTrue(res["support"].startswith("off"))


if __name__ == "__main__":
    unittest.main()
