"""P44 (0.15): F12 authorized upgrade (contracts C1 / C2 / C3) and F13 the one language value + phone settings (A1 / C6).

- C3 parse: the pasted upgrade email in zh / en, any mail client's case / spaces / dashes; the dry-run placeholder is never a
  code; two codes or two target versions are refused.
- C3 order: local checks → update.check() newer with latest == target (else refused, nothing installed, the code not used)
  → signed upgrade-auth → only on 200 the same install commands as interactive apply, pinned, stdin closed (no y/N, no
  terminal) → the version verified → marker → service re-install. Exit codes and the UPGRADE_RESULT block; the code is
  never logged or stored (only its SHA-256, for a resumable rerun on this host); read-only (fence) refused plainly.
- C2 cloud call against the fake control plane (strict inner keys); C3 CLI without a terminal (refusals only: a real install
  is never reached here); serve's one phone line after the restart, in the owner's language, once.
- A1: `appearance.language` is the one value; language.json records when it changed locally; the report carries it; the
  account's newer value is applied through the preferences write path and broadcast; the main Agent identity carries
  "Speak with the owner in …" and is hot for the next turn (Claude Code / Codex restart on the same conversation; OpenCode
  and shared sessions never restarted for it).
- C6: `pref_set` whitelist (4 keys), schema-checked values, `pref_res` before the `preferences` broadcast; safety keys
  refused (`not_allowed`); the broadcast carries read-only host facts (version, language_at, settable keys, paired phones).
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import __version__, cloud, main_identity, preferences, reporter, serve, update  # noqa: E402
from agentj.state import State  # noqa: E402
from fakecp import FakeCP  # noqa: E402

CODE = "AJUP-ABCDE-FGHJK-MNPQR-STVWX"
MAIL_ZH = ("拷贝本邮件内容发给所有席位的 Agent J，请他们完成自我升级和重启。\n目标版本：9.9.9\n"
           "新功能\n· 设置面板\n运行：agentj update apply --authorization ajup-abcde fghjk–mnpqr-stvwx\n"
           "授权码：AJUP-ABCDE-FGHJK-MNPQR-STVWX（14 天内有效）\n")
MAIL_EN = ("Copy this whole email and send it to the Agent J on every seat.\nTarget version: v9.9.9\n"
           "Run: agentj update apply --authorization AJUP-ABCDE-FGHJK-MNPQR-STVWX\n")


def _link(st, api):
    cloud.write_cloud(st, {"api": api, "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                           "linked_at": int(time.time()), "last_seq": 0})


class _Env(unittest.TestCase):
    """A throw-away HOME / XDG / state dir per test (preferences live under XDG_CONFIG_HOME)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p44-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.env = {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"), "AGENTJ_STATE_DIR": str(root / "state")}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        self.prefix = root / "venv"
        self.prefix.mkdir()
        (self.prefix / "uv-receipt.toml").write_text('[tool]\nrequirements = [{ name = "agentj" }]\n')


# ------------------------------------------------------------------ C1 / C3 parse
class Parse(unittest.TestCase):
    def test_code_normalisation(self):
        for raw in (CODE, CODE.lower(), "AJUP ABCDE FGHJK MNPQR STVWX", "ajup–abcde—fghjk-mnpqr - stvwx", "AJUPABCDEFGHJKMNPQRSTVWX",
                    " AJUP-ABCDE-\nFGHJK-MNPQR-STVWX "):
            self.assertEqual(update.normalise_code(raw), CODE, raw)
        self.assertEqual(update.normalise_code("AJUP-ABCDE-FGHJK-MNPQR-STVWO"), "AJUP-ABCDE-FGHJK-MNPQR-STVW0", "O read as 0")
        for bad in (None, "", "AJUP-ABCDE-FGHJK-MNPQR", "AJUP-ABCDE-FGHJK-MNPQR-STVWU", update.PLACEHOLDER, "x" * 300, 7):
            self.assertIsNone(update.normalise_code(bad), bad)

    def test_email_zh_en(self):
        self.assertEqual(update.parse_email(MAIL_ZH), {"code": CODE, "version": "9.9.9", "problem": None})
        self.assertEqual(update.parse_email(MAIL_EN), {"code": CODE, "version": "9.9.9", "problem": None})
        glued = "目标版本：0.15.0a1新功能 授权码 AJUP-ABCDE-FGHJK-MNPQR-STVWX"
        self.assertEqual(update.parse_email(glued)["version"], "0.15.0a1", "a version glued to the next word")
        self.assertEqual(update.parse_email("Target Version : 1.2.3rc1 code " + CODE)["version"], "1.2.3rc1")
        self.assertEqual(update.parse_email("no target here " + CODE), {"code": CODE, "version": None, "problem": None})

    def test_email_problems(self):
        self.assertEqual(update.parse_email("hello")["problem"], "no_code")
        self.assertEqual(update.parse_email("preview " + update.PLACEHOLDER)["problem"], "no_code", "dry-run placeholder")
        two = MAIL_ZH + "\nAJUP-22222-22222-22222-22222"
        self.assertEqual(update.parse_email(two)["problem"], "ambiguous_code")
        self.assertEqual(update.parse_email(MAIL_ZH + "\nTarget version: 9.9.8")["problem"], "ambiguous_version")

    def test_read_email_bounded(self):
        self.assertEqual(update.read_email("-", io.StringIO(MAIL_EN)), MAIL_EN)
        with self.assertRaises(ValueError):
            update.read_email("-", io.StringIO("x" * (update.MAX_MAIL + 1)))


# ------------------------------------------------------------------ C3 order (fakes: nothing is installed for real)
class AuthorizedApply(_Env):
    def _run(self, *, check=None, auth=None, svc_on=True, rc=0, version_out=None, code=CODE, target="9.9.9", mail=None):
        calls, auths = [], []

        def run(argv, **kw):
            calls.append((argv, kw))
            if argv[-1] == "--version":
                return SimpleNamespace(returncode=0, stdout=f"agentj {version_out or target}\n", stderr="")
            return SimpleNamespace(returncode=rc)

        def auth_fn(st, c, v):
            auths.append((c, v))
            return auth or {"status": "ok", "http": "200"}
        res = update.authorized_apply(self.st, code, target, mail=mail, prefix=str(self.prefix),
                                      plan_fn=lambda st, info, target:update.commands(info,target), check_fn=lambda: check or {"status": "newer", "latest": "9.9.9"},
                                      auth_fn=auth_fn, run=run, svc_on=svc_on)
        return res, calls, auths

    def test_ok_pinned_no_terminal_marker_and_restart(self):
        _link(self.st, "http://127.0.0.1:9")
        res, calls, auths = self._run()
        self.assertEqual((res["result"], res["reason"], res["exit"], res["service"]), ("ok", "upgraded", 0, "restart_scheduled"))
        self.assertEqual(auths, [(CODE, "9.9.9")])
        install = [c for c, _ in calls[:-2]]
        self.assertEqual(install, update.commands(update.install_kind(str(self.prefix)), "9.9.9"))
        self.assertIn("@v9.9.9#", install[0][-1], "pinned to the authorized tag")
        self.assertTrue(all(kw.get("stdin") == subprocess.DEVNULL for _, kw in calls), "never asks: stdin closed")
        self.assertEqual(calls[-1][0][-4:], ["service", "install", "--deferred", "9.9.9"])
        marker = json.loads((self.st.root / update.UPGRADED).read_text())
        self.assertEqual((marker["from"], marker["to"]), (__version__, "9.9.9"))
        rec = (self.st.root / update.AUTH_REC).read_text()
        self.assertNotIn(CODE, rec)
        self.assertIn(update._code_hash(CODE), rec)
        for f in self.st.root.rglob("*"):
            if f.is_file():
                self.assertNotIn(CODE.encode(), f.read_bytes(), f"the code is never stored ({f.name})")
        block = update.result_block(res)
        self.assertTrue(block.startswith("UPGRADE_RESULT ok\nreason: upgraded\n"))
        for line in (f"from: {__version__}", "to: 9.9.9", "service: restart_scheduled", "next: agentj doctor"):
            self.assertIn(line, block)

    def test_refused_before_anything_is_spent(self):
        _link(self.st, "http://127.0.0.1:9")
        for check, reason, ex in (({"status": "unknown", "latest": None}, "unknown", 1),
                                  ({"status": "newer", "latest": "9.9.10"}, "not_latest", 2),
                                  ({"status": "ahead", "latest": "0.0.1"}, "not_newer", 2)):
            res, calls, auths = self._run(check=check)
            self.assertEqual((res["reason"], res["exit"]), (reason, ex))
            self.assertEqual((calls, auths), ([], []), f"{reason}: no install, the code not used")
        res, calls, auths = self._run(code="AJUP-12345")
        self.assertEqual((res["reason"], res["exit"], calls, auths), ("bad_code", 2, [], []))
        res, calls, auths = self._run(mail={"code": CODE, "version": "9.9.8", "problem": None})
        self.assertEqual(res["reason"], "version_conflict")
        res, calls, auths = self._run(mail={"code": None, "version": None, "problem": "no_code"})
        self.assertEqual((res["reason"], res["exit"]), ("no_code", 2))
        self.assertFalse((self.st.root / update.UPGRADED).exists())

    def test_not_linked_and_read_only(self):
        res, calls, auths = self._run()
        self.assertEqual((res["reason"], res["exit"], calls, auths), ("not_linked", 2, [], []))
        _link(self.st, "http://127.0.0.1:9")
        os.chmod(self.prefix, 0o500)
        try:
            if not os.access(self.prefix, os.W_OK):           # (root ignores modes)
                res, calls, auths = self._run()
                self.assertEqual((res["result"], res["reason"], res["exit"], calls, auths), ("refused", "read_only", 2, [], []))
                self.assertIn("fence", update.result_block(res))
        finally:
            os.chmod(self.prefix, 0o700)

    def test_server_answers_map_to_exit_codes_without_install(self):
        _link(self.st, "http://127.0.0.1:9")
        for status, ex in (("invalid_authorization", 3), ("not_bound", 3), ("version_mismatch", 4), ("expired", 5),
                           ("already_used", 6), ("rate_limited", 7), ("unreachable", 1), ("fail", 1)):
            res, calls, _ = self._run(auth={"status": status, "http": "x", **({"version": "9.9.8"} if status == "version_mismatch" else {})})
            self.assertEqual((res["reason"], res["exit"], calls), (status, ex, []), status)
        res, _, _ = self._run(auth={"status": "version_mismatch", "version": "9.9.8"})
        self.assertIn("authorized_version: 9.9.8", update.result_block(res))

    def test_rerun_after_failed_install_resumes_this_hosts_authorization(self):
        _link(self.st, "http://127.0.0.1:9")
        res, calls, _ = self._run(rc=1)
        self.assertEqual((res["reason"], res["exit"]), ("install_failed", 1))
        self.assertFalse((self.st.root / update.UPGRADED).exists())
        res, calls, _ = self._run(auth={"status": "already_used", "http": "http_4xx"})
        self.assertEqual((res["reason"], res["exit"]), ("upgraded", 0), "same host, same code, same version: continue")
        res, calls, _ = self._run(auth={"status": "already_used", "http": "http_4xx"}, code="AJUP-22222-22222-22222-22222")
        self.assertEqual((res["reason"], calls), ("already_used", []), "another code this host never spent: refused")

    def test_version_not_reached_and_no_service(self):
        _link(self.st, "http://127.0.0.1:9")
        res, _, _ = self._run(version_out="0.0.1")
        self.assertEqual((res["reason"], res["exit"]), ("not_upgraded", 1))
        res, calls, _ = self._run(svc_on=False)
        self.assertEqual((res["reason"], res["service"]), ("upgraded", "not_installed"))
        self.assertNotIn("service", [a for c, _ in calls for a in c])

    def test_already_current(self):
        _link(self.st, "http://127.0.0.1:9")
        res, calls, auths = self._run(target=__version__, check={"status": "current", "latest": __version__})
        self.assertEqual((res["reason"], res["exit"], calls, auths), ("already_current", 0, [], []))


# ------------------------------------------------------------------ C2 against the fake control plane
class UpgradeAuthCall(_Env):
    def test_answers(self):
        with FakeCP() as cp:
            _link(self.st, cp.url)
            cp.grants = {CODE: "9.9.9", "AJUP-33333-33333-33333-33333": "9.9.9"}
            cp.grant_expired = {"AJUP-33333-33333-33333-33333"}
            self.assertEqual(cloud.upgrade_auth(self.st, CODE, "9.9.8")["status"], "version_mismatch")
            self.assertEqual(cloud.upgrade_auth(self.st, CODE, "9.9.8").get("version"), "9.9.9")
            ok = cloud.upgrade_auth(self.st, CODE, "9.9.9")
            self.assertEqual(ok["status"], "ok")
            self.assertIsInstance(ok["expires_at"], int)
            self.assertEqual(cloud.upgrade_auth(self.st, CODE, "9.9.9")["status"], "already_used")
            self.assertEqual(cloud.upgrade_auth(self.st, "AJUP-33333-33333-33333-33333", "9.9.9")["status"], "expired")
            self.assertEqual(cloud.upgrade_auth(self.st, "AJUP-44444-44444-44444-44444", "9.9.9")["status"], "invalid_authorization")
            cp.upgrade_script = ["rate", 503]
            self.assertEqual(cloud.upgrade_auth(self.st, CODE, "9.9.9")["status"], "rate_limited")
            self.assertEqual(cloud.upgrade_auth(self.st, CODE, "9.9.9")["status"], "unreachable")
            self.assertEqual(cp.rejected, [], "strict inner keys accepted")
            self.assertEqual(set(cp.upgrade_auths[0]), {"v", "t", "channel", "ts", "code", "version"})
        self.assertNotIn(CODE, self.st.log_path.read_text(), "never logged")
        self.assertEqual(cloud.upgrade_auth(State(pathlib.Path(self.tmp.name) / "other"), CODE, "9.9.9")["status"], "unlinked")


# ------------------------------------------------------------------ C3 CLI (refusals only: a real install is never reached)
class Cli(_Env):
    def _cli(self, *args, stdin_text=None, extra=None):
        return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, input=stdin_text,
                              env={**os.environ, **self.env, **(extra or {})}, capture_output=True, text=True, timeout=60)

    def test_from_email_without_terminal(self):
        res = self._cli("update", "apply", "--from-email", "-", stdin_text=MAIL_EN)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("UPGRADE_RESULT refused\nreason: not_linked", res.stdout)
        self.assertNotIn(CODE, res.stdout + res.stderr, "the code is never printed")
        from test_l3_update import _Repo, _init_text
        with FakeCP() as cp, _Repo(_init_text("9.9.10")) as repo:
            _link(self.st, cp.url)
            res = self._cli("update", "apply", "--from-email", "-", stdin_text=MAIL_EN, extra={update.URL_ENV: repo.url})
            self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
            self.assertIn("reason: not_latest\nfrom: ", res.stdout)
            self.assertIn("latest: 9.9.10", res.stdout)
            self.assertEqual(cp.upgrade_auths, [], "not the newest release: the code is not used")
            repo.body = _init_text("9.9.9")
            res = self._cli("update", "apply", "--authorization", CODE.lower(), extra={update.URL_ENV: repo.url})
            self.assertEqual(res.returncode, 3, res.stdout + res.stderr)        # no grant in the fake: 404 → nothing installed
            self.assertIn("reason: invalid_authorization", res.stdout)
            self.assertEqual([(a["code"], a["version"]) for a in cp.upgrade_auths], [(CODE, "9.9.9")])

    def test_flags_only_with_apply_and_interactive_unchanged(self):
        off = {update.URL_ENV: "off"}   # never the live latest (0.15.0a1 went live: the outcome must not depend on it)
        self.assertNotEqual(self._cli("update", "check", "--authorization", CODE, extra=off).returncode, 0)
        self.assertNotEqual(self._cli("update", "apply", "--version", "9.9.9", extra=off).returncode, 0, "--version alone is not consent")
        res = self._cli("update", "apply", "--authorization", CODE, "--from-email", "-", stdin_text=MAIL_EN)
        self.assertNotEqual(res.returncode, 0)
        res = self._cli("update", "apply", extra={update.URL_ENV: "off"})
        self.assertIn("UPGRADE_RESULT failed", res.stdout, "without the flags: no terminal required")


