"""P179: a remote's own label (self_name) vs. the owner's name for it (owner_name).
- allowlist: pairing stores self_name; any rename (account page / the remote itself) stores owner_name + when + via; the same
  device or browser pairing again keeps the owner's name; pre-P179 records migrate by the audit log (approvals.log).
- labels: pages, activity and the Agent see 「王小明（老板）· iOS Safari」 once owner-named; untouched remotes as before.
- `dev_rename` (§10.16a): a paired remote renames ONLY itself; same writer / audit / owner_name as the account page;
  every phone gets the new names through the `preferences` broadcast (named_by, self_name, label).
- the report's optional `device_names` (only when some remote is owner-named), and `agentj devices` notes."""
import _hermetic  # noqa: F401
import asyncio
import hashlib
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agentj import approvals, cloud, cli, preferences, serve, wire
from agentj.state import State, device_label, device_names, platform_of


class Names(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p179-")
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init()

    def test_pairing_keeps_self_name_and_rename_is_the_owners(self):
        did = self.st.add_device(os.urandom(32), "网页 · iOS Safari")
        rec = self.st.devices()[did]
        self.assertEqual((rec["name"], rec["self_name"]), ("网页 · iOS Safari", "网页 · iOS Safari"))
        self.assertEqual(device_names(rec)["named_by"], "device")
        self.assertEqual(device_label(rec), "网页 · iOS Safari", "untouched remotes look exactly as before")
        self.assertTrue(self.st.rename_device(did, "  王小明（老板） ", via="phone"))
        rec = self.st.devices()[did]
        self.assertEqual((rec["name"], rec["owner_name"], rec["self_name"], rec["owner_named_via"]),
                         ("王小明（老板）", "王小明（老板）", "网页 · iOS Safari", "phone"))
        self.assertGreater(rec["owner_named_at"], 0)
        n = device_names(rec)
        self.assertEqual((n["named_by"], n["self_name"], n["owner_name"]), ("owner", "网页 · iOS Safari", "王小明（老板）"))
        self.assertEqual(device_label(rec), "王小明（老板） · iOS Safari")
        self.st.rename_device(did, "老板")                                   # a second rename keeps the device's own label
        self.assertEqual(self.st.devices()[did]["self_name"], "网页 · iOS Safari")
        self.assertEqual(self.st.devices()[did]["owner_named_via"], "account")

    def test_label_rules(self):
        self.assertEqual(platform_of("网页 · Android Chrome"), "Android Chrome")
        self.assertEqual(platform_of("Web · macOS Safari"), "macOS Safari")
        self.assertEqual(platform_of("我的手机"), "")
        self.assertEqual(device_label({"name": "x", "self_name": "我的手机", "owner_name": "孔丹"}), "孔丹", "no platform part")
        long = "长" * 64
        self.assertEqual(device_label({"self_name": "网页 · iOS Safari", "owner_name": long}), long, "≤ 64: the owner's name wins")
        self.assertEqual(device_label({"name": "legacy"}), "legacy")
        self.assertEqual(device_names(None)["named_by"], "device")

    def test_same_device_or_browser_pairing_again_keeps_the_owner_name(self):
        pub = os.urandom(32)
        did = self.st.pair_device(pub, "网页 · iOS Safari", iid="install-1")["device"]
        self.st.rename_device(did, "於丹莉（老板娘）")
        self.st.pair_device(pub, "网页 · iOS Safari 2", iid="install-1")   # same key
        rec = self.st.devices()[did]
        self.assertEqual((rec["name"], rec["owner_name"], rec["self_name"]), ("於丹莉（老板娘）", "於丹莉（老板娘）", "网页 · iOS Safari 2"))
        got = self.st.pair_device(os.urandom(32), "网页 · iOS 主屏幕", iid="install-1")   # same browser, new key
        self.assertEqual(got["replaced"]["id"], did)
        rec = self.st.devices()[got["device"]]
        self.assertEqual((rec["name"], rec["owner_name"], rec["self_name"]), ("於丹莉（老板娘）", "於丹莉（老板娘）", "网页 · iOS 主屏幕"))
        other = self.st.pair_device(os.urandom(32), "网页 · Windows Chrome", iid="install-2")["device"]
        self.assertEqual(device_names(self.st.devices()[other])["named_by"], "device", "a new browser is not owner-named")

    def test_migration_by_audit(self):
        a, b = wire.device_id(os.urandom(32)), wire.device_id(os.urandom(32))
        legacy = {a: {"pub": "x", "name": "王小明（老板）", "paired_at": 1}, b: {"pub": "y", "name": "网页 · iOS Safari", "paired_at": 2}}
        self.st.write_private(self.st.devices_path, json.dumps(legacy).encode())
        digest = hashlib.sha256(json.dumps({"device": a, "name": "王小明（老板）"}, sort_keys=True).encode()).hexdigest()
        approvals.record(self.st, rid="r1", agent="account", tool="device_name", input_sha256=digest, shown_sha256=digest,
                         decision="allow", reason="account_passkey", device="account")
        other = hashlib.sha256(json.dumps({"device": b, "name": "网页 · iOS Safari"}, sort_keys=True).encode()).hexdigest()
        approvals.record(self.st, rid="r2", agent="account", tool="device_name", input_sha256=other, shown_sha256=other,
                         decision="deny", reason="gone", device="account")     # a refused rename proves nothing
        self.assertEqual(self.st.migrate_device_names(), 2)
        d = self.st.devices()
        self.assertEqual((d[a]["owner_name"], d[a]["self_name"], device_names(d[a])["named_by"]), ("王小明（老板）", None, "owner"))
        self.assertEqual((d[b]["self_name"], device_names(d[b])["named_by"]), ("网页 · iOS Safari", "device"))
        self.assertEqual(self.st.migrate_device_names(), 0, "once")
        self.assertEqual(d[a]["name"], "王小明（老板）", "the effective name never changes")
        self.assertEqual(self.st.devices_path.stat().st_mode & 0o777, 0o600)

    def test_report_device_names(self):
        a = self.st.add_device(os.urandom(32), "网页 · iOS Safari")
        b = self.st.add_device(os.urandom(32), "网页 · Android Chrome")
        body = cloud.build_report(self.st, set(), None, seq=1, ts=1)
        self.assertNotIn("device_names", body, "nothing owner-named: the report is exactly as before")
        self.st.rename_device(a, "王小明（老板）")
        body = cloud.build_report(self.st, set(), None, seq=2, ts=2)
        self.assertEqual(body["device_names"], [{"id": a, "self": "网页 · iOS Safari"}])
        self.assertEqual({d["id"]: d["name"] for d in body["devices"]}, {a: "王小明（老板）", b: "网页 · Android Chrome"})
        self.assertEqual(list(body)[-1], "device_names", "appended after every older key")

    def test_older_server_without_device_names(self):
        import base64
        cloud.write_cloud(self.st, {"api": "https://api.p179.invalid", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": 1, "last_seq": 0})
        did = self.st.add_device(os.urandom(32), "网页 · iOS Safari")
        self.st.rename_device(did, "王小明（老板）")
        seen = []

        def post(url, env):
            inner = json.loads(base64.urlsafe_b64decode(env["body"] + "=" * (-len(env["body"]) % 4)))
            seen.append(inner)
            if "device_names" in inner:            # a server older than P179: unknown key → 400
                return 400, {"error": "bad_request"}
            return 200, {"ok": True}
        cloud._NO_NAMES.clear()
        self.addCleanup(cloud._NO_NAMES.clear)
        self.assertEqual(cloud.send_report(self.st, set(), None, post=post).kind, "ok")
        self.assertEqual(["device_names" in x for x in seen], [True, False], "refused once, then the old shape at once")
        self.assertEqual(seen[1]["devices"][0]["name"], "王小明（老板）", "the owner's name still reaches the account page")
        self.assertTrue(all(k in seen[1] for k in ("language", "notices_v")), "no other back-compat step was taken")
        seen.clear()
        cloud.send_report(self.st, set(), None, post=post)
        self.assertEqual(["device_names" in x for x in seen], [False], "not asked again in this process for a day")

    def test_cli_notes(self):
        self.assertIn("名字是设备自己填的", cli._name_note({"name": "网页 · iOS Safari"}))
        note = cli._name_note({"name": "孔丹", "owner_name": "孔丹", "self_name": "网页 · Windows Chrome"})
        self.assertIn("主人改的名字", note)
        self.assertIn("name set by the owner", note)
        self.assertIn("网页 · Windows Chrome", note)


class Env(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p179-a-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                                         "AGENTJ_STATE_DIR": str(root / "state")})
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        preferences.ensure()
        self.host = serve.Host(self.st, read_stdin=False)
        self.host.reporter.trigger = mock.Mock()
        self.sent = []

        async def send_app(s, obj):
            self.sent.append((s.cid, obj))
            return True
        self.host.send_app = send_app
        self.pub, self.pub2 = os.urandom(32), os.urandom(32)
        self.did = self.st.add_device(self.pub, "网页 · iOS Safari")
        self.did2 = self.st.add_device(self.pub2, "网页 · Windows Chrome")
        self.s = serve.Session(cid=7, state="ready", device=self.did, name="网页 · iOS Safari", pub=self.pub, p33=True)
        self.s2 = serve.Session(cid=8, state="ready", device=self.did2, name="网页 · Windows Chrome", pub=self.pub2, p33=True)
        self.host.sessions = {7: self.s, 8: self.s2}

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0)

    def got(self, t, cid=None):
        return [o for c, o in self.sent if o.get("t") == t and (cid is None or c == cid)]

    async def test_dev_rename_own_only_audited_and_broadcast(self):
        await self.host._app(self.s, {"t": "dev_rename", "r": "r1", "name": "王小明（老板）", "device": self.did2})
        await self.settle()
        self.assertEqual(self.got("dev_rename_res", 7), [{"t": "dev_rename_res", "r": "r1", "ok": True, "name": "王小明（老板）"}])
        d = self.st.devices()
        self.assertEqual((d[self.did]["owner_name"], d[self.did]["owner_named_via"]), ("王小明（老板）", "phone"))
        self.assertNotIn("owner_name", d[self.did2], "a `device` field is ignored: only the asking remote is renamed")
        self.assertEqual(self.s.name, "王小明（老板） · iOS Safari", "this remote now speaks under the new label")
        self.host.reporter.trigger.assert_called_with("online")
        logged = [json.loads(x) for x in self.st.approvals_path.read_text().splitlines()]
        self.assertEqual([(x["tool"], x["decision"], x["reason"], x["device"]) for x in logged],
                         [("device_name", "allow", "own_device", self.did)])
        for cid in (7, 8):                                      # every phone gets the names (settings + source labels)
            devs = {x["id"]: x for x in self.got("preferences", cid)[-1]["host"]["devices"]}
            self.assertEqual((devs[self.did]["named_by"], devs[self.did]["self_name"], devs[self.did]["label"], devs[self.did]["name"]),
                             ("owner", "网页 · iOS Safari", "王小明（老板） · iOS Safari", "王小明（老板）"))
            self.assertEqual(devs[self.did2]["named_by"], "device")

    async def test_refusals(self):
        before = self.st.devices_path.read_bytes()
        for i, name in enumerate(("", "   ", None, 5, "x" * 300, "\u0007")):
            self.sent.clear()
            await self.host._app(self.s, {"t": "dev_rename", "r": f"b{i}", "name": name})
            self.assertEqual([o.get("why") for o in self.got("dev_rename_res")], ["bad_name"], repr(name))
        await self.host._app(self.s, {"t": "dev_rename", "r": "bad rid!", "name": "ok"})
        self.assertEqual(self.st.devices_path.read_bytes(), before, "nothing written")
        self.st.remove_device(self.did)
        self.sent.clear()
        await self.host._app(self.s, {"t": "dev_rename", "r": "g1", "name": "ok"})
        self.assertNotIn(True, [o.get("ok") for o in self.got("dev_rename_res")], "a revoked remote cannot rename")

    async def test_labels_for_pages_and_the_agent(self):
        self.assertEqual(self.host.speaker_note(self.s), "", "a device-named remote: no note (as before)")
        self.st.rename_device(self.did, "殷丹阳（助理）")
        self.host.device_renamed(self.did)
        self.assertEqual(self.s.name, "殷丹阳（助理） · iOS Safari")
        self.assertEqual(self.host._dname(self.did), "殷丹阳（助理） · iOS Safari", "activity / estop by")
        note = self.host.speaker_note(self.s)
        self.assertIn("殷丹阳（助理） · iOS Safari", note)
        self.assertIn("不是身份证明", note)
        with mock.patch.object(self.host, "onboarding_lang", return_value="en"):
            self.assertIn("not proof of identity", self.host.speaker_note(self.s))

    async def test_account_page_rename_follows_the_same_path(self):
        from agentj import remote_pair
        q = {"id": "acct1", "op": "device_name", "target": {"device": self.did2, "name": "孔丹（法务·投后）"}, "expires_at": 1 << 60}
        with mock.patch.object(remote_pair, "open_request", return_value=q), mock.patch.object(remote_pair, "consume", return_value=True), \
                mock.patch.object(self.host.remote_pair, "allowed", return_value=True), \
                mock.patch.object(self.host.remote_pair, "reply") as reply:
            self.host.relay_up = True
            self.host.remote_pair.started_at = 0
            q["created_at"] = 1
            await self.host.remote_pair.execute({})
        reply.assert_called_once_with(q, {"status": "approved"})
        await self.settle()
        rec = self.st.devices()[self.did2]
        self.assertEqual((rec["owner_name"], rec["owner_named_via"], rec["self_name"]), ("孔丹（法务·投后）", "account", "网页 · Windows Chrome"))
        self.assertEqual(self.s2.name, "孔丹（法务·投后） · Windows Chrome")
        self.assertTrue(self.got("preferences", 7), "the other remotes hear about it at once")


if __name__ == "__main__":
    unittest.main()
