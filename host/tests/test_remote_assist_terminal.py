"""#468: real customer UV + dedicated official-domain PTY, hermetic keys/state."""
import _hermetic  # noqa: F401
import _fixture_disk
import asyncio
import json
import os
import pathlib
import secrets
import tempfile
import time
import unittest
from unittest import mock

from agentj import assist, assist_terminal, controls, passkey, wire
import test_p0269_assist as old
from test_p0269_assist import _KeyEnv, helper_keys, R, write_grant
from test_l1 import Phone, _host, _ready, _state


class RemoteAssist(_KeyEnv, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await old.Grant.asyncSetUp(self)
        login = mock.patch("agentj.terminal.Desk._login", mock.AsyncMock(return_value={"result": "saved"}))
        login.start()
        self.addCleanup(login.stop)
        self.helper_sent = []
        self.h.assist._send = lambda fid, obj: self.helper_sent.append((fid, obj)) or True
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.h.agent_cfg = {"kind": "claude", "working_root": str(self.work), "fence": False}
        # Same hermetic PTY hook as the existing real terminal tests.
        self.addAsyncCleanup(self.cleanup_terminal)

    async def cleanup_terminal(self):
        await self.h.assist.end("test_cleanup")
        for job in list(self.h.assist.jobs):
            if not job.done():
                job.cancel()
        await asyncio.gather(*self.h.assist.jobs, return_exceptions=True)

    async def grant(self, minutes=30, step2_minutes=None):
        action = "assist_term_grant"
        await self.h._app(self.s, {"t": action, "r": R, "minutes": minutes})
        c = [o for _, o in self.sent if o.get("action") == action][-1]
        if c.get("why") != "passkey_required":
            return c
        mins = minutes if step2_minutes is None else step2_minutes
        target = {"minutes": mins, "key_id": c["key_id"], "seat": c["seat"], "scope": "terminal"}
        ts = int(time.time() * 1000)
        msg = controls.signed_message(self.h.channel, self.ph.did, action, c["n"], ts, controls.object_digest(action, target))
        ch = passkey.elevate_challenge(self.h.channel, self.ph.did, c["n"], action, c["n"],
                                      assist_terminal.grant_digest(c["key_id"], c["seat"], minutes))
        await self.h._app(self.s, {"t": action, "r": R, "minutes": mins, "n": c["n"], "ts": ts,
                                  "sig": wire.b64u(self.ph.sk.sign(msg)), "fa": self.ph.auth.assert_(ch, b"0", nonce=b"0")})
        return [o for _, o in self.sent if o.get("action") == action][-1]

    def signed(self, t, **kw):
        g = self.h.assist.g
        obj = {"t": t, "gid": g["id"], "seat": g["seat"], "seq": g["seq"] + 1,
               "ts": int(time.time() * 1000), **kw}
        return {**obj, "sig": wire.b64u(self.hk.ed.sign(assist_terminal.message(obj)))}

    async def peer(self, t, **kw):
        self.h.assist.on_app(self.h.assist.peer_id(), self.signed(t, **kw))
        await asyncio.sleep(0.1)

    async def until(self, fn, timeout=6):
        end = time.monotonic() + timeout
        while not fn():
            if time.monotonic() > end:
                self.fail("PTY condition timed out")
            await asyncio.sleep(.03)

    async def open(self):
        self.assertTrue((await self.grant())["ok"])
        await self.peer("assist_term_open", cols=80, rows=24)
        await self.until(lambda: self.h.assist.terminal.desk and self.h.assist.terminal.desk.term and
                         self.h.assist.terminal.desk.term.at_prompt)
        return self.h.assist.terminal.desk

    def output(self):
        return "".join(o.get("event", {}).get("d", "") for _, o in self.helper_sent)

    async def test_scope_and_minutes_are_bound_to_fresh_uv(self):
        self.assertIn(assist_terminal.CAP, self.h._caps(self.s)["caps"])
        self.assertFalse((await self.grant(30, 60))["ok"])
        self.assertIsNone(self.h.assist.live())
        self.assertTrue((await self.grant())["ok"])
        self.assertEqual(self.h.assist.g["scope"], "terminal")
        self.assertIs(self.h.assist.terminal.owner, self.s)

    async def test_task_grant_cannot_open_terminal(self):
        g = write_grant(self.st, self.ph.did, self.kid, self.h.assist.seat())
        self.h.assist.g = g
        await self.peer("assist_term_open", cols=80, rows=24)
        self.assertIsNone(self.h.assist.terminal.desk)

    async def test_real_pty_output_and_unpinned_replay_wrong_scope_refused(self):
        d = await self.open()
        x = d.term
        obj = self.signed("assist_term_in", d="printf 'ASSIST-ROUNDTRIP\\n'\r")
        self.h.assist.on_app("AJ-unpinned", obj)
        await asyncio.sleep(.1)
        self.assertNotIn("ASSIST-ROUNDTRIP", self.output())
        self.h.assist.on_app(self.h.assist.peer_id(), obj)
        await self.until(lambda: "ASSIST-ROUNDTRIP" in self.output())
        seen = self.h.assist.g["seq"]
        self.h.assist.on_app(self.h.assist.peer_id(), obj)
        self.assertEqual(self.h.assist.g["seq"], seen)
        self.assertIs(x.s, self.s)
        self.assertEqual(x.device, self.ph.did)
        self.assertEqual(set(self.st.devices()), {self.ph.did})

    async def test_expiry_refuses_input_before_sweep_and_kills_pty(self):
        d = await self.open()
        x = d.term
        self.h.assist.g["until"] = int(time.time()) - 1
        self.assertFalse(d._pty_write(x, "touch should-not-run\r"))
        await d.tick()
        self.assertIsNone(d.term)
        self.assertFalse((self.work / "should-not-run").exists())
        self.assertIsNotNone(x.proc.returncode)

    async def test_revoke_mid_session_blocks_delayed_enter_and_no_official_approvals(self):
        d = await self.open()
        x = d.term
        await self.peer("assist_term_in", d="rm -rf ./unimportant\r")
        await self.until(lambda: x.hold == "confirm")
        self.assertTrue(any(o.get("event", {}).get("t") == "term_confirm" for _, o in self.sent))
        self.assertFalse(any(o.get("event", {}).get("t") == "term_confirm" for _, o in self.helper_sent))
        await self.h.assist.end("revoked")
        self.assertFalse(d._pty_write(x, "\r"))
        self.assertIsNotNone(x.proc.returncode)
        self.assertIsNotNone(self.h.assist.terminal.report)

    async def test_audit_failure_prevents_any_new_input(self):
        d = await self.open()
        x = d.term
        with mock.patch("agentj.assist.audit", side_effect=OSError("disk full")):
            await self.peer("assist_term_in", d="touch should-not-run\r")
            self.assertFalse(d._pty_write(x, "\r"))
            self.assertFalse(self.h.assist.live())
        self.assertFalse((self.work / "should-not-run").exists())

    async def test_customer_disconnect_blocks_final_write_without_tick(self):
        d = await self.open()
        x = d.term
        self.h.sessions.pop(self.s.cid)
        self.assertFalse(d._pty_write(x, "touch should-not-run\r"))
        await d.tick()
        self.assertIsNotNone(x.proc.returncode)

    async def test_placeholder_stays_off_and_grant_refused(self):
        self.unpin()
        self.assertNotIn(assist_terminal.CAP, self.h._caps(self.s)["caps"])
        self.assertEqual((await self.grant())["why"], "unavailable")

    async def test_operator_public_invite_pins_and_fresh_hello(self):
        from agentj import assist_operator
        customer = helper_keys()
        now = int(time.time())
        obj = {"v": 1, "scope": "terminal", "gid": secrets.token_hex(12), "seat": self.h.assist.seat(),
               "key_id": self.kid, "not_before": now, "until": now + 1800, "aid": customer.id,
               "x25519": wire.b64u(customer.x_pub), "ed25519": wire.b64u(customer.ed_pub)}
        invitation = {**obj, "sig": wire.b64u(customer.ed.sign(assist_operator.invite_message(obj)))}
        from types import SimpleNamespace
        self.h.peers = SimpleNamespace(keys=self.hk)
        op = self.h.assist.operator
        with mock.patch.object(self.h.assist, "_apply_peers", mock.AsyncMock()):
            self.assertTrue((await op.ctl({"action": "import", "invite": invitation}))["ok"])
        self.assertFalse((await op.ctl({"action": "open", "gid": obj["gid"]}))["ok"])
        op.on_app(customer.id, {"t": "assist_ready", "gid": obj["gid"], "seat": obj["seat"], "until": obj["until"],
                                "scope": "terminal", "seq": 0})
        self.assertTrue((await op.ctl({"action": "open", "gid": obj["gid"]}))["ok"])
        self.assertTrue((await op.ctl({"action": "in", "gid": obj["gid"], "d": "\x1b[6n"}))["ok"])
        self.assertTrue((await op.ctl({"action": "in", "gid": obj["gid"], "d": "\x1b[1;2R"}))["ok"])
        frame = self.helper_sent[-1][1]
        self.assertTrue(frame.get("reply"))
        self.assertTrue(assist._ed_ok(self.hk.ed_pub, frame["sig"], assist_terminal.message(frame)))
        altered = {**invitation, "until": now + 3600}
        self.assertIsNone(assist_operator.validate(altered))
        self.h.peers.keys = helper_keys()
        self.assertFalse(op.live_rows())
        self.assertFalse((await op.ctl({"action": "open", "gid": obj["gid"]}))["ok"])

    async def test_durable_audit_failure_in_grant_refuses_scope(self):
        with mock.patch("agentj.assist.audit", side_effect=OSError("disk full")):
            result = await self.grant()
        self.assertFalse(result["ok"])
        self.assertFalse(self.h.assist.live())

    async def test_monotonic_deadline_survives_wall_rollback(self):
        d = await self.open()
        x = d.term
        self.h.assist.terminal.deadline = time.monotonic() - 1
        self.h.assist.clock = lambda: self.h.assist.g["not_before"] + 1
        self.assertFalse(d._pty_write(x, "touch should-not-run\r"))
        await d.tick()
        self.assertIsNotNone(x.proc.returncode)

    async def test_official_frames_never_receive_password_or_passkey_material(self):
        d = await self.open()
        for t in ("term_confirm", "term_sudo", "elev"):
            await d.h.send_app(self.s, {"t": t, "id": d.term.id, "fa": "test-passkey-id", "epk": "test-ephemeral-public"})
        self.assertFalse(any(o.get("event", {}).get("t") in ("term_confirm", "term_sudo", "elev") for _, o in self.helper_sent))
        for _, obj in self.helper_sent:
            self.assertFalse(any(k in obj.get("event", {}) for k in ("fa", "pct", "pepk", "epk")))

    async def test_end_metadata_precedes_drop_and_operator_client_exits(self):
        from types import SimpleNamespace
        from agentj import assist_operator
        d = await self.open()
        gid = self.h.assist.g["id"]
        order = []
        async def final(fid, target, reason):
            order.append(("end", target, reason))
            return True
        net = SimpleNamespace(send_assist_end=final, drop_peer=lambda fid: order.append(("drop", fid)))
        self.h.peers = SimpleNamespace(net=net)
        await self.h.assist.end("revoked")
        self.assertEqual(order[0], ("end", gid, "revoked"))
        self.assertEqual(order[1][0], "drop")
        self.assertIsNotNone(d.term if d.term else self.h.assist.terminal.report)
        # Official-side metadata is authenticated by the existing KK and must
        # reach the local tty loop as a close event, without a stale output frame.
        customer = helper_keys()
        now = int(time.time())
        obj = {"v": 1, "scope": "terminal", "gid": secrets.token_hex(12), "seat": self.h.assist.seat(),
               "key_id": self.kid, "not_before": now, "until": now + 1800, "aid": customer.id,
               "x25519": wire.b64u(customer.x_pub), "ed25519": wire.b64u(customer.ed_pub)}
        obj["sig"] = wire.b64u(customer.ed.sign(assist_operator.invite_message(obj)))
        self.h.peers = SimpleNamespace(keys=self.hk)
        op = self.h.assist.operator
        with mock.patch.object(self.h.assist, "_apply_peers", mock.AsyncMock()):
            self.assertTrue((await op.ctl({"action": "import", "invite": obj}))["ok"])
        op.on_app(customer.id, {"t": "assist_term_end", "gid": obj["gid"], "reason": "revoked"})
        result = await op.ctl({"action": "read", "gid": obj["gid"]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["why"], "ended")

    async def test_queued_io_guard_and_outbox_final_guard(self):
        from types import SimpleNamespace
        from agentj.peer import PeerNet
        from agentj.term_pty import PtyIO
        live = True
        sink = []
        def write(fd, data):
            if live:
                raise BlockingIOError()
            sink.append(bytes(data))
            return len(data)
        io = PtyIO(-1, write_guard=lambda: live)
        with mock.patch("agentj.term_pty.os.write", side_effect=write), \
             mock.patch.object(io.loop, "add_writer"), mock.patch.object(io.loop, "remove_writer"):
            self.assertTrue(io.write(b"NOT-EXECUTED"))
            self.assertTrue(io.out)
            live = False
            io._on_writable()
        self.assertFalse(sink)
        self.assertFalse(io.out)
        self.assertTrue(io.closed)
        net = SimpleNamespace(cb=SimpleNamespace(outbound_allowed=lambda fid, obj: False), db=mock.Mock())
        row = {"obj": json.dumps({"t": "assist_term_event", "gid": "0" * 24, "event": {"t": "term_out", "d": "old"}}),
               "peer": self.hk.id, "id": 1}
        self.assertTrue(await PeerNet._send_row(net, None, row, time.time()))
        net.db.execute.assert_called_once_with("DELETE FROM outbox WHERE id=?", (1,))

    async def test_old_sudo_callback_cannot_use_new_grant_generation(self):
        from agentj.term_askpass import SudoDesk
        d = await self.open()
        x = d.term
        a = self.h.assist.terminal
        value = bytearray(b"FAKE-TEST-ONLY")
        async def loaded(*args):
            a.desk = object()  # a later desk owns the same still-live grant object
            await asyncio.sleep(0)
            return value
        try:
            with mock.patch.object(SudoDesk, "_sudo_take", side_effect=loaded):
                self.assertIsNone(await d._sudo_take(x, "test-rid", bytearray()))
            self.assertTrue(all(v == 0 for v in value))
            self.assertFalse(d.login_guard())
        finally:
            a.desk = d

    async def test_old_desk_cannot_pollute_new_grant_audit(self):
        d = await self.open()
        x, a = d.term, self.h.assist.terminal
        original_gid = a.gid
        before = list(a.operations), a.confirmations, a.refusals
        try:
            for replace_desk in (True, False):
                a.desk = object() if replace_desk else d
                a.gid = original_gid if replace_desk else secrets.token_hex(12)
                with mock.patch.object(a, "live", return_value=True), mock.patch("agentj.assist.audit") as audit:
                    await d._frame(x, "STALE-OUTPUT", False)
                    await d._cast(x, "o", "STALE-CAST")
                    d._pty_release(x, "echo STALE-COMMAND")
                    d._log("term_line", self.ph.did, "confirmed")
                    self.assertEqual(audit.call_count, 0)
                    self.assertEqual((list(a.operations), a.confirmations, a.refusals), before)
        finally:
            a.desk, a.gid = d, original_gid


class ReportRetention(unittest.TestCase):
    def test_only_valid_expired_feature_reports_are_removed(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            st = SimpleNamespace(root=root)
            d = root / "assist"
            d.mkdir()
            now = int(time.time())
            old = now - 31 * 86400
            def report(gid, ended):
                return {"id": gid, "ended_at": ended, "result": "session_ended"}
            a, b, c, broken, future, linked = (ch * 24 for ch in "abcdef")
            old_file = d / ("report-" + a + ".json")
            old_file.write_text(json.dumps(report(a, old)))
            (d / "report.json").write_text(json.dumps(report(a, old)))
            new_file = d / ("report-" + b + ".json")
            new_file.write_text(json.dumps(report(b, now)))
            mismatch = d / ("report-" + c + ".json")
            mismatch.write_text(json.dumps(report(a, old)))
            damaged = d / ("report-" + broken + ".json")
            damaged.write_text("{damaged")
            future_file = d / ("report-" + future + ".json")
            future_file.write_text(json.dumps(report(future, now + 1)))
            unknown = d / "report-manual.json"
            unknown.write_text(json.dumps(report(a, old)))
            external = root / "unrelated.json"
            external.write_text(json.dumps(report(linked, old)))
            symlink = d / ("report-" + linked + ".json")
            symlink.symlink_to(external)
            # Absence of log.jsonl must not skip report retention.
            assist.prune_log(st, now)
            self.assertFalse(old_file.exists())
            self.assertFalse((d / "report.json").exists())
            for p in (new_file, mismatch, damaged, future_file, unknown, external, symlink):
                self.assertTrue(p.exists(), p.name)
            self.assertTrue(symlink.is_symlink())
            (d / "report.json").write_text(json.dumps(report(b, now)))
            assist.prune_reports(st, now)
            self.assertTrue((d / "report.json").exists())


class OperatorRetention(_KeyEnv, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await old.Grant.asyncSetUp(self)
        from types import SimpleNamespace
        self.h.peers = SimpleNamespace(keys=self.hk)
        self.h.assist._send = lambda *args: True
        self.apply = mock.patch.object(self.h.assist, "_apply_peers", mock.AsyncMock())
        self.apply.start()
        self.addCleanup(self.apply.stop)

    def invitation(self, customer, *, gid=None, end=None):
        from agentj import assist_operator
        now = int(time.time())
        obj = {"v": 1, "scope": "terminal", "gid": gid or secrets.token_hex(12), "seat": self.h.assist.seat(),
               "key_id": self.kid, "not_before": now if end is None else end - 1800,
               "until": now + 1800 if end is None else end, "aid": customer.id,
               "x25519": wire.b64u(customer.x_pub), "ed25519": wire.b64u(customer.ed_pub)}
        obj["sig"] = wire.b64u(customer.ed.sign(assist_operator.invite_message(obj)))
        return obj

    async def test_expired_registry_slots_are_pruned_before_import_but_live_cap_remains(self):
        op = self.h.assist.operator
        customer = helper_keys()
        now = int(time.time())
        for i in range(128):
            row = self.invitation(customer, gid=f"{i:024x}", end=now - 1)
            op.rows[row["gid"]] = row
            op.deadlines[row["gid"]] = time.monotonic() - 1
            op.ready[row["gid"]] = {"seq": 0}
            op.queues[row["gid"]] = []
        incoming = self.invitation(helper_keys())
        result = await op.ctl({"action": "import", "invite": incoming})
        self.assertTrue(result["ok"], result)
        self.assertEqual(set(op.rows), {incoming["gid"]})
        self.assertFalse(op.ready)
        self.assertFalse(op.queues)
        persisted = json.loads((assist.dir_of(self.st) / "operator-invites.json").read_text())
        self.assertEqual([row["gid"] for row in persisted], [incoming["gid"]])
        # All 128 live rows still enforce the cap; cleanup is not a quota waiver.
        for i in range(127):
            row = self.invitation(customer, gid=f"{i + 1000:024x}")
            op.rows[row["gid"]] = row
            op.deadlines[row["gid"]] = time.monotonic() + 1800
        refused = await op.ctl({"action": "import", "invite": self.invitation(helper_keys())})
        self.assertEqual(refused, {"ok": False, "why": "registry_full"})
        self.assertEqual(len(op.rows), 128)
        for gid in op.rows:
            op.deadlines[gid] = time.monotonic() - 1
        with mock.patch.object(op, "kick_discovery"):
            result = await op.ctl({"action": "list"})
        self.assertEqual(result["sessions"], [])
        self.assertEqual(json.loads((assist.dir_of(self.st) / "operator-invites.json").read_text()), [])


    async def test_same_customer_new_grant_is_usable_immediately_after_old_end(self):
        op = self.h.assist.operator
        customer = helper_keys()
        first, second = self.invitation(customer), self.invitation(customer)
        self.assertTrue((await op.ctl({"action": "import", "invite": first}))["ok"])
        op.on_app(customer.id, {"t": "assist_ready", "gid": first["gid"], "seat": first["seat"],
                                "until": first["until"], "scope": "terminal", "seq": 0})
        self.assertTrue((await op.ctl({"action": "open", "gid": first["gid"]}))["ok"])
        op.on_app(customer.id, {"t": "assist_term_end", "gid": first["gid"], "reason": "revoked"})
        self.assertNotIn(first["gid"], op.rows)
        self.assertEqual((await op.ctl({"action": "import", "invite": first}))["why"], "ended")
        self.assertTrue((await op.ctl({"action": "import", "invite": second}))["ok"])
        # Even an older cache containing another live metadata row for the same
        # public identity must route a frame by its exact grant, not first aid.
        stale = self.invitation(customer, gid="f" * 24)
        op.rows[stale["gid"]] = stale
        op.deadlines[stale["gid"]] = time.monotonic() + 1800
        op.on_app(customer.id, {"t": "assist_ready", "gid": second["gid"], "seat": second["seat"],
                                "until": second["until"], "scope": "terminal", "seq": 0})
        self.assertTrue((await op.ctl({"action": "open", "gid": second["gid"]}))["ok"])
        # Delayed old-grant output/end cannot poison the immediately live new row.
        op.on_app(customer.id, {"t": "assist_term_event", "gid": first["gid"], "event": {"t": "term_out", "d": "OLD"}})
        op.on_app(customer.id, {"t": "assist_term_end", "gid": first["gid"], "reason": "revoked"})
        self.assertTrue(op.fresh(second))
        self.assertTrue(op.outbound(customer.id, {"t": "assist_term_in", "gid": second["gid"]}))
        self.assertFalse(op.queues.get(second["gid"]))
        self.assertEqual((await op.ctl({"action": "in", "gid": first["gid"], "d": "no"}))["why"], "ended")
        await __import__("agentj.diskq", fromlist=["aflush"]).aflush()
        ended = json.loads((assist.dir_of(self.st) / "operator-ended.json").read_text())
        self.assertIn(first["gid"], ended)


    async def test_operator_ipc_read_batches_unicode_in_order_under_default_reader(self):
        import collections
        from agentj import names, serve
        op = self.h.assist.operator
        invitation = self.invitation(helper_keys())
        self.assertTrue((await op.ctl({"action": "import", "invite": invitation}))["ok"])
        gid = invitation["gid"]
        expected = [{"t": "assist_term_event", "gid": gid, "event": {"t": "term_out", "seq": i, "d": "😀" * 4000 + str(i)}} for i in range(200)]
        op.queues[gid] = collections.deque(expected)
        async def handle(reader, writer):
            req = json.loads(await reader.readline())
            result = await op.ctl(req)
            await serve.Host._ctl_send(self.h, writer, result)
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_unix_server(handle, path=str(self.st.sock_path))
        try:
            delivered = []
            for _ in range(250):
                result = await asyncio.to_thread(names.ctl_call, self.st, {"cmd": "assist_operator", "action": "read", "gid": gid}, 10)
                self.assertTrue(result["ok"])
                self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()) + 1, 56 * 1024)
                delivered.extend(result["events"])
                if not result["more"]:
                    break
            else:
                self.fail("IPC pagination did not terminate")
            self.assertEqual(delivered, expected)
            # A single legitimate output text can itself span several IPC batches.
            large = "水" * 30000
            op.queues[gid].append({"t": "assist_term_event", "gid": gid, "event": {"t": "term_out", "seq": 999, "d": large}})
            parts = []
            for _ in range(10):
                result = await asyncio.to_thread(names.ctl_call, self.st, {"cmd": "assist_operator", "action": "read", "gid": gid}, 10)
                self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()) + 1, 56 * 1024)
                parts.extend(e["event"]["d"] for e in result["events"])
                if not result["more"]:
                    break
            self.assertEqual("".join(parts), large)
        finally:
            server.close()
            await server.wait_closed()