# ------------------------------------------------------------------ C3: serve's one line after the restart
class UpgradedNotice(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p44-n-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                                         "AGENTJ_STATE_DIR": str(root / "state")})
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        preferences.ensure()

    async def _host(self):
        host = serve.Host(self.st, read_stdin=False)
        sent = []

        async def send_app(s, obj):
            sent.append(obj)
            return True
        host.send_app = send_app
        s = serve.Session(cid=1, state="ready", device="dev", name="phone", pub=b"p" * 32)
        host.sessions = {1: s}
        return host, sent

    async def test_once_in_the_owners_language(self):
        rows = [{"status": "ok"}] * 3 + [{"status": "warn"}, {"status": "fail"}]
        for lang, want in (("zh", f"已升级到 {__version__}（doctor: 3 ✓ / 1 ! / 1 ✗）"),
                           ("en", f"Upgraded to {__version__} (doctor: 3 ✓ / 1 ! / 1 ✗)")):
            update.write_marker(self.st, "0.14.0a1", __version__)
            host, sent = await self._host()
            host.preferences["appearance"]["language"] = lang
            with mock.patch.object(self.st, "is_allowed", return_value=True), mock.patch("agentj.doctor.run", return_value=rows):
                await host.upgraded_notice()
                await asyncio.sleep(0.05)
                self.assertEqual([o["text"] for o in sent if o.get("from") == "notice"], [want])
                sent.clear()
                await host.upgraded_notice()
                await asyncio.sleep(0.05)
                self.assertEqual([o for o in sent if o.get("from") == "notice"], [], "once per version")
        update.write_marker(self.st, "0.14.0a1", "9.9.9")
        self.assertIn("did not take effect", update.upgraded_text(update.take_marker(self.st), None, "en"))
        update.write_marker(self.st, "0.14.0a1", __version__, now=time.time() - 15 * update.DAY)
        self.assertIsNone(update.take_marker(self.st), "a stale marker says nothing")


