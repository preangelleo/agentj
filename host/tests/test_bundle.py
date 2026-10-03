"""bundle.py (protocol/PLAZA_PACKAGES.md §1): deterministic build, strict parse, paths, forbidden files, limits, manifest checks,
and every package of the real catalog (read-only; skipped when the catalog is absent)."""
import ast
import base64
import gzip
import json
import os
import pathlib
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import market_fixtures as fx  # noqa: E402
from jarvis_host import bundle  # noqa: E402

# Known catalog defects (reported to the catalog owner); a package listed here may be refused, every other must build.
CATALOG_KNOWN_BAD: dict = {}   # every catalog package must bundle (app-foundry fixed by the 500 summary cap)


def raw_bundle(b: dict) -> bytes:
    return gzip.compress(bundle.canonical_json(b), compresslevel=9, mtime=0)


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def test_deterministic_and_round_trip(self):
        d = fx.make_skill(self.root)
        a, m = bundle.build(d)
        b, _ = bundle.build(d)
        self.assertEqual(a, b, "same folder → same bytes")
        self.assertEqual(a[:4], b"\x1f\x8b\x08\x00", "gzip, no FNAME flag")
        self.assertEqual(a[4:8], b"\x00\x00\x00\x00", "mtime 0")
        os.utime(d / "README.md", (1, 1))
        self.assertEqual(bundle.build(d)[0], a, "file times do not matter")
        m2, files = bundle.parse(a)
        self.assertEqual(m2, m)
        self.assertEqual(set(files), set(fx.SKILL_FILES))
        self.assertIn("references/示例-退换货规则.md", files)
        for dot in (".env.example", ".gitignore", "tests/.gitkeep"):
            self.assertIn(dot, files)
        self.assertEqual([f["path"] for f in m["files"]], sorted(files, key=lambda p: p.encode()))
        self.assertEqual(bundle.sha256(a), __import__("hashlib").sha256(a).hexdigest())

    def test_manifest_files_regenerated(self):
        d = fx.make_skill(self.root, files={**fx.SKILL_FILES})
        m = json.loads((d / "manifest.json").read_text())
        m["files"] = [{"path": "bogus.txt", "sha256": "0" * 64, "bytes": 1}]
        (d / "manifest.json").write_text(json.dumps(m))
        _, built = bundle.build(d)
        self.assertNotIn("bogus.txt", [f["path"] for f in built["files"]])

    def test_workflow_dotfiles_and_claude_dirs(self):
        d = fx.make_workflow(self.root)
        _, files = bundle.parse(bundle.build(d)[0])
        for p in ("scaffold/.claude/agents/writer.md", "scaffold/.handoff", "scaffold/.gitignore", "scaffold/reports/.gitkeep"):
            self.assertIn(p, files)

    def test_forbidden_files_refused(self):
        for bad in (".git/config", "a/.ssh/x", ".env", ".env.local", "conf/.ENV.prod", "server.pem", "k/private.KEY",
                    "id_rsa", "keys/id_ed25519.pub", "__pycache__/x.pyc", "x/.DS_Store"):
            with self.subTest(bad):
                root = self.root / bad.replace("/", "_")
                d = fx.make_skill(root, files={**fx.SKILL_FILES, bad: "x"})
                with self.assertRaises(bundle.BundleError):
                    bundle.build(d)

    def test_empty_forbidden_folder_refused(self):
        d = fx.make_skill(self.root)
        (d / ".git").mkdir()
        with self.assertRaises(bundle.BundleError):
            bundle.build(d)

    def test_symlinks_devices_and_bad_names_refused(self):
        d = fx.make_skill(self.root / "a")
        (d / "link.md").symlink_to(d / "README.md")
        with self.assertRaisesRegex(bundle.BundleError, "symlink"):
            bundle.build(d)
        d = fx.make_skill(self.root / "b")
        (d / "dirlink").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(bundle.BundleError, "symlink"):
            bundle.build(d)
        d = fx.make_skill(self.root / "c")
        os.mkfifo(d / "pipe")
        with self.assertRaisesRegex(bundle.BundleError, "regular"):
            bundle.build(d)
        for name in ("with space.md", "colon:x.md", "back\\slash.md", "ctl\x07.md", unicodedata.normalize("NFD", "é.md")):
            with self.subTest(name):
                r = self.root / f"n{abs(hash(name))}"
                dd = fx.make_skill(r)
                (dd / name).write_text("x")
                with self.assertRaises(bundle.BundleError):
                    bundle.build(dd)
        link = self.root / "skill-link"
        link.symlink_to(fx.make_skill(self.root / "e"))
        with self.assertRaises(bundle.BundleError):
            bundle.build(link)

    def test_limits(self):
        d = fx.make_skill(self.root / "big")
        (d / "big.bin").write_bytes(os.urandom(bundle.MAX_FILE + 1))
        with self.assertRaisesRegex(bundle.BundleError, "2 MiB"):
            bundle.build(d)
        d = fx.make_skill(self.root / "many")
        for i in range(bundle.MAX_FILES):
            (d / f"f{i}.txt").write_text("x")
        with self.assertRaisesRegex(bundle.BundleError, "too many"):
            bundle.build(d)
        d = fx.make_skill(self.root / "incompressible")
        for i in range(2):
            (d / f"r{i}.bin").write_bytes(os.urandom(bundle.MAX_FILE - 10))
        with self.assertRaisesRegex(bundle.BundleError, "bundle .* > 2 MiB"):
            bundle.build(d)

    def test_manifest_checks(self):
        cases = {
            "name": {"name": "Bad_Name"}, "name2": {"name": "a"}, "type": {"type": "plugin"}, "version": {"version": "1.0"},
            "title": {"title_zh": ""}, "title_long": {"title_en": "x" * 81}, "summary": {"summary_en": "x" * 501},
            "tags": {"tags": ["x"] * 13}, "tag_long": {"tags": ["x" * 25]}, "category": {"category": "games"},
            "entry": {"entry": "missing.md"}, "schema": {"schema_version": "v0"}, "certified": {"certified": "yes"},
            "dir": {"install": {"skill_dir_name": "../../.ssh"}}, "dir2": {"install": {"skill_dir_name": "Skill"}},
            "verify": {"install": {"skill_dir_name": "demo-skill", "verify": ["x"]}},
            "harness": {"requires": {"harness": ["claude-code"]}}, "env": {"requires": {"env": [{"name": "BAD NAME"}]}},
            "skills": {"requires": {"skills": ["../x"]}}, "cli": {"requires": {"cli": [{"version": "1"}]}},
        }
        for why, over in cases.items():
            with self.subTest(why):
                d = fx.make_skill(self.root / why, **over)
                with self.assertRaises(bundle.BundleError):
                    bundle.build(d)
        files = {k: v for k, v in fx.SKILL_FILES.items() if k != "README.md"}
        with self.assertRaisesRegex(bundle.BundleError, "README"):
            bundle.build(fx.make_skill(self.root / "noreadme", files=files))
        wf = {k: v for k, v in fx.WORKFLOW_FILES.items() if not k.startswith("scaffold/")}
        with self.assertRaisesRegex(bundle.BundleError, "scaffold"):
            bundle.build(fx.make_workflow(self.root / "noscaffold", files=wf, entry="README.md"))
        wf = {**fx.WORKFLOW_FILES, "params.schema.json": "{not json"}
        with self.assertRaisesRegex(bundle.BundleError, "params"):
            bundle.build(fx.make_workflow(self.root / "badparams", files=wf))
        d = self.root / "nomanifest"
        d.mkdir()
        with self.assertRaisesRegex(bundle.BundleError, "manifest"):
            bundle.build(d)

    def test_case_and_folder_collisions(self):
        with self.assertRaises(bundle.BundleError):
            bundle.check_path_set(["A.md", "a.md"])
        with self.assertRaises(bundle.BundleError):
            bundle.check_path_set(["Docs/x.md", "docs/y.md"])
        with self.assertRaises(bundle.BundleError):
            bundle.check_path_set(["x", "x/y"])
        for bad in ("/etc/passwd", "../x", "a/../b", "a//b", "./a", "a/", "", "x" * 201, "a/" + "b" * 81, "C:x", "a\\b"):
            with self.subTest(bad), self.assertRaises(bundle.BundleError):
                bundle.check_path(bad)
        for ok in ("示例-退换货规则.md", ".env.example", ".claude/agents/x.md", "a+b@c_d-e.f"):
            self.assertEqual(bundle.check_path(ok), ok)


