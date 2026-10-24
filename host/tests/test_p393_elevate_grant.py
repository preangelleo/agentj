"""#116 (security-reviewed redesign): after one password approval on a paired phone WITH the card's toggle switched on (off by
default, signed into the decision), later commands of exactly the same format run for 10 minutes without a new card.

Allowlist only: read / inspect programs in read modes, the same option set, operands are absolute paths in root-only folders
that touch nothing sensitive. Every exploit from the review (tar checkpoint exec, rsync -e, sed 1e, cat root keys / sudoers /
another account's .env, docker run, pacman -U, systemctl start, lua / deno / java, rbash by bare name, an alias whose real
program has another name, a symlink to /etc/shadow) gets a card. The grant is memory only, bound to the approving device key
and a per-session random nonce, ends on expiry / revoke / unpair / estop / restart, and every auto-pass is one metadata line."""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import glob
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import elevate, elevate_grant, wire
from test_f17 import PW, fake_sudo, enroll, uv
from test_l1 import Phone, _host, _ready, _state

SYS_PATH = "/usr/bin:/bin"
GZIP = shutil.which("gzip", path=SYS_PATH)


def _gz_files() -> list[str]:
    """Two small root-owned .gz files in the SAME folder (a reader's grant is bound to the approved file's folder)."""
    by_dir: dict[str, list[str]] = {}
    for pat in ("/usr/share/man/man1/*.gz", "/usr/share/info/*.gz", "/usr/share/man/*/*.gz", "/usr/share/doc/*/*.gz"):
        for p in sorted(glob.glob(pat)):
            if elevate_grant.path_ok(p) and os.path.getsize(p) < 200_000 and elevate_grant.parse("gzip", ["-dc", p]):
                got = by_dir.setdefault(os.path.dirname(p), [])
                got.append(p)
                if len(got) == 2:
                    return got
    return []


GZ = _gz_files()
REAL = bool(GZIP) and elevate_grant.root_safe(os.path.realpath(GZIP)) and len(GZ) == 2


def answer(ph, card, ok, value=None, *, grant=None, sign_grant=True):
    """What the phone sends (protocol/wire.js): seal the value, sign the decision — with `grant:<hash>` when switched on."""
    rid, nonce, ts = card["id"], card["n"], int(time.time() * 1000)
    digest = elevate.shown_digest(card["kind"], elevate.shown_fields(card))
    m = {"t": "elev_answer", "id": rid, "ok": ok, "n": nonce, "ts": ts}
    ct_sha = None
    if ok:
        epk, blob = elevate.seal(wire.unb64u(card["epk"]), (value or "").encode(),
                                 elevate.seal_aad(ph.channel, ph.did, rid, card["kind"], nonce, digest))
        m["epk"], m["ct"] = wire.b64u(epk), wire.b64u(blob)
        ct_sha = hashlib.sha256(blob).hexdigest()
    if grant:
        m["grant"] = grant
    msg = elevate.signed_message(ph.channel, ph.did, rid, card["kind"], "allow" if ok else "deny", nonce, ts, digest, ct_sha,
                                 grant if sign_grant else None)
    m["sig"] = wire.b64u(ph.sk.sign(msg))
    if ok and hasattr(ph,"auth"):
        m["fa"] = uv(ph,card,digest)
    return m


