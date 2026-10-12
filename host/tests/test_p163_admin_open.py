"""P163: `agentj admin --open` — the admin page opens in this computer's default browser, the one-time link never reaches
stdout (so never an Agent transcript), the command returns at once and the detached page closes itself when unused.

- Unit: Auth.in_use, admin_open_serve (opened / no browser / opener raising), _desktop_env (SSH, display discovery).
- Real: the CLI with a fake $BROWSER script — the child serves the page, the link went only to the browser, the page
  closes once its session ends.
"""
import _hermetic  # noqa: F401,I001
import http.client
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))

from agentj import admin, cli  # noqa: E402
from agentj.state import State  # noqa: E402


class InUse(unittest.TestCase):
    def test_token_then_session_then_nothing(self):
        auth = admin.Auth()
        self.assertFalse(auth.in_use())
        t = auth.new_token()
        self.assertTrue(auth.in_use())                       # link not opened yet
        sid = auth.redeem(t)
        self.assertTrue(sid and auth.in_use())               # a logged-in page
        auth.end(sid)
        self.assertFalse(auth.in_use())                      # page closed → nothing left to serve

    def test_expired_link_is_not_in_use(self):
        auth = admin.Auth()
        auth.new_token()
        with mock.patch.object(admin.time, "monotonic", return_value=time.monotonic() + admin.TOKEN_TTL + 1):
            self.assertFalse(auth.in_use())


class FakeSrv:
    port = 4242

    def __init__(self, uses):
        self.uses = list(uses)
        self.links = []

    def new_link(self):
        self.links.append("http://127.0.0.1:4242/#t=SECRET")
        return self.links[-1]

    def in_use(self):
        return self.uses.pop(0) if self.uses else False


class Serve(unittest.TestCase):
    def test_opened_serves_until_unused(self):
        srv, seen, evs = FakeSrv([True, True, False]), [], []
        self.assertTrue(cli.admin_open_serve(srv, evs.append, opener=lambda u: seen.append(u) or True, poll=0))
        self.assertEqual(seen, srv.links)
        self.assertEqual(evs[0]["ev"], "opened")
        self.assertEqual(srv.uses, [])                        # polled until the page went unused
        self.assertNotIn("SECRET", json.dumps(evs))           # the reported event never carries the link

    def test_no_browser_returns_at_once(self):
        for opener in (lambda u: False, mock.Mock(side_effect=OSError("no browser"))):
            srv, evs = FakeSrv([True]), []
            self.assertFalse(cli.admin_open_serve(srv, evs.append, opener=opener, poll=0))
            self.assertEqual(evs[0]["ev"], "no_browser")
            self.assertEqual(srv.uses, [True])                # never waited

    def test_stop_ends_the_wait(self):
        stop = threading.Event()
        stop.set()
        self.assertTrue(cli.admin_open_serve(FakeSrv([True] * 99), lambda e: None, opener=lambda u: True, stop=stop))


class DesktopEnv(unittest.TestCase):
    def test_ssh_without_desktop_never_opens(self):
        self.assertIsNone(cli._desktop_env({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}))

    def test_display_kept(self):
        self.assertEqual(cli._desktop_env({"WAYLAND_DISPLAY": "wayland-1"})["WAYLAND_DISPLAY"], "wayland-1")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux display discovery")
    def test_service_env_finds_own_wayland_socket(self):
        import socket
        with tempfile.TemporaryDirectory() as run:
            s = socket.socket(socket.AF_UNIX)
            s.bind(os.path.join(run, "wayland-7"))
            try:
                e = cli._desktop_env({"XDG_RUNTIME_DIR": run})
                self.assertEqual(e["WAYLAND_DISPLAY"], "wayland-7")
            finally:
                s.close()


