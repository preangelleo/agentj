"""P475: factory logo inheritance survives theme export and old theme folders."""
import _hermetic  # noqa: F401,I001
import base64
import json
import pathlib
import tempfile
import unittest

from agentj import theme
import theme_fixtures as fx


class FactoryLogo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.src = self.root / "source"

    def snapshot(self, folder):
        return {p.name: p.read_bytes() for p in folder.iterdir()}

    def test_export_robots_dots_and_active_omit_official_logo(self):
        for which in ("robots", "dots", "active"):
            with self.subTest(which=which):
                dest = self.root / which
                r = theme.export(self.state, which, dest)
                self.assertTrue(r["ok"], r)
                self.assertNotIn("logo", json.loads((dest / "theme.json").read_text()))
                self.assertFalse(list(dest.glob("logo*.png")))
                self.assertTrue(theme.validate(dest)["ok"])
        theme.apply_preset(self.state, "dots")
        r = theme.export(self.state, "active", self.root / "active-dots")
        self.assertTrue(r["ok"])
        self.assertNotIn("logo", json.loads((self.root / "active-dots/theme.json").read_text()))

    def test_custom_logo_is_preserved_through_apply_export_and_retention(self):
        fx.make_theme(self.src)
        before = self.snapshot(self.src)
        self.assertTrue(theme.apply_dir(self.state, self.src)["ok"])
        dest = self.root / "custom"
        self.assertTrue(theme.export(self.state, "active", dest)["ok"])
        self.assertEqual((dest / "logo.png").read_bytes(), before["logo.png"])
        self.assertIn("logo", json.loads((dest / "theme.json").read_text()))
        self.assertEqual(base64.b64decode(theme.phone_message(self.state)["logo"]["png"]), before["logo.png"])
        plain = self.root / "plain"
        theme.export(self.state, "robots", plain)
        self.assertTrue(theme.apply_dir(self.state, plain)["ok"])
        self.assertEqual(base64.b64decode(theme.phone_message(self.state)["logo"]["png"]), before["logo.png"])
        self.assertEqual(self.snapshot(self.src), before)

    def test_old_single_and_state_logos_are_official_without_source_edits(self):
        for states in (False, True):
            with self.subTest(states=states):
                src = self.root / ("old-set" if states else "old-single")
                doc = fx.make_theme(src)
                doc["schema"] = 2 if states else 1
                refs = {s: "renamed-" + s + ".png" for s in ("idle", "working", "waiting", "question", "offline")} if states else {"idle": "logo.png"}
                for s, name in refs.items():
                    (src / name).write_bytes((theme.preset("robots") / ("logo-" + s + ".png")).read_bytes())
                doc["logo"] = {"states": refs, "disc": False} if states else {"image": "logo.png", "disc": False}
                (src / "theme.json").write_text(json.dumps(doc))
                before = self.snapshot(src)
                r = theme.validate(src)
                self.assertTrue(r["ok"], r["errors"])
                self.assertNotIn("logo", r["theme"])
                self.assertTrue(theme.apply_dir(self.state, src)["ok"])
                self.assertNotIn("logo", theme.phone_message(self.state))
                dest = self.root / ("export-set" if states else "export-single")
                self.assertTrue(theme.export(self.state, "active", dest)["ok"])
                self.assertNotIn("logo", json.loads((dest / "theme.json").read_text()))
                self.assertEqual(self.snapshot(src), before)

    def test_partly_custom_state_set_keeps_all_images(self):
        src = theme.preset("robots")
        self.src.mkdir()
        doc = json.loads((src / "theme.json").read_text())
        doc["id"] = "owner-set"
        for p in src.glob("*.png"):
            (self.src / p.name).write_bytes(p.read_bytes())
        (self.src / "logo-idle.png").write_bytes(fx.disc(128))
        (self.src / "theme.json").write_text(json.dumps(doc))
        before = self.snapshot(self.src)
        self.assertTrue(theme.apply_dir(self.state, self.src)["ok"])
        logos = theme.phone_message(self.state)["logo"]["states"]
        for state, name in doc["logo"]["states"].items():
            self.assertEqual(base64.b64decode(logos[state]), before[name])
        dest = self.root / "mixed"
        self.assertTrue(theme.export(self.state, "active", dest)["ok"])
        self.assertEqual(json.loads((dest / "theme.json").read_text())["logo"], doc["logo"])
        for name in doc["logo"]["states"].values():
            self.assertEqual((dest / name).read_bytes(), before[name])

    def test_existing_active_official_logo_is_not_retained(self):
        doc = fx.make_theme(self.src)
        (self.src / "logo.png").write_bytes((theme.preset("robots") / "logo-idle.png").read_bytes())
        active = theme.home(self.state) / "active"
        active.mkdir(parents=True)
        for name, data in self.snapshot(self.src).items():
            (active / name).write_bytes(data)
        (theme.home(self.state) / "current.json").write_text(json.dumps({"kind": "custom", "id": doc["id"], "name": doc["name"]}))
        before = self.snapshot(active)
        self.assertNotIn("logo", theme.phone_message(self.state))
        self.assertTrue(theme.export(self.state, "active", self.root / "old-active")["ok"])
        self.assertNotIn("logo", json.loads((self.root / "old-active/theme.json").read_text()))
        self.assertEqual(self.snapshot(active), before)
        plain = self.root / "plain"
        theme.export(self.state, "dots", plain)
        self.assertTrue(theme.apply_dir(self.state, plain)["ok"])
        self.assertNotIn("logo", theme.phone_message(self.state))


if __name__ == "__main__":
    unittest.main()
