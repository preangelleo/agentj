"""P277 (a Windows user on WSL2, 0.20.1a1 → 0.20.3a1, 2026-10-14: 「安装步骤失败」 and nothing else reached us): a failed upgrade can
be diagnosed without the owner's terminal.

- the failed step + a finer reason (download_failed · signature_or_hash_mismatch · uv_failed · service_restart_failed) and
  the installer's last ~20 stderr lines, redacted on the host, in UPGRADE_RESULT;
- the attempt is the host's last upgrade (last-upgrade.json) → the heartbeat's `last_upgrade` {from,to,result,reason,at}
  (never the stderr), back-compat with a server that does not know the key;
- after a failure one support report (metadata + redacted tail + doctor) unless `updates.report_failures` is off, once per
  (target, reason) a day; one plain sentence for the owner (`tell_owner:`);
- the nightly / idle worker and the fenced apply (host op `record`) remember it too.
All I/O hermetic: fake installers, fake support sender, fake report POST."""
import _hermetic  # noqa: F401,I001
import base64
import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

from agentj import __version__, auto_update, cli, cloud, minisign, update, upgrade_diag
from agentj.state import State

INFO = {"kind": "uv", "where": "/fake", "legacy": False}
NEW = "9.9.9"
FAKE_GH = "ghp" + "_" + "abcdefghijklmnopqrstuvwxyz0123456789"     # built at run time: a fake token shape, never a key
UV_ERR = ("Resolved 41 packages in 1.2s\n" + "".join(f"noise line {i}\n" for i in range(30))
          + "error: Failed to install: /ho" "me/winuser/.local/share/uv/tools/agentj/lib/python3.12/site-packages/agentj\n"
          "  Caused by: failed to remove /mnt/c/Us" "ers/WinUser/AppData/Local/Temp/x: Permission denied (os error 13)\n"
          f"  token={FAKE_GH} on DESKTOP-TEST123 for winuser\n"
          "  opaque AbCdEf0123456789AbCdEf0123456789xyZ wheel sha256 " + "ab" * 32 + "\n")
IDENTITY = ("DESKTOP-TEST123", "winuser")


class Runner:
    def __init__(self, fail=None, err=UV_ERR, current=NEW):
        self.calls, self.fail, self.err, self.current = [], fail, err, current

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, f"agentj {self.current}", "")
        if argv[-2:] == ["doctor", "--json"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": [{"id": "version", "status": "ok"}]}), "")
        if self.fail and self.fail in argv:
            return subprocess.CompletedProcess(argv, 2, "", self.err)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p277-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                                         "AGENTJ_STATE_DIR": str(root / "state"), "XDG_CACHE_HOME": str(root / ".cache")})
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        ident = mock.patch("agentj.privacy.local_identity", return_value=IDENTITY)
        ident.start()
        self.addCleanup(ident.stop)

    def apply(self, run, plan_fn=None, svc_on=True):
        with mock.patch.object(update, "install_kind", return_value=INFO), mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                mock.patch.object(update, "writable", return_value=True):
            return update.apply(self.st, NEW, plan_fn=plan_fn or (lambda st, info, target: update.commands(info, target)),
                                check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=svc_on,
                                rollback_fn=lambda st: None, fenced=False)