# ------------------------------------------------------------------ A1: the one language value
class Language(_Env):
    def _set(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = preferences.command(list(args))
        return code, json.loads(out.getvalue())

    def test_identity_line_and_hashes(self):
        main_identity.verify_core()                       # the hashed core files are unchanged
        zh = main_identity.prompt({"language": "zh"})
        en = main_identity.prompt({"language": "en", "instructions": "be terse"})
        self.assertIn("只用中文与主人交流；不要附英文译文。", zh)
        self.assertNotIn("Speak with the owner in 中文", zh)
        self.assertIn("Reply to the owner only in English; do not append a translation.", en)
        self.assertLess(en.index("Reply to the owner"), en.index("be terse"), "core + language before user preferences")
        self.assertNotEqual(main_identity.expected({"language": "zh"}, "claude")["prompt_sha256"],
                            main_identity.expected({"language": "en"}, "claude")["prompt_sha256"])

    def test_language_at_recorded_on_local_change_only(self):
        lang, at = preferences.language_state(self.st)
        self.assertEqual((lang, at), ("zh", 0), "factory default: never set")
        t0 = int(time.time() * 1000)
        code, res = self._set("set", "appearance.language", "en")
        self.assertEqual((code, res["ok"]), (0, True))
        lang, at = preferences.language_state(self.st)
        self.assertEqual(lang, "en")
        self.assertGreaterEqual(at, t0)
        rec = json.loads((self.st.root / preferences.LANG_FILE).read_text())
        self.assertEqual(rec, {"language": "en", "at": at})
        self.assertEqual(oct((self.st.root / preferences.LANG_FILE).stat().st_mode & 0o777), "0o600")
        self._set("set", "appearance.theme", "dark")
        self.assertEqual(preferences.language_state(self.st), ("en", at), "another key: the language time stays")
        self._set("set", "对话语言", "zh")
        self.assertEqual(preferences.language_state(self.st)[0], "zh", "alias conversation language → appearance.language")
        self.assertFalse(any(k.startswith("conversation.") for k in preferences.SCHEMA), "no second language key (A1)")

    def test_report_carries_and_account_newer_applies(self):
        self._set("set", "appearance.language", "en")
        _, at = preferences.language_state(self.st)
        inner = cloud.build_report(self.st, set(), {}, seq=1, ts=int(time.time()))
        self.assertEqual((inner["language"], inner["language_at"]), ("en", at))
        self.assertFalse(preferences.adopt_account_language(self.st, {"language": "zh", "language_at": at}), "tie keeps local")
        self.assertFalse(preferences.adopt_account_language(self.st, {"language": "zh", "language_at": at - 1}))
        self.assertFalse(preferences.adopt_account_language(self.st, {"language": "fr", "language_at": at + 9}))
        self.assertTrue(preferences.adopt_account_language(self.st, {"language": "zh", "language_at": at + 5}))
        self.assertEqual(preferences.language_state(self.st), ("zh", at + 5), "the account's time, not now")
        self.assertIn("language_synced", self.st.log_path.read_text())

    def test_fake_server_merge_last_write_wins(self):
        with FakeCP() as cp:
            _link(self.st, cp.url)
            cp.account_language, cp.account_language_at = "en", 10
            res = cloud.send_report(self.st, set(), {})
            self.assertEqual(res.account, {"language": "en", "language_at": 10}, "host never set it (0): account wins")
            self._set("set", "appearance.language", "en")
            self._set("set", "appearance.language", "zh")
            res = cloud.send_report(self.st, set(), {})
            self.assertEqual(res.account["language"], "zh", "a newer host value moves the account")
            self.assertEqual(res.account["language_at"], preferences.language_state(self.st)[1])
            cp.account_language = None                        # an older server: no language in the answer
            self.assertIsNone(cloud.send_report(self.st, set(), {}).account)
            self.assertEqual(cp.rejected, [])
        self.assertIsNone(cloud.parse_account_language({"language": "en", "language_at": -1}))
        self.assertIsNone(cloud.parse_account_language({"language": "en", "language_at": True}))


class _AsyncEnv(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p44-a-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                                         "AGENTJ_STATE_DIR": str(root / "state")})
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        preferences.ensure()
        self.host = serve.Host(self.st, read_stdin=False)
        self.sent = []

        async def send_app(s, obj):
            self.sent.append((s.cid, obj))
            return True
        self.host.send_app = send_app
        self.pub = os.urandom(32)
        did = self.st.add_device(self.pub, "我的手机")
        self.s = serve.Session(cid=7, state="ready", device=did, name="我的手机", pub=self.pub, p33=True)
        self.host.sessions = {7: self.s}

    def types(self):
        return [o.get("t") for _, o in self.sent]


