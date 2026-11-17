"""R-0049 (0.30.32): host efficiency items from the 2026-11-15 architecture audit, behaviour unchanged.

H6  the allowlist cache holds the revocation-filtered result; a revocation journal write (this process or another one)
    must deny at the very next check (negatives), and an unchanged allowlist must not reread the journal (positive).
H8  a streamed reply rewrites history meta.json only when a continuation page takes a new id; a restart still reloads
    the same epoch / next_id / turns (negative: an id taken by a continuation page is never handed out twice).
H9  claude_sessions keeps a settled registry file's parse: a same-size rewrite inside one timestamp tick of a young file
    is still seen, a dead pid is still dropped (liveness is never cached), returned rows are private copies.
H10 reconcile_claude skips an unchanged transcript only while the board keys it depends on are unchanged: a task
    registered after its completion was written is still stopped, an appended line is read, a completion stamped in
    the future is recovered once the clock passes it.
H11 all_since returns exactly what the page-by-page walk returned, parsing each day once; record() keeps the activity
    switch per config.json identity: off from this process, from another process, or by a same-tick rewrite of a young
    file is honoured at the next record.
H12 preferences_loop: an exception other than OSError out of apply_preferences no longer ends config hot-reload (the
    next change is applied); a settled, unchanged file is not re-read; a same-tick rewrite of a young file and a pass
    that found the lock busy are still applied.
H13 diagnostic_log.record sorts only the allowed fields a line carries: the same field order as sorting the whole
    allowlist, so the same output and the same cut-off when the node budget runs out; unlisted fields still dropped.
Throw-away state under TEST_TMP / the system temp dir; HOME is never written.
"""
import _hermetic  # noqa: F401
import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentj import activity, busy, diagnostic_log, preferences, serve, credential_revoke, history as history_mod, shared, state as state_mod, wire


def _tmp(test):
    root = Path(os.environ.get("TEST_TMP") or tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="r0049-", dir=root))
    test.addCleanup(shutil.rmtree, d, True)
    os.chmod(d, 0o700)
    return d


class AllowlistRevocationCache(unittest.TestCase):
    def setUp(self):
        self.root = _tmp(self)
        self.st = state_mod.State(self.root)
        self.pubs = [os.urandom(32) for _ in range(3)]
        self.devs = {}
        for i, p in enumerate(self.pubs):
            self.devs[wire.device_id(p)] = {"pub": wire.b64u(p), "name": f"phone{i}",
                                            "pk": {"id": wire.b64u(os.urandom(32)), "rp": "m.example.com"}}
        self.st.write_private(self.st.devices_path, json.dumps(self.devs).encode())
        self.journal = {"epoch": 1, "fingerprints": [], "devices": [], "cutoff": 0}
        self.jpath = self.root / "credential_revocations.json"
        self.st.write_private(self.jpath, json.dumps(self.journal).encode())

    def _other_process_write(self, journal):
        """What another process's write_private does: tmp + rename, without this process's write counter."""
        tmp = self.jpath.with_name(".other-" + os.urandom(4).hex())
        tmp.write_bytes(json.dumps(journal).encode())
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.jpath)

    def test_device_revoked_in_this_process_is_denied_at_the_next_check(self):
        did = wire.device_id(self.pubs[0])
        self.assertTrue(self.st.is_allowed(self.pubs[0]))          # cache warm
        self.assertTrue(self.st.is_allowed(self.pubs[0]))
        self.st.write_private(self.jpath, json.dumps({**self.journal, "devices": [did]}).encode())
        self.assertFalse(self.st.is_allowed(self.pubs[0]))
        self.assertNotIn(did, self.st.devices())
        self.assertTrue(self.st.is_allowed(self.pubs[1]))

    def test_device_revoked_by_another_process_is_denied_at_the_next_check(self):
        did = wire.device_id(self.pubs[1])
        self.assertTrue(self.st.is_allowed(self.pubs[1]))
        with mock.patch.object(state_mod, "_WRITES", [state_mod._WRITES[0]]):   # the counter cannot help here
            self._other_process_write({**self.journal, "devices": [did]})
            self.assertFalse(self.st.is_allowed(self.pubs[1]))
            self.assertNotIn(did, self.st.devices())

    def test_passkey_fingerprint_revoked_by_another_process_is_denied_at_the_next_check(self):
        did = wire.device_id(self.pubs[2])
        fp = credential_revoke.target(self.devs[did]["pk"])
        self.assertTrue(self.st.is_allowed(self.pubs[2]))
        with mock.patch.object(state_mod, "_WRITES", [state_mod._WRITES[0]]):
            self._other_process_write({**self.journal, "epoch": 2, "fingerprints": [fp]})
            self.assertFalse(self.st.is_allowed(self.pubs[2]))
        self.assertTrue(self.st.is_allowed(self.pubs[0]))

    def test_journal_appearing_after_a_journal_less_check_is_applied(self):
        os.unlink(self.jpath)
        self.st._devices_cache = None
        self.assertTrue(self.st.is_allowed(self.pubs[0]))
        with mock.patch.object(state_mod, "_WRITES", [state_mod._WRITES[0]]):
            self._other_process_write({**self.journal, "devices": [wire.device_id(self.pubs[0])]})
            self.assertFalse(self.st.is_allowed(self.pubs[0]))

    def test_damaged_journal_still_fails_closed(self):
        self.assertTrue(self.st.is_allowed(self.pubs[0]))
        with mock.patch.object(state_mod, "_WRITES", [state_mod._WRITES[0]]):
            self._other_process_write({"epoch": "bad"})
            self.assertFalse(self.st.is_allowed(self.pubs[0]))
            self.assertEqual(self.st.devices(), {})

    def test_devices_still_returns_a_private_copy(self):
        d = self.st.devices()
        d[wire.device_id(self.pubs[0])]["pub"] = "tampered"
        d.clear()
        self.assertTrue(self.st.is_allowed(self.pubs[0]))
        self.assertEqual(len(self.st.devices()), 3)