class Parse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data, self.m = bundle.build(fx.make_skill(pathlib.Path(self.tmp.name)))
        self.b = json.loads(gzip.decompress(self.data))

    def refuse(self, b, msg=None):
        with self.assertRaises(bundle.BundleError) as cm:
            bundle.parse(raw_bundle(b) if isinstance(b, dict) else b)
        if msg:
            self.assertIn(msg, str(cm.exception))

    def mutated(self, fn):
        b = json.loads(json.dumps(self.b))
        fn(b)
        return b

    def test_tampering_refused(self):
        def tamper(b):
            b["files"][0]["b64"] = base64.b64encode(b"changed").decode()
        self.refuse(self.mutated(tamper), "SHA-256")
        self.refuse(self.mutated(lambda b: b["files"].append({"path": "zzz.md", "b64": ""})))
        self.refuse(self.mutated(lambda b: b["files"].pop()))
        self.refuse(self.mutated(lambda b: b["files"].insert(0, dict(b["files"][0]))))
        self.refuse(self.mutated(lambda b: b["files"].reverse()))
        self.refuse(self.mutated(lambda b: b.update(extra=1)))
        self.refuse(self.mutated(lambda b: b.update(schema="agentjarvis.bundle/v2")))
        self.refuse(self.mutated(lambda b: b["files"][0].update(mode=0o777)))

    def test_paths_inside_a_bundle(self):
        for bad in ("../../.ssh/authorized_keys", "/etc/cron.d/x", "a/../../x", ".git/hooks/post-checkout", "x/.env"):
            with self.subTest(bad):
                def add(b, bad=bad):
                    b["files"].append({"path": bad, "b64": ""})
                    b["manifest"]["files"].append({"path": bad, "sha256": bundle.sha256(b""), "bytes": 0})
                self.refuse(self.mutated(add))

    def test_encoding_rules(self):
        raw = bundle.canonical_json(self.b)
        self.refuse(gzip.compress(json.dumps(self.b, indent=1).encode(), mtime=0), "canonical")
        self.refuse(gzip.compress(raw, mtime=0) + b"junk", "trailing")
        self.refuse(gzip.compress(raw, mtime=0)[:-20])
        self.refuse(b"not gzip")
        self.refuse(gzip.compress(raw.replace(b'"schema"', b'"schema":"x","schema"', 1), mtime=0))

        def pad(b):
            b["files"][0]["b64"] = b["files"][0]["b64"] + "\n"
        self.refuse(self.mutated(pad))
        self.assertEqual(bundle.parse(gzip.compress(raw, mtime=0))[0], self.m, "any gzip level is fine; the JSON is what counts")

    def test_size_caps(self):
        self.refuse(b"\x1f\x8b" + b"\x00" * bundle.MAX_BUNDLE, "2 MiB")
        bomb = gzip.compress(b" " * (bundle.MAX_JSON + 10), mtime=0)
        self.assertLess(len(bomb), bundle.MAX_BUNDLE)
        self.refuse(bomb, "12 MiB")


