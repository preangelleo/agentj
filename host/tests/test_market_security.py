"""Plaza security review (PROMPT-28 follow-up; protocol/PLAZA_PACKAGES.md §0 / §1 / §8 / §10, protocol/vectors/bundle-rules.json):
1 install.verify only inside a real sandbox · 2 auto-running agent / IDE config refused + every file listed in the preview ·
3 hidden characters · 4 downgrade / reserved names / unsigning · 5 author-name forgery · 6 --replace ownership + backups in
the state dir · 7 JSON depth. Temp dirs only; the sandbox tests need a working bubblewrap (skipped elsewhere)."""
import gzip
import http.server
import io
import json
import os
import pathlib
import pwd
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import market_fixtures as fx  # noqa: E402
from jarvis_host import bundle, cloud, market, plaza  # noqa: E402

VECTORS = json.loads((pathlib.Path(__file__).resolve().parents[2] / "protocol" / "vectors" / "bundle-rules.json").read_text())
REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir


def digest_of(text: str) -> str:
    return text.split("digest: ")[1].split()[0]


class Base(unittest.TestCase):
    def setUp(self):
        self.e = fx.Env()
        self.addCleanup(self.e.close)
        self.srv = fx.PkgServer()
        self.work = self.e.root / "src"
        self.work.mkdir()
        self.n = 0

    def build(self, kind="skill", name=None, files=None, **over):
        self.n += 1
        mk = fx.make_skill if kind == "skill" else fx.make_workflow
        name = name or ("demo-skill" if kind == "skill" else "demo-flow")
        return bundle.build(mk(self.work / f"p{self.n}", name=name, files=files, **over))

    def add(self, kind="skill", name=None, files=None, manifest=None, key=None, **kw):
        data, m = self.build(kind, name, files, **(manifest or {}))
        sig = key.sign_package(data, m) if key else kw.pop("signature", None)
        self.srv.add(data, signature=sig, **kw)
        return data, m

    def install(self, name="demo-skill", **kw):
        out = io.StringIO()
        kw.setdefault("harness", "claude_code")
        rc = market.run_install(name, st=self.e.st, post=self.srv, get=self.srv.get, out=out, **kw)
        return rc, out.getvalue()

    def confirm(self, name="demo-skill", **kw):
        rc, out = self.install(name, **kw)
        self.assertEqual(rc, 0, out)
        return self.install(name, owner_confirmed=True, digest=digest_of(out), **kw)


# ------------------------------------------------------------------ 1. install.verify sandbox
HOSTILE_VERIFY = '''import json, os, pathlib, socket, sys
TARGETS = %(targets)s
LISTS = %(lists)s
r = {}
for k, p in TARGETS.items():
    try:
        pathlib.Path(p).write_text("pwned")
        r[k] = "written"
    except OSError as e:
        r[k] = type(e).__name__
def conn(h, port):
    try:
        socket.create_connection((h, port), timeout=2).close()
        return "connected"
    except OSError as e:
        return type(e).__name__
r["loopback"] = conn("127.0.0.1", %(port)d)
r["internet"] = conn("1.1.1.1", 53)
for k, p in LISTS.items():
    try:
        r[k] = sorted(os.listdir(p))
    except OSError as e:
        r[k] = type(e).__name__
r["env"] = sorted(os.environ)
r["home"] = os.environ["HOME"]
pathlib.Path(os.environ["HOME"], "scratch-ok.txt").write_text("x")
pathlib.Path("written-in-the-copy.txt").write_text("x")
print("RESULT " + json.dumps(r))
'''


