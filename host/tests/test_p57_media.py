"""F21 (0.15.2, PROTOCOL §13) — media out: files the Agent shows to the phone.

Units: reference extraction (Markdown image / link, backticks, bare paths, file://, never http(s), never inside a code fence);
every refusal (outside via `..`, via a symlinked folder in the middle, via a symlink at the end, the state dir, `.git`, `.env`,
`*.pem`, a `credentials` folder, a text file holding a key, too big, a type the leading bytes contradict, a symlink swapped in
after the scan = TOCTOU); the page fields; `media_get` windows reassembling to the page's SHA-256; a changed file → `gone`;
a device that is not ready / revoked gets nothing. Chains: the three stand-in harnesses (fakeclaude / fakecodex /
fakeopencode) answering with `![](out/chart.png)` produce a page with `media`, and the bytes come back over `media_get`.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import hashlib
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import media, serve, wire  # noqa: E402

from test_l1 import Phone, _host, _state  # noqa: E402
from test_p33 import png  # noqa: E402
from test_p33_chain import _Chain, ready33  # noqa: E402

PDF = b"%PDF-1.4\n" + b"x" * 300
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + os.urandom(2000)
MP4 = b"\x00\x00\x00\x18ftypisom" + os.urandom(3000)
HTML = b"<!doctype html><title>t</title><h1>chart</h1><script>document.title='pwned'</script>"
KEY = "sk-ant-api03-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5oQ7sU9wY"


def _get(host, s, mid, timeout=10.0):
    """media_get windows until `last` → (bytes, errors)."""
    async def go():
        got, o = bytearray(), 0
        n0 = len(host._sent)
        while True:
            await host._app(s, {"t": "media_get", "mid": mid, "o": o})
            t0 = asyncio.get_running_loop().time()
            while True:
                new = [m for c, m in host._sent[n0:] if c == s.cid and m.get("mid") == mid]
                errs = [m for m in new if m["t"] == "media_err"]
                if errs:
                    return bytes(got), errs
                chunks = [m for m in new if m["t"] == "media_chunk"]
                if chunks and (chunks[-1]["last"] or len(chunks) == media.WINDOW):
                    break
                if asyncio.get_running_loop().time() - t0 > timeout:
                    return bytes(got), ["timeout"]
                await asyncio.sleep(0.01)
            for m in chunks:
                assert m["o"] == len(got), (m["o"], len(got))
                assert len(wire.pad_json(m, wire.MAX_JSON_P33)) + 17 <= 65536, "one chunk = one relay frame"
                got += wire.unb64u(m["d"])
            n0 = len(host._sent)
            if chunks[-1]["last"]:
                return bytes(got), []
            o = len(got)
    return go()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        (self.work / "out").mkdir(parents=True)
        self.outside = pathlib.Path(self.tmp.name) / "elsewhere"
        self.outside.mkdir()
        self.m = media.Media(self.st, str(self.work))

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, data, base=None):
        p = (base or self.work) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def scan(self, text):
        return self.m.scan(text, 7)


class Extraction(unittest.TestCase):
    def test_md_image_link_backtick_bare_file_url_and_never_remote(self):
        text = ("看图 ![图表](out/chart.png \"t\")，报告 [下载](<docs/my report.pdf>)，还有 `out/a.mp4` 和 ./b.wav。\n"
                "绝对路径 /srv/x.png、~/y.png；file:///tmp/z.png；网上的 ![x](https://example.com/a.png) "
                "<https://example.com/b.png> https://example.com/c.png `https://example.com/d.png` "
                "[m](mailto:a@example.com) ![d](data:image/png;base64,AAAA)\n"
                "```\nout/in_fence.png\n![](out/fenced.png)\n```\n转义 ![e](out/a\\_b.png) 和 [same](out/chart.png)")
        rs = media.refs(text)
        got = [(r.path, r.explicit) for r in rs]
        self.assertEqual(got, [("out/chart.png", True), ("docs/my report.pdf", True), ("out/a.mp4", False),
                               ("./b.wav", False), ("/srv/x.png", False), ("~/y.png", False), ("/tmp/z.png", False),
                               ("out/a_b.png", True)], "reading order, each path once")
        self.assertEqual(rs[0].text, "out/chart.png", "ref = the destination as written (the phone matches it)")
        self.assertFalse(any("example.com" in r.path for r in rs), "http(s) is never a local reference")
        self.assertFalse(any("fence" in r.path for r in rs), "nothing inside a fenced code block")
        self.assertEqual(media.refs(""), [])
        self.assertLessEqual(len(media.refs(" ".join(f"a/{i}.png" for i in range(200)))), media.MAX_REFS)


class Refusals(_Base):
    def test_offered_kinds_and_fields(self):
        self.put("out/chart.png", png(500))
        self.put("out/r.pdf", PDF)
        self.put("out/v.mp4", MP4)
        self.put("out/s.mp3", MP3)
        self.put("out/p.html", HTML)
        self.put("out/data.csv", b"a,b\n1,2\n")
        items, skips = self.scan("![](out/chart.png) `out/r.pdf` out/v.mp4 out/s.mp3 [页](out/p.html) `out/data.csv`")
        self.assertEqual(skips, [])
        self.assertEqual([(i["name"], i["kind"], i["mime"]) for i in items], [
            ("chart.png", "image", "image/png"), ("r.pdf", "pdf", "application/pdf"), ("v.mp4", "video", "video/mp4"),
            ("s.mp3", "audio", "audio/mpeg"), ("p.html", "html", "text/html"), ("data.csv", "file", "text/csv")])
        for i in items:
            self.assertTrue(wire.is_id22(i["mid"]))
            self.assertEqual(set(i), {"mid", "name", "mime", "kind", "bytes", "sha256", "ref"}, "no path on the wire")
            self.assertEqual(i["sha256"], hashlib.sha256((self.work / "out" / i["name"]).read_bytes()).hexdigest())
        self.assertTrue(self.m.known(items[0]["mid"]))
        self.assertEqual(os.stat(self.st.root / "media.json").st_mode & 0o777, 0o600)
        self.assertTrue(media.Media(self.st, str(self.work)).known(items[0]["mid"]), "the table survives a restart")

    def test_outside_via_dotdot_and_symlinks(self):
        self.put("secret.png", png(), self.outside)
        os.symlink(self.outside, self.work / "link")                      # a folder link in the middle
        os.symlink(self.outside / "secret.png", self.work / "out" / "end.png")  # a link at the end
        items, skips = self.scan("![](../elsewhere/secret.png) ![](link/secret.png) ![](out/end.png) "
                                 f"![]({self.outside / 'secret.png'})")
        self.assertEqual(items, [])
        self.assertEqual([s["why"] for s in skips], ["outside"] * 4)
        self.assertEqual(skips[0]["name"], "secret.png", "only the base name")
        # implicit references to files outside are silent (the Agent names system paths all the time)
        self.assertEqual(self.scan(f"`{self.outside / 'secret.png'}` /etc/hostname"), ([], []))

    def test_secret_places_names_and_content(self):
        self.put(".git/x.png", png())
        self.put(".env", b"A=1\n")
        self.put(".env.local", b"A=1\n")
        self.put("out/server.pem", b"x")
        self.put("credentials/a.png", png())
        self.put(".agentj/inbox/2026-10-05/a.png", png())
        self.put("out/notes.md", f"key: {KEY}\n".encode())
        self.put("out/page.html", b"<p>" + f"Authorization: Bearer {KEY}".encode() + b"</p>")
        self.put("out/ok.html", b"<img src=\"data:image/png;base64," + __import__("base64").b64encode(os.urandom(3000)) + b"\">")
        (self.st.root / "x.png").write_bytes(png())
        items, skips = self.scan("![](.git/x.png) [e](.env) [e](.env.local) [k](out/server.pem) ![](credentials/a.png) "
                                 "![](.agentj/inbox/2026-10-05/a.png) [n](out/notes.md) [p](out/page.html) [ok](out/ok.html) "
                                 f"![]({self.st.root / 'x.png'})")
        self.assertEqual([(s["name"], s["why"]) for s in skips], [
            ("x.png", "secret"), (".env", "secret"), (".env.local", "secret"), ("server.pem", "secret"), ("a.png", "secret"),
            ("a.png", "secret"), ("notes.md", "secret"), ("page.html", "secret"), ("x.png", "secret")])
        self.assertEqual([i["name"] for i in items], ["ok.html"], "a data: picture inside html is not a key")
        # a secret-named link pointing at a harmless file is still refused (the name as written counts too)
        self.put("out/fine.txt", b"hello")
        os.symlink(self.work / "out" / "fine.txt", self.work / "id_rsa")
        self.assertEqual(self.scan("[k](id_rsa)")[1], [{"name": "id_rsa", "why": "secret"}])

    def test_too_big_type_and_page_caps(self):
        big = self.work / "out" / "big.png"
        with open(big, "wb") as f:
            f.write(png(10))
            f.truncate(media.CAPS["image"] + 1)                    # sparse: no real 10 MiB written
        self.put("out/fake.png", b"this is text, not a PNG")
        self.put("out/run.py", b"print(1)\n")
        self.put("out/readme.md", b"# hi\n")
        items, skips = self.scan("![](out/big.png) ![](out/fake.png) [s](out/run.py) `out/run.py` `out/readme.md`")
        self.assertEqual(items, [])
        self.assertEqual(skips, [{"name": "big.png", "why": "too_big", "bytes": media.CAPS["image"] + 1},
                                 {"name": "fake.png", "why": "type"}, {"name": "run.py", "why": "type"}],
                         "implicit source / text files are silent; explicit ones say why")
        self.assertEqual(self.scan("[r](out/readme.md)")[0][0]["kind"], "file", "an explicit link offers a text file")
        for i in range(10):
            self.put(f"out/{i}.png", png(100 + i))
        items, _ = self.scan(" ".join(f"![](out/{i}.png)" for i in range(10)))
        self.assertEqual(len(items), media.MAX_ITEMS)
        self.assertEqual(self.scan("![](out/nothing.png)")[1], [{"name": "nothing.png", "why": "gone"}])
        self.assertEqual(self.scan("`out/nothing.png` out/ ./out"), ([], []), "missing / folders: silent")

    def test_toctou_swap_and_change_after_scan(self):
        p = self.put("out/chart.png", png(70_000))
        self.put("secret.png", png(70_000), self.outside)
        (items, _), = [self.scan("![](out/chart.png)")]
        mid = items[0]["mid"]
        self.assertEqual(b"".join(d for _, d, _ in self.m.window(mid, 0)), p.read_bytes())
        p.unlink()
        os.symlink(self.outside / "secret.png", p)                  # swapped for a link after the scan
        with self.assertRaises(media.Gone):
            self.m.window(mid, 0)
        p.unlink()
        os.symlink(self.outside, self.work / "out2")
        os.rename(self.work / "out", self.work / "out_old")
        os.symlink(self.outside, self.work / "out")                 # a folder in the middle swapped for a link
        with self.assertRaises(media.Gone):
            self.m.window(mid, 0)
        with self.assertRaises(media.Gone):
            self.m.window("A" * 22, 0)

    def test_changed_bytes_same_size_is_gone(self):
        p = self.put("out/a.png", png(1000))
        mid = self.scan("![](out/a.png)")[0][0]["mid"]
        st = p.stat()
        data = bytearray(p.read_bytes())
        data[-1] ^= 1
        p.write_bytes(bytes(data))
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))             # even with the old mtime: the hash at o = 0 differs
        with self.assertRaises(media.Gone):
            self.m.window(mid, 0)

    def test_expiry(self):
        self.put("out/a.png", png())
        now = [1000.0]
        m = media.Media(self.st, str(self.work), clock=lambda: now[0])
        mid = m.scan("![](out/a.png)", 1)[0][0]["mid"]
        now[0] += media.KEEP_SECS + 1
        with self.assertRaises(media.Gone):
            m.window(mid, 0)


class HostWire(_Base):
    def host(self):
        self.st.set_agent_config("claude", str(self.work), fence=False)
        sent = []
        h = _host(self.st, sent)
        h._sent = sent
        return h

    def test_page_fields_media_get_reassembles_and_changed_is_gone(self):
        data = os.urandom(media.CHUNK * media.WINDOW + 12345)            # two windows
        self.put("out/clip.mp4", b"\x00\x00\x00\x18ftypisom" + data)
        self.put("out/.env", b"A=1")
        h = self.host()
        ph = Phone(self.st)

        async def go():
            s = ready33(h, ph)
            t = h.hist_add({"k": "phone", "dev": ph.did, "text": "做个视频"}, "", "open")
            h.hist_update(t["id"], append="好了：`out/clip.mp4`，配置在 [这里](out/.env)", end="done")
            await h._attach_media(t["id"])
            page = h.hist.get(t["id"])
            self.assertEqual([i["name"] for i in page["media"]], ["clip.mp4"])
            self.assertEqual(page["media_skip"], [{"name": ".env", "why": "secret"}])
            self.assertTrue(any(m["t"] == "hist_turn" and m["turn"].get("media") for _, m in h._sent), "pushed to the phone")
            got, errs = await _get(h, s, page["media"][0]["mid"])
            self.assertEqual(errs, [])
            self.assertEqual(hashlib.sha256(got).hexdigest(), page["media"][0]["sha256"])
            # history pages loaded later carry the same list
            turns, _ = h.hist.page()
            self.assertEqual(turns[-1]["media"], page["media"])
            # the file changes → gone
            (self.work / "out" / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + data[:-1] + b"!")
            _, errs = await _get(h, s, page["media"][0]["mid"])
            self.assertEqual([e["why"] for e in errs], ["gone"])
            # bad shapes
            n = len(h._sent)
            await h._app(s, {"t": "media_get", "mid": "x", "o": 0})
            await h._app(s, {"t": "media_get", "mid": page["media"][0]["mid"], "o": -1})
            await asyncio.sleep(0.05)
            self.assertEqual([m.get("why") for _, m in h._sent[n:]], ["shape"])
        asyncio.run(go())

    def test_not_ready_not_p33_and_revoked_get_nothing(self):
        self.put("out/a.png", png(200_000))
        h = self.host()
        ph = Phone(self.st)

        async def go():
            items, _ = await asyncio.to_thread(h.media.scan, "![](out/a.png)", 1)
            mid = items[0]["mid"]
            pend = serve.Session(cid=31, state="pending", device=ph.did, pub=ph.pub, p33=True)
            old = serve.Session(cid=32, state="ready", device=ph.did, pub=ph.pub, p33=False)
            h.sessions.update({31: pend, 32: old})
            for s in (pend, old):
                await h._app(s, {"t": "media_get", "mid": mid, "o": 0})
            s = ready33(h, ph, 33)
            self.st.remove_device(ph.did)
            await h._app(s, {"t": "media_get", "mid": mid, "o": 0})
            await asyncio.sleep(0.2)
            self.assertFalse([m for _, m in h._sent if m["t"] in ("media_chunk", "media_err")])
            # a revoke between two chunks stops the window there
            ph2 = Phone(self.st, "二号")
            s2 = ready33(h, ph2, 34)
            await h._app(s2, {"t": "media_get", "mid": mid, "o": 0})
            for _ in range(200):
                if any(m["t"] == "media_chunk" for c, m in h._sent if c == 34):
                    break
                await asyncio.sleep(0.005)
            self.st.remove_device(ph2.did)
            await asyncio.sleep(0.6)
            n = len([m for c, m in h._sent if c == 34 and m["t"] == "media_chunk"])
            self.assertLess(n, 5, "200 000 bytes = 5 chunks; the revoke stopped the rest")
        asyncio.run(go())


# ------------------------------------------------------------------ the three harnesses
class _MediaChain(_Chain):
    def test_reply_with_a_local_image_gets_media(self):
        data = png(60_000)
        (self.work / "out").mkdir()
        (self.work / "out" / "chart.png").write_bytes(data)

        async def script(c):
            r, t = await c["said"]("图在这里 ![](out/chart.png)")
            self.assertTrue(r["ok"], r)
            await c["wait"](lambda: c["turns"]().get(t["id"], {}).get("media"))
            page = c["turns"]()[t["id"]]
            self.assertEqual([(m["name"], m["kind"], m["ref"]) for m in page["media"]], [("chart.png", "image", "out/chart.png")])
            host, s = c["host"], c["s"]
            host._sent = c["sent"]
            got, errs = await _get(host, s, page["media"][0]["mid"])
            self.assertEqual((errs, hashlib.sha256(got).hexdigest()), ([], hashlib.sha256(data).hexdigest()))
        self.run_chain(script)


class ClaudeMedia(_MediaChain):
    KIND = "claude"


class CodexMedia(_MediaChain):
    KIND = "codex"


class OpenCodeMedia(_MediaChain):
    KIND = "opencode"


del _MediaChain, _Chain

if __name__ == "__main__":
    unittest.main()
