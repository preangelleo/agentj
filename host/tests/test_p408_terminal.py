"""P408 (PROTOCOL §38, ADR-P408-terminal): the web terminal, line mode.

- default off; widening only from the owner's terminal (claim + tty) + a passkey remote's `AgentJTerminal` card; narrowing
  needs only the socket. The real root parser builds `agentj terminal`; an Agent-marked environment is refused.
- open = host nonce + signature + passkey every time: disabled · device_not_allowed · unpaired · no_passkey · passkey_failed ·
  stale · replay · bad_signature · busy · estop · locked; the `login` card goes only to the opening device.
- the computer password: checked with sudo (a stand-in here), cached prf / local / session; the cache file is ciphertext only;
  prf cannot open without the PRF output; local is refused (and deleted) when the Agent is not fenced + independent; a
  temporary entry is session mode; 3 wrong passwords lock; the password is in no message, log, cast, argv or environment.
- lines: dangerous ones wait for a signed + asserted resend; `echo hi` runs and comes back; masker units; askpass /proc checks;
  limits (timeout / idle with a fake clock), e-stop, relay_lost; the JS signs the same bytes.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import errno
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import controls, ctl_auth, elevate, passkey, preferences, term_askpass, term_cli, term_store, wire  # noqa: E402
from agentj import terminal  # noqa: E402
from agentj.term_mask import Masker  # noqa: E402
from agentj.term_shell import classify, shell_env  # noqa: E402

from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
AJ = HERE.parents[1]
FA = {"id": wire.b64u(b"\x07" * 16), "cd": "e30", "ad": "AA", "sig": "AA"}
GOOD = "-".join(("fake", "login", "pw", "4711"))          # a stand-in, never a real password
BAD = "-".join(("wrong", "pw"))
R = "abcdef0123456789"
SUDO_STUB = r"""#!/bin/bash
# stand-in sudo for the P408 tests: -k · -S -k -p MARK -- cmd · -A cmd (asks $SUDO_ASKPASS)
good=$(cat "$(dirname "$0")/good")
printf '%s\n' "$*" >> "$(dirname "$0")/args.log"
[ "$1" = "-k" ] && [ $# -eq 1 ] && exit 0
mark=""; ask=""
while [ $# -gt 0 ]; do case "$1" in -S|-k) shift;; -p) mark="$2"; shift 2;; -A) ask=1; shift;; --) shift; break;; *) break;; esac; done
for i in 1 2 3; do
  if [ -n "$ask" ]; then pw=$("$SUDO_ASKPASS" "[sudo] password: ") || { echo "sudo: no password was provided" >&2; exit 1; }
  else printf '%s' "$mark" >&2; IFS= read -r pw || { echo "sudo: no password was provided" >&2; exit 1; }; fi
  [ "$pw" = "$good" ] && exec "$@"
  echo "Sorry, try again." >&2
done
echo "sudo: 3 incorrect password attempts" >&2; exit 1
"""


def tmpdir():
    return tempfile.TemporaryDirectory(prefix="p408-", dir=os.environ.get("TEST_TMP") or None)


def signed(ph, action, target, n, ts=None, key=None):
    ts = int(time.time() * 1000) if ts is None else ts
    msg = controls.signed_message(ph.channel, ph.did, action, n, ts, controls.object_digest(action, target))
    return {"n": n, "ts": ts, "sig": wire.b64u((key or ph.sk).sign(msg))}


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        base = pathlib.Path(self.tmp.name)
        self.st = _state(base)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.phone = Phone(self.st)
        self.s = _ready(self.host, self.phone)
        self.s.p33 = True
        self.desk = self.host.term_desk
        self.desk.os_ok = True
        bin_dir = base / "bin"
        bin_dir.mkdir()
        (bin_dir / "good").write_text(GOOD)
        stub = bin_dir / "sudo"
        stub.write_text(SUDO_STUB)
        stub.chmod(0o755)
        self.desk.sudo = str(stub)
        self.home = base / "home"
        self.home.mkdir()
        self.work = base / "work"
        self.work.mkdir()
        self.host.agent_cfg = {**(self.host.agent_cfg or {}), "working_root": str(self.work), "fence": True,
                               "session_mode": "independent"}
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.pk = mock.patch.object(passkey, "verify_assertion", return_value=1)
        self.pk.start()

    def tearDown(self):
        self.pk.stop()
        self.env.stop()
        self.tmp.cleanup()

    # -------------------------------------------------------- helpers
    def passkey(self, ph=None):
        ph = ph or self.phone
        self.st.set_passkey(ph.pub, {"id": FA["id"], "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
                                     "rp": "m.agentj.app", "at": int(time.time())})

    def enable(self, *phones, until=None, web=False, minutes=15):
        ents = [{"device": p.did, "sudo": True, "web": web, **({"until": until} if until else {})} for p in phones or [self.phone]]
        term_store.write_config(self.st, {"on": True, "entries": ents, "minutes": minutes})

    def msgs(self, t=None, cid=None):
        return [o for c, o in self.sent if (t is None or o.get("t") == t) and (cid is None or c == cid)]

    async def until(self, pred, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            v = pred()
            if v:
                return v
            await asyncio.sleep(0.02)
        self.fail("timed out waiting")

    async def step1(self, s=None):
        s = s or self.s
        n0 = len(self.sent)
        await self.desk.on_phone(s, {"t": "term_open", "r": R})
        return [o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]

    async def step2(self, c1, ph=None, s=None, prf=None, fa=FA, **over):
        ph, s = ph or self.phone, s or self.s
        obj = {"t": "term_open", "r": R, **signed(ph, "term_open", {"mode": "line"}, c1["n"]), "fa": fa}
        if prf is not None:
            aad = elevate.seal_aad(ph.channel, ph.did, c1["n"], "term_prf", c1["n"],
                                   controls.object_digest("term_open", {"mode": "line"}))
            pepk, pct = elevate.seal(wire.unb64u(c1["epk"]), prf, aad)
            obj.update(pepk=wire.b64u(pepk), pct=wire.b64u(pct))
        obj.update(over)
        if fa is None:
            obj.pop("fa")
        n0 = len(self.sent)
        await self.desk.on_phone(s, obj)
        return [o for _, o in self.sent[n0:]]

    async def answer_login(self, card, pw, ph=None, s=None):
        ph, s = ph or self.phone, s or self.s
        digest = elevate.shown_digest("login", [card["term"]])
        aad = elevate.seal_aad(ph.channel, ph.did, card["id"], "login", card["n"], digest)
        epk, ct = elevate.seal(wire.unb64u(card["epk"]), pw.encode(), aad)
        ts = int(time.time() * 1000)
        sig = ph.sk.sign(elevate.signed_message(ph.channel, ph.did, card["id"], "login", "allow", card["n"], ts, digest,
                                                hashlib.sha256(ct).hexdigest()))
        await self.host.elevate.on_phone(s, {"t": "elev_answer", "id": card["id"], "ok": True, "n": card["n"], "ts": ts,
                                             "sig": wire.b64u(sig), "epk": wire.b64u(epk), "ct": wire.b64u(ct), "fa": FA})

    async def open(self, pw=GOOD, prf=None, expect_card=None):
        """Both steps; answers the login card when one comes (expect_card True / False asserts whether it did)."""
        c1 = await self.step1()
        self.assertEqual(c1["why"], "passkey_required", c1)
        out = await self.step2(c1, prf=prf)
        self.assertEqual(out[-1]["t"], "ctl_pending", out)
        carded = False

        def end():
            return next((o for o in self.sent_after(c1) if o.get("t") == "term_ready" or (
                o.get("t") == "ctl_res" and o.get("action") == "term_open" and o.get("why") != "passkey_required")), None)

        def card():
            return next((o for o in self.sent_after(c1) if o.get("t") == "elev" and o.get("kind") == "login"
                         and o.get("term") == R and o["n"] not in self.used), None)
        while True:
            got = await self.until(lambda: end() or card(), 30)
            if got.get("t") == "elev":
                self.used.add(got["n"])
                carded = True
                await self.answer_login(got, pw)
                continue
            if expect_card is not None:
                self.assertEqual(carded, expect_card)
            return got

    def sent_after(self, c1):
        i = next(k for k, (_, o) in enumerate(self.sent) if o is c1)
        return [o for _, o in self.sent[i + 1:]]

    async def line(self, text, want=None, timeout=10):
        x = self.desk.term
        await self.until(lambda: x.at_prompt, timeout)
        n0 = len(self.sent)
        await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 1, "line": text})
        if want is None:
            return [o for _, o in self.sent[n0:]]
        await self.until(lambda: want in "".join(o.get("data", "") for _, o in self.sent[n0:] if o.get("t") == "term_out"), timeout)
        await self.until(lambda: x.at_prompt, timeout)
        return [o for _, o in self.sent[n0:]]

    def run_async(self, coro):
        self.used = set()

        async def go():
            try:
                return await coro()
            finally:
                await self.desk.close("owner")
        return asyncio.run(go())


# ------------------------------------------------------------ pure parts
class Masking(unittest.TestCase):
    TOK = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789AB"

    def test_token_split_across_two_chunks_with_a_pause(self):
        m = Masker()
        out = m.feed("export X=" + self.TOK[:12]) + m.flush()
        out += m.feed(self.TOK[12:] + " done\n") + m.close()
        self.assertNotIn(self.TOK[4:], out)
        self.assertNotIn(self.TOK[8:20], out)
        self.assertIn("done", out)

    def test_bearer_context_from_the_previous_chunk(self):
        m = Masker()
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0." + "q" * 30
        out = m.feed("Authorization: Bearer ") + m.feed(jwt + "\n") + m.close()
        self.assertNotIn(jwt, out)
        self.assertNotIn("q" * 30, out)

    def test_pem_without_end_hides_everything_after(self):
        m = Masker()
        out = m.feed("before\n-----BEGIN OPENSSH PRIV") + m.feed("ATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n")
        out += m.feed("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\nmore lines\n") + m.flush() + m.close()
        self.assertIn("before", out)
        self.assertIn("私钥已隐藏", out)
        self.assertNotIn("b3BlbnNz", out)
        self.assertNotIn("QUJDREVG", out)
        self.assertNotIn("more lines", out)

    def test_pem_with_end_shows_what_follows(self):
        m = Masker()
        out = m.feed("-----BEGIN RSA " + "PRIVATE KEY-----\nAAAA\n-----END RSA " + "PRIVATE KEY-----\nafter\n") + m.close()
        self.assertEqual(out, "[私钥已隐藏 / private key hidden]\nafter\n")

    def test_env_style_output(self):
        key = "sk-proj-" + "A1b2C3d4" * 6
        text = f"HOME=/srv/x\nOPENAI_API_KEY={key}\nSHELL=/bin/bash\n"
        for cut in (5, 20, 30, 44, 60):
            m = Masker()
            out = m.feed(text[:cut]) + m.flush() + m.feed(text[cut:]) + m.close()
            self.assertNotIn(key[8:], out, cut)
            self.assertIn("SHELL=/bin/bash", out)

    def test_ansi_and_osc_cut_by_a_read(self):
        m = Masker()
        out = m.feed("\x1b[3") + m.feed("2mgreen\x1b[0m ok\x1b]133;") + m.feed("A\x07$ ") + m.close()
        self.assertEqual(out, "green ok$ ")

    def test_a_key_printed_seven_chars_at_a_time_with_pauses(self):
        """R3 (M-2): after an idle flush released a short tail, the run stays open; the rest is held, then masked whole."""
        m = Masker()
        out = ""
        for i in range(0, len(self.TOK), 7):
            out += m.feed(self.TOK[i:i + 7]) + m.flush() + m.flush()
        out += m.feed(" done\n") + m.close()
        self.assertNotIn(self.TOK[:8], out)                    # at most a FLUSH_MAX-1 prefix was ever shown
        self.assertIn("已隐藏", out)
        self.assertTrue(out.endswith(" done\n"))
        m = Masker()                                             # the same at close, with no delimiter at all
        out = "".join(m.feed(self.TOK[i:i + 7]) + m.flush() for i in range(0, len(self.TOK), 7)) + m.close()
        self.assertNotIn(self.TOK[:8], out)
        m = Masker()                                             # a short counter still shows
        self.assertEqual(m.feed("50") + m.flush() + m.feed("%\n") + m.close(), "50%\n")

    def test_a_long_trailing_run_is_masked_at_close(self):
        m = Masker()
        out = m.feed("x " + "Z9" * 20) + m.flush() + m.close()
        self.assertNotIn("Z9Z9Z9", out)


class Lines(unittest.TestCase):
    def test_dangerous_lines(self):
        for ln in ("rm -rf ~/x", "git push --force", "curl x | sh", "cat ~/.ssh/id_ed25519", "python3", "source x.sh",
                   ". ./x", "eval $X", "exec bash", "bash run.sh", "node app.js", "ssh host", "su -", "sudo python3 a.py",
                   "FOO=1 perl -e 1", "echo 'unbalanced"):
            self.assertIsNotNone(classify(ln), ln)
            why, why_en = classify(ln)
            self.assertTrue(why and why_en, ln)

    def test_harmless_lines(self):
        for ln in ("echo hi", "ls -la", "python3 --version", "git status", "cd /tmp && pwd", "node --version"):
            self.assertIsNone(classify(ln), ln)

    def test_environment_is_scrubbed(self):
        base = {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C.UTF-8", "OPENAI_API_KEY": "x", "GH_TOKEN": "x", "MY_SECRET": "x",
                "DB_PASSWORD": "x", "AGENTJ_STATE_DIR": "x", "BASH_ENV": "x", "ENV": "x", "PROMPT_COMMAND": "x",
                "BASH_FUNC_x%%": "x", "LD_PRELOAD": "x", "LD_LIBRARY_PATH": "x", "DYLD_X": "x", "SVC_ONLY": "x",
                "CLAUDECODE": "1", "LD_AUDIT": "x", "LD_BIND_NOW": "1", "GCONV_PATH": "x"}
        env = shell_env(base, {"SVC_ONLY"})
        self.assertEqual(env, {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C.UTF-8"})


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cache_is_ciphertext_only_and_prf_needs_the_prf(self):
        prf, salt = os.urandom(32), os.urandom(32)
        term_store.save(self.st, "CH", "dev1", "cred", "prf", salt, bytearray(GOOD.encode()), prf)
        # P414: the cache is now the vault (term_vault.py: wrap-<device>.json + vault-<kid>.json), no login-<device>.bin
        files = [p for p in term_store.root(self.st).iterdir() if p.name.startswith(("wrap-", "vault-"))]
        self.assertEqual(sorted(p.name.split("-")[0] for p in files), ["vault", "wrap"])
        raw = b"".join(p.read_bytes() for p in files)
        self.assertNotIn(GOOD.encode(), raw)
        self.assertNotIn(wire.b64u(GOOD.encode()).encode()[:12], raw)
        for p in files:
            self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
        self.assertIsNone(term_store.load(self.st, "CH", "dev1", "cred", None))
        self.assertIsNone(term_store.load(self.st, "CH", "dev1", "cred", os.urandom(32)))
        self.assertIsNone(term_store.load(self.st, "CH", "dev1", "other-cred", prf))
        self.assertIsNone(term_store.load(self.st, "CH2", "dev1", "cred", prf))
        self.assertEqual(bytes(term_store.load(self.st, "CH", "dev1", "cred", prf)), GOOD.encode())

    def test_local_mode_and_drop_local(self):
        term_store.save(self.st, "CH", "dev1", "cred", "local", os.urandom(32), bytearray(GOOD.encode()), None)
        self.assertEqual(bytes(term_store.load(self.st, "CH", "dev1", "cred", None)), GOOD.encode())
        term_store.save(self.st, "CH", "dev2", "cred", "prf", os.urandom(32), bytearray(GOOD.encode()), os.urandom(32))
        self.assertEqual(term_store.drop_local(self.st), 1)
        self.assertIsNone(term_store.meta(self.st, "dev1"))
        self.assertIsNotNone(term_store.meta(self.st, "dev2"))

    def test_locks(self):
        t = 1_000_000.0
        self.assertEqual(term_store.note(self.st, "d", False, t), 0)
        self.assertEqual(term_store.note(self.st, "d", False, t), 0)
        self.assertEqual(term_store.note(self.st, "d", False, t), term_store.DEV_LOCK)
        self.assertGreater(term_store.locked_for(self.st, "d", t + 10), 0)
        self.assertEqual(term_store.locked_for(self.st, "d", t + term_store.DEV_LOCK + 1), 0)
        for i in range(10):                                   # host-wide: 10 in an hour, spread over devices
            term_store.note(self.st, f"e{i}", False, t + 2000 + i)
        self.assertGreater(term_store.locked_for(self.st, "zz", t + 2100), 3000)


class Askpass(unittest.TestCase):
    """check_peer against a fake /proc tree (the real sudo is euid 0 — a test cannot be its child)."""

    def setUp(self):
        self.tmp = tmpdir()
        base = pathlib.Path(self.tmp.name)
        self.proc = base / "proc"
        (base / "t").mkdir()
        self.helper = term_askpass.Helper(base / "t", base / "t" / "askpass.sock", sys.executable)
        self.shell = 500

    def tearDown(self):
        self.tmp.cleanup()

    def mk(self, pid, ppid, sid, name, euid, argv):
        d = self.proc / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "stat").write_text(f"{pid} ({name}) S {ppid} {sid} {sid} 0 -1 4194560")
        (d / "status").write_text(f"Name:\t{name}\nUid:\t{os.getuid()}\t{euid}\t{euid}\t{euid}\n")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def tree(self, *, parent_name="sudo", parent_euid=0, sid=500, argv=None):
        self.mk(600, 500, 500, parent_name, parent_euid, ["/usr/bin/sudo", "-A", "true"])
        self.mk(601, 600, sid, "python3", os.getuid(), argv or [sys.executable, "-I", str(self.helper.path), "[sudo] pw"])

    def check(self, token="tok", uid=None):
        return term_askpass.check_peer(601, os.getuid() if uid is None else uid, token, "tok", self.shell, self.helper,
                                       str(self.proc))

    def test_sudo_child_in_the_session_passes(self):
        self.tree()
        self.assertEqual(self.check(), (None, 600))

    def test_refusals(self):
        self.tree(parent_name="bash")
        self.assertEqual(self.check()[0], "parent")              # a non-sudo caller
        self.tree(parent_euid=os.getuid() or 1000)
        self.assertEqual(self.check()[0], "parent")              # a user-owned process named sudo
        self.tree()
        self.assertEqual(self.check(token="wrong")[0], "token")
        self.assertEqual(self.check(uid=os.getuid() + 1)[0], "uid")
        self.tree(sid=999)
        self.assertEqual(self.check()[0], "session")
        self.tree(argv=[sys.executable, "-I", "/elsewhere/x.py"])
        self.assertEqual(self.check()[0], "helper")

    def test_tampered_helper_file(self):
        self.tree()
        with open(self.helper.path, "a") as f:
            f.write("\n# changed\n")
        self.assertEqual(self.check()[0], "file")

    def test_ctime_change_alone_fails(self):
        self.tree()
        os.chmod(self.helper.path, 0o700)                         # same bytes, same mtime: only ctime moves
        self.assertEqual(self.check()[0], "file")

    def test_interpreter_choice_refuses_a_user_owned_one(self):
        fake = pathlib.Path(self.tmp.name) / "python3"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        self.assertIsNone(term_askpass.python_ok(str(fake)))
        self.assertIsNone(term_askpass.safe_python([str(fake)]))

    def test_a_planted_pth_in_a_venv_does_not_run_in_the_helper(self):
        real = term_askpass.safe_python()
        if not real:
            self.skipTest("no system python3 on this machine")
        base = pathlib.Path(self.tmp.name)
        venv, marker = base / "venv", base / "pwned"
        ver = subprocess.run([real, "-IS", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                             text=True).stdout.strip()
        sp = venv / "lib" / f"python{ver}" / "site-packages"
        sp.mkdir(parents=True)
        (venv / "bin").mkdir()
        (venv / "bin" / "python").symlink_to(real)
        (venv / "pyvenv.cfg").write_text(f"home = {os.path.dirname(real)}\n")
        (sp / "evil.pth").write_text(f"import os; open({str(marker)!r}, 'w').close()\n")
        subprocess.run([str(venv / "bin" / "python"), "-I", "-c", "pass"], capture_output=True, timeout=20)
        planted_runs = marker.exists()                           # the attack the fix closes: -I alone still runs .pth
        with contextlib.suppress(FileNotFoundError):
            marker.unlink()
        self.assertEqual(term_askpass.python_ok(str(venv / "bin" / "python")), real)   # the real path, never the venv's
        for py in (str(venv / "bin" / "python"), real):
            h = term_askpass.Helper(base / "t", base / "t" / "nosock", py)
            self.assertTrue(h.path.read_text().startswith(f"#!{py} -IS\n"))
            r = subprocess.run([str(h.path), "[sudo] pw"], capture_output=True, timeout=20, env={})
            self.assertEqual(r.returncode, 1)                    # no socket: refused, nothing printed
            self.assertFalse(marker.exists(), py)
        if not planted_runs:
            self.skipTest("this python does not process venv .pth under -I (the negative part still passed)")

    @unittest.skipUnless(os.environ.get("AGENTJ_P408_REAL_SUDO") == "1", "L1: set AGENTJ_P408_REAL_SUDO=1")
    def test_l1_proc_shape_of_this_process_tree(self):
        """The /proc reader on the real tree (no sudo is run): ppid, sid, name, euid, argv of this process."""
        me = term_askpass.proc_info(os.getpid())
        self.assertEqual(me["ppid"], os.getppid())
        self.assertEqual(me["sid"], os.getsid(0))
        self.assertEqual(me["euid"], os.geteuid())
        self.assertTrue(me["name"].startswith("python"))
        self.assertTrue(me["argv"])
        self.assertIsNotNone(term_askpass.proc_info(os.getppid()))


class SecretCards(unittest.TestCase):
    def test_an_agent_cannot_ask_for_the_login_password(self):
        st = pathlib.Path(tempfile.gettempdir())
        for name, purpose in (("SUDO_PASSWORD", "x"), ("LOGIN_PASSWORD", "x"), ("MAC_PW", "x"), ("X", "电脑登录密码"),
                              ("X", "your computer password"), ("X", "the sudo password for apt"), ("ROOT_PASS", "x"),
                              ("X", "your mac login password")):
            with self.assertRaises(elevate.Refused) as e:
                elevate.norm_secret({"name": name, "purpose": purpose, "dest": "env:~/.env"}, st)
            self.assertEqual(e.exception.why, "login_password", name)
        for name, purpose in (("SMTP_PASSWORD", "邮箱授权码"), ("DB_PASSWORD", "postgres"), ("MAIL_APP_PASSWORD", "gmail app password"),
                              ("OS_PASSWORD", "openstack"), ("ADMIN_PASSWORD", "the CMS admin"),
                              ("MYSQL_ROOT_PASSWORD", "the database"), ("DB_SYSTEM_PASSWORD", "x"),       # R3 (M-3)
                              ("WP_ADMIN_PW", "WordPress 后台管理员密码"), ("X", "路由器管理员密码"),
                              ("X", "Gmail login password"), ("X", "Synology NAS login password"), ("USER_PASSWORD", "the app user")):   # R4
            self.assertFalse(elevate.system_password_ask(name, purpose), name)

    def test_login_kind(self):
        self.assertIn("login", elevate.KINDS)
        self.assertEqual(elevate.shown_fields({"kind": "login", "term": R}), [R])


class Parser(unittest.TestCase):
    def root_parser(self):
        """The AST-extracted root parser, exactly as site/generate-cli.py builds it."""
        import ast
        from agentj import cli
        tree = ast.parse(pathlib.Path(cli.__file__).read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        start = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "p" for t in n.targets))
        end = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "parse_args")
        ns = dict(vars(cli))
        exec(compile(ast.Module(body=main.body[start:end], type_ignores=[]), str(cli.__file__), "exec"), ns)
        return ns["p"]

    def test_root_parser_builds_terminal(self):
        p = self.root_parser()
        a = p.parse_args(["terminal", "enable", "--device", "abc", "--minutes", "20", "--allow-web"])
        self.assertIs(a.fn, term_cli.cmd_terminal)
        self.assertEqual((a.action, a.device, a.minutes, a.allow_web), ("enable", ["abc"], 20, True))
        self.assertEqual(p.parse_args(["terminal", "allow", "dev", "--hours", "3"]).hours, 3)
        for op in term_cli.OPS:
            self.assertEqual(p.parse_args(["terminal", op]).action, op)
        src = "".join(f.read_text() for f in (AJ / "host/agentj").glob("*.py"))
        self.assertEqual(src.count('add_parser("terminal"'), 1)

    def test_enable_refused_inside_an_agent(self):
        tmp = tmpdir()
        self.addCleanup(tmp.cleanup)
        st = _state(tmp.name)
        a = self.root_parser().parse_args(["terminal", "enable"])
        with mock.patch("agentj.state.State", return_value=st), mock.patch.object(terminal, "platform_ok", return_value=True), \
                mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), mock.patch("sys.stdin.isatty", return_value=True):
            with self.assertRaises(SystemExit) as e:
                term_cli.cmd_terminal(a)
        self.assertIn("Agent", str(e.exception.code))


class Writer:
    def __init__(self):
        self.out = []

    def write(self, b):
        self.out.append(json.loads(b))

    async def drain(self):
        pass


# ------------------------------------------------------------ serve-side flows
class Gate(Rig):
    def test_narrowing_without_a_claim_widening_needs_claim_and_tty(self):
        async def go():
            w = Writer()
            self.assertTrue(await self.host._ctl_gate(w, {"cmd": "terminal_cfg", "op": "disable"}, 4242))
            self.assertFalse(await self.host._ctl_gate(w, {"cmd": "terminal_cfg", "op": "enable"}, 4242))
            self.assertEqual(w.out[-1]["error"], "claim_required")
            self.host.ctl_claims = {"tok": (4242, time.monotonic() + 30)}
            with mock.patch.object(ctl_auth, "has_tty", return_value=False):
                self.assertFalse(await self.host._ctl_gate(w, {"cmd": "terminal_cfg", "op": "enable", "claim": "tok"}, 4242))
            self.assertEqual(w.out[-1].get("why"), "terminal")
            self.host.ctl_claims = {"tok": (4242, time.monotonic() + 30)}
            with mock.patch.object(ctl_auth, "has_tty", return_value=True):
                self.assertTrue(await self.host._ctl_gate(w, {"cmd": "terminal_cfg", "op": "allow", "claim": "tok"}, 4242))
        asyncio.run(go())
        self.assertIn("terminal_cfg", ctl_auth.SENSITIVE)
        from agentj import approval_level
        self.assertIn(terminal.TOOL, approval_level.HOST_CARDS)
        self.assertTrue(approval_level.host_card(terminal.TOOL))

    def test_enable_card_needs_a_passkey_remote(self):
        other = Phone(self.st, name="旧手机")
        s2 = _ready(self.host, other, cid=12)
        self.passkey()

        async def go():
            task = asyncio.create_task(term_cli.ctl_cfg(self.desk, {"op": "enable", "code": "123456", "devices": [self.phone.did],
                                                                    "minutes": 20}))
            ask = await self.until(lambda: next((o for o in self.msgs("ask", cid=self.s.cid) if o["tool"] == terminal.TOOL), None))
            self.assertTrue(ask["passkey_required"])
            self.assertIn("123 456", ask["summary"])
            self.assertIn("20", ask["summary"])
            await self.host._answer(s2, other.answer(ask, True))   # no passkey: cannot allow
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            await self.host._answer(self.s, {**self.phone.answer(ask, True), "fa": FA})
            return await task
        res = asyncio.run(go())
        self.assertTrue(res["ok"], res)
        cfg = term_store.read_config(self.st)
        self.assertTrue(cfg["on"])
        self.assertEqual([e["device"] for e in cfg["entries"]], [self.phone.did])
        self.assertEqual(cfg["minutes"], 20)

    def test_host_info_block(self):
        self.passkey()
        info = self.host.preferences_msg(self.s)["host"]["terminal"]
        self.assertEqual(info, {"on": False, "os": True, "me": False, "sudo": None, "web_ok": False, "minutes": 15,
                                "idle_s": 300, "open": False, "pk": True, "kind": "unknown"})
        self.enable(until=int(time.time()) + 3600)
        info = self.host.preferences_msg(self.s)["host"]["terminal"]
        self.assertTrue(info["on"] and info["me"] and info["web_ok"])
        self.assertIn("until", info)

    def test_host_info_pk_flag(self):
        """P417 #185: the row says 「先设置 Face ID」 when this device has no passkey on the computer."""
        info = self.host.preferences_msg(self.s)["host"]["terminal"]
        self.assertIs(info["pk"], False)
        self.passkey()
        self.assertIs(self.host.preferences_msg(self.s)["host"]["terminal"]["pk"], True)
        self.assertIs(self.host.term_desk.info(None)["pk"], False)


class Open(Rig):
    def test_refusals_before_anything_runs(self):
        async def go():
            self.assertEqual((await self.step1())["why"], "disabled")
            self.passkey()
            term_store.write_config(self.st, {"on": True, "entries": [], "minutes": 15})
            self.assertEqual((await self.step1())["why"], "device_not_allowed")
            stranger = serve_session(self.host, os.urandom(32), cid=30)   # an unpaired device
            self.assertEqual((await self.step1(stranger))["why"], "device_not_allowed")
            self.enable()
            d = self.st.devices()
            d[self.phone.did].pop("pk", None)
            self.st.write_private(self.st.devices_path, json.dumps(d).encode())
            self.assertEqual((await self.step1())["why"], "no_passkey")
            self.assertEqual(term_store.read_config(self.st)["entries"], [])
            self.passkey()
            self.enable(web=False)
            d = self.st.devices()
            d[self.phone.did]["rt"] = {"i": "x"}
            self.st.write_private(self.st.devices_path, json.dumps(d).encode())
            self.assertEqual((await self.step1())["why"], "web_not_allowed")
            self.enable(web=True)
            self.desk.os_ok = False
            self.assertEqual((await self.step1())["why"], "os")
        asyncio.run(go())
        self.assertIsNone(self.desk.term)

    def test_step_two_checks(self):
        self.passkey()
        self.enable()

        async def go():
            c1 = await self.step1()
            self.assertEqual((c1["fa"], len(c1["n"])), (FA["id"], 32))
            want = passkey.elevate_challenge(self.host.channel, self.phone.did, c1["n"], "term_open", c1["n"],
                                             controls.object_digest("term_open", {"mode": "line"}))
            self.assertEqual(c1["fa_ch"], wire.b64u(want))
            self.assertEqual(len(wire.unb64u(c1["salt"])), 32)
            out = await self.step2(c1, fa=None)                  # missing assertion
            self.assertEqual(out[-1]["why"], "passkey_failed")
            out = await self.step2(c1)                           # the nonce was used: one use only
            self.assertEqual(out[-1]["why"], "replay")
            c1 = await self.step1()
            with mock.patch.object(passkey, "verify_assertion", side_effect=passkey.PasskeyError("bad", "sig")):
                self.assertEqual((await self.step2(c1))[-1]["why"], "passkey_failed")
            c1 = await self.step1()
            self.assertEqual((await self.step2(c1, ts=int(time.time() * 1000) - 600_000))[-1]["why"], "stale")
            c1 = await self.step1()
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            bad = signed(self.phone, "term_open", {"mode": "line"}, c1["n"], key=Ed25519PrivateKey.generate())
            self.assertEqual((await self.step2(c1, **bad))[-1]["why"], "bad_signature")
            c1 = await self.step1()
            self.assertEqual((await self.step2(c1, n="f" * 32))[-1]["why"], "replay")   # a nonce the host never issued
        asyncio.run(go())
        self.assertIsNone(self.desk.term)
        self.assertEqual(self.msgs("elev"), [])
        log = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        self.assertTrue(any(r["result"] == "refused:passkey_failed" for r in log))

    def test_echo_runs_and_the_password_is_nowhere(self):
        self.passkey()
        self.enable()
        other = Phone(self.st, name="另一台")
        _ready(self.host, other, cid=12).p33 = True

        async def go():
            ready = await self.open()
            self.assertEqual(ready["t"], "term_ready", ready)
            self.assertEqual(ready["sudo"], "local")             # fenced + independent, no PRF
            self.assertEqual(len(ready["id"]), 16)
            x = self.desk.term
            env = pathlib.Path(f"/proc/{x.proc.pid}/environ").read_bytes()
            cmd = pathlib.Path(f"/proc/{x.proc.pid}/cmdline").read_bytes()
            out = await self.line("echo hi", "hi")
            self.assertTrue(any(o.get("prompt") for o in out if o["t"] == "term_out"))
            return env, cmd
        env, cmd = self.run_async(go)
        self.assertEqual(self.msgs("elev", cid=12), [])         # the login card: the opening device only
        self.assertTrue(self.msgs("elev", cid=self.s.cid))
        closes = self.msgs("term_close")
        self.assertEqual(closes[-1]["why"], "owner")
        blob = json.dumps([o for _, o in self.sent], ensure_ascii=False).encode()
        files = b"".join(p.read_bytes() for p in self.st.root.rglob("*") if p.is_file())
        for hay in (blob, files, env, cmd):
            self.assertNotIn(GOOD.encode(), hay)
            self.assertNotIn(wire.b64u(GOOD.encode()).encode(), hay)
        casts = list((term_store.root(self.st) / "casts").glob("*.cast"))
        self.assertEqual(len(casts), 1)
        self.assertEqual(oct(casts[0].stat().st_mode & 0o777), "0o600")
        lines = [json.loads(x) for x in casts[0].read_text().splitlines()]
        self.assertEqual(lines[0]["version"], 2)
        self.assertIn(["i", "echo hi\n"], [ev[1:] for ev in lines[1:]])
        self.assertIn(b"AGENTJ_TERM_TOKEN", env)
        self.assertNotIn(b"AGENTJ_STATE_DIR", env)
        log = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        self.assertTrue(any(r["action"] == "term_close" for r in log))

    def test_dangerous_line_waits_for_a_signed_asserted_resend(self):
        self.passkey()
        self.enable()
        victim = self.home / "p408-x"
        victim.mkdir()

        async def go():
            self.assertTrue(os.environ["HOME"].startswith(self.tmp.name))
            await self.open()
            x = self.desk.term
            out = await self.line("rm -rf ~/p408-x")
            conf = next(o for o in out if o["t"] == "term_confirm")
            self.assertEqual(conf["line"], "rm -rf ~/p408-x")
            self.assertEqual(conf["fa_ch"], wire.b64u(passkey.elevate_challenge(
                self.host.channel, self.phone.did, x.id, "term_line", conf["n"], hashlib.sha256(b"rm -rf ~/p408-x").hexdigest())))
            await asyncio.sleep(0.5)
            self.assertTrue(victim.exists())                     # not run
            base = {"t": "term_line", "id": x.id, "seq": 2, "line": "rm -rf ~/p408-x"}
            await self.desk.on_phone(self.s, {**base, **signed(self.phone, "term_line", {"id": x.id, "line": "rm -rf ~/p408-x"},
                                                               conf["n"])})            # no assertion
            await asyncio.sleep(0.3)
            self.assertTrue(victim.exists())
            await self.desk.on_phone(self.s, {**base, **signed(self.phone, "term_line", {"id": x.id, "line": "rm -rf ~/p408-y"},
                                                               conf["n"]), "fa": FA})  # signed for another line
            await asyncio.sleep(0.3)
            self.assertTrue(victim.exists())
            out = await self.line("rm -rf ~/p408-x")
            conf = next(o for o in out if o["t"] == "term_confirm")
            await self.desk.on_phone(self.s, {**base, **signed(self.phone, "term_line", {"id": x.id, "line": "rm -rf ~/p408-x"},
                                                               conf["n"]), "fa": FA})
            await self.until(lambda: not victim.exists())
        self.run_async(go)

    def test_prf_mode_and_reopen_without_the_card(self):
        self.passkey()
        self.enable()
        prf = os.urandom(32)

        async def go():
            ready = await self.open(prf=prf)
            self.assertEqual(ready["sudo"], "prf")
            await self.desk.close("owner")
            m = term_store.meta(self.st, self.phone.did)
            self.assertEqual(m["mode"], "prf")
            self.assertIsNone(term_store.load(self.st, self.host.channel, self.phone.did, FA["id"], None))
            n_cards = len(self.msgs("elev"))
            ready = await self.open(prf=prf, expect_card=False)  # the cache opens with the PRF: no card
            self.assertEqual((ready["t"], ready["sudo"]), ("term_ready", "prf"))
            self.assertEqual(len(self.msgs("elev")), n_cards)
            await self.desk.close("owner")
            ready = await self.open()                            # no PRF this time: the card again, then local
            self.assertEqual(ready["sudo"], "local")
        self.run_async(go)

    def test_local_refused_when_not_fenced_and_temporary_is_session(self):
        self.passkey()
        self.enable()
        term_store.save(self.st, self.host.channel, self.phone.did, FA["id"], "local", os.urandom(32), bytearray(GOOD.encode()), None)
        preferences.put(self.host.preferences, "agent.isolation", False)
        self.desk.config_applied()
        self.assertIsNone(term_store.meta(self.st, self.phone.did))

        async def go():
            ready = await self.open()
            self.assertEqual(ready["sudo"], "session")
            self.assertIsNone(term_store.meta(self.st, self.phone.did))
            self.assertEqual(self.host.preferences_msg(self.s)["host"]["terminal"]["sudo"], "session")
            await self.desk.close("owner")
            preferences.put(self.host.preferences, "agent.isolation", True)
            self.host.agent_cfg["session_mode"] = "shared"
            ready = await self.open()
            self.assertEqual(ready["sudo"], "session")
            await self.desk.close("owner")
            self.host.agent_cfg["session_mode"] = "independent"
            self.enable(until=int(time.time()) + 3600)
            ready = await self.open()
            self.assertEqual(ready["sudo"], "session")
            self.assertIsNone(term_store.meta(self.st, self.phone.did))
        self.run_async(go)

    def test_three_wrong_passwords_lock(self):
        self.passkey()
        self.enable()

        async def go():
            c1 = await self.step1()
            await self.step2(c1)
            for _ in range(3):
                card = await self.until(lambda: next((o for o in self.msgs("elev") if o["n"] not in self.used), None))
                self.used.add(card["n"])
                await self.answer_login(card, BAD)
            res = await self.until(lambda: next((o for o in self.sent_after(c1) if o.get("t") == "ctl_res"
                                                 and o.get("why") not in (None, "passkey_required")), None))
            self.assertEqual(res["why"], "locked")
            self.assertGreater(res["secs"], 0)
            self.assertEqual((await self.step1())["why"], "locked")
        self.run_async(go)
        self.assertIsNone(self.desk.term)
        self.assertGreater(term_store.locked_for(self.st, self.phone.did), 800)

    def test_busy_estop_relay_lost_timeout_idle(self):
        self.passkey()
        other = Phone(self.st, name="另一台")
        s2 = _ready(self.host, other, cid=12)
        s2.p33 = True
        self.passkey(other)
        self.enable(self.phone, other)
        clock = [1000.0]
        self.desk.clock = lambda: clock[0]

        async def closed_with():
            return (await self.until(lambda: self.desk.term is None and self.msgs("term_close")))[-1]["why"]

        async def go():
            await self.open()
            self.assertEqual((await self.step1(s2))["why"], "busy")
            clock[0] += 15 * 60 + 1
            await self.desk.tick()
            self.assertEqual(await closed_with(), "timeout")
            await self.open()
            clock[0] += 301
            await self.desk.tick()
            self.assertEqual(await closed_with(), "idle")
            await self.open()
            self.host._detach(self.s.cid)
            self.assertEqual(await closed_with(), "relay_lost")
            self.s = _ready(self.host, self.phone, cid=13)
            self.s.p33 = True
            await self.open()
            await self.host.do_estop("terminal", "终端")
            self.assertEqual(await closed_with(), "estop")
            n0 = len(self.sent)
            await self.host._app(self.s, {"t": "term_open", "r": R})
            self.assertEqual([o for _, o in self.sent[n0:] if o["t"] == "ctl_res"][-1]["why"], "estop")
        self.run_async(go)

    def test_sudo_through_askpass_and_a_refused_cache(self):
        self.passkey()
        self.enable()

        async def go():
            await self.open()
            x = self.desk.term
            with mock.patch.object(term_askpass, "check_peer", return_value=(None, 77)), \
                    mock.patch.object(term_askpass, "proc_info", return_value={"argv": ["/usr/bin/sudo", "-A", "true"]}):
                n0 = len(self.sent)
                await self.until(lambda: x.at_prompt)
                await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 1, "line": "sudo true && echo sudo-ok"})
                ask = await self.until(lambda: next((o for _, o in self.sent[n0:] if o["t"] == "term_sudo"), None))
                self.assertEqual(ask["argv"], ["/usr/bin/sudo", "-A", "true"])
                self.assertFalse(ask["differs"])
                obj = {"t": "term_sudo_ok", "id": x.id, "rid": ask["rid"],
                       **signed(self.phone, "term_sudo", {"id": x.id, "rid": ask["rid"]}, ask["n"]), "fa": FA}
                await self.desk.on_phone(self.s, obj)
                await self.until(lambda: "sudo-ok" in "".join(o.get("data", "") for _, o in self.sent[n0:] if o["t"] == "term_out"))
            # a second askpass call from the same sudo: the cache goes only after sudo's own message (N4)
            x.seen[99] = 1
            x.recent = "nothing"
            self.assertIsNone(await self.desk._sudo_pw(x, 99))
            self.assertIsNotNone(term_store.meta(self.st, self.phone.did))
            x.seen[98] = 1
            x.recent = "Sorry, try again.\n"
            self.assertIsNone(await self.desk._sudo_pw(x, 98))
            self.assertIsNone(term_store.meta(self.st, self.phone.did))
        self.run_async(go)
        blob = json.dumps([o for _, o in self.sent], ensure_ascii=False).encode()
        files = b"".join(p.read_bytes() for p in self.st.root.rglob("*") if p.is_file())
        self.assertNotIn(GOOD.encode(), blob + files)

    def test_r3_socket_stdio_sudo_k_each_time_and_a_forged_marker(self):
        """R3: M-1 /proc/<bash>/fd/0 cannot be reopened (a socket) · H-2 two sudo = two cards, `-k` passed each time ·
        LOW a program printing the bare OSC 133 marker is not "at the prompt"."""
        self.passkey()
        self.enable()
        if not term_askpass.safe_python():
            self.skipTest("no system python3 on this machine")

        async def go():
            await self.open()
            x = self.desk.term
            for fd in (0, 1, 2):
                with self.assertRaises(OSError) as e:
                    open(f"/proc/{x.proc.pid}/fd/{fd}", "rb").close()
                self.assertEqual(e.exception.errno, errno.ENXIO)
            self.assertNotIn(x.prompt[8:-1].encode(), pathlib.Path(f"/proc/{x.proc.pid}/environ").read_bytes())
            await self.until(lambda: x.at_prompt)
            await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 1, "line": "printf '\\033]133;A\\007'; sleep 1"})
            await asyncio.sleep(0.6)
            self.assertFalse(x.at_prompt)
            await self.until(lambda: x.at_prompt)
            with mock.patch.object(term_askpass, "check_peer", return_value=(None, 77)), \
                    mock.patch.object(term_askpass, "proc_info", return_value={"argv": ["/usr/bin/sudo", "-k", "-A", "true"]}):
                n0 = len(self.sent)
                await self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 2, "line": "sudo true; sudo true; echo two-ok"})
                for k in range(2):
                    ask = await self.until(lambda: [o for _, o in self.sent[n0:] if o["t"] == "term_sudo"][k:k + 1])
                    ask = ask[0]
                    x.seen.clear()                               # the stand-in pid is the same for both calls
                    await self.desk.on_phone(self.s, {"t": "term_sudo_ok", "id": x.id, "rid": ask["rid"], "fa": FA,
                                                      **signed(self.phone, "term_sudo", {"id": x.id, "rid": ask["rid"]}, ask["n"])})
                await self.until(lambda: "two-ok" in "".join(o.get("data", "") for _, o in self.sent[n0:] if o["t"] == "term_out"))
                self.assertEqual(len([o for _, o in self.sent[n0:] if o["t"] == "term_sudo"]), 2)
        self.run_async(go)
        calls = [ln for ln in (pathlib.Path(self.desk.sudo).parent / "args.log").read_text().splitlines() if "-A" in ln]
        self.assertEqual(calls, ["-k -A true", "-k -A true"])

    def test_no_system_python_means_no_askpass(self):
        self.passkey()
        self.enable()
        self.desk.python = ""

        async def go():
            await self.open()
            x = self.desk.term
            self.assertIsNone(x.helper)
            out = await self.line("sudo true; echo after", "after")
            self.assertFalse([o for o in out if o["t"] == "term_sudo"])
            self.assertIn("python3", "".join(o.get("data", "") for o in out if o["t"] == "term_out"))
        self.run_async(go)

    def test_output_rate_and_frames(self):
        self.passkey()

        async def go():
            from agentj.term_shell import Term
            clock = [0.0]
            self.desk.clock = lambda: clock[0]
            x = Term(self.s, R, "session", None, 15, self.desk.clock, time.time)
            n0 = len(self.sent)
            await self.desk._out(x, "a" * 20000, False)
            frames = [o for _, o in self.sent[n0:]]
            self.assertTrue(all(len(o["data"].encode()) <= 16 * 1024 for o in frames))
            self.assertEqual("".join(o["data"] for o in frames), "a" * 20000)
            await self.desk._out(x, "b" * 60000, False)           # over 64 KiB in this second: dropped
            clock[0] += 1.5
            await self.desk._out(x, "c", False)
            data = "".join(o["data"] for _, o in self.sent[n0:])
            self.assertIn("已省略 60000 字节", data)
            self.assertNotIn("b", data.replace("字节", ""))
        asyncio.run(go())


