"""#455 security round 1 (0.26.8a2): unit-level checks for L1 (taint follows the harness cgroup) and M3 (own_report reads
check the bytes); round 2: N1 (`agentj busy run` options before `--`), N2 (git path args by real path), N3 (find start
points). Fake process tables / cgroup lookups only — no real unit, no user manager, no real agentj state.
"""
import _hermetic  # noqa: F401,I001
import atexit
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import background_done, busy_launch, peer_origin  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402

HARNESS, SERVE = 4100, 4000

# F3 tightening: a simulated root-owned system tree. Only `_LSTAT` lies (uid 0 for SYS and, with group/other write
# masked, for SYS's ancestors such as /tmp); modes inside SYS are real. HOME-like dirs elsewhere keep the real uid.
SYS = os.path.realpath(tempfile.mkdtemp(prefix="p455sys", dir=TEST_TMP))
atexit.register(shutil.rmtree, SYS, True)
BINS = {"systemd-run": f"{SYS}/usr/bin/systemd-run", "bash": f"{SYS}/usr/bin/bash", "agentj": f"{SYS}/venv/bin/agentj",
        "claude": f"{SYS}/opt/claude/bin/claude", "codex": f"{SYS}/opt/codex/codex", "opencode": f"{SYS}/opt/opencode/opencode"}
for _k, _p in BINS.items():
    os.makedirs(os.path.dirname(_p), mode=0o755, exist_ok=True)
    pathlib.Path(_p).write_bytes(b"\x7fELF synthetic " + _k.encode())
    os.chmod(_p, 0o755)
os.symlink("../../opt/claude/bin/claude", f"{SYS}/usr/bin/claude")   # a relative hop with `..`, like /usr/bin → ../lib/…


def _fake_lstat(p, real=os.lstat):
    s, q = real(p), os.path.normpath(os.fspath(p))
    inside = q == SYS or q.startswith(SYS + "/")
    if not inside and not (SYS + "/").startswith(q.rstrip("/") + "/"):
        return s
    f = {k: getattr(s, k) for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_gid")}
    return types.SimpleNamespace(**f, st_uid=0, st_mode=s.st_mode if inside else s.st_mode & ~0o022)


def seal(case, **env):
    """Patch the host's registry for one test: harness overrides → BINS, the agentj entry, an empty pin record."""
    env = {"AGENTJ_CLAUDE_BIN": BINS["claude"], "AGENTJ_CODEX_BIN": BINS["codex"], "AGENTJ_OPENCODE_BIN": BINS["opencode"], **env}
    for p in (mock.patch.object(peer_origin, "_LSTAT", _fake_lstat), mock.patch.object(peer_origin, "_PINS", {}),
              mock.patch.object(peer_origin, "AGENTJ", BINS["agentj"]),
              mock.patch.dict(os.environ, {k: v for k, v in env.items() if v is not None})):
        p.start()
        case.addCleanup(p.stop)
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)


def pinned(cmd):
    """The same command with the host-registered absolute paths in command position (the F3 shape)."""
    lb = r"(?<![\w/.=-])"
    cmd = re.sub(lb + r"(systemd-run|bash)(?= )", lambda m: BINS[m.group(1)], cmd)
    cmd = re.sub(lb + r"agentj(?= busy run)", BINS["agentj"], cmd)
    return re.sub(lb + r"(?<!--harness )(claude|codex|opencode)(?=[ '\"]|$)", lambda m: BINS[m.group(1)], cmd)
HOME_CG = "/user.slice/user-1000.slice/user@1000.service/app.slice/agentj.service"
OTHER_CG = "/user.slice/user-1000.slice/user@1000.service/app.slice/other.service"