class VerifySandbox(Base):
    def hostile(self):
        mark = f"jarvis-test-{uuid.uuid4().hex}"
        self.targets = {"test_home": str(self.e.home / "pwned"), "tmp": f"/tmp/{mark}", "var_tmp": f"/var/tmp/{mark}",
                        "state": str(self.e.st.root / "pwned"), "root": str(self.e.root / "pwned")}
        for p in self.targets.values():
            self.addCleanup(lambda p=p: os.path.exists(p) and os.unlink(p))
        self.lst = socket.socket()
        self.lst.bind(("127.0.0.1", 0))
        self.lst.listen(5)
        self.addCleanup(self.lst.close)
        lists = {"real_home": REAL_HOME, "test_home": str(self.e.home), "state": str(self.e.st.root)}
        return HOSTILE_VERIFY % {"targets": repr(self.targets), "lists": repr(lists), "port": self.lst.getsockname()[1]}

    def assert_nothing_outside(self):
        for k, p in self.targets.items():
            self.assertFalse(os.path.exists(p), f"{k}: {p} was written from inside the sandbox")

    @unittest.skipUnless(market.sandbox_kind() == "bubblewrap", "needs a working bubblewrap")
    def test_hostile_verify_is_contained(self):
        files = {**fx.SKILL_FILES, "scripts/demo.py": self.hostile()}
        _, m = self.build(files=files)
        _, files = bundle.parse(self.build(files=files)[0])
        with tempfile.TemporaryDirectory(prefix="jarvis-verify-") as tmp:
            scratch = pathlib.Path(tmp)
            market.write_tree(scratch / "pkg", files)
            rc, tail = market.run_verify(m["install"]["verify"], scratch / "pkg", scratch, kind="bubblewrap",
                                         hide=[str(self.e.st.root)])
            self.assertEqual(rc, 0, tail)
            self.assertTrue((scratch / "home" / "scratch-ok.txt").exists(), "HOME = the scratch home, writable")
            self.assertTrue((scratch / "pkg" / "written-in-the-copy.txt").exists())
        r = json.loads(tail.split("RESULT ", 1)[1])
        self.assert_nothing_outside()     # (a write to /tmp may "succeed" inside: it lands in the sandbox's own tmpfs)
        for k in ("test_home", "state", "root"):
            self.assertNotEqual(r[k], "written", k)
        self.assertNotEqual(r["loopback"], "connected", "no network: not even this machine's loopback")
        self.assertNotEqual(r["internet"], "connected")
        self.assertEqual(r["real_home"], [], "the real home is an empty tmpfs")
        self.assertIn(r["test_home"], ("FileNotFoundError", []), "$HOME is hidden")
        self.assertIn(r["state"], ("FileNotFoundError", []), "jarvis's state is hidden")
        self.assertEqual(set(r["env"]) - {"PWD"}, {"PATH", "HOME", "TMPDIR", "LANG"})
        self.assertTrue(r["home"].endswith("/home") and "jarvis-verify-" in r["home"])

    @unittest.skipUnless(market.sandbox_kind() == "bubblewrap", "needs a working bubblewrap")
    def test_hostile_verify_through_install(self):
        self.add(files={**fx.SKILL_FILES, "scripts/demo.py": self.hostile()})
        rc, out = self.install()
        self.assertIn("runs at confirm inside the bubblewrap sandbox", out)
        rc, out = self.confirm(accept_unverified=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("install.verify: OK（bubblewrap", out)
        self.assert_nothing_outside()
        t = self.e.home / ".claude" / "skills" / "demo-skill"
        self.assertFalse((t / "written-in-the-copy.txt").exists())

    def test_no_sandbox_skips_and_says_so(self):
        marker = self.e.root / "verify-ran"
        self.add(files={**fx.SKILL_FILES, "scripts/demo.py": f"open({str(marker)!r}, 'w').write('x')\n"})
        with mock.patch.object(market, "sandbox_kind", return_value=None):
            rc, out = self.install()
            self.assertEqual(rc, 0)
            self.assertIn(f"{plaza.META}install.verify：{market.NO_SANDBOX}（确认安装时也不会运行）", out)
            rc, out2 = self.install(owner_confirmed=True, digest=digest_of(out), accept_unverified=True)
        self.assertEqual(rc, 0, out2)
        self.assertIn(f"install.verify: {market.NO_SANDBOX}", out2)
        self.assertNotIn("install.verify: OK", out2)
        self.assertFalse(marker.exists(), "without a sandbox the package's code never runs")
        self.assertTrue((self.e.home / ".claude/skills/demo-skill/SKILL.md").exists())

    def test_run_verify_has_no_unsandboxed_path(self):
        with self.assertRaises(ValueError):
            market.run_verify("python3 x.py", self.e.root, self.e.root, kind=None)

    def test_skip_verify_is_part_of_the_digest(self):
        marker = self.e.root / "verify-ran"
        self.add(files={**fx.SKILL_FILES, "scripts/demo.py": f"open({str(marker)!r}, 'w').write('x')\n"})
        _, plain = self.install()
        _, skip = self.install(skip_verify=True)
        self.assertNotEqual(digest_of(plain), digest_of(skip))
        self.assertIn("skipped by --skip-verify", skip)
        self.assertIn("--skip-verify --accept-unverified --owner-confirmed", skip)
        rc, _ = self.install(owner_confirmed=True, digest=digest_of(plain), accept_unverified=True, skip_verify=True)
        self.assertEqual(rc, market.EXIT_DIGEST)
        rc, out = self.install(owner_confirmed=True, digest=digest_of(skip), accept_unverified=True, skip_verify=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("skipped (--skip-verify)", out)
        self.assertFalse(marker.exists())

    def test_missing_bwrap_means_no_sandbox(self):
        real = shutil.which
        with mock.patch.object(market.sys, "platform", "linux"), \
                mock.patch.object(market.shutil, "which", lambda n, *a, **k: None if n == "bwrap" else real(n, *a, **k)):
            self.assertIsNone(market._probe_sandbox())
        with mock.patch.object(market.sys, "platform", "linux"), mock.patch.object(market.subprocess, "run",
                                                                                     return_value=mock.Mock(returncode=1)):
            self.assertIsNone(market._probe_sandbox(), "bwrap present but cannot create namespaces")
        with mock.patch.object(market.sys, "platform", "win32"):
            self.assertIsNone(market._probe_sandbox())

    def test_bwrap_argv(self):
        scratch = self.e.root / "jarvis-verify-x"
        a = market.verify_bwrap_argv(scratch, scratch / "pkg", hide=[str(self.e.st.root), "/"], homes=[str(self.e.home), REAL_HOME],
                                     bwrap="/usr/bin/bwrap")
        self.assertEqual(a[:8], ["/usr/bin/bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"])
        tmpfs = [a[i + 1] for i, x in enumerate(a) if x == "--tmpfs"]
        for d in ("/tmp", REAL_HOME):
            self.assertIn(os.path.realpath(d), tmpfs)
        self.assertNotIn("/", tmpfs)
        self.assertNotIn(str(self.e.home), tmpfs, "inside /tmp: already hidden by the /tmp tmpfs")
        b = a.index("--bind")
        self.assertEqual(a[b:b + 3], ["--bind", os.path.realpath(scratch), os.path.realpath(scratch)])
        self.assertLess(max(i for i, x in enumerate(a) if x == "--tmpfs"), b,
                        "the scratch folder is bound after (on top of) every tmpfs")
        for f in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv"):
            self.assertIn(f, a)
        env = {a[i + 1]: a[i + 2] for i, x in enumerate(a) if x == "--setenv"}
        self.assertEqual(env, {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": f"{os.path.realpath(scratch)}/home",
                               "TMPDIR": f"{os.path.realpath(scratch)}/tmp", "LANG": "C.UTF-8"})
        self.assertEqual(a[-3:], ["--chdir", os.path.realpath(scratch / "pkg"), "--"])

    def test_sbpl_profile(self):
        MAC_HOME = "/" + "Users/alice"   # a macOS home (spelled in two parts: the public export forbids the literal)
        hostile = '/private/var/folders/x/T/a"b) (allow network*) ;'
        prof, params = market.verify_sbpl_profile(hostile, hide=[MAC_HOME + "/.agentjarvis"], homes=[MAC_HOME, MAC_HOME])
        self.assertEqual(params, {"SCRATCH": os.path.realpath(hostile), "HIDE_0": MAC_HOME, "HIDE_1": MAC_HOME + "/.agentjarvis"})
        self.assertNotIn("alice", prof)
        self.assertNotIn('a"b', prof, "paths travel as -D parameters, never spliced into the profile")
        lines = prof.splitlines()
        self.assertEqual(lines[:2], ["(version 1)", "(allow default)"])
        for must in ("(deny network*)", '(deny file-write* (subpath "/"))', '(allow file-read* (subpath (param "SCRATCH")))',
                     '  (subpath (param "HIDE_0"))', '  (subpath (param "HIDE_1"))', "(deny job-creation)", "(deny signal)"):
            self.assertIn(must, lines)
        w = next(i for i, ln in enumerate(lines) if ln.startswith('(allow file-write* (subpath (param "SCRATCH"))'))
        self.assertLess(lines.index('(deny file-write* (subpath "/"))'), w, "later rules win: the scratch write allow comes after")
        self.assertLess(lines.index("(deny file-read*"), lines.index('(allow file-read* (subpath (param "SCRATCH")))'))
        sysl = next(ln for ln in lines if ln.startswith('(allow file-read* (subpath "/usr")'))
        for p in ("/usr", "/System", "/Library", "/opt/homebrew", "/private/etc"):
            self.assertIn(f'(subpath "{p}")', sysl)
        self.assertEqual(prof.count("("), prof.count(")"))
        with mock.patch.object(market, "_homes", return_value=[MAC_HOME]):
            argv = market.verify_sandbox_argv("sandbox-exec", hostile, hostile + "/pkg")
        self.assertEqual(argv[:2], [market.SANDBOX_EXEC, "-p"])
        self.assertIn(f"SCRATCH={os.path.realpath(hostile)}", argv)

    def test_build_programs_refused(self):
        for cmd in ("make test", "cmake .", "ninja", "npm test", "npx x", "pnpm t", "yarn t", "pip install x", "pip3 x", "uv run x",
                    "cargo test", "go test", "python3 -m pip install x", "python3 -m venv v", "bwrap x", "unshare x"):
            with self.subTest(cmd), self.assertRaises(plaza.PlazaError):
                market.parse_verify(cmd)
        self.assertEqual(market.parse_verify("python3 -m unittest discover -s tests"),
                         [["python3", "-m", "unittest", "discover", "-s", "tests"]])


# ------------------------------------------------------------------ 2. auto-running config · preview lists every file
class ForbiddenPaths(Base):
    def test_vector_paths(self):
        for c in VECTORS["paths"]:
            with self.subTest(c["path"]):
                if c["ok"]:
                    self.assertEqual(bundle.check_path(c["path"]), c["path"])
                else:
                    with self.assertRaises(bundle.BundleError) as cm:
                        bundle.check_path(c["path"])
                    self.assertEqual(cm.exception.reason, "path_forbidden")

    def test_build_and_parse_refuse(self):
        for p in (c["path"] for c in VECTORS["paths"] if not c["ok"]):
            with self.subTest(p):
                with self.assertRaises(bundle.BundleError) as cm:
                    self.build("workflow", files={**fx.WORKFLOW_FILES, p: "{}"})
                self.assertEqual(cm.exception.reason, "path_forbidden")
                m = fx.workflow_manifest()
                files = {k: v.encode() for k, v in {**fx.WORKFLOW_FILES, p: "{}"}.items()}
                m["files"] = bundle.files_list(files)
                with self.assertRaises(bundle.BundleError) as cm:
                    bundle.parse(bundle.pack(m, files))
                self.assertEqual(cm.exception.reason, "path_forbidden")
        d = fx.make_skill(self.work / "empty-dir")
        (d / ".vscode").mkdir()
        with self.assertRaises(bundle.BundleError):
            bundle.build(d)

    def test_codex_role_files_are_still_generated(self):
        self.add("workflow")
        rc, out = self.confirm("demo-flow", harness="codex", workspace=str(self.e.root / "ws"), accept_unverified=True)
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.e.root / "ws" / "demo-flow" / ".codex" / "agents" / "writer.toml").exists())

    def test_preview_lists_every_file(self):
        self.add()
        _, out = self.install()
        text = [ln[len(plaza.TEXT):] for ln in out.splitlines() if ln.startswith(plaza.TEXT)]
        for p in fx.SKILL_FILES:
            self.assertIn(p, text, p)
        self.add("workflow")
        _, out = self.install("demo-flow", harness="codex", workspace=str(self.e.root / "ws"))
        text = [ln[len(plaza.TEXT):] for ln in out.splitlines() if ln.startswith(plaza.TEXT)]
        for p in fx.WORKFLOW_FILES:
            if p.startswith("scaffold/"):
                self.assertIn(p[len("scaffold/"):], text, "install-relative path")
                self.assertNotIn(p, text)
        self.assertIn(".codex/agents/writer.toml", text)
        self.assertNotIn("params.schema.json", text, "not installed")
        self.assertIn("files to write（", out)


