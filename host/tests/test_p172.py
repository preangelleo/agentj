"""P172: a quoted heredoc body is data, not commands; a sub-agent's approval card reaches the phone and stays there.

1. `cat > REPORT.md <<'EOF' … EOF` with Markdown (`code`, [links](https://…), | tables |) was asked as 看不清要做什么
   (opaque.dynamic_name: a backticked table cell read as a command). Quoted bodies (<<'EOF' / <<"EOF" / <<\\EOF) are not
   expanded, so they are no longer lexed — unless something in the command could run them. Unquoted bodies, shells,
   interpreters, base64 | sh, eval … are judged exactly as before.
2. Live 10-12 16:53: a background sub-agent's PermissionRequest card went to the phone, then the MAIN thread's Stop withdrew
   it (P139 「turn end withdraws every card」) and the dialog stayed in the terminal only. The main Stop now withdraws only
   main-thread cards; a sub-agent's card stays until its call finishes, it stops, or /clear. The card names the sub-agent.
"""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import danger, shared

HERE = Path(__file__).resolve().parent

REPORT = """# 国内网盘 API 调研

## 4. Agent J 集成建议

- 凭据：放在各 CLI 自己的配置目录（如 `~/.config/bdpan/`），Agent J 不读取。
- 校验：上传后用 `ls --json` 或 `stat` 比对 size。

| 服务 | CLI | rclone | 价格 | 备注 |
|---|---|---|---|---|
| 阿里云 OSS | `ossutil` | S3 `provider=Alibaba` | 约 0.09~0.12 元/GB/月 [第三方整理](https://developer.aliyun.com/article/1691983) | 上传免费 |
| 又拍云 USS | `upx` | ❌ | 以官网为准 | rclone 没有后端 |

- 驱动全景：[OpenList drivers 源码](https://github.com/OpenListTeam/OpenList/tree/main/drivers)（`*_open` 是官方开放平台）
- 例子：`$(whoami)`、`rm -rf ~/x`、`curl -X POST https://api.example.com/send` 都只是文字
"""
PATH = "/srv/u/work/experimental/cn-cloud-drive-api/REPORT.md"


def heredoc(delim="'EOF'", end="EOF", body=REPORT, after=f"wc -c {PATH}"):
    return f"cat > {PATH} <<{delim}\n{body}{end}\n{after}"


def both(cmd):
    return danger.classify("Bash", {"command": cmd}), danger.classify_shared("Bash", {"command": cmd})


class QuotedHeredocIsData(unittest.TestCase):
    def test_leo_report_is_low_risk_in_both_modes(self):
        for delim in ("'EOF'", '"EOF"', "\\EOF", "-'EOF'", " 'EOF'"):
            with self.subTest(delim=delim):
                for v in both(heredoc(delim)):
                    self.assertEqual(v.cats, [], v.rules)

    def test_hook_keeps_ordinary_quoted_report_silent(self):
        env = dict(os.environ, PYTHONPATH=str(HERE.parent))
        r = subprocess.run([sys.executable, "-P", "-m", "agentj.danger", "hook"], capture_output=True, text=True, env=env,
                           input=json.dumps({"tool_name": "Bash", "tool_input": {"command": heredoc()}}), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "", "quoted report data is an ordinary write")

    def test_git_commit_message_heredoc(self):
        cmd = 'git commit -m "$(cat <<\'EOF\'\nfix: `ossutil` | table | [x](https://a.b)\nEOF\n)"'
        for v in both(cmd):
            self.assertEqual(v.cats, [], v.rules)

    def test_unquoted_body_keeps_the_old_reading(self):
        # an unquoted body IS expanded: its backticks / $( ) run, so the same report stays 看不清要做什么
        self.assertIn("opaque", danger.classify("Bash", {"command": heredoc("EOF")}).cats)
        self.assertIn("delete", danger.classify("Bash", {"command": heredoc("EOF", body="x $(rm -rf ~/y) x\n")}).cats)
        self.assertIn("delete", danger.classify("Bash", {"command": heredoc("EOF", body="x `rm -rf ~/y` x\n")}).cats)

    def test_raw_text_rules_still_see_the_body(self):
        # data written to a file and fed elsewhere: the raw-text rules (SQL, API URLs) are unchanged
        cmd = "cat > x.sql <<'EOF'\nDROP TABLE users;\nEOF\nsqlite3 app.db < x.sql"
        self.assertIn("delete", danger.classify("Bash", {"command": cmd}).cats)