class TaintFollowsTheHarnessCgroup(unittest.TestCase):
    """L1: a setsid / double-forked child of a group turn leaves the harness session but keeps its cgroup."""

    def episode(self, child_cg, child_ppid=1, child_sid=777, extra=None):
        L = busy_launch.Launch(tempfile.mkdtemp(dir=self.tmp.name))
        now = time.time()
        L.turn(False, now=now - 5)          # a group turn …
        L.end(now=now - 4)                  # … that ended; its sleeper lives on
        table = {SERVE: (1, SERVE, "1", now - 900), HARNESS: (SERVE, HARNESS, "100", now - 600),
                 4200: (child_ppid, child_sid, "900", now - 4.5)}        # started inside the episode, setsid'd (sid 777)
        groups = {SERVE: HOME_CG, HARNESS: HOME_CG, 4200: child_cg}
        for pid, row, group in extra or ():
            table[pid], groups[pid] = (row[0], row[1], row[2], now + row[3]), group
        cg = groups.get
        ctx = {"codex": {"pid": HARNESS, "proc_start": "100"}}
        L.scan(ctx, table, cgroup=cg, me=SERVE)
        return L.taint[-1][1]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p455sec-")
        self.addCleanup(self.tmp.cleanup)

    def test_setsid_sleeper_in_the_harness_cgroup_keeps_the_episode_open(self):
        self.assertIsNone(self.episode(HOME_CG), "orphaned, own session, same cgroup → still tainted")

    def test_process_elsewhere_lets_the_episode_close(self):
        self.assertIsNotNone(self.episode(OTHER_CG), "another cgroup, another session → not this harness's work")

    def test_serve_helpers_do_not_hold_the_episode(self):
        self.assertIsNotNone(self.episode(HOME_CG, child_ppid=SERVE, child_sid=SERVE), "serve's own helper process")

    def test_setsid_child_below_the_harness_keeps_the_episode_open(self):
        mid = [(4150, (HARNESS, HARNESS, "800", -600), HOME_CG)]           # a long-lived harness child (its shell)
        self.assertIsNone(self.episode(HOME_CG, child_ppid=4150, extra=mid))

    def test_unrelated_tree_sharing_the_cgroup_does_not_hold_the_episode(self):
        other = [(4300, (1, 4300, "50", -900), HOME_CG)]                   # e.g. another program in the same scope
        self.assertIsNotNone(self.episode(HOME_CG, child_ppid=4300, child_sid=4300, extra=other))

    def test_session_rule_still_applies(self):
        self.assertIsNone(self.episode(OTHER_CG, child_ppid=HARNESS, child_sid=HARNESS), "in the harness session")


