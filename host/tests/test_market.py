"""`jarvis plaza install | publish | like | installed` + package search / show / report / mine (market.py). Offline: the control
plane, the download / upload URLs and Jev are fakes; HOME, the workspace and the state dir are temp dirs."""
import contextlib
import io
import json
import os
import pathlib
import stat
import sys
import tomllib
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import market_fixtures as fx  # noqa: E402
from jarvis_host import bundle, cli, cloud, market, plaza, wire  # noqa: E402

OR_KEY = "sk-or-v1-" + "e" * 64


class FakeJev:
    def __init__(self, p=0.1, status=200):
        self.p, self.status, self.seen = p, status, []

    def __call__(self, url, headers, body, timeout):
        self.seen.append(json.loads(body))
        return self.status, json.dumps({"answers": {"exposure": {"noul": self.p}, "kind": {"choice": "organisation_identity"}}}).encode()


def digest_of(text: str) -> str:
    return text.split("digest: ")[1].split()[0]


class Base(unittest.TestCase):
    def setUp(self):
        self.e = fx.Env()
        self.addCleanup(self.e.close)
        self.srv = fx.PkgServer()
        self.work = self.e.root / "src"
        self.work.mkdir()
        self.n = __import__("itertools").count()

    def add_skill(self, name="demo-skill", signature=None, key=None, files=None, **kw):
        data, m = bundle.build(fx.make_skill(self.work / f"s-{name}-{next(self.n)}", name=name, files=files, **kw.pop("manifest", {})))
        if key:
            signature = key.sign_package(data, m)
        self.srv.add(data, signature=signature, **kw)
        return data, m

    def add_workflow(self, name="demo-flow", files=None, **kw):
        data, m = bundle.build(fx.make_workflow(self.work / f"w-{name}-{next(self.n)}", name=name, files=files,
                                                **kw.pop("manifest", {})))
        self.srv.add(data, **kw)
        return data, m

    def install(self, name="demo-skill", **kw):
        out = io.StringIO()
        kw.setdefault("harness", "claude_code")
        rc = market.run_install(name, st=self.e.st, post=self.srv, get=self.srv.get, out=out, **kw)
        return rc, out.getvalue()

    def preview_and_confirm(self, name="demo-skill", **kw):
        rc, out = self.install(name, **kw)
        self.assertEqual(rc, 0, out)
        return self.install(name, owner_confirmed=True, digest=digest_of(out), **kw)