class HistoryMetaWrites(unittest.TestCase):
    def setUp(self):
        self.root = _tmp(self)
        self.st = state_mod.State(self.root)
        self.st.write_private(self.st.config_path, b"{}")
        self.meta_writes = 0
        real = self.st.write_private

        def counting(path, data):
            if Path(path).name == "meta.json":
                self.meta_writes += 1
            return real(path, data)
        self.st.write_private = counting

    def test_streamed_chunks_do_not_rewrite_meta(self):
        h = history_mod.History(self.st, on=True)
        t = h.add({"k": "phone", "text": "hi"})
        after_add = self.meta_writes
        self.assertEqual(after_add, 1)
        tid = t["id"]
        for _ in range(200):
            tid = h.update(tid, append="x" * 50)[-1]["id"]
        h.update(tid, end="done")
        self.assertEqual(self.meta_writes, after_add)          # no new id → meta.json untouched
        back = history_mod.History(self.st, on=True)
        self.assertEqual((back.epoch, back.next_id), (h.epoch, h.next_id))
        self.assertEqual(back.turns[t["id"]]["reply"]["text"], h.turns[t["id"]]["reply"]["text"])
        self.assertEqual(back.turns[t["id"]]["end"], "done")

    def test_continuation_page_id_is_persisted_and_never_reused(self):
        h = history_mod.History(self.st, on=True)
        t = h.add({"k": "phone", "text": "hi"})
        before = self.meta_writes
        pages = h.update(t["id"], append="y" * (history_mod.PART_MAX + 10))
        self.assertEqual(len(pages), 2)
        self.assertEqual(self.meta_writes, before + 1)          # the new page id reached meta.json
        meta = json.loads(h.meta_path.read_text())
        self.assertEqual(meta["next_id"], h.next_id)
        # a restart that lost current.jsonl's tail must still not hand the continuation's id out again
        (h.dir / "current.jsonl").write_text(json.dumps(h.turns[t["id"]], ensure_ascii=False) + "\n")
        back = history_mod.History(self.st, on=True)
        self.assertGreater(back.add({"k": "phone", "text": "next"})["id"], pages[-1]["id"])