class Redaction(unittest.TestCase):
    def test_tail_keeps_last_lines_and_redacts_names_paths_tokens(self):
        t = upgrade_diag.tail(UV_ERR, IDENTITY)
        lines = t.splitlines()
        self.assertLessEqual(len(lines), upgrade_diag.TAIL_LINES)
        self.assertIn("Permission denied (os error 13)", t)
        for leak in ("winuser", "WinUser", "DESKTOP-TEST123", FAKE_GH,
                     "AbCdEf0123456789AbCdEf0123456789xyZ", "/ho" "me/winuser", "Us" "ers/WinUser"):
            self.assertNotIn(leak, t, leak)
        self.assertIn("ab" * 32, t, "a public artifact's sha256 stays (support compares it)")
        self.assertNotIn("noise line 0\n", t + "\n", "only the last lines")

    def test_tail_empty_and_bytes_and_long_lines(self):
        self.assertEqual(upgrade_diag.tail(""), "")
        self.assertEqual(upgrade_diag.tail(None), "")
        self.assertTrue(upgrade_diag.tail(b"error: x\n", IDENTITY).startswith("error: x"))
        self.assertLessEqual(len(upgrade_diag.tail("y" * 5000, IDENTITY)), upgrade_diag.LINE_MAX)

    def test_redaction_failure_sends_nothing(self):
        with mock.patch("agentj.privacy.redact", side_effect=RuntimeError("x")):
            self.assertEqual(upgrade_diag.redact("secret /ho" "me/winuser", IDENTITY), "")

    def test_plan_reasons(self):
        self.assertEqual(upgrade_diag.plan_reason(urllib.error.URLError("dns")), "download_failed")
        e = urllib.error.HTTPError("https://agentj.app/dl/x", 404, "nf", {}, None)
        self.assertEqual((upgrade_diag.plan_reason(e), upgrade_diag.plan_detail(e)), ("download_failed", "HTTPError HTTP 404"))
        e.close()
        self.assertEqual(upgrade_diag.plan_reason(TimeoutError()), "download_failed")
        self.assertEqual(upgrade_diag.plan_reason(ValueError("artifact_too_large")), "download_failed")
        for e in (ValueError("artifact_hash_mismatch"), ValueError("artifact_signature_mismatch"), minisign.MinisignError("bad"),
                  ValueError("artifact_identity_mismatch"), KeyError("METADATA")):
            self.assertEqual(upgrade_diag.plan_reason(e), "signature_or_hash_mismatch", repr(e))
        self.assertEqual(upgrade_diag.plan_reason(ValueError("unsupported_install")), "unsupported_install")
        self.assertEqual(upgrade_diag.plan_detail(urllib.error.URLError("proxy.corp.internal:3128 refused")), "URLError",
                         "never the exception's text (proxies, URLs, paths)")


