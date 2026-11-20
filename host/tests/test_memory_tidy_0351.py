"""R-0065. Use real Host, heartbeat, configured native adapter, and CLI in a synthetic account."""
import _hermetic  # noqa: F401 — isolate CLI subprocess discovery from live host accounts
import asyncio
import datetime as dt
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentj import agent, memory, memory_nightly, memory_tidy, serve
from agentj.state import State

HERE = Path(__file__).resolve().parents[1]
OLD = "- 2026-11-01 提醒时间：主人说「晚上九點　整。」"
NEW = "- 2026-11-18 提醒时间：主人说「八點整，標點也要留！」"
DONE = "- [x] Finished the one-off test migration"
SOURCE = "# Memory\n\n" + OLD + "\n" + NEW + "\n" + DONE + "\n"


class Nightly(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.work = self.tmp / "work"
        self.work.mkdir()
        env = {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp/"config"),
               "XDG_STATE_HOME": str(self.tmp/"xdg-state"), "AGENTJ_STATE_DIR": str(self.tmp/"state"),
               "CLAUDE_CONFIG_DIR": str(self.tmp/"claude"), "CODEX_HOME": str(self.tmp/"codex"),
               "PYTHONPATH": os.pathsep.join((str(HERE), os.environ.get("PYTHONPATH", "")))}
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.st = State(self.tmp/"state")
        self.st.init(relay="ws://127.0.0.1:1", _setup_root=self.work)
        self.now = dt.datetime(2026, 11, 20, 3, 25, tzinfo=dt.timezone.utc).timestamp()

    def host(self, kind):
        self.st.set_agent_config(kind, str(self.work))
        self.st.add_device(bytes(range(32)), "synthetic paired device")
        h = serve.Host(self.st, events="jsonl", read_stdin=False)
        cfg = self.st.agent_config()
        h.agent = agent.make(h, {**cfg, "session_mode": "independent"})
        h.preferences["heartbeat"].update(memory_tidy_timezone="UTC", interval_minutes=5, daily_at="")
        h.heartbeat.started = h.heartbeat.last_activity = self.now - 400
        return h

    def cli(self, *args):
        p = subprocess.run([sys.executable, "-m", "agentj.cli", "memory", *args],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def journey(self, kind, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(SOURCE.encode())
        h = self.host(kind)
        preview = json.loads(self.cli("tidy", "--dry-run", "--json"))
        self.assertEqual(preview["changed_files"], 1)
        self.assertEqual(path.read_bytes(), SOURCE.encode())
        self.assertEqual(memory.trash(self.st), [])
        h.agent.status = "working"
        self.assertEqual(asyncio.run(h.heartbeat.tick(self.now))["why"], "busy")
        self.assertEqual(path.read_bytes(), SOURCE.encode())
        h.agent.status = "idle"
        result = asyncio.run(h.heartbeat.tick(self.now + 40))
        self.assertEqual(result and result.get("reason"), "memory_tidy", result)
        self.assertEqual((result["merged"], result["archived"], result["conflicts"]), (1, 1, 1))
        after = path.read_bytes()
        self.assertIn((NEW+"\n").encode(), after)
        self.assertIn(b"2026-11-01", after)
        self.assertNotIn(OLD.encode(), after)
        self.assertNotIn(DONE.encode(), after)
        self.assertIsNone(memory_nightly.observe(h, self.now + 90, idle=True))
        rec = json.loads(self.cli("tidy", "--last", "--json"))
        self.assertEqual(rec["conflicts"], 1)
        self.assertIn("合并 1、归档 1、冲突 1", memory_nightly.morning_line(self.st, "zh", self.now + 5*3600))
        self.assertIn("1 merged", memory_nightly.morning_line(self.st, "en", self.now + 5*3600))
        self.assertEqual(memory_nightly.morning_line(self.st, "en", self.now + 7*86400), "")
        [archive] = memory.trash(self.st)
        self.assertEqual(archive["text"].encode(), SOURCE.encode())
        self.assertEqual(memory.purge_trash(self.st, now=archive["ts"] + 999*86400), 0)
        self.cli("restore", archive["id"])
        self.assertEqual(path.read_bytes(), SOURCE.encode())

    def test_claude_journey(self):
        self.journey("claude", self.tmp/"claude"/"projects"/memory.slug(str(self.work))/"memory"/"reminders.md")

    def test_codex_journey(self):
        self.journey("codex", self.tmp/"codex"/"memories"/"memory_summary.md")

    def test_opencode_journey(self):
        self.journey("opencode", self.tmp/"config"/"opencode"/"AGENTS.md")

    def test_upgrade_in_progress_defers_but_retained_done_journal_allows_tidy(self):
        from agentj import auto_update
        h = self.host("opencode")
        auto_update.write(self.st, {"phase": "installing"}, auto_update.JOB)
        self.assertIsNone(memory_nightly.observe(h, self.now, idle=True))
        auto_update.write(self.st, {"phase": "done"}, auto_update.JOB)
        receipt = memory_nightly.observe(h, self.now + 5, idle=True)
        self.assertEqual(receipt["reason"], "memory_tidy")

    def test_active_or_unverified_todo_has_priority_over_maintenance(self):
        h = self.host("opencode")
        from unittest.mock import patch
        for key, value in (("active", True), ("review", ["t2"]), ("ready", ["t3"])):
            plan = {"active": False, "ready": [], "review": [], key: value}
            with patch("agentj.heartbeat_todo.offer", return_value=plan), \
                    patch("agentj.memory_nightly.observe", return_value=None) as observe:
                asyncio.run(h.heartbeat.tick(self.now))
                self.assertFalse(any(call.kwargs["idle"] for call in observe.call_args_list))
            h.heartbeat.last_checked.clear()

    def test_switch_closed_during_maintenance_stops_before_next_source(self):
        h = self.host("codex")
        from unittest.mock import patch
        def close_switch(st, kind, work, *, still_idle):
            self.assertTrue(still_idle())
            import copy
            h.preferences = copy.deepcopy(h.preferences)
            h.preferences["heartbeat"]["memory_tidy_enabled"] = False
            self.assertFalse(still_idle())
            return dict(deferred=True, merged=0, archived=0, conflicts=0, index_fixed=0, guarded=0)
        with patch("agentj.memory_tidy.run", side_effect=close_switch):
            self.assertIsNone(memory_nightly.observe(h, self.now, idle=True))

    def test_skip_window_off_and_daylight(self):
        h = self.host("opencode")
        path = self.work/"documentation"/"MEMORY.md"
        path.parent.mkdir()
        path.write_text(SOURCE)
        # Cross midnight in the owner's configured zone, independently of the host clock.
        h.preferences["heartbeat"].update(memory_tidy_start="23:30", memory_tidy_end="02:30",
                                          memory_tidy_timezone="America/New_York")
        before = dt.datetime(2026, 11, 20, 4, 20, tzinfo=dt.timezone.utc).timestamp()
        for t in (before, before+1200, before+4*3600):
            self.assertIsNone(memory_nightly.observe(h, t, idle=t != before+1200))
        self.assertEqual(memory_nightly.read(self.st)["status"], "skipped")
        self.assertEqual(path.read_text(), SOURCE)
        h.preferences["heartbeat"]["memory_tidy_enabled"] = False
        self.assertIsNone(memory_nightly.observe(h, before+86400+1200, idle=True))
        self.assertEqual(path.read_text(), SOURCE)

    def test_daylight_saving_windows_run_once_per_local_night(self):
        h = self.host("codex")
        h.preferences["heartbeat"].update(memory_tidy_timezone="America/New_York",
                                          memory_tidy_start="01:00", memory_tidy_end="02:30")
        def utc(day, hour, minute=0):
            return dt.datetime(2026, *day, hour, minute, tzinfo=dt.timezone.utc).timestamp()

        # Both instants display 01:15 on the clock when DST ends.
        first, second = utc((11, 1), 5, 15), utc((11, 1), 6, 15)
        self.assertEqual(memory_nightly.window(h.preferences, first), ("2026-11-01", True))
        self.assertEqual(memory_nightly.window(h.preferences, second), ("2026-11-01", True))
        self.assertEqual(memory_nightly.observe(h, first, idle=True)["day"], "2026-11-01")
        self.assertIsNone(memory_nightly.observe(h, second, idle=True))

        # On the spring transition, 01:59 advances to 03:00. A window
        # ending at 04:00 has a legitimate idle slot after that jump.
        h.preferences["heartbeat"].update(memory_tidy_start="02:00", memory_tidy_end="04:00")
        self.assertFalse(memory_nightly.window(h.preferences, utc((3, 8), 6, 59))[1])
        self.assertEqual(memory_nightly.window(h.preferences, utc((3, 8), 7)), ("2026-03-08", True))
        self.assertFalse(memory_nightly.window(h.preferences, utc((3, 8), 8))[1])

    def test_guards_and_restore_conflict(self):
        self.host("opencode")
        path = self.work/"documentation"/"MEMORY.md"
        path.parent.mkdir()
        path.write_text(SOURCE)
        (self.work/"AGENTS.md").symlink_to(path)
        r = json.loads(self.cli("tidy", "--json"))
        self.assertEqual(r["changed_files"], 1)
        path.write_text("new owner edit\n")
        with self.assertRaises(memory.MemoryError_):
            memory.restore(self.st, r["archive_ids"][0])
        self.assertEqual(path.read_text(), "new owner edit\n")
        path.write_text(SOURCE + "\nAuthorization: Bearer " + "Qx7" * 25)
        self.assertEqual(json.loads(self.cli("tidy", "--dry-run", "--json"))["changed_files"], 0)

    def test_shared_display_name_does_not_merge_different_topics_types_or_headings(self):
        self.host("claude")
        folder = self.tmp/"claude"/"projects"/memory.slug(str(self.work))/"memory"
        folder.mkdir(parents=True)
        originals = {}
        for ix, (typ, heading, topic) in enumerate([
            ("project", "Design", "Review time"), ("feedback", "Design", "Review time"),
            ("project", "Finance", "Review time"), ("project", "Design", "Statement export"),
        ]):
            path = folder / f"note-{ix}.md"
            note = f"---\nname: Preferences\ntype: {typ}\n---\n# {heading}\n- 2026-11-1{ix} {topic}: owner said {ix}\n"
            path.write_text(note)
            originals[path] = note
        result = json.loads(self.cli("tidy", "--json"))
        self.assertEqual(result["merged"], 0)
        self.assertEqual({path: path.read_text() for path in originals}, originals)

    def test_separate_native_notes_and_index_restore(self):
        self.host("claude")
        folder = self.tmp/"claude"/"projects"/memory.slug(str(self.work))/"memory"
        folder.mkdir(parents=True)
        old, new, index = (folder/name for name in ("old.md", "current.md", "MEMORY.md"))
        first = "---\nname: dinner reminder\n---\n"+OLD+"\n"
        current = "---\nname: dinner reminder\n---\n"+NEW+"\n"
        old.write_text(first)
        new.write_text(current)
        index.write_text("- [old](old.md)\n- [current](CURRENT.md)\n")
        preview = json.loads(self.cli("tidy", "--dry-run", "--json"))
        self.assertEqual((preview["merged"], preview["conflicts"]), (1, 1))
        self.assertEqual(old.read_text(), first)
        result = json.loads(self.cli("tidy", "--json"))
        self.assertEqual(result["guarded"], 0)
        self.assertFalse(old.exists())
        self.assertIn(NEW, new.read_text())
        self.assertIn("2026-11-01 → 2026-11-18", new.read_text())
        self.assertEqual(index.read_text(), "- [current](current.md)\n")
        again = json.loads(self.cli("tidy", "--json"))
        self.assertEqual(again["changed_files"], 0)
        for tid in reversed(result["archive_ids"]):
            self.cli("restore", tid)
        self.assertEqual(old.read_text(), first)
        self.assertEqual(new.read_text(), current)
        self.assertEqual(index.read_text(), "- [old](old.md)\n- [current](CURRENT.md)\n")

    def test_docs_coverage_and_unicode_line_separator(self):
        self.host("codex")
        (self.work/"documentation").mkdir()
        (self.work/"documentation"/"guide.md").write_text("Use the blue folder for test receipts.")
        p = self.work/"documentation"/"MEMORY.md"
        p.write_text("# Memory\nA quote with a\u2028line separator.\n- covered by [guide](documentation/guide.md) — Use the blue folder for test receipts.\n- covered by [guide](documentation/guide.md) — A different statement.\n")
        result = json.loads(self.cli("tidy", "--json"))
        self.assertEqual(result["archived"], 1)
        self.assertIn("A quote with a\u2028line separator.", p.read_text())
        self.assertIn("A different statement.", p.read_text())
    def test_latest_owner_evidence_and_single_timeline_on_retire(self):
        self.host("codex")
        file = self.work/"documentation"/"MEMORY.md"
        file.parent.mkdir()
        file.write_text(SOURCE + "- 2026-11-19 提醒时间：unverified guess\n")
        memory_tidy.run(self.st, "codex", str(self.work))
        self.assertIn(NEW, file.read_text())
        self.assertNotIn("guess", file.read_text())
        next_owner = "- 2026-12-02 提醒时间：主人说「六點半。」"
        file.write_text(file.read_text()+next_owner+"\n")
        memory_tidy.run(self.st, "codex", str(self.work))
        self.assertIn(next_owner, file.read_text())
        self.assertEqual(file.read_text().count("<!-- memory timeline "), 1)
        self.assertIn("2026-11-01 → 2026-12-02", file.read_text())

    def set_credential_inventory(self, *paths):
        # Synthetic account config; registered files deliberately contain only ordinary Markdown text.
        from agentj import preferences
        raw, _ = preferences.read()
        preferences.path().write_text(preferences.edit(raw, "api_keys.extra_env_files", list(map(str, paths))))

    def test_registered_notes_alias_index_and_coverage_are_never_opened(self):
        self.host("codex")
        folder = self.tmp/"codex"/"memories"
        folder.mkdir(parents=True)
        doc = self.work/"documentation"
        doc.mkdir()
        listed = folder/"old.md"
        listed.write_text(SOURCE)
        alias = doc/"guide.md"
        os.link(listed, alias)
        index = folder/"MEMORY.md"
        index.write_text("- [old](old.md)\n- [x] finished one-off\n")
        project = doc/"MEMORY.md"
        project.write_text("- covered by [guide](documentation/guide.md) — "+OLD+"\n")
        link = self.tmp/"reference.md"
        link.symlink_to(listed)
        self.set_credential_inventory(link, index)
        never_open = {str(p) for p in (listed, alias, index)}
        real_open = os.open
        def guard(path, *args, **kw):
            self.assertNotIn(os.fspath(path), never_open, "protected Markdown was opened")
            return real_open(path, *args, **kw)
        with patch("os.open", side_effect=guard):
            for dry in (True, False):
                result = memory_tidy.run(self.st, "codex", str(self.work), dry_run=dry)
                self.assertEqual(result["changed_files"], 0)
            self.assertEqual(memory.trash(self.st), [])
        self.assertIn(OLD, listed.read_text())

    def test_registering_native_index_refuses_restore_before_recreating_note(self):
        self.host("codex")
        folder = self.tmp/"codex"/"memories"
        folder.mkdir(parents=True)
        note, index = folder/"old.md", folder/"MEMORY.md"
        note.write_text("ordinary example")
        index.write_text("- [old](old.md)\n")
        removed = memory.remove(self.st, "codex", str(self.work), "codex.memories", note.name,
                                memory.sha(note.read_bytes()), "file", retain=True)
        self.set_credential_inventory(index)
        with patch.object(memory, "read_file", side_effect=AssertionError("protected index was read")):
            with self.assertRaisesRegex(memory.MemoryError_, "credential_path"):
                memory.restore(self.st, removed["id"])
        self.assertFalse(note.exists())
        self.assertEqual(index.read_text(), "")

    def test_changed_inventory_blocks_restore_and_invalid_inventory_blocks_read(self):
        self.host("codex")
        p = self.work/"documentation"/"MEMORY.md"
        p.parent.mkdir()
        p.write_text(SOURCE)
        [tid] = memory_tidy.run(self.st, "codex", str(self.work))["archive_ids"]
        before = p.read_bytes()
        self.set_credential_inventory(p)
        with patch.object(memory, "read_file", side_effect=AssertionError("protected restore read")):
            with self.assertRaisesRegex(memory.MemoryError_, "credential_path"):
                memory.restore(self.st, tid)
        self.assertEqual(memory.trash(self.st), [])
        self.assertEqual(p.read_bytes(), before)
        self.set_credential_inventory()
        from agentj import preferences
        with patch.object(preferences, "read", side_effect=OSError("unavailable")):
            with self.assertRaisesRegex(memory.MemoryError_, "credential_inventory_unavailable"):
                memory.read_file(str(p))
            self.assertEqual(memory_tidy.run(self.st, "codex", str(self.work))["changed_files"], 0)
        memory.restore(self.st, tid)
        self.assertEqual(p.read_text(), SOURCE)

    def test_archive_requires_private_owned_directory_without_links(self):
        self.host("codex")
        source = self.work/"note.md"
        source.write_text("synthetic")
        archive = self.st.root/"memory_trash"
        other = self.tmp/"unrelated"
        other.mkdir(mode=0o750)
        archive.symlink_to(other, target_is_directory=True)
        with self.assertRaises(memory.MemoryError_):
            memory._put_trash(self.st, {"path": str(source)})
        self.assertEqual(memory.trash(self.st), [])
        self.assertEqual(memory.purge_trash(self.st), 0)
        self.assertEqual(stat.S_IMODE(other.stat().st_mode), 0o750)
        self.assertEqual(list(other.iterdir()), [])
        archive.unlink()
        archive.mkdir(mode=0o755)
        with self.assertRaises(memory.MemoryError_):
            memory._put_trash(self.st, {"path": str(source)})
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o755)

    def test_archive_links_and_mismatched_record_ids_cannot_be_used(self):
        self.host("codex")
        source = self.work/"note.md"
        source.write_text("synthetic")
        tid = memory._put_trash(self.st, {"path": str(source)})
        entry = self.st.root/"memory_trash"/(tid + ".json")
        other = self.tmp/"unrelated"
        entry.rename(other)
        for link in ("symbolic", "hard"):
            if link == "symbolic":
                entry.symlink_to(other)
            else:
                os.link(other, entry)
            self.assertEqual(memory.trash(self.st), [])
            with self.assertRaises(memory.MemoryError_):
                memory.restore(self.st, tid)
            self.assertTrue(other.is_file())
            entry.unlink()
        other.rename(entry)
        info = json.loads(entry.read_text())
        info["id"] = "../unrelated"
        entry.write_text(json.dumps(info))
        self.assertEqual(memory.trash(self.st), [])
        with self.assertRaises(memory.MemoryError_):
            memory.restore(self.st, tid)
        self.assertTrue(entry.is_file())

    def test_memory_source_fd_must_match_before_first_read(self):
        self.host("codex")
        a, b = self.work/"a.md", self.work/"b.md"
        a.write_text("synthetic one")
        b.write_text("synthetic two")
        real_open = os.open
        def different_open(path, flags, *args, **kwargs):
            return real_open(b if path == a else path, flags, *args, **kwargs)
        with patch.object(os, "open", side_effect=different_open), patch.object(os, "read", side_effect=AssertionError("unexpected read")):
            with self.assertRaisesRegex(memory.MemoryError_, "changed"):
                memory.read_file(a)
        os.link(b, self.work/"alias.md")
        with patch.object(os, "read", side_effect=AssertionError("unexpected read")):
            with self.assertRaises(memory.MemoryError_):
                memory.read_file(b)
        fifo = self.work/"pipe.md"
        os.mkfifo(fifo)
        def nonblocking_open(path, flags, *args, **kwargs):
            if path == fifo:
                self.assertTrue(flags & os.O_NONBLOCK)
            return real_open(path, flags, *args, **kwargs)
        with patch.object(os, "open", side_effect=nonblocking_open):
            with self.assertRaises(memory.MemoryError_):
                memory.read_file(fifo)

    def test_closed_nightly_record_has_no_read_or_write_side_effects(self):
        h = self.host("codex")
        p = self.st.root/memory_nightly.RECORD
        private = self.tmp/"synthetic-private"
        private.write_text('{"day":"2026-11-20","status":"done","merged":1}')
        original = private.read_bytes()
        p.symlink_to(private)
        self.assertIsNone(memory_nightly.observe(h, self.now, idle=True))
        self.assertTrue(p.is_symlink())
        self.assertEqual(private.read_bytes(), original)
        p.unlink()
        self.set_credential_inventory(p)
        with patch.object(os, "read", side_effect=AssertionError("unexpected read")):
            self.assertIsNone(memory_nightly.observe(h, self.now, idle=True))
        self.assertFalse(p.exists())
        p.write_bytes(original)
        p.chmod(0o600)
        self.assertEqual(memory_nightly.morning_line(self.st, "en", self.now), "")
        self.assertIsNone(memory_nightly.observe(h, self.now, idle=True))
        self.assertEqual(p.read_bytes(), original)

    def test_nightly_metadata_rejects_bad_dates_counts_unknown_fields_and_oversize(self):
        h = self.host("codex")
        p = self.st.root/memory_nightly.RECORD
        ordinary = {"day": "2026-11-20", "status": "done", "merged": 1}
        for change in ({"day":"2026-02-30"}, {"opened":"marker"}, {"merged":"marker"},
                       {"guarded":True}, {"status":"marker"}, {"reason":"marker"},
                       {"conflicts":-1}, {"index_fixed":10**15}, {"partial":{"merged":"marker"}},
                       {"unlisted":"marker"}, {"unlisted":"A"*5000}):
            raw = json.dumps({**ordinary, **change}).encode()
            p.write_bytes(raw)
            p.chmod(0o600)
            with patch.object(self.st, "log") as log:
                self.assertEqual(memory_nightly.read(self.st), {})
                self.assertEqual(memory_nightly.morning_line(self.st, "zh", self.now), "")
                self.assertIsNone(memory_nightly.observe(h, self.now, idle=True))
                log.assert_not_called()
            self.assertEqual(p.read_bytes(), raw)

    def test_keeps_ambiguous_decisions_and_unrelated_scope(self):
        self.host("opencode")
        file = self.work/"AGENTS.md"
        file.write_text("# Process\n- [x] Always require this approval\n# Memory\n"
                        "- 2026-01-22 Display: red\n- 2026-01-23 Display: green\n")
        self.assertEqual(memory_tidy.run(self.st, "opencode", str(self.work))["changed_files"], 0)


if __name__ == "__main__":
    unittest.main()