class Fenced(unittest.TestCase):
    """Inside the Agent fence the state is hidden and the pid namespace dies with the command: `--open` asks serve
    (elevate socket) and serve starts the page. Only {"ev","port"} crosses the socket — never the link."""

    def test_cli_asks_the_host_when_the_state_is_hidden(self):
        import socket
        d = tempfile.mkdtemp(prefix="aj-p163f-")
        sock = os.path.join(d, "e.sock")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(sock)
        srv.listen(1)
        got = []

        def host():
            c, _ = srv.accept()
            got.append(json.loads(c.makefile().readline()))
            c.sendall(b'{"ev": "opened", "port": 4321}\n')
            c.close()
        threading.Thread(target=host, daemon=True).start()
        env = {**os.environ, "AGENTJ_STATE_DIR": os.path.join(d, "hidden"), "AGENTJ_ELEVATE_SOCK": sock,
               "PYTHONPATH": str(HOST)}
        r = subprocess.run([sys.executable, "-P", "-m", "agentj.cli", "admin", "--open", "--events", "jsonl"], cwd=HOST,
                           env=env, capture_output=True, text=True, timeout=60)
        srv.close()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(got, [{"t": "admin_open"}])
        self.assertEqual(json.loads(r.stdout), {"ev": "opened", "port": 4321})

    def test_host_side_one_page_at_a_time(self):
        from agentj import elevate
        el = object.__new__(elevate.Elevator)
        el.st = mock.Mock()
        first, second = mock.Mock(), mock.Mock()
        first.poll.return_value = None                       # still running
        with mock.patch.object(cli, "spawn_admin_open", side_effect=[({"ev": "opened", "port": 1, "pid": 9}, first),
                                                                    ({"ev": "opened", "port": 2, "pid": 10}, second)]):
            self.assertEqual(el._admin_open(), {"ev": "opened", "port": 1})
            self.assertEqual(el._admin_open(), {"ev": "opened", "port": 2})
        first.terminate.assert_called_once()                 # the new page replaced the old one
        el.st.log.assert_called_with("admin_open", trigger="agent", result="opened")


@unittest.skipUnless(sys.platform.startswith("linux"), "uses a fake $BROWSER via a Wayland display variable")
class RealCli(unittest.TestCase):
    """`agentj admin --open` end to end: a fake browser records the URL instead of opening a window."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-p163-")
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.got = pathlib.Path(self.dir) / "url"
        browser = pathlib.Path(self.dir) / "browser.sh"
        browser.write_text(f'#!/bin/sh\nprintf %s "$1" > "{self.got}"\n')
        browser.chmod(0o700)
        self.env = {**os.environ, "AGENTJ_STATE_DIR": str(self.st.root), "BROWSER": f"{browser} %s",
                    "WAYLAND_DISPLAY": "wayland-p163", "PYTHONPATH": str(HOST)}   # this checkout, not an installed agentj
        self.env.pop("SSH_CONNECTION", None)
        self.env.pop("SSH_TTY", None)
        self.pid = None

    def tearDown(self):
        if self.pid:
            try:
                os.kill(self.pid, 15)
            except ProcessLookupError:
                pass
        import shutil
        shutil.rmtree(self.dir, True)

    def _run(self, *extra):
        return subprocess.run([sys.executable, "-P", "-m", "agentj.cli", "admin", "--open", *extra], cwd=HOST,
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_open_returns_and_page_closes_when_done(self):
        t0 = time.monotonic()
        r = self._run("--events", "jsonl")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 30)            # returned without waiting for the page
        ev = json.loads(r.stdout)
        self.pid = ev["pid"]
        self.assertEqual(ev["ev"], "opened")
        for _ in range(100):
            if self.got.exists() and self.got.read_text():
                break
            time.sleep(0.1)
        url = self.got.read_text()
        self.assertTrue(url.startswith(f"http://127.0.0.1:{ev['port']}/#t="), url)
        token = url.split("#t=", 1)[1]
        self.assertNotIn(token, r.stdout + r.stderr)          # the link went to the browser only
        c = http.client.HTTPConnection("127.0.0.1", ev["port"], timeout=10)
        c.request("GET", "/")
        resp = c.getresponse()
        resp.read()
        self.assertEqual(resp.status, 200)
        h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{ev['port']}"}
        c.request("POST", "/api/session", json.dumps({"token": token}), h)
        sid = json.loads(c.getresponse().read())["session"]
        c.request("POST", "/api/session/end", "{}", {**h, "Authorization": f"Bearer {sid}"})
        self.assertEqual(c.getresponse().status, 200)
        os.kill(self.pid, 0)                                  # still alive right now …
        deadline = time.monotonic() + cli.ADMIN_OPEN_POLL + 15
        while time.monotonic() < deadline:                    # … and gone within one poll once the page is closed
            try:
                os.kill(self.pid, 0)
            except ProcessLookupError:
                self.pid = None
                break
            time.sleep(0.3)
        self.assertIsNone(self.pid, "the detached admin page did not close after its session ended")

    def test_url_file_conflict(self):
        r = self._run("--url-file", os.path.join(self.dir, "x"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--url-file", r.stderr)

    def test_no_desktop_tells_the_owner_what_to_run(self):
        self.env["SSH_CONNECTION"] = "1.2.3.4 5 6.7.8.9 22"
        self.env.pop("WAYLAND_DISPLAY")
        self.env.pop("DISPLAY", None)
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("agentj admin", r.stderr)
        self.assertFalse(self.got.exists())


if __name__ == "__main__":
    unittest.main()