class LanguageHot(_AsyncEnv):
    async def test_change_is_hot_for_the_next_turn(self):
        agent = SimpleNamespace(cfg={"language": "zh"}, identity_changed=mock.Mock())
        self.host.agent, self.host.agent_cfg = agent, agent.cfg
        self.host.reporter.trigger = mock.Mock()
        raw = preferences.edit(preferences.read()[0], "appearance.language", "en")
        res = await self.host.apply_preferences(raw)
        self.assertEqual((res["ok"], res["needs"]), (True, []), "no serve restart for the language")
        self.assertEqual(agent.cfg["language"], "en")
        agent.identity_changed.assert_called_once()
        self.host.reporter.trigger.assert_called_once_with("language")
        self.assertIn("preferences", self.types())

    async def test_account_language_applied_and_broadcast(self):
        self.host.reporter.trigger = mock.Mock()
        self.host.account_language({"language": "en", "language_at": 5})
        for _ in range(100):
            if preferences.get(self.host.preferences, "appearance.language") == "en":
                break
            await asyncio.sleep(0.02)
        self.assertEqual(preferences.get(preferences.effective(self.st), "appearance.language"), "en", "written to the file")
        self.assertEqual(preferences.language_state(self.st), ("en", 5))
        self.assertIn("preferences", self.types())
        self.host.reporter.trigger.assert_not_called()       # the account already has it: no report "back"
        self.sent.clear()
        self.host.account_language({"language": "zh", "language_at": 5})     # tie: nothing
        self.host.account_language({"language": "zh", "language_at": 4})     # older: nothing
        await asyncio.sleep(0.1)
        self.assertEqual(preferences.get(self.host.preferences, "appearance.language"), "en")

    async def test_reporter_hands_the_account_value_to_serve(self):
        got = []
        rep = reporter.Reporter(self.st, lambda: (set(), {}), debounce=0, on_account=got.append,
                                send=lambda st, o, p: cloud.ReportResult("ok", "200", 1, {"language": "en", "language_at": 3}))
        _link(self.st, "http://127.0.0.1:9")
        await rep.send_once("test")
        self.assertEqual(got, [{"language": "en", "language_at": 3}])

    async def test_adapters(self):
        from agentj.agent import ClaudeAgent
        from agentj.agent_codex import CodexAgent
        from agentj.agent_opencode import OpenCodeAgent
        from agentj.shared_codex import SharedCodexAgent
        host = SimpleNamespace(st=mock.Mock())
        for cls, restarts in ((ClaudeAgent, True), (CodexAgent, True), (OpenCodeAgent, False)):
            a = cls(host, {"kind": cls.kind, "dir": "/tmp"})
            a._end_proc = mock.AsyncMock()
            a.identity_changed()
            await a._identity_refresh()
            self.assertEqual(a._end_proc.await_count, 1 if restarts else 0, cls.__name__)
            self.assertFalse(a.identity_stale)
            await a._identity_refresh()
            self.assertEqual(a._end_proc.await_count, 1 if restarts else 0, "once per change")
        ceo = ClaudeAgent(host, {"kind": "claude", "dir": "/tmp", "_workflow_ceo": True})
        ceo.identity_changed()
        self.assertFalse(ceo.identity_stale, "workflow CEOs carry no main identity")
        shared = SharedCodexAgent.__new__(SharedCodexAgent)
        shared._end_proc = mock.AsyncMock()
        await shared.reload_identity()
        shared._end_proc.assert_not_awaited()


