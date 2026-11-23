"""0372 (chairman 2026-11-23, decision 5 option A): `agentj agent <kind> --unfenced` / `--allow-docker` pass the same gate
as `agentj config set agent.isolation false` / `agent.allow_docker true`.

- fence on → off and docker off → on are the gated directions, read from the stored entry;
- serve decides who is calling (control.sock claim: peer pid + process ancestry — never this command line or its
  environment): the owner's terminal goes on, an Agent's call becomes the owner's Face ID config card and writes nothing;
- with pairing history and no serve nothing is written; a host that was never paired goes on (containers, Olares);
- fence off → on and docker on → off never ask.

The CLI is the real one in a child process; serve is a real Host on a real control.sock in this process, so a plain child
is "started below serve" (an Agent) and a double-forked, re-parented one is "an owner's terminal" — the same split as
test_p139.ControlSocket. Cards go through the real Host.ask to a paired phone with a passkey; Host.ask is never stubbed.

What "an owner's terminal" means here: a process that left serve's process tree (double fork + setsid) and carries no
harness marker. That is all serve can see, and it is exactly what a same-uid process that escapes on purpose looks like
(ctl_auth module docstring, ADR-0372 GAP): these tests cannot tell the owner from such a process, and do not claim to.

Security review follow-ups (2026-11-23): F1 `agentj agent switch --undo` / a switch rollback only narrows (UndoOnlyNarrows);
F2 "never paired" reads devices.json itself and treats doubt as paired; F3 the gate runs before the dsh route is written;
F5 serve's log line says `by=claim` and names the closed word.
Round 2: a switch writes its new entry through the same narrowing (the entry is re-read at the write, not taken from the
job's start); the working-root bootstrap exception and the gate share State.paired_ever().
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from _testtmp import TEST_TMP  # noqa: E402
from agentj import approval_jev, approval_level, ctl_auth, doctor, preferences, serve  # noqa: E402
from agentj import switch_harness as sw  # noqa: E402
from agentj.state import State  # noqa: E402

from test_l1 import _ready  # noqa: E402
from _p541_device import Phone  # noqa: E402
from test_p155_switch_harness import UP, Clock, FakeServe, fake_probe, mk_state  # noqa: E402
import test_offlist_zero_cards_0307 as offlist  # noqa: E402

AGENTJ = str(HERE.parent / "bin" / "agentj")
# an owner's terminal: re-parented away from this (= serve's) process, no harness marker — as test_p139.CLIENT
OUTSIDE = r"""
import json, os, subprocess, sys
if os.fork():
    os._exit(0)
os.setsid()
if os.fork():
    os._exit(0)
