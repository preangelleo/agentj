"""L2: the Agent cannot approve devices, use the permission tool's token or change jarvis's state (G-A8 / G-A24).

Chain: real `serve` (no relay) + the stand-in `claude` (fakeclaude.py) + the real permission tool + the real bubblewrap fence.
The phone *approves* a hostile command (the human was fooled — the G-A24 case) and that command tries every way we know to
reach jarvis from inside: read the keys / allowlist / passphrase hash, write the allowlist, talk to the control socket, re-claim
the permission socket with the token in its own environment, walk /proc into serve's view, patch jarvis's code or the shell
start-up files. Each must fail, serve must log the refused claim, and the state must be unchanged afterwards.

Gate: the approval passphrase (scrypt, persisted lock that doubles), and `decide` refusing every code without it — the path a
same-user process *outside* the fence (or a holder of an admin-page session) would take. Plus: fence unavailable = agent not
started; the shipped package records no wire frames; the signed `decline` after N at `jarvis login`.
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
import jarvis_host  # noqa: E402
from jarvis_host import cloud, fence, gate, serve, wire  # noqa: E402
from jarvis_host.state import State  # noqa: E402

from test_l1 import Phone, _host, _ready  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
PKG = pathlib.Path(jarvis_host.__file__).resolve().parent   # wherever it is imported from: checkout or an installed wheel (L3)
PASS = "correct horse battery"

ATTACK = r'''
import json, os, socket, sys
st, serve_pid, home = sys.argv[1], int(sys.argv[2]), os.path.expanduser("~")
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
    s = socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(os.environ["AGENTJARVIS_PERM_SOCK"])
    s.sendall((json.dumps({"t": "claim", "token": os.environ.get("AGENTJARVIS_PERM_TOKEN", "")}) + "\n").encode())
    got = s.recv(200)
    if not got:
        raise ConnectionRefusedError("closed")
    return got
t("re-claim perm.sock", reclaim)
t("serve /proc root", lambda: open(f"/proc/{serve_pid}/root{st}/host_ed25519.key", "rb").read())
t("serve /proc environ", lambda: open(f"/proc/{serve_pid}/environ", "rb").read())
def pids():
    mine = {os.getpid(), os.getppid()}
    return [p for p in os.listdir("/proc") if p.isdigit()]
r["pids visible"] = len(pids())
def environ_of_permtool():
    for p in pids():
        try:
            cmd = open(f"/proc/{p}/cmdline", "rb").read()
        except OSError:
            continue
        if cmd.split(b"\0")[-3:-1] == [b"-m", b"jarvis_host.permtool"]:   # the tool itself, not claude's --mcp-config
            return open(f"/proc/{p}/environ", "rb").read()
    raise LookupError("no permtool")
t("permtool environ", environ_of_permtool)
def w_ok(p):
    if not os.access(p, os.W_OK):
        raise PermissionError("not writable")
    return "writable"
t("write jarvis code", lambda: w_ok(sys.argv[3]))
for rc in (".bashrc", ".profile", ".zshrc"):
    if os.path.exists(os.path.join(home, rc)):
        t("write ~/" + rc, lambda rc=rc: w_ok(os.path.join(home, rc)))
r["tmp"] = sorted(os.listdir("/tmp"))
rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
r["runtime"] = sorted(os.listdir(rt)) if os.path.isdir(rt) else []
r["env"] = sorted(k for k in ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "DISPLAY", "TMUX", "SSH_AUTH_SOCK",
                              "AGENTJARVIS_STATE_DIR") if k in os.environ)
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
        os.environ["AGENTJARVIS_CLAUDE_BIN"] = str(wrapper)
        self.st.set_agent_config("claude", str(self.work))
        (self.work / "attack.py").write_text(ATTACK)

    def tearDown(self):
        os.environ.pop("AGENTJARVIS_CLAUDE_BIN", None)
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
            cmd = f"{sys.executable} attack.py {self.st.root.resolve()} {os.getpid()} {PKG.resolve()}"
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
        opened = {k: v for k, v in r.items() if isinstance(v, str) and v.startswith("open:")}
        self.assertEqual(opened, {}, f"reached from inside the fence: {opened}")
        for k in ("read host_ed25519.key", "read devices.json", "read approver.json", "control socket", "re-claim perm.sock",
                  "serve /proc root", "write jarvis code", "permtool environ"):
            self.assertTrue(r[k].startswith("blocked:"), k)
        self.assertLess(r["pids visible"], 10, "a private PID namespace: jarvis's processes are not there")
        under_tmp = {pathlib.PurePath(p).relative_to("/tmp").parts[0] for p in [os.path.realpath(self.tmp.name), *fence.code_paths()]
                     if p.startswith("/tmp/")}
        self.assertEqual(set(r["tmp"]), under_tmp, "a private /tmp: only the path to the agent's folder (and jarvis's code, read-only)")
        self.assertNotIn("bus", r["runtime"])
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


class FenceConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_agent_folder_cannot_be_jarvis_itself(self):
        for d in (self.st.root, self.st.perm_dir, PKG):
            d.mkdir(exist_ok=True)
            with self.assertRaises(ValueError) as e:
                self.st.set_agent_config("claude", str(d))
            self.assertEqual(str(e.exception), "protected_dir")
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

    def test_fence_unavailable_means_the_agent_is_not_started(self):
        work = pathlib.Path(self.tmp.name) / "w"
        work.mkdir()
        self.st.set_agent_config("codex", str(work))
        sent, notices = [], []
        host = _host(self.st, sent)
        host.agent_notice = notices.append
        started = []

        async def fake_exec(*a, **k):
            started.append(a)
            raise AssertionError("must not start")

        async def go():
            from jarvis_host import agent as agents
            ag = agents.make(host, self.st.agent_config())
            with mock.patch.object(fence.shutil, "which", return_value=None), \
                    mock.patch.object(agents, "_bin", return_value="/usr/bin/true"), \
                    mock.patch("asyncio.create_subprocess_exec", fake_exec):
                fence._probe_cache.clear()
                await ag.turn("你好")
            return ag
        ag = asyncio.run(go())
        fence._probe_cache.clear()
        self.assertEqual(started, [])
        self.assertTrue(ag.is_down())
        self.assertIn("bubblewrap", notices[-1])
        self.assertIn("--unfenced", notices[-1])
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
        self.assertTrue(any(pkg == r or pkg.startswith(r + "/") for r in ro), f"jarvis's code is read-only: {pkg} in {ro}")
        for k in ("DBUS_SESSION_BUS_ADDRESS", "TMUX", "SSH_AUTH_SOCK", "WAYLAND_DISPLAY", "DISPLAY"):
            self.assertIn(k, a[a.index("--unsetenv"):])


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
            path = url.split("agentjarvis.test", 1)[1]
            calls.append((path, payload))
            return answers[path]
        res = cloud.login(self.st, "https://api.agentjarvis.test", show=lambda lg: None, confirm=lambda t, n=None: False,
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
            self.assertEqual(cloud.decline(self.st, "https://api.agentjarvis.test", login_id,
                                           post=lambda u, p, s=status, b=body: (s, b)), want)


if __name__ == "__main__":
    unittest.main()
