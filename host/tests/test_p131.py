"""P131 (0.18.2): the first-run checklist never blocks the chat, says where and what failed, and can use the owner's browser.

Adopt (browser.py): only loopback endpoints; `--dry-run` writes nothing; a real adopt is only an offer until the owner's signed
"use it"; adopted, runtime / status / setup read the owner's browser and never install, start, stop or kill anything; a
browser that stops answering is "the browser you registered is not running"; one also listening on the network is refused;
`setup` that finds a running CDP browser asks first (`--own` installs anyway). Checklist (first_run.py): `close` / `reopen`, the
`--close` CLI, plain-language `explain` per row naming which browser was checked, owner actions (install / adopt / open and
sign in) recorded by a signed mark and carried out by `act`. Serve: `setup_close` hides it on every page, `setup_get open`
reopens it, a signed action mark runs the action and pushes the result. Window: brand title / class / start page and the
per-desktop "bring to front".
"""
import _hermetic  # noqa: F401,I001
import asyncio
import http.server
import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import browser, browser_sites as bs, first_run, serve  # noqa: E402

from test_l1 import Phone, _host, _state  # noqa: E402
from test_p115 import fake_probes, sign  # noqa: E402


class FakeCDPServer:
    """Answers /json/version like a Chromium started with --remote-debugging-port; counts every request it gets."""
    def __init__(self, host="127.0.0.1"):
        seen = self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                port = self.server.server_address[1]
                b = json.dumps({"Browser": "Chrome/141.0", "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser/abc"}).encode()
                self.send_response(200 if self.path == "/json/version" else 404)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer((host, 0), H)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Adopt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.fake = FakeCDPServer(); self.addCleanup(self.fake.stop)
        self.ep = f"http://127.0.0.1:{self.fake.port}"

    def files(self):
        d = browser.bdir(self.st)
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def test_only_loopback_endpoints(self):
        for bad in ("http://198.51.100.5:9222", "http://example.com:9222", "http://0.0.0.0:9222", "https://evil.test",
                    "http://user:pw@127.0.0.1:9222", "http://127.0.0.1", "http://127.0.0.1:99999", None, "x" * 300):
            res = browser.adopt(self.st, bad)
            self.assertFalse(res["ok"], bad)
            self.assertIn(res["reason"], ("not_loopback", "shape"), bad)
        self.assertEqual(browser.parse_endpoint("http://localhost:9222"), 9222)
        self.assertEqual(browser.parse_endpoint("127.0.0.1:9333"), 9333)
        self.assertEqual(self.files(), [], "a refused endpoint writes nothing")

    def test_dry_run_reads_and_writes_nothing(self):
        res = browser.adopt(self.st, self.ep, dry_run=True)
        self.assertEqual((res["ok"], res["result"], res["port"]), (True, "would_adopt", self.fake.port))
        self.assertTrue(res["loopback_only"])
        self.assertEqual(self.files(), [])
        self.assertEqual(set(self.fake.seen), {"/json/version"}, "only /json/version — no tabs, no pages")

    def test_dry_run_site_check_uses_its_own_tabs(self):
        from test_p114 import FakeCDP
        f = FakeCDP([(0, "https://mail.google.com/mail/u/0/#inbox", "complete")])
        real = bs.probe
        with mock.patch.object(bs, "probe", wraps=lambda cdp, rule, url, **kw: real(cdp, rule, url, clock=f.clock, sleep=f.sleep)):
            res = browser.adopt(self.st, self.ep, dry_run=True, sites=["gmail"], _cdp=f)
        self.assertEqual(res["sites"]["gmail"]["status"], "logged_in")
        self.assertEqual(f.created, ["about:blank"])
        self.assertEqual(f.closed, ["T1"], "the tab it opened is closed")
        self.assertEqual(self.files(), [], "a dry run never writes sites.json or an audit line")

    def test_adopt_is_an_offer_until_the_owner_confirms_then_never_installs_or_kills(self):
        res = browser.adopt(self.st, self.ep)
        self.assertEqual(res["result"], "confirm_on_phone")
        self.assertIsNone(browser.adopted(self.st))
        self.assertIsNone(browser.runtime(self.st), "an offer is not adoption")
        self.assertEqual(browser.source(self.st), {"kind": "offer", "port": self.fake.port})
        with mock.patch.object(browser, "install", side_effect=AssertionError("never installs")), \
                mock.patch.object(os, "kill", side_effect=AssertionError("never kills")), \
                mock.patch.object(os, "killpg", side_effect=AssertionError("never kills")), \
                mock.patch.object(browser, "service_install", side_effect=AssertionError("never starts a service")):
            done = browser.confirm_adopt(self.st, "n" * 32)
            self.assertEqual((done["ok"], done["port"]), (True, self.fake.port))
            rt = browser.runtime(self.st)
            self.assertTrue(rt["adopted"])
            self.assertEqual(rt["ws"], f"ws://127.0.0.1:{self.fake.port}/devtools/browser/abc")
            st = browser.status(self.st)
            self.assertEqual((st["state"], st["source"]["kind"], st["source"]["port"]), ("ready", "adopted", self.fake.port))
            self.assertEqual(browser.setup(self.st)["result"], "adopted")
            self.assertEqual(browser.endpoint(self.st)["endpoint"], self.ep)
            self.assertEqual(browser.run(self.st), 0, "the supervisor never starts a second browser")
        self.assertFalse(browser.offer_path(self.st).exists())
        rec = json.loads(browser.adopted_path(self.st).read_text())
        self.assertEqual(set(rec), {"port", "product", "adopted_at", "decision_ref"}, "no profile path, no cookies")
        self.assertEqual(oct(browser.adopted_path(self.st).stat().st_mode & 0o777), "0o600")
        # it stops: "the browser you registered is not running" — and Agent J does not start it
        self.fake.stop()
        self.assertIsNone(browser.runtime(self.st))
        st = browser.status(self.st)
        self.assertEqual((st["state"], st["attention"]), ("attention", ["adopt_not_running"]))
        self.assertIn("没在运行", st["hints"][0])
        with mock.patch.object(browser, "install", side_effect=AssertionError("never installs")):
            res = browser.setup(self.st)
        self.assertEqual((res["ok"], res["result"], res["reason"]), (False, "adopted", "adopt_not_running"))
        self.fake = FakeCDPServer()          # setUp's cleanup stops the new one
        self.assertEqual(browser.forget_adopted(self.st)["result"], "forgotten")
        self.assertIsNone(browser.adopted(self.st))

    @unittest.skipUnless(sys.platform.startswith("linux"), "uses /proc/net/tcp")
    def test_a_browser_also_listening_on_the_network_is_refused(self):
        wide = FakeCDPServer("0.0.0.0"); self.addCleanup(wide.stop)
        res = browser.adopt(self.st, f"http://127.0.0.1:{wide.port}", dry_run=True)
        self.assertEqual((res["ok"], res["reason"]), (False, "adopt_not_running"))

    def test_one_slow_read_is_not_not_running(self):
        """TB live run 10-11: a single failed /json/version from the busy :9222 browser made it "not running"; read twice."""
        browser._write_json(self.st, browser.adopted_path(self.st), {"port": self.fake.port, "product": "x", "adopted_at": 0})
        real = browser.probe_endpoint
        calls = []
        def flaky(port, timeout=2.0):
            calls.append(timeout)
            return None if len(calls) == 1 else real(port, timeout)
        with mock.patch.object(browser, "probe_endpoint", side_effect=flaky), mock.patch.object(browser.time, "sleep"):
            self.assertTrue(browser.runtime(self.st)["adopted"])
        self.assertEqual(len(calls), 2)
        with mock.patch.object(browser, "probe_endpoint", return_value=None), mock.patch.object(browser.time, "sleep"):
            self.assertIsNone(browser.runtime(self.st))
            self.assertFalse(browser.source(self.st)["running"])

    def test_nothing_listening(self):
        res = browser.adopt(self.st, f"http://127.0.0.1:{free_port()}")
        self.assertEqual((res["ok"], res["reason"]), (False, "adopt_not_running"))

    def test_setup_asks_first_when_a_browser_already_runs_here(self):
        with mock.patch.dict(os.environ, {"AGENTJ_BROWSER_DETECT": "on"}), \
                mock.patch.object(browser, "ADOPT_PORTS", (free_port(), self.fake.port)), \
                mock.patch.object(browser, "install", side_effect=browser.BrowserError("download_failed")) as inst:
            self.assertEqual(browser.source(self.st), {"kind": "offer", "port": self.fake.port})
            res = browser.setup(self.st)
            self.assertEqual((res["ok"], res["result"], res["port"]), (False, "existing_found", self.fake.port))
            self.assertIn(str(self.fake.port), res["hint"])
            inst.assert_not_called()
            self.assertEqual(browser.offer(self.st)["port"], self.fake.port)
            res = browser.setup(self.st, own=True)                # the owner said "install a separate one"
            inst.assert_called_once()
            self.assertEqual(res["reason"], "download_failed")
            self.assertTrue(browser.offer(self.st)["declined"])
            self.assertEqual(browser.source(self.st), {"kind": "none"}, "declined: not asked again")
            browser.setup(self.st)
            self.assertEqual(inst.call_count, 2, "after a no, setup installs without asking again")

    def test_cli_parses_adopt_and_setup_own(self):
        import argparse
        ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd")
        browser.add_parser(sub)
        a = ap.parse_args(["browser", "adopt", "--endpoint", self.ep, "--dry-run", "--site", "gmail", "--json"])
        self.assertEqual((a.bcmd, a.endpoint, a.dry_run, a.site), ("adopt", self.ep, True, ["gmail"]))
        self.assertTrue(ap.parse_args(["browser", "setup", "--own"]).own)
        self.assertTrue(ap.parse_args(["browser", "adopt", "--forget"]).forget)
        res = browser.execute({"action": "adopt", "endpoint": self.ep, "dry_run": True}, self.st)
        self.assertEqual(res["result"], "would_adopt")


