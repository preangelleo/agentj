"""Sign-in QR inside an embedded window (the WeChat Channels layout since November 2026), on loopback only.

A local publisher page adds an embedded sign-in window late; inside it a QR image arrives late too. The crop must wait for
the window, find the element inside it, and crop that element only. Embedded windows of other origins are never searched.
Capture retries are bounded (3) and every failure names its reason. No account, no external URL, no real login material:
the fixture QR encodes an example.invalid address."""
import _hermetic  # noqa: F401
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import itertools
import pathlib
import re
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import Mock, patch

import segno

from agentj import browser, browser_login as bl, qrcrop
from agentj.browser_cdp import CDPTimeout, QRBlank, QRFrameLoading, painted
from agentj.state import State
from test_p114 import chrome_path

FIXTURE_TEXT = "https://example.invalid/connect/confirm?uuid=fixture-not-a-login"


@contextmanager
def serve(route):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            status, ctype, body = route(self.path)
            data = body if isinstance(body, bytes) else body.encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def qr_png() -> bytes:
    buf = io.BytesIO()
    segno.make(FIXTURE_TEXT, error="m").save(buf, kind="png", scale=10, border=4)
    return buf.getvalue()


@unittest.skipUnless(chrome_path(), "requires a real Chromium binary")
class EmbeddedWindow(unittest.TestCase):
    """Real headless Chromium, real CDP, the product's own _open_and_capture."""

    def setUp(self):
        self.root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.image = qr_png()
        self.slow = False
        self.embed = self.enterContext(serve(self.embedded))
        self.enterContext(patch.object(browser, "installed", return_value={"path": chrome_path()}))
        self.publisher = self.enterContext(serve(self.page))
        st = State(self.root / "state")
        browser.ensure_dir(st)
        process, _ = browser.start_browser(st, headless=True)
        self.addCleanup(browser.stop_proc, process)
        self.rt = browser.runtime(st)

    # -- loopback servers (route → status, content type, body)
    def embedded(self, path):
        if path == "/qrcode.png":
            return 200, "image/png", self.image
        if path == "/slow":
            time.sleep(20)             # keeps the embedded document from completing during the test
            return 404, "text/plain", ""
        if path.startswith("/connect"):
            if self.slow:
                return 200, "text/html", "<!doctype html><body><script>document.write('<img src=\"/slow\">')</script>"
            # the image itself is attached a little later, like the official page's script does
            return 200, "text/html", (
                "<!doctype html><body style='margin:0'><div class='web_qrcode_img_wrp'>"
                "<img class='js_qrcode_img web_qrcode_img' style='width:160px;height:160px;display:none'>"
                "<img class='qrcode lightBorder js_qrcode_img' style='display:none'></div><script>"
                "setTimeout(function(){var i=document.querySelector('.web_qrcode_img');i.src='/qrcode.png';"
                "i.style.display='block'},600)</script></body>")
        return 404, "text/plain", ""

    def wrapper(self, path):
        # an unlisted window (think: an ad) that embeds the listed sign-in origin
        return 200, "text/html", (f"<!doctype html><body style='margin:0'><iframe width=208 height=208 frameborder=0 "
                                  f"src='{self.embed}/connect/qrconnect?nested=1'></iframe></body>")

    def page(self, path):
        if path == "/nested":
            return 200, "text/html", (f"<!doctype html><body style='margin:0;padding:200px 300px'><iframe width=240 "
                                      f"height=240 frameborder=0 src='{self.unlisted}/wrap'></iframe></body>")
        if path == "/login":
            return 200, "text/html", (
                "<!doctype html><body style='margin:0;padding:200px 300px'><div class='login-view'></div><script>"
                "setTimeout(function(){var f=document.createElement('iframe');f.width=208;f.height=208;"
                f"f.frameBorder=0;f.src='{self.embed}/connect/qrconnect?fixture=1';"
                "document.querySelector('.login-view').appendChild(f)},1200)</script></body>")
        return 404, "text/plain", ""

    def template(self, frame_origins=True):
        esc = re.escape(self.publisher)
        qr = {"selectors": ["img.web_qrcode_img", "img[src*='qrcode']"], "ttl": 120}
        if frame_origins:
            qr["frame_origins"] = [self.embed]
        return {"title": {"zh": "本地", "en": "Local"}, "origins": [self.publisher], "login_method": "qr",
                "login_url": self.publisher + "/login", "check_url": self.publisher + "/desk",
                "in_url": ["^" + esc + "/desk$"], "out_url": ["^" + esc + "/login$"], "qr": qr}

    def capture(self, template):
        cards = bl.LoginCards(Mock())
        got = cards._open_and_capture(self.rt, "fixture", template)
        if got.get("cdp"):
            self.addCleanup(got["cdp"].close)
        return got

    # -- tests
    def test_waits_for_the_embedded_window_and_crops_the_element_inside(self):
        got = self.capture(self.template())
        self.assertEqual(got["state"], "qr", got.get("reason"))
        png = got["png"]
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertTrue(painted(png))
        img = qrcrop.png_decode(png)
        # element crop (160 CSS px + 2 × 8 px margin, at scale 2), never the 208 px window or the page around it
        self.assertEqual((img.w, img.h), (352, 352))
        if qrcrop.available():
            self.assertEqual([digest for digest, _ in qrcrop.find(img)], [hashlib.sha256(FIXTURE_TEXT.encode()).hexdigest()])

    def test_embedded_window_of_an_unlisted_origin_is_never_searched(self):
        with patch.object(bl, "QR_WAIT", 8.0):
            got = self.capture(self.template(frame_origins=False))
        self.assertEqual(got, {"state": "none", "reason": "qr_not_found"})

    def test_listed_origin_nested_in_an_unlisted_window_is_never_searched(self):
        self.unlisted = self.enterContext(serve(self.wrapper))
        t = self.template()
        t["login_url"] = self.publisher + "/nested"
        t["out_url"] = ["^" + re.escape(self.publisher) + "/nested$"]
        with patch.object(bl, "QR_WAIT", 8.0):
            got = self.capture(t)
        self.assertEqual(got, {"state": "none", "reason": "qr_not_found"})
        # control: the same nested page with the wrapper's origin listed too finds the code
        t["qr"]["frame_origins"] = [self.unlisted, self.embed]
        self.assertEqual(self.capture(t)["state"], "qr")

    def test_embedded_window_still_loading_reports_page_not_ready(self):
        self.slow = True
        with patch.object(bl, "QR_WAIT", 3.0):
            got = self.capture(self.template())
        self.assertEqual(got, {"state": "none", "reason": "page_not_ready"})