class RealDangerUnchanged(unittest.TestCase):
    CASES = {
        "bash <<'EOF'\nrm -rf ~/x\nEOF": "delete",
        "cat <<'EOF' | sh\nrm -rf ~/x\nEOF": "delete",
        "cat > x.sh <<'EOF'\nrm -rf ~/x\nEOF\nbash x.sh": "delete",
        "cat > x.sh <<'EOF'\nrm -rf ~/x\nEOF\nchmod +x x.sh && ./x.sh": "delete",
        "docker exec -i box sh <<'EOF'\nrm -rf /data\nEOF": "delete",
        "ssh host <<'EOF'\nrm -rf /srv/x\nEOF": "delete",
        "sudo bash <<'EOF'\nrm -rf /srv/x\nEOF": "delete",
        "cat <<'EOF' | xargs rm -rf\n/srv/x\nEOF": "delete",
        "cat > f.md <<'EOF'\nnotes\nEOF\nrm -rf ~/y": "delete",              # after the delimiter: a command again
        "cat > f.md <<'EOF'\nnotes\nEOF\ngit push origin main": "send",
        "cat > f.md <<'EO'F\nnotes\nEOF\nrm -rf ~/y": "delete",              # odd delimiter: old reading
        "cat > f.md <<'EOF'\nrm -rf ~/y": "delete",                         # never closed: old reading
        "echo cm0gLXJmIH4vCg== | base64 -d | sh": "opaque",
        "base64 -d <<< cm0gLXJmIH4vCg== | bash": "opaque",
        'eval "$(echo cm0gLXJmIH4vCg== | base64 -d)"': "opaque",
        'X=rm; $X -rf ~/y': "delete",
        "cat > ~/.ssh/authorized_keys <<'EOF'\nssh-ed25519 AAAA x\nEOF": "credentials",
        "cat > ~/.claude/settings.json <<'EOF'\n{}\nEOF": "credentials",
    }

    def test_cases(self):
        for cmd, cat in self.CASES.items():
            with self.subTest(cmd=cmd):
                self.assertIn(cat, danger.classify("Bash", {"command": cmd}).cats)

    def test_shared_mode_cases(self):
        for cmd in ("bash <<'EOF'\ngit push --force origin main\nEOF", "cat <<'EOF' | sh\ngit push origin main\nEOF",
                    "cat > x.sh <<'EOF'\ngit push origin main\nEOF\n./x.sh", "echo cm0gLXJmIH4vCg== | base64 -d | sh",
                    "cat > f.md <<'EOF'\nnotes\nEOF\ngit push --force origin main"):
            with self.subTest(cmd=cmd):
                self.assertTrue(danger.classify_shared("Bash", {"command": cmd}).cats)


def ev(name, inp=None, tool="Bash", **kw):       # git push: a card in shared mode (a local rm is not, ADR-A145)
    return {"hook_event_name": name, "session_id": "owner", "tool_name": tool, "tool_input": inp or {"command": "git push"},
            **kw}


