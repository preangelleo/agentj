"""P358 (backlog #68): a read-only `find … -not -path '*/.git/*'` was judged a credential read in shared mode.

① find's -name / -path / -not -path / -prune patterns and grep's --exclude / --exclude-dir values are filters, not files
   read; a glob counts only when it is aimed at a credential name (`.env*`, `*credentials*`, `id_*`), not when `*` could
   reach one. Real reads (`cat .env`, `find . -name .env -exec cat {} \\;`, `grep -r KEY ~/.ssh`) still ask.
② a goal-mode card that times out tells the Agent so (the owner may be away), names the rule, and for credentials
   suggests a read-only way round it; the shared Claude hook forwards that message instead of a fixed line.
③ `agentj goal status` lists what each of 「等你决定 N 件」 is: the tool and one plain reason, never the command.
④ (addendum) an untargeted dump — `cat *`, `find . -type f -exec cat {} +`, `find . -not -name '*.py' -exec cat` — asks when
   the folder it runs in really holds a credential file a bash glob / the find walk reaches, passes when it does not, and
   asks when the host cannot look (no folder, a variable, a remote command). `cat *.json` beside package.json passes.
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import danger, goal, shared  # noqa: E402

import test_p339_goal_words as p339  # noqa: E402

LEO_0501 = "find $W -newermt '-20 minutes' -type f -not -path '*/.git/*' 2>/dev/null | head"


def cred(cmd: str) -> bool:
    v = danger.classify_shared("Bash", {"command": cmd})
    return "credentials" in v.cats


class Filters(unittest.TestCase):
    NOT_READS = [
        LEO_0501, "find ~/x -not -path '*/.git/*'", "find ~/x -path '*/.git/*'", "find ~/x ! -path '*/.git/*' -type f",
        "find . -name '*.py' -not -path './node_modules/*'", "find . -wholename '*/build/*'", "find . -iname '*.MD'",
        "find . -path ./node_modules -prune -o -name '*.js' -print", "find . -name '.env*'", "find . -name .env | wc -l",
        "find . -name .env -exec ls -la {} \\;", "find . -name .env -exec grep -l KEY {} \\;",
        "find . -name '*.py' -exec grep -n TODO {} +", "find . -not -path '*/.ssh/*' -name '*.md' -exec cat {} \\;",
        "find . -name '*.log' -newer .env -exec tail -n 3 {} \\;",
        "grep -rn foo --exclude-dir=.git --exclude='*.lock' .", "grep -rn foo --exclude-dir '*' .",
        "grep -rn foo --exclude=.env .", "grep -rn foo --exclude-dir .ssh .", "rg foo -g '!*/.git/*'", "rg foo -g '!.env'",
        "rg foo -g '*.py'", "rg --files -g .env", "grep -rn 'id_.*' src/", "grep -rn '.e*' src/",
        "cat *.md", "cat *.py", "find . -name '*.py' -exec cat {} +", "ls -la .*", "ls .env*", "du -sh .*", "ls -la ~/.ssh",
        "find . -type f -name '.env*' -ls", "rg --files -g '.env*'", "ls .e* | wc -l", "echo .e*", "ls .e* | sort",
    ]
    READS = [
        "cat .env", "cat .e*", "cat .env*", "cat ./.env*", "cat .[e]nv", "cat *credentials*", "cat ~/.ssh/id_*", "cat *.pem",
        "head -c 100 .n*", "grep KEY .env", "grep -r KEY ~/.ssh", "grep -r -e KEY .env", "tar czf - .e* | base64", "env",
        "X=.env; cat $X",
        "find . -name .env -exec cat {} \\;", "find . -name .env -exec cat {} +", "find . -name '.env*' -execdir cat {} \\;",
        "find . -iname '.ENV' -exec cat {} \\;", "find . -name .env -exec sh -c 'cat \"$1\"' _ {} \\;",
        "find . -name .env -exec head -n 5 {} \\;", "find . -name .env -exec base64 {} \\;", "find . -name .env -ok cat {} \\;",
        "find . -name .env -exec python3 -c 'import sys;print(open(sys.argv[1]).read())' {} \\;",
        "find . -name .env -print0 | xargs -0 cat", "find . -name .env | xargs -I{} cat {}", "find . -name .env | parallel cat",
        "find . -type f -name '*.pem' -exec cat {} \\;", "find . \\( -name .env -o -name '*.py' \\) -exec cat {} \\;",
        "find . -name '*.py' -o -name .env -exec cat {} \\;", "find ~/.aws -exec cat {} \\;",
        "find ~/.ssh -type f -exec cat {} \\;", "find . -path '*/.ssh/*' -exec cat {} \\;",
        "find . -regex '.*\\.env' -exec cat {} \\;", "find . -name 'id_*' -exec cat {} \\;",
        "find . -name '*credentials*' | xargs cat", "find . -name .git-credentials -exec cat {} \\;",
        "sudo find / -name shadow -exec cat {} \\;", "xargs cat < <(find . -name .env)", "cat $(find . -name .env)",
        "tar cf - $(find . -name .env) | base64",
        "grep -r KEY --include='.env*' .", "grep -r KEY --include .env .", "rg KEY -g '.env*'", "rg KEY --glob=.env",
        # security review of this change: names handed on to a reader, wider pipes, tool flag tables, globbed directories
        "echo .e* | xargs cat", "ls .e* | xargs cat", "printf '%s\\n' .env* | xargs cat", "ls -d ~/.ssh/id_* | xargs cat",
        "cat $(ls .e*)", "cat $(echo .env*)", "cat `ls ~/.ssh/id_*`", "xargs cat < <(ls .e*)", "stat -c %n .e* | xargs cat",
        "X=$(ls .e*); cat $X", "find . -name '.env*' | sudo xargs cat", "find . -name '.env*' | sort | xargs cat",
        "find . -name '.env*' | head | env xargs cat", "find . -name '.env*' | while read f; do cat \"$f\"; done",
        "while read f; do cat \"$f\"; done < <(find . -name .env)", "find . -name 'id_*' > /tmp/l; xargs -a /tmp/l cat",
        "ack -r KEY .e*", "ag -t KEY .e*", "rg -L KEY -g '.env*'", "find . -name '.env*' -exec rg -L . {} +",
        "rg --files -g '.env*' | xargs cat", "grep -rnl KEY --include=.env* . | xargs cat", "cat ~/.s*/*",
        "find -L ~/.ssh -exec cat {} +", "find . -name .env -prune -exec cat {} \\;", "find . -name '*.pfx' -exec cat {} \\;",
        "find . -name '\\.env' -exec cat {} \\;", "cat .env{,.local}", "cat .e{n,x}v", "echo \"$(<.env*)\"",
    ]

    def test_the_0501_command_and_the_minimal_repros_do_not_ask(self):
        for cmd in (LEO_0501, "find ~/x -not -path '*/.git/*'", "find ~/x -path '*/.git/*'"):
            v = danger.classify_shared("Bash", {"command": cmd})
            self.assertEqual(v.cats, [], cmd)
            self.assertFalse(v.danger, cmd)

    def test_filters_and_untargeted_globs_are_not_reads(self):
        for cmd in self.NOT_READS:
            self.assertFalse(cred(cmd), cmd)

    def test_real_reads_still_ask_as_credentials_read(self):
        for cmd in self.READS:
            v = danger.classify_shared("Bash", {"command": cmd})
            self.assertIn("credentials", v.cats, cmd)
            self.assertEqual(v.cred, "read", cmd)
            self.assertEqual(danger.grant_keys(v), ["credentials:read"], cmd)

    def test_strict_and_safe_levels_unchanged(self):
        for mode in ("strict", "safe"):
            self.assertIn("credentials", danger.classify_shared("Bash", {"command": "find . -name .env"}, mode=mode).cats)
            self.assertEqual(danger.classify_shared("Bash", {"command": LEO_0501}, mode=mode).cats, [], mode)

    def test_glob_must_aim_at_a_credential_name(self):
        for w in (".env*", ".e*", "*credentials*", "id_*", "*.env", "secret*", ".[e]nv"):
            self.assertTrue(danger._glob_cred(w), w)
        for w in ("*", "*/.git/*", "!*/.git/*", "*.json", "*.txt", "*.py", ".gitignore"):
            self.assertFalse(danger._glob_cred(w), w)


class Dumps(unittest.TestCase):
    """④ the dump is judged by what the folder holds on this computer."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="p358d-", dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        tree = {"clean": ["a.py", "package.json", "README.md", "src/main.py"],
                "dot": ["main.py", "package.json", ".env", "sub/credentials.json"],      # only a dot file at the top
                "top": ["a.py", "credentials.json", "id_ed25519"],
                "deep": ["x.py", "a/b/id_rsa"]}
        for d, files in tree.items():
            for f in files:
                (self.root / d / f).parent.mkdir(parents=True, exist_ok=True)
                (self.root / d / f).write_text("x")
        (self.root / "linked").mkdir()
        (self.root / "linked" / "k").symlink_to(self.root / "top" / "id_ed25519")     # a key behind a plain name

    def ask(self, cmd, where):
        v = danger.classify_shared("Bash", {"command": cmd}, cwd=str(self.root / where) if where else None)
        return "credentials" in v.cats and v.cred == "read"

    ASKS = [("top", "cat *"), ("top", "cat *.json"), ("top", "cat ./*"), ("top", "head -n 3 *"), ("top", "sudo cat *"),
            ("top", "grep KEY *"), ("top", "echo * | xargs cat"), ("top", "ls | xargs cat"), ("top", "cat $(ls)"),
            ("top", "for f in *; do cat \"$f\"; done"), ("clean", "cat ../top/*"), ("clean", "cd ../top && cat *"),
            ("dot", "cat */*"), ("dot", "cat sub/*.json"), ("dot", "grep -r KEY *"), ("dot", "ls -a | xargs cat"),
            ("dot", "find . -type f -exec cat {} +"), ("dot", "find . -not -name '*.py' -exec cat {} \\;"),
            ("dot", "find . -type f | xargs cat"), ("dot", "find . -type f -print0 | xargs -0 cat"),
            ("dot", "find . -type f > l; xargs -a l cat"), ("dot", "find . -type f | while read f; do cat \"$f\"; done"),
            ("dot", "find . -type f \\( -name '*.py' -o -name '*.md' \\) -exec cat {} +"),     # a group: not trusted
            ("dot", "grep -r KEY --include '*' ."), ("deep", "find . -type f -exec cat {} +"), ("deep", "tar czf /tmp/x.tgz *"),
            ("deep", "find -L . -type f -exec cat {} +"), ("deep", "find . -mindepth 1 -type f -exec cat {} +"),
            # the host cannot look: no folder, a variable folder, a remote command
            (None, "cat *"), (None, "find . -type f -exec cat {} +"), ("clean", "cat $W/*"), ("clean", "find $W -type f -exec cat {} +"),
            ("clean", "ssh h 'cat *'"), ("clean", "ssh h find . -type f -exec cat {} +"), ("clean", "cd $D && cat *"),
            # the adversarial round of the addendum: glob options, a folder change inside a shell, links, copies, lists
            ("dot", "shopt -s dotglob; cat *"), ("dot", "setopt globdots; cat *"), ("dot", "GLOBIGNORE=x; cat *"),
            ("clean", "cat *(D)"), ("deep", "cat **/*"), ("clean", "sh -c 'cd ../top && cat *'"),
            ("clean", "bash -c \"cd ../top; cat *\""), ("clean", "(cd ../top; cat *)"), ("clean", "env -C ../top cat *"),
            ("clean", "env --chdir=../top cat *"), ("clean", "cat ../dot/.env > k; cat *"), ("clean", "xargs -a <(ls ../top) cat"),
            ("clean", "ls ../top | xargs cat"), ("clean", "cat ../{top,clean}/*"), ("clean", "cat ../t*/*"),
            ("dot", "find . -type f -fprint l; xargs -a l cat"), ("dot", "grep -r '' ."), ("clean", "grep -r . ../top"),
            ("linked", "cat *"), ("linked", "cat k"), ("linked", "find . -type f -exec cat {} +"), ("linked", "find -L . -exec cat {} +")]
    PASSES = [("clean", "cat *"), ("clean", "cat *.json"), ("clean", "cat */*"), ("clean", "find . -type f -exec cat {} +"),
              ("clean", "find . -not -name '*.py' -exec cat {} \\;"), ("clean", "grep -r KEY --include '*' ."),
              ("clean", "ls | xargs cat"), ("clean", "tar czf /tmp/x.tgz *"), ("top", "cat *.py"),
              ("dot", "cat *"), ("dot", "cat *.json"), ("dot", "grep KEY *"), ("dot", "echo * | xargs cat"),  # bash `*` skips .env
              ("dot", "find . -name '*.py' -exec cat {} +"), ("dot", "find . -type f > l"), ("dot", "find . -type d -exec cat {} +"),
              ("dot", "find . -type f -exec rm {} +"), ("dot", "find . -type f -exec ls -la {} +"), ("dot", "wc -l *"),
              ("dot", "ls *"), ("dot", LEO_0501), ("dot", "find . -type f -not -path '*/.git/*' | head"),
              ("dot", "find . -path ./sub -prune -o -type f -not -name .env -exec cat {} +"),
              ("deep", "find . -maxdepth 2 -type f -exec cat {} +"), ("deep", "find . -path ./a -prune -o -type f -exec cat {} +"),
              ("top", "cp * /tmp/"), ("dot", "grep -rn TODO ."), ("clean", "grep -r '' ."), ("clean", "cat **/*"), (None, "cat *.py"), (None, "find . -name '*.md' -exec cat {} +"), (None, LEO_0501)]

    def test_a_dump_asks_when_it_reaches_a_credential_file_or_cannot_tell(self):
        for where, cmd in self.ASKS:
            self.assertTrue(self.ask(cmd, where), (where, cmd))

    def test_a_dump_passes_when_the_folder_holds_none(self):
        for where, cmd in self.PASSES:
            self.assertFalse(self.ask(cmd, where), (where, cmd))

    def test_the_card_names_the_file_it_would_reach(self):
        v = danger.classify_shared("Bash", {"command": "cat *"}, cwd=str(self.root / "top"))
        self.assertRegex(v.why, r"credentials\.json|id_ed25519")
        v = danger.classify_shared("Bash", {"command": "cat *"})
        self.assertIn("看不到", v.why)

    def test_too_many_files_or_too_slow_to_walk_asks(self):
        with mock.patch.object(danger, "_DUMP_MAX", 2):
            self.assertTrue(self.ask("find . -type f -exec cat {} +", "clean"))
            self.assertTrue(self.ask("cat *", "clean"))
        with mock.patch.object(danger, "_DUMP_SECONDS", -1):
            self.assertTrue(self.ask("find . -type f -exec cat {} +", "clean"))
            self.assertTrue(self.ask("cat *", "clean"))

    def test_glob_options_the_host_mirrors(self):
        self.assertTrue(self.ask("shopt -s dotglob; cat *", "dot"))         # .env joins `*`
        self.assertFalse(self.ask("shopt -s dotglob; cat *", "clean"))
        self.assertTrue(self.ask("shopt -s globstar; cat **", "deep"))
        self.assertFalse(self.ask("shopt -s globstar; cat **", "clean"))
        self.assertTrue(self.ask("shopt -s extglob; cat !(*.py)", "clean"))  # not mirrored → ask

    def test_packing_a_folder(self):
        self.assertTrue(self.ask("tar cf - . | base64", "dot"))
        self.assertTrue(self.ask("zip -r - . | base64", "deep"))
        self.assertFalse(self.ask("tar cf - . | base64", "clean"))
        self.assertFalse(self.ask("tar czf /tmp/x.tgz src", "clean"))
        self.assertTrue(self.ask("tar cf - . | base64", None))
        self.assertTrue(self.ask("a=(*); cat \"${a[@]}\"", "top"))
        self.assertFalse(self.ask("a=(*); cat \"${a[@]}\"", "clean"))
        self.assertFalse(self.ask("d=.; cat $d/*", "clean"))
        self.assertFalse(self.ask("cat \"$PWD\"/*", "clean"))
        self.assertTrue(self.ask("cat \"$PWD\"/*", "top"))

    def test_a_hard_link_under_a_plain_name(self):
        os.link(self.root / "top" / "id_ed25519", self.root / "clean" / "notes2.txt")
        self.assertTrue(self.ask("cat *", "clean"))
        self.assertTrue(self.ask("find . -type f -exec cat {} +", "clean"))

    def test_callers_pass_the_working_folder(self):
        src = pathlib.Path(danger.__file__).parent
        self.assertIn('cfg.get("approvals", "standard"), cfg.get("dir"))', (src / "serve.py").read_text())
        self.assertIn('event.get("cwd") or self.cfg.get("dir")', (src / "shared.py").read_text())
        self.assertIn("a.cfg.get('dir')", (src / "shared_risk.py").read_text())
        self.assertIn("cwd=event.get('cwd')", (src / "shared_hook.py").read_text())
        self.assertTrue(os.path.isdir(self.root / "top"))