@unittest.skipUnless(os.path.isdir("/proc/self"), "procfs start times (Linux)")
class ClaudeRegistryCache(unittest.TestCase):
    def setUp(self):
        self.base = _tmp(self)
        self.cfg = self.base / "claude"
        (self.cfg / "sessions").mkdir(parents=True)
        self.work = self.base / "work"
        self.work.mkdir()
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.cfg)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sock = self.base / "m.sock"
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(self.sock))
        self.addCleanup(self.srv.close)
        getattr(shared, "_REGISTRY", {}).clear()
        # settled = older than this when read (mtime and ctime; utime cannot age a ctime, so the tests wait instead)
        patcher = mock.patch.object(shared, "REGISTRY_SETTLED_S", 0.1, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _start(pid):
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rfind(")") + 2:].split()[19]

    def _row(self, sid, pid=None, status="idle"):
        pid = pid or os.getpid()
        return {"pid": pid, "sessionId": sid, "cwd": str(self.work), "kind": "interactive", "procStart": self._start(pid),
                "messagingSocketPath": str(self.sock), "updatedAt": 1, "startedAt": 1, "status": status}

    def _write(self, name, row, settle=False):
        p = self.cfg / "sessions" / name
        p.write_text(json.dumps(row))
        if settle:
            time.sleep(0.15)
        return p

    def test_young_file_rewritten_at_the_same_size_in_one_tick_is_seen(self):
        mock.patch.object(shared, "REGISTRY_SETTLED_S", 2.0, create=True).start()   # the shipped window
        self.addCleanup(mock.patch.stopall)
        sid = "aaaaaaaa-0000-0000-0000-000000000001"
        p = self._write("a.json", self._row(sid, status="busy"))
        st0 = os.stat(p)
        self.assertEqual(shared.claude_sessions(str(self.work), sid)[0]["status"], "busy")
        p.write_text(json.dumps(self._row(sid, status="idle")))        # same size, in place
        self.assertEqual(os.stat(p).st_size, st0.st_size)
        real_stat = os.stat

        def coarse(path, *a, **k):         # one timestamp tick: inode, size, mtime and ctime all as before the rewrite
            return st0 if not a and not k and Path(path) == p else real_stat(path, *a, **k)
        with mock.patch.object(shared.os, "stat", coarse):
            self.assertEqual(shared.claude_sessions(str(self.work), sid)[0]["status"], "idle")

    def test_settled_file_is_parsed_once(self):
        sid = "aaaaaaaa-0000-0000-0000-000000000002"
        p = self._write("b.json", self._row(sid), settle=True)
        real, reads = Path.read_text, []

        def spy(path, *a, **k):
            if Path(path) == p:
                reads.append(1)
            return real(path, *a, **k)
        with mock.patch.object(Path, "read_text", spy):
            for _ in range(20):
                self.assertEqual(len(shared.claude_sessions(str(self.work), sid)), 1)
        self.assertEqual(len(reads), 1)

    def test_settled_file_replaced_is_seen(self):
        sid = "aaaaaaaa-0000-0000-0000-000000000003"
        p = self._write("c.json", self._row(sid, status="busy"), settle=True)
        self.assertEqual(shared.claude_sessions(str(self.work), sid)[0]["status"], "busy")
        tmp = p.with_name("c.tmp")
        tmp.write_text(json.dumps(self._row(sid, status="idle")))
        os.replace(tmp, p)
        self.assertEqual(shared.claude_sessions(str(self.work), sid)[0]["status"], "idle")

    def test_dead_process_is_dropped_although_its_parse_is_cached(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait()))
        sid = "aaaaaaaa-0000-0000-0000-000000000004"
        self._write("d.json", self._row(sid, pid=child.pid), settle=True)
        self.assertEqual(len(shared.claude_sessions(str(self.work), sid)), 1)
        child.kill()
        child.wait()
        self.assertEqual(shared.claude_sessions(str(self.work), sid), [])

    def test_returned_rows_are_private_copies(self):
        sid = "aaaaaaaa-0000-0000-0000-000000000005"
        self._write("e.json", self._row(sid), settle=True)
        row = shared.claude_sessions(str(self.work), sid)[0]
        row["_agentj_transcript_attestation"] = object()
        row["status"] = "tampered"
        again = shared.claude_sessions(str(self.work), sid)[0]
        self.assertNotIn("_agentj_transcript_attestation", again)
        self.assertEqual(again["status"], "idle")

    def test_other_directory_and_bad_directory_match_nothing(self):
        sid = "aaaaaaaa-0000-0000-0000-000000000006"
        self._write("f.json", self._row(sid), settle=True)
        other = self.base / "other"
        other.mkdir()
        self.assertEqual(shared.claude_sessions(str(other)), [])
        self.assertEqual(shared.claude_sessions("bad\0dir"), [])
        link = self.base / "link"
        link.symlink_to(self.work)
        self.assertEqual(len(shared.claude_sessions(str(link), sid)), 1)