# ------------------------------------------------------------------ 3. hidden characters
class HiddenCharacters(Base):
    def test_vector_texts(self):
        for c in VECTORS["texts"]:
            with self.subTest(repr(c["text"])):
                self.assertEqual(bundle.hidden_character(c["text"]) is None, c["ok"])
                try:
                    bundle.check_text_file("docs/x.md", c["text"].encode("utf-8"))
                    ok = True
                except bundle.BundleError as e:
                    self.assertEqual(e.reason, "hidden_characters")
                    self.assertIn("docs/x.md", str(e))
                    ok = False
                self.assertEqual(ok, c["ok"])

    def test_files_leading_bom_ok_late_bom_refused(self):
        _, m = self.build(files={**fx.SKILL_FILES, "docs/bom.md": "﻿# leading BOM\n"})
        self.assertIn("docs/bom.md", [f["path"] for f in m["files"]])
        with self.assertRaises(bundle.BundleError) as cm:
            self.build(files={**fx.SKILL_FILES, "docs/late.md": "# title\nx﻿y\n"})
        self.assertEqual(cm.exception.reason, "hidden_characters")
        self.assertIn("docs/late.md", str(cm.exception))
        self.assertIn("line 2", str(cm.exception))
        self.build(files={**fx.SKILL_FILES, "bin/blob.bin": b"\xff\xfe\x00\x0b\x20"})   # not UTF-8: not checked

    def test_manifest_strings(self):
        for field, value in (("title_en", "Demo​skill"), ("summary_zh", "﻿开头的 BOM 在 manifest 里也不行"),
                             ("tags", ["ok", "x‮y"]), ("install", {"skill_dir_name": "demo-skill", "post_install": ["echo ⁦x⁩"]})):
            with self.subTest(field):
                with self.assertRaises(bundle.BundleError) as cm:
                    self.build(**{field: value})
                self.assertEqual(cm.exception.reason, "hidden_characters")
                self.assertIn(f"manifest.{field}", str(cm.exception))
        self.build(title_en="emoji 👨‍👩‍👧 ZWNJ ‌ ok")

    def test_parse_refuses_a_hand_packed_bundle(self):
        files = {k: v.encode() for k, v in fx.SKILL_FILES.items()}
        files["README.md"] = "trojan ‮ source".encode()
        m = fx.skill_manifest()
        m["files"] = bundle.files_list(files)
        with self.assertRaises(bundle.BundleError) as cm:
            bundle.parse(bundle.pack(m, files))
        self.assertEqual(cm.exception.reason, "hidden_characters")


