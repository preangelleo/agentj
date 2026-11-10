"""#0304 (Chairman 11-09: 「那我这些语音消息的原文件，它保留多久呢？……最多也只保存二十四小时足够。」): voice originals in
`<agent folder>/.agentj/inbox/<day>/` expire after `inbox.voice_retention_hours` (default 24; 0 = never), reaped by serve's
periodic sweep through the O_NOFOLLOW dir-fd chain. Other attachments keep the 30-day + quota rule."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state)
import asyncio
import contextlib
import hashlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from _testtmp import TEST_TMP  # noqa: E402
from agentj import diagnostic_log, inbox, preferences, serve, telegram, uploads  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p33 import b64, bid, wav  # noqa: E402
from test_p44 import PrefSet as _P44PrefSet  # noqa: E402

KEY = "inbox.voice_retention_hours"
H = 3600


def _tree(root: pathlib.Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _stamp(t: float) -> tuple[str, str]:
    lt = time.localtime(t)
    return time.strftime("%Y-%m-%d", lt), time.strftime("%H%M%S", lt)


class _Env(unittest.TestCase):
    """Isolated HOME / XDG_* / state; a stub `systemctl` first on PATH (the real user manager is never asked)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p0304-v-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.d = pathlib.Path(self.tmp.name)
        stub = self.d / "bin"
        stub.mkdir()
        (stub / "systemctl").write_text("#!/bin/sh\nexit 0\n")
        (stub / "systemctl").chmod(0o755)
        env = {"HOME": str(self.d / "home"), "XDG_CONFIG_HOME": str(self.d / "cfg"), "XDG_STATE_HOME": str(self.d / "xs"),
               "XDG_DATA_HOME": str(self.d / "xd"), "XDG_CACHE_HOME": str(self.d / "xc"),
               "AGENTJ_STATE_DIR": str(self.d / "state"), "PATH": f"{stub}:/usr/bin:/bin"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        self.work = self.d / "work"
        self.work.mkdir()
        self.ib = self.work / ".agentj" / "inbox"
        self.now = time.time()
        self.events = []

    def log(self, ev, **kw):
        self.events.append((ev, kw))

    def put(self, age_h: float, slug: str, ext=".wav", data=b"RIFFxxxxWAVE", mtime=None) -> pathlib.Path:
        """A file as `place` names it, placed age_h hours before self.now."""
        day, hms = _stamp(self.now - age_h * H)
        p = self.ib / day / f"{hms}-{os.urandom(3).hex()}-{slug}{ext}"
        with contextlib.suppress(FileExistsError):
            with inbox.dot_dir(str(self.work)):
                pass
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.write_bytes(data)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def reap(self, hours=24, keep=None, now=None):
        return inbox.reap_voice(str(self.work), self.st, hours, keep, now=self.now if now is None else now, log=self.log)


class Setting(_Env):
    def test_default_24_and_allowed_values(self):
        m = preferences.SCHEMA[KEY]
        self.assertEqual((m["type"], m["default"], m["tier"]), ("integer", 24, "user"))
        self.assertEqual(m["enum"], [0, 1, 6, 24, 72, 168, 720])
        self.assertEqual(preferences.get(preferences.defaults(), KEY), 24, "a user who never set it gets 24 h")
        self.assertEqual(inbox.voice_hours(preferences.defaults()), 24)
        for bad in ({}, None, {"inbox": {KEY.split(".")[1]: 5}}, {"inbox": {KEY.split(".")[1]: True}}):
            self.assertEqual(inbox.voice_hours(bad), 24, bad)
        self.assertEqual(inbox.voice_hours({"inbox": {"voice_retention_hours": 0}}), 0)
        self.assertEqual(preferences.resolve_key("语音保留时长"), KEY)

    def test_config_accepts_listed_values_only(self):
        for v in (0, 1, 6, 24, 72, 168, 720):
            self.assertEqual(preferences.get(preferences.validate({"inbox": {"voice_retention_hours": v}}), KEY), v)
        for bad in (2, 25, -1, 24.0, "24", True):
            with self.subTest(bad=bad), self.assertRaises(preferences.ConfigError) as cm:
                preferences.validate({"inbox": {"voice_retention_hours": bad}})
            self.assertEqual(cm.exception.key, KEY)
        with self.assertRaises(preferences.ConfigError) as cm:
            preferences.validate({"inbox": {"voice_retention_hours": 5}})
        self.assertIn("0, 1, 6, 24, 72, 168, 720", cm.exception.detail, "an integer enum is listed, not a TypeError")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(preferences.command(["keys", "--search", "voice retention", "--json"]), 0)
        self.assertIn(KEY, out.getvalue(), "agentj config keys lists it")
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(preferences.command(["set", KEY, "6"]), 0)
            self.assertEqual(preferences.command(["set", KEY, "7"]), 1)
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), 6)


