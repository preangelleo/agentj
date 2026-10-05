"""L2: the Agent cannot approve devices, use the permission tool's token or change agentj's state (G-A8 / G-A24).

Chain: real `serve` (no relay) + the stand-in `claude` (fakeclaude.py) + the real permission tool + the real bubblewrap fence.
The phone *approves* a hostile command (the human was fooled — the G-A24 case) and that command tries every way we know to
reach agentj from inside: read the keys / allowlist / passphrase hash, write the allowlist, talk to the control socket, re-claim
the permission socket with the token in its own environment, walk /proc into serve's view, patch agentj's code or the shell
start-up files. Each must fail, serve must log the refused claim, and the state must be unchanged afterwards.

Gate: the approval passphrase (scrypt, persisted lock that doubles), and `decide` refusing every code without it — the path a
same-user process *outside* the fence (or a holder of an admin-page session) would take. Plus: fence unavailable = agent not
started; the shipped package records no wire frames; the signed `decline` after N at `agentj login`.
"""
import asyncio
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _hermetic  # noqa: E402,F401  (never the real ~/.local/state; see _hermetic.py)
import agentj  # noqa: E402
from agentj import cloud, fence, gate, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402

from test_l1 import Phone, _host, _ready  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
PKG = pathlib.Path(agentj.__file__).resolve().parent   # wherever it is imported from: checkout or an installed wheel (L3)
PASS = "correct horse battery"