class LogPaging(unittest.TestCase):
    def test_all_grant_operations_can_be_read_backwards_without_other_grants(self):
        from types import SimpleNamespace
        from agentj.assist_terminal import log_page
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            d = root / "assist"
            d.mkdir()
            st = SimpleNamespace(root=root)
            gid, other = "1" * 24, "2" * 24
            lines = []
            for i in range(601):
                lines.append(json.dumps({"gid": gid, "ev": "terminal_command", "i": i, "text": "x" * 300}))
                if i % 4 == 0:
                    lines.append(json.dumps({"gid": other, "ev": "OTHER-GRANT-MUST-NOT-LEAK", "i": i}))
            log = d / "log.jsonl"
            log.write_text("\n".join(lines) + "\n")
            cursor, collected, seen = None, [], set()
            first_cursor = None
            for _ in range(20):
                page = log_page(st, gid, cursor)
                self.assertNotIn("why", page)
                self.assertLessEqual(len(page["entries"]), 200)
                self.assertTrue(all(e["gid"] == gid for e in page["entries"]))
                collected = page["entries"] + collected
                if not page["more"]:
                    self.assertIsNone(page["next_cursor"])
                    break
                next_cursor = page["next_cursor"]
                self.assertTrue(next_cursor)
                self.assertNotEqual(next_cursor, cursor)
                self.assertNotIn(next_cursor, seen)
                seen.add(next_cursor)
                first_cursor = first_cursor or next_cursor
                cursor = next_cursor
            else:
                self.fail("log pagination did not terminate")
            self.assertEqual([e["i"] for e in collected], list(range(601)))
            self.assertEqual(log_page(st, other, first_cursor)["why"], "bad_cursor")
            for bad in (-1, True, "../../outside", "v1:broken", "x" * 1000):
                self.assertEqual(log_page(st, gid, bad)["why"], "bad_cursor")
            replacement = d / "prune.tmp"
            replacement.write_text(lines[-1] + "\n")
            replacement.replace(log)
            self.assertEqual(log_page(st, gid, first_cursor)["why"], "pruned")
            self.assertEqual(log_page(st, "not-a-grant")["why"], "shape")


