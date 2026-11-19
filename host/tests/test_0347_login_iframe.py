"""Browser sign-in observations on loopback. No accounts, challenge images or external URLs."""
import _hermetic
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from agentj import browser, browser_login as bl, browser_sites as bs
from agentj.browser_cdp import CDP
from agentj.state import State
from test_p114 import FakeElev, FakeHost, Sess, chrome_path


@contextmanager
def serve(route):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            status, headers, body = route(self.path, self.headers)
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", headers.get("Cache-Control", "no-store"))
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                if name != "Cache-Control":
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def html(body):
    return 200, {}, "<!doctype html>" + body


@unittest.skipUnless(chrome_path(), "requires a real Chromium binary")
class BrowserLoginIframe(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.desk_status = 200
        self.desk_hits = 0
        self.cache_shell = False
        self.entry = self.enterContext(serve(self.embedded))
        self.origin = self.enterContext(serve(self.publisher))
        escaped = re.escape(self.origin)
        rule = {
            "title": {"en": "Local fixture", "zh": "本地测试"},
            "origins": [self.origin],
            "login_method": "qr",
            "login_url": self.origin + "/login",
            "check_url": self.origin + "/desk",
            "in_url": ["^" + escaped + "/desk$"],
            "out_url": ["^" + escaped + "/login$"],
            "landing_url": ["^" + escaped + "/landing$"],
            "out_dom": ["iframe#signin"],
            "qr": {"selectors": ["#unused"], "ttl": 120},
        }
        self.enterContext(patch.object(bs, "templates", return_value={"fixture": rule}))
        self.enterContext(patch.object(browser, "installed", return_value={"path": chrome_path()}))
        self.enterContext(patch.object(bl, "POLL", 300))  # The test drives the phone check explicitly.
        self.enterContext(patch.object(bl, "capture_qr", return_value=b"test card marker"))
        self.enterContext(patch.object(bl, "SETTLE", 0.01))
        self.enterContext(patch.object(State, "sign_key", return_value=b"x" * 32))
        self.st, self.original = self.start("original")
        self.host = FakeHost(self.st)
        self.cards = bl.LoginCards(FakeElev(self.host))
        self.addAsyncCleanup(self.finish)
        issued = await self.cards.request({"site": "fixture"})
        self.assertEqual(issued["result"], "sent")
        self.card = self.cards.cards[issued["id"]]
        self.target = self.cards.tabs["fixture"]

    def embedded(self, path, headers):
        if path == "/prompt":
            return html('<button onclick="location.replace(\'/acknowledged\')">Continue</button>')
        return html("<p>Fixture acknowledgment</p>")

    def publisher(self, path, headers):
        if path == "/fixture-signed":
            return 303, {"Location": "/desk", "Set-Cookie": "fixture_state=yes; Path=/; SameSite=Strict"}, ""
        if path == "/desk":
            self.desk_hits += 1
            if self.desk_status != 200:
                return self.desk_status, {}, "<p>Fixture unavailable</p>"
            if "fixture_state=yes" not in headers.get("Cookie", "").split("; "):
                return 303, {"Location": "/login"}, ""
            if self.cache_shell:
                return 200, {"Cache-Control": "private, max-age=3600"}, "<p>Fixture app shell</p>"
        if path == "/login":
            return html(f'<iframe id="signin" src="{self.entry}/prompt"></iframe>')
        return html("<p>Local page</p>")

    def start(self, name):
        state = State(self.root / name)
        browser.ensure_dir(state)
        process, _ = browser.start_browser(state, headless=True)
        self.assertIsNotNone(process)
        self.addCleanup(browser.stop_proc, process)
        runtime = browser.runtime(state)
        self.assertIsNotNone(runtime)
        return state, runtime

    async def finish(self):
        self.cards.end_all("cancelled")
        await asyncio.sleep(0)

    async def navigate(self, cdp, session, url, expected):
        result = cdp.call("Page.navigate", {"url": url}, session=session)
        self.assertFalse(result.get("errorText"))
        for _ in range(100):
            if cdp.location(session) == (expected, "complete"):
                return
            await asyncio.sleep(0.05)
        self.fail("local fixture did not finish navigating")

    async def wait_for_frame(self, cdp, session, path):
        for _ in range(100):
            tree = cdp.call("Page.getFrameTree", session=session)["frameTree"]
            for child in tree.get("childFrames", []):
                if child["frame"]["url"] == self.entry + path:
                    return child["frame"]["id"]
            await asyncio.sleep(0.05)
        self.fail("embedded fixture page did not finish navigating")

    async def choose_page(self, url, expected):
        with CDP(self.original["ws"]) as cdp:
            await self.navigate(cdp, cdp.attach(self.target), url, expected)

    async def tap(self):
        seen = len(self.host.sent)
        await self.cards.on_phone(Sess("dev1"), {
            "t": "login_qr_check", "id": self.card["id"], "n": self.card["nonce"],
        })
        await asyncio.sleep(0)
        self.assertGreater(len(self.host.sent), seen)
        return self.cards.result({"id": self.card["id"]})["result"]

    def assert_waiting(self):
        self.assertEqual(self.cards.result({"id": self.card["id"]})["result"], "pending")
        self.assertIn(self.card["id"], self.cards.cards)
        self.assertEqual(self.host.sent[-1].get("state"), "waiting")
        self.assertFalse(any(m.get("result") == "done" for m in self.host.sent))

    async def test_embedded_continue_and_landing_stay_pending_until_trusted_check(self):
        with CDP(self.original["ws"]) as cdp:
            session = cdp.attach(self.target)
            frame_id = await self.wait_for_frame(cdp, session, "/prompt")
            world = cdp.call("Page.createIsolatedWorld", {"frameId": frame_id}, session=session)
            result = cdp.call("Runtime.evaluate", {"contextId": world["executionContextId"],
                              "expression": "document.querySelector('button').click()"}, session=session)
            self.assertNotIn("exceptionDetails", result)
            await self.wait_for_frame(cdp, session, "/acknowledged")
        await self.tap()
        self.assert_waiting()
        await self.choose_page(self.origin + "/landing", self.origin + "/landing")
        await self.tap()
        self.assert_waiting()
        await self.choose_page(self.origin + "/fixture-signed", self.origin + "/desk")
        self.assertEqual(await self.tap(), "done")

    async def test_changing_the_selected_profile_leaves_original_card_pending(self):
        await self.choose_page(self.origin + "/landing", self.origin + "/landing")
        alternate_state, alternate = self.start("alternate")
        self.assertNotEqual(self.original["pid"], alternate["pid"])
        self.assertNotEqual(browser.profile_dir(self.st), browser.profile_dir(alternate_state))
        with CDP(alternate["ws"]) as cdp:
            target = cdp.new_tab("about:blank")
            await self.navigate(cdp, cdp.attach(target), self.origin + "/fixture-signed", self.origin + "/desk")
        bs.add(alternate_state, "fixture")
        rows = (await asyncio.to_thread(bs.check, alternate_state, ["fixture"]))["sites"]
        self.assertEqual(rows[0]["status"], "logged_in")
        with patch.object(browser, "runtime", return_value=alternate):
            await self.tap()
        self.assert_waiting()

    async def test_closing_the_original_tab_leaves_card_pending(self):
        await self.choose_page(self.origin + "/fixture-signed", self.origin + "/desk")
        with CDP(self.original["ws"]) as cdp:
            cdp.close_tab(self.target)
            for _ in range(100):
                if not cdp.tab_alive(self.target):
                    break
                await asyncio.sleep(0.05)
            self.assertFalse(cdp.tab_alive(self.target))
        await self.tap()
        self.assert_waiting()

    async def test_leaving_the_trusted_origin_leaves_card_pending(self):
        await self.choose_page(self.origin + "/fixture-signed", self.origin + "/desk")
        await self.choose_page(self.entry + "/desk", self.entry + "/desk")
        await self.tap()
        self.assert_waiting()

    async def verify_http_error(self, status):
        await self.choose_page(self.origin + "/fixture-signed", self.origin + "/desk")
        self.desk_status = status
        hits = self.desk_hits
        await self.tap()
        self.assertGreater(self.desk_hits, hits)
        self.assert_waiting()

    async def test_protected_url_returning_forbidden_stays_pending(self):
        await self.verify_http_error(403)

    async def test_protected_url_returning_unavailable_stays_pending(self):
        await self.verify_http_error(503)

    async def test_cached_shell_cannot_replace_a_current_server_observation(self):
        self.cache_shell = True
        await self.choose_page(self.origin + "/fixture-signed", self.origin + "/desk")
        with CDP(self.original["ws"]) as cdp:
            session = cdp.attach(self.target)
            primed = cdp.call("Runtime.evaluate", {
                "expression": "fetch('/desk', {cache:'reload'}).then(async r => {await r.text(); return r.status})",
                "awaitPromise": True, "returnByValue": True,
            }, session=session)
            self.assertEqual(primed["result"].get("value"), 200)
            self.desk_status = 503
            hits = self.desk_hits
            observed = cdp.call("Runtime.evaluate", {
                "expression": "fetch('/desk', {cache:'force-cache'}).then(r => r.status)",
                "awaitPromise": True, "returnByValue": True,
            }, session=session)
        self.assertEqual(observed["result"].get("value"), 200)
        self.assertEqual(self.desk_hits, hits, "fixture did not serve the primed Chromium cache")
        await self.tap()
        self.assert_waiting()
        self.assertGreater(self.desk_hits, hits, "confirmation must reach the fixture server")