class Rig:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.st, self.sent = _state(str(root)), []
        self.host = _host(self.st, self.sent)
        self.host.agent = types.SimpleNamespace(kind="claude", cur_send=None)
        self.ph = enroll(self.st, Phone(self.st))
        self.session = _ready(self.host, self.ph)
        self.host.elevate.sudo = str(fake_sudo(root, PW))
        self.host.elevate.grants.path = SYS_PATH
        self.notices = []
        real = self.host.agent_notice
        def notice(text):
            self.notices.append(text)
            real(text)
        self.host.agent_notice = notice
        self.seen = set()

    def req(self, argv, why="Read the system backup folder", timeout=30):
        return elevate.norm_sudo({"argv": argv, "why": why, "cwd": "/", "timeout": timeout})

    async def card(self, task):
        for _ in range(500):
            for _, obj in self.sent:
                if obj.get("t") == "elev" and obj["id"] not in self.seen:
                    self.seen.add(obj["id"])
                    return obj
            if task.done():
                return None
            await asyncio.sleep(.01)
        raise AssertionError("no card")

    async def approve(self, argv, *, toggle=True, ph=None, session=None, value=PW, timeout=30):
        """→ (card shown or None, result). A card is approved with the password; `toggle` = the owner switched it on."""
        task = asyncio.create_task(self.host.elevate.request(self.req(argv, timeout=timeout)))
        card = await self.card(task)
        if card is not None:
            h = (card.get("grant") or {}).get("h") if toggle else None
            await self.host._app(session or self.session, answer(ph or self.ph, card, True, value, grant=h))
        res = await asyncio.wait_for(task, 10)
        await asyncio.sleep(0)
        return card, res

    async def deny_next(self, argv):
        task = asyncio.create_task(self.host.elevate.request(self.req(argv)))
        card = await self.card(task)
        if card is not None:
            await self.host._app(self.session, answer(self.ph, card, False))
        return card, await asyncio.wait_for(task, 10)

    async def shows_card(self, argv):
        task = asyncio.create_task(self.host.elevate.request(self.req(argv)))
        card = await self.card(task)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return card

    def log(self):
        return elevate.read_log(self.st)

    def of(self, t):
        return [obj for _, obj in self.sent if obj.get("t") == t]


def run(fn):
    with tempfile.TemporaryDirectory() as folder:
        asyncio.run(fn(Rig(pathlib.Path(folder))))


A, B = (["gzip", "-dc", GZ[0]], ["gzip", "-dc", GZ[1]]) if REAL else (None, None)