# ------------------------------------------------------------------ C6: pref_set
class PrefSet(_AsyncEnv):
    async def pref(self, key, value, r="r1"):
        self.sent.clear()
        await self.host._app(self.s, {"t": "pref_set", "r": r, "key": key, "value": value})
        return [o for _, o in self.sent if o.get("t") == "pref_res"]

    async def test_whitelist_values_order_and_broadcast(self):
        for key, value in (("appearance.language", "en"), ("appearance.theme", "dark"), ("voice.wake_enabled", True),
                           ("voice.speak_replies", True)):
            res = await self.pref(key, value)
            self.assertEqual(res, [{"t": "pref_res", "r": "r1", "ok": True, "key": key}], key)
            ts = self.types()
            self.assertLess(ts.index("pref_res"), ts.index("preferences"), "pref_res, then the broadcast")
            self.assertEqual(preferences.get(preferences.effective(self.st), key), value, "the same preferences file")
        self.assertEqual(preferences.language_state(self.st)[0], "en")
        self.assertGreater(preferences.language_state(self.st)[1], 0, "a phone change is a local change (stamped)")

    async def test_refusals(self):
        before = preferences.path().read_text()
        for key, value in (("human.devices", "x"), ("approval.timeout", 999), ("appearance.font_scale", 2), (None, 1)):
            res = await self.pref(key, value)
            self.assertEqual((res[0]["ok"], res[0]["problem"]), (False, "not_allowed"), key)
            self.assertNotIn("preferences", self.types())
        for key, value in (("appearance.language", "fr"), ("appearance.theme", 3), ("voice.wake_enabled", "yes")):
            res = await self.pref(key, value)
            self.assertEqual(res[0]["problem"], "bad_value", key)
        self.assertEqual(preferences.path().read_text(), before, "nothing written")
        self.sent.clear()
        await self.host._app(self.s, {"t": "pref_set", "r": "bad rid!", "key": "appearance.theme", "value": "dark"})
        self.assertEqual(self.sent, [], "a malformed request id is ignored")
        self.s.p33 = False
        await self.host._app(self.s, {"t": "pref_set", "r": "r2", "key": "appearance.theme", "value": "dark"})
        self.assertEqual(self.sent, [], "only from a p33 session")
        self.s.p33 = True
        self.s.state = "pending"
        await self.host._app(self.s, {"t": "pref_set", "r": "r3", "key": "appearance.theme", "value": "dark"})
        self.assertEqual(self.sent, [], "never from an unapproved device")
        self.assertEqual(preferences.path().read_text(), before)

    async def test_preferences_carries_read_only_host_facts(self):
        m = self.host.preferences_msg()
        self.assertEqual(m["host"]["version"], __version__)
        self.assertEqual(m["host"]["settable"], list(serve.PREF_SET_KEYS))
        self.assertEqual([(d["name"], d["online"]) for d in m["host"]["devices"]], [("我的手机", True)])
        self.assertEqual(set(m["host"]["devices"][0]), {"id", "name", "paired_at", "online"}, "metadata only, no keys")
        self.assertIs(type(m["host"]["language_at"]), int)
        self.assertIn("high_risk_warnings", m["value"]["agent"], "the safety switches stay visible (read-only)")
        self.assertIn("session_mode", m["value"]["agent"])


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------ back-compat: a 0.15 host against the live 0.14 server
def _inner_of(env: dict) -> dict:
    import base64
    b = env["body"]
    return json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))