class ReconcileClaudeSkip(unittest.TestCase):
    OWNER = "shared-claude:abc"

    def setUp(self):
        self.root = _tmp(self)
        self.tr = self.root / "t.jsonl"
        self.b = busy.Board(self.root)
        self.b.claude_transcripts = {self.OWNER: str(self.tr)}
        self.b.mem[self.OWNER + ":tu:gate"] = {"key": self.OWNER + ":tu:gate", "session": self.OWNER, "since": time.time()}
        self.lines = [{"type": "assistant", "uuid": "u1", "message": {"content": [{"type": "text", "text": "hi"}]}}]
        self._flush()

    def _flush(self):
        self.tr.write_text("".join(json.dumps(x) + "\n" for x in self.lines))

    def _reads(self):
        real, n = open, [0]

        def spy(path, *a, **k):
            if str(path) == str(self.tr):
                n[0] += 1
            return real(path, *a, **k)
        return n, mock.patch("builtins.open", spy)

    def test_unchanged_transcript_and_board_is_not_reread(self):
        n, patch = self._reads()
        with patch:
            for _ in range(5):
                self.b.reconcile_claude(None)
        self.assertEqual(n[0], 1)

    def test_task_registered_after_its_completion_was_written_is_still_stopped(self):
        self.lines.append({"type": "system", "subtype": "task_notification", "task_id": "T1", "status": "completed"})
        self._flush()
        self.b.reconcile_claude(None)
        self.b.reconcile_claude(None)
        key = self.OWNER + ":task:T1"
        self.b.mem[key] = {"key": key, "session": self.OWNER, "since": time.time()}
        self.b.reconcile_claude(None)                   # transcript unchanged, board changed
        self.assertNotIn(key, self.b.mem)
        self.assertTrue(any(f.get("key") == key for f in self.b.finished))

    def test_appended_line_is_read(self):
        key = self.OWNER + ":task:T2"
        self.b.mem[key] = {"key": key, "session": self.OWNER, "since": time.time()}
        self.b.reconcile_claude(None)
        self.b.reconcile_claude(None)
        self.lines.append({"type": "system", "subtype": "task_updated", "task_id": "T2", "patch": {"status": "completed"}})
        self._flush()
        self.b.reconcile_claude(None)
        self.assertNotIn(key, self.b.mem)

    def test_future_stamped_completion_is_recovered_once_the_clock_passes_it(self):
        now = time.time()
        key = self.OWNER + ":T3"
        self.b.launched[key] = False
        self.b.native_starts[key] = {"since": int(now) - 100, "generation": "0" * 32, "label": "job"}
        note = "<task-notification><task-id>T3</task-id><status>completed</status></task-notification>"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60))
        self.lines += [{"type": "queue-operation", "operation": "enqueue", "content": note, "timestamp": stamp},
                       {"type": "queue-operation", "operation": "remove", "content": note, "timestamp": stamp}]
        self._flush()
        self.b.reconcile_claude(None)
        self.b.reconcile_claude(None)
        self.assertFalse(any(f.get("key") == key for f in self.b.finished))     # not yet: stamped in the future
        with mock.patch.object(busy.time, "time", return_value=now + 120):
            self.b.reconcile_claude(None)
        self.assertTrue(any(f.get("key") == key for f in self.b.finished))