class InstallSkill(Base):
    def test_preview_writes_nothing_and_shows_everything(self):
        self.add_skill()
        rc, out = self.install()
        self.assertEqual(rc, market.EXIT_OK)
        self.assertEqual(self.e.tree(), [], "nothing in HOME")
        self.assertIn(market.UNVERIFIED, out)
        self.assertNotIn(market.CERT_BADGE, out)
        for s in ("DEMO_TOKEN", "密钥 SECRET", "Example 账号", "https://example.com/signup", "python3 -m pip install",
                  "python3 scripts/demo.py --self-test", str(self.e.home / ".claude/skills/demo-skill"), "NOT INSTALLED",
                  "--accept-unverified --owner-confirmed --digest", "SHOWN ONLY, never run by jarvis"):
            self.assertIn(s, out)
        self.assertRegex(digest_of(out), r"^[0-9a-f]{16}$")
        self.assertEqual([c["inner"]["t"] for c in self.srv.calls], ["plaza_pkg_get"])
        self.assertEqual(self.srv.calls[0]["url"], f"{fx.API}/v1/host/plaza/pkg/get")
        cached = list((self.e.st.root / "plaza" / "cache").iterdir())
        self.assertEqual(len(cached), 1)
        self.assertEqual(stat.S_IMODE(os.stat(cached[0]).st_mode), 0o600)

    def test_confirm_installs_with_modes_record_and_report(self):
        data, m = self.add_skill()
        rc, out = self.install()
        with self.assertRaisesRegex(plaza.PlazaError, "accept-unverified"):
            self.install(owner_confirmed=True, digest=digest_of(out))
        self.assertFalse((self.e.home / ".claude").exists())
        rc, out2 = self.install(owner_confirmed=True, digest=digest_of(out), accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out2)
        t = self.e.home / ".claude" / "skills" / "demo-skill"
        self.assertEqual(sorted(str(p.relative_to(t)) for p in t.rglob("*") if p.is_file()), sorted(fx.SKILL_FILES))
        self.assertEqual(stat.S_IMODE(os.stat(t / "SKILL.md").st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(t / "scripts/demo.py").st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(os.stat(t / "scripts").st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(os.stat(t).st_mode), 0o755)
        self.assertEqual((t / "SKILL.md").read_text(), fx.SKILL_FILES["SKILL.md"], "claude_code: unchanged")
        self.assertIn("install.verify: OK", out2)
        rec = market.read_installed(self.e.st)
        self.assertEqual([(r["name"], r["version"], r["harness"], r["certified"], r["sha256"]) for r in rec],
                         [("demo-skill", "1.0.0", "claude_code", False, bundle.sha256(data))])
        self.assertEqual(stat.S_IMODE(os.stat(self.e.st.root / "plaza" / "installed.json").st_mode), 0o600)
        inst = [c for c in self.srv.calls if c["route"] == "installed"]
        self.assertEqual(len(inst), 1)
        self.assertEqual(set(inst[0]["inner"]), {"v", "t", "channel", "ts", "nonce", "name", "version"})
        self.assertEqual(inst[0]["inner"]["t"], "plaza_pkg_installed")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        e = inst[0]["env"]
        Ed25519PublicKey.from_public_bytes(wire.unb64u(e["pk"])).verify(
            wire.unb64u(e["sig"]), f"agentjarvis-host-plaza-pkg-installed-v1\n{e['body']}".encode())
        self.assertIn("DEMO_TOKEN", out2)
        self.assertIn(str(self.e.home / "agent-workspace" / ".env"), out2)
        self.assertFalse(list(t.parent.glob(".*staging*")), "no staging left")
        o = io.StringIO()
        market.run_installed(st=self.e.st, out=o)
        self.assertIn("demo-skill 1.0.0", o.getvalue())

    def test_report_failure_is_only_a_warning(self):
        self.add_skill()
        self.srv.answers["pkg_installed"] = (500, {})
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK)
        self.assertIn("warning", out)

    def test_digest_binds_harness_target_options(self):
        self.add_skill()
        _, out = self.install()
        d = digest_of(out)
        for kw in ({"harness": "codex"}, {"replace": True}):
            with self.subTest(kw):
                rc, o = self.install(owner_confirmed=True, digest=d, accept_unverified=True, **kw)
                self.assertEqual(rc, market.EXIT_DIGEST, o)
        self.assertEqual(self.e.tree(), [])
        with self.assertRaises(plaza.PlazaError):
            self.install(owner_confirmed=True)
        # a new bundle under the same name (the server changed it) → a new digest
        self.srv.packages.clear()
        self.add_skill(manifest={"summary_en": "changed"})
        rc, _ = self.install(owner_confirmed=True, digest=d, accept_unverified=True)
        self.assertEqual(rc, market.EXIT_DIGEST)

    def test_existing_target_refused_unless_replace(self):
        self.add_skill()
        t = self.e.home / ".claude" / "skills" / "demo-skill"
        t.mkdir(parents=True)
        (t / "mine.md").write_text("the user's own file")
        rc, out = self.install()
        self.assertIn("EXISTS", out)
        with self.assertRaises(plaza.PlazaError):
            self.install(owner_confirmed=True, digest=digest_of(out), accept_unverified=True)
        self.assertEqual((t / "mine.md").read_text(), "the user's own file")
        # the folder is not jarvis's install of this package: --replace refuses too (preview and confirm)
        with self.assertRaisesRegex(plaza.PlazaError, "不是这个包装的"):
            self.install(replace=True)
        with self.assertRaisesRegex(plaza.PlazaError, "不是这个包装的"):
            self.install(replace=True, owner_confirmed=True, digest=digest_of(out), accept_unverified=True)
        self.assertEqual((t / "mine.md").read_text(), "the user's own file")
        self.assertEqual(sorted(p.name for p in t.parent.iterdir()), ["demo-skill"])

    def test_codex_and_opencode_targets(self):
        self.add_skill()
        rc, _ = self.preview_and_confirm(harness="codex", accept_unverified=True)
        self.assertEqual(rc, 0)
        t = self.e.home / ".agents" / "skills" / "demo-skill"
        self.assertEqual((t / "SKILL.md").read_text(), "---\nname: demo-skill\ndescription: demo\n---\n\nRead AGENTS.md and .agents/skills/x first.\n")
        self.assertEqual((t / "scripts/demo.py").read_text(), fx.SKILL_FILES["scripts/demo.py"], "only .md files are converted")
        rc, _ = self.preview_and_confirm(harness="opencode", accept_unverified=True)
        self.assertEqual(rc, 0)
        self.assertTrue((self.e.home / ".config" / "opencode" / "skills" / "demo-skill" / "SKILL.md").exists())
        self.assertEqual(len(market.read_installed(self.e.st)), 2)

    def test_harness_not_supported_by_package(self):
        self.add_skill(manifest={"requires": {"harness": ["codex"]}})
        with self.assertRaisesRegex(plaza.PlazaError, "supports only"):
            self.install(harness="claude_code")

    def test_verify_failure_installs_nothing(self):
        files = {**fx.SKILL_FILES, "scripts/demo.py": "import sys\nprint('broken: ' + str(sys.argv))\nsys.exit(3)\n"}
        self.add_skill(files=files)
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_ERROR)
        self.assertIn("exit 3", out)
        self.assertIn(plaza.TEXT + "broken", out, "the verify output is printed as data")
        self.assertFalse((self.e.home / ".claude").exists())
        self.assertEqual(market.read_installed(self.e.st), [])

    def test_verify_runs_with_an_empty_environment_offline(self):
        probe = ("import os, sys, socket, pathlib\n"
                 "env = dict(os.environ)\n"
                 "assert 'OPENROUTER_API_KEY' not in env and 'AGENTJARVIS_STATE_DIR' not in env, env\n"
                 "assert set(env) <= {'PATH','HOME','TMPDIR','LANG','LC_ALL','PYTHONDONTWRITEBYTECODE','PYTHONNOUSERSITE','LC_CTYPE','PWD'}, sorted(env)\n"
                 "assert env['HOME'] != os.path.expanduser('~root') and 'jarvis-verify-' in env['HOME'], env['HOME']\n"
                 "assert pathlib.Path('SKILL.md').exists()\n"
                 "pathlib.Path('junk-from-verify.txt').write_text('x')\n"
                 "print('self-test OK')\n")
        self.add_skill(files={**fx.SKILL_FILES, "scripts/demo.py": probe})
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": OR_KEY, "AGENTJARVIS_STATE_DIR": str(self.e.st.root)}):
            rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        t = self.e.home / ".claude" / "skills" / "demo-skill"
        self.assertFalse((t / "junk-from-verify.txt").exists(), "verify ran in a throw-away copy")
        self.assertFalse(list(t.rglob("__pycache__")))

    def test_verify_timeout(self):
        self.add_skill(files={**fx.SKILL_FILES, "scripts/demo.py": "import time\ntime.sleep(30)\n"})
        with mock.patch.object(market, "VERIFY_TIMEOUT", 1):
            rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_ERROR)
        self.assertIn("timeout", out)

    def test_download_url_and_bytes_checked(self):
        self.add_skill()
        for url in ("https://evil.example/v1/plaza/dl/" + fx.TOKEN, "http://127.0.0.1:10/v1/plaza/dl/" + fx.TOKEN,
                    f"{fx.API}/v1/plaza/up/{fx.TOKEN}", f"{fx.API}/v1/plaza/dl/{fx.TOKEN}?x=1", f"{fx.API}/v1/plaza/dl/../../x",
                    "file:///etc/passwd", None):
            with self.subTest(url):
                self.srv.download_url = url
                with self.assertRaisesRegex(plaza.PlazaError, "download"):
                    self.install()
        self.srv.download_url = f"{fx.API}/v1/plaza/dl/{fx.TOKEN}"
        data, _ = self.add_skill(manifest={"summary_en": "other"})
        self.srv.serve_bytes = data[:-1] + b"\x00"
        self.srv.packages["demo-skill"]["detail"]["sha256"] = bundle.sha256(b"something else")
        with self.assertRaisesRegex(plaza.PlazaError, "SHA-256"):
            self.install()
        self.assertEqual(self.e.tree(), [])

    def test_https_api_requires_https_transfer(self):
        cloud.write_cloud(self.e.st, {"api": "https://api.agentjarvis.net", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "A"},
                                      "linked_at": 1, "last_seq": 0})
        ok = "https://api.agentjarvis.net/v1/plaza/dl/" + fx.TOKEN
        self.assertEqual(cloud.transfer_url(self.e.st, ok, "dl"), ok)
        for bad in ("http://api.agentjarvis.net/v1/plaza/dl/" + fx.TOKEN, "https://api.agentjarvis.net:8443/v1/plaza/dl/" + fx.TOKEN,
                    "https://user@api.agentjarvis.net/v1/plaza/dl/" + fx.TOKEN, "https://api.agentjarvis.net.evil.example/v1/plaza/dl/" + fx.TOKEN,
                    "https://alpha-app.agentjarvis.net/v1/plaza/dl/" + fx.TOKEN):
            with self.subTest(bad), self.assertRaises(cloud.CloudError):
                cloud.transfer_url(self.e.st, bad, "dl")
        got = []
        status, raw = cloud.plaza_download(self.e.st, ok, get=lambda u, max_bytes, timeout: got.append((u, max_bytes)) or (200, b"x"))
        self.assertEqual(got, [(ok, cloud.MAX_BUNDLE)])
        with self.assertRaises(cloud.CloudError):
            cloud.plaza_download(self.e.st, ok, get=lambda u, max_bytes, timeout: (200, b"x" * (cloud.MAX_BUNDLE + 1)))

    def test_server_errors_are_one_line(self):
        with self.assertRaisesRegex(plaza.PlazaError, "no such package"):
            self.install("missing-pkg")
        self.srv.answers["pkg_get"] = (403, {"error": "plaza_requires_seat"})
        with self.assertRaisesRegex(plaza.PlazaError, "付费席位"):
            self.install()
        with self.assertRaises(plaza.PlazaError):
            self.install("Bad/Name")
        cloud.delete_cloud(self.e.st)
        with self.assertRaises(plaza.PlazaError):
            self.install()