r = subprocess.run(json.loads(sys.argv[1]), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90)
tmp = sys.argv[2] + ".tmp"
open(tmp, "w").write(json.dumps({"rc": r.returncode, "out": r.stdout + r.stderr}))
os.replace(tmp, sys.argv[2])
"""


class Gate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        t = tempfile.TemporaryDirectory(prefix="u0372-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        d = self.d = pathlib.Path(t.name)
        self.work = d / "work"
        self.work.mkdir()
        (d / "cfg").mkdir()
        (d / "cfg/.credentials.json").write_text("{}")      # presence only (P174): no credential
        fake = {"HOME": str(d / "home"), "XDG_CONFIG_HOME": str(d / "home/.config"),
                "XDG_STATE_HOME": str(d / "home/.local/state"), "XDG_DATA_HOME": str(d / "home/.local/share"),
                "XDG_CACHE_HOME": str(d / "home/.cache"), "XDG_RUNTIME_DIR": str(d / "run"),
                "AGENTJ_STATE_DIR": str(d / "s"), "CLAUDE_CONFIG_DIR": str(d / "cfg")}
        (d / "run").mkdir(mode=0o700)
        (d / "bin").mkdir()
        stub = d / "bin/systemctl"                          # the user manager is not isolated by a fake HOME
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
        fake["PATH"] = str(d / "bin") + os.pathsep + os.environ.get("PATH", "")
        env = mock.patch.dict(os.environ, fake)
        env.start()
        self.addCleanup(env.stop)
        for k in ctl_auth.AGENT_ENV:                        # this test may itself run below an Agent harness
            if k in os.environ:
                p = mock.patch.dict(os.environ)
                p.start()
                self.addCleanup(p.stop)
                os.environ.pop(k, None)
        self.env = dict(os.environ)
        r = subprocess.run([AGENTJ, "init", "--relay", "ws://127.0.0.1:1", "--working-root", str(self.work)],
                           env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.st = State(d / "s")
        self.host = self.server = self.phone = None
        self.sent = []

    async def asyncTearDown(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            for t in list(getattr(self.host, "_ctl_bg", ())):
                t.cancel()

    # ---- fixtures
    def pair(self):
        """Pairing history: one phone with an approval key and a passkey (the owner's Face ID)."""
        self.phone = Phone(self.st, with_passkey=True)

    async def serve(self, online=True):
        self.host = serve.Host(self.st, read_stdin=False)
        self.host.ask_ttl = 30

        async def send_app(s, obj):
            self.sent.append(obj)
            return True
        self.host.send_app = send_app
        for p in (mock.patch.object(approval_jev, "available", return_value=False),
                  mock.patch.object(approval_level, "remind_offline", new=mock.AsyncMock(return_value=[]))):
            p.start()
            self.addCleanup(p.stop)
        if online and self.phone is not None:
            self.s = _ready(self.host, self.phone)
        self.server = await asyncio.start_unix_server(self.host.on_ctl, str(self.st.sock_path))

    async def cli(self, *args, owner=False, env=None):
        argv = [AGENTJ, "agent", *args]
        e = {**self.env, **(env or {})}
        if not owner:                                       # a child of serve: an Agent (ctl_auth.refusal, ancestry)
            p = await asyncio.create_subprocess_exec(*argv, env=e, stdin=asyncio.subprocess.DEVNULL,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(p.communicate(), 90)
            return p.returncode, out.decode()
        res = self.d / f"owner-{len(list(self.d.glob('owner-*')))}.json"
        p = await asyncio.create_subprocess_exec(sys.executable, "-c", OUTSIDE, json.dumps(argv), str(res), env=e)
        await p.wait()
        for _ in range(1800):
            if res.exists():
                break
            await asyncio.sleep(.05)
        got = json.loads(res.read_text())
        return got["rc"], got["out"]

    def raw(self):
        return json.loads(self.st.config_path.read_text()).get("agent") or {}

    async def card(self, n=1):
        for _ in range(200):
            cards = [o for o in self.sent if o.get("t") == "ask"]
            if len(cards) >= n:
                return cards[n - 1]
            await asyncio.sleep(.05)
        self.fail("no card reached the phone: " + json.dumps([o.get("t") for o in self.sent]))

    async def answer(self, card, ok):
        await self.host._app(self.s, self.phone.answer(card, ok))
        for _ in range(200):
            if not getattr(self.host, "_ctl_bg", None):
                return
            await asyncio.sleep(.05)

    def pref(self, key):
        return preferences.get(preferences.validate(preferences.read()[1]), key)

    async def base(self, *extra):
        """A fenced codex entry, written before any pairing (first connect)."""
        rc, out = await self.cli("codex", "--dir", str(self.work), *extra)
        self.assertEqual(rc, 0, out)

    FLAGS = (("--unfenced", "fence", False, "agent.isolation", False),
             ("--allow-docker", "docker", True, "agent.allow_docker", True))

    # ---- an Agent outside the fence: nothing written, the owner's Face ID card
    async def test_agent_call_writes_nothing_and_sends_the_face_id_card(self):
        for flag, field, _, key, val in self.FLAGS:
            with self.subTest(flag=flag):
                await self.base()
                if self.phone is None:
                    self.pair()
                    await self.serve()
                before = self.st.config_path.read_bytes()
                n = len([o for o in self.sent if o.get("t") == "ask"])
                rc, out = await self.cli("codex", "--dir", str(self.work), flag)
                self.assertNotEqual(rc, 0, out)
                self.assertIn("没有改动", out)
                self.assertEqual(self.st.config_path.read_bytes(), before)
                card = await self.card(n + 1)
                self.assertEqual(card["tool"], "agentj config")
                self.assertTrue(card.get("passkey_required"), card)     # Face ID, as `agentj config set <key>`
                self.assertIn(key, json.dumps(card, ensure_ascii=False))
                await self.answer(card, False)
                self.assertEqual(self.st.config_path.read_bytes(), before)
                self.assertNotEqual(self.pref(key), val)

    async def test_agent_marker_in_the_environment_changes_nothing(self):
        """A harness marker is not what decides: with and without it a process below serve gets the card, never the write."""
        await self.base()
        self.pair()
        await self.serve()
        before = self.st.config_path.read_bytes()
        for extra in ({"CLAUDECODE": "1"}, {}):
            rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced", env=extra)
            self.assertNotEqual(rc, 0, out)
            self.assertEqual(self.st.config_path.read_bytes(), before)

    async def test_card_allowed_takes_effect_as_the_setting(self):
        for flag, field, want, key, val in self.FLAGS:
            with self.subTest(flag=flag):
                await self.base()
                if self.phone is None:
                    self.pair()
                    await self.serve()
                before = self.st.config_path.read_bytes()
                n = len([o for o in self.sent if o.get("t") == "ask"])
                rc, out = await self.cli("codex", "--dir", str(self.work), flag)
                self.assertNotEqual(rc, 0, out)
                with mock.patch.object(self.host, "_reload_runtime"):
                    await self.answer(await self.card(n + 1), True)
                self.assertEqual(self.pref(key), val)                    # what the owner saw on the card
                self.assertEqual(self.st.config_path.read_bytes(), before)   # the Agent's command itself wrote nothing
                self.assertEqual(State(self.d / "s").agent_config()[field], want)

    async def test_card_timeout_changes_nothing(self):
        await self.base()
        self.pair()
        await self.serve()
        self.host.ask_ttl = 0.3
        before = self.st.config_path.read_bytes()
        for flag, _, _, key, val in self.FLAGS:
            rc, out = await self.cli("codex", "--dir", str(self.work), flag)
            self.assertNotEqual(rc, 0, out)
        for _ in range(200):
            if not getattr(self.host, "_ctl_bg", None):
                break
            await asyncio.sleep(.05)
        self.assertFalse(getattr(self.host, "_ctl_bg", None))
        self.assertEqual(self.st.config_path.read_bytes(), before)
        self.assertEqual((self.pref("agent.isolation"), self.pref("agent.allow_docker")), (True, False))

    async def test_no_remote_online_changes_nothing(self):
        await self.base()
        self.pair()
        await self.serve(online=False)
        self.host.ask_ttl = 0.3
        before = self.st.config_path.read_bytes()
        for flag, _, _, key, val in self.FLAGS:
            rc, out = await self.cli("codex", "--dir", str(self.work), flag)
            self.assertNotEqual(rc, 0, out)
            self.assertEqual(self.st.config_path.read_bytes(), before)
        for _ in range(200):
            if not getattr(self.host, "_ctl_bg", None):
                break
            await asyncio.sleep(.05)
        self.assertEqual((self.pref("agent.isolation"), self.pref("agent.allow_docker")), (True, False))

    # ---- pairing history, serve not running: refused for every caller
    async def test_paired_host_offline_refuses(self):
        await self.base()
        self.pair()
        before = self.st.config_path.read_bytes()
        for flag, _, _, _, _ in self.FLAGS:
            for owner in (False, True):
                with self.subTest(flag=flag, owner=owner):
                    rc, out = await self.cli("codex", "--dir", str(self.work), flag, owner=owner)
                    self.assertNotEqual(rc, 0, out)
                    self.assertIn("没有改动", out)
                    self.assertEqual(self.st.config_path.read_bytes(), before)

    async def test_removed_remote_is_pairing_history(self):
        await self.base()
        self.pair()
        self.assertTrue(self.st.remove_device(self.phone.did))
        self.assertEqual(self.st.devices(), {})
        before = self.st.config_path.read_bytes()
        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.st.config_path.read_bytes(), before)

    async def test_first_entry_and_off_then_on_are_widenings_too(self):
        """No stored entry reads as fenced: `agentj agent off` then `--unfenced` is not a way around."""
        self.pair()
        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.raw(), {})

    # ---- the owner's terminal: at once
    async def test_owner_terminal_applies_at_once(self):
        for flag, field, want, key, val in self.FLAGS:
            with self.subTest(flag=flag):
                await self.base()
                if self.phone is None:
                    self.pair()
                    await self.serve()
                rc, out = await self.cli("codex", "--dir", str(self.work), flag, owner=True)
                self.assertEqual(rc, 0, out)
                self.assertIs(self.raw().get(field, False if field == "docker" else True), want)
                self.assertEqual([o for o in self.sent if o.get("t") == "ask"], [])
                await self.base()                           # back: reverse direction, from an Agent, no card

    # ---- never paired: as before (containers, Olares before pairing)
    async def test_never_paired_applies_at_once(self):
        for flag, field, want, _, _ in self.FLAGS:
            for running in (False, True):
                with self.subTest(flag=flag, running=running):
                    await self.base()
                    if running and self.server is None:
                        await self.serve()
                    rc, out = await self.cli("codex", "--dir", str(self.work), flag, env={"CLAUDECODE": "1"})
                    self.assertEqual(rc, 0, out)
                    self.assertIs(self.raw().get(field), want)
        self.assertEqual([o for o in self.sent if o.get("t") == "ask"], [])

    # ---- the narrowing direction never asks
    async def test_narrowing_never_asks(self):
        await self.base("--unfenced")                       # never paired yet: written
        self.assertIs(self.raw()["fence"], False)
        self.pair()
        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")   # off → off: no change of scope
        self.assertEqual(rc, 0, out)
        rc, out = await self.cli("codex", "--dir", str(self.work))                 # off → on, an Agent, serve not running
        self.assertEqual(rc, 0, out)
        self.assertIs(self.raw()["fence"], True)
        await self.serve()
        rc, out = await self.cli("codex", "--dir", str(self.work), "--allow-docker", owner=True)
        self.assertEqual(rc, 0, out)
        self.assertIs(self.raw().get("docker"), True)
        rc, out = await self.cli("codex", "--dir", str(self.work), "--allow-docker")   # on → on
        self.assertEqual(rc, 0, out)
        rc, out = await self.cli("codex", "--dir", str(self.work))                 # docker on → off, an Agent, serve running
        self.assertEqual(rc, 0, out)
        self.assertNotIn("docker", self.raw())
        self.assertEqual([o for o in self.sent if o.get("t") == "ask"], [])

    # The one "was automatic, is a card now" exception of this change for the #537 release replay, as an exact tuple.
    # A replayed request may newly card only when ALL FOUR hold; every other shape of `agentj agent` stays automatic.
    REPLAY_EXCEPTION = {
        "command": ("agentj agent <claude|codex|opencode|kimi|qwen|dsh> --unfenced",
                    "agentj agent <claude|codex|opencode|kimi|qwen|dsh> --allow-docker"),
        "stored_entry": ("agent.fence is not false", "agent.docker is not true"),     # paired with the command above
        "caller": "no control.sock claim (ctl_auth.refusal is not None)",
        "host": "pairing history (State.paired_ever() is True)",
    }

    async def test_the_new_card_needs_all_four_facts(self):
        cards = lambda: len([o for o in self.sent if o.get("t") == "ask"])   # noqa: E731
        await self.base()
        await self.serve()
        for flag, field, want, _, _ in self.FLAGS:                      # host: never paired → automatic
            rc, out = await self.cli("codex", "--dir", str(self.work), flag)
            self.assertEqual((rc, cards()), (0, 0), out)
            await self.base()
        self.pair()
        self.s = _ready(self.host, self.phone)
        for i, (flag, field, want, _, _) in enumerate(self.FLAGS):
            with self.subTest(flag=flag):
                rc, out = await self.cli("codex", "--dir", str(self.work))              # command: no flag → automatic
                self.assertEqual((rc, cards()), (0, i), out)
                rc, out = await self.cli("codex", "--dir", str(self.work), flag, owner=True)   # caller: claim → automatic
                self.assertEqual((rc, cards()), (0, i), out)
                rc, out = await self.cli("codex", "--dir", str(self.work), flag)        # stored entry: already wide → automatic
                self.assertEqual((rc, cards()), (0, i), out)
                await self.base()
                rc, out = await self.cli("codex", "--dir", str(self.work), flag)        # all four → the card
                self.assertNotEqual(rc, 0, out)
                await self.answer(await self.card(i + 1), False)
                self.assertEqual(cards(), i + 1)
        self.assertEqual(len(self.REPLAY_EXCEPTION["command"]), len(self.FLAGS))

    async def test_setting_already_in_effect_is_not_written_by_an_agent(self):
        await self.base()
        self.pair()
        await self.serve()
        self.host.preferences = preferences.validate({**self.host.preferences, "agent": {
            **self.host.preferences.get("agent", {}), "isolation": False}})
        before = self.st.config_path.read_bytes()
        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.st.config_path.read_bytes(), before)
        self.assertEqual([o for o in self.sent if o.get("t") == "ask"], [])

    async def test_a_host_that_cannot_confirm_the_caller_refuses(self):
        """An older serve (no such command) and one that hangs up: nothing written, also for the owner's terminal."""
        await self.base()
        self.pair()
        before = self.st.config_path.read_bytes()

        async def older(r, w):
            while line := await r.readline():
                cmd = json.loads(line).get("cmd")
                w.write((json.dumps({"ok": True, "claim": "c" * 32} if cmd == "claim" else
                                    {"ok": False, "error": "unknown command"}) + "\n").encode())
                await w.drain()
            w.close()

        async def hangs_up(r, w):
            w.close()
        for handler in (older, hangs_up):
            server = await asyncio.start_unix_server(handler, str(self.st.sock_path))
            try:
                for owner in (False, True):
                    with self.subTest(handler=handler.__name__, owner=owner):
                        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced", owner=owner)
                        self.assertNotEqual(rc, 0, out)
                        self.assertIn("没有改动", out)
                        self.assertEqual(self.st.config_path.read_bytes(), before)
            finally:
                server.close()
                await server.wait_closed()
                self.st.sock_path.unlink(missing_ok=True)

    async def test_owner_terminal_is_logged_by_serve(self):
        await self.base()
        self.pair()
        await self.serve()
        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced", owner=True)
        self.assertEqual(rc, 0, out)
        self.assertIs(self.raw()["fence"], False)
        rows = [json.loads(x) for x in self.st.log_path.read_text().splitlines() if '"agent_scope"' in x]
        self.assertEqual([(r.get("by"), r.get("change")) for r in rows], [("claim", "isolation")])   # F5

    async def test_left_the_process_tree_but_carries_a_harness_marker(self):
        """Re-parented away from serve, yet started from an Agent harness: no claim — the card, nothing written."""
        await self.base()
        self.pair()
        await self.serve()
        before = self.st.config_path.read_bytes()
        for flag, _, _, key, _ in self.FLAGS:
            rc, out = await self.cli("codex", "--dir", str(self.work), flag, owner=True, env={"CLAUDECODE": "1"})
            self.assertNotEqual(rc, 0, out)
            self.assertIn("没有改动", out)
            self.assertEqual(self.st.config_path.read_bytes(), before)
        self.assertEqual(len([o for o in self.sent if o.get("t") == "ask"]), 2)
        rows = [x for x in self.st.log_path.read_text().splitlines() if '"agent_scope"' in x]
        self.assertEqual(rows, [])

    # ---- F2: "never paired" is read from devices.json itself; doubt counts as paired
    async def test_unreadable_revocation_journal_is_not_never_paired(self):
        await self.base()
        self.pair()
        self.assertFalse(self.st.removed_path.exists())     # the common state: one phone, never removed
        journal = self.st.root / "credential_revocations.json"
        before = self.st.config_path.read_bytes()
        for damage in (b"{not json", b" " * 130000, b"[]"):
            journal.write_bytes(damage)
            self.assertEqual(State(self.d / "s").devices(), {})         # what the gate must not rely on
            for flag, _, _, _, _ in self.FLAGS:
                with self.subTest(damage=damage[:8], flag=flag):
                    rc, out = await self.cli("codex", "--dir", str(self.work), flag)
                    self.assertNotEqual(rc, 0, out)
                    self.assertEqual(self.st.config_path.read_bytes(), before)

    async def test_unreadable_device_list_is_not_never_paired(self):
        await self.base()
        before = self.st.config_path.read_bytes()
        for damage in (b"{not json", b"[]", b"\xff\xfe"):
            self.st.devices_path.write_bytes(damage)
            with self.subTest(damage=damage):
                rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")
                self.assertNotEqual(rc, 0, out)
                self.assertIn("没有改动", out)
                self.assertEqual(self.st.config_path.read_bytes(), before)

    async def test_device_list_that_is_not_a_readable_file(self):
        await self.base()
        before = self.st.config_path.read_bytes()
        dev = self.st.devices_path
        dev.unlink(missing_ok=True)
        dev.mkdir()                                         # a directory: unreadable → counts as paired
        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.st.config_path.read_bytes(), before)
        dev.rmdir()
        if os.geteuid():                                    # root reads through mode 000
            dev.write_text("{}")
            dev.chmod(0)
            self.addCleanup(lambda: dev.exists() and dev.chmod(0o600))
            rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")
            self.assertNotEqual(rc, 0, out)
            self.assertEqual(self.st.config_path.read_bytes(), before)
            dev.chmod(0o600)
            dev.unlink()
        # A dangling symlink reads as "no file" — as State.devices() reads it: the host has no usable device at all
        # then, nothing could answer a card, and making the link needs write access to the state folder (which can
        # write config.json itself). Asserted as it is, so a change of this reading is a decision, not an accident.
        dev.symlink_to(self.d / "nowhere.json")
        self.assertFalse(State(self.d / "s").paired_ever())
        rc, out = await self.cli("codex", "--dir", str(self.work), "--unfenced")
        self.assertEqual(rc, 0, out)
        self.assertIs(self.raw()["fence"], False)
        dev.unlink()
        dev.symlink_to(self.d / "real.json")                # a link to a real list is followed
        (self.d / "real.json").write_text(json.dumps({"d" * 16: {"name": "x"}}))
        self.assertTrue(State(self.d / "s").paired_ever())

    # ---- F3: "nothing changed" is true for the dsh route file too
    async def test_refused_dsh_route_writes_no_file(self):
        self.pair()

        def tree():
            return {str(f.relative_to(self.d)): f.stat().st_size for f in self.d.rglob("*")
                    if f.is_file() and not f.name.startswith("owner-") and "__pycache__" not in f.parts}
        before = tree()
        rc, out = await self.cli("dsh", "--dir", str(self.work), "--base-url", "http://127.0.0.1:9/v1", "--model", "m-1",
                                 "--unfenced")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("没有改动", out)
        self.assertNotIn("路线已写入", out)
        self.assertEqual(tree(), before)

    # ---- F4: the doctor hint names the command that really turns the fence back on
    async def test_doctor_hint_follows_where_the_fence_was_turned_off(self):
        await self.base("--unfenced")
        self.assertIn("agentj agent codex --dir", doctor.check_fence(self.st)["hint"])
        await self.base()
        text = preferences.edit(preferences.read()[0], "agent.isolation", False)
        preferences.path().parent.mkdir(parents=True, exist_ok=True)
        preferences.path().write_text(text)
        row = doctor.check_fence(State(self.d / "s"))
        self.assertIn("agentj config set agent.isolation true", row["hint"])
        self.assertNotIn("agentj agent codex", row["hint"])

    async def test_unknown_widening_is_refused_by_serve(self):
        self.pair()
        await self.serve()
        r, w = await asyncio.open_unix_connection(str(self.st.sock_path))
        for widen in (["everything"], [], "isolation", [["isolation"]]):
            w.write((json.dumps({"cmd": "agent_scope", "widen": widen}) + "\n").encode())
            await w.drain()
            got = json.loads(await r.readline())
            self.assertEqual((got.get("ok"), got.get("owner"), got.get("code")), (False, None, "bad_request"), got)
        w.close()
        await w.wait_closed()
        self.assertEqual([o for o in self.sent if o.get("t") == "ask"], [])


