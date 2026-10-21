"""P411 (#172 + #76): screen help — the QR crop (qrcrop.py), the screen-help cards in serve (screenhelp.HelpCards via
elevate.sock), the narrow channel for the owner's own scripts, and the `agentj qr push` / `agentj screen ask` CLI."""
import _hermetic  # noqa: F401
import time
import asyncio
import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
import pathlib
import struct
import tempfile
import threading
import unittest
import zlib
from unittest.mock import patch

from agentj import elevate, qrcrop, screenhelp
from agentj.state import State

ZX = qrcrop.available()
need_zx = unittest.skipUnless(ZX, "zxing-cpp not installed in this environment (uv sync adds it)")


# ------------------------------------------------------------------ sample pictures (no OpenCV, no network)
def qr_gray(text: str, scale: int = 6) -> tuple[int, bytes]:
    import zxingcpp as z
    im = z.write_barcode_to_image(z.create_barcode(text, z.BarcodeFormat.QRCode), scale=scale, add_quiet_zones=False)
    return im.shape[1], bytes(memoryview(im))


def page_with_qr(text="https://example.com/login?t=abc", w=900, h=700, x0=240, y0=160, card=True):
    """A dark page, a white card, the code on it — like a sign-in page; RGB."""
    n, g = qr_gray(text)
    px = bytearray(bytes([38, 40, 44]) * w * h)
    if card:
        for y in range(y0 - 30, y0 + n + 120):
            px[(y * w + x0 - 30) * 3:(y * w + x0 + n + 30) * 3] = b"\xff" * ((n + 60) * 3)
    for y in range(n):
        px[((y0 + y) * w + x0) * 3:((y0 + y) * w + x0 + n) * 3] = bytes(v for p in g[y * n:(y + 1) * n] for v in (p, p, p))
    return qrcrop.png_encode(qrcrop.Img(w, h, 3, bytes(px))), (x0, y0, n)