class Signature(Base):
    def test_certified_with_a_valid_signature(self):
        key = fx.TestKey()
        self.add_skill(key=key, track="official", certified=True)
        rc, out = self.install(keyring=key.keyring)
        self.assertEqual(rc, 0, out)
        self.assertIn(market.CERT_BADGE, out)
        self.assertNotIn("--accept-unverified", out)
        rc, out = self.install(keyring=key.keyring, owner_confirmed=True, digest=digest_of(out))
        self.assertEqual(rc, 0, out)
        self.assertTrue(market.read_installed(self.e.st)[0]["certified"])

    def test_server_flag_without_valid_signature_is_refused(self):
        key, other = fx.TestKey(), fx.TestKey()
        cases = {
            "missing": dict(signature=None),
            "untrusted key": dict(key=other),
            "garbage": dict(signature="untrusted comment: x\nAAAA\ntrusted comment: y\nBBBB\n"),
        }
        for why, kw in cases.items():
            for flags in ({"track": "official", "certified": True}, {"track": "community", "certified": True}):
                with self.subTest(why=why, **flags):
                    self.srv.packages.clear()
                    self.add_skill(**kw, **flags)
                    rc, out = self.install(keyring=key.keyring)
                    self.assertEqual(rc, market.EXIT_SIGNATURE, out)
                    self.assertNotIn(market.CERT_BADGE, out)
        self.assertEqual(self.e.tree(), [])

    def test_signature_for_another_package(self):
        key = fx.TestKey()
        data, m = self.add_skill(track="official", certified=True)
        sig_other = key.sign(data, market.minisign.trusted_comment("other-skill", m["version"], m["type"], bundle.sha256(data)))
        self.srv.packages["demo-skill"]["detail"]["signature"] = sig_other
        rc, out = self.install(keyring=key.keyring)
        self.assertEqual(rc, market.EXIT_SIGNATURE)
        old_data, old_m = data, m
        self.srv.packages.clear()
        data, m = self.add_skill(track="official", certified=True, manifest={"version": "1.0.1"})
        self.srv.packages["demo-skill"]["detail"]["signature"] = key.sign_package(old_data, old_m)
        rc, out = self.install(keyring=key.keyring)
        self.assertEqual(rc, market.EXIT_SIGNATURE, "a valid signature of an older version does not certify this one")

    def test_community_with_invalid_signature_is_refused(self):
        key = fx.TestKey()
        self.add_skill(signature=fx.TestKey().sign(b"x", "y"))
        rc, _ = self.install(keyring=key.keyring)
        self.assertEqual(rc, market.EXIT_SIGNATURE)

    def test_real_plaza_key_is_the_default(self):
        key = fx.TestKey()
        self.add_skill(key=key, track="official", certified=True)
        rc, _ = self.install()
        self.assertEqual(rc, market.EXIT_SIGNATURE, "a test key is not in TRUSTED_KEYS")


