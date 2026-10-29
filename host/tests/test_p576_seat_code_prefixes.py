"""P576: a seat transfer code (`ajh_`, minted by this host) was refused by the Olares 「绑定你的席位」 box — the Market image
still ran a setup page from before P547 that knew only `ajt_` / `AJI-`. Issuer → every receiver, end to end on the source:
the whole prompt and the bare code through `agentj login --seat-file` / `--seat -` / the interactive paste, the Olares
setup page (whole prompt and bare code), the local shape check; and static: every seat-code prefix a issuer mints is
accepted by every receiver (host, Olares page, Dashboard seat-bind, phone mask) and covered by every credential scanner."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import argparse
import importlib.util
import io
import os
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
AJ = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from agentj import cli, cloud, seat_transfer as xfer  # noqa: E402

PAGE = AJ / "olares/agentj/files/setup_page.py"
SEATS_TS = AJ / "dashboard/src/seats.ts"
B64 = r"[A-Za-z0-9_-]{43}"


def _page():
    spec = importlib.util.spec_from_file_location("p576_setup_page", PAGE)
    page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(page)
    return page


def _minted() -> set[str]:
    """Every seat-code prefix an issuer mints: the Dashboard account page (seats.ts newSetupToken) and this host (P547)."""
    out = {xfer.PREFIX}
    if SEATS_TS.exists():
        out |= set(re.findall(r"newSetupToken = \(\) => `(aj[a-z]_)\$\{", SEATS_TS.read_text()))
        assert len(out) == 2, out
    return out


class IssuerToReceivers(unittest.TestCase):
    def setUp(self):
        self.token = xfer.new_token()
        self.zh, self.en = xfer.prompt("bkmj-test", self.token)

    def test_issued_code_is_ajh_and_passes_the_shape_check(self):
        self.assertTrue(self.token.startswith("ajh_"))
        self.assertTrue(cloud.seat_code_ok(self.token))

    def test_seat_file_bare_code(self):   # the file holds only the code (≤ SEAT_FILE_MAX bytes; install.md Step 7)
        for text in (self.token, self.token + "\r\n", "  " + self.token + "\n", "设置码：" + self.token + "\n"):
            with self.subTest(text=text[:12]), tempfile.TemporaryDirectory() as d:
                p = pathlib.Path(d) / "seat"
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                a = argparse.Namespace(seat_file=str(p), install_code_file=None, seat=None)
                self.assertEqual(cli._read_seat_code(a), self.token)

    def test_seat_arg_stdin_and_interactive_paste(self):
        a = argparse.Namespace(seat_file=None, install_code_file=None, seat="-")
        with mock.patch.object(sys, "stdin", io.StringIO(self.token + "\n")):
            self.assertEqual(cli._read_seat_code(a), self.token)
        for text in (self.zh, self.en, "  " + self.zh + "\n\n" + self.en + "\n"):   # `--seat "<whole prompt>"`
            self.assertEqual(cli._read_seat_code(argparse.Namespace(seat_file=None, install_code_file=None, seat=text)), self.token)
        for text in (self.zh, self.en, self.token):
            code = cli._pasted_code(text)
            self.assertEqual(code, self.token)
            self.assertRegex(code, r"\A(?:AJI-[a-f0-9]{32}|aj[th]_" + B64 + r")\Z")   # what _paste_login accepts

    @unittest.skipUnless(PAGE.exists(), "the Olares app source is not in this tree")
    def test_olares_bind_box_whole_prompt_and_bare_code(self):
        page = _page()
        for text in (self.zh, self.en, self.token):
            with self.subTest(text=text[:12]), tempfile.TemporaryDirectory() as root, \
                    mock.patch.object(page, "OLA", root), mock.patch.object(page, "onboarding", return_value={"seat": False}), \
                    mock.patch.object(page, "_run", return_value=(0, "bound")) as run:
                res = page.login({"code": text})
                self.assertTrue(res is None or res.get("ok") is not False, res)
                self.assertEqual(run.call_args.args[0][:4], ["agentj", "login", "--seat", "-"])
                self.assertEqual(run.call_args.kwargs["stdin"], self.token + "\n")


class PrefixSets(unittest.TestCase):
    def test_every_minted_prefix_is_accepted_by_every_receiver(self):
        page = _page() if PAGE.exists() else None   # the public export carries no Olares app source; the private tree checks it all
        cli_src = (HERE.parent / "agentj/cli.py").read_text()
        for pre in _minted():
            code = pre + "A" * 43
            with self.subTest(prefix=pre):
                self.assertTrue(cloud.seat_code_ok(code))
                self.assertEqual(cli._pasted_code("x " + code + " y"), code)
                self.assertIn(r're.fullmatch(r"AJI-[a-f0-9]{32}|aj[th]_[A-Za-z0-9_-]{43}", code)', cli_src)
                if page is None:
                    continue
                self.assertEqual(page.CODE.search("x " + code + " y").group(0), code)
                self.assertIn(f'"{pre}"', re.search(r"code\.startswith\(\(([^)]*)\)\)", PAGE.read_text()).group(1))   # → --seat -
                self.assertIn(pre, re.search(r"no_code:'([^']*)'", PAGE.read_text()).group(1))   # the zh error names it

    @unittest.skipUnless(SEATS_TS.exists(), "the Dashboard source is not in this tree")
    def test_dashboard_seat_bind_accepts_both(self):
        ts = SEATS_TS.read_text()
        accepted = set(re.findall(r"TOKEN_RE = /\^(aj[a-z]_)\[A-Za-z0-9_-\]\{43\}\$/", ts))
        self.assertEqual(accepted, _minted())

    @unittest.skipUnless((AJ / "web/public/js/seatxfer.js").exists(), "the phone source is not in this tree")
    def test_phone_mask_knows_the_host_prefix(self):
        js = (AJ / "web/public/js/seatxfer.js").read_text()
        rx = re.search(r"const TOKEN = /(.+?)/g;", js).group(1).replace(r"\b", "")
        for pre in _minted():
            self.assertRegex(pre + "A" * 43, rx)
        self.assertNotIn("'ajt_••••…'", js, "the mask keeps the code's own prefix")

    def test_scanners_cover_every_minted_prefix(self):
        files = ["host/agentj/privacy.py", "support/snapshot.py", "support/support_desk.py", "worker/src/scan.ts",
                 "dashboard/public/plazaredact.js", "site/static/help/redact.js", "site/public/help/redact.js",
                 "host/agentj/rescue_kit.py"]
        seen = 0
        for rel in files:
            p = AJ / rel
            if not p.exists():
                continue
            seen += 1
            classes = re.findall(r"aj\[([a-z-]+)\]_", p.read_text())
            self.assertTrue(classes, rel)
            for pre in _minted():
                with self.subTest(file=rel, prefix=pre):
                    self.assertTrue(all(re.fullmatch(f"[{c}]", pre[2]) for c in classes), (rel, classes))
        self.assertGreater(seen, 0)


if __name__ == "__main__":
    unittest.main()