class ApplyResult(_Env):
    def test_uv_failure_block_has_step_reason_and_redacted_stderr(self):
        run = Runner(fail="install")
        res = self.apply(run)
        self.assertEqual((res["result"], res["reason"], res["step"], res["exit"]), ("failed", "uv_failed", "install", 1))
        self.assertTrue(all(kw.get("capture_output") and kw.get("stdin") == subprocess.DEVNULL for _, kw in run.calls))
        block = update.result_block(res)
        self.assertIn("reason: uv_failed\n", block)
        self.assertIn("step: install\n", block)
        self.assertIn("stderr:   Caused by: failed to remove", block)
        self.assertNotIn("winuser", block.lower().replace("<user>", ""))
        self.assertIn("why: 安装命令失败", block)
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["from"], rec["to"], rec["result"], rec["reason"], rec["step"]), (__version__, NEW, "failed", "uv_failed", "install"))
        self.assertIn("Permission denied", rec["stderr_tail"])
        self.assertEqual(oct((self.st.root / upgrade_diag.LAST).stat().st_mode & 0o777), "0o600")

    def test_failed_rollback_names_the_recover_command(self):
        old = self.st.root / "upgrade-cache" / f"agentj-{__version__}-py3-none-any.whl"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"old")
        import hashlib
        old.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"old").hexdigest()}))
        run = Runner(fail="--force")           # the install and the rollback's reinstall both fail (container check: venv gone)
        with mock.patch.object(update, "install_kind", return_value=INFO), mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                mock.patch.object(update, "writable", return_value=True):
            res = update.apply(self.st, NEW, plan_fn=lambda st, info, target: update.commands(info, target),
                               check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=False,
                               rollback_fn=lambda st: old, fenced=False)
        self.assertEqual((res["reason"], res["rollback"]), ("uv_failed", "failed"))
        self.assertIn("tool install --force " + str(old), res["recover"])
        self.assertIn("recover: ", update.result_block(res))
        res["support"] = "off"
        self.assertIn("recover 命令", upgrade_diag.owner_sentence(res, {"status": "off"}))
        ok = self.apply(Runner())
        self.assertNotIn("recover", ok)

    def test_download_and_signature_failures_install_nothing(self):
        for exc, reason, detail in ((urllib.error.URLError("x"), "download_failed", "URLError"),
                                    (ValueError("artifact_signature_mismatch"), "signature_or_hash_mismatch", "ValueError: artifact_signature_mismatch")):
            run = Runner()

            def plan(st, info, target, exc=exc):
                raise exc
            res = self.apply(run, plan_fn=plan)
            self.assertEqual((res["reason"], res["step"], res["detail"], run.calls), (reason, "download", detail, []))
            self.assertIn(f"detail: {detail}", update.result_block(res))
            self.assertEqual(upgrade_diag.read(self.st)["reason"], reason)

    def test_service_restart_failure(self):
        run = Runner(fail="--deferred", err="Failed to connect to bus: No medium found\n")
        res = self.apply(run)
        self.assertEqual((res["reason"], res["step"], res["service"]), ("service_restart_failed", "service", "failed"))
        self.assertIn("stderr: Failed to connect to bus: No medium found", update.result_block(res))

    def test_success_and_refusals(self):
        res = self.apply(Runner())
        self.assertEqual(res["reason"], "upgraded")
        block = update.result_block(res)
        self.assertNotIn("step:", block)
        self.assertNotIn("stderr:", block)
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["result"], rec["reason"]), ("ok", "upgraded"))
        self.assertNotIn("step", rec)
        (self.st.root / upgrade_diag.LAST).unlink()
        with mock.patch.object(update, "writable", return_value=True):
            r = update.apply(self.st, NEW, check_fn=lambda: {"status": "current", "latest": NEW}, run=Runner(), fenced=False)
        self.assertEqual(r["reason"], "already_current")
        self.assertIsNone(upgrade_diag.read(self.st), "nothing attempted, nothing remembered")

    def test_authorized_apply_records_and_names_the_step(self):
        cloud.write_cloud(self.st, {"api": "http://127.0.0.1:9", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": int(time.time()), "last_seq": 0})
        run = Runner(fail="install")
        with mock.patch.object(update, "install_kind", return_value=INFO), mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                mock.patch.object(update, "writable", return_value=True):
            res = update.authorized_apply(self.st, "AJUP-11111-11111-11111-11111", NEW, plan_fn=lambda st, i, t: update.commands(i, t),
                                          check_fn=lambda: {"status": "newer", "latest": NEW}, auth_fn=lambda *a: {"status": "ok"},
                                          run=run, svc_on=True, rollback_fn=lambda st: None)
        self.assertEqual((res["reason"], res["step"]), ("uv_failed", "install"))
        self.assertIn("Permission denied", res["stderr_tail"])
        self.assertEqual(upgrade_diag.read(self.st)["reason"], "uv_failed")


class Report(_Env):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {upgrade_diag.REPORT_ENV: ""})     # _hermetic switches it off for every other test
        p.start()
        self.addCleanup(p.stop)

    def failed(self):
        res = self.apply(Runner(fail="install"))
        return res

    def test_report_once_with_metadata_and_redacted_tail(self):
        res = self.failed()
        sent = []

        def send(text):
            sent.append(text)
            return 5, "已发给客服 / sent to support: thread st_" + "A" * 22 + "\n"
        upgrade_diag.after_failure(self.st, res, lang="zh", send=send)
        self.assertEqual(len(sent), 1)
        body = sent[0]
        for part in ("[auto] agentj upgrade failed", f"to: {NEW}", "reason: uv_failed", "step: install", "install: ", "Permission denied"):
            self.assertIn(part, body)
        for leak in ("winuser", "DESKTOP-TEST123", "ghp_"):
            self.assertNotIn(leak, body)
        self.assertEqual(res["support"], "sent (st_" + "A" * 22 + ")")
        self.assertIn("已自动发给 Agent J 客服（st_" + "A" * 22 + "）", res["tell_owner"])
        block = update.result_block(res)
        self.assertIn("support: sent (st_", block)
        self.assertIn("tell_owner: 升级到 9.9.9 没成功（安装命令执行失败）", block)
        # the same target + reason again within a day: not reported twice
        res2 = self.failed()
        upgrade_diag.after_failure(self.st, res2, send=send)
        self.assertEqual((len(sent), res2["support"]), (1, "already sent (st_" + "A" * 22 + ")"))

    def test_real_support_path_redacts_and_attaches_doctor(self):
        from agentj import privacy, support
        fields = []

        class Ch:
            signed, st = True, None

            def send(self, f):
                fields.append(f)
                return 200, {"thread": "st_" + "C" * 22, "seq": 1}
        with mock.patch.object(support, "channel_for", return_value=Ch()), mock.patch.object(support, "remember"), \
                mock.patch.object(support, "doctor_text", return_value="agentj 0.20.3a1 · /ho" "me/winuser/x\n✗ service: down"), \
                mock.patch.object(privacy, "layer2", return_value=privacy.Verdict("unavailable")):
            res = upgrade_diag.after_failure(self.st, self.failed(), lang="zh")
        self.assertEqual(res["support"], "sent (st_" + "C" * 22 + ")")
        f = fields[0]
        self.assertEqual(f["kind"], "bug")
        self.assertIn("reason: uv_failed", f["body"])
        self.assertIn("service: down", f["attach"])
        for leak in ("winuser", "DESKTOP-TEST123"):
            self.assertNotIn(leak, f["body"] + f["attach"])

    def test_off_by_preference_and_env(self):
        cfg = pathlib.Path(os.environ["XDG_CONFIG_HOME"]) / "agentj"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json5").write_text('{"version": 1, "updates": {"report_failures": false}}')
        send = mock.Mock()
        res = upgrade_diag.after_failure(self.st, self.failed(), lang="en", send=send)
        send.assert_not_called()
        self.assertTrue(res["support"].startswith("off"))
        self.assertIn("automatic reporting is off", res["tell_owner"])
        (cfg / "config.json5").unlink()
        with mock.patch.dict(os.environ, {upgrade_diag.REPORT_ENV: "off"}):
            res = upgrade_diag.after_failure(self.st, self.failed(), send=send)
        send.assert_not_called()

    def test_send_failure_never_breaks_the_result(self):
        res = upgrade_diag.after_failure(self.st, self.failed(), send=mock.Mock(side_effect=OSError("down")))
        self.assertEqual(res["support"], "not_sent (OSError)")
        self.assertIn("agentj support ask --attach-doctor", res["tell_owner"])
        res = upgrade_diag.after_failure(self.st, self.failed(), send=lambda t: (2, "NOT SENT — layer 2"))
        self.assertEqual(res["support"], "not_sent (blocked)")
        self.assertEqual(res["exit"], 1)

    def test_success_is_not_reported(self):
        send = mock.Mock()
        res = upgrade_diag.after_failure(self.st, self.apply(Runner()), send=send)
        send.assert_not_called()
        self.assertNotIn("support", res)

    def test_cli_prints_support_and_owner_lines(self):
        res = {"from": __version__, "to": NEW, "service": "unchanged", "result": "failed", "reason": "download_failed", "exit": 1,
               "step": "download", "detail": "URLError"}
        out = io.StringIO()
        a = mock.Mock(mode="apply", authorization=None, from_email=None, version=NEW, json=False, yes=True)
        with mock.patch.object(update, "check", return_value={"status": "newer", "latest": NEW}), \
                mock.patch.object(update, "apply", return_value=res), \
                mock.patch.object(upgrade_diag, "report", return_value={"status": "sent", "thread": "st_" + "B" * 22}), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            cli.cmd_update(a)
        self.assertEqual(e.exception.code, 1)
        text = out.getvalue()
        self.assertIn("reason: download_failed", text)
        self.assertIn("step: download", text)
        self.assertIn("support: sent (st_", text)
        self.assertIn("tell_owner: 升级到 9.9.9 没成功（下载安装包失败，网络或 agentj.app 暂时连不上）；Agent J 主机仍是", text)


class Heartbeat(_Env):
    def link(self):
        cloud.write_cloud(self.st, {"api": "http://127.0.0.1:9", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": int(time.time()), "last_seq": 0})

    def test_report_field_metadata_only(self):
        inner = cloud.build_report(self.st, [], None, seq=1, ts=1)
        self.assertNotIn("last_upgrade", inner, "no attempt yet: the report is byte-identical to before")
        self.apply(Runner(fail="install"))
        inner = cloud.build_report(self.st, [], None, seq=2, ts=2)
        lu = inner["last_upgrade"]
        self.assertEqual(set(lu), {"from", "to", "result", "reason", "at"})
        self.assertEqual((lu["to"], lu["result"], lu["reason"]), (NEW, "failed", "uv_failed"))
        self.assertNotIn("Permission", json.dumps(inner))

    def test_older_server_retry_without_the_key(self):
        self.link()
        self.apply(Runner(fail="install"))
        cloud._NO_UPGRADE.clear()
        seen = []

        def post(url, env):
            inner = json.loads(base64.urlsafe_b64decode(env["body"] + "=" * (-len(env["body"]) % 4)))
            seen.append("last_upgrade" in inner)
            if "last_upgrade" in inner:
                return 400, {"error": "bad_request"}
            return 200, {"ok": True}
        r = cloud.send_report(self.st, [], None, post=post)
        self.assertEqual(r.kind, "ok")
        self.assertEqual(seen, [True, False])
        seen.clear()
        cloud.send_report(self.st, [], None, post=post)
        self.assertEqual(seen, [False], "remembered for a day for that server")
        cloud._NO_UPGRADE.clear()


class HostRecord(_Env):
    def test_host_op_record_validates(self):
        rec = upgrade_diag.entry({"from": __version__, "to": NEW, "result": "failed", "reason": "uv_failed", "step": "install",
                                  "stderr_tail": "error at /ho" "me/winuser/x"})
        self.assertEqual(update.host_op(self.st, {"op": "record", "rec": rec}, {}), {"ok": True})
        got = upgrade_diag.read(self.st)
        self.assertEqual((got["reason"], got["step"]), ("uv_failed", "install"))
        self.assertNotIn("winuser", got["stderr_tail"])
        for bad in ({"op": "record"}, {"op": "record", "rec": {**rec, "result": "maybe"}}, {"op": "record", "rec": {**rec, "to": "../x"}}):
            self.assertEqual(update.host_op(self.st, bad, {}), {"ok": False, "why": "record"})

    def test_fenced_apply_hands_the_record_to_the_host(self):
        calls = []

        def host(st, op, timeout=60, **kw):
            calls.append((op, kw))
            return None if op != "record" else {"ok": True}
        with mock.patch.object(update, "_host", side_effect=host), mock.patch.object(update, "install_kind", return_value=INFO), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), mock.patch.object(update, "writable", return_value=True):
            update.apply(self.st, NEW, plan_fn=lambda st, i, t: update.commands(i, t), check_fn=lambda: {"status": "newer", "latest": NEW},
                         run=Runner(fail="install"), svc_on=False, fenced=True)
        rec = [kw for op, kw in calls if op == "record"]
        self.assertEqual(len(rec), 1)
        self.assertEqual((rec[0]["to"], rec[0]["rec"]["reason"]), (NEW, "uv_failed"))


class Worker(_Env):
    def test_nightly_install_failure_is_remembered_and_reported(self):
        old = self.st.root / "old.whl"
        new = self.st.root / "new.whl"
        for p in (old, new):
            p.write_bytes(b"w")
            import hashlib
            p.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"w").hexdigest()}))
        job = {"attempt": "a", "from": __version__, "to": NEW, "install": INFO, "argv": ["agentj"], "old": str(old),
               "new": str(new), "phase": "installed", "when": "night"}
        auto_update.write(self.st, job, auto_update.JOB)

        def runner(argv):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {__version__}", "")
            if "doctor" in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": [{"id": "version", "status": "ok"}]}), "")
            if "install" in argv and str(old) in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 1, "", "")
        sent = []
        with mock.patch.object(upgrade_diag, "after_failure", side_effect=lambda st, res, **kw: sent.append(res) or res), \
                mock.patch("agentj.phone_update.release"), mock.patch("agentj.service_recovery.cleanup_job"):
            r = auto_update.run(self.st, runner=runner, restart=lambda v: None)
        self.assertEqual(r["result"], "rolled_back")
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["to"], rec["result"], rec["reason"], rec["step"]), (NEW, "rolled_back", "not_upgraded", "verify"))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["rollback"], "ok")

    def test_categories(self):
        for raw, cat in (("install_failed", "uv_failed"), ("restart_failed", "service_restart_failed"), ("URLError", "download_failed"),
                         ("artifact_signature_mismatch", "signature_or_hash_mismatch"), ("Weird Thing", "unknown"), (None, "unknown")):
            self.assertEqual(upgrade_diag.category(raw), cat)


class Config(unittest.TestCase):
    def test_preference_is_a_user_key_default_on(self):
        from agentj import preferences
        self.assertIs(preferences.get(preferences.defaults(), "updates.report_failures"), True)
        meta = preferences.SCHEMA["updates.report_failures"]
        self.assertEqual((meta["tier"], meta["type"], meta["default"]), ("user", "boolean", True))


if __name__ == "__main__":
    unittest.main()