ATTACK = r'''
import json, os, socket, subprocess, sys
st, serve_pid, home = sys.argv[1], int(sys.argv[2]), os.path.expanduser("~")
mac = sys.platform == "darwin"
r = {}
def t(name, fn):
    try:
        r[name] = "open:" + str(fn())[:60]
    except Exception as e:
        r[name] = "blocked:" + type(e).__name__
for f in ("host_ed25519.key", "host_x25519.key", "devices.json", "config.json", "approver.json", "approvals.log", "host.log"):
    t("read " + f, lambda f=f: open(os.path.join(st, f), "rb").read())
t("write devices.json", lambda: open(os.path.join(st, "devices.json"), "r+").write("{}"))
def ctl():
    s = socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(os.path.join(st, "control.sock"))
    s.sendall(b'{"cmd":"pair"}\n'); return s.recv(200)
t("control socket", ctl)
def reclaim():
    s = socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(os.environ["AGENTJ_PERM_SOCK"])
    s.sendall((json.dumps({"t": "claim", "token": os.environ.get("AGENTJ_PERM_TOKEN", "")}) + "\n").encode())
    got = s.recv(200)
    if not got:
        raise ConnectionRefusedError("closed")
    return got
t("re-claim perm.sock", reclaim)
t("serve /proc root", lambda: open(f"/proc/{serve_pid}/root{st}/host_ed25519.key", "rb").read())
t("serve /proc environ", lambda: open(f"/proc/{serve_pid}/environ", "rb").read())
t("signal serve", lambda: os.kill(serve_pid, 0) or "signal allowed")
def pids():
    if mac:
        import ctypes
        arr = (ctypes.c_int * 8192)()
        n = ctypes.CDLL(None, use_errno=True).proc_listpids(1, 0, arr, ctypes.sizeof(arr))
        if n <= 0:
            raise PermissionError("proc_listpids")
        return [str(arr[i]) for i in range(n // 4) if arr[i]]
    return [p for p in os.listdir("/proc") if p.isdigit()]
try:
    r["pids visible"] = len(pids())
except OSError:
    r["pids visible"] = "blocked"
if mac:
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    def pidpath():
        b = ctypes.create_string_buffer(4096)
        if libc.proc_pidpath(serve_pid, b, 4096) <= 0:
            raise PermissionError("proc_pidpath")
        return b.value
    t("serve process info", pidpath)
    def serve_env():   # KERN_PROCARGS2 is not filtered by the sandbox (G-A53): what matters is that serve keeps no secret there
        mib, size = (ctypes.c_int * 3)(1, 49, serve_pid), ctypes.c_size_t(1 << 20)
        b = ctypes.create_string_buffer(1 << 20)
        if libc.sysctl(mib, 3, b, ctypes.byref(size), None, 0) != 0:
            return "not readable"
        raw = b.raw[:size.value]
        secret = [k for k in (b"AGENTJ_PERM_TOKEN=",) if k in raw]   # the human's own variables reach the Agent anyway
        return ("visible (residual); agentj secrets in it: " + (",".join(x.decode() for x in secret) or "none")
                if b"HOME=" in raw else "argv only")
    r["serve environ (sysctl)"] = serve_env()
def environ_of_permtool():
    for p in pids():
        try:
            cmd = open(f"/proc/{p}/cmdline", "rb").read()
        except OSError:
            continue
        if cmd.split(b"\0")[-3:-1] == [b"-m", b"agentj.permtool"]:   # the tool itself, not claude's --mcp-config
            return open(f"/proc/{p}/environ", "rb").read()
    raise LookupError("no permtool")
if mac:   # no /proc; sysctl shows same-user environments (G-A53) — the token is in the Agent's own environment anyway, spent
    r["permtool environ"] = "n/a on macOS: same token as in this process's own environment, already spent (re-claim refused)"
else:
    t("permtool environ", environ_of_permtool)
def w_ok(p):                     # a real open for writing (append, no create, nothing written): access() is not enough
    if os.path.isdir(p):           # under a sandbox; a directory → try to create a probe file in it
        q = os.path.join(p, ".aj-probe")
        os.close(os.open(q, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        os.unlink(q)
        return "writable"
    os.close(os.open(p, os.O_WRONLY | os.O_APPEND))
    return "writable"
t("write agentj code", lambda: w_ok(sys.argv[3]))
t("write agentj code file", lambda: w_ok(os.path.join(sys.argv[3], "__init__.py")))
for rc in (".bashrc", ".profile", ".zshrc"):
    if os.path.exists(os.path.join(home, rc)):
        t("write ~/" + rc, lambda rc=rc: w_ok(os.path.join(home, rc)))
def rename_away(d):             # moving a directory above agentj's code / state aside would let a new one be planted
    os.rename(d, d + ".aj-probe")
    os.rename(d + ".aj-probe", d)   # (put back at once if it ever worked)
    return "renamed"
t("rename above agentj code", lambda: rename_away(sys.argv[4]))
t("rename above state dir", lambda: rename_away(os.path.dirname(st)))
if mac:
    def create_new(p):
        os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        os.unlink(p)
        return "created"
    t("plant a LaunchAgent", lambda: create_new(os.path.join(home, "Library", "LaunchAgents", "aj-probe.plist")))
    t("create a missing ~/.zlogin", lambda: create_new(os.path.join(home, ".zlogin")) if not os.path.exists(
        os.path.join(home, ".zlogin")) else w_ok(os.path.join(home, ".zlogin")))
    def launchd_job():
        p = subprocess.run(["launchctl", "submit", "-l", "aj.l3.probe", "--", "/usr/bin/true"], capture_output=True)
        if p.returncode == 0:
            subprocess.run(["launchctl", "remove", "aj.l3.probe"], capture_output=True)
            return "job created"
        raise PermissionError("launchctl submit refused")
    t("launchd job (launchctl submit)", launchd_job)
    def setuid_crontab():
        p = subprocess.run(["/usr/bin/crontab", "-l"], capture_output=True)
        if p.returncode == 0 or b"no crontab" in p.stderr:
            return "crontab runs"
        raise PermissionError(p.stderr.decode()[:60] or "refused")
    t("crontab (setuid)", setuid_crontab)
    def lsopen():
        p = subprocess.run(["/usr/bin/open", "-g", "-j", "-a", "TextEdit"], capture_output=True)
        if p.returncode == 0:
            subprocess.run(["/usr/bin/osascript", "-e", 'quit app "TextEdit"'], capture_output=True)
            return "opened an app"
        raise PermissionError("LaunchServices refused")
    t("open an app (LaunchServices)", lsopen)
    def apple_event():
        p = subprocess.run(["/usr/bin/osascript", "-e", 'tell application "Finder" to get name of startup disk'],
                           capture_output=True)
        if p.returncode == 0:
            return "apple event sent"
        raise PermissionError(p.stderr.decode()[-60:])
    t("Apple Events (osascript)", apple_event)
    def tmux_sock():
        d = f"/private/tmp/tmux-{os.getuid()}"
        socks = sorted(os.listdir(d)) if os.path.isdir(d) else []
        if not socks:
            raise FileNotFoundError("no tmux server running")
        for n in socks:
            s = socket.socket(socket.AF_UNIX); s.settimeout(2)
            s.connect(os.path.join(d, n))
            s.close()
            return "connected to tmux " + n
    t("tmux socket", tmux_sock)
# G-A56: other programs' control sockets. Only visibility for herdr (never a byte to the human's real herdr session); a bare
# connect for the container engine (the daemon is sent nothing).
def herdr_sock():
    p = os.path.join(home, ".config", "herdr", "herdr.sock")
    os.stat(p)
    return "visible"
t("herdr socket", herdr_sock)
t("herdr folder", lambda: os.listdir(os.path.join(home, ".config", "herdr")) or (_ for _ in ()).throw(FileNotFoundError("empty")))
def docker_sock():
    for p in ("/var/run/docker.sock", "/run/docker.sock"):
        if os.path.exists(p):
            s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(p); s.close()
            return "connected " + p
    raise FileNotFoundError("no docker socket")
t("docker socket", docker_sock)
r["tmp"] = sorted(os.listdir("/tmp")) if not mac else "shared on macOS (no mount namespace)"
rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
r["runtime"] = sorted(os.listdir(rt)) if os.path.isdir(rt) else []
r["env"] = sorted(k for k in os.environ if k in ("WAYLAND_DISPLAY", "DISPLAY", "TMUX", "SSH_AUTH_SOCK",
                                                "AGENTJ_STATE_DIR", "DOCKER_HOST")
                  or k.startswith(("HERDR_", "ZELLIJ", "WEZTERM_", "KITTY_", "NVIM")))
open("attack.json", "w").write(json.dumps(r, ensure_ascii=False, indent=1))
print("done")
'''