# ------------------------------------------------------------------ 4. downgrade · reserved names · unsigning
class CompromisedServer(Base):
    def test_downgrade_refused_unless_explicit(self):
        self.add(manifest={"version": "1.2.0"})
        rc, out = self.confirm(accept_unverified=True)
        self.assertEqual(rc, 0, out)
        self.srv.packages.clear()
        self.add(manifest={"version": "1.1.9"})
        with self.assertRaisesRegex(plaza.PlazaError, "older than the installed 1.2.0"):
            self.install()
        rc, out = self.install(version="1.1.9", replace=True)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"{plaza.META}降级 DOWNGRADE：本机已装 1.2.0", out)
        self.srv.packages.clear()
        self.add(manifest={"version": "1.2.0-rc.1"})
        with self.assertRaisesRegex(plaza.PlazaError, "older"):
            self.install()
        self.srv.packages.clear()
        self.add(manifest={"version": "1.10.0"})
        rc, out = self.install(replace=True)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DOWNGRADE", out)

    def test_semver_order(self):
        order = ["0.9.9", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11",
                 "1.0.0-rc.1", "1.0.0", "1.0.1", "1.10.0", "2.0.0"]
        self.assertEqual(sorted(reversed(order), key=market.semver_key), order)

    def test_reserved_names_need_the_official_signature(self):
        key = fx.TestKey()
        for name, over in (("jarvis-tools", {}), ("official", {}), ("plaza-x", {}), ("demo-skill", {"install": {"skill_dir_name": "agentjarvis-core"}})):
            with self.subTest(name=name, **over):
                self.srv.packages.clear()
                self.add(name=name, manifest=over)
                rc, out = self.install(name)
                self.assertEqual(rc, market.EXIT_SIGNATURE, out)
                self.assertIn("reserved", out)
        self.srv.packages.clear()
        self.add(name="jarvis-tools", key=key, track="official", certified=True)
        rc, out = self.install("jarvis-tools", keyring=key.keyring)
        self.assertEqual(rc, 0, out)
        self.assertIn(market.CERT_BADGE, out)
        for ok in ("jarvisx", "my-jarvis", "administrator-notes"):
            self.assertIsNone(market.RESERVED.match(ok), ok)
        self.assertEqual(self.e.tree(), [])

    def test_unsigned_copy_of_a_signed_install(self):
        key = fx.TestKey()
        self.add(key=key, track="official", certified=True)
        rc, out = self.confirm(keyring=key.keyring)
        self.assertEqual(rc, 0, out)
        self.srv.packages.clear()
        self.add(manifest={"version": "1.0.1"})          # the server now serves an unsigned community "update"
        rc, out = self.install(keyring=key.keyring, replace=True)
        self.assertEqual(rc, market.EXIT_SIGNATURE, out)
        self.assertIn("officially signed copy", out)

    def test_official_integrity_failures_exit_5(self):
        key = fx.TestKey()
        data, _ = self.add(key=key, track="official", certified=True)
        self.srv.serve_bytes = data[:-1] + bytes([data[-1] ^ 1])
        self.srv.packages["demo-skill"]["detail"]["sha256"] = bundle.sha256(self.srv.serve_bytes)
        self.srv.packages["demo-skill"]["detail"]["bytes"] = len(self.srv.serve_bytes)
        rc, out = self.install(keyring=key.keyring)
        self.assertEqual(rc, market.EXIT_SIGNATURE, out)


