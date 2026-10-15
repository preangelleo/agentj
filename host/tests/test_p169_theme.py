"""P169 (PROTOCOL §23): the phone's theme — schema, the file rules (validate), apply / dormant / restore / reset round
trips, the two factory presets, the CLI and serve's push to the phone."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import serve, theme, wire  # noqa: E402
from agentj.state import State  # noqa: E402
import theme_fixtures as fx  # noqa: E402

REPO = HERE.parents[2]
WEB = HERE.parents[1] / "web" / "public"   # private and public tree layouts alike
# Leo's palette (leo-design-language references/leo-palette.json, 37 colours, 2026-10-12): factory defaults come only from here.
PALETTE = set("""#faf9f5 #f0eee6 #e3dacc #858585 #2c2b26 #141414 #d97757 #6a9bcc #827dbd #629887 #ebc9b7 #c7dedc #cacadb #ffe292
#f7827e #fcbe7c #f7e17c #b7e396 #78dedc #71bbde #d1a7e8 #f0aad4 #dbb293 #fbc8c6 #fee2c5 #fbf2c5 #dff3d1 #c4f0f0 #c1e1f0 #ebd8f5
#f8daec #efddcf #f2f2f2 #ededed #e5e5e5 #acacac #888888""".split())


def codes(r):
    return {e["code"] for e in r["errors"]}


def hexes(obj):
    return re.findall(r"#[0-9a-fA-F]{6}", json.dumps(obj))