class OwnReportBytes(unittest.TestCase):
    """M3: the no-card read is refused unless the file has one link, is ≤ 1 MiB and holds no secret."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p455own-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = pathlib.Path(self.tmp.name) / "ws"
        (self.ws / "reports").mkdir(parents=True)

    def file(self, name, body):
        p = self.ws / "reports" / name
        p.write_text(body)
        return str(p)

    def test_clean_report_passes(self):
        self.assertTrue(background_done.own_clean(self.file("review.md", "VERDICT: pass\n"), str(self.ws)))

    def test_hard_link_is_refused(self):
        src = self.ws / "notes.txt"
        src.write_text("plain\n")
        os.link(src, self.ws / "reports" / "notes.md")
        self.assertFalse(background_done.own_clean(str(self.ws / "reports" / "notes.md"), str(self.ws)))

    def test_secret_bytes_are_refused(self):
        tok = "ghp_" + "a1B2c3D4" * 4 + "e5F6"        # synthetic, built at run time
        self.assertFalse(background_done.own_clean(self.file("copied.log", "job output " + tok + "\n"), str(self.ws)))

    def test_oversize_and_symlink_are_refused(self):
        self.assertFalse(background_done.own_clean(self.file("big.log", "x" * ((1 << 20) + 1)), str(self.ws)))
        (self.ws / "reports" / "link.md").symlink_to(self.ws / "reports" / "big.log")
        self.assertFalse(background_done.own_clean(str(self.ws / "reports" / "link.md"), str(self.ws)))


class Round2Whitelist(unittest.TestCase):
    """N1–N3: pure peer_origin checks on a throw-away workspace and state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p455r2-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        base = pathlib.Path(self.tmp.name)
        self.ws, self.state, self.out = base / "ws", base / "state", base / "out"
        for d in (self.ws / "src", self.state, self.out / ".ssh"):
            d.mkdir(parents=True)
        self.head = f"systemd-run --user --collect --unit=agentj-p470-ceo -p WorkingDirectory={self.ws} "
        seal(self)

    def dispatch(self, tail):
        return peer_origin.dispatch(pinned(self.head + tail), str(self.ws), str(self.state))

    def bash(self, cmd):
        return peer_origin.allowed("Bash", {"command": cmd}, str(self.ws), str(self.state))

    def test_n1_busy_run_only_its_own_options_then_dashdash(self):
        ok = ["agentj busy run --who ceo --label P462 -- codex exec go",
              "agentj busy run -- claude -p go",
              f"agentj busy run --who ceo --label 'P470 收尾' --kind temp --report {self.ws}/.jarvis/REPORT-470.md --harness codex "
              "--model gpt --workflow wf --role ceo --parent jarvis -- bash -c 'codex exec go > .jarvis/out/p470.log 2>&1'",
              "agentj busy run --report .jarvis/REPORT.md -- opencode run go"]
        for t in ok:
            self.assertEqual(self.dispatch(t), "agentj-p470-ceo.service", t)
        bad = ["agentj busy run python3 x.py -- claude -p hi", "agentj busy run --who ceo python3 x.py -- claude -p hi",
               "agentj busy run /tmp/x -- claude -p hi", "agentj busy run --who --label -- claude -p hi",
               "agentj busy run --who -x -- claude -p hi", "agentj busy run --label --report -- claude -p hi",
               "agentj busy run --who ceo claude -p hi", "agentj busy run --who=ceo -- claude -p hi",
               "agentj busy run --who a --who b -- claude -p hi", "agentj busy run --kind boss -- claude -p hi",
               "agentj busy run --cwd /tmp -- claude -p hi", "agentj busy run --wh ceo -- claude -p hi",
               "agentj busy run -h -- claude -p hi", "agentj busy run --who", "agentj busy run --who ceo --",
               f"agentj busy run --report {self.state}/approvals.log -- claude -p hi",
               f"agentj busy run --report {self.out}/r.md -- claude -p hi", "agentj busy run --report /dev/null -- claude -p hi",
               f"agentj busy run --report {self.ws}/.env -- claude -p hi", "agentj busy run --report ~/r.md -- claude -p hi",
               "agentj busy run --who ceo -- python3 x.py -- claude -p hi"]
        for t in bad:
            self.assertIsNone(self.dispatch(t), t)
        self.assertIsNone(peer_origin.dispatch(pinned("systemd-run --user --collect --unit=agentj-p470-ceo agentj busy run --report "
                                                      "rel.md -- claude -p hi"), str(self.ws), str(self.state)),
                          "a relative --report needs a WorkingDirectory")

    def test_n2_git_paths_by_their_real_path(self):
        (self.out / ".ssh" / "id_ed25519").write_text("synthetic\n")
        (self.ws / "notes.txt").symlink_to(self.out / ".ssh" / "id_ed25519")
        (self.ws / "log.txt").symlink_to(self.state / "approvals.log")
        (self.ws / "src" / "a.py").write_text("x = 1\n")
        self.assertTrue(self.bash("git diff src/a.py"))
        for cmd in ("git diff notes.txt", "git add notes.txt", "git diff -- notes.txt", "git log -p log.txt"):
            self.assertFalse(self.bash(cmd), cmd)

    def test_n3_find_every_start_point(self):
        for cmd in ("find src -name '*.py'", "find -P src -type f", "find -O3 src", "find", "find . -maxdepth 1"):
            self.assertTrue(self.bash(cmd), cmd)
        s = self.state
        for cmd in (f"find -L {s}", f"find -H {s}", f"find -P {s}", f"find -O3 {s} -name x", f"find -D tree {s}",
                    f"find src {s} -name x", "find -L .", "find . -follow", f"find !x {s}", f"find - {s}",
                    f"find -P -L {s}", "find -LP .", "find -Dtree ."):
            self.assertFalse(self.bash(cmd), cmd)