class Reaper(_Env):
    def test_legacy_names_expire_after_24h_other_files_untouched(self):
        old = [self.put(30, "语音-101010"), self.put(30, "语音-101010-2", ".webm"), self.put(26, "voice", ".ogg"),
               self.put(48, "Voice-090909", ".m4a")]
        young = self.put(2, "语音-121212")
        keep = [self.put(30, "photo", ".jpg"), self.put(30, "song", ".mp3"), self.put(30, "语音-1-transcript", ".txt"),
                self.put(30, "语音-video", ".mp4"), self.put(30, "notes", ".pdf")]
        gone = self.reap(24)
        self.assertEqual(sorted(x[1] for x in gone), sorted(p.name for p in old))
        for p in old:
            self.assertFalse(p.exists(), p.name)
        for p in [young, *keep]:
            self.assertTrue(p.exists(), p.name)
        self.assertEqual(inbox.retain(str(self.work), now=self.now), [], "the 30-day rule is unchanged")

    def test_setting_values_including_0_never(self):
        a, b, c = self.put(0.5, "语音-1"), self.put(2, "语音-2"), self.put(30, "语音-3")
        self.assertEqual(self.reap(0), [], "0 = never auto-delete")
        self.assertTrue(a.exists() and b.exists() and c.exists())
        self.assertEqual([x[1] for x in self.reap(72)], [])
        self.assertEqual(sorted(x[1] for x in self.reap(1)), sorted([b.name, c.name]))
        self.assertTrue(a.exists())
        self.assertEqual(self.reap(5), [], "a value outside the list deletes nothing")

    def test_age_comes_from_the_name_not_mtime(self):
        fresh_name_old_mtime = self.put(2, "语音-a", mtime=self.now - 40 * 86400)
        old_name_fresh_mtime = self.put(30, "语音-b", mtime=self.now)
        self.reap(24)
        self.assertTrue(fresh_name_old_mtime.exists(), "an Agent-touched mtime never ages a file")
        self.assertFalse(old_name_fresh_mtime.exists(), "nor does a fresh mtime keep an old original")

    def test_ledger_provenance_and_legacy_only_before_upgrade(self):
        self.reap(24, now=self.now - 10 * 86400)                 # the upgrade: first sweep 10 days ago
        since = json.loads((self.st.root / inbox.VOICE_LEDGER).read_text())["since"]
        self.assertAlmostEqual(since, self.now - 10 * 86400, delta=1)
        src = self.d / "take.webm"
        src.write_bytes(b"\x1aE\xdf\xa3" + b"x" * 50)
        noted = inbox.place(str(self.work), str(src), "meeting", "audio/webm", bid(), now=self.now - 3 * 86400)
        inbox.note_voice(self.st, noted, "telegram")
        named_late = self.put(3 * 24, "语音-999999")              # a legacy-looking name placed after the upgrade, no row
        self.assertEqual([x[1] for x in self.reap(24)], [os.path.basename(noted)])
        self.assertTrue(named_late.exists(), "after the upgrade a name alone is not provenance")
        self.assertEqual(json.loads((self.st.root / inbox.VOICE_LEDGER).read_text())["files"], {}, "row compacted")
        stat = os.stat(self.st.root / inbox.VOICE_LEDGER)
        self.assertEqual(stat.st_mode & 0o777, 0o600, "the ledger is private")

    def test_staged_or_claimed_file_is_kept(self):
        p = self.put(30, "语音-s")
        abs_path = os.path.join(os.path.realpath(self.work), ".agentj", "inbox", p.parent.name, p.name)
        self.assertEqual(self.reap(24, keep={abs_path}), [])
        self.assertTrue(p.exists())
        self.assertEqual(len(self.reap(24, keep=set())), 1)

    def test_links_are_never_followed(self):
        victim = self.d / "victim"
        day, hms = _stamp(self.now - 30 * H)
        (victim / day).mkdir(parents=True)
        (victim / day / f"{hms}-abcdef-语音-1.wav").write_bytes(b"precious")
        (victim / "precious.wav").write_bytes(b"precious")
        before = _tree(victim)
        for where in ("dot", "inbox", "day"):
            w = self.d / f"w-{where}"
            w.mkdir()
            if where == "dot":
                (w / ".agentj").symlink_to(victim)
            elif where == "inbox":
                (w / ".agentj").mkdir(mode=0o700)
                (w / ".agentj" / "inbox").symlink_to(victim)
            else:
                (w / ".agentj" / "inbox").mkdir(parents=True, mode=0o700)
                (w / ".agentj" / "inbox" / day).symlink_to(victim / day)
            self.assertEqual(inbox.reap_voice(str(w), self.st, 1, now=self.now, log=self.log), [], where)
            self.assertEqual(_tree(victim), before, where)
        self.assertIn(("inbox_unsafe", {"op": "voice"}), self.events)
        link = self.put(30, "语音-real")                           # a real day folder holding a link entry …
        planted = link.parent / f"{_stamp(self.now - 30 * H)[1]}-fedcba-语音-link.wav"
        planted.symlink_to(victim / "precious.wav")
        gone = self.reap(1)
        self.assertEqual([x[1] for x in gone], [link.name])
        self.assertTrue(planted.is_symlink(), "a link entry is not a voice original: skipped")
        self.assertEqual(_tree(victim), before)

    def test_audit_events_basenames_only(self):
        p = self.put(30, "语音-audit", data=b"RIFF" + b"y" * 96)
        gone = self.reap(24)
        self.assertEqual(gone, [(p.parent.name, p.name, 100)])
        sha8 = hashlib.sha256(p.name.encode()).hexdigest()[:8]
        self.assertEqual(self.events, [("inbox_voice_reaped", {"day": p.parent.name, "name_hash": sha8, "bytes": 100})])
        rec = diagnostic_log.record("inbox_voice_reaped", self.events[0][1], State.LOG_FIELDS)
        self.assertEqual((rec.get("day"), rec.get("bytes"), rec.get("name_hash")), (p.parent.name, 100, sha8),
                         "host.log keeps day, bytes and the name as sha256 8 hex")
        self.assertNotIn("/", json.dumps(rec, ensure_ascii=False))

    def test_audit_never_logs_the_free_text_name(self):
        p = self.put(30, "语音-Q3 salary talk with Alice", data=b"RIFF" + b"y" * 12)   # a user-chosen tail can carry words
        self.assertEqual(len(self.reap(24)), 1)
        dumped = json.dumps(self.events, ensure_ascii=False)
        self.assertNotIn("salary", dumped)
        self.assertNotIn(p.name, dumped)