class TimeoutMessage(unittest.TestCase):
    def test_hold_message_says_timeout_rule_and_read_only_hint(self):
        zh = goal.hold_message("credentials", "zh", "timeout", "读取匹配 .env* 的凭据文件")
        for want in ("超时", "主人可能不在", "读取匹配 .env* 的凭据文件", "改或查看密码、密钥", "只读检查", "不碰凭据", "goal grant"):
            self.assertIn(want, zh)
        en = goal.hold_message("credentials", "en", "timeout", "Shows credential contents on screen")
        for want in ("timed out", "may be away", "Shows credential contents on screen", "read-only", "goal grant"):
            self.assertIn(want, en)
        dev = goal.hold_message("irreversible", "zh", "device")
        self.assertNotIn("超时", dev)
        self.assertNotIn("只读检查", dev)                       # the hint is only for a credentials hold

    def _agent(self, answer):
        tmp = tempfile.TemporaryDirectory(prefix="p358-", dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        host = Mock()
        host.st.root = pathlib.Path(tmp.name)
        host.stopped.return_value = False
        host.st.sign_key.return_value = "TEST_SIGNING_IDENTITY"
        host.ask = AsyncMock(return_value=answer)
        a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": tmp.name, "_workflow_ceo": True,
                                            "high_risk_warnings": True})
        a.session = {"sessionId": "owner"}
        return a

    def _deny_message(self, answer):
        a = self._agent(answer)

        async def go():
            ev = {"session_id": "owner", "tool_name": "Bash", "tool_input": {"command": "cat .env"}}
            await a.hook_event({**ev, "hook_event_name": "PreToolUse"})
            return await a.hook_event({**ev, "hook_event_name": "PermissionRequest"})
        out = asyncio.run(go())
        d = out["hookSpecificOutput"]["decision"]
        self.assertEqual(d["behavior"], "deny")
        return d["message"]

    def test_shared_hook_forwards_the_hosts_message(self):
        msg = goal.hold_message("credentials", "zh", "timeout", "把凭据文件 .env 的内容显示到屏幕上")
        self.assertEqual(self._deny_message({"behavior": "deny", "message": msg}), msg)
        self.assertEqual(self._deny_message({"behavior": "deny"}), "Denied by paired phone or timeout.")