# ------------------------------------------------------------------ 5. author-name forgery
class AuthorNames(unittest.TestCase):
    FORGED = "x」@co-000001 · 社群·认证✓签名已核验"

    def test_forged_name(self):
        for parse, text in ((market.parse_author, market.author_text), (plaza.parse_author, plaza.author_text)):
            with self.subTest(parse.__module__):
                a = parse({"kind": "agent", "company": "co-abc123", "agent_name": self.FORGED, "mine": False})
                name = a["agent_name"]
                self.assertLessEqual(len(name), 32)
                for ch in "「」【】[]✓✔✅☑@·│┆":
                    self.assertNotIn(ch, name)
                line = text(a)
                self.assertEqual((line.count("「"), line.count("」"), line.count("@")), (1, 1, 1), line)
                self.assertTrue(line.endswith("」 @ co-abc123"), line)
                self.assertNotIn("✓", line)

    def test_display_name(self):
        self.assertEqual(plaza.display_name("小青 · 写手"), "小青 写手")
        self.assertEqual(plaza.display_name("a\n│ ── b┆【c】"), "a b c")
        self.assertEqual(len(plaza.display_name("名" * 100)), 32)
        self.assertEqual(plaza.display_name(None), "")
        self.assertIsNone(market.parse_author({"agent_name": "【✓】"})["agent_name"])