def serve_session(host, pub, cid):
    from agentj import serve
    s = serve.Session(cid=cid, state="ready", device=wire.device_id(pub), name="?", pub=pub)
    s.p33 = True
    host.sessions[cid] = s
    return s


class JsParity(unittest.TestCase):
    def test_js_signs_the_same_bytes(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        cases = [("term_open", {"mode": "line"}), ("term_line", {"id": "0123456789abcdef", "line": "rm -rf ~/x"}),
                 ("term_sudo", {"id": "0123456789abcdef", "rid": "fedcba9876543210"}), ("term_forget", {})]
        for f in ("protocol/wire.js", "web/public/proto/wire.js"):
            js = (f"import {{ controlMessage }} from {json.dumps((AJ / f).as_uri())};\nconst out = [];\n"
                  f"for (const [a, o] of {json.dumps(cases)}) out.push(Buffer.from(await controlMessage('CH', 'DEV', a, '{'a' * 32}', "
                  "1790000000123, o)).toString('hex'));\nconsole.log(JSON.stringify(out));")
            r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            want = [controls.signed_message("CH", "DEV", a, "a" * 32, 1790000000123, controls.object_digest(a, o)).hex()
                    for a, o in cases]
            self.assertEqual(json.loads(r.stdout), want, f)

    def test_js_login_card_digest(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        js = (f"import {{ elevateDigest, elevateFields }} from {json.dumps((AJ / 'protocol/wire.js').as_uri())};\n"
              f"console.log(await elevateDigest('login', elevateFields({{kind: 'login', term: '{R}'}})));")
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), elevate.shown_digest("login", [R]))


if __name__ == "__main__":
    unittest.main()
