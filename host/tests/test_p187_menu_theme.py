"""P187: a theme can recolour the menus (colors.menu — background, panel, text, accent, line); validate keeps them
readable (text and the 72 % notes ≥ 4.5:1 on the background and the panel, the accent ≥ 3:1), the factory values are
Leo's palette and match web menu.css, the published schema is the shipped one, and the phone message carries them."""
import _hermetic  # noqa: F401,I001
import json
import pathlib
import re
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import theme  # noqa: E402
from agentj.state import State  # noqa: E402
import theme_fixtures as fx  # noqa: E402

REPO = HERE.parents[2]
MENU_CSS = (HERE.parents[1] / "web" / "public" / "menu.css").read_text()   # private and public tree layouts alike


def codes(r):
    return {(e["code"], e["path"]) for e in r["errors"]}


class MenuColours(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name) / "t"
        fx.make_theme(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def menu(self, **m):
        doc = json.loads((self.d / "theme.json").read_text())
        doc["colors"]["menu"] = m
        (self.d / "theme.json").write_text(json.dumps(doc))
        return theme.validate(self.d)

    def test_defaults_are_the_factory_menu_and_leos_palette(self):
        props = theme.schema()["properties"]["colors"]["properties"]["menu"]["properties"]
        self.assertEqual({k: v.get("default") for k, v in props.items()},
                         {"background": "#2c2b26", "panel": "#141414", "text": "#faf9f5", "accent": "#71bbde", "line": None})
        # menu.css: the same factory values (P299: Noir page / Sooty panels / Lotion / Milky Waves)
        for token, hexv in (("--menu-bg", "var(--c-noir)"), ("--menu-panel", "var(--c-sooty)"), ("--menu-text", "var(--c-lotion)"),
                            ("--menu-accent", "var(--milky-waves)")):
            self.assertIn(f"{token}:{hexv};", MENU_CSS)
        self.assertIn("--milky-waves:#71bbde;", MENU_CSS)

    def test_a_readable_menu_passes(self):
        self.assertTrue(self.menu(background="#2c2b26", panel="#141414", text="#faf9f5", accent="#d97757", line="#858585")["ok"])
        self.assertTrue(self.menu(accent="#f7827e")["ok"], "one key alone: the others stay factory")

    def test_contrast_rules(self):
        r = self.menu(text="#858585")                    # Black Mana on Noir: below 4.5
        self.assertIn(("color.contrast", "colors.menu.text"), codes(r))
        r = self.menu(background="#faf9f5")              # a light page with the factory Lotion text
        self.assertIn(("color.contrast", "colors.menu.background"), codes(r))
        r = self.menu(panel="#e3dacc")                   # a light panel under Lotion text
        self.assertIn(("color.contrast", "colors.menu.panel"), codes(r))
        r = self.menu(accent="#2c2b26")                  # Noir accent on the Noir page: switches would vanish
        self.assertEqual(codes(r), {("color.contrast", "colors.menu.accent")})
        r = self.menu(text="red")
        self.assertIn(("schema.pattern", "colors.menu.text"), codes(r))
        r = self.menu(shadow="#000000")
        self.assertTrue(any(c == "schema.additional" or c.startswith("schema.") for c, _p in codes(r)))
        msg = [e for e in self.menu(background="#faf9f5")["errors"] if e["path"] == "colors.menu.background"][0]
        self.assertTrue(re.search(r"[一-鿿]", msg["zh"]) and "needs 4.5:1" in msg["en"])

    def test_phone_message_and_published_schema(self):
        st = State(pathlib.Path(self.tmp.name) / "state")
        st.init(relay="ws://127.0.0.1:1")
        self.menu(background="#2c2b26", panel="#141414", accent="#d97757")
        self.assertTrue(theme.apply_dir(st.root, self.d)["ok"])
        m = theme.phone_message(st.root)
        self.assertEqual(m["colors"]["menu"], {"background": "#2c2b26", "panel": "#141414", "accent": "#d97757"})
        site = HERE.parents[1] / "site" / "public" / "schema" / "agentj-theme-1.json"   # the site is not in the public tree
        if site.exists():
            self.assertEqual(site.read_bytes(), theme.SCHEMA_PATH.read_bytes())


if __name__ == "__main__":
    unittest.main()
