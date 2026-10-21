"""P424 #64 — a file link is no longer 「不在工作目录」 because the Agent's shell `cd`-ed into a sub-project.

Leo (10-16): Jarvis links `~/work/.agentj/outbox/…` pictures; after a `cd` into a sub-project the scan saw only that
sub-folder (repro: workdir ~/work → offered, ~/work/app → why=outside). Now, toward the owner, the allowed folders
are the configured working root ∪ the session's current folder (`Media.current`), a relative path is read from the current
folder first, and a `cd` never narrows the scan. Reverse: a file outside both is still `outside`; symlink escapes, the state
dir, `/`, the home folder (and above), a current folder reached through a link or holding a credential folder never widen it;
toward anyone else (owner=False: contacts / friends) the current folder is not used at all.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

AJ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AJ))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import media, serve  # noqa: E402

from test_l1 import _state  # noqa: E402
from test_p33 import png  # noqa: E402


class CwdUnion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(os.path.realpath(self.tmp.name))
        self.st = _state(str(base))
        self.root = base / "work"                       # the configured working root (~/work)
        self.sub = self.root / "app"              # where the Agent's shell went
        self.out = self.root / ".agentj" / "outbox"
        for d in (self.sub / "docs", self.out):
            d.mkdir(parents=True)
        self.elsewhere = base / "elsewhere"
        self.elsewhere.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        (self.out / "a.png").write_bytes(png())
        self.plan = png()
        (self.sub / "docs" / "plan.png").write_bytes(self.plan)
        (self.elsewhere / "x.png").write_bytes(png())
        self.cwd = None
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def media(self, workdir):
        return media.Media(self.st, str(workdir), cwd=lambda: self.cwd)

    def whys(self, m, text):
        items, skips = m.scan(text, 1)
        return [i["name"] for i in items], [s["why"] for s in skips]

    # ---------------------------------------------------------------- positive
    def test_cd_into_subproject_never_narrows_the_configured_root(self):
        self.cwd = str(self.sub)
        got = self.whys(self.media(self.root), f"![]({self.out / 'a.png'}) ![]({self.sub / 'docs' / 'plan.png'})")
        self.assertEqual(got, (["a.png", "plan.png"], []))

    def test_host_given_the_subfolder_still_offers_files_of_the_current_root(self):
        # the reported shape: the scan's folder is the sub-project while the session works in ~/work
        self.cwd = str(self.root)
        m = self.media(self.sub)
        self.assertEqual(self.whys(m, f"![]({self.out / 'a.png'})"), (["a.png"], []))
        self.cwd = None                                       # nothing known about the session: the old floor
        self.assertEqual(self.whys(m, f"![]({self.out / 'a.png'})"), ([], ["outside"]))

    def test_relative_path_is_read_from_the_current_folder_first(self):
        self.cwd = str(self.sub)
        m = self.media(self.root)
        items, skips = m.scan("![](docs/plan.png)", 1)
        self.assertEqual(([i["name"] for i in items], skips), (["plan.png"], []))
        rec = m.get(items[0]["mid"])
        self.assertEqual(os.path.join(rec["root"], *rec["parts"]), str(self.sub / "docs" / "plan.png"))
        self.assertEqual(b"".join(b for _, b, _ in m.window(items[0]["mid"], 0)), self.plan, "the phone gets the bytes")
        self.cwd = str(self.root / "nowhere")                  # a vanished current folder: relative stays on the root
        self.assertEqual(self.whys(m, "![](app/docs/plan.png)"), (["plan.png"], []))

    def test_serve_reads_the_shared_sessions_current_folder(self):
        class _S:
            cwd = "/srv/project"

        class _A:
            state = _S()
        host = mock.Mock(agent=_A())
        self.assertEqual(serve.Host._agent_cwd(host), "/srv/project")
        self.assertIsNone(serve.Host._agent_cwd(mock.Mock(agent=None)))
        _A.state = "running"                                    # an adapter whose `state` is a word, not a hook view
        self.assertIsNone(serve.Host._agent_cwd(mock.Mock(agent=_A())))

    # ---------------------------------------------------------------- reverse: still refused
    def test_outside_both_folders_is_still_outside(self):
        self.cwd = str(self.sub)
        self.assertEqual(self.whys(self.media(self.root), f"![]({self.elsewhere / 'x.png'})"), ([], ["outside"]))

    def test_symlink_escape_from_the_current_folder_is_outside(self):
        os.symlink(self.elsewhere, self.sub / "link")
        os.symlink(self.elsewhere / "x.png", self.sub / "end.png")
        self.cwd = str(self.sub)
        got = self.whys(self.media(self.root), "![](link/x.png) ![](end.png)")
        self.assertEqual(got, ([], ["outside", "outside"]))

    def test_current_folder_that_would_widen_too_far_is_ignored(self):
        m = self.media(self.root)
        (self.home / "h.png").write_bytes(png())
        for cwd in ("/", str(self.home), str(self.home.parent)):
            self.cwd = cwd
            self.assertIsNone(m.current(), cwd)
        self.cwd = str(self.home)
        self.assertEqual(self.whys(m, f"![]({self.home / 'h.png'})"), ([], ["outside"]))
        self.cwd = str(self.st.root)                             # agentj's own state is never a folder to send from
        self.assertIsNone(m.current())
        self.cwd = "relative/dir"
        self.assertIsNone(m.current())
        self.cwd = str(self.elsewhere) + "\x00"
        self.assertIsNone(m.current())

    def test_current_folder_through_a_link_or_in_a_credential_folder_is_ignored(self):
        os.symlink(self.elsewhere, self.root / "jump")
        m = self.media(self.root)
        self.cwd = str(self.root / "jump")                       # the written path holds a link → not trusted
        self.assertIsNone(m.current())
        self.assertEqual(self.whys(m, f"![]({self.elsewhere / 'x.png'})"), ([], ["outside"]))
        keys = self.elsewhere / ".ssh"
        keys.mkdir()
        (keys / "k.png").write_bytes(png())
        self.cwd = str(keys)
        self.assertIsNone(m.current())
        self.assertEqual(self.whys(m, f"![]({keys / 'k.png'})"), ([], ["outside"]))

    def test_dot_folders_below_home_never_widen(self):
        # security review of d1d446d98: `cd ~/.config/x` (or ~/.docker, ~/.kube, ~/.password-store, ~/.local/share, ~/.mozilla)
        m = self.media(self.root)
        for rel in (".config/x", ".docker", ".kube", ".password-store/web", ".local/share", ".mozilla/firefox",
                    "coding/proj/.cache", "coding/.claude/x", ".worktrees/p1"):
            d = self.home / rel
            d.mkdir(parents=True, exist_ok=True)
            (d / "f.png").write_bytes(png())
            self.cwd = str(d)
            with self.subTest(rel=rel):
                self.assertIsNone(m.current(), rel)
                self.assertEqual(self.whys(m, f"![]({d / 'f.png'})"), ([], ["outside"]), rel)

    def test_project_worktree_below_home_still_widens(self):
        m = self.media(self.root)
        for rel in ("coding/proj/.worktrees/p1", "coding/proj/.claude/worktrees/w1"):
            d = self.home / rel
            d.mkdir(parents=True)
            (d / "f.png").write_bytes(png())
            self.cwd = str(d)
            with self.subTest(rel=rel):
                self.assertEqual(m.current(), str(d), rel)
                self.assertEqual(self.whys(m, f"![]({d / 'f.png'})"), (["f.png"], []), rel)

    def test_provider_failure_widens_nothing(self):
        def boom():
            raise RuntimeError("no hook yet")
        m = media.Media(self.st, str(self.sub), cwd=boom)
        self.assertIsNone(m.current())
        self.assertEqual(self.whys(m, f"![]({self.out / 'a.png'})"), ([], ["outside"]))

    def test_others_never_get_the_current_folder(self):
        self.cwd = str(self.root)
        m = self.media(self.sub)
        rec, why = m.check(media.Ref(str(self.out / "a.png"), str(self.out / "a.png"), True), owner=False)
        self.assertEqual((rec, why), (None, "outside"))
        rec, why = m.check(media.Ref(str(self.out / "a.png"), str(self.out / "a.png"), True), owner=True)
        self.assertIsNone(why)
        self.assertEqual(m.strict(rec), "outside", "a contact's mail re-check keeps the configured folder only")


if __name__ == "__main__":
    unittest.main()