class Round3(unittest.TestCase):
    """Security round 3: F1 (find expression allowlist; -files0-from / bfs -f), unquoted globs / braces, git /proc paths,
    F3 (the harness mode word right after the bare harness)."""

    setUp, dispatch, bash = Round2Whitelist.setUp, Round2Whitelist.dispatch, Round2Whitelist.bash

    def fixture(self):
        (self.out / ".ssh" / "id_ed25519").write_text("synthetic\n")
        (self.ws / "list0").symlink_to(self.out / ".ssh" / "id_ed25519")
        (self.ws / "src" / "a.py").write_text("x = 1\n")

    def test_f1_find_expression_allowlist(self):
        self.fixture()
        for cmd in ("find src -name '*.py' -type f -print", "find . -maxdepth 2 -mindepth 1 -iname x -o -path './src/*' -print0",
                    "find src -newer src/a.py", "find src -size +1k -mtime -2 -mmin +5", "find src \\( -name a -o -name b \\)",
                    "find src ! -name x", "find src -not -name x -a -type d", "find src -type f,d", "find -P src -ipath '*/a*'",
                    "find src -name '-files0-from'"):
            self.assertTrue(self.bash(cmd), cmd)
        for cmd in ("find -files0-from /proc/self/environ", "find . -files0-from /proc/self/environ",
                    "find . -files0-from list0", "find -files0-from list0", "find . -files0-from=list0", "find -files0-fromX",
                    "find -f /proc/self", "find -f src", "find src -newer /proc/self/environ", "find src -newer list0",
                    f"find src -newer {self.state}/approvals.log", "find src -exec cat '{}' ';'", "find . -execdir id ';'",
                    "find . -delete", "find . -fprint out", "find . -fls out", "find . -ok id ';'", "find . -empty",
                    "find src -name", "find src -type z", "find src -maxdepth x", "find src -name a stray", "find src , -print",
                    "find . -follow", "find -L .", "find src -newerXY src/a.py", "find src -printf '%p'", "find src -regex x"):
            self.assertFalse(self.bash(cmd), cmd)

    def test_unquoted_globs_and_braces_are_refused(self):
        self.fixture()
        self.assertFalse(self.bash("cat {/proc/self/environ,src/a.py}"), "brace expansion into a /proc path")
        self.assertFalse(self.bash("find src -newer {src/a.py,-files0-from,/proc/self/environ}"))
        self.assertFalse(self.bash("cat src/*"))
        self.assertFalse(self.bash("find src -name *.py"), "bash would expand it into unjudged words")
        self.assertFalse(self.bash("cat \\'{/proc/self/environ,x}"), "an escaped quote does not open quoting")
        self.assertTrue(self.bash("grep -n 'a*b' src/a.py"))
        self.assertTrue(self.bash('find src -name "*.py"'))
        self.assertIsNone(self.dispatch("claude -p go?"))
        self.assertIsNone(self.dispatch("claude -p {go,x}"))
        self.assertEqual(self.dispatch("claude -p 'go? {x}'"), "agentj-p470-ceo.service")

    def test_git_proc_sys_dev_paths(self):
        self.fixture()
        for cmd in ("git diff --no-index /dev/null /proc/self/environ", "git diff --no-index /dev/null /sys/kernel/x",
                    "git diff --no-index /dev/null /dev/stdin", "git log -- /proc/1/cmdline"):
            self.assertFalse(self.bash(cmd), cmd)
        self.assertTrue(self.bash("git diff --no-index /dev/null src/a.py"))

    def test_magic_links_into_dev_and_proc(self):
        """/dev/stdin and /dev/fd/N resolve through serve's own fds (often to /dev/null) — not what the harness reads."""
        self.fixture()
        (self.ws / "in").symlink_to("/dev/stdin")
        (self.ws / "hop").symlink_to(self.ws / "in")
        (self.ws / "self").symlink_to("/proc/self")
        (self.ws / "nul").symlink_to("/dev/null")
        for cmd in ("cat /dev/stdin", "cat /dev/fd/0", "cat in", "cat hop", "head self/environ", "find in",
                    "git diff --no-index /dev/null in", "git diff --no-index /dev/null hop", "find src -newer in"):
            self.assertFalse(self.bash(cmd), cmd)
        for cmd in ("cat /dev/null", "cat nul", "cat src/a.py", "wc -l nul src/a.py"):
            self.assertTrue(self.bash(cmd), cmd)

    def test_f3_harness_mode_word_right_after_the_harness(self):
        for t in ("claude -p go", "claude --print go", "claude -p --dangerously-skip-permissions go", "codex exec go",
                  "opencode run go", "bash -c 'claude -p go > out.log 2>&1'"):
            self.assertEqual(self.dispatch(t), "agentj-p470-ceo.service", t)
        for t in ("claude mcp add x -- npx y -p", "claude update -p", "claude --model opus -p go", "claude config set -p x",
                  "codex -c x=1 exec go", "codex login exec", "opencode --x run go", "opencode auth run",
                  "bash -c 'claude mcp add x -p'", "bash -c 'claude update --print'", "claude", "codex"):
            self.assertIsNone(self.dispatch(t), t)


