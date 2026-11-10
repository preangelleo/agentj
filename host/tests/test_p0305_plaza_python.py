"""0.30.5 (ADR-0305-plaza-python): a plaza package declares Python ≥ 3.N and the self-check PATH has none (a clean Mac: only
/usr/bin/python3 3.9.6) → after the owner's card the host prepares one with uv in Agent J's own state folder, the sandbox
mounts it read-only, the self-check passes and the record says verify.python.source = agentj_uv. No uv / uv failing → the
missing thing is called 「Python ≥ 3.N」 with the one step that adds it, never 「安装成功」/「可以用了」. A certified package's
declared requirements are installed as wheels only. Offline: a stand-in uv (one file, shebang + body) writes what the real
one would; stand-in pythons report their version."""
import _hermetic  # noqa: F401,I001
import io
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import market_fixtures as fx  # noqa: E402
import test_market as tm  # noqa: E402
from agentj import market, plaza_deps, plaza_perms, plaza_python  # noqa: E402

TMP = os.environ.get("TEST_TMP") or None
BANNED = ("安装成功", "可以用了", "ready to use")

FAKE_UV = r"""#!/bin/sh
echo "$*" >> "@LOG@"
env | sed 's/=.*//' | sort | tr '\n' ' ' >> "@LOG@.env"; echo >> "@LOG@.env"
env | grep -E '^(UV_NO_CONFIG|HOME|UV_CACHE_DIR|UV_PYTHON_INSTALL_DIR|HTTPS_PROXY|https_proxy)=' >> "@LOG@.env"
head -c 1 >/dev/null
case "@MODE@" in fail) echo "error: download failed for @HOME@/secret-path" >&2; exit 2;; sleep) sleep 30; exit 0;; esac
case "$1 $2" in
"python install")
  d="$4/cpython-3.12.4-fake"; mkdir -p "$d/bin"
  printf '#!/bin/sh\ncase "$*" in *version_info*) echo "3 12 4";; esac\nexit @PYRC@\n' > "$d/bin/python3.12"
  chmod 755 "$d/bin/python3.12"; ln -s python3.12 "$d/bin/python3"
  ln -sfn "$d" "$4/cpython-3.12-fake"         # QA-0305-P1: the real uv also makes the minor-version link
  exit 0;;
"venv --python")
  case "$3" in                                 # as the real uv: a path (or 3.N) goes through the minor-version link;
  /*) py="$UV_PYTHON_INSTALL_DIR/cpython-3.12-fake/bin/python3.12";;     # only the full key, managed-only, names the folder
  *) [ -d "$UV_PYTHON_INSTALL_DIR/$3" ] && [ "$UV_PYTHON_PREFERENCE" = only-managed ] || exit 2
     py="$UV_PYTHON_INSTALL_DIR/$3/bin/python3.12";;
  esac
  case "@MODE@" in minorlink) py="$UV_PYTHON_INSTALL_DIR/cpython-3.12-fake/bin/python3.12";; esac
  mkdir -p "$4/bin"; ln -s "$py" "$4/bin/python"; ln -s python "$4/bin/python3"; ln -s python "$4/bin/python3.12"
  printf 'home = %s\nversion = 3.12.4\n' "$(dirname "$py")" > "$4/pyvenv.cfg"; exit 0;;
"pip install") exit 0;;
esac
exit 3
"""


def stand_in(d: pathlib.Path, name: str, ver: str) -> str:
    p = d / name
    p.write_text(f'#!/bin/sh\ncase "$*" in *version_info*) echo "{ver}";; esac\nexit 0\n')
    p.chmod(0o755)
    return str(p)


class Base(tm.Base):
    def setUp(self):
        super().setUp()
        self.box = pathlib.Path(tempfile.mkdtemp(prefix="aj-p0305-", dir=TMP))
        self.addCleanup(shutil.rmtree, self.box, True)
        (self.box / "sys").mkdir()
        self.log = self.box / "uv.log"
        self.uv("ok")
        p = mock.patch.dict(os.environ, {"AGENTJ_PLAZA_UV": "on"})
        p.start()
        self.addCleanup(p.stop)
        self.system("3 9 6")
        real = market.verify_sandbox_argv

        def argv(kind, scratch, pkg_root, *, hide=(), expose=()):     # the stand-in "system" folder is visible, as /usr/bin is
            return real(kind, scratch, pkg_root, hide=hide, expose=[*expose, str(self.box / "sys")])
        for patch in (mock.patch.object(market, "verify_sandbox_argv", argv),
                      mock.patch.object(market, "_system_pythons", lambda want: list(self.pythons))):
            patch.start()
            self.addCleanup(patch.stop)

    def uv(self, mode, present=True, pyrc=0):
        exe = self.box / "uv"
        exe.write_text(FAKE_UV.replace("@LOG@", str(self.log)).replace("@MODE@", mode).replace("@HOME@", os.path.expanduser("~"))
                       .replace("@PYRC@", str(pyrc)))
        exe.chmod(0o755)
        p = mock.patch.object(plaza_python, "uv_binary", lambda: str(exe) if present else None)
        p.start()
        self.addCleanup(p.stop)

    def system(self, ver):
        self.pythons = [stand_in(self.box / "sys", "python3", ver)]

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def skill(self, **req):
        return self.add_skill(manifest={"requires": {"harness": ["claude_code"], "os": ["linux", "macos"],
                                                     "cli": [{"name": "python3", "version": ">=3.10"}], "skills": [], **req}})

    def rec(self):
        return market.read_installed(self.e.st)[-1]

    def no_success_words(self, out):
        for w in BANNED:
            self.assertNotIn(w, out)


