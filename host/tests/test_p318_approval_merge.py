"""P318 item 4 (Leo 10-15 21:56): two 「允许 Docker」 cards back to back — allowing the first made the second see a changed
configuration, so it answered 「配置在等待批准期间已被改过」 and nothing applied.

Now an Agent's security config request is a per-key patch: a new request for a key a pending card already asks about merges
into ONE card (the old one ends `gone`, approvals.log reason `superseded`); an allow replays the patch on the configuration as
it is then — unrelated changes meanwhile are kept; a security key someone else set to another value → not applied.
Hermetic: HOME / XDG_CONFIG_HOME / AGENTJ_STATE_DIR are a throw-away dir (never the real ~/.config/agentj).
"""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402,F401  P202: fixtures on tmpfs
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentj.state import State


class ConfigCards(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        t = tempfile.TemporaryDirectory(prefix="p318-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        env = patch.dict(os.environ, {"HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / ".config"),
                                      "AGENTJ_STATE_DIR": str(self.root / "s")})
        env.start()
        self.addCleanup(env.stop)
        from agentj import preferences as p
        self.p = p
        self.assertTrue(str(p.path()).startswith(str(self.root)))
        self.st = State()
        self.st.init()
        p.ensure()
        from agentj.serve import Host
        self.host = Host(self.st, read_stdin=False)
        self.server = await asyncio.start_unix_server(self.host.on_ctl, str(self.st.sock_path))
        self.cards = []                      # [(changes, answer future, gone future)]
        self.notices = []
        self.host.agent_notice = self.notices.append

        async def ask(tool, tool_input, gone=None, **kw):
            fut = asyncio.get_running_loop().create_future()
            self.cards.append((tool_input["changes"], fut, gone))
            done, _ = await asyncio.wait({fut, gone}, return_when=asyncio.FIRST_COMPLETED)
            return fut.result() if fut in done else {"behavior": "deny"}
        self.host.ask = ask

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def ctl(self, req):
        r, w = await asyncio.open_unix_connection(str(self.st.sock_path))
        w.write((json.dumps(req) + "\n").encode())
        await w.drain()
        out = json.loads(await r.readline())
        w.close()
        await w.wait_closed()
        return out

    async def agent_set(self, key, value):
        """What an Agent's `agentj config set` sends: the whole file text with one key edited, no claim."""
        return await self.ctl({"cmd": "config_apply", "raw": self.p.edit(self.p.read()[0], key, value)})

    async def settle(self, cond):
        for _ in range(100):
            if cond():
                return True
            await asyncio.sleep(.02)
        return False

    def open_cards(self):
        return [c for c in self.cards if not c[1].done() and not c[2].done()]

    def log(self, ev):
        return [json.loads(x) for x in self.st.log_path.read_text().splitlines() if f'"{ev}"' in x]

    async def test_same_key_twice_is_one_card_and_the_allow_applies(self):
        for _ in range(2):                         # an identical repeat keeps the pending card (no second push)
            out = await self.agent_set("agent.allow_docker", True)
            self.assertEqual(out["code"], "owner_approval")
            await self.settle(lambda: len(self.cards) >= 1)
        self.assertEqual(len(self.cards), 1)
        self.assertFalse(self.cards[0][2].done())
        self.assertEqual(len(self.host._cfg_cards), 1)
        self.assertEqual(len(self.log("ctl_config_card_repeat")), 1)
        self.cards[0][1].set_result({"behavior": "allow"})
        self.assertTrue(await self.settle(lambda: self.p.get(self.host.preferences, "agent.allow_docker") is True))
        self.assertEqual(self.notices, [])
        self.assertEqual(self.p.get(self.p.read()[1], "agent.allow_docker"), True)   # committed to the file
        self.assertTrue(await self.settle(lambda: len(self.log("ctl_config_card_done")) == 1))
        self.assertEqual([r["result"] for r in self.log("ctl_config_card_done")], ["applied"])

    async def test_new_value_for_the_same_key_supersedes_and_the_allow_applies(self):
        await self.agent_set("agent.approvals", "native")
        await self.settle(lambda: len(self.cards) == 1)
        out = await self.agent_set("agent.approvals", "strict")
        self.assertEqual(out["code"], "owner_approval")
        self.assertTrue(await self.settle(lambda: len(self.cards) == 2))
        self.assertEqual(self.cards[0][2].result(), "superseded")
        self.assertEqual(self.open_cards()[0][0], {"agent.approvals": "strict"})
        self.open_cards()[0][1].set_result({"behavior": "allow"})
        self.assertTrue(await self.settle(lambda: self.p.get(self.host.preferences, "agent.approvals") == "strict"))
        self.assertTrue(await self.settle(lambda: len(self.log("ctl_config_card_done")) == 2))
        self.assertEqual(sorted(r["result"] for r in self.log("ctl_config_card_done")), ["applied", "superseded"])

    async def test_allow_racing_a_merge_is_logged_superseded_after_allow(self):
        await self.agent_set("agent.allow_docker", True)
        await self.settle(lambda: len(self.cards) == 1)
        self.cards[0][1].set_result({"behavior": "allow"})
        self.cards[0][2].set_result("superseded")      # the merge lands in the same tick as the device's allow
        self.assertTrue(await self.settle(lambda: self.log("ctl_config_card_done")))
        self.assertEqual(self.log("ctl_config_card_done")[0]["result"], "superseded_after_allow")
        self.assertFalse(self.p.get(self.host.preferences, "agent.allow_docker"))

    async def test_dotted_alias_cannot_hide_a_change_on_the_ctl_path(self):
        # {agent:{allow_docker:true}, "agent.allow_docker": false}: approval saw false, the runtime would read true
        text = self.p.edit(self.p.read()[0], "agent.allow_docker", True).rstrip()[:-1].rstrip().rstrip(",") + \
            ',\n  "agent.allow_docker": false\n}\n'
        import json5
        doc = json5.loads(text, allow_duplicate_keys=False)          # well-formed JSON5: only the dotted key is wrong
        self.assertEqual((doc["agent"]["allow_docker"], doc["agent.allow_docker"]), (True, False))
        out = await self.ctl({"cmd": "config_apply", "raw": text})
        # P324: the dotted literal is dropped (the runtime never read it) — the card shows the value the runtime will read
        self.assertFalse(out["ok"])
        self.assertEqual((out["key"], out["code"]), ("agent.allow_docker", "owner_approval"))
        await self.settle(lambda: len(self.cards) == 1)
        self.assertEqual(self.cards[0][0], {"agent.allow_docker": True})
        self.assertFalse(self.p.get(self.host.preferences, "agent.allow_docker"))
        self.assertFalse(self.p.get(self.p.read()[1], "agent.allow_docker"))
        self.assertEqual(self.log("ctl_config_card")[-1]["keys"], "agent.allow_docker")

    async def test_replay_reads_under_the_lock_never_overwrites_a_concurrent_edit(self):
        await self.agent_set("agent.allow_docker", True)
        await self.settle(lambda: len(self.cards) == 1)
        await self.host.config_apply_lock.acquire()      # an owner apply is in progress
        try:
            self.cards[0][1].set_result({"behavior": "allow"})
            await asyncio.sleep(.1)                       # the replay is waiting now (the old code had read the file here)
            path = self.p.path()                          # … and the owner's apply commits meanwhile
            path.write_text(self.p.edit(path.read_text(), "appearance.theme", "dark"))
            self.host.preferences = self.p.validate(self.p.read()[1])
        finally:
            self.host.config_apply_lock.release()
        self.assertTrue(await self.settle(lambda: self.p.get(self.host.preferences, "agent.allow_docker") is True))
        doc = self.p.read()[1]
        self.assertEqual((self.p.get(doc, "agent.allow_docker"), self.p.get(doc, "appearance.theme")), (True, "dark"))

    async def test_final_value_not_what_the_card_showed_is_a_conflict(self):
        await self.agent_set("agent.approvals", "native")
        await self.settle(lambda: len(self.cards) == 1)
        real = self.p.edit

        def skewed(text, key, value, remove=False):    # an edit that lands another value than the card showed
            return real(text, key, "strict" if key == "agent.approvals" else value, remove=remove)
        with patch.object(self.p, "edit", skewed):
            self.cards[0][1].set_result({"behavior": "allow"})
            self.assertTrue(await self.settle(lambda: self.notices))
        self.assertEqual(self.p.get(self.host.preferences, "agent.approvals"), "standard")   # nothing applied
        self.assertEqual(self.p.get(self.p.validate(self.p.read()[1]), "agent.approvals"), "standard")
        self.assertEqual(self.log("ctl_config_card_done")[-1]["result"], "stale")

    async def test_merge_keeps_both_keys_newest_value(self):
        await self.agent_set("agent.allow_docker", True)
        await self.settle(lambda: len(self.cards) == 1)
        # the same file text plus a second security key: overlaps the pending card's key → one card with both
        raw = self.p.edit(self.p.edit(self.p.read()[0], "agent.allow_docker", True), "agent.high_risk_warnings", True)
        out = await self.ctl({"cmd": "config_apply", "raw": raw})
        self.assertEqual(out["code"], "owner_approval")
        self.assertTrue(await self.settle(lambda: len(self.cards) == 2))
        self.assertEqual(set(self.open_cards()[0][0]), {"agent.allow_docker", "agent.high_risk_warnings"})

    async def test_allow_after_an_unrelated_change_applies_on_the_latest_config(self):
        out = await self.agent_set("agent.allow_docker", True)
        self.assertEqual(out["code"], "owner_approval")
        await self.settle(lambda: len(self.cards) == 1)
        res = await self.host.apply_preferences(self.p.edit(self.p.read()[0], "appearance.theme", "dark"))   # owner, meanwhile
        self.assertTrue(res["ok"], res)
        self.cards[0][1].set_result({"behavior": "allow"})
        self.assertTrue(await self.settle(lambda: self.p.get(self.host.preferences, "agent.allow_docker") is True))
        self.assertEqual(self.p.get(self.host.preferences, "appearance.theme"), "dark")   # the other change is kept
        doc = self.p.read()[1]
        self.assertEqual((self.p.get(doc, "agent.allow_docker"), self.p.get(doc, "appearance.theme")), (True, "dark"))
        self.assertEqual(self.notices, [])

    async def test_allow_after_a_conflicting_same_key_change_is_refused(self):
        out = await self.agent_set("agent.approvals", "native")
        self.assertEqual(out["code"], "owner_approval")
        await self.settle(lambda: len(self.cards) == 1)
        res = await self.host.apply_preferences(self.p.edit(self.p.read()[0], "agent.approvals", "strict"))  # owner, meanwhile
        self.assertTrue(res["ok"], res)
        self.cards[0][1].set_result({"behavior": "allow"})
        self.assertTrue(await self.settle(lambda: self.notices))
        self.assertIn("The configuration changed while waiting; this approval was not applied.", self.notices[0])
        self.assertEqual(self.p.get(self.host.preferences, "agent.approvals"), "strict")    # never reverted
        self.assertEqual(self.p.get(self.p.read()[1], "agent.approvals"), "strict")
        self.assertEqual(self.log("ctl_config_card_done")[-1]["result"], "stale")

    async def test_unapproved_security_edit_in_the_file_never_rides_along(self):
        await self.agent_set("agent.allow_docker", True)
        await self.settle(lambda: len(self.cards) == 1)
        path = self.p.path()               # an Agent writes the file directly, without asking
        path.write_text(self.p.edit(path.read_text(), "agent.high_risk_warnings", True))
        self.cards[0][1].set_result({"behavior": "allow"})
        self.assertTrue(await self.settle(lambda: self.notices))
        self.assertFalse(self.p.get(self.host.preferences, "agent.allow_docker"))
        self.assertFalse(self.p.get(self.host.preferences, "agent.high_risk_warnings"))

    def test_validate_refuses_dotted_keys(self):
        for doc in ({"agent.allow_docker": False}, {"agent": {"allow_docker": True}, "agent.allow_docker": False},
                    {"agent": {"isolation.x": 1}}):
            with self.assertRaises(self.p.ConfigError) as cm:
                self.p.validate(doc)
            self.assertIn('may not contain "."', str(cm.exception.result()))
        self.p.validate(self.p.read()[1])                 # the shipped defaults have no dotted key

    def test_upgrade_old_file_with_dotted_keys_reads_without_error(self):
        # P324 red line: a config.json5 from before P318 with literal dotted keys must not become a config error at upgrade
        path = self.p.path()
        old = '// mine\n{\n  version: 1,\n  "agent.allow_docker": true,\n  agent: {high_risk_warnings: true},\n  "voice.tts.mode": "off"\n}\n'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(old)
        raw, doc = self.p.read()
        self.assertEqual(raw, old)                        # the file is not rewritten
        self.assertNotIn("agent.allow_docker", doc)
        self.assertIs(self.p.get(doc, "agent.allow_docker"), None)   # never read before P318, still not read now
        self.assertTrue(self.p.get(doc, "agent.high_risk_warnings"))
        self.assertEqual(self.p.ignored(raw), ["agent.allow_docker", "voice.tts.mode"])
        self.p.validate(doc)
        self.assertIs(self.p.get(self.p.effective(), "agent.allow_docker"), False)
        # a later `config set` lands on the nested key the runtime reads, not on the inert literal
        new = self.p.edit(raw, "agent.allow_docker", True)
        self.assertIn('"agent.allow_docker": true', new)
        self.assertIs(self.p.get(self.p.parse(new), "agent.allow_docker"), True)
        new = self.p.edit(raw, "agent.high_risk_warnings", False)
        self.assertIs(self.p.get(self.p.parse(new), "agent.high_risk_warnings"), False)

    def test_flat_raises_on_a_duplicate_and_security_changes_reads_like_the_runtime(self):
        from agentj import ctl_auth
        with self.assertRaises(ctl_auth.DuplicateKey):
            ctl_auth._flat({"agent": {"allow_docker": True}, "agent.allow_docker": False})
        with self.assertRaises(ctl_auth.DuplicateKey):
            ctl_auth.security_changes({}, {"agent": {"allow_docker": True}, "agent.allow_docker": False})
        # a lone literal key is not what the runtime reads: no change reported, and validate refuses it anyway
        self.assertEqual(ctl_auth.security_changes({}, {"agent.allow_docker": True}), [])
        self.assertEqual(ctl_auth.security_changes({}, {"agent": {"allow_docker": True}}), ["agent.allow_docker"])

    def test_superseded_reason(self):
        from agentj import serve
        loop = asyncio.new_event_loop()
        try:
            f = loop.create_future()
            self.assertFalse(serve._superseded(f))
            f.set_result("superseded")
            self.assertTrue(serve._superseded(f))
            g = loop.create_future()
            g.set_result(True)
            self.assertFalse(serve._superseded(g))
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
