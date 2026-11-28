"""Isolated Host with genuine device signing and WebAuthn verification."""
import _hermetic  # noqa: F401,I001
import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentj import browser_ego, controls, provenance, wire
from creator_preview import browser_authority, browser_creator
from agentj.browser_login import RequestPhone
from test_controls import sign
from test_l1 import Phone, _host, _ready, _state
from test_p408_terminal import registered_passkey, tmpdir


BILI = {"t": "browser_creator", "site": "bilibili", "action": "archive_status"}
STUDIO = {"t": "browser_creator", "site": "youtube-studio", "action": "channel_status"}
CHANNEL = "UC" + "x" * 22


class CreatorGrantsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = self.enterContext(tmpdir())
        self.st = _state(root)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.phone = Phone(self.st)
        self.s = _ready(self.host, self.phone)
        self.s.p33 = True
        self.s.sign_pub = self.st.sign_key(self.phone.did)
        self.assertTrue(self.host._paired_live(self.s))
        self.closed = self.host.elevate.creator
        self.desk = self.host.elevate.creator = browser_authority.CreatorGrants(self.host)
        record, self.auth = registered_passkey()
        self.st.set_passkey(self.s.pub, record)
        browser_ego.bind_profile(self.st, "fixture-profile")
        self.rt = {"backend": "ego-lite", "runtime_id": ("fixture-cli", ("23:4",), "fixture-profile")}
        self.enterContext(patch.object(browser_authority, "FIELD_VERIFIED_SITES", frozenset(browser_creator.SITES)))
        self.enterContext(patch("agentj.browser.system", return_value="macos"))
        self.enterContext(patch("agentj.browser.settings", return_value={"enabled": True}))
        self.enterContext(patch("agentj.browser.runtime", side_effect=lambda st: self.rt))
        self.enterContext(patch("agentj.preferences.path", return_value=Path(root) / "prefs.json5"))
        self.read_calls = []
        self.enterContext(patch.object(browser_creator, "read", side_effect=self.read_fixture))
        self.actor = SimpleNamespace(proc=object(), kind="claude",
                                     cur_send=SimpleNamespace(phone_binding=RequestPhone.from_session(self.host, self.s)))
        self.host.agent = self.actor
        self.origin = {"verified": True, "message_id": 7, "channel": "phone"}
        self.enterContext(patch.object(browser_authority.approval_material, "verified_phone_chat",
                                      side_effect=lambda h, pid: (self.actor, self.origin) if pid == 111 else None))
        self.enterContext(patch.object(provenance, "untrusted_turn", return_value=False))

    def read_fixture(self, rt, site, account, action, live, until):
        if not live():
            return None
        self.read_calls.append((site, action))
        if action == "archive_status":
            return {"account": account, "archives": [{"id": "BV1234567890", "state": -1}]}
        return {"account": account}

    async def request(self, obj, session=None):
        start = len(self.sent)
        session = self.s if session is None else session
        active = []
        track = self.host._bg

        def spawn(coro, what):
            task = track(coro, what)
            if what == "browser_creator_grant":
                active.append(task)
            return task

        with patch.object(self.host, "_bg", side_effect=spawn):
            await self.host._app(session, {"r": "deadbeef", **obj})
        if active:
            await asyncio.wait_for(active[0], 2)
        # Host/Elevator deliberately handles the control next to the receive loop.
        for _ in range(50):
            for _, response in self.sent[start:]:
                if response.get("r") == "deadbeef":
                    return deepcopy(response)
            await asyncio.sleep(0.01)
        return None

    async def grant(self, site="bilibili", account="1234"):
        prepare = await self.request({"t": "browser_grant_prepare", "site": site, "account": account, "minutes": 15})
        self.assertTrue(prepare["ok"], prepare)
        target = prepare["target"]
        obj = sign(self.phone, "browser_grant", target, {"t": "browser_grant", "target": target})
        challenge = await self.request(obj)
        self.assertEqual(challenge["why"], "passkey_required")
        self.assertFalse(self.desk.grants.get(site))
        challenge = wire.unb64u(challenge["fa_ch"])
        proof = self.auth.assert_(challenge, b"", nonce=b"")
        receipt = {**obj, "fa": {key: proof[key] for key in ("id", "cd", "ad", "sig")}}
        accepted = await self.request(receipt)
        self.assertTrue(accepted["ok"], accepted)
        return target, receipt

    async def test_unverified_release_sites_cannot_open_a_creator_request(self):
        self.host.elevate.creator = self.closed
        listed = await self.request({"t": "browser_grants"})
        self.assertFalse(listed["available"])
        self.assertEqual(listed["grants"], [])
        for request, account in ((BILI, "1234"), (STUDIO, CHANNEL)):
            offer = await self.request({"t": "browser_grant_prepare", "site": request["site"],
                                        "account": account, "minutes": 15})
            self.assertEqual(offer["why"], "site_unavailable")
            self.assertEqual((await self.closed.from_agent(request, 111))["reason"], "site_unavailable")
        self.assertEqual(self.read_calls, [])

    async def test_real_signed_uv_read_repeat_revoke_distinct_sites_and_replay(self):
        target, receipt = await self.grant()
        for _ in range(2):
            result = await self.desk.from_agent(BILI, 111)
            self.assertEqual(result, {"ok": True, "site": "bilibili", "action": "archive_status", "account": "1234",
                                      "archives": [{"id": "BV1234567890", "state": -1}]})
        self.assertEqual((await self.desk.from_agent(STUDIO, 111))["reason"], "site_grant_required")
        await self.grant("youtube-studio", CHANNEL)
        self.assertEqual((await self.desk.from_agent(STUDIO, 111))["account"], CHANNEL)
        self.assertEqual(len((await self.request({"t": "browser_grants"}))["grants"]), 2)
        rv = {"id": target["id"]}
        self.assertTrue((await self.request(sign(self.phone, "browser_grant_revoke", rv,
                                                 {"t": "browser_grant_revoke", **rv})))["ok"])
        self.assertEqual((await self.request(receipt))["why"], "changed")
        self.assertEqual((await self.desk.from_agent(BILI, 111))["reason"], "site_grant_required")
        self.assertTrue((await self.desk.from_agent(STUDIO, 111))["ok"])

    async def test_bad_source_targets_tampering_wrong_uv_and_runtime_rebind(self):
        prepared = await self.request({"t": "browser_grant_prepare", "site": "bilibili", "account": "1234", "minutes": 15})
        target = prepared["target"]
        signed = sign(self.phone, "browser_grant", target, {"t": "browser_grant", "target": target})
        changed = {**target, "origin": "https://member.bilibili.com.evil.invalid"}
        self.assertEqual((await self.request({**signed, "target": changed}))["why"], "changed")
        restricted = await self.request(signed)
        self.assertEqual(restricted["why"], "passkey_required")
        wrong = self.auth.assert_(b"0" * 32, b"", nonce=b"")
        self.assertEqual((await self.request({**signed, "fa": wrong}))["why"], "passkey_failed")
        self.assertFalse(self.desk.grants)
        await self.grant()
        before = list(self.read_calls)
        for req, pid, reason in (
            (BILI, 999, "owner_phone_required"),
            ({**BILI, "url": "https://evil.invalid"}, 111, "unsupported_creator_action"),
            ({**BILI, "action": "publish"}, 111, "unsupported_creator_action"),
            ({**BILI, "action": "upload"}, 111, "unsupported_creator_action"),
            ({**BILI, "action": "Runtime.evaluate"}, 111, "unsupported_creator_action"),
        ):
            self.assertEqual((await self.desk.from_agent(req, pid))["reason"], reason)
        other = Phone(self.st, name="other")
        actor = _ready(self.host, other, cid=24)
        actor.sign_pub = self.st.sign_key(other.did)
        self.actor.cur_send.phone_binding = RequestPhone.from_session(self.host, actor)
        self.assertEqual((await self.desk.from_agent(BILI, 111))["reason"], "owner_phone_required")
        self.actor.cur_send.phone_binding = RequestPhone.from_session(self.host, self.s)
        self.rt = {**self.rt, "runtime_id": ("fixture-cli", ("24:6",), "fixture-profile")}
        self.assertEqual((await self.desk.from_agent(BILI, 111))["reason"], "browser_or_grant_changed")
        self.assertEqual((await self.request({"t": "browser_grants"}))["grants"], [])
        self.assertEqual(self.read_calls, before)

    async def test_estop_runtime_change_unpair_and_expiry_are_terminal(self):
        await self.grant()
        self.host.elevate.cancel_all("stopped")
        self.assertEqual((await self.desk.from_agent(BILI, 111))["reason"], "site_grant_required")
        await self.grant()
        browser_ego.bind_profile(self.st, "fixture-profile")
        self.assertEqual((await self.desk.from_agent(BILI, 111))["reason"], "site_grant_required")
        await self.grant()
        self.desk.grants["bilibili"] = replace(self.desk.grants["bilibili"], until=0)
        self.desk._prune()
        self.assertFalse(self.desk.grants)
        await self.grant()
        self.st.remove_device(self.phone.did)
        self.desk._prune()
        self.assertFalse(self.desk.grants)

    async def test_revocation_while_reading_suppresses_result(self):
        target, _ = await self.grant()
        entered, finish = asyncio.Event(), asyncio.Event()

        async def read(*args):
            entered.set()
            await finish.wait()
            return {"account": target["account"], "archives": []}

        with patch.object(self.desk, "_read", side_effect=read):
            task = asyncio.create_task(self.desk.from_agent(BILI, 111))
            await asyncio.wait_for(entered.wait(), 1)
            rv = {"id": target["id"]}
            await self.request(sign(self.phone, "browser_grant_revoke", rv, {"t": "browser_grant_revoke", **rv}))
            finish.set()
            self.assertFalse((await task)["ok"])

    def test_host_rejects_credential_and_provider_strings_in_projected_result(self):
        good = {"account": "1234", "archives": [{"id": "BV1234567890", "state": 0}]}
        self.assertEqual(browser_creator.project("bilibili", "1234", "archive_status", good), good)
        for bad in ({**good, "cookie": "SYNTHETIC"}, {**good, "archives": [{"id": "javascript:synthetic", "state": 0}]}):
            self.assertIsNone(browser_creator.project("bilibili", "1234", "archive_status", bad))
