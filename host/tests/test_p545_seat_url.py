"""P545 (FEEDBACK #309, PROTOCOL §40): the seat's own phone address m.agentj.app/<seat id> — the shared vectors, and the
two places the host says it (pairing approved, `agentj` with everything set up)."""
import _hermetic
import json
import pathlib
import unittest
from types import SimpleNamespace as NS

from agentj import cli, seaturl

AJ = pathlib.Path(__file__).resolve().parents[2]
VECTORS = json.loads((AJ / "protocol" / "vectors" / "p545-seat-url.json").read_text(encoding="utf-8"))


class SeatUrl(unittest.TestCase):
    def test_vectors(self):
        self.assertEqual(VECTORS["label"].encode(), seaturl.LABEL)
        for c in VECTORS["cases"]:
            self.assertEqual(seaturl.seat_id(c["channel"]), c["seat"])
            self.assertEqual(seaturl.seat_url(c["channel"]), c["url"])
            self.assertRegex(c["seat"], seaturl.SEAT_RE)
            self.assertNotIn(c["channel"], c["url"])

    def test_bad_channel_and_other_web(self):
        for bad in ("", "short", "x" * 23, None, "a" * 21 + "!"):
            with self.assertRaises(ValueError):
                seaturl.seat_id(bad)
        ch = VECTORS["cases"][0]["channel"]
        self.assertEqual(seaturl.seat_url(ch, "http://127.0.0.1:8080/"), "http://127.0.0.1:8080")
        self.assertEqual(seaturl.seat_url(ch, "https://m.agentj.app/"), VECTORS["cases"][0]["url"])

    def test_pairing_links_open_the_seat_page(self):
        import os
        from agentj import wire
        c = VECTORS["cases"][2]
        k, i, p = os.urandom(32), os.urandom(16), os.urandom(32)
        for make in (wire.pairing_link, wire.pairing_link_v1):
            link = make("https://m.agentj.app", wire.PAIR_DEFAULT_RELAY, c["channel"], k, i, p, 2 ** 31)
            self.assertTrue(link.startswith(c["url"] + "/#p="), link[:40])
            self.assertEqual(wire.parse_pairing_link(link)["channel"], c["channel"]) if make is wire.pairing_link else None
            test = make("http://127.0.0.1:8080/", "ws://127.0.0.1:9", c["channel"], k, i, p, 2 ** 31)
            self.assertTrue(test.startswith("http://127.0.0.1:8080/#p="), "a test / self-hosted web root keeps its root")
        qr = wire.pairing_qr(wire.pairing_link("https://m.agentj.app", wire.PAIR_DEFAULT_RELAY, c["channel"], k, i, p, 2 ** 31))
        self.assertEqual((qr.version, qr.symbol_size(border=0)[0]), (8, 49), "the seat path keeps the QR at version 8 / 49 modules")

    def test_cli_lines(self):
        ch = VECTORS["cases"][1]["channel"]
        st = NS(config=lambda: {"channel": ch, "web": "https://m.agentj.app"})
        self.assertEqual(cli._seat_url(st), VECTORS["cases"][1]["url"])
        line = cli._seat_line(st)
        self.assertIn(VECTORS["cases"][1]["url"], line)
        self.assertIn("本席位专属网址", line)
        self.assertIn("This seat's own address", line)
        broken = NS(config=lambda: {})
        self.assertEqual(cli._seat_url(broken), cli.DEFAULT_WEB)
        src = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
        self.assertIn("print(_seat_line(st), flush=True)", src)
        self.assertIn('home = _seat_url(st).removeprefix("https://")', src)


if __name__ == "__main__":
    unittest.main()