class Server014(_Env):
    """Host = anytime, server = 05:00 JST: a 0.15 host must keep reporting to a 0.14 server (strict inner keys, report
    OPTIONAL only agent_name / machine → 400 bad_request; no /v1/host/upgrade-auth → bare 404)."""

    def _marker(self):
        return cloud.read_cloud(self.st)["no_language"]

    def _age_marker(self, **kw):
        d = json.loads(self.st.cloud_path.read_text())
        d["no_language"].update(kw)
        self.st.cloud_path.write_text(json.dumps(d))

    def test_report_retries_once_without_language_and_remembers(self):
        self._set_en()
        with FakeCP() as cp:
            cp.server_014 = True
            _link(self.st, cp.url)
            res = cloud.send_report(self.st, set(), {})
            self.assertEqual((res.kind, res.status, res.account), ("ok", "200", None))
            self.assertEqual(cp.rejected, [("/v1/host/report", 400), ("/v1/host/report", 400)], "one refused try, then the old shape")
            self.assertEqual(len(cp.reports), 1)
            self.assertFalse({"language", "language_at"} & set(cp.reports[0]))
            self.assertEqual(cloud.read_cloud(self.st)["last_seq"], cp.reports[0]["seq"])
            mk = self._marker()
            self.assertEqual((mk["agent"], mk["api"]), (cloud.AGENT, cp.url))
            for _ in range(3):                                   # later reports: one request each, no keys
                self.assertEqual(cloud.send_report(self.st, set(), {}).kind, "ok")
            self.assertEqual(len(cp.rejected), 2, "no repeated probes")
            self.assertEqual(len(cp.reports), 4)
            seqs = [r["seq"] for r in cp.reports]
            self.assertEqual(seqs, sorted(set(seqs)), "seq strictly grows")
            self.assertTrue(all(not ({"language", "language_at"} & set(r)) for r in cp.reports))
        self.assertIn("server_without_language_sync", self.st.log_path.read_text())

    def test_reprobe_after_a_day_a_version_change_or_server_upgrade(self):
        self._set_en()
        with FakeCP() as cp:
            cp.server_014 = True
            _link(self.st, cp.url)
            cloud.send_report(self.st, set(), {})
            old = self._marker()["at"]
            self._age_marker(at=old - cloud.NO_LANGUAGE_REPROBE_MS - 1)
            self.assertEqual(cloud.send_report(self.st, set(), {}).kind, "ok")
            self.assertEqual(len(cp.rejected), 4, "a day later: asked again")
            self.assertGreaterEqual(self._marker()["at"], old)
            self._age_marker(agent="agentj/0.14.0")
            cloud.send_report(self.st, set(), {})
            self.assertEqual(len(cp.rejected), 6, "another agentj version: asked again")
            self._age_marker(api="https://api.example.invalid")
            cloud.send_report(self.st, set(), {})
            self.assertEqual(len(cp.rejected), 8, "another server: asked again")
            cp.server_014 = False                               # the server got 0.15
            cp.account_language, cp.account_language_at = "zh", 5
            self._age_marker(at=0)
            res = cloud.send_report(self.st, set(), {})
            self.assertEqual(res.account, {"language": "en", "language_at": preferences.language_state(self.st)[1]})
            self.assertIsNone(self._marker(), "the re-probe went through: marker cleared")
            self.assertEqual(len(cp.rejected), 8)
            self.assertIn("language", cp.reports[-1])

    def test_seq_gate_with_refusal_and_replay(self):
        _link(self.st, "http://127.0.0.1:9")
        answers = [(400, {"error": "bad_request"}), (409, {"error": "replay"}), (200, {"ok": True})]
        sent = []

        def post(url, env):
            sent.append(_inner_of(env))
            return answers.pop(0)
        res = cloud.send_report(self.st, set(), {}, post=post)
        self.assertEqual(res.kind, "ok")
        self.assertEqual(len(sent), 3)
        self.assertIn("language", sent[0])
        self.assertTrue({"language", "language_at"} <= set(sent[1]) & set(sent[2]), "F19-only refusal retains F13 language sync")
        self.assertFalse(set(cloud.NOTICE_KEYS) & (set(sent[1]) | set(sent[2])))
        self.assertTrue(sent[0]["seq"] < sent[1]["seq"] < sent[2]["seq"], "a fresh seq every try")
        self.assertEqual(cloud.read_cloud(self.st)["last_seq"], sent[2]["seq"])
        self.assertIsNotNone(self._marker())

    def test_other_400_is_not_taken_for_an_old_server(self):
        _link(self.st, "http://127.0.0.1:9")
        sent = []

        def post(url, env):
            sent.append(_inner_of(env))
            return 400, {"error": "bad_request"}
        res = cloud.send_report(self.st, set(), {}, post=post)
        self.assertEqual((res.kind, res.status, len(sent)), ("fail", "http_4xx", 3), "two capability retries only, bounded")
        self.assertIsNone(self._marker(), "the old shape was refused too: nothing remembered")

        def post5(url, env):
            sent.append(_inner_of(env))
            return 503, {}
        sent.clear()
        self.assertEqual(cloud.send_report(self.st, set(), {}, post=post5).status, "http_5xx")
        self.assertEqual(len(sent), 1, "no retry on anything but 400 bad_request")

    def test_upgrade_auth_against_014_is_unsupported_not_invalid(self):
        with FakeCP() as cp:
            cp.server_014 = True
            _link(self.st, cp.url)
            cp.grants = {CODE: "9.9.9"}
            r = cloud.upgrade_auth(self.st, CODE, "9.9.9")
            self.assertEqual((r["status"], r["http"]), ("unsupported", "http_4xx"))
            self.assertEqual(cp.upgrade_auths, [])
        res = update.authorized_apply(self.st, CODE, "9.9.9", prefix=str(self.prefix),
                                      plan_fn=lambda st, info, target:update.commands(info,target), check_fn=lambda: {"status": "newer", "latest": "9.9.9"},
                                      auth_fn=lambda st, c, v: r, run=lambda *a, **k: self.fail("installed"), svc_on=True)
        self.assertEqual((res["reason"], res["exit"], res["result"]), ("unsupported", 8, "refused"))
        self.assertIn("does not support upgrade authorization yet", update.result_block(res))
        self.assertFalse((self.st.root / update.AUTH_REC).exists(), "the code was not spent")

    def _set_en(self):
        with contextlib.redirect_stdout(io.StringIO()):
            preferences.command(["set", "appearance.language", "en"])