class CleanMac(Base):
    def test_old_system_python_is_prepared_with_uv_and_the_self_check_passes(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.skill()
        rc, prev = self.install(accept_unverified=True)
        self.assertEqual(rc, 0, prev)
        self.assertIn("将由 Agent J 用 uv 为这个包准备 Python ≥ 3.10（需要联网下载，放在 Agent J 自己的目录，不改系统 Python）", prev)
        self.assertIn("Agent J will prepare Python ≥ 3.10 for this package with uv", prev)
        self.assertNotIn("依赖待补", prev)
        self.assertEqual(self.calls(), [], "nothing is downloaded before the owner's yes")
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        v = self.rec()["verify"]
        self.assertEqual(v["state"], "ok", out)
        self.assertEqual((v["python"]["source"], v["python"]["version"]), ("agentj_uv", "3.12.4"))
        env_dir = plaza_python.base(self.e.st) / "envs"
        self.assertTrue(v["python"]["path"].startswith(os.path.realpath(env_dir)), v["python"])
        self.assertNotIn("依赖待补", out)
        self.assertIn(plaza_deps.PASSED[0], out)
        self.assertIn(f"这个技能请用 {v['python']['path']} 运行 python3 脚本（Agent J 准备的 Python 3.12.4）", out)
        self.assertIn(f"Run this skill's python3 scripts with {v['python']['path']}", out)
        calls = self.calls()
        self.assertEqual([c.split()[:2] for c in calls], [["python", "install"], ["venv", "--python"]], calls)
        self.assertIn("--install-dir " + os.path.realpath(plaza_python.base(self.e.st) / "interpreters"), calls[0])
        self.assertEqual(calls[1].split()[2], "cpython-3.12.4-fake", "P1: the venv names the real folder, not a path")
        self.assertEqual(v["python_prep"], {"want": "3.10", "req": None, "req_ok": False, "req_sha": None})
        for d in ("", "interpreters", "cache", "envs"):
            self.assertEqual(stat.S_IMODE(os.stat(plaza_python.base(self.e.st) / d).st_mode), 0o700, d)
        # agentj plaza verify reuses it: no second download
        out2 = io.StringIO()
        self.assertEqual(market.run_reverify("demo-skill", st=self.e.st, out=out2, local=True), market.EXIT_OK, out2.getvalue())
        self.assertEqual(len(self.calls()), 2, self.calls())
        self.assertEqual(self.rec()["verify"]["python"]["source"], "agentj_uv")

    def test_no_uv_is_pending_named_python_with_one_step(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.uv("ok", present=False)
        self.skill()
        rc, prev = self.install(accept_unverified=True)
        self.assertIn("Python ≥ 3.10", prev.split("digest:")[0])
        self.assertNotIn("将由 Agent J 用 uv", prev)
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["missing"]), ("pending", ["Python ≥ 3.10"]))
        self.assertEqual(v["python"], {"want": "3.10", "found": "3.9.6", "prep": "no_uv"})
        self.assertNotIn("python3.10:", out)
        self.assertIn("系统里找到的是 3.9.6", out)
        self.assertIn("the system has 3.9.6", out)
        self.assertIn("用系统包管理器装 python3.10 或更新版本" if sys.platform != "darwin" else "从 python.org 下载 Python 3.10", out)
        self.assertNotIn("会再试一次", out)
        self.no_success_words(out)
        self.assertEqual(self.calls(), [])

    def failing(self, mode, why):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.uv(mode)
        self.skill()
        with mock.patch.object(plaza_python, "STEP", {"python": 2.0, "venv": 2.0, "pip": 2.0}):
            rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["missing"], v["python"]["prep"]), ("pending", ["Python ≥ 3.10"], why))
        self.assertIn("会再试一次由 Agent J 用 uv 自动准备", out)
        self.assertIn("tries once more to prepare it automatically with uv", out)
        self.no_success_words(out)
        self.assertNotIn(os.path.expanduser("~") + "/secret-path", out)
        self.assertEqual(list((plaza_python.base(self.e.st) / "envs").glob("*/ready.json")), [])
        return out

    def test_uv_failing_is_pending_and_verify_will_retry(self):
        self.failing("fail", "failed")
        self.uv("ok")                                 # the rerun tries again and passes
        out = io.StringIO()
        self.assertEqual(market.run_reverify("demo-skill", st=self.e.st, out=out, local=True), market.EXIT_OK, out.getvalue())
        self.assertEqual((self.rec()["verify"]["state"], self.rec()["verify"]["python"]["source"]), ("ok", "agentj_uv"))

    def test_uv_too_slow_is_pending(self):
        self.failing("sleep", "timeout")

    def test_host_system_task_and_chat_line_name_python(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.uv("ok", present=False)
        self.skill()
        self.preview_and_confirm(accept_unverified=True)
        from types import SimpleNamespace
        from agentj import plaza_host
        sent, pages = [], []
        host = SimpleNamespace(st=self.e.st, lang="zh", preferences=None, agent=SimpleNamespace(submit=sent.append),
                               hist_add=lambda item, *a: pages.append(item) or {"id": 7})
        got = plaza_host._deps_task(host, ["demo-skill"], False)
        self.assertEqual(got, {"demo-skill": ["Python ≥ 3.10"]})
        self.assertEqual(len(sent), 1)
        self.assertIn("系统里找到的是 3.9.6", sent[0].text)
        line = plaza_host._say(host, "deps_pending", name="demo-skill", deps="、".join(got["demo-skill"]))
        self.assertIn("Python ≥ 3.10", line)
        for t in (sent[0].text, line):
            self.no_success_words(t)

    def test_new_enough_system_python_never_calls_uv(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.system("3 12 1")
        self.skill()
        rc, prev = self.install(accept_unverified=True)
        self.assertNotIn("将由 Agent J 用 uv", prev)
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["python"]["source"], v["python"]["version"]), ("ok", "system", "3.12.1"))
        self.assertEqual(self.calls(), [])
        self.assertNotIn("Agent J 准备的 Python", out)
        self.assertFalse(plaza_python.base(self.e.st).exists())


