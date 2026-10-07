"""F29 / ADR-A177: the compact pairing link and its QR (version 8-M, 49 modules, instead of 13-M / 69).

The phone side (both formats parse, scanning simulated screen photos with jsQR) is web/test/p73_scan.test.mjs,
which reads web/test/fixtures/p73/pair_qr.json — made by the host code here, checked for drift below."""
import _hermetic  # noqa: F401
import contextlib
import importlib.util
import io
import pathlib
import secrets
import unittest
from unittest import mock

from agentj import cli, wire
from agentj.state import DEFAULT_RELAY, DEFAULT_WEB

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "web" / "test" / "fixtures" / "p73"


def fields():
    return (wire.channel_id(secrets.token_bytes(32)), secrets.token_bytes(32), secrets.token_bytes(16),
            secrets.token_bytes(32), 1_790_000_000 + secrets.randbelow(10**8))


class CompactLink(unittest.TestCase):
    def test_round_trip_and_both_formats_parse_alike(self):
        for _ in range(20):
            ch, k, i, p, x = fields()
            new = wire.pairing_link(DEFAULT_WEB, DEFAULT_RELAY, ch, k, i, p, x)
            old = wire.pairing_link_v1(DEFAULT_WEB, DEFAULT_RELAY, ch, k, i, p, x)
            self.assertRegex(new, r"^https://m\.agentj\.app/#p=[0-9]+$")
            want = {"relay": DEFAULT_RELAY, "channel": ch, "host_pub": k, "pid": i, "psk": p, "expires": x}
            self.assertEqual(wire.parse_pairing_link(new), want)
            self.assertEqual(wire.parse_pairing_link(old), want)
            self.assertLess(len(new), len(old))

    def test_relay_field(self):
        ch, k, i, p, x = fields()
        for relay in ("wss://alpha-relay.agentjarvis.net", "ws://127.0.0.1:8899", DEFAULT_RELAY):
            self.assertEqual(wire.parse_pairing_link(wire.pairing_link(DEFAULT_WEB, relay, ch, k, i, p, x))["relay"], relay)
        default = wire.pairing_link(DEFAULT_WEB, DEFAULT_RELAY, ch, k, i, p, x)
        other = wire.pairing_link(DEFAULT_WEB, "wss://relay.agentj.app.example", ch, k, i, p, x)
        self.assertLess(len(default), len(other), "the default relay costs nothing")
        with self.assertRaises(ValueError):
            wire.pairing_link(DEFAULT_WEB, "https://relay.agentj.app", ch, k, i, p, x)
        with self.assertRaises(ValueError):
            wire.pairing_link(DEFAULT_WEB, DEFAULT_RELAY, ch, k[:31], i, p, x)
        with self.assertRaises(ValueError):
            wire.parse_pairing_link("https://m.agentj.app/#p=12345")

    def test_qr_version_8_m_numeric_segment(self):
        seen = set()
        for _ in range(30):
            link = wire.pairing_link(DEFAULT_WEB, DEFAULT_RELAY, *fields())
            qr = wire.pairing_qr(link)
            seen.add((qr.version, qr.symbol_size(border=0)[0]))
            self.assertIn(qr.error, ("M", "Q"))   # segno may boost the level when it fits the same version
        self.assertEqual(seen, {(8, 49)})
        old = wire.pairing_qr(wire.pairing_link_v1(DEFAULT_WEB, DEFAULT_RELAY, *fields()))
        self.assertEqual((old.version, old.error), (13, "M"), "an old link still renders as before")

    def test_terminal_qr_is_narrower(self):
        link = wire.pairing_link(DEFAULT_WEB, DEFAULT_RELAY, *fields())
        with mock.patch.object(cli, "_qr_mode", return_value="compact"), contextlib.redirect_stdout(io.StringIO()) as out:
            cli._print_qr(link)
        lines = out.getvalue().splitlines()
        self.assertEqual(max(len(x) for x in lines), 49 + 8, "49 modules + 4-module quiet zone each side")
        got = cli.qr_from_half_blocks(lines)
        want = [[bool(m) for m in row] for row in wire.pairing_qr(link).matrix_iter(scale=1, border=4)]
        self.assertEqual(got[:len(want)], want)

    def test_admin_page_qr_uses_the_segments(self):
        src = (pathlib.Path(wire.__file__).parent / "admin.py").read_text()
        self.assertIn("wire.pairing_qr(self.link).svg_data_uri(", src)

    def test_web_fixture_matches_the_host(self):
        spec = importlib.util.spec_from_file_location("gen_pair_qr", FIXTURES / "gen_pair_qr.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        self.assertEqual((FIXTURES / "pair_qr.json").read_text(), gen.render(),
                         "rerun web/test/fixtures/p73/gen_pair_qr.py")


if __name__ == "__main__":
    unittest.main()