class InstallWorkflow(Base):
    def test_render_params_escaping_and_signing(self):
        self.add_workflow()
        brand = 'Acme "Q" & <b>\n第二行 \\ end'
        kw = dict(params=[f"brand={brand}", "platforms=douyin,youtube", "daily_cap=5", "auto_send=true", "price=9.5"],
                  workspace=str(self.e.root / "ws"), accept_unverified=True)
        rc, out = self.install("demo-flow", **kw)
        self.assertEqual(rc, 0, out)
        self.assertIn("verified: []", out)
        self.assertIn("demo-skill", out)
        rc, out2 = self.install("demo-flow", owner_confirmed=True, digest=digest_of(out), **kw)
        self.assertEqual(rc, 0, out2)
        t = self.e.root / "ws" / "demo-flow"
        self.assertTrue((t / "CLAUDE.md").exists() and not (t / "scaffold").exists() and not (t / "manifest.json").exists())
        cfg = json.loads((t / "documentation/configuration.json").read_text())
        self.assertEqual(cfg["brand"], brand, "the value lands JSON-escaped and the JSON parses")
        self.assertEqual(cfg["platforms"], "douyin, youtube")
        self.assertEqual(cfg["chairman"], "human:owner")
        self.assertEqual(tomllib.loads((t / "config.toml").read_text())["brand"], brand)
        self.assertIn("Acme &quot;Q&quot; &amp; &lt;b&gt;", (t / "site/index.html").read_text())
        con = (t / "documentation/CONSTITUTION.md").read_text()
        self.assertIn("\nverified: []\n", con)
        self.assertIn("平台 douyin, youtube；自动发送 true；上限 5；价格 9.5", con)
        self.assertRegex(con, r"stale_after: \d{4}-\d{2}-\d{2}")
        self.assertNotIn("{{", "".join(p.read_text() for p in t.rglob("*") if p.is_file()))
        self.assertFalse((t / ".codex").exists(), "claude_code: no Codex role files")
        self.assertTrue((t / ".handoff").exists() and (t / "reports/.gitkeep").exists())
        self.assertEqual(stat.S_IMODE(os.stat(t / "tools/check.py").st_mode), 0o755)
        self.assertIn("jarvis plaza install demo-skill", out2)
        self.assertIn("jarvis plaza install secret-scan", out2)
        self.assertIn("python3 tools/check.py", out2)

    def test_sign_as_and_codex_roles(self):
        self.add_workflow()
        kw = dict(harness="codex", sign_as="张三", workspace=str(self.e.root / "ws"), accept_unverified=True)
        rc, out = self.install("demo-flow", **kw)
        self.assertIn("张三", out)
        self.assertIn("documentation/CONSTITUTION.md", out)
        rc, out2 = self.install("demo-flow", owner_confirmed=True, digest=digest_of(out), **kw)
        self.assertEqual(rc, 0, out2)
        t = self.e.root / "ws" / "demo-flow"
        self.assertRegex((t / "documentation/CONSTITUTION.md").read_text(),
                         r"verified: \[\{ by: human:张三, at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \}\]")
        self.assertRegex((t / "documentation/ROLES.md").read_text(), r"verified: \[\{ by: human:张三, at: \d{4}-\d{2}-\d{2} \}\]")
        role = tomllib.loads((t / ".codex/agents/writer.toml").read_text())
        self.assertEqual(role["name"], "writer")
        self.assertEqual(role["description"], '青禾家居 的写手 "quoted" \\ back')
        self.assertEqual(role["developer_instructions"], "你是 青禾家居 的写手。")
        self.assertIn("harness=codex", (t / "CLAUDE.md").read_text())
        _, out3 = self.install("demo-flow", **{**kw, "sign_as": "李四", "replace": True})
        self.assertNotEqual(digest_of(out3), digest_of(out), "the signature is part of what the human confirms")

    def test_param_validation(self):
        self.add_workflow()
        bad = [["nope=1"], ["tone=loud"], ["daily_cap=x"], ["daily_cap=-1"], ["auto_send=yes"], ["platforms=tiktok"],
               ["platforms=douyin,douyin"], ["slug=Not Ok"], ["contact=not-an-email"], ["install_date=2020-01-01"],
               ["harness=codex"], ["brand="], ["brand=a{{x}}"], ["brand=bidi‮"], ["brand=x", "brand=y"], ["novalue"]]
        for p in bad:
            with self.subTest(p), self.assertRaises(plaza.PlazaError):
                self.install("demo-flow", params=p)
        pf = self.e.root / "p.json"
        pf.write_text(json.dumps({"brand": "文件里的品牌", "platforms": ["youtube"], "daily_cap": 2}, ensure_ascii=False))
        rc, out = self.install("demo-flow", params_file=str(pf))
        self.assertEqual(rc, 0, out)
        self.assertIn("文件里的品牌", out)
        pf.write_text(json.dumps({"daily_cap": "2"}))
        with self.assertRaises(plaza.PlazaError):
            self.install("demo-flow", params_file=str(pf))
        with self.assertRaises(plaza.PlazaError):
            market.resolve_params({"required": ["must"], "properties": {"must": {"type": "string"}}}, [], None, "codex",
                                  __import__("datetime").datetime.now())

    def test_skill_takes_no_params(self):
        self.add_skill()
        with self.assertRaisesRegex(plaza.PlazaError, "no --param"):
            self.install(params=["a=b"])

    def test_render_aborts(self):
        cases = {
            "unknown placeholder": {"scaffold/x.md": "{{not_a_param}}"},
            "json breaks": {"scaffold/bad.json": '{"a": {{daily_cap}}, }'},
        }
        for why, extra in cases.items():
            with self.subTest(why):
                self.srv.packages.clear()
                self.add_workflow(files={**fx.WORKFLOW_FILES, **extra})
                with self.assertRaisesRegex(plaza.PlazaError, "aborted"):
                    self.install("demo-flow")
        self.assertEqual(self.e.tree(), [])

    def test_residual_placeholder_aborts(self):
        values = {"a": "x"}
        with self.assertRaisesRegex(plaza.PlazaError, "aborted"):
            market.render_file("x.md", "{{a}} {{b}}", values, None, "2026-10-03")
        self.assertEqual(market.render_file("x.md", "{{a}} {{ b }} {{B}}", values, None, "2026-10-03"), "x {{ b }} {{B}}",
                         "only the placeholder grammar is a placeholder")