def png_filtered(w, h, ch, data, filt):
    """A PNG whose every row uses filter `filt` (encoder side of the five PNG filters)."""
    st = w * ch
    rows, prev = [], bytes(st)
    for y in range(h):
        cur = data[y * st:(y + 1) * st]
        out = bytearray(st)
        for i in range(st):
            a = cur[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if filt == 0:
                p = 0
            elif filt == 1:
                p = a
            elif filt == 2:
                p = b
            elif filt == 3:
                p = (a + b) >> 1
            else:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                p = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out[i] = (cur[i] - p) & 255
        rows.append(bytes([filt]) + bytes(out))
        prev = cur

    def chunk(k, b):
        return struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b) & 0xFFFFFFFF)
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[ch]
    return (qrcrop.PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


class Png(unittest.TestCase):
    def test_all_five_filters_and_channel_counts_round_trip(self):
        import random
        rnd = random.Random(411)
        for ch in (1, 2, 3, 4):
            data = bytes(rnd.getrandbits(8) for _ in range(13 * 9 * ch))
            for f in range(5):
                img = qrcrop.png_decode(png_filtered(13, 9, ch, data, f))
                self.assertIsNotNone(img, (ch, f))
                self.assertEqual((img.w, img.h, img.ch, img.data), (13, 9, ch, data), (ch, f))
            back = qrcrop.png_decode(qrcrop.png_encode(qrcrop.Img(13, 9, ch, data)))
            self.assertEqual(back.data, data)

    def test_not_png_or_broken_is_none_never_raises(self):
        for b in (b"", b"GIF89a....", qrcrop.PNG_SIG + b"\0\0\0\rIHDR", b"\xff\xd8\xff\xe0jpeg"):
            self.assertIsNone(qrcrop.png_decode(b))
        out, info = qrcrop.tidy(b"\xff\xd8\xff\xe0 a jpeg")
        self.assertIsNone(out)
        self.assertEqual(info["reason"], "not_png")


class Crop(unittest.TestCase):
    @need_zx
    def test_local_cut_is_the_code_with_a_white_quiet_zone_and_decodes_the_same(self):
        png, (x0, y0, n) = page_with_qr()
        out, info = qrcrop.tidy(png)
        self.assertEqual(info["method"], "local", info)
        self.assertEqual(info["codes"], 1)
        img = qrcrop.png_decode(out)
        self.assertLess(img.w * img.h, 900 * 700 / 3)            # the code, not the page
        self.assertLessEqual(abs(img.w - n), n * 0.35)
        ring = img.data[:img.w * img.ch] + img.data[-img.w * img.ch:]
        self.assertEqual(set(ring), {255})                        # fresh white edge: no dark page background
        before = {h for h, _ in qrcrop.find(qrcrop.png_decode(png))}
        self.assertEqual({h for h, _ in qrcrop.find(img)}, before)
        self.assertNotIn("example.com", json.dumps(info))         # content is never returned

    @need_zx
    def test_cut_that_decodes_differently_falls_back_to_the_original(self):
        png, _ = page_with_qr()
        real = qrcrop.find
        calls = []

        def fake(img):
            calls.append(1)
            return real(img) if len(calls) == 1 else [("f" * 64, [[0, 0], [1, 0], [1, 1], [0, 1]])]
        with patch.object(qrcrop, "find", side_effect=fake):
            out, info = qrcrop.tidy(png)
        self.assertIsNone(out)
        self.assertEqual(info["reason"], "verify_failed")

    @need_zx
    def test_no_code_and_no_vision_key_sends_the_original(self):
        blank = qrcrop.png_encode(qrcrop.Img(64, 64, 1, bytes(64 * 64)))
        out, info = qrcrop.tidy(blank, vision=True, env={})
        self.assertIsNone(out)
        self.assertEqual(info["reason"], "no_code")

    def test_without_zxing_the_picture_goes_uncut(self):
        png, _ = page_with_qr() if ZX else (qrcrop.png_encode(qrcrop.Img(8, 8, 1, bytes(64))), None)
        with patch.object(qrcrop, "_zx", return_value=None):
            out, info = qrcrop.tidy(png)
        self.assertIsNone(out)
        self.assertEqual((info["local"], info["reason"]), (False, "no_local_detector"))

    @need_zx
    def test_vision_fallback_against_a_fake_upstream_and_its_check(self):
        png, (x0, y0, n) = page_with_qr()
        w, h = 900, 700
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen["auth"], seen["model"] = self.headers.get("Authorization"), body["model"]
                seen["img"] = body["messages"][0]["content"][1]["image_url"]["url"][:22]
                corners = {"top_left": [x0 / w * 1000, y0 / h * 1000], "top_right": [(x0 + n) / w * 1000, y0 / h * 1000],
                           "bottom_right": [(x0 + n) / w * 1000, (y0 + n) / h * 1000],
                           "bottom_left": [x0 / w * 1000, (y0 + n) / h * 1000]}
                data = json.dumps({"choices": [{"message": {"content": "```json\n" + json.dumps(corners) + "\n```"}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        env = {"AGENTJ_VISION_URL": f"http://127.0.0.1:{srv.server_port}/v1", "AGENTJ_VISION_MODEL": "fake-vision",
               "AGENTJ_VISION_API_KEY": "test-key-not-real"}
        real = qrcrop.find
        first = []

        def miss_once(img):                     # the local detector "misses" the full picture, reads the cut
            first.append(1)
            return [] if len(first) == 1 else real(img)
        with patch.object(qrcrop, "find", side_effect=miss_once):
            off, info0 = qrcrop.tidy(png, vision=False, env=env)
        self.assertIsNone(off)                                    # opt-in only
        self.assertEqual(seen, {})
        first.clear()
        with patch.object(qrcrop, "find", side_effect=miss_once):
            out, info = qrcrop.tidy(png, vision=True, env=env)
        self.assertEqual(info["method"], "vision", info)
        self.assertEqual((seen["auth"], seen["model"], seen["img"]), ("Bearer test-key-not-real", "fake-vision",
                                                                      "data:image/png;base64,"))
        self.assertNotIn("test-key-not-real", json.dumps(info))
        self.assertTrue(real(qrcrop.png_decode(out)))
        # a model answer whose cut does not decode → the original
        first.clear()
        with patch.object(qrcrop, "find", side_effect=lambda img: []):
            out, info = qrcrop.tidy(png, vision=True, env=env)
        self.assertIsNone(out)
        self.assertEqual(info["reason"], "verify_failed")


# ------------------------------------------------------------------ serve's side
class Sess:
    def __init__(self, device, state="ready"):
        self.device, self.state, self.pub = device, state, b"p"


class FakeHost:
    def __init__(self, st, approvers=("dev1",)):
        self.st, self.lang = st, "zh"
        self.sent, self.pushed = [], []
        self.approvers = list(approvers)
        self.sessions = {1: Sess("dev1")}
        self.stop_on = False

    def stopped(self):
        return self.stop_on

    def _approvers(self):
        return self.approvers

    async def _send_ready(self, fn):
        self.sent.append(fn(None))

    async def send_app(self, s, obj):
        self.sent.append(obj)
        return True

    def push_notify(self, kind):
        self.pushed.append(kind)


class FakeElev:
    def __init__(self, host):
        self.host, self.st = host, host.st


PNG = qrcrop.png_encode(qrcrop.Img(4, 4, 1, bytes(16)))
B64 = base64.b64encode(PNG).decode()


class Cards(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state"); self.st.root.mkdir(mode=0o700)
        self.logs = []
        p = patch.object(State, "log", lambda st, ev, **kw: self.logs.append((ev, kw))); p.start(); self.addCleanup(p.stop)
        p = patch.object(State, "is_allowed", lambda st, pub: True); p.start(); self.addCleanup(p.stop)
        self.host = FakeHost(self.st)
        self.hc = screenhelp.HelpCards(FakeElev(self.host))
        self.addCleanup(self.hc.end_all, "gone")

    async def push(self, **kw):
        return await self.hc.push({"t": "help_push", "kind": "screen", "img": B64, "text": "验证码是什么？", **kw})

    async def test_push_puts_one_card_on_the_phones_and_logs_no_content(self):
        res = await self.push(by="agent")
        self.assertEqual((res["result"], res["devices"]), ("sent", 1))
        card = self.host.sent[-1]
        self.assertEqual(card["t"], "help_card")
        self.assertEqual((card["kind"], card["mime"], card["reply"], card["by"], card["from"], card["text"]),
                         ("screen", "image/png", "text", "agent", "agent", "验证码是什么？"))
        self.assertEqual(base64.b64decode(card["img"]), PNG)
        self.assertLessEqual(card["ttl"], 300)
        self.assertEqual(self.host.pushed, ["ask"])
        logged = json.dumps(self.logs, ensure_ascii=False)
        self.assertNotIn("验证码", logged)
        self.assertNotIn(B64[:40], logged)
        self.assertEqual(self.hc.open_msgs(None)[0]["id"], res["id"])      # re-sent after a reconnect / cards_get

    async def test_only_pictures_within_the_limits(self):
        pdf = base64.b64encode(b"%PDF-1.7 not a picture").decode()
        self.assertEqual((await self.hc.push({"kind": "qr", "img": pdf}))["result"], "bad")
        self.assertEqual((await self.hc.push({"kind": "qr", "img": "!!notbase64"}))["result"], "bad")
        big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(screenhelp.IMG_MAX)).decode()
        self.assertEqual((await self.hc.push({"kind": "qr", "img": big}))["result"], "bad")
        self.assertEqual((await self.hc.push({"kind": "file", "img": B64}))["result"], "bad")
        self.assertEqual((await self.hc.push({"kind": "qr", "img": B64, "from": "../etc"}))["result"], "bad")
        jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + bytes(20)).decode()
        self.assertEqual((await self.hc.push({"kind": "qr", "img": jpeg}))["result"], "sent")
        self.assertEqual(self.host.sent[-1]["mime"], "image/jpeg")

    async def test_rate_busy_no_device_and_stop(self):
        self.host.stop_on = True
        self.assertEqual((await self.push())["result"], "stopped")
        self.host.stop_on = False
        self.host.approvers = []
        self.assertEqual((await self.push())["result"], "no_device")
        self.host.approvers = ["dev1"]
        for _ in range(screenhelp.MAX_OPEN):
            self.assertEqual((await self.push(**{"from": "cron-a"}))["result"], "sent")
        self.assertEqual((await self.push(**{"from": "cron-a"}))["result"], "busy")
        self.hc.end_all("gone")
        for _ in range(screenhelp.RATE - screenhelp.MAX_OPEN):
            self.assertEqual((await self.push(**{"from": "cron-a"}))["result"], "sent")
        self.hc.end_all("gone")
        self.assertEqual((await self.push(**{"from": "cron-a"}))["result"], "rate")
        self.assertEqual((await self.push(**{"from": "cron-b"}))["result"], "sent")     # per source
        self.assertIn(("screen_help_refused", {"kind": "screen", "by": "auto", "reason": "rate"}), self.logs)

    async def test_typed_answer_goes_back_once_and_is_never_logged(self):
        res = await self.push()
        c = self.host.sent[-1]
        with patch.object(State, "sign_key", return_value=b"k" * 32):
            await self.hc.on_phone(Sess("dev1"), {"t": "help_text", "id": res["id"], "n": c["n"], "text": " 482913 "})
        self.assertEqual(self.hc.cards, {})
        got = await self.hc.wait({"id": res["id"], "wait": 0})
        self.assertEqual((got["result"], got["answer"]), ("answered", "482913"))
        again = await self.hc.wait({"id": res["id"], "wait": 0})
        self.assertNotIn("answer", again)
        self.assertTrue(again["answer_taken"])
        self.assertNotIn("482913", json.dumps(self.logs))
        await asyncio.sleep(0.01)
        self.assertIn({"t": "help_end", "id": res["id"], "result": "answered"}, self.host.sent)

    async def test_wait_returns_when_the_owner_answers(self):
        res = await self.push()
        c = self.host.sent[-1]

        async def later():
            await asyncio.sleep(0.05)
            with patch.object(State, "sign_key", return_value=b"k" * 32):
                await self.hc.on_phone(Sess("dev1"), {"t": "help_done", "id": res["id"], "n": c["n"]})
        task = asyncio.create_task(later())
        got = await self.hc.wait({"id": res["id"], "wait": 5})
        await task
        self.assertEqual(got["result"], "done")
        pend = await self.push()
        self.assertEqual((await self.hc.wait({"id": pend["id"], "wait": 0.01}))["result"], "pending")

    async def test_secret_like_answer_is_refused_on_the_phone(self):
        res = await self.push()
        c = self.host.sent[-1]
        with patch.object(State, "sign_key", return_value=b"k" * 32):
            await self.hc.on_phone(Sess("dev1"), {"t": "help_text", "id": res["id"], "n": c["n"],
                                                  "text": "sk-proj-" + "Ab3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z"})
        self.assertEqual(self.host.sent[-1], {"t": "help_err", "id": res["id"], "why": "secret"})
        self.assertIn(res["id"], self.hc.cards)                   # still open: the owner can answer properly

    async def test_unapproved_device_wrong_nonce_and_reply_mode(self):
        res = await self.push(reply="done")
        c = self.host.sent[-1]
        with patch.object(State, "sign_key", return_value=None):
            await self.hc.on_phone(Sess("devX"), {"t": "help_done", "id": res["id"], "n": c["n"]})
        self.assertIn(res["id"], self.hc.cards)
        with patch.object(State, "sign_key", return_value=b"k" * 32):
            await self.hc.on_phone(Sess("dev1"), {"t": "help_done", "id": res["id"], "n": "0" * 32})
            self.assertEqual(self.host.sent[-1]["why"], "gone")
            await self.hc.on_phone(Sess("dev1"), {"t": "help_text", "id": res["id"], "n": c["n"], "text": "x"})
            self.assertEqual(self.host.sent[-1]["why"], "shape")   # a done-only card takes no text
            await self.hc.on_phone(Sess("dev1"), {"t": "help_cancel", "id": res["id"], "n": c["n"]})
        self.assertEqual((await self.hc.wait({"id": res["id"]}))["result"], "cancelled")

    async def test_expiry_wipes_the_picture(self):
        with patch.dict(screenhelp.TTL, {"screen": (300, 0, 900)}):
            res = await self.push(ttl=0)
        card = next(iter(self.hc.cards.values())) if self.hc.cards else None
        await asyncio.sleep(0.05)
        self.assertEqual((await self.hc.wait({"id": res["id"], "wait": 0}))["result"], "expired")
        if card:
            self.assertEqual(card["img"], bytearray())

    async def test_page_change_end_and_stop_everything(self):
        res = await self.push()
        self.assertEqual((await self.hc.end_req({"id": res["id"], "result": "page_changed"}))["result"], "page_changed")
        self.assertEqual((await self.hc.end_req({"id": res["id"], "result": "done"}))["result"], "bad")
        res2 = await self.push()
        self.hc.end_all("stopped")
        self.assertEqual((await self.hc.wait({"id": res2["id"]}))["result"], "stopped")
        self.assertEqual((await self.hc.wait({"id": "nope"}))["result"], "unknown")


class Socket(unittest.IsolatedAsyncioTestCase):
    """The real elevate.sock round trip: a 1.3 MB picture fits one request line; the phone answer arrives."""

    async def test_round_trip_over_elevate_sock(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name) / "s"); st.root.mkdir(mode=0o700)
        host = FakeHost(st)
        elev = elevate.Elevator.__new__(elevate.Elevator)
        elev.host, elev.st = host, st
        elev.help = screenhelp.HelpCards(elev)
        with patch.object(State, "is_allowed", lambda s, pub: True):
            path = pathlib.Path(tmp.name) / "e.sock"
            srv = await asyncio.start_unix_server(elev.on_client, path=str(path), limit=elevate.MAX_FRAME)
            self.addCleanup(srv.close)
            big = b"\x89PNG\r\n\x1a\n" + os.urandom(1_300_000)
            req = {"t": "help_push", "kind": "qr", "img": base64.b64encode(big).decode(), "text": "请扫码", "by": "auto",
                   "from": "nightly-backup"}
            res = await asyncio.to_thread(elevate.client_request, st, req, 20, None, str(path))
            self.assertEqual(res["result"], "sent", res)
            card = host.sent[-1]
            self.assertEqual((card["from"], card["by"]), ("nightly-backup", "auto"))
            self.assertEqual(len(base64.b64decode(card["img"])), len(big))

            async def answer():
                await asyncio.sleep(0.1)
                with patch.object(State, "sign_key", return_value=b"k" * 32):
                    await elev.on_phone(Sess("dev1"), {"t": "help_done", "id": res["id"], "n": card["n"]})
            task = asyncio.create_task(answer())
            got = await asyncio.to_thread(elevate.client_request, st, {"t": "help_wait", "id": res["id"], "wait": 5}, 20,
                                          None, str(path))
            await task
            self.assertEqual(got["result"], "done")
            self.assertIn("help_done", elevate.PHONE_TYPES)


def chrome_path():
    for p in (os.environ.get("AGENTJ_TEST_CHROME"), "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


@unittest.skipUnless(chrome_path(), "needs a Chromium (AGENTJ_TEST_CHROME)")
class RealBrowser(unittest.IsolatedAsyncioTestCase):
    """help_capture / help_page against a real headless Chromium: the element, the whole page, the address change."""

    async def asyncSetUp(self):
        from agentj import browser
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state"); self.st.root.mkdir(mode=0o700)
        png = page_with_qr(w=360, h=420, x0=60, y0=60)[0] if ZX else PNG
        page = ("<!doctype html><body style='margin:0;background:#26282c'><h1 style='color:#fff'>Sign in</h1>"
                "<img id='qr' width='240' height='240' style='image-rendering:pixelated' src='data:image/png;base64," + base64.b64encode(png).decode() + "'></body>").encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = page if self.path.startswith("/login") else b"<p>signed in</p>"
                self.send_response(200); self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.origin = f"http://127.0.0.1:{self.srv.server_address[1]}"
        p = patch.object(browser, "installed", return_value={"version": "sys", "platform": "linux-x64", "path": chrome_path()})
        p.start(); self.addCleanup(p.stop)
        browser.ensure_dir(self.st)
        self.proc, _ = browser.start_browser(self.st, headless=True)
        self.addCleanup(browser.stop_proc, self.proc)
        self.hc = screenhelp.HelpCards(FakeElev(FakeHost(self.st)))

    async def fixture_ready(self, cdp, session, path, selector=None):
        # Target.createTarget announces the requested URL before the renderer leaves about:blank.
        # Wait for our own fixture, not a fixed sleep; all capture assertions below remain unchanged.
        expression = "location.pathname === %s && document.readyState === 'complete'" % json.dumps(path)
        if selector:
            expression += " && !!document.querySelector(%s)" % json.dumps(selector)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session=session)
            if result.get("result", {}).get("value") is True:
                return
            await asyncio.sleep(0.05)
        self.fail("local browser fixture did not navigate to " + path)

    async def test_capture_element_page_and_watch(self):
        from agentj import browser
        from agentj.browser_cdp import CDP
        rt = browser.runtime(self.st)
        with CDP(rt["ws"]) as cdp:
            target = cdp.new_tab("about:blank", background=False)
            session = cdp.attach(target)
            navigation = cdp.call("Page.navigate", {"url": self.origin + "/login"}, session=session)
            self.assertNotIn("errorText", navigation, navigation)
            await self.fixture_ready(cdp, session, "/login", "#qr")
        self.assertEqual((await self.hc.capture({"tab": "nowhere.example"}))["result"], "no_tab")
        self.assertEqual((await self.hc.capture({"tab": "/login", "selector": "#missing"}))["result"], "no_element")
        el = await self.hc.capture({"tab": "/login", "selector": "#qr"})
        self.assertEqual(el["result"], "ok", el)
        whole = await self.hc.capture({"tab": "/login"})
        self.assertEqual(whole["result"], "ok", whole)
        a, b = base64.b64decode(el["img"]), base64.b64decode(whole["img"])
        self.assertTrue(a.startswith(qrcrop.PNG_SIG) and b.startswith(qrcrop.PNG_SIG))
        self.assertNotIn("127.0.0.1", json.dumps(el))                 # the address only as a short hash
        if ZX:
            for shot in (a, b):
                out, info = qrcrop.tidy(shot)
                self.assertEqual(info["method"], "local", info)
        pg = await self.hc.page({"target": el["target"], "selector": "#qr"})
        self.assertEqual((pg["result"], pg["page"], pg["present"]), ("ok", el["page"], True))
        with CDP(rt["ws"]) as cdp:
            s = cdp.attach(el["target"])
            cdp.call("Page.navigate", {"url": self.origin + "/app"}, session=s)
            await self.fixture_ready(cdp, s, "/app")
        pg = await self.hc.page({"target": el["target"], "selector": "#qr"})
        self.assertNotEqual(pg["page"], el["page"])
        self.assertFalse(pg["present"])


# ------------------------------------------------------------------ the CLI
class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.answers = []

    def run_cli(self, argv, env=None):
        def fake_call(req, timeout):
            self.calls.append(req)
            return self.answers.pop(0) if self.answers else {"result": "sent", "id": "a" * 32, "ttl": 120, "devices": 1}
        a = screenhelp.run_argparse_for_tests(argv)
        out = io.StringIO()
        with patch.object(screenhelp, "_call", side_effect=fake_call), patch("sys.stdout", out), \
                patch.dict(os.environ, env or {}, clear=False), self.assertRaises(SystemExit) as e:
            a.fn(a)
        return e.exception.code, json.loads(out.getvalue())

    def file(self, name, data):
        p = pathlib.Path(self.tmp.name) / name
        p.write_bytes(data)
        return str(p)

    def test_a_non_picture_file_is_refused_before_anything_is_sent(self):
        code, out = self.run_cli(["qr", "push", self.file("x.pdf", b"%PDF-1.7")])
        self.assertEqual((code, out["result"], out["shown"]), (2, "bad", False))
        self.assertEqual(self.calls, [])

    @need_zx
    def test_qr_push_sends_the_cut_and_reports_how(self):
        png, _ = page_with_qr()
        with patch.dict(os.environ, {"AGENTJ_FENCED": "1"}):
            code, out = self.run_cli(["qr", "push", self.file("p.png", png), "--text", "请用微信扫码", "--compact"])
        self.assertEqual((code, out["result"], out["crop"]["method"]), (0, "sent", "local"))
        req = self.calls[0]
        self.assertEqual((req["t"], req["kind"], req["by"], req["text"], req["wait"]), ("help_push", "qr", "agent",
                                                                                         "请用微信扫码", 0))
        self.assertLess(len(base64.b64decode(req["img"])), len(png))

    def test_no_crop_and_a_script_source(self):
        png = qrcrop.png_encode(qrcrop.Img(8, 8, 1, bytes(64)))
        env = {k: "" for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "OPENCODE", "AGENTJ_FENCED")}
        with patch.object(screenhelp, "_caller", return_value="auto"):
            code, out = self.run_cli(["qr", "push", self.file("p.png", png), "--no-crop", "--from", "cron-login"], env)
        self.assertEqual(code, 0)
        self.assertNotIn("crop", out)
        self.assertEqual((self.calls[0]["from"], self.calls[0]["by"]), ("cron-login", "auto"))
        self.assertEqual(base64.b64decode(self.calls[0]["img"]), png)

    def test_screen_ask_waits_and_returns_the_answer(self):
        self.answers = [{"result": "answered", "id": "b" * 32, "answer": "7KQ2"}]
        code, out = self.run_cli(["screen", "ask", self.file("s.png", PNG), "-q", "验证码是什么？", "--wait-s", "30"])
        self.assertEqual((code, out["result"], out["answer"]), (0, "answered", "7KQ2"))
        self.assertEqual((self.calls[0]["kind"], self.calls[0]["reply"], self.calls[0]["wait"]), ("screen", "text", 30))

    def test_pending_tells_how_to_keep_waiting(self):
        self.answers = [{"result": "pending", "id": "c" * 32}]
        code, out = self.run_cli(["screen", "ask", self.file("s.png", PNG)])
        self.assertEqual((code, out["result"]), (4, "pending"))
        self.assertIn("agentj screen wait " + "c" * 32, out["note"])

    def test_page_capture_and_wait_until_page_change(self):
        self.answers = [{"result": "ok", "img": B64, "mime": "image/png", "target": "T" * 32, "page": "p1"},
                        {"result": "sent", "id": "d" * 32, "ttl": 120, "devices": 2},
                        {"result": "pending", "id": "d" * 32},
                        {"result": "ok", "page": "p1", "present": True},
                        {"result": "pending", "id": "d" * 32},
                        {"result": "ok", "page": "p2"},
                        {"result": "page_changed", "id": "d" * 32}]
        code, out = self.run_cli(["qr", "push", "--selector", "#qr", "--wait-until", "page-change", "--no-crop"])
        self.assertEqual((code, out["result"]), (0, "page_changed"))
        self.assertEqual([c["t"] for c in self.calls], ["help_capture", "help_push", "help_wait", "help_page", "help_wait",
                                                         "help_page", "help_end"])
        self.assertEqual(self.calls[0], {"t": "help_capture", "selector": "#qr"})
        self.assertEqual(self.calls[-1]["result"], "page_changed")

    def test_page_change_needs_the_browser(self):
        code, out = self.run_cli(["qr", "push", self.file("p.png", PNG), "--wait-until", "page-change"])
        self.assertEqual((code, out["result"]), (2, "bad"))

    def test_serve_not_running(self):
        self.answers = [{"result": "unavailable"}]
        code, out = self.run_cli(["screen", "wait", "e" * 32])
        self.assertEqual((code, out["result"], out["shown"]), (1, "unavailable", False))

    def test_screen_capture_without_a_tool_is_honest(self):
        with patch.object(screenhelp.shutil, "which", return_value=None):
            code, out = self.run_cli(["screen", "ask", "--screen"])
        self.assertEqual((code, out["result"]), (5, "no_screen"))
        self.assertEqual(self.calls, [])


class BackgroundTab(unittest.TestCase):
    """A background tab never paints, so Page.captureScreenshot hangs (60 s measured 10-20): activate it first."""

    def test_capture_activates_the_tab_before_the_screenshot(self):
        from agentj import browser, browser_cdp
        log = []

        class FakeCDP:
            def __init__(self, ws, *a, **k):
                self.front = False

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def attach(self, tid):
                return "S1"

            def detach(self, s):
                pass

            def call(self, method, params=None, session=None, timeout=None):
                log.append(method)
                if method == "Target.getTargets":
                    return {"targetInfos": [{"type": "page", "targetId": "ABCDEF0123", "url": "https://x.example/login"}]}
                if method == "Runtime.evaluate":
                    return {"result": {"value": "hidden"}}
                if method == "Target.activateTarget":
                    self.front = params == {"targetId": "ABCDEF0123"}
                    return {}
                if method == "Page.captureScreenshot":
                    if not self.front:
                        raise browser_cdp.CDPError("timeout")       # what a background tab does
                    return {"data": base64.b64encode(PNG).decode()}
                return {}
        with patch.object(browser, "runtime", return_value={"ws": "ws://x"}), \
                patch.object(browser_cdp, "CDP", FakeCDP):
            res = screenhelp.HelpCards(FakeElev(FakeHost(State(pathlib.Path(tempfile.mkdtemp()) / "s"))))._capture({})
        self.assertEqual(res["result"], "ok", res)
        self.assertLess(log.index("Target.activateTarget"), log.index("Page.captureScreenshot"))


class OneScreen(unittest.TestCase):
    """--screen takes one monitor, never the stitched desktop (7680×9440, mostly empty, measured 10-20)."""

    def grab(self, env, tools, outputs=None, platform="linux"):
        ran = []

        def run(cmd, **k):
            ran.append(cmd)
            if cmd[0] in ("hyprctl", "swaymsg", "xrandr"):
                return type("R", (), {"stdout": outputs.get(cmd[0], "") if outputs else ""})()
            with open(cmd[-1], "wb") as fh:
                fh.write(PNG)
            return type("R", (), {"stdout": ""})()
        with patch.dict(os.environ, env, clear=False), \
                patch.object(screenhelp.sys, "platform", platform), \
                patch.object(screenhelp.shutil, "which", side_effect=lambda c: f"/usr/bin/{c}" if c in tools else None), \
                patch.object(screenhelp.subprocess, "run", side_effect=run):
            for k in ("WAYLAND_DISPLAY", "DISPLAY"):
                if k not in env:
                    os.environ.pop(k, None)
            data, why = screenhelp.grab_screen()
        self.assertEqual((data, why), (PNG, ""))
        return [c for c in ran if c[0] not in ("hyprctl", "swaymsg", "xrandr")][0]

    def test_wayland_takes_the_focused_output(self):
        mons = json.dumps([{"name": "DP-1", "focused": False}, {"name": "eDP-1", "focused": True}])
        cmd = self.grab({"WAYLAND_DISPLAY": "wayland-1"}, {"grim", "hyprctl"}, {"hyprctl": mons})
        self.assertEqual(cmd[:3], ["grim", "-o", "eDP-1"])

    def test_sway_and_unknown_compositor(self):
        outs = json.dumps([{"name": "HDMI-A-1", "active": False}, {"name": "DP-2", "active": True}])
        self.assertEqual(self.grab({"WAYLAND_DISPLAY": "w"}, {"grim", "swaymsg"}, {"swaymsg": outs})[:3],
                         ["grim", "-o", "DP-2"])
        plain = self.grab({"WAYLAND_DISPLAY": "w"}, {"grim"})               # no way to tell: plain grim, as before
        self.assertEqual((plain[0], len(plain)), ("grim", 2))

    def test_macos_main_display_only(self):
        cmd = self.grab({}, {"screencapture"}, platform="darwin")
        self.assertEqual(cmd[:4], ["screencapture", "-x", "-D", "1"])

    def test_x11_crops_the_primary_monitor(self):
        xr = ("Screen 0: minimum 8 x 8, current 5760 x 2160\n"
              "HDMI-1 connected 3840x2160+1920+0 (normal) 600mm x 340mm\n"
              "eDP-1 connected primary 1920x1080+0+0 (normal) 344mm x 194mm\n"
              "DP-1 disconnected (normal)\n")
        cmd = self.grab({"DISPLAY": ":0"}, {"import", "xrandr"}, {"xrandr": xr})
        self.assertEqual(cmd[:6], ["import", "-window", "root", "-crop", "1920x1080+0+0", "+repage"])
        one = "eDP-1 connected primary 1920x1080+0+0 (normal)\n"
        self.assertNotIn("-crop", self.grab({"DISPLAY": ":0"}, {"import", "xrandr"}, {"xrandr": one}))


class Wiring(unittest.TestCase):
    def test_root_parser_has_the_commands_once(self):
        from agentj import cli
        src = pathlib.Path(cli.__file__).read_text()
        self.assertEqual(src.count("screenhelp.add_parser(sub)"), 1)
        for name in ('add_parser("qr"', 'add_parser("screen"'):
            hits = sum(pathlib.Path(p).read_text().count(name) for p in pathlib.Path(cli.__file__).parent.glob("*.py"))
            self.assertEqual(hits, 1, name)


    def test_protocol_and_skill_name_the_messages_and_commands(self):
        aj = pathlib.Path(__file__).resolve().parents[2]
        proto = (aj / "protocol/PROTOCOL.md").read_text(encoding="utf-8")
        for t in screenhelp.PHONE_TYPES + ("help_card", "help_end", "help_err", "help_capture", "help_push", "help_wait",
                                           "help_page", "§37"):
            self.assertIn(t, proto)
        skill = (aj / "host/agentj/skills/agentj-screen-help/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: agentj-screen-help", skill)
        for w in ("agentj qr push", "--wait-until page-change", "agentj screen ask", "--screen", "agentj screen wait",
                  "--from", "agentj secret request"):
            self.assertIn(w, skill)
        self.assertIn("agentj-screen-help", (aj / "host/agentj/skills/agentj-browser/SKILL.md").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