class CorruptLedger(_Env):
    """#0304 sec B2: a ledger that exists but cannot be read must not reset `since` to now (which would let the legacy-name
    rule cover files placed after the upgrade). Fail closed: nothing deleted that run, `inbox_unsafe op=voice_ledger`."""

    def ledger(self) -> pathlib.Path:
        return self.st.root / inbox.VOICE_LEDGER

    def test_unparsable_ledger_deletes_nothing_and_is_not_rewritten(self):
        for raw in (b"{not json", b'{"since": "x", "files": {}}', b'[1, 2]', b'{"since": 1.0}', b"\xff\xfe"):
            with self.subTest(raw=raw):
                self.events.clear()
                self.ledger().write_bytes(raw)
                old = [self.put(30, "语音-101010"), self.put(48, "voice", ".ogg")]
                self.assertEqual(self.reap(24), [])
                for f in old:
                    self.assertTrue(f.exists(), f.name)
                self.assertEqual(self.ledger().read_bytes(), raw, "since is never reset to now")
                self.assertEqual(self.events, [("inbox_unsafe", {"op": "voice_ledger"})])

    def test_unreadable_ledger_deletes_nothing(self):
        self.ledger().mkdir()                                     # exists, cannot be read as a file
        old = self.put(30, "语音-101010")
        self.assertEqual(self.reap(24), [])
        self.assertTrue(old.exists())
        self.assertIn(("inbox_unsafe", {"op": "voice_ledger"}), self.events)
        noted = self.put(30, "meeting", ".webm")
        inbox.note_voice(self.st, str(noted), "telegram")         # never raises; nothing written over it
        self.assertTrue(self.ledger().is_dir())

    def test_note_on_a_corrupt_ledger_repairs_without_the_legacy_rule(self):
        self.ledger().write_bytes(b"{garbage")
        legacy = self.put(30, "语音-101010")
        noted = self.put(30, "meeting", ".webm")
        inbox.note_voice(self.st, str(noted), "telegram", now=self.now)
        d = json.loads(self.ledger().read_text())
        self.assertEqual(d["since"], 0, "repaired ledger: no legacy window (the upgrade time is unknown)")
        self.assertEqual(list(d["files"]), [f"{noted.parent.name}/{noted.name}"])
        self.assertEqual([x[1] for x in self.reap(24)], [noted.name], "a noted original still expires")
        self.assertTrue(legacy.exists(), "a name alone is never provenance after a repair")

    def test_absent_ledger_still_starts_the_legacy_window(self):
        self.assertFalse(self.ledger().exists())
        old = self.put(30, "语音-101010")
        self.assertEqual([x[1] for x in self.reap(24)], [old.name])
        self.assertAlmostEqual(json.loads(self.ledger().read_text())["since"], self.now, delta=1)
        self.assertNotIn(("inbox_unsafe", {"op": "voice_ledger"}), self.events)