class Publish(Base):
    def setUp(self):
        super().setUp()
        self.pkg = fx.make_skill(self.e.root / "mine", name="my-skill")

    def pub(self, **kw):
        out = io.StringIO()
        kw.setdefault("env", {"OPENROUTER_API_KEY": OR_KEY})
        kw.setdefault("transport", FakeJev())
        rc = market.run_publish(str(self.pkg), st=self.e.st, post=self.srv, put=self.srv.put, identity=("devbox", "alice"), out=out, **kw)
        text = out.getvalue()
        self.assertNotIn(OR_KEY, text)
        return rc, text

    def test_preview_then_publish_exact_bytes(self):
        jev = FakeJev()
        rc, out = self.pub(transport=jev, show_name=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.srv.calls, [], "nothing sent at preview")
        for p in fx.SKILL_FILES:
            self.assertIn(p, out)
        self.assertIn("贾维斯一号", out)
        self.assertIn("layer 2 saw", out)
        sent = jev.seen[0]["state"]["plaza_draft"]
        self.assertEqual(set(sent), {"manifest", "README.md", "SKILL.md"})
        self.assertLessEqual(len(json.dumps(sent, ensure_ascii=False)), market.DRAFT_MAX + 200)
        rc, out2 = self.pub(show_name=True, owner_confirmed=True, digest=digest_of(out))
        self.assertEqual(rc, 0, out2)
        call = self.srv.calls[0]
        self.assertEqual(call["url"], f"{fx.API}/v1/host/plaza/pkg/publish")
        self.assertEqual(set(call["inner"]), {"v", "t", "channel", "ts", "nonce", "name", "type", "version", "sha256", "bytes", "show_name"})
        url, body = self.srv.puts[0]
        self.assertEqual(url, self.srv.upload_url)
        self.assertEqual(bundle.sha256(body), call["inner"]["sha256"])
        self.assertEqual(len(body), call["inner"]["bytes"])
        m, _ = bundle.parse(body)
        self.assertIs(m["certified"], False)
        self.assertEqual(m["author"], "community")
        self.assertIn("published", out2)

    def test_layer1_hit_refuses_and_never_rewrites(self):
        secret_file = self.pkg / "scripts" / "conf.py"
        secret_file.write_text(f"KEY = '{OR_KEY}'\nOWNER = 'bob@gm' 'ail.com'\nHOME = '/ho' 'me/alice/x'\n")
        before = secret_file.read_bytes()
        rc, out = self.pub()
        self.assertEqual(rc, market.EXIT_BLOCKED)
        self.assertIn("scripts/conf.py: ", out)
        self.assertRegex(out, r"scripts/conf\.py: \w+ ×\d")
        self.assertEqual(secret_file.read_bytes(), before)
        self.assertEqual(self.srv.calls, [])
        m = json.loads((self.pkg / "manifest.json").read_text())
        secret_file.unlink()
        m["summary_en"] = "mail me at bob@gm" + "ail.com"
        (self.pkg / "manifest.json").write_text(json.dumps(m))
        rc, out = self.pub()
        self.assertEqual(rc, market.EXIT_BLOCKED)
        self.assertIn("manifest.summary_en: email ×1", out)

    def test_layer2_flagged_unavailable_and_digest(self):
        rc, out = self.pub(transport=FakeJev(0.9))
        self.assertEqual(rc, market.EXIT_BLOCKED)
        rc, out = self.pub(env={})
        self.assertEqual(rc, market.EXIT_UNAVAILABLE)
        d = digest_of(out)
        rc, _ = self.pub(owner_confirmed=True, digest=d, show_name=True)
        self.assertEqual(rc, market.EXIT_DIGEST)
        (self.pkg / "README.md").write_text("changed after the human looked")
        rc, _ = self.pub(owner_confirmed=True, digest=d)
        self.assertEqual(rc, market.EXIT_DIGEST)
        self.assertEqual(self.srv.calls, [])
        with self.assertRaises(plaza.PlazaError):
            self.pub(owner_confirmed=True)

    def test_upload_url_and_server_scan(self):
        _, out = self.pub()
        d = digest_of(out)
        self.srv.upload_url = "https://evil.example/v1/plaza/up/" + fx.TOKEN
        with self.assertRaisesRegex(plaza.PlazaError, "not uploaded"):
            self.pub(owner_confirmed=True, digest=d)
        self.assertEqual(self.srv.puts, [])
        self.srv.upload_url = f"{fx.API}/v1/plaza/up/{fx.TOKEN}"
        self.srv.put_answer = (422, {"error": "secret_found", "path": "scripts/demo.py", "kind": "aws_key"})
        with self.assertRaisesRegex(plaza.PlazaError, "scripts/demo.py: aws_key"):
            self.pub(owner_confirmed=True, digest=d)
        self.srv.answers["pkg_publish"] = (409, {"error": "name_taken"})
        with self.assertRaisesRegex(plaza.PlazaError, "name"):
            self.pub(owner_confirmed=True, digest=d)

    def test_junk_refused(self):
        (self.pkg / "__pycache__").mkdir()
        with self.assertRaisesRegex(plaza.PlazaError, "__pycache__"):
            self.pub()
        self.assertEqual(self.srv.calls, [])