@unittest.skipUnless(REAL, "needs a root-owned gzip and two root-owned .gz files")
class Grant(unittest.TestCase):
    def test_gzip_dc_then_another_file_auto_runs_with_record_and_audit(self):
        async def go(r):
            card, res = await r.approve(A)
            g = card["grant"]
            folder = os.path.dirname(GZ[0])
            self.assertEqual((g["secs"], g["prog"], g["fmt"], g["dir"]), (600, "gzip", f"gzip -c -d {folder}/*", folder))
            self.assertEqual((res["result"], res["code"]), ("done", 0))
            self.assertEqual(len(r.host.elevate.grants.grants), 1)
            on = [x for x in r.log() if x.get("grant_sha256")]
            self.assertEqual(on[-1]["grant_sha256"], g["h"], "the signed decision records the owner's choice")
            self.assertEqual(elevate.check_record(r.st, on[-1]), "ok", "and its signature covers it")
            r.sent.clear()
            card, res = await r.approve(B)
            self.assertIsNone(card, "same program, same options, another root-owned file: no card")
            self.assertEqual((res["result"], res["code"], res["auto"]), ("done", 0, True))
            self.assertEqual(r.of("elev_auto")[0]["exe"], "gzip")
            self.assertTrue(any("10" in n and "gzip" in n for n in r.notices), r.notices)
            line = [x for x in r.log() if x.get("reason") == "grant"]
            self.assertEqual(len(line), 1)
            self.assertEqual((line[0]["id"], line[0]["device"], line[0]["exe"]), (res["id"], r.ph.did, os.path.realpath(GZIP)))
            text = elevate.log_path(r.st).read_text() + json.dumps(r.sent) + json.dumps(r.notices)
            self.assertNotIn(PW, text)
            self.assertNotIn(GZ[1], elevate.log_path(r.st).read_text(), "never the command text")
            rec = [json.loads(x) for x in (r.root / "sudo-record.jsonl").read_text().splitlines()]
            self.assertEqual(rec[1]["argv"][6], os.path.realpath(GZIP), "the auto-run pins the resolved executable")
            card, _ = await r.approve(["gzip", "-cd", GZ[0]])
            self.assertIsNone(card, "-cd is the same option set as -dc")
        run(go)

    def test_other_option_set_gets_a_card(self):
        async def go(r):
            await r.approve(A)
            for argv in (["gzip", "-d", GZ[0]], ["gzip", "-dcv", GZ[0]], ["gzip", "--stdout", "-d", GZ[0]], ["zcat", GZ[0]]):
                with self.subTest(argv=argv):
                    card, res = await r.deny_next(argv)
                    self.assertIsNotNone(card)
                    self.assertEqual(res["result"], "denied")
        run(go)

    def test_toggle_off_by_default_means_no_grant(self):
        async def go(r):
            await r.approve(A, toggle=False)
            self.assertFalse(r.host.elevate.grants.grants)
            card, _ = await r.deny_next(B)
            self.assertIsNotNone(card)
        run(go)

    def test_grant_outside_the_signature_or_another_hash_is_refused(self):
        async def go(r):
            task = asyncio.create_task(r.host.elevate.request(r.req(A)))
            card = await r.card(task)
            h = card["grant"]["h"]
            await r.host._app(r.session, answer(r.ph, card, True, PW, grant=h, sign_grant=False))
            await r.host._app(r.session, answer(r.ph, card, True, PW, grant="0" * 64))
            await asyncio.sleep(.05)
            self.assertFalse(task.done(), "both refused: the card stays open")
            reasons = [x.get("reason") for x in r.log() if x.get("result") == "refused"]
            self.assertEqual(reasons, ["bad_signature", "shape"])
            await r.host._app(r.session, answer(r.ph, card, False))
            self.assertEqual((await asyncio.wait_for(task, 10))["result"], "denied")
            self.assertFalse(r.host.elevate.grants.grants)
        run(go)

    def test_auto_run_timeout_capped_by_the_window(self):
        async def go(r):
            now = [1000.0]
            r.host.elevate.grants.clock = lambda: now[0]
            await r.approve(A)
            now[0] += elevate_grant.GRANT_SECS - 100
            card, res = await r.approve(B, timeout=3600)
            self.assertIsNone(card)
            self.assertEqual([x for x in r.log() if x.get("reason") == "grant"][-1]["timeout"], 100)
            now[0] = 1000.0
            card, res = await r.approve(B, timeout=3600)
            self.assertEqual([x for x in r.log() if x.get("reason") == "grant"][-1]["timeout"], elevate_grant.MAX_AUTO_TIMEOUT)
        run(go)

    def test_expires_at_ten_minutes_and_use_does_not_extend(self):
        async def go(r):
            now = [1000.0]
            r.host.elevate.grants.clock = lambda: now[0]
            await r.approve(A)
            now[0] += elevate_grant.GRANT_SECS - 1
            card, _ = await r.approve(B)
            self.assertIsNone(card)
            now[0] += 1
            card, _ = await r.deny_next(B)
            self.assertIsNotNone(card, "at 600 s the window is over")
            self.assertTrue(any(x.get("result") == "grant_off" and x.get("reason") == "expired" for x in r.log()))
        run(go)

    def test_revoke_now_restart_estop_unpair(self):
        async def go(r):
            await r.approve(A)
            (g,) = r.host.elevate.grants.grants.values()
            pw = g.pw
            await r.host._app(r.session, {"t": "elev_revoke"})
            await asyncio.sleep(0)
            self.assertFalse(r.host.elevate.grants.grants)
            self.assertEqual(pw, bytearray(len(pw)), "the kept password copy is zeroed")
            self.assertEqual(r.of("elev_grants")[-1]["items"], [])
            await r.approve(A)
            r.host.elevate.cancel_all("stopped")
            self.assertFalse(r.host.elevate.grants.grants)
            await r.approve(A)
            await r.host.revoke(r.ph.did)
            self.assertFalse(r.host.elevate.grants.grants)
            reasons = {x.get("reason") for x in r.log() if x.get("result") == "grant_off"}
            self.assertLessEqual({"revoked", "stopped", "unpaired"}, reasons)
        run(go)

    def test_restart_clears(self):
        async def go(r):
            await r.approve(A)
            (g,) = r.host.elevate.grants.grants.values()
            await r.host.elevate.stop()
            self.assertEqual(g.pw, bytearray(len(g.pw)))
            self.assertFalse(elevate.Elevator(r.host).grants.grants, "memory only")
            self.assertNotIn(PW, "".join(p.read_text(errors="ignore") for p in r.st.root.rglob("*") if p.is_file()))
        run(go)

    def test_session_nonce_binding(self):
        async def go(r):
            await r.approve(A)
            agent = r.host.agent
            nonce = agent._aj_grant_nonce
            self.assertRegex(nonce, r"^[0-9a-f]{32}$")
            r.host.agent = types.SimpleNamespace(kind="claude", cur_send=None)   # a new Agent object (even at a reused id)
            card, _ = await r.deny_next(B)
            self.assertIsNotNone(card, "another Agent session")
            self.assertNotEqual(r.host.agent._aj_grant_nonce, nonce)
            r.host.agent = agent
            r.st.set_agent_session("claude", "another-conversation")
            card, _ = await r.deny_next(B)
            self.assertIsNotNone(card, "a new conversation of the same harness")
            class Slotted:
                __slots__ = ("kind",)
            s = Slotted()
            s.kind = "claude"
            self.assertIsNone(elevate_grant.scope_of(types.SimpleNamespace(agent=s, st=r.st)), "no nonce, no grant")
        run(go)

    def test_key_change_and_stored_password_rejected(self):
        async def go(r):
            await r.approve(A)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            new = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            r.st.pair_device(r.ph.pub, "手机", new)
            self.assertIsNotNone(await r.shows_card(B))
            self.assertFalse(r.host.elevate.grants.grants)
        run(go)

    def test_stored_password_rejected_falls_back_to_a_card(self):
        async def go(r):
            await r.approve(A)
            fake_sudo(r.root, "changed-password")
            card, _ = await r.deny_next(B)
            self.assertIsNotNone(card)
            self.assertTrue(any(x.get("reason") == "bad_password" and x.get("result") == "grant_off" for x in r.log()))
        run(go)

    def test_exploits_after_a_grant_still_get_a_card(self):
        async def go(r):
            await r.approve(A)
            for argv in (["gzip", "-dc", "/etc/shadow"], ["gzip", "-dc", "/root/backup.gz"], ["sh", "-c", "true"],
                         ["tar", "--checkpoint=1", "--checkpoint-action=exec=sh", "-cf", "/dev/null", "/etc/hosts"]):
                with self.subTest(argv=argv):
                    card = await r.shows_card(argv)
                    self.assertIsNotNone(card)
                    self.assertNotIn("grant", card, "no toggle for these")
        run(go)