class Schema(unittest.TestCase):
    def test_is_draft_2020_12_and_uses_only_supported_keywords(self):
        s = theme.schema()
        self.assertEqual(s["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(s["$id"], "https://agentj.app/schema/agentj-theme-1.json")
        def walk(n, where):
            if isinstance(n, dict):
                for k, v in n.items():
                    if where.endswith("properties") or where.endswith("$defs"):
                        walk(v, where + "/" + k)          # property / definition names, not keywords
                        continue
                    self.assertIn(k, theme.SCHEMA_KEYWORDS, f"{where}/{k}")
                    walk(v, where + "/" + k)
            elif isinstance(n, list):
                for i, v in enumerate(n):
                    walk(v, f"{where}[{i}]")
        walk(s, "")

    def test_defaults_are_the_robots_preset_and_leos_palette(self):
        s, robots = theme.schema(), json.loads((theme.preset("robots") / "theme.json").read_text())
        cp = s["properties"]["colors"]["properties"]
        for g in ("state", "card"):
            for k, v in cp[g]["properties"].items():
                self.assertEqual(v["default"], robots["colors"][g][k], f"{g}.{k}")
        for pid in theme.FACTORY:
            doc = json.loads((theme.preset(pid) / "theme.json").read_text())
            self.assertTrue(set(map(str.lower, hexes(doc))) <= PALETTE, f"{pid}: {set(hexes(doc)) - PALETTE}")
        skill = pathlib.Path("~/.claude/skills/leo-design-language/references/leo-palette.json").expanduser()
        if skill.is_file():                                   # the frozen copy above is still the owner's palette
            live = {c["hex"].lower() for g in json.loads(skill.read_text())["groups"] for c in g["colors"]}
            self.assertEqual(live, PALETTE)

    def test_presets_match_the_web_page(self):
        f = theme.preset("robots")
        for n in ("robots-12.png", "robots-18.png"):
            self.assertEqual((f / n).read_bytes(), (WEB / "assets" / n).read_bytes(), n)
        lib = json.loads((WEB / "assets" / "robots.json").read_text())
        names = json.loads((f / "theme.json").read_text())["busy"]["robots"]["names"]
        self.assertEqual(names, [{"zh": r["zh"], "en": r["en"]} for r in lib["robots"]])
        css = (WEB / "app.css").read_text()
        dots = json.loads((theme.preset("dots") / "theme.json").read_text())["busy"]["dots"]["colors"]
        for i, p in enumerate(dots):
            self.assertIn(f"--busy-h{i}:{p['heavy']};--busy-l{i}:{p['light']}", css)
        self.assertEqual(len(dots), 9)
        site = HERE.parents[1] / "site" / "public" / "schema" / "agentj-theme-1.json"   # the site is not in the public tree
        if site.exists():
            self.assertEqual(site.read_bytes(), theme.SCHEMA_PATH.read_bytes(), "the site publishes the shipped schema")

    def test_jsonschema_agrees_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed (the built-in subset validator is the gate)")
        v = jsonschema.Draft202012Validator(theme.schema())
        jsonschema.Draft202012Validator.check_schema(theme.schema())
        with tempfile.TemporaryDirectory() as d:
            good = fx.make_theme(d)
        bad = [dict(good, id="X"), dict(good, colour={}), dict(good, colors={"state": {"idle": "green"}}),
               dict(good, busy={"style": "sparkles"}), {k: v for k, v in good.items() if k != "name"}, dict(good, schema=2)]
        for doc in [good, *(json.loads((theme.preset(p) / "theme.json").read_text()) for p in theme.FACTORY)]:
            self.assertEqual(list(v.iter_errors(doc)), [])
            self.assertEqual(theme.schema_errors(doc), [])
        for doc in bad:
            self.assertTrue(list(v.iter_errors(doc)))
            self.assertTrue(theme.schema_errors(doc), doc)


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name) / "t"

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, **over):
        doc = json.loads((self.d / "theme.json").read_text())
        for k, v in over.items():
            cur = doc
            *path, last = k.split("__")
            for p in path:
                cur = cur[p]
            cur[last] = v
        (self.d / "theme.json").write_text(json.dumps(doc))

    def test_factory_presets_pass_and_their_ids_are_reserved(self):
        for pid in theme.FACTORY:
            self.assertTrue(theme.validate(theme.preset(pid), factory_ok=True)["ok"], pid)
            self.assertEqual(codes(theme.validate(theme.preset(pid))), {"id.reserved"})

    def test_good_themes_pass(self):
        for style in ("robots", "dots"):
            with self.subTest(style=style):
                d = self.d.with_name(style)
                fx.make_theme(d, style=style)
                r = theme.validate(d)
                self.assertTrue(r["ok"], r["errors"])
                self.assertEqual(r["warnings"], [])

    def test_sections_are_optional(self):
        fx.make_theme(self.d)
        doc = {"schema": 1, "id": "only-green", "name": {"zh": "绿", "en": "Green"}, "colors": {"state": {"idle": "#2e7d32"}}}
        (self.d / "theme.json").write_text(json.dumps(doc))
        r = theme.validate(self.d)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual({w["code"] for w in r["warnings"]}, {"file.extra"}, "unused PNGs are reported, not copied")

    def test_folder_and_json_errors(self):
        self.assertEqual(codes(theme.validate(self.d)), {"dir.missing"})
        self.d.mkdir()
        self.assertEqual(codes(theme.validate(self.d)), {"json.missing"})
        (self.d / "theme.json").write_text("{nope")
        self.assertEqual(codes(theme.validate(self.d)), {"json.syntax"})
        (self.d / "theme.json").write_text(json.dumps({"schema": 1, "id": "a-b", "name": {"zh": "x", "en": "x"}, "colours": {}}))
        r = theme.validate(self.d)
        self.assertEqual(codes(r), {"schema.unknown"})
        self.assertEqual(r["errors"][0]["path"], "colours")
        self.assertIn("拼错", r["errors"][0]["zh"])

    def test_schema_errors_carry_the_path(self):
        fx.make_theme(self.d)
        self.edit(colors__state__idle="green", id="Bad Id")
        r = theme.validate(self.d)
        self.assertEqual({(e["code"], e["path"]) for e in r["errors"]},
                         {("schema.pattern", "colors.state.idle"), ("schema.pattern", "id")})

    def test_colour_rules(self):
        fx.make_theme(self.d)
        self.edit(colors__state__idle="#ffe292")                       # Lotion text on it: 1.2:1
        self.assertIn("color.contrast", codes(theme.validate(self.d)))
        fx.make_theme(self.d)
        self.edit(colors__state__working="#2d4f84")                    # next to idle #2d4f83
        self.assertEqual(codes(theme.validate(self.d)), {"color.distinct"})
        fx.make_theme(self.d)
        self.edit(colors__card__owner="#555555")                       # Noir text on it
        self.assertEqual(codes(theme.validate(self.d)), {"color.contrast"})
        fx.make_theme(self.d, style="dots")
        self.edit(busy__dots__colors=[{"heavy": "#2d4f83", "light": "#2d4f90"}])
        self.assertEqual(codes(theme.validate(self.d)), {"color.blink"})

    def test_custom_colours_need_not_be_leos(self):
        fx.make_theme(self.d)
        self.assertFalse(set(hexes(fx.COLOURS)) <= PALETTE)
        self.assertTrue(theme.validate(self.d)["ok"])

    def test_logo_rules(self):
        fx.make_theme(self.d)
        (self.d / "logo.png").write_bytes(fx.png(100, 80, lambda x, y: (0, 0, 0, 255)))
        self.assertEqual(codes(theme.validate(self.d)), {"png.size"})
        (self.d / "logo.png").write_bytes(b"\xff\xd8\xff\xe0 a jpeg")
        self.assertEqual(codes(theme.validate(self.d)), {"png.not_png"})
        (self.d / "logo.png").write_bytes(fx.png(128, 128, lambda x, y: (0, 0, 0, 0)))
        self.assertEqual(codes(theme.validate(self.d)), {"logo.empty"})
        (self.d / "logo.png").write_bytes(fx.png(128, 128, lambda x, y: (9, 9, 9, 255), extra_chunks=[(b"acTL", bytes(8))]))
        self.assertEqual(codes(theme.validate(self.d)), {"png.animated"})
        (self.d / "logo.png").write_bytes(fx.disc(256))
        self.assertTrue(theme.validate(self.d)["ok"], "256 × 256 is allowed too")
        big = os.urandom(70 * 1024)
        (self.d / "logo.png").write_bytes(big)
        self.assertEqual(codes(theme.validate(self.d)), {"file.size"})
        (self.d / "logo.png").unlink()
        os.symlink(theme.preset("robots") / "robots-12.png", self.d / "logo.png")
        self.assertEqual(codes(theme.validate(self.d)), {"file.missing"}, "a link is never followed")
        (self.d / "logo.png").unlink()
        (self.d / "logo.png").write_bytes(fx.disc(128)[:60])
        self.assertTrue(codes(theme.validate(self.d)) <= {"png.corrupt"} and codes(theme.validate(self.d)))

    def test_send_icon_rules(self):
        fx.make_theme(self.d)
        (self.d / "send.png").write_bytes(fx.png(128, 128, lambda x, y: (255, 255, 255, 255), ctype=2))
        self.assertEqual(codes(theme.validate(self.d)), {"send.alpha"})
        (self.d / "send.png").write_bytes(fx.png(128, 128, lambda x, y: (255, 255, 255, 255)))
        self.assertEqual(codes(theme.validate(self.d)), {"send.corners", "send.cover"})
        (self.d / "send.png").write_bytes(fx.disc(128, r=0.05))
        self.assertEqual(codes(theme.validate(self.d)), {"send.cover"})
        (self.d / "send.png").write_bytes(fx.arrow(64))
        self.assertEqual(codes(theme.validate(self.d)), {"png.size"})

    def test_robot_rules(self):
        fx.make_theme(self.d)
        self.edit(busy__robots__names=[{"zh": "一", "en": "One"}, {"zh": "二", "en": "Two"}])     # 2 names, 3 rows
        self.assertEqual(codes(theme.validate(self.d)), {"robots.size"})
        fx.make_theme(self.d)
        w, _h, rows, _a, _an = theme.read_png((self.d / "r12.png").read_bytes())
        soft = lambda x, y: tuple(rows[y][4 * x:4 * x + 3]) + ((128,) if rows[y][4 * x + 3] else (0,))   # noqa: E731
        (self.d / "r12.png").write_bytes(fx.png(w, 36, soft))
        self.assertEqual(codes(theme.validate(self.d)), {"robots.edges"})
        rainbow = lambda x, y: ((x * 37) % 256, (y * 53) % 256, 90, 255) if rows[y][4 * x + 3] else (0, 0, 0, 0)   # noqa: E731
        (self.d / "r12.png").write_bytes(fx.png(w, 36, rainbow))
        self.assertEqual(codes(theme.validate(self.d)), {"robots.colours"})
        no_bow = lambda x, y: (0, 0, 0, 0) if x >= 12 * 12 else tuple(rows[y][4 * x:4 * x + 4])   # noqa: E731
        (self.d / "r12.png").write_bytes(fx.png(w, 36, no_bow))
        r = theme.validate(self.d)
        self.assertEqual(codes(r), {"robots.empty"})
        self.assertIn("鞠躬帧", r["errors"][0]["zh"])
        self.assertIn("bow", r["errors"][0]["en"])

    def test_reading_pngs_of_every_colour_type(self):
        for ctype in (2, 6):
            w, h, rows, alpha, _ = theme.read_png(fx.png(3, 2, lambda x, y: (x, y, 7, 200), ctype=ctype))
            self.assertEqual((w, h, alpha), (3, 2, ctype == 6))
            self.assertEqual(rows[1][4:8], bytes((1, 1, 7, 200 if ctype == 6 else 255)))
        for n in ("robots-12.png", "robots-18.png"):                 # optimised files (filters 1–4 in use)
            w, h, rows, alpha, _ = theme.read_png((theme.preset("robots") / n).read_bytes())
            self.assertTrue(alpha)
            self.assertEqual(len(rows), h)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root, self.src = base / "state", base / "src"
        fx.make_theme(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        self.assertEqual(theme.current(self.root)["id"], "robots", "nothing applied = the factory robots")
        r = theme.apply_dir(self.root, self.src)
        self.assertTrue(r["ok"], r)
        self.assertEqual(theme.current(self.root)["kind"], "custom")
        first_sleep = r["dormant"]
        self.assertTrue(first_sleep.endswith("-robots"))
        active = theme.home(self.root) / "active"
        self.assertEqual(sorted(p.name for p in active.iterdir()), ["logo.png", "r12.png", "r18.png", "send.png", "theme.json"])
        (self.src / "logo.png").write_bytes(b"changed later")     # the applied copy is private
        self.assertTrue(theme.validate(active)["ok"])
        fx.make_theme(self.src)
        self.assertTrue(theme.apply_dir(self.root, self.src)["unchanged"])
        self.assertEqual(len(theme.dormant(self.root)), 1, "applying the same theme again changes nothing")
        r = theme.apply_preset(self.root, "dots")
        sleeping = {d["entry"]: d for d in theme.dormant(self.root)}
        self.assertEqual(sleeping[r["dormant"]]["id"], "night-owl")
        self.assertTrue((theme.home(self.root) / "dormant" / r["dormant"] / "r18.png").is_file(), "a sleeping theme keeps its files")
        back = theme.restore(self.root, r["dormant"])
        self.assertTrue(back["ok"], back)
        self.assertEqual(theme.current(self.root)["id"], "night-owl")
        self.assertNotIn(r["dormant"], {d["entry"] for d in theme.dormant(self.root)}, "the woken one leaves the dormant list")
        self.assertIn("dots", [d["id"] for d in theme.dormant(self.root)])
        reset = theme.apply_preset(self.root, theme.DEFAULT)
        self.assertEqual(theme.current(self.root)["id"], "robots")
        self.assertFalse(active.exists())
        self.assertTrue(theme.restore(self.root, first_sleep)["ok"], "the factory entry restores too")
        self.assertTrue(theme.apply_preset(self.root, "robots")["unchanged"])
        self.assertTrue(theme.restore(self.root, reset["dormant"])["ok"])
        self.assertEqual(theme.current(self.root)["id"], "night-owl")

    def test_a_failing_theme_changes_nothing(self):
        theme.apply_preset(self.root, "dots")
        before = (theme.home(self.root) / "current.json").read_bytes(), theme.dormant(self.root)
        (self.src / "send.png").write_bytes(fx.png(128, 128, lambda x, y: (255, 255, 255, 255)))
        r = theme.apply_dir(self.root, self.src)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "invalid")
        self.assertEqual(((theme.home(self.root) / "current.json").read_bytes(), theme.dormant(self.root)), before)
        self.assertFalse([p for p in theme.home(self.root).iterdir() if p.name.startswith(".staging")])
        self.assertEqual(theme.restore(self.root, "../etc")["error"], "unknown_entry")
        self.assertEqual(theme.restore(self.root, "nope")["error"], "unknown_entry")

    def test_dormant_is_bounded(self):
        with mock.patch.object(theme, "DORMANT_KEEP", 3):
            for i in range(6):
                theme.apply_preset(self.root, ("dots", "robots")[i % 2])
            self.assertEqual(len(theme.dormant(self.root)), 3)

    def test_export_gives_an_editable_template(self):
        out = pathlib.Path(self.tmp.name) / "mine"
        r = theme.export(self.root, "robots", out)
        self.assertTrue(r["ok"])
        doc = json.loads((out / "theme.json").read_text())
        self.assertEqual(doc["id"], "my-robots")
        self.assertTrue(theme.validate(out)["ok"], "a copy of the factory robots passes as a custom theme")
        self.assertEqual(theme.export(self.root, "robots", out)["error"], "dest_not_empty")
        theme.apply_dir(self.root, self.src)
        self.assertTrue(theme.export(self.root, "active", pathlib.Path(self.tmp.name) / "again")["ok"])

    def test_the_fenced_agent_sees_the_store(self):
        self.assertEqual(theme.home(self.root), self.root / "agentperm" / "theme")