class ReadsAndSmallWrites(Base):
    def test_search_all_packages_then_qa(self):
        self.add_skill()
        out = io.StringIO()
        market.run_search(["邮件", "gmail"], st=self.e.st, post=self.srv, out=out)
        self.assertEqual([(c["pkg"], c["inner"]["t"]) for c in self.srv.calls], [(True, "plaza_pkg_search"), (False, "plaza_search")])
        self.assertEqual(self.srv.calls[0]["inner"]["q"], "邮件 gmail")
        self.assertNotIn("type", self.srv.calls[0]["inner"])
        o = out.getvalue()
        self.assertEqual(o.count(plaza.FENCE_OPEN), 2)
        self.assertLess(o.index("demo-skill"), o.index("广场问答"))
        self.srv.calls.clear()
        market.run_search([], type_="workflow", sort="likes", tag="email", track="official", limit=99, st=self.e.st, post=self.srv, out=io.StringIO())
        self.assertEqual(len(self.srv.calls), 1)
        inner = self.srv.calls[0]["inner"]
        self.assertEqual((inner["type"], inner["sort"], inner["tag"], inner["track"], inner["limit"]), ("workflow", "likes", "email", "official", 50))
        self.srv.calls.clear()
        market.run_search(["x"], type_="qa", st=self.e.st, post=self.srv, out=io.StringIO())
        self.assertEqual([c["pkg"] for c in self.srv.calls], [False])
        out = io.StringIO()
        market.run_search(["x"], as_json=True, st=self.e.st, post=self.srv, out=out)
        j = json.loads(out.getvalue())
        self.assertEqual(j["packages"][0]["name"], "demo-skill")
        self.assertIn("items", j)

    def test_show_package_or_post(self):
        self.add_skill()
        out = io.StringIO()
        market.run_show("demo-skill", st=self.e.st, post=self.srv, out=out)
        self.assertIn("中文说明", out.getvalue())
        self.assertIn("jarvis plaza install demo-skill", out.getvalue())
        self.assertEqual(self.srv.calls[-1]["inner"], {**self.srv.calls[-1]["inner"], "name": "demo-skill"})
        self.assertNotIn("version", self.srv.calls[-1]["inner"])
        self.srv.answers["qa_get"] = (404, {"error": "not_found"})
        with self.assertRaises(plaza.PlazaError):
            market.run_show("pz_" + "A" * 22, st=self.e.st, post=self.srv, out=io.StringIO())
        self.assertFalse(self.srv.calls[-1]["pkg"])
        market.run_show("demo-skill", version="1.0.0", as_json=True, st=self.e.st, post=self.srv, out=io.StringIO())
        self.assertEqual(self.srv.calls[-1]["inner"]["version"], "1.0.0")
        with self.assertRaises(plaza.PlazaError):
            market.run_show("../etc", st=self.e.st, post=self.srv, out=io.StringIO())

    def test_like_report_mine(self):
        self.add_skill()
        out = io.StringIO()
        market.run_like("demo-skill", True, st=self.e.st, post=self.srv, out=out)
        market.run_like("demo-skill", False, st=self.e.st, post=self.srv, out=out)
        market.run_report("demo-skill", "malware", st=self.e.st, post=self.srv, out=out)
        with self.assertRaisesRegex(plaza.PlazaError, "packages"):
            market.run_report("demo-skill", "abuse", st=self.e.st, post=self.srv, out=out)
        with self.assertRaisesRegex(plaza.PlazaError, "posts"):
            market.run_report("pz_" + "A" * 22, "malware", st=self.e.st, post=self.srv, out=out)
        market.run_mine(st=self.e.st, post=self.srv, out=out)
        kinds = [c["inner"]["t"] for c in self.srv.calls]
        self.assertEqual(kinds, ["plaza_pkg_like", "plaza_pkg_like", "plaza_pkg_report", "plaza_mine", "plaza_pkg_mine"])
        self.assertEqual(set(self.srv.calls[0]["inner"]), {"v", "t", "channel", "ts", "nonce", "name", "on"})
        self.assertEqual([self.srv.calls[0]["inner"]["on"], self.srv.calls[1]["inner"]["on"]], [True, False])
        self.assertEqual(set(self.srv.calls[2]["inner"]), {"v", "t", "channel", "ts", "nonce", "name", "reason"})
        o = out.getvalue()
        self.assertIn("liked", o)
        self.assertIn("unliked", o)
        self.assertIn("本公司发布的包", o)

    def test_cli_wiring(self):
        with mock.patch.dict(os.environ, {"AGENTJARVIS_STATE_DIR": str(self.e.st.root)}), contextlib.redirect_stderr(io.StringIO()):
            for argv, code in ((["plaza", "install", "demo-skill", "--owner-confirmed"], 1),
                               (["plaza", "report", "demo-skill", "--reason", "because"], 2),
                               (["plaza", "search", "--type", "everything"], 2),
                               (["plaza", "search", "--official", "--community"], 2),
                               (["plaza", "publish", str(self.e.root / "missing"), "--owner-confirmed"], 1)):
                with self.subTest(argv), self.assertRaises(SystemExit) as cm:
                    cli.main(argv)
                self.assertEqual(cm.exception.code, code)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                cli.main(["plaza", "installed", "--json"])
            self.assertEqual(cm.exception.code, 0)
            self.assertEqual(json.loads(out.getvalue()), {"items": []})