def _state(d, passphrase=True) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    if passphrase:
        gate.set_passphrase(st, PASS)
    return st


def _fence_ok() -> bool:
    with tempfile.TemporaryDirectory() as d:
        st = State(pathlib.Path(d) / "s")
        st.root.mkdir()
        ok = fence.problem(st, d) is None
    fence._probe_cache.clear()
    return ok


@unittest.skipUnless(_fence_ok(), "bubblewrap fence not available on this machine")
class FencedChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        wrapper = pathlib.Path(self.tmp.name) / "claude"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeclaude.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        os.environ["AGENTJ_CLAUDE_BIN"] = str(wrapper)
        self.st.set_agent_config("claude", str(self.work))
        (self.work / "attack.py").write_text(ATTACK)

    def tearDown(self):
        os.environ.pop("AGENTJ_CLAUDE_BIN", None)
        self.tmp.cleanup()

    def test_an_approved_hostile_command_cannot_reach_jarvis(self):
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        host.ask_ttl = 120                # production deadline: an approval must act at once, not at the deadline
        before = {f: (self.st.root / f).read_bytes() for f in ("devices.json", "config.json", "approver.json",
                                                              "host_ed25519.key")}

        def msgs():
            return [o for _, o in sent if o["t"] == "msg" and o.get("from") in ("agent", "notice")]

        async def wait(pred, ms=20000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; sent={sent[-6:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.02)

        async def go():
            run = asyncio.create_task(host.run())
            await wait(lambda: host.agent is not None)
            s = _ready(host, ph)
            self.assertEqual(stat.S_IMODE(self.st.perm_dir.stat().st_mode), 0o700)
            top = min(fence.code_paths(), key=len)       # the outermost code directory: its parent is what could be renamed
            cmd = f"{sys.executable} attack.py {self.st.root.resolve()} {os.getpid()} {PKG.resolve()} {os.path.dirname(top)}"
            await host._app(s, {"t": "msg", "id": "a" * 16, "text": "RUN: " + cmd, "ts": 0})
            await wait(lambda: any(o["t"] == "ask" for _, o in sent))
            self.assertTrue(host.perm_claimed.is_set())
            self.assertIsNone(host.perm_token, "the one-time token is spent before the agent got its first message")
            ask = [o for _, o in sent if o["t"] == "ask"][-1]
            t0 = time.monotonic()
            await host._app(s, ph.answer(ask, True))      # the human approves it: the worst case
            await wait(lambda: any(m["text"].startswith("已执行") for m in msgs()))
            self.assertLess(time.monotonic() - t0, 15, "the approval acted at once")
            # the agent's permission tool still works after the attempt (its claim was not taken over)
            await host._app(s, {"t": "msg", "id": "b" * 16, "text": "RUN: touch after.txt", "ts": 0})
            await wait(lambda: len([o for _, o in sent if o["t"] == "ask"]) == 2)
            await host._app(s, ph.answer([o for _, o in sent if o["t"] == "ask"][-1], True))
            await wait(lambda: (self.work / "after.txt").exists())
            host.stopping.set()
            await run
        asyncio.run(go())
        r = json.loads((self.work / "attack.json").read_text())
        if os.environ.get("AJ_EVIDENCE_DIR"):          # acceptance evidence: what the hostile command saw from inside
            pathlib.Path(os.environ["AJ_EVIDENCE_DIR"], "fence-attack.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
        st_parent = os.path.dirname(os.path.realpath(self.st.root))
        private = sys.platform != "darwin" and st_parent.startswith(("/tmp/", "/var/tmp/"))   # Linux: the fence's private tmpfs, not the real one
        code_parent = os.path.dirname(min(fence.code_paths(), key=len))
        private_code = sys.platform != "darwin" and code_parent.startswith(("/tmp/", "/var/tmp/"))   # e.g. a wheel installed under /tmp
        opened = {k: v for k, v in r.items() if isinstance(v, str) and v.startswith("open:")
                  and not (private and k == "rename above state dir") and k not in ("write agentj code", "write agentj code file", "rename above agentj code")
                  and not k.startswith("write ~/") and k not in ("plant a LaunchAgent", "create a missing ~/.zlogin", "launchd job (launchctl submit)")}
        self.assertEqual(opened, {}, f"reached from inside the fence: {opened}")
        for k in ("read host_ed25519.key", "read devices.json", "read approver.json", "control socket", "re-claim perm.sock",
                  "serve /proc root", "signal serve", "herdr socket",
                  "herdr folder", "docker socket"):
            self.assertTrue(r[k].startswith("blocked:"), k)
        if sys.platform != "darwin":
            self.assertTrue(r["permtool environ"].startswith("blocked:"), "permtool environ")
        self.assertTrue(r["write agentj code file"].startswith("open:"), "F14 self-code writable")
        if not private:
            self.assertTrue(r["rename above state dir"].startswith("blocked:"), "rename above state dir")
        self.assertTrue(os.path.isdir(st_parent) and (self.st.root / "config.json").exists(), "the real state dir is in place")
        if sys.platform == "darwin":      # no PID / mount namespace: the same rules as an SBPL deny-list (fence.sbpl_profile)
            self.assertTrue(r["serve environ (sysctl)"].endswith(("none", "argv only", "not readable")), "no agentj secret")
            for k in ("serve process info", "crontab (setuid)", "open an app (LaunchServices)", "tmux socket"):
                self.assertTrue(r[k].startswith("blocked:"), k)
            self.assertEqual(r["pids visible"], "blocked", "no process list outside the sandbox")
        else:
            self.assertLess(r["pids visible"], 10, "a private PID namespace: agentj's processes are not there")
            under_tmp = {pathlib.PurePath(p).relative_to("/tmp").parts[0] for p in [os.path.realpath(self.tmp.name),
                                                                                    *fence.code_paths()] if p.startswith("/tmp/")}
            self.assertEqual(set(r["tmp"]), under_tmp,
                             "a private /tmp: only the path to the agent's folder (and agentj's code, read-only)")
        # F14: the private runtime dir carries back only the user's service manager and session bus
        self.assertLessEqual(set(r["runtime"]), set(fence._RUNTIME_BACK))
        rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        if sys.platform != "darwin" and os.path.isdir(os.path.join(rt, "systemd")):
            self.assertIn("systemd", r["runtime"], "F14: systemctl --user reachable")
        for k, v in r.items():
            if k.startswith("write ~/"):
                self.assertTrue(v.startswith("open:"), f"F14: {k} is the owner's general configuration")
        self.assertEqual(r["env"], [])
        for f, b in before.items():
            self.assertEqual((self.st.root / f).read_bytes(), b, f"{f} unchanged")
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "perm_refused", "reason": "spent"', log, "the re-claim is refused and logged")
        self.assertIn('"ev": "perm_claimed"', log)
        self.assertEqual(log.count('"ev": "perm_claimed"'), 1)


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name, passphrase=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_verify_store_only_a_hash(self):
        with self.assertRaises(gate.GateError) as e:
            gate.verify(self.st, PASS)
        self.assertEqual(e.exception.reason, "not_set")
        with self.assertRaises(gate.GateError) as e:
            gate.set_passphrase(self.st, "short")
        self.assertEqual(e.exception.reason, "too_short")
        gate.set_passphrase(self.st, PASS)
        self.assertEqual(stat.S_IMODE(self.st.approver_path.stat().st_mode), 0o600)
        raw = self.st.approver_path.read_text()
        self.assertNotIn(PASS, raw)
        self.assertEqual(json.loads(raw)["kdf"], "scrypt")
        self.assertEqual(gate.verify(self.st, PASS), gate.FREE_TRIES)
        with self.assertRaises(gate.GateError) as e:
            gate.set_passphrase(self.st, "another passphrase")
        self.assertEqual(e.exception.reason, "exists", "a second set needs the old one (change)")
        with self.assertRaises(gate.GateError):
            gate.set_passphrase(self.st, "another passphrase", old="not it at all")
        gate.set_passphrase(self.st, "another passphrase", old=PASS)
        self.assertEqual(gate.verify(self.st, "another passphrase"), gate.FREE_TRIES)
        self.assertNotIn(PASS, self.st.log_path.read_text())
        self.st.check_perms()

    def test_wrong_tries_lock_and_the_lock_doubles_and_survives_a_restart(self):
        gate.set_passphrase(self.st, PASS)
        now = 1_000_000.0
        for left in (4, 3, 2, 1):
            with self.assertRaises(gate.GateError) as e:
                gate.verify(self.st, "nope nope", now=now)
            self.assertEqual((e.exception.reason, e.exception.info["left"]), ("wrong", left))
        with self.assertRaises(gate.GateError) as e:
            gate.verify(self.st, "nope nope", now=now)
        self.assertEqual((e.exception.reason, e.exception.info["seconds"]), ("locked", 60))
        with self.assertRaises(gate.GateError) as e:
            gate.verify(State(self.st.root), PASS, now=now + 30)        # a fresh State = a restarted serve
        self.assertEqual(e.exception.reason, "locked", "even the right passphrase waits out the lock")
        for _ in range(5):
            with self.assertRaises(gate.GateError):
                gate.verify(self.st, "nope nope", now=now + 61)
        self.assertEqual(gate.lock_left(self.st, now=now + 61), 120, "second lock = 2 min")
        self.assertEqual(gate.verify(self.st, PASS, now=now + 200), gate.FREE_TRIES)
        self.assertEqual(json.loads(self.st.approver_path.read_text())["locks"], 0, "a right one clears the ledger")


class Decide(unittest.TestCase):
    """What a same-user process outside the fence — or a holder of an admin-page session — gets at the control socket."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _pending(self, host):
        s = serve.Session(cid=7, state="pending", device="NEWNEWNEWNEWNEWN", name="攻击者的手机", pub=os.urandom(32), h=os.urandom(32))
        s.deadline = time.monotonic() + 60
        host.sessions[7] = s
        host.pairing = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=7)
        return s

    def test_the_right_code_without_the_passphrase_approves_nothing(self):
        host = _host(self.st, [])

        async def go():
            s = self._pending(host)
            code = wire.safety_code(s.h)
            out = [await host.decide(host.pairing, code, None)]            # nothing typed: asks again, costs no try
            self.assertEqual(out[0], {"ev": "pass_wrong", "left": gate.FREE_TRIES})
            out = [await host.decide(host.pairing, code, "guess guess")]
            for _ in range(3):
                out.append(await host.decide(host.pairing, code, "guess guess"))
            self.assertEqual(self.st.devices(), {})
            self.assertIn(7, host.sessions, "a wrong passphrase keeps the device waiting (the code is not used up)")
            p = host.pairing
            out.append(await host.decide(p, code, "guess guess"))     # 5th wrong → locked → denied
            out.append(await host.decide(p, code, PASS))
            return out
        out = asyncio.run(go())
        self.assertEqual([(o["ev"], o["left"]) for o in out[:4]], [("pass_wrong", n) for n in (4, 3, 2, 1)])
        self.assertEqual(out[4], {"ev": "denied", "reason": "pass_locked"})
        self.assertEqual(out[5], {"ev": "denied", "reason": "no_pending_device"})
        self.assertEqual(self.st.devices(), {})
        log = self.st.log_path.read_text()
        self.assertEqual(log.count('"ev": "pair_pass_wrong"'), 4)
        self.assertIn('"reason": "pass_locked"', log)

    def test_no_passphrase_set_refuses_and_right_passphrase_approves(self):
        host = _host(self.st, [])
        sent = []

        async def send_app(s, obj):
            sent.append(obj)
            return True
        host.send_app = send_app

        async def go():
            s = self._pending(host)
            r1 = await host.decide(host.pairing, wire.safety_code(s.h), PASS)
            return r1
        self.assertEqual(asyncio.run(go())["ev"], "approved")
        self.assertEqual(len(self.st.devices()), 1)
        self.st.approver_path.unlink()
        host2 = _host(self.st, [])

        async def go2():
            s = self._pending(host2)
            s.device = "OTHEROTHEROTHER1"
            return await host2.decide(host2.pairing, wire.safety_code(s.h), PASS)
        self.assertEqual(asyncio.run(go2()), {"ev": "denied", "reason": "pass_not_set"})
        self.assertEqual(len(self.st.devices()), 1)


INSIDE = r"""
import json, os, socket, subprocess, sys
out = {"env": sorted(k for k in os.environ if k.startswith(("HERDR_", "ZELLIJ", "WEZTERM_", "KITTY_", "TMUX", "NVIM"))
                     or k in ("DOCKER_HOST", "CONTAINER_HOST"))}
for name, p in json.loads(sys.argv[1]).items():
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(p); s.close()
        out[name] = "open"
    except OSError as e:
        out[name] = "blocked:" + type(e).__name__
    out[name + " exists"] = os.path.exists(p) and not os.path.samefile(p, "/dev/null")
if len(sys.argv) > 2:   # herdr's own client, as an injected Agent would use it
    r = subprocess.run([sys.argv[2], "status", "server"], capture_output=True, text=True, timeout=20)
    out["herdr status"] = (r.stdout + r.stderr).strip()[:200]
print(json.dumps(out))
"""


def _herdr() -> str | None:
    import shutil
    return shutil.which("herdr")


@unittest.skipUnless(_fence_ok(), "fence not available on this machine")
class ControlSockets(unittest.TestCase):
    """G-A56: control sockets outside the runtime dir and /tmp. A herdr server in a TEMPORARY HOME (never the human's own
    session: no HERDR_* of this shell reaches it, its socket is under the temp HOME), a listening socket in an arbitrary
    folder (tmux -S / emacs / nvim style), and the docker socket."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-ctl-")
        self.home = pathlib.Path(self.tmp.name) / "home"
        (self.home / "proj").mkdir(parents=True)
        self.st = _state(self.tmp.name, passphrase=False)
        self.st.perm_dir.mkdir(exist_ok=True)
        self.work = self.home / "proj"
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except Exception:  # noqa: BLE001
                    p.kill()
        self.tmp.cleanup()

    def _env(self, **extra) -> dict:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8"}
        env.update(extra)
        return env

    def _inside(self, env, targets: dict, allow_docker=False, herdr=None) -> dict:
        import subprocess
        if sys.platform == "darwin":
            a = fence.sandbox_argv(self.st, str(self.work), home=str(self.home), allow_docker=allow_docker, environ=env)
        else:
            a = fence.bwrap_argv(self.st, str(self.work), home=str(self.home), runtime="", allow_docker=allow_docker, environ=env)
        argv = a + [sys.executable, "-c", INSIDE, json.dumps(targets)] + ([herdr] if herdr else [])
        r = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return json.loads(r.stdout)

    def test_hide_env_by_prefix(self):
        env = {"HERDR_SOCKET_PATH": "/x", "HERDR_PANE_ID": "1", "HERDR_NEW_THING": "1", "ZELLIJ_SESSION_NAME": "s",
               "WEZTERM_UNIX_SOCKET": "/w", "KITTY_WINDOW_ID": "3", "NVIM": "/n", "VSCODE_IPC_HOOK_CLI": "/v", "DOCKER_HOST": "unix:///d",
               "HOME": "/h", "PATH": "/p"}
        names = fence.hide_env(env)
        for k in env:
            if k not in ("HOME", "PATH"):
                self.assertIn(k, names)
        self.assertNotIn("HOME", names)
        self.assertNotIn("DOCKER_HOST", fence.hide_env(env, allow_docker=True))

    @unittest.skipUnless(_herdr(), "herdr not installed")
    def test_a_herdr_server_is_unreachable_from_inside(self):
        import subprocess
        herdr = _herdr()
        env = self._env(TERM="xterm")
        srv = subprocess.Popen([herdr, "server"], env=env, cwd=str(self.home), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.procs.append(srv)
        sock = self.home / ".config" / "herdr" / "herdr.sock"
        t0 = time.monotonic()
        while not sock.exists() and time.monotonic() - t0 < 15:
            time.sleep(0.1)
        self.assertTrue(sock.exists(), "test herdr server started in the temporary HOME")
        outside = subprocess.run([herdr, "status", "server"], env=env, capture_output=True, text=True, timeout=20).stdout
        self.assertIn("status: running", outside, "control: outside the fence the herdr client reaches the server")
        # what an Agent started from a herdr pane inherits
        agent_env = self._env(HERDR_ENV="1", HERDR_SOCKET_PATH=str(sock), HERDR_PANE_ID="p1", HERDR_BIN_PATH=herdr)
        r = self._inside(agent_env, {"herdr": str(sock)}, herdr=herdr)
        try:
            subprocess.run([herdr, "server", "stop"], env=env, capture_output=True, timeout=20)
        except Exception:  # noqa: BLE001
            pass
        if os.environ.get("AJ_EVIDENCE_DIR"):
            pathlib.Path(os.environ["AJ_EVIDENCE_DIR"], "fence-herdr.json").write_text(
                json.dumps({"outside": outside.strip()[:200], "inside": r}, ensure_ascii=False, indent=1))
        self.assertTrue(r["herdr"].startswith("blocked"), r)
        self.assertFalse(r["herdr exists"], r)
        self.assertNotIn("status: running", r["herdr status"], r)   # Linux: "not running"; macOS: "Operation not permitted"
        self.assertEqual(r["env"], [], "no HERDR_* variable inside")

    @unittest.skipUnless(sys.platform == "linux", "Linux: from /proc/net/unix (macOS: named folders only, G-A56)")
    def test_a_listening_socket_in_any_folder_is_hidden(self):
        import socket
        p = self.home / "proj" / "tmux-custom.sock"     # tmux -S / emacs / nvim --listen style: an arbitrary path
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(p))
        srv.listen()
        try:
            c = socket.socket(socket.AF_UNIX)
            c.connect(str(p))
            c.close()                                    # control: reachable outside
            r = self._inside(self._env(), {"custom": str(p)})
        finally:
            srv.close()
        self.assertTrue(r["custom"].startswith("blocked"), r)
        a = fence.bwrap_argv(self.st, str(self.work), home=str(self.home), runtime="", environ=self._env())
        self.assertNotIn(str(self.st.perm_sock_path), a, "the permission socket is never hidden")

    def test_docker_socket_is_hidden_unless_the_human_allowed_it(self):
        import socket
        dock = next((p for p in ("/run/docker.sock", "/var/run/docker.sock") if os.path.exists(p)), None)
        if not dock or not os.access(dock, os.W_OK):
            self.skipTest("no docker socket this user can use")
        try:
            c = socket.socket(socket.AF_UNIX); c.settimeout(3); c.connect(dock); c.close()
        except OSError:
            self.skipTest("the docker daemon is not running")
        env = self._env(DOCKER_HOST=f"unix://{dock}")
        r = self._inside(env, {"docker": dock})
        self.assertTrue(r["docker"].startswith("blocked"), r)
        self.assertEqual(r["env"], [], "DOCKER_HOST unset")
        r2 = self._inside(env, {"docker": dock}, allow_docker=True)
        self.assertEqual(r2["docker"], "open", "--allow-docker: the human's explicit choice")
        self.assertEqual(r2["env"], ["DOCKER_HOST"])

    @unittest.skipUnless(sys.platform == "linux", "bubblewrap argv")
    def test_control_folders_and_the_runtime_dir_without_xdg(self):
        (self.home / ".config" / "herdr").mkdir(parents=True)
        (self.home / ".screen").mkdir()
        a = fence.bwrap_argv(self.st, str(self.work), home=str(self.home), runtime="", environ=self._env())
        tmpfs = [a[i + 1] for i, x in enumerate(a) if x == "--tmpfs"]
        self.assertIn(str((self.home / ".config" / "herdr").resolve()), tmpfs)
        self.assertIn(str((self.home / ".screen").resolve()), tmpfs)
        if os.path.isdir(f"/run/user/{os.getuid()}"):
            self.assertIn(os.path.realpath(f"/run/user/{os.getuid()}"), tmpfs, "hidden also when XDG_RUNTIME_DIR is unset")
        self.assertLess(tmpfs.index(str((self.home / ".config" / "herdr").resolve())), tmpfs.index(str(self.st.root.resolve())))
        # never the home itself or a folder that holds the Agent's folder
        d = fence.control_dirs(str(self.home), {"HERDR_SOCKET_PATH": str(self.home / "h.sock"),
                                                "SCREENDIR": str(self.work)}, workdir=str(self.work))
        self.assertNotIn(str(self.home.resolve()), d)
        self.assertNotIn(str(self.work.resolve()), d)


class FenceConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_agent_folder_cannot_be_jarvis_itself(self):
        for d in (self.st.root, self.st.perm_dir):
            d.mkdir(exist_ok=True)
            with self.assertRaises(ValueError) as e:
                self.st.set_agent_config("claude", str(d))
            self.assertEqual(str(e.exception), "protected_dir")
        self.st.set_agent_config("claude", str(PKG))  # F14 self-maintenance allowed
        work = pathlib.Path(self.tmp.name) / "w"
        work.mkdir()
        self.st.set_agent_config("claude", str(work))
        self.assertTrue(self.st.agent_config()["fence"], "fenced by default")
        self.st.set_agent_config("claude", str(work), fence=False)
        self.assertFalse(self.st.agent_config()["fence"])
        cfg = json.loads(self.st.config_path.read_text())
        cfg["agent"]["fence"] = "no"
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        self.assertTrue(self.st.agent_config()["fence"], "anything but an explicit false reads as fenced")
        self.assertFalse(self.st.agent_config()["docker"], "docker hidden by default (G-A56)")
        cfg["agent"]["docker"] = "yes"
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        self.assertFalse(self.st.agent_config()["docker"], "anything but an explicit true keeps docker hidden")
        self.st.set_agent_config("claude", str(work), docker=True)
        self.assertTrue(self.st.agent_config()["docker"])
        from agentj import doctor
        row = doctor.check_agent(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("--allow-docker", row["summary"])

    def test_fence_unavailable_degrades_to_the_harness_permissions_with_one_notice(self):
        """F14: no fence → the Agent starts unwrapped (the harness's own permissions), the phone is told once per serve."""
        work = pathlib.Path(self.tmp.name) / "w"
        work.mkdir()
        self.st.set_agent_config("codex", str(work))
        sent, notices = [], []
        host = _host(self.st, sent)
        host.agent_notice = notices.append
        from agentj import agent as agents
        ag = agents.make(host, {**self.st.agent_config(), "session_mode": "independent"})
        gone = (mock.patch.object(fence, "SANDBOX_EXEC", "/nonexistent/sandbox-exec") if sys.platform == "darwin"
                else mock.patch.object(fence.shutil, "which", return_value=None))
        with gone:
            fence._probe_cache.clear()
            self.assertEqual(ag.launch_argv(["/usr/bin/true", "x"]), ["/usr/bin/true", "x"], "started, unwrapped")
            self.assertEqual(ag.launch_argv(["/usr/bin/true", "y"]), ["/usr/bin/true", "y"])
        fence._probe_cache.clear()
        told = [n for n in notices if "沙箱不可用" in n]
        self.assertEqual(len(told), 1, notices)
        self.assertIn("sandbox-exec" if sys.platform == "darwin" else "bubblewrap", told[0])
        self.assertNotIn("--unfenced", told[0])
        self.assertIn('"ev": "agent_fence_fail"', self.st.log_path.read_text())

    def test_bwrap_argv_hides_state_and_puts_back_only_the_permission_folder(self):
        a = fence.bwrap_argv(self.st, "/srv/work", home="/home/u", runtime="")
        self.assertEqual(a[-1], "--")
        self.assertIn("--unshare-pid", a)
        self.assertIn("--die-with-parent", a)
        root, perm = str(self.st.root.resolve()), str(self.st.perm_dir.resolve())
        tmpfs = [a[i + 1] for i, x in enumerate(a) if x == "--tmpfs"]
        self.assertTrue(any(root == t or root.startswith(t + "/") for t in tmpfs), "state dir hidden")
        binds = [(a[i + 1], a[i + 2]) for i, x in enumerate(a) if x == "--bind"]
        self.assertIn((perm, perm), binds)
        self.assertEqual([b for b in binds if b[0].startswith(root) and b[0] != perm], [], "nothing else from the state dir")
        ro = [a[i + 1] for i, x in enumerate(a) if x == "--ro-bind"]
        pkg = str(PKG.resolve())
        self.assertFalse(any(pkg == r or pkg.startswith(r + "/") for r in ro), "F14: self-code is writable")
        for k in ("TMUX", "SSH_AUTH_SOCK", "WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "AGENTJ_STATE_DIR"):
            self.assertIn(k, a[a.index("--unsetenv"):])
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", a, "F14: the session bus stays reachable (service management)")


class Package(unittest.TestCase):
    def test_the_shipped_package_records_no_wire_frames(self):
        for f in PKG.rglob("*"):
            if f.suffix in (".py", ".js", ".html") and f.is_file():
                text = f.read_text()
                self.assertNotIn("WIRE_DUMP", text, f.name)
                self.assertNotIn("import wiredump", text, f.name)
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            host = serve.Host(st, events="jsonl", read_stdin=False)
            before = sorted(p.name for p in st.root.iterdir())
            host._tap("out", b"\x01" * 40)
            self.assertIsNone(serve.Host._tap(host, "in", "x"))
            self.assertEqual(sorted(p.name for p in st.root.iterdir()), before, "the shipped _tap writes nothing")


class Decline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_at_the_terminal_sends_a_signed_decline(self):
        calls = []
        login_id = "L" * 22
        answers = {"/v1/host/login": (201, {"login_id": login_id, "user_code": "BCDF-GHJK", "verification_uri": "https://x/",
                                            "expires_in": 600, "interval": 5}),
                   "/v1/host/poll": (200, {"status": "bound", "host_id": "h_1", "tenant": {"slug": "evil-co", "name": "Evil"}}),
                   "/v1/host/decline": (200, {"status": "declined"})}

        def post(url, payload):
            path = url.split("agentj.test", 1)[1]
            calls.append((path, payload))
            return answers[path]
        res = cloud.login(self.st, "https://api.agentj.test", show=lambda lg: None, confirm=lambda t, n=None: False,
                          post=post, sleep=lambda s: None)
        self.assertEqual((res["status"], res["undone"]), ("declined", "undone"))
        self.assertFalse(self.st.cloud_path.exists(), "nothing written on N")
        path, env = calls[-1]
        self.assertEqual(path, "/v1/host/decline")
        inner = json.loads(wire.unb64u(env["body"]))
        self.assertEqual(set(inner), {"v", "t", "channel", "ts", "login_id"})
        self.assertEqual((inner["t"], inner["login_id"], inner["channel"]), ("decline", login_id, cloud.channel_of(self.st)))
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(wire.unb64u(env["pk"])).verify(
            wire.unb64u(env["sig"]), f"{cloud.CTX_DECLINE}\n{env['body']}".encode())
        for status, body, want in ((409, {"error": "already_confirmed"}, "already_confirmed"), (404, {"error": "not_found"}, "not_found"),
                                   (429, {"error": "rate_limited"}, "fail"), (500, {}, "fail")):
            self.assertEqual(cloud.decline(self.st, "https://api.agentj.test", login_id,
                                           post=lambda u, p, s=status, b=body: (s, b)), want)


if __name__ == "__main__":
    unittest.main()
