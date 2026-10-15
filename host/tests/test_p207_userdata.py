"""P207 §0: the owner's data survives upgrades — userdata.snapshot / heal / version_snapshot and the upgrade hooks."""
import datetime as dt
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _hermetic  # noqa: E402,F401
from agentj import update, userdata  # noqa: E402
from agentj.friends import FriendStore  # noqa: E402
from agentj.state import State  # noqa: E402

A = "AJ-7KQ2-M9XA-4TPE-W3HC"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ajud-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.st = State(self.tmp / "state")
        self.st.root.mkdir(mode=0o700)
        self.fr = FriendStore(self.st)
        self.fr.add_friend(A, "xA", "pkA", "mboxA", {"name": "光的使者"})
        self.fr.set_setting("owner", "王小明")
        self.fr.set_setting("intro", "做 AI 产品")
        (self.st.root / "devices.json").write_text(json.dumps({"d1": {"name": "王小明的 iPhone Air", "owner_name": "王小明的 iPhone Air"}}))
        os.chmod(self.st.root / "devices.json", 0o600)

    def t(self, sec):
        return dt.datetime(2026, 10, 13, 10, 0, sec, tzinfo=dt.timezone.utc)


class Snapshot(Base):
    def test_copies_user_files_private(self):
        snap = userdata.snapshot(self.st, "pre-upgrade-v0.20.1a1", now=self.t(0))
        self.assertIsNotNone(snap)
        self.assertEqual(stat.S_IMODE(snap.stat().st_mode), 0o700)
        for rel in ("peer/friends.json", "peer/settings.json", "devices.json", "peer/peer_ed25519.key"):
            if (self.st.root / rel).exists():
                self.assertEqual((snap / rel).read_bytes(), (self.st.root / rel).read_bytes(), rel)
                self.assertEqual(stat.S_IMODE((snap / rel).stat().st_mode), 0o600, rel)
        m = json.loads((snap / "manifest.json").read_text())
        self.assertIn("peer/friends.json", m["files"])

    def test_keeps_newest_ten(self):
        for i in range(13):
            userdata.snapshot(self.st, "s", now=self.t(i))
        snaps = userdata.snapshots(self.st)
        self.assertEqual(len(snaps), userdata.KEEP)
        self.assertTrue(snaps[0].name.startswith("20261013T100012Z"))

    def test_nothing_to_keep_on_a_new_state(self):
        st = State(self.tmp / "fresh")
        st.root.mkdir(mode=0o700)
        with mock.patch.object(userdata, "_pref_path", lambda st: None):
            self.assertIsNone(userdata.snapshot(st, "x"))

    def test_version_snapshot_once_per_version(self):
        self.assertIsNotNone(userdata.version_snapshot(self.st, "0.20.2a1"))
        self.assertIsNone(userdata.version_snapshot(self.st, "0.20.2a1"))
        self.assertIsNotNone(userdata.version_snapshot(self.st, "0.20.3a1"))

    def test_isolated_state_never_reads_real_preferences(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            os.environ.pop("XDG_CONFIG_HOME")
            self.assertIsNone(userdata._pref_path(self.st))


class Heal(Base):
    def test_missing_friends_and_card_come_back(self):
        userdata.snapshot(self.st, "pre-upgrade", now=self.t(0))
        (self.st.root / "peer/friends.json").unlink()
        (self.st.root / "peer/settings.json").unlink()
        got = userdata.heal(self.st)
        self.assertEqual({g["file"] for g in got}, {"peer/friends.json", "peer/settings.json"})
        self.assertIn(A, FriendStore(self.st).friends())
        self.assertEqual(FriendStore(self.st).owner, "王小明")
        self.assertIn('"userdata_restored"', self.st.log_path.read_text())
        self.assertIn("好友名单", userdata.restored_text(got))
        self.assertIn("friends", userdata.restored_text(got, "en"))

    def test_corrupt_file_restored_from_newest_good_copy(self):
        userdata.snapshot(self.st, "a", now=self.t(0))
        (self.st.root / "peer/friends.json").write_text("{not json")
        userdata.snapshot(self.st, "b", now=self.t(1))          # newest copy is the corrupt one → skipped
        got = userdata.heal(self.st)
        self.assertEqual(got[0]["file"], "peer/friends.json")
        self.assertTrue(got[0]["from"].startswith("20261013T100000Z"))
        self.assertIn(A, FriendStore(self.st).friends())

    def test_cleared_profile_is_never_resurrected(self):
        self.fr.set_profile("my bio") if hasattr(self.fr, "set_profile") else (self.st.root / "peer/profile.md").write_text("my bio")
        userdata.snapshot(self.st, "a", now=self.t(0))
        (self.st.root / "peer/profile.md").write_text("")
        userdata.snapshot(self.st, "b", now=self.t(1))
        self.assertEqual(userdata.heal(self.st), [])
        self.assertEqual((self.st.root / "peer/profile.md").read_text(), "")
        (self.st.root / "peer/profile.md").unlink()             # gone, but the newest copy was empty: still nothing comes back
        self.assertEqual(userdata.heal(self.st), [])

    def test_remotes_and_agent_id_keys_are_never_auto_restored(self):
        userdata.snapshot(self.st, "a", now=self.t(0))
        (self.st.root / "devices.json").unlink()                 # e.g. the owner logged every remote out by hand
        for k in ("peer/peer_ed25519.key", "peer/peer_x25519.key"):
            (self.st.root / k).unlink(missing_ok=True)
        self.assertEqual(userdata.heal(self.st), [])
        self.assertFalse((self.st.root / "devices.json").exists())

    def test_long_reason_same_second_never_loops(self):
        r = "x" * 60
        a = userdata.snapshot(self.st, r, now=self.t(5))
        b = userdata.snapshot(self.st, r, now=self.t(5))
        self.assertNotEqual(a, b)

    def test_owner_emptied_list_is_never_resurrected(self):
        userdata.snapshot(self.st, "a", now=self.t(0))
        self.fr.remove_friend(A) if hasattr(self.fr, "remove_friend") else self.fr._write("friends.json", {})
        self.fr._write("friends.json", {})
        self.assertEqual(userdata.heal(self.st), [])
        self.assertEqual(FriendStore(self.st).friends(), {})

    def test_no_snapshot_no_restore(self):
        (self.st.root / "peer/friends.json").unlink()
        self.assertEqual(userdata.heal(self.st), [])


class UpgradeHook(Base):
    def test_apply_snapshots_before_install(self):
        seen = []

        def run(cmd, **kw):
            seen.append([p.name for p in userdata.snapshots(self.st)])
            return mock.Mock(returncode=1)          # fail the install → rollback path; the snapshot must already exist

        res = update.apply(self.st, "9.9.9", check_fn=lambda: {"status": "newer", "latest": "9.9.9"}, run=run,
                           svc_on=False, plan_fn=lambda st, info, t: [["true"]], rollback_fn=lambda st: None,
                           prefix=str(self.tmp))
        self.assertEqual(res["reason"], "uv_failed")
        self.assertTrue(seen and any("pre-upgrade" in n for n in seen[0]), seen)


if __name__ == "__main__":
    unittest.main()