class Stdlib(unittest.TestCase):
    def test_bundle_is_stdlib_only(self):
        tree = ast.parse((pathlib.Path(bundle.__file__)).read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                self.assertEqual(n.level, 0, "no relative imports: the admin tool loads this file by path")
                mods.add((n.module or "").split(".")[0])
        self.assertTrue(mods <= set(sys.stdlib_module_names) | {"__future__"}, mods)


@unittest.skipUnless(fx.CATALOG.is_dir(), "the catalog is not on this machine")
class Catalog(unittest.TestCase):
    def test_every_catalog_package_builds_and_parses(self):
        pkgs = sorted([*(fx.CATALOG / "skills").iterdir(), *(fx.CATALOG / "workflows").iterdir()])
        self.assertGreaterEqual(len(pkgs), 33)
        refused = {}
        for d in pkgs:
            try:
                data, m = bundle.build(d)
            except bundle.BundleError as e:
                refused[d.name] = str(e)
                continue
            m2, files = bundle.parse(data)
            self.assertEqual(m2["name"], d.name)
            self.assertEqual(bundle.build(d)[0], data)
            listed = json.loads((d / "manifest.json").read_text())["files"]
            self.assertEqual(sorted((f["path"], f["sha256"]) for f in listed), sorted((f["path"], f["sha256"]) for f in m["files"]),
                             f"{d.name}: the catalog's own files list agrees")
        for name, why in refused.items():
            self.assertIn(name, CATALOG_KNOWN_BAD, f"{name}: {why}")
            self.assertIn(CATALOG_KNOWN_BAD[name], why)


if __name__ == "__main__":
    unittest.main()
