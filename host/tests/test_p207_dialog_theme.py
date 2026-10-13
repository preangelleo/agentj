"""P207 追加 1: every centred dialog shares ONE background token (web app.css --dlg-bg = Noir #2c2b26, light and dark); a
theme may override exactly that token (colors.dialog.background) — readable (Lotion text ≥ 4.5:1) or refused; the phone
message carries it and the web theme.js maps it to --dlg-bg."""
import _hermetic  # noqa: F401,I001
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import theme  # noqa: E402
from agentj.state import State  # noqa: E402
import theme_fixtures as fx  # noqa: E402

WEB = HERE.parents[1] / "web" / "public"


def codes(r):
    return {(e["code"], e["path"]) for e in r["errors"]}


class DialogColour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name) / "t"
        fx.make_theme(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def dialog(self, **m):
        doc = json.loads((self.d / "theme.json").read_text())
        doc["colors"]["dialog"] = m
        (self.d / "theme.json").write_text(json.dumps(doc))
        return theme.validate(self.d)

    def test_factory_is_noir_everywhere(self):
        props = theme.schema()["properties"]["colors"]["properties"]["dialog"]["properties"]
        self.assertEqual(list(props), ["background"])
        self.assertEqual(props["background"]["default"], "#2c2b26")
        css = (WEB / "app.css").read_text()
        self.assertIn("--dlg-bg:var(--c-noir);", css)
        self.assertIn("--dbg:var(--dlg-bg)", css)
        self.assertNotIn("--dbg:var(--c-lotion", css, "no light dialog variant left")
        self.assertIn("--dbg:var(--dlg-bg)", (WEB / "menu.css").read_text())
        self.assertIn("['dialog', DIALOG_VARS]", (WEB / "js" / "theme.js").read_text())

    def test_readable_or_refused(self):
        self.assertTrue(self.dialog(background="#141414")["ok"])
        self.assertIn(("color.contrast", "colors.dialog.background"), codes(self.dialog(background="#faf9f5")))
        self.assertIn(("schema.pattern", "colors.dialog.background"), codes(self.dialog(background="black")))
        r = self.dialog(border="#000000")
        self.assertTrue(any(c.startswith("schema.") for c, _p in codes(r)))

    def test_phone_message_carries_it(self):
        st = State(pathlib.Path(self.tmp.name) / "state")
        st.init(relay="ws://127.0.0.1:1")
        self.dialog(background="#141414")
        self.assertTrue(theme.apply_dir(st.root, self.d)["ok"])
        self.assertEqual(theme.phone_message(st.root)["colors"]["dialog"], {"background": "#141414"})


if __name__ == "__main__":
    unittest.main()
