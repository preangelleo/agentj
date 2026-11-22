"""Git config value enumeration uses the existing request-bound UV gate."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from agentj import approval_level, approval_offlist, controls, danger, goal, passkey, permtool, provenance, shared_hook, uv_effect
from agentj.shared_risk import RiskGuard
from test_p336_approval_level import PROP, sign
import test_uv_list as fixture


class GitConfigReadFloor(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ListApproval.asyncSetUp
    open = fixture.ListApproval.open
    close = fixture.ListApproval.close

    def config_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        home = Path(directory.name)
        marker = "synthetic-config-marker"
        (home / ".gitconfig").write_text("[fixture]\n\tmarker = " + marker + "\n")
        git = shutil.which("git")
        self.assertIsNotNone(git)
        env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / "xdg"),
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
        return home, marker, git, env

    async def signed_setting(self, key, value):
        target = {"key": key, "value": value}
        signed = sign(self.ph, target)
        nonce = signed["n"]
        challenge = passkey.elevate_challenge(self.h.channel, self.ph.did, nonce, "approval_set", nonce,
                                              controls.object_digest("approval_set", target))
        signed["fa"] = self.ph.auth.assert_(challenge, b"0", nonce=b"0")
        with mock.patch.object(self.h, "_reload_runtime"):
            await self.h.on_approval_set(self.s, signed)
        self.assertTrue([msg for _, msg in self.sent if msg["t"] == "ctl_res"][-1]["ok"])
        self.assertEqual(approval_level.signed_value(self.st, key), value)

    async def deny_with_uv_required(self, argv):
        launch = mock.Mock(return_value="unexpected execution")

        async def check_then_run():
            if not await self.guard.check("Bash", {"command": shlex.join(argv)}):
                return None
            return launch()

        task = asyncio.create_task(check_then_run())
        for _ in range(100):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.002)
        self.assertFalse(task.done() and task.result() is not None, "unverified query ran")
        self.assertEqual(len(self.h.asks), 1)
        request = next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.close, task, request)
        self.assertTrue(request.require_passkey)
        card = self.h._ask_msg(request, self.s)
        missing_uv = self.ph.answer(card, True)
        missing_uv.pop("fa")
        await self.h._answer(self.s, missing_uv)
        self.assertFalse(request.fut.done())
        await self.h._answer(self.s, self.ph.answer(card, False))
        self.assertIsNone(await task)
        launch.assert_not_called()

    async def exercise_native_queries(self):
        home, marker, git, env = self.config_fixture()
        for option in ("--list", "-l"):
            result = subprocess.run([git, "config", option], cwd=home, env=env,
                                    capture_output=True, text=True, check=True)
            self.assertIn("fixture.marker=" + marker, result.stdout)
        self.guard = RiskGuard(self.h.agent)
        for args in (("config", "list"), ("config", "--list"), ("config", "-l")):
            with self.subTest(args=args):
                await self.deny_with_uv_required([git, "-C", str(home), *args])
        card_count = len([msg for _, msg in self.sent if msg["t"] == "ask"])
        self.assertTrue(await self.guard.check("Bash", {"command": "git status --short"}))
        self.assertEqual(len([msg for _, msg in self.sent if msg["t"] == "ask"]), card_count)

    async def test_signed_full_access_requires_uv_before_git_config_reads(self):
        await self.signed_setting(approval_level.KEY, "harness")
        self.assertEqual(approval_level.level(self.h.preferences, self.st), "harness")
        await self.exercise_native_queries()

    async def test_active_goal_requires_uv_before_git_config_reads(self):
        await self.signed_setting(approval_level.GOAL_KEY, True)
        self.h.goals.owner_asked()
        result = {}

        async def propose(_session, _frame):
            result.update(await self.h.goals.propose(PROP, wait=True))

        with mock.patch.object(self.h, "_app_frame", side_effect=propose), provenance.paired_request(
            lambda: self.h._paired_live(self.s)
        ):
            await self.h._app(self.s, {"t": "fixture-goal-proposal"})
        self.assertEqual(result.get("result"), "active")
        self.assertIsNotNone(self.h.goals.current())
        self.assertEqual(goal.load(self.st)["grants"], [])
        try:
            await self.exercise_native_queries()
        finally:
            self.h.goals.end("fixture-cleanup", by="owner")
            await asyncio.sleep(0)

    async def test_existing_denials_survive_git_query_classification(self):
        home, _, _, env = self.config_fixture()
        for command, source, background, reason in (
            ("git config list", approval_offlist._make("unknown", None, "unknown"), None, "off_list_write_unknown"),
            ("git config --list; cat " + shlex.quote(str(home / "coding" / ".env")),
             None, None, "off_list_credential"),
            ("git config list", None, "order", "git_config_read_source"),
        ):
            with self.subTest(command=command, background=background), mock.patch.dict(os.environ, env):
                actual_source = source or approval_offlist.source(self.h, background=background, order_owner=True)
                with mock.patch.object(approval_offlist, "source", return_value=actual_source):
                    result = await self.h.ask("Bash", {"command": command}, _harness_permission=True,
                                              background=background, _order_owner=True)
                self.assertEqual((result["behavior"], result["reason"]), ("deny", reason))
                self.assertFalse(self.h.asks)
                self.assertFalse([msg for _, msg in self.sent if msg["t"] == "ask"])

    async def test_request_bound_uv_can_authorize_only_this_query(self):
        task, ask = await self.open("Bash", {"command": "git config list"})
        signed = self.ph.answer(self.h._ask_msg(ask, self.s), True)
        await self.h._answer(self.s, signed)
        self.assertEqual((await task)["behavior"], "allow")
        retry, next_ask = await self.open("Bash", {"command": "git config list"})
        self.assertNotEqual(ask.rid, next_ask.rid)
        await self.h._answer(self.s, signed)
        self.assertFalse(next_ask.fut.done())
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(next_ask, self.s), False))
        self.assertEqual((await retry)["behavior"], "deny")

    def test_only_value_list_forms_receive_the_sensitive_effect(self):
        positives = ("git config list", "git -C . config list --show-origin", "git config --global --list",
                     "git config --list", "git config -l", "git config -zl")
        negatives = ("git status --short", "git config --get user.name", "git config list --name-only",
                     "git config --list --name-only", "git config list -h", "printf '%s' 'git config list'")
        for command in positives + negatives:
            with self.subTest(command=command):
                inp = {"command": command}
                verdict = danger.classify_shared("Bash", inp)
                effect = uv_effect.assess("Bash", inp)
                floor = approval_level.full_access_keep("Bash", inp, verdict)
                if command in positives:
                    self.assertEqual(effect.effects, ("sensitive_storage",))
                    self.assertIsNotNone(floor)
                else:
                    self.assertEqual(effect.effects, ())
                    self.assertIsNone(floor)

    def test_unavailable_shared_hook_refuses_value_listing(self):
        for command in ("git config list", "git config --list", "git config -l"):
            event = {"tool_name": "Bash", "tool_input": {"command": command}}
            fallback = shared_hook.fallback("PreToolUse", "codex", event, True)
            self.assertEqual(fallback["permissionDecision"], "deny")

    def test_independent_claude_hook_forces_host_permission_or_refuses(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "git config list"}}
        for extra, expected in (({}, "ask"), ({"agent_id": "synthetic-child"}, "deny")):
            with mock.patch("sys.stdin", io.StringIO(json.dumps({**event, **extra}))), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(danger.hook_main([]), 0)
            self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], expected)
        result = permtool.Link().ask({"tool_name": "Bash", "input": event["tool_input"]}, 1)
        self.assertEqual(result["behavior"], "deny")
