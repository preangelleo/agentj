"""#459 (chairman 2026-11-04): whenever Agent J helps install OpenCode it is the newest 2.x — one constant, every hint."""
import _hermetic  # noqa: F401
import pathlib
import re
import unittest

from agentj import opencode_install as oi

AJ = pathlib.Path(__file__).resolve().parents[2]          # export-neutral: agentjarvis/ (or the exported root)
PKG = AJ / "host" / "agentj"


class OneConstant(unittest.TestCase):
    def test_constants_name_the_newest_2x(self):
        self.assertEqual(oi.MIN_MAJOR, 2)
        self.assertIn("opencode.ai/v2/install", oi.SCRIPT)
        self.assertTrue(oi.NPM.endswith("@opencode/cli@2"))
        for bad in ("releases/latest", "opencode-ai", "mise"):
            self.assertNotIn(bad, oi.SCRIPT + oi.NPM)

    def test_version_gate(self):
        for v, ok in (("2.2.5", True), ("opencode v2.0.1", True), ("3.0.0", True), ("1.19.5", False),
                      ("1.18.34", False), ("", False), (None, False), ("garbage", False)):
            with self.subTest(v=v):
                self.assertEqual(oi.current(v), ok)

    def test_upgrade_hint_both_languages(self):
        for lang in ("zh", "en"):
            t = oi.upgrade_text("1.19.5", lang)
            # security A-N3: what the Agent runs is the major-pinned npm package, never an unpinned curl | bash
            self.assertIn(oi.NPM, t)
            self.assertNotIn("curl", t)
            self.assertNotIn("curl", oi.install_text(lang))
            self.assertIn("agentj agent detect --json", t)
            self.assertIn("agentj agent restart", t)
            self.assertIn(oi.NPM, oi.install_text(lang))

    def test_no_other_install_command_in_the_package(self):
        """Every install / switch / rescue / doctor / skill hint reads the constant: no second spelling anywhere."""
        pat = re.compile(r"@opencode/cli|opencode\.ai/(?:v2/)?install|npm i(?:nstall)? -g opencode")
        hits = []
        for f in PKG.rglob("*"):
            if f.suffix in (".py", ".md", ".json") and f.name != "opencode_install.py" and f.is_file():
                if pat.search(f.read_text(encoding="utf-8", errors="replace")):
                    hits.append(f.relative_to(PKG).as_posix())
        self.assertEqual(hits, [])

    def test_one_line_installer_pins_a_2x_archive(self):
        sh = AJ / "setup" / "setup-agents.sh"
        if not sh.is_file():
            self.skipTest("setup-agents.sh is not in this export")
        m = re.search(r"OPENCODE_VERSION='(\d+)\.(\d+)\.(\d+)'", sh.read_text())
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), oi.MIN_MAJOR)

    def test_install_md_has_no_unpinned_npm_or_1x_source(self):
        md = AJ.parent / "documentation" / "product" / "install.md"
        if not md.is_file():
            self.skipTest("install.md source is not in this export")
        text = md.read_text(encoding="utf-8")
        self.assertIn(oi.SCRIPT, text)
        self.assertIn(oi.NPM, text)
        self.assertNotRegex(text, r"npm i -g @opencode/cli(?!@2)")


if __name__ == "__main__":
    unittest.main()