class UndoOnlyNarrows(unittest.TestCase):
    """F1: `agentj agent switch --undo` and a switch rollback put an earlier entry back. They never widen: isolation
    stays on and docker stays off unless the entry stored at that moment already has them off / on."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="u0372-undo-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        unit = pathlib.Path(self.tmp.name) / "units"
        p = mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name))
        p.start()
        self.addCleanup(p.stop)
        self.clock = Clock()

    def job(self, st, fake, target, **kw):
        return sw.worker(st, target, None, probe=kw.pop("probe", None) or fake_probe(), ctl=fake, restart=kw.pop("restart", fake.restart), status=UP,
                         ready=lambda st, limit: "idle", sleep=self.clock.sleep, clock=self.clock, **kw)

    def narrow(self, st):
        """The owner's `agentj agent <kind>` without a flag: the stored entry is fenced, no docker."""
        e = st.config()["agent"]
        st.set_agent_config(e["kind"], e["dir"], e.get("model"), fence=True, docker=False, record_root=False)

    def test_undo_after_a_narrowing_stays_narrow(self):
        for was in ({"fence": False}, {"docker": True}):
            with self.subTest(was=was):
                d = tempfile.mkdtemp(dir=self.tmp.name)
                st = mk_state(d, "claude", **was)
                fake = FakeServe(st, "claude")
                self.assertEqual(self.job(st, fake, "opencode", force=True), 0)
                self.assertEqual(sw._previous(st)["raw"].get(*list(was.items())[0][:1]), list(was.values())[0])
                self.narrow(st)
                self.assertEqual(self.job(st, fake, None, undo=True), 0)
                e = st.config()["agent"]
                self.assertEqual((e["kind"], e["fence"], e.get("docker", False)), ("claude", True, False))
                self.assertEqual((st.agent_config()["fence"], st.agent_config()["docker"]), (True, False))

    def test_undo_without_a_narrowing_keeps_the_owners_choice(self):
        """Nothing was narrowed in between: the entry comes back as it was (the undo is not a widening)."""
        st = mk_state(self.tmp.name, "claude", fence=False)
        fake = FakeServe(st, "claude")
        raw = st.config()["agent"]
        self.assertEqual(self.job(st, fake, "opencode", force=True), 0)
        self.assertIs(st.config()["agent"]["fence"], False)             # the switch copied the stored value
        self.assertEqual(self.job(st, fake, None, undo=True), 0)
        self.assertEqual(st.config()["agent"], raw)

    def test_rollback_after_a_narrowing_during_the_switch_stays_narrow(self):
        for was in ({"fence": False}, {"docker": True}):
            with self.subTest(was=was):
                d = tempfile.mkdtemp(dir=self.tmp.name)
                st = mk_state(d, "claude", **was)
                fake = FakeServe(st, "claude")
                fake.behaviour["opencode"] = {"status": "error"}        # the new harness does not come up → rollback

                def restart():
                    if st.config()["agent"]["kind"] == "opencode":      # while the switch is under way
                        self.narrow(st)
                    fake.restart()
                self.assertNotEqual(self.job(st, fake, "opencode", force=True, restart=restart), 0)
                e = st.config()["agent"]
                self.assertEqual((e["kind"], e["fence"], e.get("docker", False)), ("claude", True, False))

    def test_switch_keeps_a_narrowing_made_while_its_gate_ran(self):
        """Round 2: the job read the entry when it started; the owner fenced the Agent while the gate probe ran. The new
        entry is written from the entry stored at the write, not from that snapshot."""
        for was in ({"fence": False}, {"docker": True}):
            with self.subTest(was=was):
                d = tempfile.mkdtemp(dir=self.tmp.name)
                st = mk_state(d, "claude", **was)
                fake = FakeServe(st, "claude")
                inner = fake_probe()

                def probe(**kw):
                    self.narrow(st)                                     # during the pre-check window
                    return inner(**kw)
                self.assertEqual(self.job(st, fake, "opencode", probe=probe), 0)
                e = st.config()["agent"]
                self.assertEqual((e["kind"], e["fence"], e.get("docker", False)), ("opencode", True, False))

    def test_switch_without_a_narrowing_copies_the_stored_choice(self):
        st = mk_state(self.tmp.name, "claude", fence=False)
        self.assertEqual(self.job(st, FakeServe(st, "claude"), "opencode"), 0)
        self.assertIs(st.config()["agent"]["fence"], False)
        d = tempfile.mkdtemp(dir=self.tmp.name)
        st = mk_state(d, "claude", docker=True)
        self.assertEqual(self.job(st, FakeServe(st, "claude"), "opencode"), 0)
        self.assertIs(st.config()["agent"].get("docker"), True)

    def test_narrowed(self):
        n = State.narrowed
        wide = {"kind": "claude", "fence": False, "docker": True}
        self.assertEqual(n({"fence": False, "docker": True}, wide), wide)
        self.assertEqual(n({"fence": True}, wide), {"kind": "claude", "fence": True})
        self.assertEqual(n({"fence": False}, wide), {"kind": "claude", "fence": False})
        for cur in (None, {}, "x", {"fence": 0, "docker": 1}):          # no entry, or not exactly false / true
            self.assertEqual(n(cur, wide), {"kind": "claude", "fence": True})
        self.assertEqual(wide, {"kind": "claude", "fence": False, "docker": True})   # the caller's dict is not touched

    def test_restore_with_no_stored_entry_is_fenced(self):
        st = mk_state(self.tmp.name, "claude")
        st.set_agent_config(None)
        sw._restore(st, {"kind": "claude", "dir": str(pathlib.Path(self.tmp.name) / "work"), "model": None,
                         "fence": False, "docker": True})
        e = st.config()["agent"]
        self.assertEqual((e["fence"], "docker" in e), (True, False))

    def test_a_fenced_agent_can_ask_serve_for_the_undo_job(self):
        """Why F1 matters: the job spec an Agent sends over elevate.sock (`harness_job`) is accepted with `undo` and
        launched by serve for any caller, with no card — so the clamp, not the caller, is what keeps the fence on."""
        st = mk_state(self.tmp.name, "claude", fence=False)
        fake = FakeServe(st, "claude")
        self.assertEqual(self.job(st, fake, "opencode", force=True), 0)
        self.narrow(st)
        spec, err = sw.check_spec({"t": "harness_job", "kind": "switch", "undo": True})
        self.assertIsNone(err)
        launched = []

        def launch(st_, argv, *a, **k):
            launched.append(argv)
            self.job(st_, fake, None, undo=True)
        res = sw.host_job(st, {"t": "harness_job", "kind": "switch", "undo": True}, launch)
        self.assertTrue(res.get("ok"), res)
        self.assertIn("--undo", launched[0])
        self.assertIs(st.config()["agent"]["fence"], True)