class SubAgentCard(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p172-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        (self.root / "work/.claude").mkdir(parents=True)
        (self.root / "home/.claude").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "home/.claude")})
        env.start()
        self.addCleanup(env.stop)

    def agent(self, ask):
        h = Mock()
        h.st.root = self.root
        h.stopped.return_value = False
        h.lang = "zh"
        h.st.sign_key.return_value = "K"
        h.ask = ask
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": str(self.root / "work"), "_workflow_ceo": True,
                                         "high_risk_warnings": True})
        a.session = {"sessionId": "owner", "pid": os.getpid()}
        a.issue_hook_token()
        a.native_source = {"kind": "terminal", "role": "owner"}
        return a

    def run_card(self, follow, agent_id="sub1", pr_has_agent=False):
        """Dispatch a background sub-agent, let it ask, then deliver `follow`; returns (card still open?, ask kwargs, a)."""
        opened, seen = asyncio.Event(), {}

        async def ask(tool, inp, gone=None, **kw):
            seen.update(kw)
            opened.set()
            try:
                await asyncio.wait_for(asyncio.shield(gone), 0.5)
                return {"behavior": "deny"}
            except asyncio.TimeoutError:
                return {"behavior": "allow"}
        a = self.agent(ask)

        async def go():
            await a.hook_event(ev("PreToolUse", {"description": "写网盘报告", "subagent_type": "general-purpose",
                                                 "prompt": "…", "run_in_background": True}, tool="Agent"))
            await a.hook_event({"hook_event_name": "SubagentStart", "session_id": "owner", "agent_id": agent_id,
                                "agent_type": "general-purpose"})
            r = await a.hook_event(ev("PreToolUse", agent_id=agent_id, agent_type="general-purpose"))
            self.assertEqual(r["hookSpecificOutput"]["permissionDecision"], "ask")
            pr = ev("PermissionRequest", **({"agent_id": agent_id} if pr_has_agent else {}))
            card = asyncio.ensure_future(a.hook_event(pr))
            await opened.wait()
            for f in follow:
                await a.hook_event(dict(f))
            return await asyncio.wait_for(card, 5)
        res = asyncio.run(go())
        return res, seen, a

    MAIN_STOP = {"hook_event_name": "Stop", "session_id": "owner", "last_assistant_message": "x", "background_tasks": []}

    def test_main_stop_keeps_the_sub_agents_card(self):
        for has in (False, True):           # PermissionRequest with or without agent_id: PreToolUse names the agent
            with self.subTest(pr_has_agent=has):
                res, seen, a = self.run_card([self.MAIN_STOP], pr_has_agent=has)
                self.assertEqual(res["hookSpecificOutput"]["decision"]["behavior"], "allow", "the phone still answers")
                self.assertEqual(a.open_cards, {})
                self.assertEqual(a.card_agent, {})

    def test_turn_end_without_stop_keeps_it_too(self):
        opened = asyncio.Event()

        async def ask(tool, inp, gone=None, **kw):
            opened.set()
            try:
                await asyncio.wait_for(asyncio.shield(gone), 0.5)
                return {"behavior": "deny"}
            except asyncio.TimeoutError:
                return {"behavior": "allow"}
        a = self.agent(ask)

        async def go():
            await a.hook_event(ev("PreToolUse", agent_id="s9"))
            card = asyncio.ensure_future(a.hook_event(ev("PermissionRequest")))
            await opened.wait()
            a._withdraw_answered("Stop", {})            # what _end_without_stop does
            return await card
        self.assertEqual(asyncio.run(go())["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_sub_agent_end_or_clear_withdraws_it(self):
        for follow in ({"hook_event_name": "SubagentStop", "session_id": "owner", "agent_id": "sub1"},
                       {"hook_event_name": "SessionEnd", "session_id": "owner"},
                       ev("PostToolUse", tool_response={"ok": True}, agent_id="sub1")):
            with self.subTest(follow=follow["hook_event_name"]):
                res, _, a = self.run_card([follow])
                self.assertEqual(res, {}, "answered elsewhere: no late decision from us")
                self.assertEqual(a.open_cards, {})

    def test_another_sub_agent_stopping_keeps_it(self):
        res, _, _ = self.run_card([{"hook_event_name": "SubagentStop", "session_id": "owner", "agent_id": "other"}])
        self.assertEqual(res["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_card_names_the_sub_agent(self):
        _, seen, _ = self.run_card([])
        self.assertEqual(seen.get("origin"), "来自子 Agent：general-purpose · 写网盘报告")

    def test_main_thread_card_has_no_origin_and_stop_still_withdraws(self):
        opened, seen = asyncio.Event(), {}

        async def ask(tool, inp, gone=None, **kw):
            seen.update(kw)
            opened.set()
            await asyncio.wait_for(gone, 5)
            return {"behavior": "deny"}
        a = self.agent(ask)

        async def go():
            await a.hook_event(ev("PreToolUse"))
            card = asyncio.ensure_future(a.hook_event(ev("PermissionRequest")))
            await opened.wait()
            await a.hook_event(dict(self.MAIN_STOP))
            return await asyncio.wait_for(card, 5)
        self.assertEqual(asyncio.run(go()), {})
        self.assertNotIn("origin", seen)


class HostCardOrigin(unittest.TestCase):
    """serve.Host.ask puts the origin on the card's last line (the first stays the command: notices, push)."""

    def test_origin_line(self):
        from test_danger import Phone, _host, _ready, _state
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            (Path(d) / "work").mkdir()
            st.set_agent_config("claude", str(Path(d) / "work"))
            sent = []
            host = _host(st, sent)
            host.ask_ttl = 1
            _ready(host, Phone(st))

            async def go():
                return await host.ask("Bash", {"command": "rm -rf x"}, origin="来自子 Agent：general-purpose · 写报告")
            asyncio.run(go())
            card = [o for _, o in sent if o["t"] == "ask"][-1]
            lines = card["summary"].split("\n")
            self.assertIn("rm -rf x", lines[0])
            self.assertEqual(lines[-1], "来自子 Agent：general-purpose · 写报告")


if __name__ == "__main__":
    unittest.main()
