"""P114 (P90 design, ADR-A194): Agent J's own browser, read-only sign-in checks, the login QR card, good morning."""
import _hermetic  # noqa: F401
import asyncio
import base64
import datetime as dt
import hashlib
import http.server
import io
import json
import os
import pathlib
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch

from agentj import browser, browser_sites as bs, browser_login as bl, browser_cdp, elevate, doctor, main_identity, preferences
from agentj.state import State

ROOT = pathlib.Path(__file__).resolve().parents[1] / "agentj"


def chrome_path():
    for p in (os.environ.get("AGENTJ_TEST_CHROME"), "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.root.mkdir(mode=0o700)


# ---------------------------------------------------------------- manifest + download
def _zip(files: dict, links: dict | None = None) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, (data, mode) in files.items():
            i = zipfile.ZipInfo(name); i.external_attr = (stat.S_IFREG | mode) << 16
            z.writestr(i, data)
        for name, target in (links or {}).items():
            i = zipfile.ZipInfo(name); i.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(i, target)
    return b.getvalue()


class FakeOpener:
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.seen.append(url)
        body = self.routes.get(url)
        if isinstance(body, Exception):
            raise body
        if body is None:
            raise OSError("404")
        return io.BytesIO(body)


class Manifest(Base):
    def test_signed_manifest_covers_four_platforms_with_real_hashes(self):
        m = browser.manifest()
        self.assertEqual(set(m["builds"]), {"linux-x64", "linux-arm64", "mac-arm64", "mac-x64"})
        for k, b in m["builds"].items():
            self.assertTrue(b["url"].startswith("https://cdn.playwright.dev/builds/cft/" + m["version"] + "/"), k)
            self.assertTrue(b["url"].endswith("/" + b["path"]))
            self.assertRegex(b["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(b["size"], 100_000_000)
        self.assertEqual(m["approved_mirrors"], [])          # the mainland mirror is Jarvis's decision (design item 2)
        self.assertGreaterEqual(m["min_free_bytes"], 1_500_000_000)

    def _fake_manifest(self, data, url="https://cdn.example/b.zip", mirrors=()):
        m = browser.manifest()
        key = browser.platform_key()
        m["builds"][key] = {"url": url, "path": "p/b.zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                            "executable": ["pkg", "chrome"]}
        m["approved_mirrors"] = list(mirrors)
        return m

    def test_install_verifies_unpacks_keeps_exec_bits_and_symlinks_idempotent(self):
        data = _zip({"pkg/chrome": (b"#!/bin/sh\n", 0o755), "pkg/lib/a.so": (b"x", 0o644)}, {"pkg/lib/b.so": "a.so"})
        op = FakeOpener({"https://cdn.example/b.zip": data})
        with patch.object(browser, "manifest", return_value=self._fake_manifest(data)):
            r = browser.install(self.st, opener=op)
            self.assertEqual(r["result"], "installed")
            have = browser.installed(self.st)
            self.assertTrue(os.access(have["path"], os.X_OK))
            self.assertTrue(os.path.islink(pathlib.Path(have["path"]).parent / "lib" / "b.so"))
            self.assertEqual(oct(os.stat(browser.install_path(self.st)).st_mode & 0o777), "0o600")
            self.assertEqual(browser.install(self.st, opener=op)["result"], "present")
            self.assertEqual(len(op.seen), 1)

    def test_corrupt_download_is_discarded_and_nothing_installed(self):
        data = _zip({"pkg/chrome": (b"#!/bin/sh\n", 0o755)})
        m = self._fake_manifest(data)
        bad = FakeOpener({"https://cdn.example/b.zip": data[:-1] + b"X"})
        with patch.object(browser, "manifest", return_value=m):
            with self.assertRaises(browser.BrowserError) as e:
                browser.install(self.st, opener=bad)
            self.assertEqual(e.exception.reason, "download_corrupt")
        self.assertIsNone(browser.installed(self.st))
        self.assertEqual([p.name for p in browser.bdir(self.st).iterdir() if p.name.startswith(".")], [])

    def test_mirror_serves_same_bytes_only_after_official_fails(self):
        data = _zip({"pkg/chrome": (b"#!/bin/sh\n", 0o755)})
        m = self._fake_manifest(data, mirrors=["https://mirror.example/base"])
        op = FakeOpener({"https://cdn.example/b.zip": OSError("blocked"), "https://mirror.example/base/p/b.zip": data})
        with patch.object(browser, "manifest", return_value=m):
            self.assertEqual(browser.install(self.st, opener=op)["result"], "installed")
        self.assertEqual(op.seen, ["https://cdn.example/b.zip", "https://mirror.example/base/p/b.zip"])

    def test_approved_mirror_without_any_mirror_is_refused(self):
        data = _zip({"pkg/chrome": (b"#!/bin/sh\n", 0o755)})
        with patch.object(browser, "manifest", return_value=self._fake_manifest(data)), \
                patch.object(browser, "settings", return_value={"enabled": True, "download_source": "approved-mirror", "mirror": "", "check_time": "08:00"}):
            with self.assertRaises(browser.BrowserError) as e:
                browser.install(self.st, opener=FakeOpener({}))
            self.assertEqual(e.exception.reason, "no_source")

    def test_path_traversal_and_escaping_symlinks_are_rejected(self):
        for files, links in (({"../evil": (b"x", 0o644)}, None), ({"pkg/chrome": (b"x", 0o755)}, {"pkg/l": "/etc/passwd"})):
            data = _zip(files, links)
            with patch.object(browser, "manifest", return_value=self._fake_manifest(data)):
                with self.assertRaises(browser.BrowserError) as e:
                    browser.install(self.st, opener=FakeOpener({"https://cdn.example/b.zip": data}))
                self.assertEqual(e.exception.reason, "extract_failed")
            self.assertFalse((pathlib.Path(self.tmp.name) / "evil").exists())

    def test_no_space_and_disabled_never_download(self):
        data = _zip({"pkg/chrome": (b"x", 0o755)})
        op = FakeOpener({"https://cdn.example/b.zip": data})
        with patch.object(browser, "manifest", return_value=self._fake_manifest(data)), \
                patch.object(browser.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10, 9, 1)):
            r = browser.setup(self.st, opener=op)
            self.assertEqual((r["ok"], r["reason"]), (False, "no_space"))
        with patch.object(browser, "settings", return_value={"enabled": False, "download_source": "auto", "mirror": "", "check_time": "08:00"}):
            self.assertEqual(browser.setup(self.st, opener=op)["result"], "disabled")
        self.assertEqual(op.seen, [])

    def test_unsupported_platforms(self):
        with patch.object(browser, "system", return_value="wsl1"):
            self.assertIsNone(browser.platform_key())
        with patch.object(browser._platform, "machine", return_value="riscv64"):
            self.assertIsNone(browser.platform_key())


# ---------------------------------------------------------------- the resident process
FAKE_CHROME = r'''#!{py}
import http.server, json, os, sys, socketserver
prof = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--user-data-dir=")][0]
addr = "0.0.0.0" if os.environ.get("FAKE_BIND_ALL") else [a.split("=", 1)[1] for a in sys.argv if a.startswith("--remote-debugging-address=")][0]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        port = self.server.server_address[1]
        b = json.dumps({{"Browser": "Fake/1", "webSocketDebuggerUrl": "ws://127.0.0.1:%d/devtools/browser/x" % port}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
s = http.server.HTTPServer((addr, 0), H)
open(os.path.join(prof, "DevToolsActivePort"), "w").write("%d\n/devtools/browser/x\n" % s.server_address[1])
s.serve_forever()
'''


class Runtime(Base):
    def _fake(self):
        exe = pathlib.Path(self.tmp.name) / "chrome"
        exe.write_text(FAKE_CHROME.format(py=sys.executable)); exe.chmod(0o755)
        return {"version": "t", "platform": "linux-x64", "path": str(exe)}

    @unittest.skipUnless(sys.platform.startswith("linux"), "uses /proc/net/tcp")
    def test_start_verifies_loopback_pid_and_starttime(self):
        browser.ensure_dir(self.st)
        with patch.object(browser, "installed", return_value=self._fake()):
            proc, rec = browser.start_browser(self.st, headless=True)
            try:
                self.assertEqual(browser.listen_addresses(rec["port"], proc.pid), {"127.0.0.1"})
                rt = browser.runtime(self.st)
                self.assertTrue(rt and rt["ws"].startswith("ws://127.0.0.1:"))
                self.assertEqual(oct(os.stat(browser.runtime_path(self.st)).st_mode & 0o777), "0o600")
                saved = json.loads(browser.runtime_path(self.st).read_text())
                saved["starttime"] = "0"                  # a reused pid is not our browser
                browser.runtime_path(self.st).write_text(json.dumps(saved))
                self.assertIsNone(browser.runtime(self.st))
            finally:
                browser.stop_proc(proc)

    @unittest.skipUnless(sys.platform.startswith("linux"), "uses /proc/net/tcp")
    def test_a_debugging_port_on_all_addresses_is_stopped(self):
        browser.ensure_dir(self.st)
        with patch.object(browser, "installed", return_value=self._fake()), patch.dict(os.environ, {"FAKE_BIND_ALL": "1"}):
            with self.assertRaises(browser.BrowserError) as e:
                browser.start_browser(self.st, headless=True)
        self.assertEqual(e.exception.reason, "not_loopback")
        self.assertFalse(browser.runtime_path(self.st).exists())

    def test_launch_args_never_weaken_the_browser(self):
        args = browser._launch_args("/x/chrome", pathlib.Path("/p"), True)
        for bad in ("--no-sandbox", "--password-store=basic", "--remote-allow-origins", "0.0.0.0"):
            self.assertFalse(any(bad in a for a in args), bad)
        self.assertIn("--remote-debugging-port=0", args)
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertIn("--headless=new", args)
        self.assertNotIn("--headless=new", browser._launch_args("/x/chrome", pathlib.Path("/p"), False))

    def test_restricted_userns_uses_an_apparmor_allowed_system_browser_or_asks_for_the_phone_fix(self):
        aa = pathlib.Path(self.tmp.name) / "apparmor.d"; aa.mkdir()
        sysbin = pathlib.Path(self.tmp.name) / "opt-chrome"; sysbin.write_text("#!/bin/sh\n"); sysbin.chmod(0o755)
        have = {"path": str(pathlib.Path(self.tmp.name) / "state/browser/bin/1/chrome-linux64/chrome")}
        with patch.object(browser, "system", return_value="linux"), patch.object(browser, "userns_restricted", return_value=True), \
                patch.object(browser, "APPARMOR_DIR", aa), patch.object(browser, "SYSTEM_BROWSERS", (str(sysbin),)):
            with self.assertRaises(browser.BrowserError) as e:                 # nothing allowed → never --no-sandbox
                browser.choose_executable(self.st, have)
            self.assertEqual(e.exception.reason, "sandbox_blocked")
            (aa / "chrome").write_text(f"profile chrome {sysbin} flags=(unconfined) {{\n  userns,\n}}\n")
            self.assertEqual(browser.choose_executable(self.st, have), (str(sysbin), "system"))
            path, text = browser.apparmor_profile(self.st)
            self.assertTrue(path.startswith(str(aa)))
            (aa / "mine").write_text(text)
            self.assertEqual(browser.choose_executable(self.st, {"path": str(self.st.root.resolve() / "browser/bin/9/chrome-linux64/chrome")})[1], "bundled")
        with patch.object(browser, "userns_restricted", return_value=False):
            self.assertEqual(browser.choose_executable(self.st, have), (have["path"], "bundled"))

    def test_sandbox_fix_goes_through_the_phone_password_card(self):
        sent = []
        with patch.object(browser, "system", return_value="linux"), patch.object(browser, "userns_restricted", return_value=True), \
                patch.object(browser, "apparmor_allows", return_value=False), \
                patch.object(elevate, "client_request", side_effect=lambda st, req, *a, **k: sent.append(req) or {"result": "denied"}):
            r = browser.sandbox_fix(self.st)
        self.assertFalse(r["ok"]); self.assertEqual(r["result"], "denied")
        self.assertEqual(sent[0]["t"], "sudo"); self.assertIn("apparmor_parser -r", sent[0]["argv"][2])
        self.assertIn("userns,", sent[0]["argv"][4]); self.assertNotIn("no-sandbox", json.dumps(sent))

    def test_units_are_loopback_supervisors_with_backoff(self):
        u = browser.unit_text(["/venv/bin/agentj"], {"PATH": "/usr/bin", "AGENTJ_STATE_DIR": "/s"})
        self.assertIn('ExecStart="/venv/bin/agentj" "browser" "run"', u)
        self.assertIn("Restart=on-failure", u)
        self.assertIn("After=graphical-session.target", u)
        p = __import__("plistlib").loads(browser.plist_bytes("net.agentj.host.browser", ["/a"], "/l"))
        self.assertEqual(p["ProgramArguments"], ["/a", "browser", "run"])
        self.assertEqual(p["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(p["LimitLoadToSessionType"], "Aqua")


# ---------------------------------------------------------------- templates + probes
class Templates(Base):
    def test_every_template_is_complete_and_https(self):
        for k, t in bs.templates().items():
            for f in ("title", "origins", "login_url", "check_url", "in_url", "out_url", "login_method"):
                self.assertIn(f, t, k)
            self.assertTrue(all(o.startswith("https://") for o in t["origins"]))
            self.assertTrue(t["check_url"].startswith("https://") and t["login_url"].startswith("https://"))
            self.assertEqual(set(t["title"]), {"zh", "en"})
            if t["login_method"] == "qr":
                self.assertLessEqual(t["qr"]["ttl"], 120)

    def test_classify_known_urls(self):
        T = bs.templates()
        cases = [("gmail", "https://mail.google.com/mail/u/0/#inbox", "in"),
                 ("gmail", "https://accounts.google.com/v3/signin/identifier?x", "out"),
                 ("gmail", "https://workspace.google.com/intl/en/gmail/", "out"),
                 ("google", "https://myaccount.google.com/intro", "out"),
                 ("google", "https://myaccount.google.com/?pli=1", "in"),
                 ("google", "https://www.google.com/account/about/?hl=en-US", "out"),
                 ("bilibili", "https://passport.bilibili.com/login?gourl=x", "out"),
                 ("bilibili", "https://member.bilibili.com/platform/home", "in"),
                 ("mp-weixin", "https://mp.weixin.qq.com/", "out"),
                 ("mp-weixin", "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN&token=1", "in"),
                 ("channels-weixin", "https://channels.weixin.qq.com/login.html", "out"),
                 ("channels-weixin", "https://channels.weixin.qq.com/platform", "in"),
                 ("douyin-creator", "https://creator.douyin.com/", "out"),
                 ("youtube-studio", "https://studio.youtube.com/channel/UCx", "in"),
                 ("gmail", "https://evil.example/mail/", "offsite"),
                 ("gmail", "chrome-error://chromewebdata/", "error")]
        for site, url, want in cases:
            self.assertEqual(bs.classify(url, T[site]), want, (site, url))


class FakeCDP:
    """Scripted URL timeline per tab; records tabs it created and closed."""
    def __init__(self, timeline, dom=False):
        self.timeline, self.dom, self.t = timeline, dom, 0.0
        self.created, self.closed = [], []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def new_tab(self, url, background=True):
        self.created.append(url); self.t0 = self.t; return f"T{len(self.created)}"

    def attach(self, target):
        return "S"

    def close_tab(self, target):
        self.closed.append(target)

    def call(self, method, params=None, session=None, timeout=None):
        if method == "Runtime.evaluate" and "querySelector" in params["expression"]:
            return {"result": {"value": self.dom}}
        return {}

    def location(self, session):
        el = self.t - self.t0
        cur = self.timeline[0]
        for at, url, ready in self.timeline:
            if el >= at:
                cur = (at, url, ready)
        return cur[1], cur[2]


class Probe(Base):
    def run_probe(self, site, timeline, dom=False):
        f = FakeCDP(timeline, dom)
        got = bs.probe(f, bs.templates()[site], bs.templates()[site]["check_url"], clock=f.clock, sleep=f.sleep)
        self.assertEqual(f.closed, ["T1"])              # its own tab, closed in finally
        return got

    def test_late_redirect_to_sign_in_is_never_a_false_signed_in(self):
        tl = [(0, "https://channels.weixin.qq.com/platform", "loading"), (1.2, "https://channels.weixin.qq.com/platform", "complete"),
              (1.5, "https://channels.weixin.qq.com/login.html", "interactive"), (2.7, "https://channels.weixin.qq.com/login.html", "complete")]
        self.assertEqual(self.run_probe("channels-weixin", tl), ("auth_required", "signed_out"))

    def test_stable_app_url_is_signed_in(self):
        tl = [(0, "https://mp.weixin.qq.com/", "loading"), (0.8, "https://mp.weixin.qq.com/cgi-bin/home?t=1&token=9", "complete")]
        self.assertEqual(self.run_probe("mp-weixin", tl), ("logged_in", "ok"))

    def test_app_url_with_a_sign_in_form_on_screen_is_signed_out(self):
        tl = [(0, "https://creator.douyin.com/creator-micro/home", "complete")]
        self.assertEqual(self.run_probe("douyin-creator", tl, dom=True), ("auth_required", "signed_out"))
        self.assertEqual(self.run_probe("douyin-creator", tl, dom=False), ("logged_in", "ok"))

    def test_signed_in_must_hold_five_seconds(self):
        tl = [(0, "https://channels.weixin.qq.com/platform", "complete"), (4.0, "https://channels.weixin.qq.com/login.html", "complete")]
        self.assertEqual(self.run_probe("channels-weixin", tl), ("auth_required", "signed_out"))

    def test_network_error_and_timeout_are_not_signed_out(self):
        self.assertEqual(self.run_probe("gmail", [(0, "chrome-error://chromewebdata/", "complete")]), ("unavailable", "offline"))
        self.assertEqual(self.run_probe("gmail", [(0, "https://mail.google.com/mail/u/0/", "loading")]), ("unknown", "timeout"))
        self.assertEqual(self.run_probe("gmail", [(0, "https://other.example/", "complete")]), ("unknown", "offsite"))


class Sites(Base):
    def test_add_custom_list_remove_and_never_store_account_data(self):
        self.assertTrue(bs.add(self.st, "gmail")["ok"])
        self.assertFalse(bs.add(self.st, "custom", origin="http://x.example", url="http://x.example/a")["ok"])
        r = bs.add(self.st, "custom", origin="https://shop.example", url="https://shop.example/admin", title="Shop")
        self.assertEqual(r["site"], "custom-shop-example")
        self.assertFalse(bs.add(self.st, "nope")["ok"])
        d = json.loads(bs.sites_path(self.st).read_text())
        self.assertEqual(oct(os.stat(bs.sites_path(self.st)).st_mode & 0o777), "0o600")
        for rec in d["sites"].values():
            self.assertEqual(rec["automation_refs"], [])
            self.assertFalse({"email", "user", "cookie", "password", "token"} & set(rec))
        self.assertTrue(bs.remove(self.st, "gmail")["ok"])
        self.assertEqual([x["site"] for x in bs.summary(self.st)], ["custom-shop-example"])

    def test_check_records_status_audit_without_urls_and_custom_is_unknown(self):
        bs.add(self.st, "mp-weixin"); bs.add(self.st, "custom", origin="https://a.example", url="https://a.example/x")
        with patch.object(bs, "probe", return_value=("auth_required", "signed_out")):
            r = bs.check(self.st, _cdp=object())
        got = {x["site"]: x["status"] for x in r["sites"]}
        self.assertEqual(got, {"mp-weixin": "auth_required", "custom-a-example": "unknown"})
        log = (browser.bdir(self.st) / "checks.jsonl").read_text()
        self.assertNotIn("http", log)
        self.assertEqual({c["to"] for c in r["changed"]}, {"auth_required"})

    def test_retries_twice_on_unknown_then_gives_up_honestly(self):
        bs.add(self.st, "gmail")
        with patch.object(bs, "probe", return_value=("unknown", "timeout")) as p:
            r = bs.check(self.st, _cdp=object())
        self.assertEqual(p.call_count, 3)
        self.assertEqual(r["sites"][0]["status"], "unknown")

    def test_if_stale_skips_fresh_signed_in_sites(self):
        bs.add(self.st, "gmail"); bs.add(self.st, "bilibili")
        with patch.object(bs, "probe", return_value=("logged_in", "ok")):
            bs.check(self.st, _cdp=object())
        with patch.object(bs, "probe", return_value=("logged_in", "ok")) as p:
            self.assertEqual(bs.check(self.st, if_stale=True, _cdp=object())["checked"], 0)
            p.assert_not_called()

    def test_no_browser_is_unavailable_not_signed_out(self):
        bs.add(self.st, "gmail")
        with patch.object(browser, "runtime", return_value=None):
            r = bs.check(self.st)
        self.assertEqual((r["sites"][0]["status"], r["sites"][0]["reason"]), ("unavailable", "no_browser"))

    def test_registry_projection_matches_p86_shape(self):
        bs.add(self.st, "gmail"); bs.add(self.st, "channels-weixin")
        d = bs.load(self.st)
        d["sites"]["gmail"].update(status="logged_in", checked_at=int(time.time()))
        d["sites"]["channels-weixin"].update(status="auth_required", checked_at=int(time.time()))
        bs._save(self.st, d)
        with patch.object(browser, "runtime", return_value={"ws": "x"}):
            reg = {e["id"]: e for e in bs.registry(self.st)}
        self.assertEqual(reg["browser-runtime"]["status"], "ready")
        self.assertEqual((reg["browser-gmail"]["status"], reg["browser-gmail"]["check"]["status"]), ("ready", "ok"))
        self.assertEqual((reg["browser-channels-weixin"]["status"], reg["browser-channels-weixin"]["check"]["status"]),
                         ("unavailable", "auth_required"))
        for e in reg.values():
            self.assertIsNone(e["credential_name"]); self.assertEqual(e["kind"], "browser")
            self.assertRegex(e["check"]["source_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(reg["browser-gmail"]["depends_on"], ["browser-runtime"])
        d["sites"]["gmail"]["checked_at"] = int(time.time()) - bs.FRESH - 5
        bs._save(self.st, d)
        with patch.object(browser, "runtime", return_value={"ws": "x"}):
            self.assertEqual({e["id"]: e for e in bs.registry(self.st)}["browser-gmail"]["status"], "stale")
        with patch.object(browser, "runtime", return_value=None):
            self.assertTrue(all(e["status"] == "unavailable" for e in bs.registry(self.st)))

    def test_daily_check_clock_once_a_day_with_catch_up(self):
        tz = dt.timezone(dt.timedelta(hours=9))
        self.assertFalse(bs.daily_due(self.st, dt.datetime(2026, 10, 10, 7, 59, tzinfo=tz)))
        self.assertTrue(bs.daily_due(self.st, dt.datetime(2026, 10, 10, 8, 0, tzinfo=tz)))
        bs.daily_done(self.st, dt.datetime(2026, 10, 10, 8, 1, tzinfo=tz))
        self.assertFalse(bs.daily_due(self.st, dt.datetime(2026, 10, 10, 23, 0, tzinfo=tz)))
        self.assertTrue(bs.daily_due(self.st, dt.datetime(2026, 10, 11, 14, 0, tzinfo=tz)))   # slept through 08:00: once

    def test_quiet_hours(self):
        self.assertTrue(bl.quiet(dt.datetime(2026, 10, 10, 23, 30)))
        self.assertTrue(bl.quiet(dt.datetime(2026, 10, 10, 6, 59)))
        self.assertFalse(bl.quiet(dt.datetime(2026, 10, 10, 8, 0)))

    def test_telegram_qr_consent_can_only_be_revoked_by_the_agent(self):
        self.assertFalse(bs.tg_consent(self.st))
        self.assertEqual(bs.telegram_qr(self.st, "on")["reason"], "owner_only")
        self.assertFalse(bs.tg_consent(self.st))
        bs.set_tg_consent(self.st, True, "dev1")
        self.assertTrue(bs.tg_consent(self.st))
        self.assertTrue(bs.telegram_qr(self.st, "off")["ok"])
        self.assertFalse(bs.tg_consent(self.st))


# ---------------------------------------------------------------- the product surfaces
class Surfaces(Base):
    def test_config_keys_defaults_and_validation(self):
        d = preferences.defaults()["browser"]
        self.assertEqual(d, {"enabled": True, "download_source": "auto", "mirror": "", "check_time": "08:00"})
        for bad in ({"browser": {"check_time": "8am"}}, {"browser": {"mirror": "http://x"}}, {"browser": {"download_source": "any"}}):
            with self.assertRaises(preferences.ConfigError):
                preferences.validate(bad)
        preferences.validate({"browser": {"enabled": False, "mirror": "https://m.example/cft", "check_time": "07:30"}})

    def test_identity_line_both_languages(self):
        for lang, needles in (("zh", ("agentj-good-morning", "agentj browser check", "二维码只走手机登录卡", "登录不等于授权")),
                              ("en", ("agentj-good-morning", "agentj browser check", "never into chat", "not permission"))):
            t = main_identity.prompt({"language": lang})
            for n in needles:
                self.assertIn(n, t)
        main_identity.verify_core()                       # the hashed core is untouched

    def test_skills_ship_and_say_the_right_commands(self):
        b = (ROOT / "skills/agentj-browser/SKILL.md").read_text()
        g = (ROOT / "skills/agentj-good-morning/SKILL.md").read_text()
        for n in ("name: agentj-browser", "agentj browser login", "--if-stale", "connect_over_cdp", "telegram-qr status|off",
                  "never claim success"):
            self.assertIn(n, b)
        for n in ("name: agentj-good-morning", "早上好", "good morning", "agentj browser check", "never permission to publish",
                  "Never promise they stay signed in all day"):
            self.assertIn(n, g)

    def test_cli_parses_every_subcommand(self):
        import argparse
        p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd")
        browser.add_parser(sub)
        for argv in (["browser", "setup"], ["browser", "status", "--registry"], ["browser", "check", "--site", "gmail", "--if-stale"],
                     ["browser", "login", "bilibili", "--wait"], ["browser", "sites", "add", "custom", "--origin", "https://a.b", "--url", "https://a.b/x"],
                     ["browser", "telegram-qr", "off"], ["browser", "open", "https://a.b"], ["browser", "endpoint"]):
            p.parse_args(argv)
        with self.assertRaises(SystemExit):
            p.parse_args(["browser", "telegram-qr", "on"])

    def test_doctor_row_never_fails(self):
        with patch.object(browser, "status", return_value={"state": "attention", "attention": ["not_installed"], "hints": ["h"]}):
            self.assertEqual(doctor.check_browser(self.st)["status"], "warn")
        with patch.object(browser, "status", return_value={"state": "disabled"}):
            self.assertEqual(doctor.check_browser(self.st)["status"], "ok")

    def test_phone_types_route_through_the_elevator(self):
        for t in bl.PHONE_TYPES:
            self.assertIn(t, elevate.PHONE_TYPES)

    def test_find_qr_expression_is_fixed_and_selectors_are_json(self):
        expr = browser_cdp._FIND_QR % json.dumps(["a'b", "c"])
        self.assertIn('["a\'b", "c"]', expr)
        self.assertNotIn("eval(", expr)


# ---------------------------------------------------------------- the login card inside a fake serve
class FakeHost:
    def __init__(self, st, approvers=("dev1",)):
        self.st, self.channel, self.lang = st, "chan", "zh"
        self.sent, self.hist, self.pushed = [], [], []
        self.approvers = list(approvers)
        self.telegram = None

    def stopped(self):
        return False

    def _approvers(self):
        return self.approvers

    async def _send_ready(self, fn):
        self.sent.append(fn(None))

    async def send_app(self, s, obj):
        self.sent.append(obj); return True

    def push_notify(self, kind):
        self.pushed.append(kind)

    def hist_add(self, src, reply="", end="done", card=None):
        self.hist.append(reply)


class FakeElev:
    def __init__(self, host):
        self.host, self.st = host, host.st


class Sess:
    def __init__(self, device):
        self.device = device


class Fixture:
    """A local QR sign-in site: /app redirects to /login until the test "scans" (a cookie set by the page's poll)."""
    def __init__(self):
        self.scanned = False
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=()):
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                signed = "sid=ok" in (self.headers.get("Cookie") or "")
                if self.path.startswith("/app"):
                    return self._send(200, b"<html><body>app</body></html>") if signed else \
                        self._send(302, headers=[("Location", "/login")])
                if self.path.startswith("/login"):
                    return self._send(200, LOGIN_PAGE, [("Content-Type", "text/html")])
                if self.path.startswith("/poll"):
                    if outer.scanned:
                        return self._send(200, b'{"ok":true}', [("Set-Cookie", "sid=ok; Path=/"), ("Content-Type", "application/json")])
                    return self._send(200, b'{"ok":false}', [("Content-Type", "application/json")])
                self._send(404)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.origin = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def template(self):
        o = self.origin.replace(".", "\\.")
        return {"title": {"zh": "测试站", "en": "Test site"}, "kind": "cn", "origins": [self.origin], "login_url": self.origin + "/login",
                "check_url": self.origin + "/app", "in_url": ["^" + o + "/app"], "out_url": ["^" + o + "/login"],
                "login_method": "qr", "qr": {"selectors": ["canvas.qrcode"], "ttl": 60}}


LOGIN_PAGE = b"""<!doctype html><html><body><h1>Sign in</h1><p>account: someone@example.com</p>
<canvas class="qrcode" width="200" height="200"></canvas>
<script>const c=document.querySelector('canvas').getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,200,200);c.fillStyle='#000';
for(let i=0;i<20;i++)for(let j=0;j<20;j++)if((i*7+j*3)%5<2)c.fillRect(i*10,j*10,10,10);
setInterval(async()=>{const r=await fetch('/poll');const j=await r.json();if(j.ok)location='/app'},300);</script></body></html>"""


@unittest.skipUnless(chrome_path(), "needs a Chromium (AGENTJ_TEST_CHROME)")
class RealBrowser(unittest.IsolatedAsyncioTestCase):
    """End to end against a real Chromium: probe, QR card, scan, the browser's own confirmation, check → logged_in."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state"); self.st.root.mkdir(mode=0o700)
        self.fx = Fixture(); self.addCleanup(self.fx.srv.shutdown)
        tpl = dict(bs.templates()); tpl["fixture"] = self.fx.template()
        for target, value in ((bs, "templates"), ):
            p = patch.object(target, value, return_value=tpl); p.start(); self.addCleanup(p.stop)
        p = patch.object(browser, "installed", return_value={"version": "sys", "platform": "linux-x64", "path": chrome_path()})
        p.start(); self.addCleanup(p.stop)
        browser.ensure_dir(self.st)
        self.proc, _ = browser.start_browser(self.st, headless=True)
        self.addCleanup(browser.stop_proc, self.proc)

    async def test_qr_card_scan_confirmed_by_the_browser_then_check(self):
        bs.add(self.st, "fixture")
        r = await asyncio.to_thread(bs.check, self.st, ["fixture"])
        self.assertEqual(r["sites"][0]["status"], "auth_required")
        host = FakeHost(self.st)
        lc = bl.LoginCards(FakeElev(host))
        res = await lc.request({"site": "fixture"})
        self.assertEqual(res["result"], "sent", res)
        card = next(m for m in host.sent if m.get("t") == "login_qr")
        png = base64.b64decode(card["img"])
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertLess(len(png), 60000)                  # the QR area only, not a full-page screenshot
        self.assertNotIn("img", json.dumps(lc.result({"id": res["id"]})))
        again = await lc.request({"site": "fixture"})     # a repeated good morning reuses the open card
        self.assertEqual((again["result"], again["id"], again.get("reused")), ("sent", res["id"], True))
        # "I scanned it" before the scan is not success
        with patch.object(State, "sign_key", return_value=b"k" * 32):
            await lc.on_phone(Sess("dev1"), {"t": "login_qr_check", "id": res["id"], "n": card["n"]})
        self.assertEqual(host.sent[-1], {"t": "login_qr_state", "id": res["id"], "state": "waiting"})
        self.fx.scanned = True
        end = time.monotonic() + 90            # watcher (2 s) + the full trusted probe (5 s stable); slow under parallel gates
        while lc.cards and time.monotonic() < end:
            await asyncio.sleep(0.5)
        self.assertFalse(lc.cards)
        done = [m for m in host.sent if m.get("t") == "login_qr_done"]
        self.assertEqual(done[-1]["result"], "done")
        self.assertEqual({x["site"]: x["status"] for x in bs.summary(self.st)}["fixture"], "logged_in")
        self.assertTrue(any("已登录" in h for h in host.hist))
        log = (browser.bdir(self.st) / "login.log").read_text()
        self.assertNotIn("iVBOR", log)                     # never the image
        self.assertEqual(lc.result({"id": res["id"]})["result"], "done")

    async def test_unpaired_or_unapproved_devices_cannot_touch_cards(self):
        bs.add(self.st, "fixture")
        host = FakeHost(self.st)
        lc = bl.LoginCards(FakeElev(host))
        res = await lc.request({"site": "fixture"})
        with patch.object(State, "sign_key", return_value=None):
            await lc.on_phone(Sess("devX"), {"t": "login_qr_cancel", "id": res["id"], "n": "0" * 32})
        self.assertIn(res["id"], lc.cards)
        with patch.object(State, "sign_key", return_value=b"k" * 32):
            await lc.on_phone(Sess("dev1"), {"t": "login_qr_cancel", "id": res["id"], "n": "0" * 32})   # wrong nonce
            self.assertIn(res["id"], lc.cards)
            card = next(m for m in host.sent if m.get("t") == "login_qr")
            await lc.on_phone(Sess("dev1"), {"t": "login_qr_cancel", "id": res["id"], "n": card["n"]})
        self.assertNotIn(res["id"], lc.cards)
        await asyncio.sleep(0)
        self.assertEqual([m["result"] for m in host.sent if m.get("t") == "login_qr_done"], ["cancelled"])

    async def test_telegram_gets_text_by_default_and_the_qr_only_after_consent(self):
        bs.add(self.st, "fixture")
        host = FakeHost(self.st)

        class TG:
            def enabled(self):
                return True
        host.telegram = TG()
        from agentj import telegram
        calls = []
        with patch.object(telegram, "configuration", return_value={"owner_id": 7, "key_env": "X"}), \
                patch.object(telegram, "api", side_effect=lambda c, m, v, **k: calls.append((m, v))), \
                patch.object(telegram, "upload", side_effect=lambda c, m, f, fld, n, mime, data, **k: calls.append((m, len(data)))), \
                patch.object(State, "sign_key", return_value=b"k" * 32):
            lc = bl.LoginCards(FakeElev(host))
            res = await lc.request({"site": "fixture"})
            self.assertEqual(res["telegram"], "text")
            self.assertEqual([c[0] for c in calls], ["sendMessage"])
            self.assertNotIn("http", calls[0][1]["text"])
            card = next(m for m in host.sent if m.get("t") == "login_qr")
            self.assertEqual((card["tg"], card["channel"]), ("text", "Telegram"))
            await lc.on_phone(Sess("dev1"), {"t": "login_qr_tg", "id": res["id"], "n": card["n"], "on": True})
            self.assertEqual(calls[-1][0], "sendPhoto")
            self.assertTrue(bs.tg_consent(self.st))
            lc.end_all("stopped")


if __name__ == "__main__":
    unittest.main()