class Requirements(Base):
    REQ = {"python_requirements": "requirements.txt"}

    def files(self, req="numpy\n"):
        return {**fx.SKILL_FILES, "requirements.txt": req}

    def test_certified_package_gets_its_declared_wheels_only(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.system("3 12 1")                        # even with a new enough system Python: its own environment
        key = fx.TestKey()
        self.add_skill(key=key, track="official", certified=True, files=self.files(),
                       manifest={"requires": {"harness": ["claude_code"], "os": ["linux", "macos"], "skills": [],
                                              "cli": [{"name": "python3", "version": ">=3.10"}], **self.REQ}})
        rc, prev = self.install(keyring=key.keyring)
        self.assertEqual(rc, 0, prev)
        self.assertIn("，并从 PyPI 下载安装包声明的 Python 依赖（requirements.txt）：numpy（只装现成的 wheel，不从源码构建）", prev)
        rc, out = self.install(keyring=key.keyring, owner_confirmed=True, digest=tm.digest_of(prev))
        self.assertEqual(rc, 0, out)
        pip = [c for c in self.calls() if c.startswith("pip install")]
        self.assertEqual(len(pip), 1, self.calls())
        self.assertRegex(pip[0], r"^pip install --python \S+/venv/bin/python3 --only-binary :all: -r \S+/requirements\.txt$")
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["python"]["source"], v["requirements"]), ("ok", "agentj_uv", "installed"))

    def test_unverified_package_requirements_are_not_installed(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.add_skill(files=self.files(), manifest={"requires": {"harness": ["claude_code"], "os": ["linux", "macos"], "skills": [],
                                                                  "cli": [{"name": "python3", "version": ">=3.10"}], **self.REQ}})
        rc, prev = self.install(accept_unverified=True)
        self.assertIn("；包声明的 Python 依赖（requirements.txt）不自动安装：只有 Agent J 官方签名的包才自动装", prev)
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual([c for c in self.calls() if c.startswith("pip")], [])
        self.assertIn("包声明的 Python 依赖没有安装：只有 Agent J 官方签名的包才自动装", out)
        self.assertEqual(self.rec()["verify"]["requirements"], "uncertified")

    def test_only_plain_requirement_lines(self):
        ok = b"numpy\n# c\nPillow[extra]>=10,<12 ; python_version >= \"3.10\"\nrequests==2.31.0  # pin\n\n"
        self.assertEqual(plaza_python.req_lines(ok), ["numpy", 'Pillow[extra]>=10,<12 ; python_version >= "3.10"', "requests==2.31.0"])
        for bad in (b"-e git+https://x/y.git\n", b"--index-url https://evil/simple\nnumpy\n", b"./local\n", b"x @ https://h/x.whl\n",
                    b"-r other.txt\n", b"/abs/path\n", b"\xff\n"):
            self.assertIsNone(plaza_python.req_lines(bad), bad)


class Hygiene(Base):
    def test_uv_runs_clean_in_agent_js_own_folder(self):
        with mock.patch.dict(os.environ, {"AJ_P0305_FAKE_TOKEN": "x", "HTTPS_PROXY": "http://proxy.example.com:3128",
                                          "https_proxy": "http://u:pw@proxy.example.com:3128"}):
            r = plaza_python.prepare(self.e.st, "demo-skill", "c" * 64, (3, 10))
        self.assertTrue(r["ok"], r)
        env = (self.log.parent / "uv.log.env").read_text()
        self.assertNotIn("AJ_P0305_FAKE_TOKEN", env)
        self.assertIn("UV_NO_CONFIG=1", env)
        b = os.path.realpath(plaza_python.base(self.e.st))
        self.assertIn(f"HOME={b}/home", env)
        self.assertIn(f"UV_CACHE_DIR={b}/cache", env)
        self.assertIn("HTTPS_PROXY=http://proxy.example.com:3128", env)
        self.assertNotIn("u:pw@", env)
        real_uv = os.path.realpath(os.path.expanduser("~/.local/share/uv"))
        self.assertNotIn(real_uv, self.log.read_text())
        again = plaza_python.prepare(self.e.st, "demo-skill", "c" * 64, (3, 10))
        self.assertTrue(again["ok"] and again["reused"])
        self.assertEqual(len(self.calls()), 2)

    def test_failure_tail_is_redacted(self):
        self.uv("fail")
        r = plaza_python.prepare(self.e.st, "demo-skill", "d" * 64, (3, 10))
        self.assertEqual((r["ok"], r["why"]), (False, "failed"))
        self.assertIn("~/secret-path", r["tail"])
        self.assertNotIn(os.path.expanduser("~") + "/", r["tail"])

    def test_off_and_unsafe(self):
        with mock.patch.dict(os.environ, {"AGENTJ_PLAZA_UV": "off"}):
            self.assertEqual(plaza_python.prepare(self.e.st, "demo-skill", "e" * 64, (3, 10))["why"], "off")
        self.assertEqual(plaza_python.prepare(self.e.st, "../x", "e" * 64, (3, 10))["why"], "unsafe")
        self.assertEqual(plaza_python.prepare(self.e.st, "demo-skill", "e" * 64, (3, 99))["why"], "too_new")
        self.assertEqual(self.calls(), [])

    def test_uv_binary_must_not_be_writable_by_others(self):
        p = self.box / "uv-gw"
        p.write_text("#!/bin/sh\n")
        p.chmod(0o777)
        with mock.patch("agentj.update.installer_binary", lambda n: str(p)):
            self.assertIsNone(_real_uv_binary())
        p.chmod(0o755)
        with mock.patch("agentj.update.installer_binary", lambda n: str(p)):
            self.assertEqual(_real_uv_binary(), os.path.realpath(p))
        with mock.patch.dict(os.environ, {"AGENTJ_PLAZA_UV": "off"}), mock.patch("agentj.update.installer_binary", lambda n: str(p)):
            self.assertIsNone(_real_uv_binary())


_REAL_UV_BINARY = plaza_python.uv_binary


def _real_uv_binary():
    return _REAL_UV_BINARY()


class Sandbox(unittest.TestCase):
    def test_bwrap_mounts_the_prepared_python_read_only_after_hiding_home_and_state(self):
        a = market.verify_bwrap_argv("/tmp/aj-p0305-s", "/tmp/aj-p0305-s/pkg", homes=["/home/x"], hide=["/home/x/.local/state/agentj"],
                                     bwrap="bwrap", expose=["/home/x/.local/state/agentj/plaza/python/envs/a-0123456789abcdef"])
        i = a.index("/home/x/.local/state/agentj/plaza/python/envs/a-0123456789abcdef")
        self.assertEqual(a[i - 1], "--ro-bind")
        self.assertEqual(a[i + 1], a[i])
        last_tmpfs = max(j for j, x in enumerate(a) if x == "--tmpfs")
        self.assertGreater(i, last_tmpfs)
        self.assertEqual(a[1:4], ["--ro-bind", "/", "/"])
        self.assertNotIn("--bind", a[i - 1:i])

    def test_sbpl_reads_and_runs_but_never_writes_the_prepared_python(self):
        ex = "/srv/owner-x/.local/state/agentj/plaza/python/envs/a-0123456789abcdef"
        prof, params = market.verify_sbpl_profile("/tmp/aj-p0305-s", homes=["/srv/owner-x"], hide=["/srv/owner-x/.local/state/agentj"],
                                                  expose=[ex])
        self.assertEqual(params["EXPOSE_0"], ex)
        self.assertIn('(allow file-read* process-exec (subpath (param "EXPOSE_0")))', prof)
        for line in prof.splitlines():
            if "EXPOSE" in line:
                self.assertNotIn("write", line)
        self.assertIn('(deny file-write* (subpath "/"))', prof)
        self.assertLess(prof.index('(deny file-read*'), prof.index('(param "EXPOSE_0")'))
        self.assertNotIn(ex, prof)                    # paths travel as parameters

    def test_no_expose_keeps_the_old_profiles(self):
        self.assertNotIn("EXPOSE", market.verify_sbpl_profile("/tmp/s", homes=["/srv/owner-x"])[0])
        a = market.verify_bwrap_argv("/tmp/s", "/tmp/s/pkg", homes=[], bwrap="bwrap")
        self.assertEqual(a.count("--ro-bind"), 1)


class Words(unittest.TestCase):
    def test_task_text_names_python_and_gives_one_step(self):
        deps = [{"name": "Python ≥ 3.10", "user": [], "found": "3.9.6", "prep": "no_uv"}]
        with mock.patch.object(plaza_python.sys, "platform", "darwin"):
            zh = plaza_deps.task_text("video-teardown", "1.0.0", deps, False, market.VERIFY_PATH, ["python3"])
            en = plaza_deps.task_text("video-teardown", "1.0.0", deps, True, market.VERIFY_PATH, ["python3"])
        self.assertIn("- Python ≥ 3.10：自检的系统 PATH 里没有（系统里找到的是 3.9.6）。本机没有 uv，Agent J 没法自动准备：从 python.org 下载 "
                      "Python 3.10 或更新版本的官方安装包（装到 /usr/local/bin），然后运行 `agentj plaza verify video-teardown`。", zh)
        self.assertIn("download the official installer for Python 3.10 or newer from python.org", en)
        self.assertNotIn("Homebrew", zh + en)
        self.assertNotIn("python3.10:", zh + en)
        with mock.patch.object(plaza_python.sys, "platform", "linux"):
            zh = plaza_deps.task_text("video-teardown", "1.0.0", [{**deps[0], "prep": "failed", "consent": True}], False,
                                      market.VERIFY_PATH)
        self.assertIn("先运行 `agentj plaza verify video-teardown`：它会再试一次由 Agent J 用 uv 自动准备（需要联网）。还不行再用系统包管理器装 "
                      "python3.10 或更新版本", zh)
        for t in (zh, en):
            for w in BANNED:
                self.assertNotIn(w, t)

    def test_label_is_shown_bare(self):
        self.assertEqual(plaza_deps.label("Python ≥ 3.10", [], False), "Python ≥ 3.10")
        self.assertEqual(plaza_deps.label("python3.10", [], False), "python3.10（包自检报告的）")

    def test_card_line(self):
        info = {"name": "video-teardown", "version": "1.0.0", "type": "skill", "sha256": "a" * 64, "digest": "b" * 16,
                "certified": True, "publisher": {"known": False}, "perm": plaza_perms.EMPTY, "extra": [], "audit": [],
                "target": "/home/x/.claude/skills/video-teardown", "harness": "claude_code", "verify": "bubblewrap",
                "python_prep": {"want": (3, 10), "req": "requirements.txt", "req_ok": True, "names": ["numpy"], "count": 1}}
        zh, en = plaza_perms.install_card_text(info, False), plaza_perms.install_card_text(info, True)
        self.assertIn("将由 Agent J 用 uv 为这个包准备 Python ≥ 3.10（需要联网下载，放在 Agent J 自己的目录，不改系统 Python），并从 PyPI "
                      "下载安装包声明的 Python 依赖（requirements.txt）：numpy（只装现成的 wheel，不从源码构建）", zh)
        self.assertIn("Agent J will prepare Python ≥ 3.10 for this package with uv (downloaded from the internet, kept in Agent J's "
                      "own folder; the system Python is not changed), and download from PyPI and install the Python dependencies "
                      "the package declares (requirements.txt): numpy (ready-made wheels only, nothing built from source)", en)
        g = plaza_perms.group_card_text([info], False, gsha="c" * 64)
        self.assertIn("用 uv 准备 Python ≥ 3.10", g)
        self.assertEqual(plaza_python.file_label("../../etc/passwd"), "requirements")


class FixRound(Base):
    """0305 fix round (security BLOCK F1–F8, QA P1 / P2, GAP-0305-1)."""

    def installed(self, uv="ok", **kw):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.uv(uv, **kw)
        self.skill()
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        return out

    def logs(self, ev):
        import json
        lines = self.e.st.log_path.read_text().splitlines() if self.e.st.log_path.exists() else []
        return [r for r in map(json.loads, lines) if r.get("ev") == ev]

    # --- P1: the real uv's minor-version link
    def test_p1_venv_never_goes_through_the_minor_version_link(self):
        self.installed()
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["python"]["source"]), ("ok", "agentj_uv"))
        b = os.path.realpath(plaza_python.base(self.e.st))
        self.assertTrue(os.path.islink(os.path.join(b, "interpreters", "cpython-3.12-fake")), "the stand-in makes uv's link")
        env_dir = os.path.dirname(os.path.dirname(os.path.dirname(v["python"]["path"])))
        cfg = pathlib.Path(env_dir, "venv", "pyvenv.cfg").read_text()
        self.assertIn(f"home = {b}/interpreters/cpython-3.12.4-fake/bin", cfg)
        self.assertNotIn("cpython-3.12-fake", os.readlink(os.path.join(env_dir, "venv", "bin", "python")))
        self.assertEqual(self.logs("plaza_python")[-1]["result"], "ok")

    def test_p1_a_venv_through_the_link_is_refused_and_never_ready(self):
        self.uv("minorlink")
        r = plaza_python.prepare(self.e.st, "demo-skill", "1" * 64, (3, 10))
        self.assertEqual((r["ok"], r["why"], r["step"]), (False, "failed", "venv"))
        self.assertEqual(list((plaza_python.base(self.e.st) / "envs").glob("*/ready.json")), [])

    def test_p1_a_probe_failure_is_not_ready_and_logged_failed(self):
        out = self.installed(pyrc=1)                   # the prepared python does not run in the sandbox
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["missing"], v["python"]["prep"]), ("pending", ["Python ≥ 3.10"], "failed"))
        self.assertEqual(list((plaza_python.base(self.e.st) / "envs").glob("*/ready.json")), [])
        last = self.logs("plaza_python")[-1]
        self.assertEqual((last["result"], last.get("reason")), ("failed", "probe"))
        self.no_success_words(out)

    def test_p1_b_a_broken_ready_env_is_rebuilt(self):
        probe = lambda r: True  # noqa: E731
        r = plaza_python.prepare(self.e.st, "demo-skill", "2" * 64, (3, 10), probe=probe)
        self.assertTrue(r["ok"], r)
        venv_py = pathlib.Path(r["expose"][0], "venv", "bin", "python")
        venv_py.unlink()                               # what 0.30.6-pre built: through the minor-version link
        venv_py.symlink_to(os.path.join(os.path.realpath(plaza_python.base(self.e.st)), "interpreters", "cpython-3.12-fake",
                                        "bin", "python3.12"))
        again = plaza_python.prepare(self.e.st, "demo-skill", "2" * 64, (3, 10), probe=probe)
        self.assertTrue(again["ok"] and not again["reused"], again)
        self.assertEqual([c.split()[0] for c in self.calls()].count("venv"), 2)
        bad = lambda r: False  # noqa: E731
        third = plaza_python.prepare(self.e.st, "demo-skill", "2" * 64, (3, 10), probe=bad)
        self.assertFalse(third["ok"])                  # a ready env the sandbox cannot run is removed, rebuilt, still refused
        self.assertFalse(pathlib.Path(r["expose"][0], "ready.json").exists())

    # --- F2: interpreter_dir location
    def test_f2_interpreter_dir_must_be_the_real_cpython_folder(self):
        r = plaza_python.prepare(self.e.st, "demo-skill", "3" * 64, (3, 10))
        self.assertTrue(r["ok"], r)
        b = os.path.realpath(plaza_python.base(self.e.st))
        env_dir, interp = r["expose"]
        self.assertEqual(plaza_python.exposure(b, env_dir)["expose"], [env_dir, interp])
        link = os.path.join(b, "interpreters", "cpython-3.12-fake")
        other = self.box / "cpython-3.12.4-elsewhere"
        other.mkdir()
        odd = os.path.join(b, "interpreters", "not-a-python")
        os.mkdir(odd)
        for wrong in (link, str(other), "/usr", odd, os.path.join(b, "cache")):
            with self.subTest(wrong=wrong):
                self.assertIsNone(plaza_python.interp_dir(b, wrong))
        import json
        ready = pathlib.Path(env_dir, "ready.json")
        rec = json.loads(ready.read_text())
        for wrong in (link, str(other), "/usr"):
            ready.write_text(json.dumps({**rec, "interpreter_dir": wrong}))
            self.assertIsNone(plaza_python.exposure(b, env_dir), wrong)
            self.assertEqual(plaza_python.fence_dirs(self.e.st), [])

    # --- F3
    def test_f3_probe_failure_after_prep_is_not_prepared(self):
        used = {}
        fake = lambda probe=None: {"ok": True, "path": "/nonexistent/venv/bin/python3", "version": "3.12.4", "expose": [],  # noqa: E731
                                   "requirements": "installed"}
        with tempfile.TemporaryDirectory(dir=TMP) as t:
            exe, _, _ = market._try_prep(fake, used, (3, 10), "bubblewrap", pathlib.Path(t), pathlib.Path(t), [], ["true", "--"],
                                         {}, market._now_mono() + 5, True)
        self.assertIsNone(exe)
        self.assertEqual((used["prep"], used["requirements"]), ("failed", "not_prepared"))

    # --- F4 / F5
    def test_f4_card_lists_up_to_five_dependency_names(self):
        lines = plaza_python.req_lines(("numpy>=1.26\nPillow[extra]\nrequests==2.31\nscipy\nopencv-python-headless\n"
                                        + "a" * 60 + "\nrich\n").encode())
        names, count = plaza_python.req_names(lines)
        self.assertEqual(names, ["numpy", "Pillow", "requests", "scipy", "opencv-python-headless"])
        self.assertEqual(count, 7)
        plan = {"want": (3, 10), "req": "requirements.txt", "req_ok": True, "names": names, "count": count}
        self.assertIn("，并从 PyPI 下载安装包声明的 Python 依赖（requirements.txt）：numpy、Pillow、requests、scipy、"
                      "opencv-python-headless…等 7 个（只装现成的 wheel，不从源码构建）", plaza_python.card_line(plan, False))
        self.assertIn("(requirements.txt): numpy, Pillow, requests, scipy, opencv-python-headless… 7 in all (ready-made",
                      plaza_python.card_line(plan, True))
        long, _ = plaza_python.req_names(["b" * 90 + "==1"])
        self.assertEqual(long, ["b" * 40])
        self.assertNotIn("<x>", plaza_python.card_line({**plan, "names": ["<x>"], "count": 1}, False))

    def test_f5_a_marker_never_hides_an_option(self):
        for bad in (b'numpy ; python_version > "3" --pre\n', b'numpy; os_name == "posix" -i x\n', b"numpy ;-x\n"):
            self.assertIsNone(plaza_python.req_lines(bad), bad)
        self.assertEqual(plaza_python.req_lines(b'numpy ; platform_machine != "x86-64"\n'), ['numpy ; platform_machine != "x86-64"'])

    # --- P2
    def test_p2_preview_names_the_version_found(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.uv("ok", present=False)
        self.skill()
        rc, prev = self.install(accept_unverified=True)
        self.assertIn("Python ≥ 3.10（系统里找到的是 3.9.6）", prev)
        self.assertNotIn("Python ≥ 3.10（本机没找到）", prev)

    # --- F1 + F8
    def test_f1_uv_runs_only_when_the_card_showed_it(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.skill()
        real = market._py_plan
        with mock.patch.object(market, "_py_plan", lambda *a: {**real(*a), "uv": False, "no_prep": "no_uv"}):
            rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertEqual(self.calls(), [], "no uv line on the card → no uv call")
        self.assertNotIn("python_prep", self.rec()["verify"])

    def test_f1_digest_covers_the_uv_line(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.skill()
        rc, with_uv = self.install(accept_unverified=True)
        self.uv("ok", present=False)
        rc, without = self.install(accept_unverified=True)
        self.assertNotEqual(tm.digest_of(with_uv), tm.digest_of(without))
        rc, out = self.install(accept_unverified=True, owner_confirmed=True, digest=tm.digest_of(with_uv))
        self.assertEqual(rc, market.EXIT_DIGEST, out)

    def no_consent(self, rec_change):
        self.installed("ok", present=False)                       # pending: no uv at install, so no consent recorded
        self.assertNotIn("python_prep", self.rec()["verify"])
        rec_change()
        self.uv("ok")                                               # uv appears later: verify still does not use it
        out = io.StringIO()
        self.assertEqual(market.run_reverify("demo-skill", st=self.e.st, out=out, local=True), market.EXIT_ERROR, out.getvalue())
        text = out.getvalue()
        self.assertEqual(self.calls(), [])
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["python"]["prep"]), ("pending", "no_consent"))
        self.assertIn("运行 `agentj plaza request demo-skill --replace`（主人手机上重新弹卡，同意后由 Agent J 准备）", text)
        self.assertIn("run `agentj plaza request demo-skill --replace` (a new card on the owner's phone", text)
        self.assertNotIn("会再试一次", text)
        self.assertNotIn("tries once more", text)
        return text

    def test_f1_verify_without_a_recorded_yes_never_prepares(self):
        self.no_consent(lambda: None)                              # e.g. video-teardown installed before 0.30.6

    def test_f1_verify_with_a_different_plan_never_prepares(self):
        import json
        def change():
            p = self.e.st.root / "plaza" / "installed.json"
            d = json.loads(p.read_text())
            d["items"][-1]["verify"]["python_prep"] = {"want": "3.9", "req": None, "req_ok": False, "req_sha": None}
            p.write_text(json.dumps(d))
        self.no_consent(change)

    def test_f8_inside_the_fence_the_preview_does_not_promise_uv(self):
        if not market.sandbox_kind():
            self.skipTest("no sandbox on this machine")
        self.skill()
        with mock.patch.object(market.fence, "inside", lambda *a: True):
            rc, prev = self.install(accept_unverified=True)
            self.assertNotIn("将由 Agent J 用 uv", prev)
            self.assertIn("Python ≥ 3.10（系统里找到的是 3.9.6）", prev)
            rc, out = self.install(accept_unverified=True, owner_confirmed=True, digest=tm.digest_of(prev))
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.rec()["verify"]["python"]["prep"], "host_only")
        self.assertIn("Agent 的沙箱里不能自动准备：运行 `agentj plaza request demo-skill --replace`", out)

    # --- GAP-0305-1: the Agent's fence shows exactly the two folders, read-only
    def fence_argv(self):
        from agentj import fence
        rt = self.box / "run"
        rt.mkdir(exist_ok=True)
        return fence.bwrap_argv(self.e.st, str(self.e.home), home=str(self.e.home), runtime=str(rt), environ={})

    def test_gap1_fence_mounts_the_env_and_its_interpreter_read_only(self):
        self.installed()
        from agentj import fence
        v = self.rec()["verify"]
        b = os.path.realpath(plaza_python.base(self.e.st))
        env_dir = os.path.dirname(os.path.dirname(os.path.dirname(v["python"]["path"])))
        interp = os.path.join(b, "interpreters", "cpython-3.12.4-fake")
        a = self.fence_argv()
        root = os.path.realpath(self.e.st.root)
        state_tmpfs = max(i for i in range(len(a) - 1) if a[i] == "--tmpfs" and a[i + 1] == root)
        mounted = [a[i + 1] for i in range(len(a) - 1) if a[i] == "--ro-bind" and a[i + 1].startswith(b)]
        self.assertEqual(mounted, [env_dir, interp])
        for d in (env_dir, interp):
            i = a.index(d)
            self.assertEqual((a[i - 1], a[i + 1]), ("--ro-bind", d))
            self.assertGreater(i, state_tmpfs)
        for never in ("cache", "home", ".lock"):
            self.assertNotIn(os.path.join(b, never), a)
        self.assertNotIn(b, a)
        prof, params = fence.sbpl_profile(self.e.st, str(self.e.home), home=str(self.e.home))
        self.assertEqual(sorted(v for k, v in params.items() if k.startswith("PYEX_")), sorted([env_dir, interp]))
        line = [ln for ln in prof.splitlines() if "PYEX_" in ln]
        self.assertEqual(line, ['(allow file-read* process-exec (subpath (param "PYEX_0")) (subpath (param "PYEX_1")))'])
        self.assertLess(prof.index('(deny file-read* file-write* (subpath (param "STATE")))'), prof.index(line[0]))
        for ln in prof.splitlines():
            if "PYEX_" in ln or "PYUP_" in ln:
                self.assertNotIn("write", ln)
        self.assertNotIn(env_dir, prof)                  # paths travel as parameters

    def test_gap1_unqualified_records_are_not_mounted(self):
        self.installed()
        import json
        p = self.e.st.root / "plaza" / "installed.json"
        d = json.loads(p.read_text())
        b = os.path.realpath(plaza_python.base(self.e.st))
        good = d["items"][-1]["verify"]["python"]["path"]
        for wrong in (os.path.join(b, "cache", "venv", "bin", "python3"), "/usr/bin/python3",
                      os.path.join(b, "envs", "x", "venv", "bin", "python3"), good.replace("/venv/bin/python3", "/venv/bin/python")):
            with self.subTest(wrong=wrong):
                d["items"][-1]["verify"]["python"]["path"] = wrong
                p.write_text(json.dumps(d))
                self.assertEqual(plaza_python.fence_dirs(self.e.st), [])
                self.assertFalse([x for x in self.fence_argv() if x.startswith(b)])
        d["items"][-1]["verify"]["python"] = {**d["items"][-1]["verify"]["python"], "path": good, "source": "system"}
        p.write_text(json.dumps(d))
        self.assertEqual(plaza_python.fence_dirs(self.e.st), [])
        self.assertEqual([k for k in fence.sbpl_profile(self.e.st, str(self.e.home), home=str(self.e.home))[1] if k.startswith("PY")]
                         if (fence := __import__("agentj.fence", fromlist=["x"])) else [], [])

if __name__ == "__main__":
    unittest.main()
