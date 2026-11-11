"""0.30.7 (chairman 11-10): 「改回全放行并重启」 on a Codex sandbox refusal — one host-defined owner button.

- the refusal page carries typed host metadata card.fix only for the owner's top-level config.toml sandbox_mode (CONFIG
  notice); the desktop-thread / Agent J default / misplaced-only cases get no button;
- the tap is the signed `cmd_run` {"cmd": "codex_full_access"} from a paired device + a real passkey assertion (approval_widen
  tier): config.toml's top-level sandbox_mode is removed in process, the installed service is restarted (a stand-in
  systemctl first on PATH — never the real user manager), controls.log + approvals.log record device / before / after;
- after the restart the next ready phone session gets 「已回到全放行」 once, audited;
- unsigned / forged / replayed / unpaired / no-passkey / bad assertion → nothing changes, nothing restarts;
- the subprocess path never runs it; the Agent has no way to send it (only a phone control reaches cmd_run).
"""
import _hermetic  # noqa: F401,I001
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
from agentj import agent_codex as cx, cmdrun, codex_full_access as cfa, codex_perm, controls, passkey, service, wire  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from test_l1 import Phone, _host, _paired_app, _ready, _state  # noqa: E402

NAME = cfa.NAME
UNIT = "agentj-t0307cfa"
OWNER_TOML = '# mine\nsandbox_mode = "workspace-write"\nmodel = "gpt-x"\n\n[tui]\nnotifications = true\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.codex = root / "codex"
        self.codex.mkdir()
        (self.codex / "config.toml").write_text(OWNER_TOML)
        bindir = root / "bin"
        bindir.mkdir()
        self.calls = root / "systemctl.calls"
        stub = bindir / "systemctl"           # never the real user manager (memory: systemd user manager not isolated)
        stub.write_text("#!/bin/sh\n"
                        f"echo \"$*\" >> '{self.calls}'\n"
                        "case \"$*\" in *is-active*) echo active;; *is-enabled*) echo enabled;; esac\nexit 0\n")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        cfg = root / "xdg"
        env = {"CODEX_HOME": str(self.codex), "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
               "AGENTJ_SERVICE_NAME": UNIT, "XDG_CONFIG_HOME": str(cfg)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        unit = pathlib.Path(service.unit_dir()) / f"{UNIT}.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("[Service]\nExecStart=/bin/true\n")
        g = mock.patch.object(cmdrun, "RESTART_GRACE", 0)
        g.start()
        self.addCleanup(g.stop)
        self.st = _state(str(root / "state"))
        self.sent = []
        self.host = _host(self.st, self.sent)

    # helpers
    def top(self):
        return codex_perm.config_info()["top"]

    def restarts(self):
        return [x for x in (self.calls.read_text().splitlines() if self.calls.exists() else []) if x.startswith("--user restart")]

    def clog(self):
        p = self.st.controls_path
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def alog(self):
        p = self.st.approvals_path
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def mine(self):
        """approvals.log lines of this control (tool cmd_run, object {"cmd": NAME})."""
        d = controls.object_digest("cmd_run", {"cmd": NAME})
        return [x for x in self.alog() if x.get("tool") == "cmd_run" and x.get("object_sha256") == d]

    def signed(self, ph, *, n=None, key=None, ts=None, cmd=NAME):
        n = n or os.urandom(16).hex()
        ts = int(time.time() * 1000) if ts is None else ts
        msg = controls.signed_message(ph.channel, ph.did, "cmd_run", n, ts, controls.object_digest("cmd_run", {"cmd": cmd}))
        return {"t": "cmd_run", "r": os.urandom(8).hex(), "cmd": cmd, "n": n, "ts": ts,
                "sig": wire.b64u((key or ph.sk).sign(msg))}

    def with_fa(self, ph, obj):
        ch = passkey.elevate_challenge(self.host.channel, ph.did, obj["n"], "cmd_run", obj["n"],
                                       controls.object_digest("cmd_run", {"cmd": obj["cmd"]}))
        return {**obj, "fa": ph.auth.assert_(ch, b"", nonce=b"")}

    def send(self, s, obj):
        s.p33 = True
        async def go():
            await _paired_app(self.host, s, obj)
            for _ in range(300):
                await asyncio.sleep(0.01)
                if not self.host.cmd_desk.busy and not [t for t in asyncio.all_tasks()
                                                        if "full_access" in repr(t.get_coro())]:
                    break
            await asyncio.sleep(0.05)
        asyncio.run(go())
        return [o for _, o in self.sent if o.get("t") == "ctl_res" and o.get("r") == obj["r"]]


class RefusalButton(Base):
    def refuse(self, source, thread_type="workspaceWrite"):
        pages = []
        orig = self.host.hist_add
        self.host.hist_add = lambda src, *a, **k: (pages.append((src, a, k)), orig(src, *a, **k))[1]
        a = cx.CodexAgent(self.host, {"kind": "codex", "dir": self.tmp.name}, persist=False)
        a.human = {"sandbox_mode": "workspace-write"}
        a.thread = {"sandbox": {"type": thread_type}}
        a.perm_source = source
        a._answer = lambda *x, **y: None
        async def go():
            a._refuse("1", "Bash", {"command": "curl -s https://example.com"}, {"decision": "decline"}, "Codex 的沙箱不允许联网")
        asyncio.run(go())
        sys_pages = [p for p in pages if p[0].get("k") == "sys"]
        self.assertEqual(len(sys_pages), 1)
        src, args, kw = sys_pages[0]
        return args[0] if args else kw.get("reply"), kw.get("card")

    def test_config_case_carries_the_one_button(self):
        text, card = self.refuse("config")
        self.assertEqual(card, {"fix": NAME})
        self.assertIn("agentj codex-sandbox default", text)

    def test_desktop_and_default_cases_have_none(self):
        for src in ("desktop", "agentj", "research", "pending", None):
            with self.subTest(src=src):
                self.sent.clear()
                _, card = self.refuse(src)
                self.assertIsNone(card)

    def test_no_top_level_setting_no_button(self):
        (self.codex / "config.toml").write_text('[tui]\nsandbox_mode = "read-only"\n')    # misplaced: not what Codex read
        _, card = self.refuse("config")
        self.assertIsNone(card)

    def test_other_notices_never_get_it(self):
        pages = []
        orig = self.host.hist_add
        self.host.hist_add = lambda src, *a, **k: (pages.append(k.get("card")), orig(src, *a, **k))[1]
        a = cx.CodexAgent(self.host, {"kind": "codex", "dir": self.tmp.name}, persist=False)
        a.perm_source = "config"
        a._answer = lambda *x, **y: None
        async def go():
            a._refuse("1", "mcp", {"arguments_size": 9}, {"action": "decline"}, cx.MCP_TOO_BIG_WHY, cx.MCP_TOO_BIG_NOTICE)
        asyncio.run(go())
        self.assertEqual(pages, [None])


class _Tap(Base):
    def setUp(self):
        super().setUp()
        self.ph = Phone(self.st, with_passkey=True)
        self.s = _ready(self.host, self.ph)


class SignedTap(_Tap):

    def test_signed_tap_with_face_id_restores_restarts_and_receipts(self):
        obj = self.signed(self.ph)
        first = self.send(self.s, obj)
        self.assertEqual((first[-1]["ok"], first[-1]["why"]), (False, "passkey_required"))
        self.assertEqual(self.top(), "workspace-write", "nothing before the assertion")
        self.assertEqual(self.restarts(), [])
        res = self.send(self.s, self.with_fa(self.ph, obj))
        self.assertTrue(res[-1]["ok"], res)
        self.assertEqual(res[-1]["restart"], "requested")
        text = (self.codex / "config.toml").read_text()
        self.assertIsNone(self.top())
        self.assertIn("# mine", text)
        self.assertIn("notifications = true", text)
        self.assertEqual(len(list(self.codex.glob("config.toml.agentj-*.bak"))), 1)
        self.assertEqual(self.restarts(), [f"--user restart {UNIT}.service"])
        ok = [x for x in self.clog() if x.get("cmd") == NAME and x["result"] == "ok"]
        self.assertEqual(len(ok), 1)
        self.assertEqual((ok[0]["device"], ok[0]["before"], ok[0]["after"], ok[0]["restart"]),
                         (self.ph.did, "workspace-write", "default", "requested"))
        self.assertEqual(ok[0]["sig"], obj["sig"])
        a = self.mine()
        self.assertEqual((len(a), a[0]["device"], a[0]["decision"]), (1, self.ph.did, "allow"))
        from agentj import approvals
        self.assertEqual(approvals.check_record(self.st, a[0]), "ok", "r1 A-3: verifiable as the signed control it is")
        self.assertTrue((self.st.root / cfa.RECEIPT).exists(), "the receipt waits for the restarted serve")
        self.assertEqual(oct((self.st.root / cfa.RECEIPT).stat().st_mode & 0o777), "0o600")

        # the same serve (restart not yet happened) does not claim the receipt
        self.assertFalse(cfa.deliver(self.host))
        # "after the restart": a new serve on the same state, a phone comes ready → the receipt once
        sent2 = []
        host2 = _host(self.st, sent2)
        notices = []
        host2.agent_notice = lambda t, **k: notices.append(t)
        s2 = _ready(host2, self.ph, cid=12)
        with mock.patch.object(cfa.os, "getpid", return_value=-1):
            async def ready():
                with mock.patch.object(host2, "_bg", side_effect=lambda c, *a, **k: c.close()):
                    try:
                        await host2.on_ready(s2, None)
                    except Exception:  # noqa: BLE001 — later on_ready steps are not under test; the receipt comes first
                        pass
            asyncio.run(ready())
            self.assertFalse(cfa.deliver(host2), "delivered once")
        self.assertEqual(len([n for n in notices if "已回到全放行" in n]), 1, notices)
        self.assertFalse((self.st.root / cfa.RECEIPT).exists())
        self.assertTrue([x for x in self.clog() if x.get("cmd") == NAME and x["result"] == "receipt:ok"])

        # replay of the same signed object: refused, nothing changes
        (self.codex / "config.toml").write_text(OWNER_TOML)
        res = self.send(self.s, self.with_fa(self.ph, obj))
        self.assertEqual((res[-1]["ok"], res[-1]["why"]), (False, "replay"))
        self.assertEqual(self.top(), "workspace-write")
        self.assertEqual(len(self.restarts()), 1)

    def assertNothing(self, why_in):
        self.assertEqual(self.top(), "workspace-write")
        self.assertEqual(self.restarts(), [])
        self.assertFalse((self.st.root / cfa.RECEIPT).exists())
        self.assertFalse(self.mine())
        self.assertIn(why_in, {x["result"] for x in self.clog()})

    def test_forged_signature(self):
        obj = self.with_fa(self.ph, self.signed(self.ph, key=Ed25519PrivateKey.generate()))
        res = self.send(self.s, obj)
        self.assertEqual((res[-1]["ok"], res[-1]["why"]), (False, "bad_signature"))
        self.assertNothing("refused:bad_signature")

    def test_unsigned(self):
        obj = self.signed(self.ph)
        obj.pop("sig")
        res = self.send(self.s, self.with_fa(self.ph, obj))
        self.assertFalse(res[-1]["ok"])
        self.assertNothing("refused:shape")

    def test_bad_assertion(self):
        obj = self.signed(self.ph)
        fa = self.with_fa(self.ph, {**obj, "n": os.urandom(16).hex()})["fa"]    # Face ID over another nonce
        res = self.send(self.s, {**obj, "fa": fa})
        self.assertEqual((res[-1]["ok"], res[-1]["why"]), (False, "passkey_failed"))
        self.assertNothing("refused:passkey_failed")

    def test_no_passkey_phone(self):
        ph = Phone(self.st, name="平板")
        s = _ready(self.host, ph, cid=13)
        res = self.send(s, self.signed(ph))
        self.assertEqual((res[-1]["ok"], res[-1]["why"]), (False, "no_passkey"))
        self.assertNothing("refused:no_passkey")

    def test_unpaired_device(self):
        ph = Phone(self.st, name="旧手机", with_passkey=True)
        s = _ready(self.host, ph, cid=14)
        self.st.remove_device(ph.did)
        obj = self.with_fa(ph, self.signed(ph))
        self.send(s, obj)
        self.assertEqual(self.top(), "workspace-write")
        self.assertEqual(self.restarts(), [])

    def test_no_live_service_means_no_restart_and_an_honest_receipt(self):
        (pathlib.Path(service.unit_dir()) / f"{UNIT}.service").unlink()
        notices = []
        self.host.agent_notice = lambda t, **k: notices.append(t)
        obj = self.signed(self.ph)
        res = self.send(self.s, self.with_fa(self.ph, obj))
        self.assertEqual((res[-1]["ok"], res[-1]["restart"]), (True, "manual"))
        self.assertIsNone(self.top())
        self.assertEqual(self.restarts(), [])
        self.assertTrue(any("agentj service restart" in n for n in notices), notices)

    def test_already_default_changes_nothing(self):
        (self.codex / "config.toml").write_text('model = "gpt-x"\n')
        res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertEqual((res[-1]["ok"], res[-1]["restart"], res[-1].get("why")), (True, "none", "unchanged"))
        self.assertEqual(self.restarts(), [])
        self.assertEqual(list(self.codex.glob("config.toml.agentj-*.bak")), [])


class R1Symlinks(_Tap):
    """r1 A-1: nothing in the write follows a symlink (tmp name, backup name, config.toml itself)."""

    def setUp(self):
        super().setUp()
        self.victim = pathlib.Path(self.tmp.name) / "victim.txt"
        self.victim.write_text("VICTIM\n")
        self.notices = []
        self.host.agent_notice = lambda t, **k: self.notices.append(t)

    def test_planted_tmp_name_is_not_followed(self):
        (self.codex / ".config.toml.agentj-tmp").symlink_to(self.victim)
        res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertTrue(res[-1]["ok"], res)
        self.assertEqual(self.victim.read_text(), "VICTIM\n")
        cfg = self.codex / "config.toml"
        self.assertFalse(cfg.is_symlink())
        self.assertIsNone(self.top())
        self.assertEqual([p.name for p in self.codex.glob(".config.toml.agentj-*.tmp")], [], "no tmp left behind")

    def test_planted_backup_name_is_not_followed(self):
        stamp = "20991231-235959"
        (self.codex / f"config.toml.agentj-{stamp}.bak").symlink_to(self.victim)               # dangling-or-not: skipped
        (self.codex / f"config.toml.agentj-{stamp}-1.bak").symlink_to(self.codex / "missing")  # dangling link
        with mock.patch.object(codex_perm.time, "strftime", return_value=stamp):
            res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertTrue(res[-1]["ok"], res)
        self.assertEqual(self.victim.read_text(), "VICTIM\n")
        self.assertFalse((self.codex / "missing").exists(), "a dangling link is not created through")
        b = self.codex / f"config.toml.agentj-{stamp}-2.bak"
        self.assertFalse(b.is_symlink())
        self.assertEqual(b.read_text(), OWNER_TOML)
        self.assertEqual(oct(b.stat().st_mode & 0o777), "0o600")

    def test_symlinked_config_is_refused_and_left_alone(self):
        real = pathlib.Path(self.tmp.name) / "dotfiles-config.toml"
        real.write_text(OWNER_TOML)
        cfg = self.codex / "config.toml"
        cfg.unlink()
        cfg.symlink_to(real)
        self.assertFalse(cfa.offered("config"), "no button the tap could not honour")
        res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertEqual((res[-1]["ok"], res[-1]["why"], res[-1]["restart"]), (False, "config_invalid", "none"))
        self.assertTrue(cfg.is_symlink())
        self.assertEqual(real.read_text(), OWNER_TOML)
        self.assertEqual(self.restarts(), [])
        self.assertEqual(list(self.codex.glob("config.toml.agentj-*.bak")), [])
        self.assertTrue(any("符号链接" in n and "dotfiles" in n for n in self.notices), self.notices)
        self.assertIn("refused:config_invalid", {x["result"] for x in self.clog()})
        self.assertEqual(len(self.mine()), 1, "the tap itself is still in approvals.log")
        with self.assertRaises(codex_perm.NotRegular):
            codex_perm.set_mode(None)
        with self.assertRaises(codex_perm.NotRegular):
            codex_perm.fix()
        cfg.unlink()
        cfg.symlink_to(self.codex / "gone.toml")                                              # dangling
        with self.assertRaises(codex_perm.NotRegular):
            codex_perm.set_mode("read-only")
        self.assertFalse((self.codex / "gone.toml").exists())

    def test_cli_says_why_for_a_symlink(self):
        real = pathlib.Path(self.tmp.name) / "dotfiles-config.toml"
        real.write_text(OWNER_TOML)
        cfg = self.codex / "config.toml"
        cfg.unlink()
        cfg.symlink_to(real)
        a = mock.Mock(action="default", mode=None, json=False)
        with self.assertRaises(SystemExit) as cm:
            codex_perm.cmd(a)
        self.assertIn("symlink", str(cm.exception.code))
        self.assertEqual(real.read_text(), OWNER_TOML)


class R1Profile(_Tap):
    """r1 A-2: a selected [profiles.<p>] with its own sandbox_mode still restricts — never 「已回到全放行」."""
    PROF = 'sandbox_mode = "workspace-write"\nprofile = "strict"\n\n[profiles.strict]\nsandbox_mode = "read-only"\n'

    def test_not_offered_and_tap_says_the_profile_still_restricts(self):
        (self.codex / "config.toml").write_text(self.PROF)
        self.assertFalse(cfa.offered("config"))
        notices = []
        self.host.agent_notice = lambda t, **k: notices.append(t)
        res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertEqual((res[-1]["ok"], res[-1]["why"], res[-1]["restart"]), (False, "profile_restricts", "none"))
        self.assertEqual((self.codex / "config.toml").read_text(), self.PROF, "nothing changed")
        self.assertEqual(self.restarts(), [])
        self.assertTrue(any("[profiles.strict]" in n and "没有回到全放行" in n for n in notices), notices)
        self.assertFalse(any("已回到全放行" in n for n in notices))

    def test_receipt_checks_the_effective_mode(self):
        (self.codex / "config.toml").write_text('profile = "strict"\n\n[profiles.strict]\nsandbox_mode = "read-only"\n')
        for lang, bad in (("zh", "已回到全放行"), ("en", "Back to full access")):
            ok, text = cfa.receipt_text({"restart": "requested"}, lang)
            self.assertFalse(ok)
            self.assertNotIn(bad, text)
            self.assertIn("[profiles.strict]", text)
        (self.codex / "config.toml").write_text('profile = "open"\n\n[profiles.open]\nsandbox_mode = "danger-full-access"\n')
        self.assertTrue(cfa.receipt_text({"restart": "requested"}, "zh")[0], "a full-access profile is full access")

    def test_unselected_profile_does_not_matter(self):
        (self.codex / "config.toml").write_text('sandbox_mode = "workspace-write"\n\n[profiles.strict]\nsandbox_mode = "read-only"\n')
        self.assertTrue(cfa.offered("config"))
        res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertTrue(res[-1]["ok"], res)
        self.assertIsNone(self.top())


class R1PreRecord(_Tap):
    """r1 A-3: the approvals.log line is written before config.toml changes; no line → nothing changes, host.log says so."""

    def test_line_exists_before_the_write(self):
        seen = []
        orig = codex_perm.set_mode
        def spy(*a, **k):
            seen.append(len(self.mine()))
            return orig(*a, **k)
        with mock.patch.object(codex_perm, "set_mode", spy):
            res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertTrue(res[-1]["ok"], res)
        self.assertEqual(seen, [1])

    def test_record_failure_aborts_and_is_logged(self):
        from agentj import approvals
        with mock.patch.object(approvals, "record", side_effect=OSError("disk full")):
            res = self.send(self.s, self.with_fa(self.ph, self.signed(self.ph)))
        self.assertEqual((res[-1]["ok"], res[-1]["why"]), (False, "audit_failed"))
        self.assertEqual(self.top(), "workspace-write")
        self.assertEqual(self.restarts(), [])
        self.assertFalse((self.st.root / cfa.RECEIPT).exists())
        self.assertEqual(list(self.codex.glob("config.toml.agentj-*.bak")), [])
        log = self.st.log_path.read_text()
        self.assertIn("cmd_run_audit_failed", log)
        self.assertNotIn("disk full", log, "type only")
        self.assertIn("refused:audit_failed", {x["result"] for x in self.clog()})


class Contract(unittest.TestCase):
    def test_listed_by_name_and_never_a_subprocess(self):
        self.assertIn(NAME, cmdrun.available_commands())
        self.assertEqual(cmdrun.run(NAME, runner=lambda *a, **k: self.fail("subprocess"))["code"], -2)

    def test_tier_is_approval_widen(self):
        self.assertTrue(cfa.requires_uv())
        with mock.patch.dict("agentj.confirm_tier.OWNER_ACTIONS", {"approval_widen": "reversible"}):
            self.assertFalse(cfa.requires_uv(), "the table decides, not this module")

    def test_only_the_host_defines_the_action(self):
        src = (pathlib.Path(cfa.__file__)).read_text()
        self.assertNotIn("subprocess", src)
        self.assertIn("codex_perm.set_mode(None)", src)


if __name__ == "__main__":
    unittest.main()