class WireTransport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import types
        from agentj import serve
        from agentj.noise import CipherState
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addAsyncCleanup(_fixture_disk.drain_async_disk, self)
        self.st = _state(self.tmp.name)
        self.h = _host(self.st, [])
        self.ph = Phone(self.st)
        self.s = _ready(self.h, self.ph)
        self.s.p33 = True
        key = os.urandom(32)
        self.s.send = CipherState(key)
        receiver = CipherState(key)
        self.physical, self.received = [], []
        reassembler = wire.Defrag()
        async def op(code, cid, payload):
            self.assertEqual(code, wire.OP_DATA)
            obj = wire.unpad_json(receiver.decrypt(b"", payload[1:]), wire.MAX_JSON_P33)
            self.assertLessEqual(len(wire.dumps(obj)), wire.MAX_JSON_P33)
            self.physical.append(obj)
            whole = reassembler.feed(obj)
            if whole is not None:
                self.received.append(whole)
        self.h._op = op
        # Real product send_app, padding, encryption, fragmentation and reassembly.
        self.h.send_app = types.MethodType(serve.Host.send_app, self.h)

    async def test_report_view_and_large_log_use_real_p33_transport_without_loss(self):
        from agentj.assist_terminal import customer_message, report_view, log_page
        gid = "7" * 24
        report = {"id": gid, "scope": "terminal", "result": "session_ended", "ended_at": int(time.time()),
                  "operations": [{"at": i, "command": "🐉" * 512, "state": "shell_line_released", "exit_status": "unknown"} for i in range(64)],
                  "operations_total": 640, "official_statement": {k: "🧪" * 2000 for k in ("summary", "diagnosis", "verification")},
                  "unresolved": ["未验证"]}
        self.assertGreater(len(wire.dumps(report)), wire.MAX_JSON_P33)
        view = report_view(report)
        self.assertLessEqual(len(wire.dumps(view)), 12 * 1024)
        self.assertEqual(view["operations_total"], 640)
        self.assertLess(view["operations_shown"], 64)
        self.assertTrue(view["operations_truncated"])
        self.assertTrue(await self.h.send_app(self.s, customer_message({"t": "assist_report", "gid": gid, "report": report})))
        self.assertEqual(self.received[-1]["report"], view)
        d = assist.dir_of(self.st)
        d.mkdir(exist_ok=True)
        full = json.dumps(report, ensure_ascii=False)
        (d / ("report-" + gid + ".json")).write_text(full)
        rows = [{"gid": gid, "i": i, "ev": "output", "data": "長" * 1000} for i in range(30)]
        (d / "log.jsonl").write_text("\n".join(json.dumps(v, ensure_ascii=False) for v in rows) + "\n")
        expected = log_page(self.st, gid)
        self.h.assist.terminal.gid = gid
        before = len(self.physical)
        await self.h.assist.terminal.log(self.s, {"r": R, "gid": gid})
        delivered = self.received[-1]
        self.assertEqual(delivered["t"], "assist_log_res")
        self.assertEqual(delivered["entries"], expected["entries"])
        self.assertEqual(delivered["next_cursor"], expected["next_cursor"])
        self.assertTrue(any(m["t"] == "frag" for m in self.physical[before:]))
        self.assertEqual((d / ("report-" + gid + ".json")).read_text(), full)
        # Escaped controls use their REAL JSON cost in the display-view budget.
        hostile = {**report, "official_statement": {k: "\x00" * 2000 for k in ("summary", "diagnosis", "verification")}}
        self.assertLessEqual(len(wire.dumps(report_view(hostile))), 12 * 1024)

    async def test_old_awaiting_customer_send_cannot_forward_as_new_grant(self):
        from agentj.assist_terminal import _Host
        from types import SimpleNamespace
        sent = []
        current = True
        desk = SimpleNamespace(login_guard=lambda: current)
        async def switched(s, obj):
            nonlocal current
            current = False
            a.gid, a.desk = "9" * 24, object()
            return True
        a = SimpleNamespace(desk=desk, gid="8" * 24, live=lambda: True, h=self.h,
                            assist=SimpleNamespace(_send=lambda *args: sent.append(args), peer_id=lambda: "unused"))
        self.h.send_app = switched
        with mock.patch.object(self.h.st, "passkey_of_device", return_value=None):
            await _Host(a, desk).send_app(self.s, {"t": "term_out", "id": "terminal", "seq": 1, "d": "OLD"})
        self.assertFalse(sent)