class GoalHost(unittest.TestCase):
    """The real host: a goal, a credentials step whose card nobody answers, then `agentj goal status`."""
    setUp = p339.HostGoal.setUp
    tearDown = p339.HostGoal.tearDown
    asks = p339.HostGoal.asks
    signed = p339.HostGoal.signed
    card_then = p339.HostGoal.card_then
    start_goal = p339.HostGoal.start_goal
    run_ask = p339.HostGoal.run_ask

    def shared(self):
        return mock.patch.object(self.host, "_classify", lambda tool, inp: danger.classify_shared(tool, inp, None, "standard"))

    def test_timeout_message_and_status_list(self):
        self.start_goal()
        cmd = "cat ~/.aws/credentials"
        with self.shared():
            res, card = self.run_ask(cmd)                    # nobody answers: ask_ttl = 1 s
        self.assertEqual(card["goal"], "hold")
        self.assertEqual(res["behavior"], "deny")
        for want in ("超时", "主人可能不在", "只读检查", "改或查看密码、密钥"):
            self.assertIn(want, res["message"])
        d = goal.load(self.st)
        b = d["blocked"][-1]
        self.assertEqual(b["why"], "credentials")
        self.assertTrue(b.get("rule"))
        text = goal.status_text(self.st, d, "zh")
        self.assertIn("等你决定 1 件", text)
        self.assertIn("1.【Bash】", text)
        self.assertIn("改或查看密码、密钥", text)
        self.assertNotIn(cmd, text)                          # the command itself is not listed
        en = goal.status_text(self.st, d, "en")
        self.assertIn("1. [Bash]", en)

    def test_shared_mode_0501_command_runs_without_a_card(self):
        self.start_goal()
        with self.shared():
            n = len(self.asks("Bash"))
            res, _ = self.run_ask(LEO_0501)
        self.assertEqual(res["behavior"], "allow")
        self.assertEqual(len(self.asks("Bash")), n)


class StatusRedaction(unittest.TestCase):
    def test_status_lines_never_show_a_secret(self):
        key = "sk-" + "Q1w2E3r4T5y6U7i8O9p0" * 2
        d = {"blocked": [{"tool": "Bash", "why": "credentials", "human": "",
                          "rule": "读取匹配 .env* 的凭据文件", "summary": f"curl -H 'x: {key}'", "raw": f"curl {key}"}]}
        lines = "\n".join(goal.blocked_lines(d, False))
        self.assertIn("读取匹配 .env* 的凭据文件", lines)
        self.assertNotIn(key[:12], lines)
        self.assertEqual(goal.blocked_lines({"blocked": []}, False), [])


if __name__ == "__main__":
    unittest.main()