class Klass(unittest.TestCase):
    """The allowlist itself: every exploit from the review is never a class (= a card every time)."""
    def k(self, argv, path=SYS_PATH):
        return elevate_grant.klass(argv, path)

    def test_review_exploits_have_no_class(self):
        home = "/home/someone-else/.env"
        cases = [
            ["tar", "--checkpoint=1", "--checkpoint-action=exec=sh", "-cf", "/dev/null", "/etc/hosts"],
            ["rsync", "-e", "sh -c id", "a", "b"], ["zip", "-TT", "sh -c id", "x.zip", "/etc/hosts"],
            ["sed", "-n", "1e id", "/etc/hosts"], ["cat", "/root/.ssh/id_ed25519"], ["cat", "/etc/sudoers"],
            ["cat", "/etc/shadow"], ["cat", "/etc/gshadow"], ["cat", "/proc/1/environ"], ["cat", home],
            ["cat", "/etc/ssl/private/x.pem"], ["head", "/var/backups/app.env"], ["docker", "run", "-v", "/:/h", "alpine"],
            ["pacman", "-U", "/tmp/x.pkg"], ["systemctl", "start", "x"], ["systemctl", "restart", "sshd"],
            ["systemctl", "link", "/home/x/evil.service"], ["lua", "-e", "os.execute('id')"], ["lua5.4", "-e", "x"],
            ["deno", "eval", "1"], ["java", "-jar", "x"], ["sqlite3", "x", ".shell id"], ["psql", "-c", "\\! id"],
            ["socat", "exec:sh", "-"], ["setpriv", "sh"], ["chrt", "1", "sh"], ["prlimit", "sh"], ["bwrap", "sh"],
            ["capsh", "--", "-c", "id"], ["ip", "netns", "exec", "x", "sh"], ["busctl", "call"], ["dbus-send", "--system"],
            ["mise", "exec", "--", "sh"], ["less", "/etc/hosts"], ["find", "/", "-exec", "sh", ";"], ["env", "sh"],
            ["xargs", "sh"], ["awk", "BEGIN{system(\"id\")}"], ["cp", "/etc/hosts", "/tmp/x"], ["tee", "/etc/x"],
            ["dd", "if=/dev/sda"], ["chmod", "4755", "/bin/bash"], ["gzip", "-d", "/usr/share/man/man1/ls.1.gz"],
            ["tail", "-f", "/var/log/pacman.log"], ["dmesg", "-c"], ["ss", "-K"], ["grep", "-r", "x", "/etc"],
            ["grep", "-f", "/etc/shadow", "/etc/hosts"], ["journalctl", "--file=/x"], ["cat", "-"], ["cat", "relative.txt"],
            ["cat", "/etc/../etc/shadow"], ["head", "-20", "/etc/hosts"], ["file", "-m", "/x", "/etc/hosts"],
            ["rbash"], ["ruby3.3", "-e", "x"], ["bun", "x"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertIsNone(self.k(argv))

    def test_interpreters_by_resolved_name_are_red(self):
        for name in ("rbash", "lua", "lua5.4", "luajit", "ruby", "ruby3.3", "deno", "bun", "java", "mise", "asdf", "php8"):
            with self.subTest(name=name):
                self.assertTrue(elevate.interpreter_command([name]))
        bash = shutil.which("bash", path=SYS_PATH)
        if bash:
            with tempfile.TemporaryDirectory() as d:
                os.symlink(bash, os.path.join(d, "innocent"))
                with mock.patch.dict(os.environ, {"PATH": d + os.pathsep + SYS_PATH}):
                    self.assertTrue(elevate.interpreter_command(["innocent"]), "a bare name is judged by what PATH runs")

    @unittest.skipUnless(REAL, "needs a root-owned gzip")
    def test_alias_with_another_real_name_has_no_class(self):
        with tempfile.TemporaryDirectory() as d:
            os.symlink(os.path.realpath(GZIP), os.path.join(d, "unxz"))   # unxz → xz, coreutils applets: argv0 changes
            self.assertIsNone(self.k(["unxz", "-dc", GZ[0]], path=d))
            self.assertIsNotNone(self.k(["gzip", "-dc", GZ[0]]))
        unxz = shutil.which("unxz", path=SYS_PATH)
        if unxz and os.path.basename(os.path.realpath(unxz)) != "unxz":
            self.assertIsNone(self.k(["unxz", "-c", GZ[0]]))

    @unittest.skipUnless(REAL, "needs a root-owned gzip")
    def test_operand_symlinks_and_user_folders_have_no_class(self):
        with tempfile.TemporaryDirectory() as d:
            link = os.path.join(d, "x.gz")
            os.symlink("/etc/shadow", link)
            self.assertIsNone(self.k(["gzip", "-dc", link]), "a symlink to /etc/shadow")
            os.unlink(link)
            os.symlink(GZ[0], link)
            self.assertIsNone(self.k(["gzip", "-dc", link]), "any symlink: it could be swapped after the check")
            plain = os.path.join(d, "y.gz")
            shutil.copy(GZ[0], plain)
            self.assertIsNone(self.k(["gzip", "-dc", plain]), "a folder the Agent can write")
        self.assertIsNone(self.k(["gzip", "-dc", GZ[0], "/etc/shadow"]), "every operand is checked")

    def test_class_key_and_offer(self):
        cat = shutil.which("cat", path=SYS_PATH)
        if not (cat and elevate_grant.root_safe(os.path.realpath(cat))):
            self.skipTest("needs a root-owned cat")
        k1 = self.k(["cat", "-n", "/etc/hosts"])
        self.assertEqual(k1, (os.path.realpath(cat), "", ("-n",), "/etc"))
        self.assertNotEqual(k1, self.k(["cat", "/etc/hosts"]))
        off = elevate_grant.offer_of(k1)
        self.assertEqual((off["prog"], off["fmt"], off["secs"], off["dir"]), ("cat", "cat -n /etc/*", 600, "/etc"))
        self.assertRegex(off["h"], r"^[0-9a-f]{64}$")
        self.assertEqual(elevate_grant.parse("head", ["-n", "5", "/x"])[1], ("-n=5",))
        self.assertEqual(elevate_grant.parse("systemctl", ["status", "--no-pager", "sshd"])[:2], ("status", ("--no-pager",)))
        self.assertIsNone(elevate_grant.parse("systemctl", ["status", "/etc/x"]))

    def test_sensitive_paths(self):
        for p in ("/etc/shadow", "/etc/gshadow-", "/etc/sudoers", "/etc/sudoers.d/x", "/root", "/root/x", "/home/bob/notes",
                  "/var/backups/.ssh/x", "/srv/app/.env", "/srv/app/prod.env", "/opt/tls/server.pem", "/opt/id_rsa",
                  "/proc/1/environ", "/etc/ssh/ssh_host_ed25519_key", "/var/backups/shadow.bak", "/dev/sda"):
            with self.subTest(p=p):
                self.assertTrue(elevate_grant.sensitive(p))
        for p in ("/etc/hosts", "/var/log/pacman.log", "/var/backups/nightly.gz", "/usr/share/man/man1/ls.1.gz"):
            with self.subTest(p=p):
                self.assertFalse(elevate_grant.sensitive(p))

    def test_signed_message_grant_line(self):
        base = elevate.signed_message("C", "D", "r" * 32, "sudo", "allow", "n", 1, "d", "c")
        self.assertEqual(base, elevate.signed_message("C", "D", "r" * 32, "sudo", "allow", "n", 1, "d", "c", None))
        self.assertEqual(elevate.signed_message("C", "D", "r" * 32, "sudo", "allow", "n", 1, "d", "c", "ab"),
                         base + b"\ngrant:ab")


def nopasswd_sudo(d: pathlib.Path) -> pathlib.Path:
    """sudo with NOPASSWD / !authenticate: never prints the prompt, the command inherits stdin. Records what the command
    could read on stdin (length + whether it equals the password) after running it."""
    p = d / "nopasswd-sudo"
    p.write_text(f"""#!{sys.executable}
import json, subprocess, sys
a = sys.argv[1:]
assert a[:2] == ["-S", "-k"] and a[2] == "-p" and a[4] == "--", a
code = subprocess.run(a[5:]).returncode
data = sys.stdin.buffer.read()
with open({str(d / 'nopasswd-record.jsonl')!r}, "a") as f:
    f.write(json.dumps({{"len": len(data), "pw": {PW!r}.encode() in data}}) + "\\n")
sys.exit(code)
""")
    p.chmod(0o700)
    return p


class StdinAndPrompt(unittest.TestCase):
    """The password only ever answers sudo's own prompt; a grant needs sudo to have asked exactly once."""
    def test_zero_operand_forms_have_no_class(self):
        for argv in (["cat"], ["sha256sum"], ["wc"], ["grep", "x"], ["head", "-n", "1"], ["gzip", "-c"]):
            with self.subTest(argv=argv):
                self.assertIsNone(elevate_grant.parse(argv[0], argv[1:]))
                self.assertIsNone(elevate_grant.klass(argv, SYS_PATH))
        self.assertIsNone(elevate_grant.parse("grep", ["-e", "x"]), "-e pattern and no file")

    def test_prompted_run_reports_one_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            res = elevate.run_sudo(["true"], bytearray(PW.encode()), "/", 10, str(fake_sudo(pathlib.Path(d), PW)))
            self.assertEqual((res["result"], res["code"], res["prompts"]), ("done", 0, 1))
            res = elevate.run_sudo(["true"], bytearray(b"wrong"), "/", 10, str(fake_sudo(pathlib.Path(d), PW)))
            self.assertEqual(res["result"], "bad_password")

    def test_no_prompt_means_password_never_reaches_the_command(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(elevate, "PROMPT_WAIT", .3):
            root = pathlib.Path(d)
            sudo = str(nopasswd_sudo(root))
            for argv in (["true"], ["echo", "hi"]):          # silent command (grace) and a printing one (stdout closes)
                with self.subTest(argv=argv):
                    res = elevate.run_sudo(argv, bytearray(PW.encode()), "/", 10, sudo)
                    self.assertEqual((res["result"], res["code"], res["prompts"]), ("done", 0, 0))
            recs = [json.loads(x) for x in (root / "nopasswd-record.jsonl").read_text().splitlines()]
            self.assertEqual(recs, [{"len": 0, "pw": False}] * 2, "stdin was closed unwritten")

    @unittest.skipUnless(REAL, "needs a root-owned gzip and two root-owned .gz files")
    def test_nopasswd_sudo_creates_no_grant(self):
        async def go(r):
            r.host.elevate.sudo = str(nopasswd_sudo(r.root))
            card, res = await r.approve(A)
            self.assertIsNotNone(card)
            self.assertEqual((res["result"], res["code"]), ("done", 0))
            self.assertFalse(r.host.elevate.grants.grants, "sudo never checked the password: nothing kept")
            self.assertFalse(any(x.get("result") == "grant_on" for x in r.log()))
            rec = json.loads((r.root / "nopasswd-record.jsonl").read_text())
            self.assertEqual(rec, {"len": 0, "pw": False})
            self.assertIsNotNone(await r.shows_card(B))
        with mock.patch.object(elevate, "PROMPT_WAIT", .3):
            run(go)

    @unittest.skipUnless(REAL, "needs a root-owned gzip and two root-owned .gz files")
    def test_create_raising_wipes_the_password(self):
        async def go(r):
            seen = []
            orig = elevate_grant.GrantBook.create
            def spy(self, card, device, sk, pw):
                seen.append(pw)
                return orig(self, card, device, sk, pw)
            with mock.patch.object(elevate_grant.GrantBook, "create", spy), \
                    mock.patch.object(elevate_grant, "Grant", side_effect=RuntimeError("boom")):
                _, res = await r.approve(A)
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0], bytearray(len(seen[0])), "zeroed")
            self.assertTrue(len(seen[0]) > 0)
            self.assertFalse(r.host.elevate.grants.grants)
        run(go)


class Tightened(unittest.TestCase):
    def test_keyrings_and_service_secrets_have_no_class(self):
        for p in ("/home/x/.gnupg/secring.gpg", "/etc/pacman.d/gnupg/secring.gpg", "/var/backups/secring.old",
                  "/srv/k/private-keys-v1.d/a.key", "/srv/k/openpgp-revocs.d/a.rev", "/srv/backup/keys.gpg",
                  "/var/lib/iwd/home.psk", "/var/lib/bluetooth/x/info", "/var/lib/tailscale/tailscaled.state",
                  "/var/lib/postgres/data/pg_hba.conf", "/var/lib/mysql/mysql/user.MYD", "/etc/hysteria/config.yaml",
                  "/etc/sing-box/config.json", "/etc/xray/config.json", "/etc/wireguard/wg0.conf",
                  "/etc/NetworkManager/system-connections/home.nmconnection"):
            with self.subTest(p=p):
                self.assertTrue(elevate_grant.sensitive(p))
                self.assertIsNone(elevate_grant.klass(["cat", p], SYS_PATH))
        if os.path.isdir("/etc/pacman.d/gnupg"):
            self.assertFalse(elevate_grant.path_ok("/etc/pacman.d/gnupg"))

    def test_systemctl_cat_has_no_class(self):
        self.assertIsNone(elevate_grant.parse("systemctl", ["cat", "sshd"]))
        self.assertIsNone(elevate_grant.klass(["systemctl", "cat", "sshd"], SYS_PATH))

    def test_du_needs_summary_and_never_a_top_level_folder(self):
        for args in (["/var/log/x"], ["-h", "/var/log/x"], ["-s", "/"], ["-sh", "/etc"], ["-s", "/var"], ["-s", "/usr"],
                     ["-s", "/home"], ["-s", "/root"], ["-s", "/opt"], ["-s", "/srv"], ["-s", "/var/log", "/"]):
            with self.subTest(args=args):
                self.assertIsNone(elevate_grant.parse("du", args))
                self.assertIsNone(elevate_grant.klass(["du", *args], SYS_PATH))
        self.assertEqual(elevate_grant.parse("du", ["-s", "/var/log/x"])[:3], ("", ("-s",), ["/var/log/x"]))
        self.assertEqual(elevate_grant.parse("du", ["--summarize", "-h", "/var/log/x"])[1], ("--summarize", "-h"))

    def test_reader_key_carries_the_folder(self):
        cat = shutil.which("cat", path=SYS_PATH)
        if not (cat and elevate_grant.root_safe(os.path.realpath(cat))):
            self.skipTest("needs a root-owned cat")
        with mock.patch.object(elevate_grant, "path_ok", return_value=True):
            a = elevate_grant.klass(["cat", "/var/log/a.log"], SYS_PATH)
            self.assertEqual(a[3], os.path.realpath("/var/log"))
            self.assertEqual(a, elevate_grant.klass(["cat", "/var/log/b.log"], SYS_PATH))
            self.assertNotEqual(a, elevate_grant.klass(["cat", "/etc/other.conf"], SYS_PATH))
            self.assertIsNone(elevate_grant.klass(["cat", "/var/log/a.log", "/etc/other.conf"], SYS_PATH), "two folders")
            self.assertNotEqual(elevate_grant.class_hash(a),
                                elevate_grant.class_hash(elevate_grant.klass(["cat", "/etc/other.conf"], SYS_PATH)))

    def test_every_path_program_is_bound_to_the_approved_folder(self):
        # P393 #116 review (signed scope 「同目录」): not only readers — ls / stat / du / wc / sha256sum / file … too
        with mock.patch.object(elevate_grant, "path_ok", return_value=True):
            for argv, same, other in ((["ls", "-l", "/var/log/a.log"], ["ls", "-l", "/var/log/b.log"], ["ls", "-l", "/etc/x.conf"]),
                                      (["stat", "/var/log/a.log"], ["stat", "/var/log/b.log"], ["stat", "/etc/x.conf"]),
                                      (["wc", "-l", "/var/log/a.log"], ["wc", "-l", "/var/log/b.log"], ["wc", "-l", "/etc/x.conf"]),
                                      (["sha256sum", "/var/log/a.log"], ["sha256sum", "/var/log/b.log"], ["sha256sum", "/etc/x.conf"]),
                                      (["du", "-s", "/var/log/a"], ["du", "-s", "/var/log/b"], ["du", "-s", "/etc/x"])):
                with self.subTest(prog=argv[0]):
                    exe = shutil.which(argv[0], path=SYS_PATH)
                    if not (exe and elevate_grant.root_safe(os.path.realpath(exe))):
                        continue
                    a = elevate_grant.klass(argv, SYS_PATH)
                    self.assertIsNotNone(a)
                    self.assertEqual(a[3], os.path.realpath(os.path.dirname(argv[-1])), "the approved folder is in the key")
                    self.assertEqual(a, elevate_grant.klass(same, SYS_PATH), "same folder: the same class")
                    self.assertNotEqual(a, elevate_grant.klass(other, SYS_PATH), "another folder: another class (a card)")
                    self.assertIsNone(elevate_grant.klass([*argv, other[-1]], SYS_PATH), "two folders: no class")
                    self.assertEqual(elevate_grant.offer_of(a)["dir"], a[3])
        # a folder operand is its own folder: ls /var/log covers /var/log, never its parent /var
        ls = shutil.which("ls", path=SYS_PATH)
        if ls and elevate_grant.root_safe(os.path.realpath(ls)) and os.path.isdir("/var/log"):
            with mock.patch.object(elevate_grant, "path_ok", return_value=True):
                k = elevate_grant.klass(["ls", "/var/log"], SYS_PATH)
                self.assertEqual(k[3], os.path.realpath("/var/log"))
                self.assertNotEqual(k, elevate_grant.klass(["ls", "/var/lib"], SYS_PATH))
        # no path operands: nothing to bind
        sc = shutil.which("systemctl", path=SYS_PATH)
        if sc and elevate_grant.root_safe(os.path.realpath(sc)):
            k = elevate_grant.klass(["systemctl", "status", "sshd"], SYS_PATH)
            if k is not None:
                self.assertEqual(k[3], "")

    def test_cat_grant_then_same_folder_auto_runs_other_folder_card(self):
        cat = shutil.which("cat", path=SYS_PATH)
        if not (cat and elevate_grant.root_safe(os.path.realpath(cat))):
            self.skipTest("needs a root-owned cat")
        async def go(r):
            card, res = await r.approve(["cat", "/var/log/a.log"])
            self.assertIsNotNone(card)
            self.assertEqual(card["grant"]["fmt"], "cat " + os.path.join(os.path.realpath("/var/log"), "*"))
            self.assertEqual(len(r.host.elevate.grants.grants), 1)
            card, res = await r.approve(["cat", "/var/log/b.log"])
            self.assertIsNone(card, "same program, same options, same folder: runs under the grant")
            self.assertTrue(res["auto"])
            card, res = await r.deny_next(["cat", "/etc/other.conf"])
            self.assertIsNotNone(card, "another folder: a card")
            self.assertEqual(res["result"], "denied")
        with mock.patch.object(elevate_grant, "path_ok", return_value=True):
            run(go)


if __name__ == "__main__":
    unittest.main()