class ActivityPagingAndSwitch(unittest.TestCase):
    def setUp(self):
        self.root = _tmp(self)
        self.st = state_mod.State(self.root)
        self.st.write_private(self.st.config_path, json.dumps({"channel": "c", "activity": True}).encode())
        d = self.root / "activity"
        d.mkdir(mode=0o700)
        for k, day in enumerate(("2026-11-15", "2026-11-16", "2026-11-17")):
            with open(d / (day + ".jsonl"), "w") as f:
                for i in range(437):
                    f.write(json.dumps({"ts": 1_700_000_000_000 + k * 10_000 + i, "k": "turn_end", "i": i}) + "\n")
                    if i % 97 == 0:
                        f.write("not json\n")     # skipped lines shift cursors: the walk must keep them as before

    def _old_walk(self, since_ms=None, limit=100_000):
        items, cur = [], None
        while len(items) < limit:
            got, cur = activity.page(self.st, cur, activity.PAGE, since_ms)
            items += got
            if not cur:
                break
        return list(reversed(items))

    def test_all_since_is_the_old_walk_with_one_parse_per_day(self):
        for since, limit in ((None, 100_000), (1_700_000_010_200, 100_000), (None, 120), (1_700_000_000_000, 51)):
            want = self._old_walk(since, limit)
            real, calls = activity._read_day, []

            def spy(st, name):
                calls.append(name)
                return real(st, name)
            with mock.patch.object(activity, "_read_day", spy):
                got = activity.all_since(self.st, since, limit)
            self.assertEqual(got, want)
            self.assertEqual(len(calls), len(set(calls)))      # every day parsed at most once
        self.assertEqual(len(activity.all_since(self.st)), 3 * 437)

    def _other_process_config(self, cfg):
        tmp = self.st.config_path.with_name(".cfg-" + os.urandom(4).hex())
        tmp.write_text(json.dumps(cfg))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.st.config_path)

    def _settle(self):
        mock.patch.object(activity, "SETTLED_S", 0.1, create=True).start()
        self.addCleanup(mock.patch.stopall)
        time.sleep(0.15)

    def test_switch_off_from_this_process_is_honoured_at_the_next_record(self):
        self._settle()
        self.assertIsNotNone(activity.record(self.st, "turn_end"))
        activity.set_enabled(self.st, False)
        self.assertIsNone(activity.record(self.st, "turn_end"))

    def test_switch_off_from_another_process_is_honoured_at_the_next_record(self):
        self._settle()
        self.assertIsNotNone(activity.record(self.st, "turn_end"))
        with mock.patch.object(state_mod, "_WRITES", [state_mod._WRITES[0]]):
            self._other_process_config({"channel": "c", "activity": False})
            self.assertIsNone(activity.record(self.st, "turn_end"))

    def test_young_file_rewritten_in_one_tick_is_read_again(self):
        p = self.st.config_path
        p.write_text(json.dumps({"channel": "cc", "activity": True}))     # young; the same size as the rewrite below
        st0 = os.stat(p)
        self.assertIsNotNone(activity.record(self.st, "turn_end"))
        p.write_text(json.dumps({"channel": "c", "activity": False}))
        self.assertEqual(os.stat(p).st_size, st0.st_size)
        real_stat = os.stat

        def coarse(path, *a, **k):
            return st0 if not a and not k and Path(path) == p else real_stat(path, *a, **k)
        with mock.patch.object(activity.os, "stat", coarse):
            self.assertIsNone(activity.record(self.st, "turn_end"))

    def test_settled_config_is_read_once(self):
        self._settle()
        with mock.patch.object(activity, "enabled", side_effect=activity.enabled) as spy:
            for _ in range(30):
                activity.record(self.st, "turn_end")
        self.assertEqual(spy.call_count, 1)

    def test_state_doubles_answer_as_before(self):
        """A test double without config_path, or a State whose config() is replaced, is asked every time (no cache)."""
        from types import SimpleNamespace
        d = SimpleNamespace(root=self.root, config=lambda: {"activity": False})
        self.assertIsNone(activity.record(d, "turn_end"))
        self._settle()
        self.assertIsNotNone(activity.record(self.st, "turn_end"))
        self.st.config = lambda: {"activity": False}
        self.assertIsNone(activity.record(self.st, "turn_end"))


