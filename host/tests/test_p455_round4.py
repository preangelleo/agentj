"""#455 security round 4 (0.26.8a2) B4-1: the same_host no-card read allowlist never opens a file through an option whose
value skips the read check — `date -f/--file/-r`, `file -f/-m` (attached or not), GNU long-option prefixes (`--fil`),
`--opt=value`, short clusters carrying a path, names after `--`, and every audited _RD word (sort -T, tree -R,
sha*sum -c, grep --exclude-from …). Pure peer_origin checks on a throw-away workspace + state dir; no real agentj state."""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import peer_origin  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402


class B41OptionReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p455r4-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        base = pathlib.Path(self.tmp.name)
        self.ws, self.state, self.out = base / "ws", base / "state", base / "out"
        for d in (self.ws / "src", self.state, self.out / ".ssh"):
            d.mkdir(parents=True)
        (self.ws / "src" / "a.py").write_text("x = 1\n")
        (self.state / "devices.json").write_text("{}\n")
        (self.state / "approvals.log").write_text("synthetic\n")
        (self.out / ".ssh" / "id_ed25519").write_text("synthetic\n")
        (self.ws / "x").symlink_to(self.state / "devices.json")        # the symlink route: a plain workspace name
        (self.ws / "k").symlink_to(self.out / ".ssh" / "id_ed25519")
        (self.ws / "-z").symlink_to(self.state / "approvals.log")      # a name that looks like an option

    def bash(self, cmd):
        return peer_origin.allowed("Bash", {"command": cmd}, str(self.ws), str(self.state))

    def refused(self, cmds):
        for c in cmds:
            self.assertFalse(self.bash(c), c)

    def allowed(self, cmds):
        for c in cmds:
            self.assertTrue(self.bash(c), c)

    def test_reported_date_and_file_forms(self):
        s = self.state
        self.refused([f"date -f {s}/devices.json", f"date --file={s}/approvals.log", f"date --file {s}/devices.json",
                      "date -f /proc/self/environ", "date -f x", "date --fi=x", f"date -r {s}/devices.json",
                      "date --reference=x", "date -fx", "date -uf x", "date -d 'TZ=\"x\" now'", "date -d a/b",
                      f"file -f{s}/devices.json", f"file -m{s}/approvals.log", "file -fx", "file -mx", "file -m x",
                      "file -f x", f"file --files-from={s}/devices.json", "file --magic-file x", "file --magic=x",
                      "file -C -m src/a.py", "file -bm x src/a.py", "file -f/proc/self/environ", "file -m~/x"])
        self.allowed(["date", "date -u", "date +%Y-%m-%d", "date '+%H:%M %Z'", "date -Iseconds", "date --iso-8601=minutes",
                      "date -R", "date --rfc-3339=seconds", "date -d yesterday", "date -d '2026-11-04 10:00' +%s",
                      "date --date=@1700000000", "file src/a.py", "file -b src/a.py", "file -i src/a.py"])

    def test_attached_values_long_prefixes_and_dashdash(self):
        s = self.state
        self.refused(["cat -- -z", "head -n 1 -- -z", "wc -l -z", "cat -x", "grep --fil x src/a.py", "grep --fi x src/a.py",
                      "grep --file=x src/a.py", "grep -fx src/a.py", "grep -e a -fx src/a.py", "grep -ef x src/a.py",
                      "wc --files0 x", "wc --fil=x", "wc --files0-from=-", "du --files0-from x", "sort --files0-fr=x",
                      "diff --recursiv src src", "diff -ru src src", "sort --out=src/b src/a.py", "sort --o src/b src/a.py",
                      f"cut -d, -f1 {s}/devices.json", "cut -d/ -f1 src/a.py", "head -n1/x src/a.py", "ls --hide=x/../x",
                      "diff --from-file=x src/a.py", "diff --from-file x src/a.py", "realpath --relative-to=k src/a.py",
                      "stat -c %n x", "nl -s: x", "tail -n 2 k", f"ls {s}"])
        self.allowed(["cat src/a.py", "cat -- src/a.py", "cat -n src/a.py", "head -n5 src/a.py", "head -5 src/a.py",
                      "wc -lc src/a.py", "grep -n x src/a.py", "grep -e x -e y src/a.py",
                      "grep --files-with-matches x src/a.py", "grep -F --fixed-strings x src/a.py", "cut -d, -f1 src/a.py",
                      "sort -k2,2 -t: src/a.py", "diff --from-file=src/a.py src/a.py", "ls -la src", "ls --color=never src",
                      "stat -c %n src/a.py", "realpath --relative-to=src src/a.py", "du -sh src", "tree -L 2 src", "cat -"])

    def test_audited_rd_words(self):
        """Each _RD word with an option that opens a path second-hand, echoes it, or writes: refused."""
        self.refused(["sort -T src src/a.py", "sort --temporary-directory=src src/a.py", "sort -To src/a.py",
                      "sort --compress-program=id src/a.py", "tree -R src", "tree -o src/t src",
                      "sha256sum -c src/a.py", "md5sum --check src/a.py", "sha1sum --ch src/a.py", "sha256sum -bc src/a.py",
                      "grep --exclude-from=src/a.py x src", "grep --exclude-from src/a.py x src/a.py",
                      "grep -D read x src/a.py", "grep --devices=read x src/a.py", "egrep --dereference-recursive x src",
                      "fgrep -d recurse x src", "egrep --directories=recurse x src", "file --compile src/a.py",
                      "uniq src/a.py src/b", "du -X x src", "du --exclude-from=x src", "tree --fromfile x"])
        self.allowed(["sha256sum src/a.py", "md5sum -b src/a.py", "sort -u src/a.py", "uniq -c src/a.py", "du -X src/a.py src",
                      "tree --fromfile src/a.py", "cmp src/a.py src/a.py", "readlink -f src/a.py", "nl -ba src/a.py"])


if __name__ == "__main__":
    unittest.main()