@unittest.skipUnless(fx.CATALOG.is_dir(), "the catalog is not on this machine")
class CatalogInstall(Base):
    """A real skill and a real workflow from the catalog into temp HOME / workspace for all three harnesses, with params from
    their schema defaults (x-auto computed)."""

    def test_skill_and_workflow_every_harness(self):
        for d in (fx.CATALOG / "skills" / "srt-to-text", fx.CATALOG / "workflows" / "llm-wiki"):
            data, m = bundle.build(d)
            self.srv.add(data)
            for h in HARN:
                with self.subTest(pkg=m["name"], harness=h):
                    ws = self.e.root / f"ws-{h}"
                    rc, out = self.preview_and_confirm(m["name"], harness=h, workspace=str(ws), accept_unverified=True)
                    self.assertEqual(rc, 0, out[-3000:])
                    self.assertIn("install.verify: OK", out)
                    t = (market.skills_root(h) / m["install"]["skill_dir_name"]) if m["type"] == "skill" else ws / m["name"]
                    self.assertTrue(t.is_dir())
                    if m["type"] == "workflow":
                        text = "".join(p.read_text("utf-8", "replace") for p in t.rglob("*") if p.is_file())
                        self.assertIsNone(market._PH.search(text))
                        for j in t.rglob("*.json"):
                            json.loads(j.read_text())
                        roles = list((t / ".claude" / "agents").glob("*.md"))
                        tomls = list((t / ".codex" / "agents").glob("*.toml")) if (t / ".codex").exists() else []
                        self.assertEqual(len(tomls), len(roles) if h == "codex" else 0)
                        for f in tomls:
                            self.assertTrue(tomllib.loads(f.read_text())["developer_instructions"])
                        self.assertIn("verified: []", (t / "documentation" / "CONSTITUTION.md").read_text())

    def test_every_catalog_workflow_renders_with_defaults(self):
        for d in sorted((fx.CATALOG / "workflows").iterdir()):
            try:
                data, m = bundle.build(d)
            except bundle.BundleError:
                continue          # a known catalog defect (test_bundle lists it)
            m, files = bundle.parse(data)
            schema = json.loads(files["params.schema.json"])
            for h in HARN:
                with self.subTest(pkg=d.name, harness=h):
                    values, auto = market.resolve_params(schema, [], None, h, __import__("datetime").datetime.now().astimezone())
                    out = market.install_files(m, files, h, values, "Alex" if h == "codex" else None, values.get("install_date", "2026-10-03"))
                    self.assertTrue(out)
                    market.parse_verify(m["install"]["verify"])


HARN = ("claude_code", "codex", "opencode")

if __name__ == "__main__":
    unittest.main()