class RootException(unittest.TestCase):
    """Round 2: the working-root bootstrap exception (preferences.transact) asks State.paired_ever() too."""
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="u0372-root-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        self.d = pathlib.Path(t.name)
        env = mock.patch.dict(os.environ, {"HOME": str(self.d / "home"), "XDG_CONFIG_HOME": str(self.d / "xdg-config")})
        env.start()
        self.addCleanup(env.stop)
        self.st = State(self.d / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        preferences.ensure()
        self.work = self.d / "work"
        self.work.mkdir()

    def test_paired_host_with_an_unreadable_revocation_journal_takes_no_offline_root(self):
        self.st.add_device(os.urandom(32), "phone")
        for damage in (b"{not json", b" " * 130000):
            (self.st.root / "credential_revocations.json").write_bytes(damage)
            st = State(self.d / "state")
            self.assertEqual(st.devices(), {})
            before = st.config_path.read_bytes()
            with self.assertRaises(preferences.ConfigError):
                st.set_agent_config("claude", str(self.work))
            self.assertEqual(st.config_path.read_bytes(), before)
            self.assertFalse(preferences.get(preferences.effective(st), "agent.working_root", ""))
            self.assertTrue(st.paired_ever())

    def test_never_paired_host_still_takes_its_first_folder(self):
        self.assertFalse(self.st.paired_ever())
        self.st.set_agent_config("claude", str(self.work))
        self.assertEqual(preferences.get(preferences.effective(self.st), "agent.working_root"), os.path.realpath(self.work))


class CallSites(unittest.TestCase):
    """Review round 3: `State.set_agent_config(narrow=…)` defaults to False (the gated owner path may widen). Every other
    writer must say `narrow=` itself: a new call site that neither passes it nor is reviewed here fails this test."""
    REVIEWED = {("cli.py", "cmd_agent"): 2}     # the entry after `_scope_gate`, and `agentj agent off` (writes no entry)

    @staticmethod
    def scan(source, name):
        import ast
        out = []
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                continue
            for node in ast.iter_child_nodes(fn):
                stack = [node]
                while stack:
                    n = stack.pop()
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue                              # that function is visited on its own
                    if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) == "set_agent_config":
                        out.append((name, getattr(fn, "name", "<module>"), any(k.arg == "narrow" for k in n.keywords)))   # `**kwargs` alone says nothing: implicit unless reviewed
                    stack.extend(ast.iter_child_nodes(n))
        return out

    def test_the_list_shows_both_commands(self):
        from agentj import uv_list
        rows = {r["command"]: r["reason"] for r in uv_list.entries()}
        for flag, zh in (("--unfenced", "关掉 Agent 隔离"), ("--allow-docker", "打开 Docker 访问")):
            reason = rows["agentj agent <harness> " + flag]
            self.assertIn(zh, reason)
            self.assertIn(" / ", reason)
            self.assertIn("never-paired host is exempt", reason)
        self.assertEqual(len(rows), len(uv_list.entries()))              # no duplicate command
        self.assertNotIn("agentj agent <harness> --unfenced", uv_list.COMMANDS)   # display only: the gate decides, not required()
        self.assertFalse(uv_list.required("agentj agent", {"command": "agentj agent codex --unfenced"}))

    def test_scanner_sees_an_unreviewed_call(self):
        src = "def a(st):\n    st.set_agent_config('x')\ndef b(st):\n    st.set_agent_config('x', narrow=True)\n" \
              "class C:\n    async def c(self):\n        def d():\n            self.st.set_agent_config(None)\n        d()\n" \
              "def e(st, **kw):\n    st.set_agent_config('x', **kw)\ndef f(st, **kw):\n    st.set_agent_config('x', narrow=True, **kw)\n"
        self.assertEqual(sorted(self.scan(src, "m.py")), [("m.py", "a", False), ("m.py", "b", True), ("m.py", "d", False),
                                                          ("m.py", "e", False), ("m.py", "f", True)])

    def test_every_call_site_passes_narrow_or_is_reviewed(self):
        pkg = HERE.parent / "agentj"
        found = [hit for f in sorted(pkg.rglob("*.py")) for hit in self.scan(f.read_text(), str(f.relative_to(pkg)))]
        self.assertTrue(any(explicit for _, _, explicit in found), "the switch passes narrow=True")
        implicit = {}
        for name, fn, explicit in found:
            if not explicit:
                implicit[(name, fn)] = implicit.get((name, fn), 0) + 1
        self.assertEqual(implicit, self.REVIEWED,
                         "a set_agent_config call without narrow=: pass narrow=True unless it is the gated owner path "
                         "(then review it and list it here)")