class Provenance(_Env):
    def _upload(self, up, origin, mime="audio/wav", data=None):
        data = data or wav(0.5)
        b = bid()
        m = {"bid": b, "purpose": "att", "name": "语音-101010" if origin == "recording" else "memo", "mime": mime,
             "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "origin": origin, "secs": 1}
        self.assertEqual(up.open("D" * 16, m)[0]["t"], "blob_ack")
        for o in range(0, len(data), uploads.CHUNK):
            up.chunk("D" * 16, {"bid": b, "o": o, "d": b64(data[o:o + uploads.CHUNK])})
        out, _ = up.end("D" * 16, b)
        self.assertTrue(out[-1]["ok"], out)
        return up.blobs[("D" * 16, b)]

    def test_phone_recordings_are_noted_picked_files_are_not(self):
        up = uploads.Uploads(self.st, str(self.work))
        rec, picked = self._upload(up, "recording"), self._upload(up, "file")
        rows = json.loads((self.st.root / inbox.VOICE_LEDGER).read_text())["files"]
        key = lambda b: f"{os.path.basename(os.path.dirname(b.path))}/{os.path.basename(b.path)}"  # noqa: E731
        self.assertEqual(rows, {key(rec): "phone"})
        self.assertNotIn(key(picked), rows)
        self.assertEqual(self.reap(1, keep=up.staged_paths(), now=self.now + 2 * H), [], "staged: kept")
        voice = rec.voice
        up.commit([rec, picked])
        self.assertFalse(os.path.exists(voice), "the private <bid>.voice copy goes on commit")
        gone = self.reap(1, keep=up.staged_paths(), now=self.now + 2 * H)
        self.assertEqual([x[1] for x in gone], [os.path.basename(rec.path)])
        self.assertTrue(os.path.exists(picked.path), "a picked audio file keeps the 30-day rule")

    def test_telegram_voice_notes_are_marked(self):
        items = telegram.media_of({"voice": {"file_id": "x", "duration": 3}, "audio": {"file_id": "y", "mime_type": "audio/mpeg",
                                                                                      "file_name": "song.mp3"}})
        self.assertEqual([(x["name"], x["tg_voice"]) for x in items], [("voice", True), ("song.mp3", False)])


class Serve(_Env):
    def test_sweep_loop_reaps_with_the_configured_hours(self):
        host = serve.Host(self.st, events="quiet", read_stdin=False)
        host.agent_cfg = {"dir": str(self.work)}
        host.preferences = preferences.validate({"inbox": {"voice_retention_hours": 6}})
        calls = []

        def fake(workdir, st, hours, keep, log=None):
            calls.append((workdir, hours, keep))
            return [("2026-01-01", "x", 5)]

        async def stop(_):
            raise asyncio.CancelledError

        with mock.patch.object(inbox, "reap_voice", fake), mock.patch.object(inbox, "retain", lambda *a, **k: []), \
                mock.patch.object(serve.asyncio, "sleep", stop), mock.patch.object(host.st, "log", self.log):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(host.sweep_loop())
        self.assertEqual(calls, [(str(self.work), 6, set())])
        self.assertIn(("inbox_voice_reap", {"count": 1, "bytes": 5}), self.events)
        self.assertLessEqual(serve.VOICE_EVERY, 3600, "at least hourly")

    def test_cli_status_line_names_the_retention(self):
        from agentj import cli
        self.st.set_agent_config("claude", str(self.work))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            cli.cmd_inbox(SimpleNamespace(mode="list", json=False))
        self.assertIn("语音原始录音：保留 24 小时", out.getvalue())
        self.assertIn("voice originals: kept 24 h", out.getvalue())
        with mock.patch("sys.stdout", io.StringIO()):
            preferences.command(["set", KEY, "0"])
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            cli.cmd_inbox(SimpleNamespace(mode="list", json=True))
        self.assertEqual(json.loads(out.getvalue())["voice_retention_hours"], 0)


class PrefSetVoice(_P44PrefSet):
    async def test_pref_set_accepts_listed_values_refuses_others(self):
        self.assertIn(KEY, serve.PREF_SET_KEYS)
        msg = self.host.preferences_msg()
        self.assertIn(KEY, msg["host"]["settable"])
        self.assertEqual(msg["value"]["inbox"]["voice_retention_hours"], 24, "the phone reads value.inbox.voice_retention_hours")
        for v in (0, 1, 6, 72, 168, 720, 24):
            res = await self.pref(KEY, v)
            self.assertEqual(res, [{"t": "pref_res", "r": "r1", "ok": True, "key": KEY}], v)
            self.assertEqual(preferences.get(preferences.effective(self.st), KEY), v)
        for bad in (2, 25, -1, 24.0, "24", True, None):
            res = await self.pref(KEY, bad)
            self.assertEqual(res[0]["problem"], "bad_value", bad)
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), 24)


del _P44PrefSet

if __name__ == "__main__":
    unittest.main()