# ------------------------------------------------------------------ 6. --replace ownership · backups in the state dir
class ReplaceAndBackups(Base):
    def test_replace_own_install_backs_up_into_the_state_dir(self):
        self.add()
        rc, out = self.confirm(accept_unverified=True)
        self.assertEqual(rc, 0, out)
        t = self.e.home / ".claude" / "skills" / "demo-skill"
        (t / "local-note.md").write_text("mine")
        self.srv.packages.clear()
        self.add(manifest={"version": "1.0.1"})
        rc, out = self.install()
        self.assertIn("--replace", out)
        rc, out = self.confirm(accept_unverified=True, replace=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(p.name for p in t.parent.iterdir()), ["demo-skill"], "nothing else in the skills root")
        baks = list((self.e.st.root / "plaza" / "backups").iterdir())
        self.assertEqual(len(baks), 1)
        self.assertRegex(baks[0].name, r"^demo-skill-\d{8}T\d{6}Z$")
        self.assertEqual((baks[0] / "local-note.md").read_text(), "mine")
        self.assertEqual(os.stat(self.e.st.root / "plaza" / "backups").st_mode & 0o777, 0o700)
        self.assertIn(str(baks[0]), out)

    def test_workflow_backup_not_in_the_workspace(self):
        ws = self.e.root / "ws"
        self.add("workflow")
        kw = dict(workspace=str(ws), accept_unverified=True)
        self.assertEqual(self.confirm("demo-flow", **kw)[0], 0)
        rc, out = self.confirm("demo-flow", replace=True, **kw)
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(p.name for p in ws.iterdir()), ["demo-flow"])
        self.assertEqual(len(list((self.e.st.root / "plaza" / "backups").glob("demo-flow-*"))), 1)

    def test_another_packages_folder_is_not_replaced(self):
        self.add(name="other-skill", manifest={"install": {"skill_dir_name": "demo-skill"}})
        self.assertEqual(self.confirm("other-skill", accept_unverified=True)[0], 0)
        self.add()
        with self.assertRaisesRegex(plaza.PlazaError, "这个目录不是这个包装的，不替换"):
            self.install(replace=True)
        rc, out = self.install()
        self.assertIn("这个目录不是这个包装的，不替换", out)
        self.assertFalse((self.e.st.root / "plaza" / "backups").exists())