class PreferencesLoop(unittest.TestCase):
    def setUp(self):
        self.root = _tmp(self)
        self.cfgdir = self.root / "xdg"
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.cfgdir)})
        env.start()
        self.addCleanup(env.stop)
        self.path = preferences.path()
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"appearance": {"theme": "dark"}}')
        self.logs, self.applied, self.fail = [], [], []

        class St:
            root = self.root

            def log(_, ev, **kw):
                self.logs.append(ev)
        host = mock.Mock(spec=["stopping", "config_stamp", "st", "apply_preferences", "config_problem"])
        host.stopping, host.config_stamp, host.st, host.config_problem = asyncio.Event(), None, St(), None

        async def apply():
            self.applied.append(self.path.read_text())
            if self.fail:
                raise self.fail.pop(0)
        host.apply_preferences = apply
        self.host = host

    async def _ticks(self, n, between=None):
        real_sleep = asyncio.sleep
        ticks = [0]

        async def fast(_):
            ticks[0] += 1
            await real_sleep(0)
        with mock.patch.object(serve.asyncio, "sleep", fast):
            task = asyncio.ensure_future(serve.Host.preferences_loop(self.host))
            for i in range(n):
                while ticks[0] <= i and not task.done():
                    await real_sleep(0)
                if between:
                    between(i)
            self.host.stopping.set()
            while ticks[0] <= n and not task.done():
                await real_sleep(0)
        await asyncio.wait_for(task, 5)
        return task

    def test_non_oserror_from_apply_does_not_end_hot_reload(self):
        self.fail.append(RuntimeError("boom"))

        def between(i):
            if i == 3:
                self.path.write_text('{"appearance": {"theme": "light"}}')
        asyncio.run(self._ticks(8, between))
        self.assertEqual(len(self.applied), 2)
        self.assertIn("light", self.applied[-1])
        self.assertIn("preferences_loop_failed", self.logs)

    def test_settled_unchanged_file_is_not_reread(self):
        mock.patch.object(serve, "PREFS_SETTLED_S", 0.1, create=True).start()
        self.addCleanup(mock.patch.stopall)
        time.sleep(0.15)
        real, reads = Path.read_bytes, []

        def spy(path):
            if Path(path) == self.path:
                reads.append(1)
            return real(path)
        with mock.patch.object(Path, "read_bytes", spy):
            asyncio.run(self._ticks(20))
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(len(reads), 1)

    def test_young_file_rewritten_in_one_tick_is_applied(self):
        st0 = os.stat(self.path)
        real_stat = os.stat

        def coarse(path, *a, **k):
            return st0 if not a and not k and Path(path) == self.path else real_stat(path, *a, **k)

        def between(i):
            if i == 2:
                self.path.write_text('{"appearance": {"theme": "lite"}}')   # same size, same stat below
                self.assertEqual(real_stat(self.path).st_size, st0.st_size)
        with mock.patch.object(serve.os, "stat", coarse):
            asyncio.run(self._ticks(6, between))
        self.assertEqual(len(self.applied), 2)
        self.assertIn("lite", self.applied[-1])

    def test_busy_lock_pass_is_retried_for_an_unchanged_file(self):
        mock.patch.object(serve, "PREFS_SETTLED_S", 0.1, create=True).start()
        self.addCleanup(mock.patch.stopall)
        time.sleep(0.15)
        fd = os.open(self.root / "preferences.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)

        def between(i):
            if i == 3:
                fcntl.flock(fd, fcntl.LOCK_UN)
        asyncio.run(self._ticks(8, between))
        self.assertEqual(len(self.applied), 1)


class DiagnosticLogOrder(unittest.TestCase):
    PRIORITY = ("status", "cid", "id", "request", "phase", "task", "code", "exception_type")

    def _whole_allowlist_order(self, allowed, fields):
        p = self.PRIORITY
        return [k for k in sorted(allowed, key=lambda k: (k not in p, p.index(k) if k in p else k)) if k in fields]

    def test_order_and_budget_cut_match_the_whole_allowlist_sort(self):
        import random
        allowed = state_mod.State.LOG_FIELDS
        names = sorted(allowed) + ["not_listed", "body", "token"]
        rnd = random.Random(49)
        cut_seen = 0
        for _ in range(400):
            fields = {}
            for k in rnd.sample(names, rnd.randint(1, 25)):
                fields[k] = rnd.choice([1, "v", None, True, ["x"] * rnd.randint(1, 40),
                                        {f"k{i}": [i] * 20 for i in range(rnd.randint(1, 20))}])
            out = diagnostic_log.record("ev", fields, allowed)
            keys = [k for k in out if k not in ("ev", "diagnostic_truncated")]
            want = self._whole_allowlist_order(allowed, fields)
            self.assertEqual(keys, want[:len(keys)])
            self.assertTrue(set(keys) <= allowed)
            if len(keys) < len(want):
                cut_seen += 1
                self.assertTrue(out.get("diagnostic_truncated"))
        self.assertGreater(cut_seen, 0)    # the budget cut-off was exercised


class RecentIdsBound(unittest.TestCase):
    def test_bounded_oldest_first_and_recent_duplicates_still_blocked(self):
        ids = shared.RecentIds(100)
        for i in range(1000):
            ids.add(f"u{i}")
            ids.add(f"u{i}")                                   # re-adding never refreshes nor grows
        self.assertEqual(len(ids), 100)
        self.assertNotIn("u0", ids)
        self.assertNotIn("u899", ids)
        for i in range(900, 1000):
            self.assertIn(f"u{i}", ids)                       # the newest 100 are all still blocked
        ids.clear()
        self.assertEqual(len(ids), 0)

    def test_shared_agent_uses_the_bounded_set(self):
        self.assertGreaterEqual(shared.SEEN_MAX, 10000)
        a = shared.SharedClaudeAgent.__new__(shared.SharedClaudeAgent)
        with mock.patch.object(shared.Agent, "__init__", lambda *a, **k: None):
            try:
                shared.SharedClaudeAgent.__init__(a, mock.MagicMock(), {"dir": str(_tmp(self))})
            except Exception:  # noqa: BLE001 — only the attributes set before any host wiring matter here
                pass
        self.assertIsInstance(a.seen, shared.RecentIds)
        self.assertEqual(a.seen.cap, shared.SEEN_MAX)


class RecentDuplicateThroughTheReader(unittest.TestCase):
    """The real read_transcript loop; sidechain records stop right after the de-duplication step (no turn wiring)."""

    def test_duplicated_record_inside_a_small_window_is_read_once(self):
        tr = _tmp(self) / "t.jsonl"
        recs = [{"type": "assistant", "uuid": u, "isSidechain": True} for u in ("U1", "U2", "U1", "U3", "U2")]
        tr.write_text("".join(json.dumps(r) + "\n" for r in recs))
        added = []

        class Counting(shared.RecentIds):
            __slots__ = ()

            def add(self, ident):
                added.append(ident)
                super().add(ident)
        a = shared.SharedClaudeAgent.__new__(shared.SharedClaudeAgent)
        a.transcript, a.offset, a.discard_line, a.identity_marker, a.session = tr, 0, False, None, None
        a.seen = Counting(4)
        for i in range(10):
            a.seen.add(f"old-{i}")                            # the window is already past its cap
        del added[:]
        a.read_transcript()
        self.assertEqual(added, ["U1", "U2", "U3"])          # both recent duplicates skipped
        self.assertEqual(a.offset, tr.stat().st_size)


if __name__ == "__main__":
    unittest.main()
