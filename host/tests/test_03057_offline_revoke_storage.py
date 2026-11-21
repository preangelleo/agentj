"""Exercise the archived R-0055 storage replay against temporary State and fake Host effects.

The storage functions are from d61aad7f23. Commit setup models only the durable roster
rename and its witness. Notice delivery is a test callback with no transport.
"""
import _hermetic  # noqa: F401,I001

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import _offline_revoke_journal as tx
from agentj import approvals
from agentj.state import State
from agentj.text import pick

REASON = "terminal_offline_revoke"
LIMIT, OUTBOX = "offline_revoke.json", "security_outbox.json"
STEPS = ("teardown", "audit", "limit", "activity", "notice", "push", "dashboard")



def _audit(st, did: str, reason: str, rid: str) -> None:
    if any(r.get("id") == rid for r in approvals.read_log(st)):
        return
    approvals.record(st, rid=rid, agent="host", tool="agentj revoke",
                     input_sha256=approvals.input_digest({"device": did}), shown_sha256="", decision="allow", reason=reason,
                     device=did)


def _read(st, name):
    try:
        return json.loads((st.root / name).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _reconcile(st, iid):
    with st.devices_lock():
        it = _outbox(st).get(iid)
        if not tx.valid(iid, it):
            return "recovery_pending"
        records = tx.raw(st)
        rec = records.get(it["device"])
        witness = tx.witness(st, it)
        if it["phase"] == "aborted" or (it["phase"] == "prepared" and witness == "present" and rec
                                        and tx.digest(rec) == it["digest"]):
            if it["phase"] != "aborted":
                _outbox_edit(st, iid, lambda x: x.update(phase="aborted"))
            # A phase replacement may have become visible before its directory
            # sync failed. Establish durability anew before consuming evidence.
            tx.sync(st.root)
            if witness == "present":
                tx.stage_path(st, iid).unlink()
            elif witness != "consumed":
                return "recovery_pending"
            tx.sync(st.root)
            st.log("offline_revoke_aborted", id=iid)
            tx.sync(st.log_path)
            _outbox_remove(st, iid)
            return "not_removed"
        if witness != "consumed" or (rec and rec.get(tx.MARKER) == it["marker"]):
            return "recovery_pending"
        tx.sync(st.root)
        if it["phase"] != "removed":
            _outbox_edit(st, iid, lambda x: x.update(phase="removed"))
        return "removed"


def _commit_limit(st, it):
    with st.file_lock("offline_revoke"):
        lim = _read(st, LIMIT)
        if not isinstance(lim, dict) or _num(lim.get("last", 0)) is None:
            raise ValueError("unreadable limit")
        if lim.get("last", 0) < it["used"]:
            tx.replace(st.root / LIMIT, {**lim, "v": 1, "last": it["used"], "boot": it["boot"],
                                         "obs": None, "aged": 0, "fails": []})
        else:
            tx.sync(st.root / LIMIT)
            tx.sync(st.root)


def _outbox(st) -> dict:
    d = _read(st, OUTBOX)
    if not isinstance(d, dict):
        raise ValueError("unreadable security outbox")
    return d


def _outbox_remove(st, iid):
    with st.file_lock("security_outbox"):
        d = _outbox(st)
        d.pop(iid, None)
        tx.replace(st.root / OUTBOX, d)


def _outbox_put(st, item: dict) -> None:
    with st.file_lock("security_outbox"):
        d = _outbox(st)
        if item["id"] in d:
            raise ValueError("duplicate operation")
        d[item["id"]] = item
        tx.replace(st.root / OUTBOX, d)


def _outbox_edit(st, iid: str, fn) -> dict | None:
    with st.file_lock("security_outbox"):
        d = _outbox(st)
        if not isinstance(d.get(iid), dict):
            return None
        fn(d[iid])
        it = d[iid]
        if set(STEPS) <= set(it.get("done", [])) and not it.get("pending"):
            d.pop(iid)
        tx.replace(st.root / OUTBOX, d)
        return it


async def _effects(host, iid):
    st = host.st
    it = await asyncio.to_thread(lambda: _outbox(st).get(iid))
    if not tx.valid(iid, it) or it["phase"] != "removed":
        return
    did, name = it["device"], it["name"]
    for step in STEPS:
        if step in it["done"]:
            continue
        try:
            if step == "teardown":
                await host._drop_device(did, "revoked", removed=True, terminal_generation=it["marker"])
            elif step == "audit":
                await asyncio.to_thread(_audit_sync, st, did, iid)
            elif step == "limit":
                await asyncio.to_thread(_commit_limit, st, it)
            elif step == "activity":
                host.activity("decision", tool="agentj revoke", result="allow", reason=REASON,
                              by=pick(host.lang, "Agent J 主机终端", "Agent J host terminal"), summary=name)
            elif step == "notice":
                await _notice(host, iid, it)
                continue
            elif step == "push":
                host.push_notify("security")
            elif step == "dashboard":
                host.reporter.trigger("revoke")
            await asyncio.to_thread(_outbox_edit, st, iid, lambda x: x.update(done=sorted(set(x["done"]) | {step})))
        except Exception as exc:
            st.log("offline_revoke_effect_pending", id=iid, step=step, exception_type=type(exc).__name__)


def _audit_sync(st, did, iid):
    # Preserve older entries, including a torn final append. A new JSON row
    # needs its own line even after interruption in another approval writer.
    try:
        with st.approvals_path.open("rb") as log:
            log.seek(0, os.SEEK_END)
            if log.tell():
                log.seek(-1, os.SEEK_END)
                if log.read(1) != b"\n":
                    st.append_private(st.approvals_path, "")
    except FileNotFoundError:
        pass
    _audit(st, did, REASON, iid)
    tx.sync(st.approvals_path)
    tx.sync(st.root)
    rows = []
    with st.approvals_path.open("rb") as log:
        for line in log:
            if not line.endswith(b"\n"):
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(row, dict) and row.get("id") == iid:
                rows.append(row)
    if (len(rows) != 1 or rows[0].get("device") != did or rows[0].get("reason") != REASON
            or rows[0].get("decision") != "allow" or rows[0].get("tool") != "agentj revoke"):
        raise OSError("incomplete audit record")


async def _replay_locked(host):
    st = host.st
    try:
        operations = await asyncio.to_thread(_outbox, st)
    except (OSError, ValueError):
        st.log("offline_revoke_replay_pending")
        return
    for iid in operations:
        try:
            outcome = await asyncio.to_thread(_reconcile, st, iid)
            if outcome == "removed":
                await _effects(host, iid)
            elif outcome == "recovery_pending":
                st.log("offline_revoke_replay_pending")
        except Exception as exc:
            st.log("offline_revoke_replay_failed", exception_type=type(exc).__name__)


async def _notice(host, iid, item):
    for recipient in item["pending"]:
        host.effects["notice", recipient] += 1
    await asyncio.to_thread(_outbox_edit, host.st, iid, lambda x: x.update(
        pending=[], done=sorted(set(x["done"]) | {"notice"})))


class FakeHost:
    lang = "en"

    def __init__(self, root, effects):
        self.st = State(root)
        self.effects = effects
        self.reporter = mock.Mock()
        self.reporter.trigger.side_effect = lambda _reason: self.effects.update(["dashboard"])

    async def _drop_device(self, did, _reason, *, removed, terminal_generation):
        assert removed and did not in self.st.devices() and terminal_generation == "f" * 32
        self.effects["teardown"] += 1

    def activity(self, *_args, **_kwargs):
        self.effects["activity"] += 1

    def push_notify(self, _reason):
        self.effects["push"] += 1


class OfflineRevokeStorage(unittest.IsolatedAsyncioTestCase):
    iid = "orv-0000000000000001"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="aj-revoke-fixture-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.effects = Counter()
        self.st = State(self.root)
        self.target = {tx.MARKER: "f" * 32, "paired_at": 1}
        self.retained = {"paired_at": 2}
        tx.replace(self.st.config_path, {"channel": "synthetic"})
        tx.replace(self.st.devices_path, {"lost": self.target, "retained": self.retained})

    def prepare(self, *, commit=False):
        stage = tx.prepare(self.st, self.iid, {"retained": self.retained})
        _outbox_put(self.st, {
            "v": 2, "id": self.iid, "device": "lost", "name": "Synthetic remote",
            "stage": stage, "used": 123.0, "boot": None,
            "marker": self.target[tx.MARKER], "digest": tx.digest(self.target),
            "phase": "prepared", "done": [], "recipients": {"retained": tx.digest(self.retained)},
            "pending": ["retained"],
        })
        if commit:
            os.replace(tx.stage_path(self.st, self.iid), self.st.devices_path)
            tx.sync(self.root)

    async def restart(self):
        await _replay_locked(FakeHost(self.root, self.effects))

    def audit(self):
        return [row for row in approvals.read_log(self.st) if row.get("id") == self.iid]

    async def test_committed_removal_recovers_audit_and_effects_during_quota_failure(self):
        self.prepare(commit=True)
        original = tx.replace

        def fail_quota(path, data):
            if path.name == LIMIT:
                raise OSError("synthetic quota write fault")
            original(path, data)

        with mock.patch.object(tx, "replace", side_effect=fail_quota):
            await self.restart()
            await self.restart()
        expected = Counter({"teardown": 1, "activity": 1, ("notice", "retained"): 1, "push": 1, "dashboard": 1})
        self.assertEqual(self.effects, expected)
        self.assertEqual(self.st.devices(), {"retained": self.retained})
        self.assertEqual([row["reason"] for row in self.audit()], [REASON])
        self.assertEqual(set(_outbox(self.st)[self.iid]["done"]), set(STEPS) - {"limit"})
        await self.restart()
        self.assertEqual(_read(self.st, LIMIT)["last"], 123)
        self.assertEqual(_outbox(self.st), {})
        await self.restart()
        self.assertEqual(len(self.audit()), 1)
        self.assertEqual(self.effects, expected)

    async def test_precommit_failure_retains_both_devices_and_has_no_removal_effects(self):
        self.prepare()
        await self.restart()
        await self.restart()
        self.assertEqual(self.st.devices(), {"lost": self.target, "retained": self.retained})
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.effects, Counter())
        self.assertEqual(_outbox(self.st), {})
        self.assertFalse(tx.stage_path(self.st, self.iid).exists())

    async def test_truncated_audit_row_kept_separate_from_committed_removal(self):
        self.prepare(commit=True)
        self.st.append_private(self.st.approvals_path, '{"id":"earlier"}')
        torn = b'{"id":"interrupted"'
        with self.st.approvals_path.open("ab") as log:
            log.write(torn)
        await self.restart()
        await self.restart()
        self.assertEqual(self.st.approvals_path.read_bytes().splitlines()[1], torn)
        self.assertEqual([row["id"] for row in approvals.read_log(self.st)], ["earlier", self.iid])
        self.assertEqual(_outbox(self.st), {})

    async def test_abort_sync_failure_preserves_witness_for_next_restart(self):
        self.prepare()
        original = tx.sync

        def fail_abort_sync(path):
            if path == self.root and _outbox(self.st)[self.iid]["phase"] == "aborted":
                raise OSError("synthetic cancellation directory sync fault")
            original(path)

        with mock.patch.object(tx, "sync", side_effect=fail_abort_sync):
            await self.restart()
        self.assertTrue(tx.stage_path(self.st, self.iid).exists())
        self.assertEqual(_outbox(self.st)[self.iid]["phase"], "aborted")
        self.assertIn("lost", self.st.devices())
        await self.restart()
        self.assertEqual(self.st.devices(), {"lost": self.target, "retained": self.retained})
        self.assertFalse(tx.stage_path(self.st, self.iid).exists())
        self.assertEqual(_outbox(self.st), {})
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.effects, Counter())