class Retries(unittest.TestCase):
    """No browser: the capture step with a synthetic clock and scripted crop outcomes."""

    def run_capture(self, outcomes):
        driver = Mock()
        driver.location.return_value = ("https://example.invalid/login", "complete")
        clock = itertools.count()
        rule = {"login_url": "https://example.invalid/login", "origins": ["https://example.invalid"],
                "out_url": [r"^https://example\.invalid/login$"],
                "qr": {"selectors": ["img"], "frame_origins": ["https://embed.example.invalid"]}}
        with patch.object(bl, "CDP", return_value=driver), patch.object(bl, "capture_qr", side_effect=outcomes) as cap, \
                patch.object(bl, "QR_WAIT", 20), patch.object(bl.time, "sleep"), \
                patch.object(bl.time, "monotonic", side_effect=lambda: next(clock)):
            got = bl.LoginCards(Mock())._open_and_capture({"ws": "fixture"}, "fixture", rule)
        for call in cap.call_args_list:     # the embedded window's origin is passed to every crop
            self.assertEqual(call.kwargs["origins"], ["https://example.invalid", "https://embed.example.invalid"])
        return got, cap.call_count

    def test_first_timeout_and_a_blank_crop_are_retried(self):
        png = b"\x89PNG fixture"
        got, calls = self.run_capture([CDPTimeout("x"), QRBlank("blank"), png, png])
        self.assertEqual((got["state"], got["png"]), ("qr", png))
        self.assertEqual(calls, 4)

    def test_three_blank_crops_name_the_reason(self):
        got, calls = self.run_capture([QRBlank("blank")] * 5)
        self.assertEqual((got, calls), ({"state": "none", "reason": "capture_blank"}, 3))

    def test_three_timeouts_stop_at_three(self):
        got, calls = self.run_capture([CDPTimeout("x")] * 5)
        self.assertEqual((got, calls), ({"state": "none", "reason": "capture_timeout"}, 3))

    def test_unstable_crops_stop_at_three(self):
        got, calls = self.run_capture([b"\x89PNG a", b"\x89PNG b", b"\x89PNG c", b"\x89PNG d", b"\x89PNG e", b"\x89PNG f"])
        self.assertEqual((got, calls), ({"state": "none", "reason": "capture_timeout"}, 6))

    def test_loading_embedded_window_reports_page_not_ready(self):
        got, _ = self.run_capture(itertools.repeat(QRFrameLoading("loading")))
        self.assertEqual(got, {"state": "none", "reason": "page_not_ready"})

    def test_blank_detection_on_real_pngs(self):
        flat = qrcrop.png_encode(qrcrop.Img(418, 418, 3, bytes([245]) * (418 * 418 * 3)))
        self.assertFalse(painted(flat))
        self.assertTrue(painted(qr_png()))
        self.assertTrue(painted(b"\x89PNG not decodable"))

    def test_page_supplied_rectangle_is_bounded_before_any_capture(self):
        from agentj import browser_cdp
        driver = Mock()
        with patch.object(browser_cdp, "locate_qr", return_value=("found", (0.0, 0.0, 5000.0, 5000.0))):
            self.assertIsNone(browser_cdp.capture_qr(driver, "s", ["img"]))
        driver.call.assert_not_called()
        huge = qrcrop.png_encode(qrcrop.Img(2000, 2000, 1, bytes(2000 * 2000)))
        self.assertFalse(painted(huge))

    def test_reason_is_a_card_reason(self):
        self.assertIn("capture_blank", bl.ISSUE_REASONS)


if __name__ == "__main__":
    unittest.main()