class Phone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        self.src = pathlib.Path(self.tmp.name) / "src"
        fx.make_theme(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_factory_message_is_small(self):
        m = theme.phone_message(self.st.root)
        self.assertEqual(m, {"t": "theme", "v": 1, "id": "robots", "factory": "robots", "rev": "robots"})

    def test_custom_message_carries_everything_and_fits_frag(self):
        theme.apply_dir(self.st.root, self.src)
        m = theme.phone_message(self.st.root)
        self.assertIsNone(m["factory"])
        self.assertEqual(m["colors"], fx.COLOURS)
        self.assertEqual(m["busy"]["n"], 3)
        self.assertEqual(m["logo"]["disc"], True)
        import base64
        self.assertEqual(base64.b64decode(m["send"]["png"]), (self.src / "send.png").read_bytes())
        self.assertGreater(len(wire.frag_split(m, "f" * 16)), 0)

    def test_broken_active_falls_back(self):
        theme.apply_dir(self.st.root, self.src)
        (theme.home(self.st.root) / "active" / "r18.png").write_bytes(b"garbage")
        m = theme.phone_message(self.st.root)
        self.assertEqual((m["factory"], m.get("broken")), ("robots", True))

    def test_serve_sends_on_ready_and_on_change(self):
        host = serve.Host(self.st, events="jsonl", read_stdin=False)
        got = []

        async def send_app(s, obj):
            got.append(obj)
            return True
        host.send_app = send_app
        s = SimpleNamespace(cid="c", p33=True, state="ready", pub=b"", device="d")
        host.sessions = {"c": s}
        asyncio.run(host.on_ready(s, 0))
        self.assertEqual([m["id"] for m in got if m.get("t") == "theme"], ["robots"])
        self.assertFalse(host.theme_refresh(), "nothing changed")
        theme.apply_dir(self.st.root, self.src)
        self.assertTrue(host.theme_refresh())
        self.assertEqual(host.theme_cached["id"], "night-owl")
        self.assertFalse(host.theme_refresh())
        os.utime(theme.home(self.st.root) / "current.json", ns=(1, 1))
        self.assertFalse(host.theme_refresh(), "touched but the same theme: no resend")
        got.clear()
        s.p33 = False
        asyncio.run(host.on_ready(s, 0))
        self.assertNotIn("theme", [m.get("t") for m in got], "a §8-only phone never gets it")


class Skill(unittest.TestCase):
    SKILL = HERE.parent / "agentj" / "skills" / "agentj-theme" / "SKILL.md"

    def test_ships_and_states_the_same_rules(self):
        t = self.SKILL.read_text()
        self.assertTrue(t.startswith("---\nname: agentj-theme\n"))
        for needle in ("agentj theme validate", "agentj theme apply", "agentj theme restore", "agentj theme reset",
                       "agentj theme export robots", "**13 frames**", f"{theme.FRAMES * 12} × 12·N", f"{theme.FRAMES * 18} × 18·N",
                       f"≥ {theme.STATE_MIN_CONTRAST}:1", f"≥ {theme.CARD_MIN_CONTRAST}:1", "128×128 or 256×256",
                       f"≤ {theme.ROBOT_MAX_COLOURS} colours", "https://agentj.app/docs/theme/"):
            self.assertIn(needle, t)
        from agentj import personalize
        self.assertIn("agentj-theme", personalize.bundled())

    def test_its_png_snippet_writes_a_valid_png(self):
        code = re.search(r"```python\n(.*?)```", self.SKILL.read_text(), re.S).group(1)
        code = "\n".join(line[2:] for line in code.splitlines())
        ns = {}
        exec(code, ns)   # noqa: S102 — the skill's own snippet, from the repository
        with tempfile.TemporaryDirectory() as d:
            ns["png"](d + "/x.png", 156, 12, lambda x, y: (200, 10, 10, 255) if (x % 12) in (4, 5, 6) else (0, 0, 0, 0))
            w, h, rows, alpha, _ = theme.read_png(pathlib.Path(d + "/x.png").read_bytes())
            self.assertEqual((w, h, alpha), (156, 12, True))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        env = {**os.environ, "AGENTJ_STATE_DIR": self.state, "PYTHONPATH": str(HERE.parent)}
        return subprocess.run([sys.executable, "-m", "agentj.cli", "theme", *args], capture_output=True, text=True, env=env, timeout=60)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        self.src = pathlib.Path(self.tmp.name) / "src"
        fx.make_theme(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_validate_apply_list_restore_reset(self):
        r = self.run_cli("validate", str(self.src))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("全部通过", r.stdout)
        bad = pathlib.Path(self.tmp.name) / "bad"
        fx.make_theme(bad)
        (bad / "send.png").write_bytes(fx.arrow(64))
        r = self.run_cli("validate", str(bad))
        self.assertEqual(r.returncode, 1)
        self.assertIn("✗ send_icon.image:", r.stdout)
        self.assertIn("nothing was changed", r.stdout)
        self.assertEqual(self.run_cli("apply", str(bad)).returncode, 1)
        r = self.run_cli("apply", str(self.src), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        slept = json.loads(r.stdout)["dormant"]
        lst = json.loads(self.run_cli("list", "--json").stdout)
        self.assertEqual(lst["current"]["id"], "night-owl")
        self.assertEqual([d["entry"] for d in lst["dormant"]], [slept])
        self.assertEqual([p["id"] for p in lst["factory"] if p["default"]], ["robots"])
        self.assertEqual(self.run_cli("reset").returncode, 0)
        self.assertEqual(json.loads(self.run_cli("show", "--json").stdout)["current"]["id"], "robots")
        r = self.run_cli("restore", json.loads(self.run_cli("list", "--json").stdout)["dormant"][0]["entry"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.run_cli("list", "--json").stdout)["current"]["id"], "night-owl")
        self.assertEqual(self.run_cli("apply", "dots").returncode, 0)
        self.assertEqual(self.run_cli("restore", "no-such").returncode, 1)
        self.assertEqual(json.loads(self.run_cli("schema").stdout)["$id"], theme.schema()["$id"])
        self.assertEqual(self.run_cli("validate", "robots").returncode, 0, "a preset name validates the preset")

    def test_light_dark_still_go_to_appearance(self):
        with mock.patch("agentj.personalize.command", return_value=0) as pc:
            self.assertEqual(theme.command(["dark"]), 0)
            self.assertEqual(theme.command(["set", "light"]), 0)
        self.assertEqual([c.args[0] for c in pc.call_args_list], [["theme", "dark"], ["theme", "set", "light"]])


if __name__ == "__main__":
    unittest.main()