class F3Pin(unittest.TestCase):
    """F3 tightening (Jarvis ruling 11-04): the dispatch shape binds every executable to a host-resolved absolute path —
    systemd-run / bash sealed (root-owned chain), agentj = the host's own entry, the harness = binaries.resolve in serve's
    own environment, sealed and hash-pinned. Bare words, fence-writable paths and changed files → not the shape (card)."""

    def setUp(self):
        Round2Whitelist.setUp(self)
        self.home = pathlib.Path(self.tmp.name) / "home"
        (self.home / ".local" / "bin").mkdir(parents=True)

    def go(self, cmd):
        return peer_origin.dispatch(cmd, str(self.ws), str(self.state))

    def plant(self, rel="claude", body=b"#!/bin/sh\nexec evil\n"):
        f = self.home / ".local" / "bin" / rel
        f.write_bytes(body)
        f.chmod(0o755)
        return str(f)

    def test_positive_registered_sealed_binaries(self):
        head = pinned("systemd-run --user --collect --unit=agentj-p470-ceo ")
        for tail in ("claude -p go", "codex exec go", "opencode run go", f"-p WorkingDirectory={self.ws} bash -c 'claude -p go'"):
            self.assertEqual(self.go(pinned(head + tail)), "agentj-p470-ceo.service", tail)
        via = head + f"{SYS}/usr/bin/claude -p go"     # the registry path behind a root-owned `..` link
        self.assertEqual(self.go(via), "agentj-p470-ceo.service")

    def test_bare_words_resolve_through_someone_elses_path(self):
        a = "systemd-run --user --collect --unit=agentj-p470-ceo "
        for cmd in (a + "claude -p go", pinned(a) + "claude -p go", pinned(a) + "bash -c 'claude -p go'",
                    pinned(a) + "bash -c " + repr(pinned("claude -p go")), a + pinned("claude -p go"),
                    pinned(a) + BINS["bash"] + " -c 'claude -p go'",
                    pinned(a) + "agentj busy run -- " + pinned("claude -p go"),
                    pinned(a) + f"{self.home}/agentj busy run -- " + pinned("claude -p go")):
            self.assertIsNone(self.go(cmd), cmd)

    def test_fence_planted_claude_in_local_bin(self):
        fake = self.plant()
        head = pinned("systemd-run --user --collect --unit=agentj-p470-ceo ")
        path = f"{self.home}/.local/bin:{SYS}/usr/bin:/usr/bin:/bin"
        seal(self, AGENTJ_CLAUDE_BIN=None, PATH=path, HOME=str(self.home))   # serve's own PATH finds the fake first
        self.assertIsNone(self.go(f"{head}{fake} -p go"), "registry → a fence-writable file: card")
        self.assertIsNone(self.go(head + "claude -p go"))
        seal(self)                                                              # registry → the sealed one
        self.assertIsNone(self.go(f"{head}{fake} -p go"), "not the registered path")
        seal(self, AGENTJ_CLAUDE_BIN=fake)                                      # even an explicit registry entry
        self.assertIsNone(self.go(f"{head}{fake} -p go"), "a uid-owned (fence-writable) file is never sealed")
        sysrun = self.plant("systemd-run", b"\x7fELF x")
        self.assertIsNone(self.go(pinned(f"{sysrun} --user --collect --unit=agentj-p470-ceo claude -p go")))

    def test_registered_path_with_another_hash_or_mode(self):
        cmd = pinned("systemd-run --user --collect --unit=agentj-p470-ceo codex exec go")
        self.assertEqual(self.go(cmd), "agentj-p470-ceo.service")
        orig = pathlib.Path(BINS["codex"]).read_bytes()
        self.addCleanup(pathlib.Path(BINS["codex"]).write_bytes, orig)
        pathlib.Path(BINS["codex"]).write_bytes(orig + b" swapped")
        self.assertIsNone(self.go(cmd), "same path, other bytes than the record")
        pathlib.Path(BINS["codex"]).write_bytes(orig)
        os.utime(BINS["codex"], ns=(1, 1))
        self.assertEqual(self.go(cmd), "agentj-p470-ceo.service", "same bytes again (stat changed): rehashed, matches")
        os.chmod(BINS["codex"], 0o775)
        self.addCleanup(os.chmod, BINS["codex"], 0o755)
        self.assertIsNone(self.go(cmd), "group-writable file")
        os.chmod(BINS["codex"], 0o755)
        os.chmod(os.path.dirname(BINS["codex"]), 0o777)
        self.addCleanup(os.chmod, os.path.dirname(BINS["codex"]), 0o755)
        self.assertIsNone(self.go(cmd), "a writable directory on the way")

    def test_scripts_need_a_sealed_absolute_interpreter(self):
        f = pathlib.Path(BINS["opencode"])
        orig = f.read_bytes()
        self.addCleanup(f.write_bytes, orig)
        cmd = pinned("systemd-run --user --collect --unit=agentj-p470-ceo opencode run go")
        for body, ok in ((b"#!/usr/bin/env node\n", False), (f"#!{SYS}/usr/bin/bash\n".encode(), True),
                         (f"#!{self.home}/.local/bin/node\n".encode(), False)):
            peer_origin._PINS.clear()
            f.write_bytes(body)
            self.assertEqual(bool(self.go(cmd)), ok, body)

    def test_fence_writable_means_uid_owned(self):
        """fence.bwrap_argv: `--dev-bind / /` as the same uid; credential files / unit dirs / sockets come back read-only,
        temp / state dirs become a private tmpfs, and (F3 B) only the harness install trees + an installed agentj are bound
        read-only (test_p455_f3b). ~/.local/bin stays writable inside — a file planted there is never sealed."""
        from agentj import fence
        from agentj.state import State
        st = State(pathlib.Path(self.tmp.name) / "s")
        st.init(relay="ws://127.0.0.1:1")
        st.perm_dir.mkdir(exist_ok=True)
        a = fence.bwrap_argv(st, str(self.ws), home=str(self.home), runtime="", environ={"HOME": str(self.home)})
        self.assertEqual(a[1:4], ["--dev-bind", "/", "/"])
        ro = [a[i + 2] for i, x in enumerate(a) if x == "--ro-bind"]
        for d in (self.home / ".local" / "bin", self.home / ".local" / "share" / "mise" / "installs"):
            self.assertFalse(any(str(d) == r or str(d).startswith(r.rstrip("/") + "/") for r in ro), d)
        self.assertFalse(peer_origin._sealed(self.plant()))
        self.assertTrue(peer_origin._sealed(BINS["bash"]))

if __name__ == "__main__":
    unittest.main()