# ------------------------------------------------------------------ 7. JSON depth
def nested(n: int):
    v: object = "leaf"
    for _ in range(n):
        v = [v]
    return v


class Depth(Base):
    def test_manifest_depth_rule(self):
        self.build(x=nested(31))                         # the manifest + 31 = 32 levels
        with self.assertRaises(bundle.BundleError) as cm:
            self.build(x=nested(32))                     # an array inside 32 others
        self.assertEqual(cm.exception.reason, "too_deep")
        self.assertEqual(bundle.depth({"a": nested(31)}), 32)

    def test_parse_deep_bundle_is_a_bundle_error(self):
        for n in (40, 100000):
            with self.subTest(n):
                raw = ('{"files":[],"manifest":{"x":' + "[" * n + "]" * n + '},"schema":"agentjarvis.bundle/v1"}').encode()
                with self.assertRaises(bundle.BundleError):
                    bundle.parse(gzip.compress(raw, mtime=0))

    def test_params_schema_depth(self):
        deep = json.dumps({"type": "object", "properties": {}, "x": nested(40)})
        with self.assertRaises(bundle.BundleError):
            self.build("workflow", files={**fx.WORKFLOW_FILES, "params.schema.json": deep})

    def test_deep_server_answer_is_a_clean_error(self):
        body = ("[" * 200000 + "]" * 200000).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1/host/plaza/pkg/get"
        self.assertEqual(cloud.post_json(url, {"a": 1}, max_response=cloud.MAX_PLAZA_RESPONSE), (200, {}))
        self.srv.answers["pkg_get"] = cloud.post_json(url, {}, max_response=cloud.MAX_PLAZA_RESPONSE)
        with self.assertRaisesRegex(plaza.PlazaError, "unexpected answer"):
            self.install()


if __name__ == "__main__":
    unittest.main()