class Window(unittest.TestCase):
    def test_own_browser_is_recognisable(self):
        args = browser._launch_args("/x/chrome", pathlib.Path("/p"), False, "file:///s/start.html")
        self.assertIn(f"--window-name={browser.WINDOW_NAME}", args)
        self.assertIn(f"--class={browser.WINDOW_CLASS}", args)
        self.assertEqual(args[-1], "file:///s/start.html")
        self.assertIn("Agent J 专用浏览器", browser.START_HTML)
        with tempfile.TemporaryDirectory() as d:
            prof = pathlib.Path(d)
            browser.brand_profile(prof)
            theme = json.loads((prof / "Default/Preferences").read_text())["browser"]["theme"]
            self.assertEqual(theme["user_color"] & 0xFFFFFFFF, browser.BRAND_ARGB)
            (prof / "Default/Preferences").write_text(json.dumps({"profile": {"name": "x"}}))
            browser.brand_profile(prof)
            data = json.loads((prof / "Default/Preferences").read_text())
            self.assertEqual(data["profile"], {"name": "x"}, "other preferences are kept")

    def test_raise_per_desktop(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(browser, "system", return_value="linux"), \
                mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "x", "WAYLAND_DISPLAY": "wayland-1"}), \
                mock.patch.object(browser.shutil, "which", return_value="/usr/bin/hyprctl"), \
                mock.patch.object(browser.subprocess, "run", side_effect=run):
            self.assertEqual(browser.raise_window(4242), {"ok": True, "method": "hyprland"})
        self.assertEqual(calls[0], ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "pid:4242" })'])
        calls.clear()
        legacy = lambda argv, **kw: calls.append(argv) or mock.Mock(returncode=0, stdout="ok" if "focuswindow" in argv else "error: …")
        with mock.patch.object(browser, "system", return_value="linux"), \
                mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "x"}), \
                mock.patch.object(browser.shutil, "which", return_value="/usr/bin/hyprctl"), \
                mock.patch.object(browser.subprocess, "run", side_effect=legacy):
            self.assertEqual(browser.raise_window(4242), {"ok": True, "method": "hyprland"}, "older Hyprland")
        self.assertEqual(calls[-1], ["hyprctl", "dispatch", "focuswindow", "pid:4242"])
        env = {k: v for k, v in os.environ.items() if k not in ("HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY")}
        with mock.patch.object(browser, "system", return_value="linux"), \
                mock.patch.dict(os.environ, {**env, "WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            self.assertEqual(browser.raise_window(1), {"ok": False, "method": "unsupported"}, "GNOME/KDE Wayland: says so")
        with mock.patch.object(browser, "system", return_value="wsl2"):
            self.assertEqual(browser.raise_window(1)["method"], "unsupported")
        with mock.patch.object(browser, "system", return_value="macos"), \
                mock.patch.object(browser.subprocess, "run", side_effect=run):
            self.assertEqual(browser.raise_window(77), {"ok": True, "method": "macos"})
        self.assertIn("unix id is 77", calls[-1][-1])

    def test_open_login_foreground_tab_and_front(self):
        from test_p114 import FakeCDP
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            f = FakeCDP([(0, "about:blank", "complete")])
            f.detach = lambda session: None
            calls = []
            orig = f.call
            f.call = lambda m, p=None, session=None, timeout=None: calls.append(m) or orig(m, p, session, timeout)
            seen = []
            f.new_tab = lambda url, background=True: seen.append((url, background)) or "T1"
            with mock.patch.object(browser, "raise_window", return_value={"ok": True, "method": "hyprland"}) as rw:
                res = bs.open_login(st, "gmail", _cdp=f)
            self.assertEqual((res["ok"], res["site"], res["raised"]["ok"]), (True, "gmail", True))
            self.assertEqual(seen, [(bs.templates()["gmail"]["login_url"], False)], "a foreground tab on the sign-in page")
            self.assertIn("Page.bringToFront", calls)
            self.assertEqual(rw.call_args.args[1], browser.WINDOW_NAME)
            self.assertIn("gmail", bs.load(st)["sites"], "the site joins the daily check")
            self.assertEqual(f.closed, [], "the owner's sign-in tab is never closed")


class Checklist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)

    def row(self, snap, iid):
        return next(i for i in snap["items"] if i["id"] == iid)

    def test_close_reopen_and_cli(self):
        self.assertEqual(first_run.close(self.st)["result"], "needs_setup", "nothing to close: no journal is created")
        self.assertFalse(first_run.path(self.st).exists())
        with fake_probes({"exit": ("pending", "start_check")}):
            rev = first_run.resume(self.st)["revision"]
        self.assertTrue(first_run.active(first_run.read(self.st)))
        snap = first_run.close(self.st)
        self.assertEqual((snap["result"], snap["closed"], snap["revision"]), ("closed", True, rev + 1))
        self.assertFalse(first_run.active(first_run.read(self.st)), "closed: never comes back by itself")
        self.assertEqual(first_run.close(self.st)["revision"], rev + 1, "closing twice changes nothing")
        snap = first_run.reopen(self.st)
        self.assertFalse(snap["closed"])
        self.assertTrue(first_run.active(first_run.read(self.st)))
        import argparse
        ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd")
        first_run.add_parser(sub)
        self.assertTrue(ap.parse_args(["setup", "checklist", "--close"]).close)
        self.assertTrue(ap.parse_args(["setup", "checklist", "--pause"]).close)
        self.assertEqual(first_run.execute("close", self.st)["result"], "closed")
        self.assertIn("已收起", first_run._text({"result": "closed"}))
        self.assertTrue(ap.parse_args(["setup", "checklist", "--open"]).reopen)
        self.assertFalse(first_run.execute("open", self.st)["closed"])

    def test_closed_stays_closed_until_the_owner_reopens(self):
        """追加2 (Leo 09:31 「关闭后还在反复弹」): resume, check, new revisions and a restart never bring it back."""
        with fake_probes({"exit": ("pending", "start_check")}):
            first_run.resume(self.st)
            first_run.close(self.st)
            for op in ("resume", "check", "resume"):
                snap = first_run.execute(op, self.st)
                self.assertTrue(snap["closed"], op + " keeps it closed")
            first_run.check(self.st, ["gmail"], live=True)
        rec = first_run.read(self.st)                          # a restart reads the same journal
        self.assertTrue(rec["closed"])
        self.assertFalse(first_run.active(rec), "a reconnect after a restart does not show it")
        self.assertFalse(first_run.reopen(self.st)["closed"], "only the owner's own ask reopens it")

    def test_explain_names_the_browser_the_effect_and_the_next_step(self):
        with fake_probes({"browser": ("waiting_local", "browser_not_installed"), "gmail": ("pending", "needs_browser")}):
            snap = first_run.resume(self.st)
        ex = self.row(snap, "browser")["explain"]
        self.assertEqual(ex["fact"]["zh"], "没通过：Agent J 的专用浏览器还没安装。")
        self.assertIn("只影响要用网站的事", ex["effect"]["zh"])
        self.assertEqual(ex["next"], ["install_browser"])
        self.assertIn("要先准备好浏览器", self.row(snap, "gmail")["explain"]["fact"]["zh"])
        rec = first_run.read(self.st)
        rec["items"]["gmail"].update(state="waiting_local", reason="site_signin_required")
        rec["browser_source"] = {"kind": "own"}
        ex = first_run.explain(rec, "gmail")
        self.assertEqual(ex["fact"]["zh"], "没通过：Agent J 的专用浏览器里还没登录 Gmail 网页登录（你平时用的浏览器不算）。")
        self.assertEqual(ex["effect"]["zh"], "只影响要用 Gmail 网页登录 的事，其他照常。")
        self.assertEqual(ex["next"], ["open_login", "check"])
        self.assertIn("your everyday browser does not count", ex["fact"]["en"])
        rec["browser_source"] = {"kind": "adopted", "port": 9222, "running": True}
        ex = first_run.explain(rec, "gmail")
        self.assertIn("你登记的浏览器（端口 9222）里还没登录", ex["fact"]["zh"])
        self.assertNotIn("不算", ex["fact"]["zh"])
        rec["items"]["browser"].update(state="waiting_local", reason="existing_browser_found")
        rec["browser_source"] = {"kind": "offer", "port": 9222}
        ex = first_run.explain(rec, "browser")
        self.assertEqual(ex["next"], ["adopt_browser", "install_browser"])
        self.assertIn("端口 9222", ex["fact"]["zh"])
        rec["items"]["awake"].update(state="waiting_local", reason="sleep_enabled", choice="selected")
        ex = first_run.explain(rec, "awake")
        self.assertIn("自动睡眠", ex["fact"]["zh"])
        rec["items"]["service"].update(state="attention", reason="not_active")
        self.assertIn("网络连通", first_run.explain(rec, "service")["effect"]["zh"], "names what waits on it")
        for text in json.dumps(first_run.snapshot(rec), ensure_ascii=False).split('"'):
            self.assertNotIn("如果这项没通过", text)
        self.assertEqual(first_run.snapshot(rec)["progress"]["total"], 11)

    def test_action_marks_are_recorded_then_carried_out(self):
        with fake_probes({"gmail": ("waiting_local", "site_signin_required"), "browser": ("verified", None)}):
            rev = first_run.resume(self.st)["revision"]
            with self.assertRaises(first_run.SetupError):
                first_run.mark(self.st, "gmail", "install_browser", rev, "n1")
            with self.assertRaises(first_run.SetupError):
                first_run.mark(self.st, "awake", "open_login", rev, "n1")
            snap = first_run.mark(self.st, "gmail", "open_login", rev, "n1")
            self.assertEqual(self.row(snap, "gmail")["choice"], "selected", "asking to sign in = using it")
            self.assertEqual(snap["action"]["state"], "running")
            with mock.patch.object(bs, "open_login", return_value={"ok": True, "result": "opened", "site": "gmail",
                                                                   "raised": {"ok": False, "method": "unsupported"}}):
                snap = first_run.act(self.st, "gmail", "open_login", "n1")
        self.assertEqual(snap["action"], {"item": "gmail", "choice": "open_login", "at": snap["action"]["at"], "state": "done",
                                          "result": "opened", "raised": False})
        self.assertEqual(snap["revision"], rev + 2)
        with fake_probes({"browser": ("verified", None)}), \
                mock.patch.object(browser, "setup", return_value={"ok": False, "result": "attention", "reason": "download_failed"}) as su:
            first_run.mark(self.st, "browser", "install_browser", snap["revision"], "n2")
            snap = first_run.act(self.st, "browser", "install_browser", "n2")
        su.assert_called_once_with(self.st, own=True)
        self.assertEqual((snap["action"]["state"], snap["action"]["reason"]), ("failed", "download_failed"))

    def test_live_check_runs_the_real_site_check_only_for_named_rows(self):
        with fake_probes({}):
            first_run.resume(self.st)
        with mock.patch.object(browser, "runtime", return_value={"ws": "ws://127.0.0.1:1/devtools/browser/x"}), \
                mock.patch.object(bs, "check", return_value={"ok": True}) as chk, fake_probes({}):
            first_run.check(self.st)
            chk.assert_not_called()
            first_run.check(self.st, ["gmail"], live=True)
            chk.assert_called_once()
            self.assertEqual(chk.call_args.args[1], ["gmail"])


class ServeP131(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []

    def got(self, t, r=None):
        return [o for _, o in self.sent if o["t"] == t and (r is None or o.get("r") == r)]

    def test_close_reopen_and_action_over_the_session(self):
        ph = Phone(self.st, "网页 · iOS Safari")
        host = _host(self.st, self.sent)
        s = serve.Session(cid=11, state="ready", device=ph.did, name="网页 · iOS Safari", pub=ph.pub)
        host.sessions[11] = s

        async def go():
            with fake_probes({"gmail": ("waiting_local", "site_signin_required")}):
                snap = await asyncio.to_thread(first_run.resume, self.st, host.setup_serve())
                await host._app(s, {"t": "setup_close", "r": "c1"})
                await asyncio.sleep(0.05)
                self.assertTrue(first_run.read(self.st)["closed"])
                self.assertTrue(self.got("setup_card", "c1")[0]["closed"])
                pushed = [o for o in self.got("setup_card") if "r" not in o]
                self.assertTrue(pushed and pushed[-1]["closed"] and pushed[-1]["show"] is False, "every page hides it")
                await host._app(s, {"t": "setup_get", "r": "o1", "open": True})
                self.assertFalse(self.got("setup_card", "o1")[0]["closed"])
                self.assertFalse(first_run.read(self.st)["closed"])
                rev = first_run.read(self.st)["revision"]
                tgt = {"item": "gmail", "choice": "open_login", "rev": rev}
                done = {"ok": True, "result": "opened", "site": "gmail", "raised": {"ok": True, "method": "hyprland"}}
                with mock.patch.object(bs, "open_login", return_value=done) as ol:
                    await host._app(s, {"t": "setup_mark", "r": "u1", **tgt})                 # unsigned: nothing happens
                    self.assertEqual(self.got("ctl_res", "u1")[0]["why"], "shape")
                    await host._app(s, sign(ph, tgt, {"t": "setup_mark", "r": "a1"}))
                    self.assertTrue(self.got("ctl_res", "a1")[0]["ok"])
                    await host.setup_act
                    await asyncio.sleep(0.05)
                ol.assert_called_once_with(self.st, "gmail")
                last = [o for o in self.got("setup_card") if "r" not in o][-1]
                self.assertEqual((last["action"]["choice"], last["action"]["state"], last["action"]["raised"]),
                                 ("open_login", "done", True))
                _ = snap
        asyncio.run(go())
        acts = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        self.assertEqual([a["result"] for a in acts if a["action"] == "setup_mark"], ["refused:shape", "ok"])

    def test_elevate_close_pushes_hidden(self):
        host = _host(self.st, self.sent)

        async def go():
            with fake_probes({}):
                await asyncio.to_thread(first_run.resume, self.st)
            res = await host.elevate._p115({"t": "setup", "op": "close"})
            self.assertEqual(res["result"], "closed")
            await asyncio.sleep(0.05)
        asyncio.run(go())
        self.assertTrue(first_run.read(self.st)["closed"])

    def test_closed_pushes_never_show_and_open_reopens(self):
        ph = Phone(self.st, "网页 · iOS Safari")
        host = _host(self.st, self.sent)
        host.sessions[12] = serve.Session(cid=12, state="ready", device=ph.did, name="网页 · iOS Safari", pub=ph.pub)

        async def go():
            with fake_probes({}):
                await asyncio.to_thread(first_run.resume, self.st)
                await host.elevate._p115({"t": "setup", "op": "close"})
                res = await host.elevate._p115({"t": "setup", "op": "resume"})
                self.assertTrue(res["closed"])
                host.push_setup(first_run.check(self.st), show=True)
                await asyncio.sleep(0.05)
                pushed = [o for o in self.got("setup_card") if "r" not in o]
                self.assertTrue(pushed and all(o["show"] is False for o in pushed), "a closed checklist never shows itself")
                res = await host.elevate._p115({"t": "setup", "op": "open"})
                await asyncio.sleep(0.05)
                self.assertFalse(res["closed"])
                self.assertIs([o for o in self.got("setup_card") if "r" not in o][-1]["show"], True)
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