class RestartBySource(offlist._Matrix):
    """Decision 0300 item 3 option A (chairman 2026-11-23): the host no longer brokers `agentj service restart` (#510's
    card + fixed host action; `restart_guard.schedule` is gone). What must hold without it: the command is an ordinary
    off-list write — the owner's own turn runs it without a card, every non-owner source is refused, nobody gets a card.
    Every turn source and every adapter entry of test_offlist_zero_cards_0307, built as their injectors build them."""
    COMMANDS = ("agentj service restart", "systemctl --user restart agentj.service")

    async def test_owner_turn_runs_it_and_every_non_owner_source_is_refused(self):
        seen = set()
        for name, (send, extra, trust) in self.sources.all().items():
            if trust == "host":            # heartbeat / scheduled task / settings: decided elsewhere (decision item 1)
                continue
            seen.add(trust)
            for entry in ("claude", "flagged", "unflagged"):
                for command in self.COMMANDS:
                    with self.subTest(source=name, entry=entry, command=command):
                        kind, behavior = await self.run_one(entry, send, extra, "Bash", {"command": command})
                        self.assertEqual((kind, behavior), ("none", "allow" if trust == "owner" else "deny"))
        self.assertEqual(seen, {"owner", "host_read", "external", "unknown"})

    async def test_host_trust_sources_run_it_without_a_card_as_on_main(self):
        """Pinned as it is today, not a judgement: `approval_offlist.decide` treats host trust like the owner, so a
        heartbeat, a scheduled task and a settings page run the restart with no card. Changing that is a decision."""
        names = set()
        for name, (send, extra, trust) in self.sources.all().items():
            if trust != "host":
                continue
            names.add(name)
            for entry in ("claude", "flagged", "unflagged"):
                for command in self.COMMANDS:
                    with self.subTest(source=name, entry=entry, command=command):
                        kind, behavior = await self.run_one(entry, send, extra, "Bash", {"command": command})
                        self.assertEqual((kind, behavior), ("none", "allow"))
        self.assertEqual(names, {"heartbeat", "scheduled_task", "settings"})

    def test_no_host_side_restart_scheduler_is_left(self):
        from agentj import restart_guard
        self.assertFalse(hasattr(restart_guard, "schedule"))
        self.assertTrue(callable(restart_guard.prove))      # `service_restart_ready` (control.sock) still asks it


if __name__ == "__main__":
    unittest.main()
