"""Agent bridge (PROTOCOL §8): drives the customer's own, already-logged-in agent through its official headless
interfaces only — Claude Code: `claude -p` with stream-json in/out and `--permission-prompt-tool` (our MCP tool, see
permtool.py); Codex: `codex exec --json` (text only in v1). No private sockets, no terminal injection.

Bridge ≤ session: the agent runs as this OS user, in the directory the human chose, with the human's own login, settings
and permission rules. We never pass a bypass / skip-permissions / allowed-tools flag; the phone only answers the questions
the agent itself would have asked, and an approval returns the tool input unchanged.

L2: the agent runs inside the fence (fence.py) unless the human chose `--unfenced`; if the fence cannot start, the agent is
not started at all. Claude Code gets its first message only after our permission tool has claimed serve's socket.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys

from . import fence
from .text import clean, text_units

MAX_TEXT = 4000               # = wire.MAX_TEXT (UTF-16 units per app message)
LINE_LIMIT = 64 * 1024 * 1024  # stream-json lines can carry whole tool results
STATUSES = ("none", "idle", "working", "waiting", "down")
PERM_TOOL = "mcp__agentjarvis__approve"
CLAIM_WAIT = 30               # s: the permission tool must claim perm.sock this soon after Claude Code starts


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Split a reply into chunks of ≤ limit UTF-16 units, preferring line breaks. Never drops text."""
    out, cur, n = [], [], 0
    for ch in text:
        u = 2 if ord(ch) > 0xFFFF else 1
        if n + u > limit:
            s = "".join(cur)
            cut = s.rfind("\n")
            if cut > len(s) // 2:
                out.append(s[:cut])
                cur, n = list(s[cut + 1:]), text_units(s[cut + 1:])
            else:
                out.append(s)
                cur, n = [], 0
        cur.append(ch)
        n += u
    if cur:
        out.append("".join(cur))
    return [c for c in out if c.strip()]


def summarize(tool: str, tool_input) -> str:
    """What the phone shows for a permission request (and what the device's signature covers, §8)."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    s = None
    if tool == "Bash" and isinstance(inp.get("command"), str):
        s = inp["command"]
        if isinstance(inp.get("description"), str) and inp["description"].strip():
            s = f"{s}\n— {inp['description'].strip()}"
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read") and isinstance(inp.get("file_path") or inp.get("notebook_path"), str):
        path = inp.get("file_path") or inp.get("notebook_path")
        if tool == "Write" and isinstance(inp.get("content"), str):
            s = f"{path}\n（写入 {len(inp['content'].encode())} 字节）\n{inp['content'][:600]}"
        elif tool == "Edit" and isinstance(inp.get("old_string"), str):
            s = f"{path}\n- {inp['old_string'][:400]}\n+ {str(inp.get('new_string', ''))[:400]}"
        else:
            s = path
    elif tool in ("WebFetch",) and isinstance(inp.get("url"), str):
        s = inp["url"]
    if s is None:
        s = json.dumps(inp, ensure_ascii=False, separators=(", ", ": "))
    s = clean(s, 4000)
    return s if text_units(s) <= 2000 else s[:1990] + " …"


class Agent:
    """Base: a queue of user messages, one turn at a time, status reported to the host."""
    kind = "?"

    def __init__(self, host, cfg: dict):
        self.host, self.cfg = host, cfg
        self.q: asyncio.Queue[str] = asyncio.Queue()
        self.status = "idle"
        self.task: asyncio.Task | None = None

    def set_status(self, s: str) -> None:
        if s != self.status:
            self.status = s
            self.host.agent_status(s)

    def submit(self, text: str) -> None:
        self.q.put_nowait(text)

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        while True:
            text = await self.q.get()
            self.set_status("working")
            try:
                await self.turn(text)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — an adapter bug costs one turn, never serve
                self.host.agent_notice(f"Agent 出错（{type(e).__name__}），这条没有完成。")
            self.host.agent_turn_end()
            if self.q.empty():
                self.set_status("down" if self.is_down() else "idle")

    def is_down(self) -> bool:
        return False

    def perm_lost(self) -> None:
        """The permission tool's connection to serve dropped (ClaudeAgent restarts; others have none)."""

    def launch_argv(self, argv: list[str]) -> list[str] | None:
        """argv inside the fence (or as is when the human chose --unfenced); None + a notice when the fence cannot start."""
        if not self.cfg.get("fence", True):
            return argv
        why = fence.problem(self.host.st, self.cfg["dir"])
        if why:
            self.host.st.log("agent_fence_fail", agent=self.kind, reason=why)
            self.host.agent_notice(f"{fence.REASONS.get(why, why)}。为了安全，Agent 没有启动。"
                                   "在这台电脑的终端运行 `jarvis agent " + self.kind + " --unfenced` 才能不隔离运行（不推荐）。")
            return None
        return fence.wrap(self.host.st, argv, self.cfg["dir"])

    async def turn(self, text: str) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task


def source_root() -> str | None:
    """The directory holding `jarvis_host/` when it is a source checkout; None when the package is installed in a venv's
    site-packages (`uv tool` / pipx / pip): then the venv's interpreter finds it alone and nothing extra goes into the
    agent's environment (L3)."""
    import site
    root = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    installed = {os.path.realpath(p) for p in site.getsitepackages() + [site.getusersitepackages()]}
    return None if root in installed else root


def _bin(env: str, name: str) -> str | None:
    return os.environ.get(env) or shutil.which(name)


class ClaudeAgent(Agent):
    """One long-lived `claude -p --input-format stream-json` process; restarted with --resume <session id> if it exits."""
    kind = "claude"

    def __init__(self, host, cfg: dict):
        super().__init__(host, cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.turn_done: asyncio.Event | None = None
        self.got_init = False
        self.err_tail = ""
        self.failed_start = False

    def is_down(self) -> bool:
        return self.failed_start

    def argv(self, resume: str | None) -> list[str]:
        # the running venv's own interpreter + the installed module (L3); -P: the agent's folder (Claude Code's cwd) is
        # never put on sys.path, so a `jarvis_host/` or `json.py` planted there cannot stand in for the permission tool
        mcp = {"mcpServers": {"agentjarvis": {"type": "stdio", "command": sys.executable,
                                              "args": ["-P", "-m", "jarvis_host.permtool"]}}}
        a = [_bin("AGENTJARVIS_CLAUDE_BIN", "claude") or "claude", "-p", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose", "--permission-prompt-tool", PERM_TOOL,
             "--disallowedTools", PERM_TOOL, "--mcp-config", json.dumps(mcp)]
        if self.cfg.get("model"):
            a += ["--model", self.cfg["model"]]
        if resume:
            a += ["--resume", resume]
        return a

    async def _spawn(self) -> bool:
        exe = _bin("AGENTJARVIS_CLAUDE_BIN", "claude")
        if not exe:
            self.failed_start = True
            self.host.agent_notice("这台电脑上没找到 Claude Code（claude 命令）。装好并登录后再试。")
            return False
        argv = self.launch_argv(self.argv(self.host.st.agent_session(self.kind)))
        if argv is None:
            self.failed_start = True
            return False
        env = dict(os.environ)
        env.update(self.host.new_perm_env())
        src = source_root()
        if src:   # a source checkout (launcher + PYTHONPATH): the tool must import this same package
            env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["MCP_TOOL_TIMEOUT"] = str(int((self.host.ask_ttl + 60) * 1000))   # never cut a pending approval short
        sid = self.host.st.agent_session(self.kind)
        self.proc = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=env,
                                                         stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT,
                                                         start_new_session=True)
        self.got_init, self.failed_start, self.err_tail = False, False, ""
        self.reader = asyncio.create_task(self._read(self.proc, resumed=bool(sid)))
        asyncio.create_task(self._drain_err(self.proc))
        self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True))
        # no message before the permission tool holds serve's socket: by then the one-time token is spent (L2)
        proc = self.proc
        try:
            await asyncio.wait_for(self.host.perm_claimed.wait(), CLAIM_WAIT)
        except asyncio.TimeoutError:
            self.host.st.log("agent_no_claim", agent=self.kind)
            self.host.agent_notice("Claude Code 的批准通道没有接上：为了安全，这条消息没有交给它。")
            await self._kill(proc)
            return False
        if proc.returncode is not None or self.proc is not proc:
            return False
        return True

    def perm_lost(self) -> None:
        p = self.proc
        if p and p.returncode is None:
            self.host.agent_notice("Claude Code 的批准通道断了：为了安全，已重启 Claude Code。")
            asyncio.create_task(self._kill(p))

    async def _kill(self, p) -> None:
        if self.proc is p:
            self.proc = None
        if p.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                p.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            with contextlib.suppress(ProcessLookupError):
                p.kill()

    async def _drain_err(self, proc) -> None:
        with contextlib.suppress(Exception):
            while line := await proc.stderr.readline():
                self.err_tail = (self.err_tail + line.decode("utf-8", "replace"))[-2000:]

    async def _read(self, proc, resumed: bool) -> None:
        try:
            while line := await proc.stdout.readline():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    self.on_event(ev)
        except Exception:  # noqa: BLE001
            pass
        code = await proc.wait()
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        if self.proc is proc:
            self.proc = None
        if resumed and not self.got_init:          # a stale session id: start a fresh conversation next time
            self.host.st.set_agent_session(self.kind, None)
        if self.turn_done and not self.turn_done.is_set():
            last = clean(self.err_tail.strip().splitlines()[-1], 200) if self.err_tail.strip() else ""
            self.host.agent_notice(f"Claude Code 退出了（{code}）" + (f"：{last}" if last else ""))
            self.turn_done.set()

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            self.got_init = True
            sid = ev.get("session_id")
            if isinstance(sid, str):
                self.host.st.set_agent_session(self.kind, sid)
        elif t == "assistant":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    self.host.agent_text(block["text"])
        elif t == "result":
            if ev.get("is_error") or ev.get("subtype") not in ("success", None):
                r = ev.get("result")
                self.host.agent_notice("Agent 这一轮没有正常完成" + (f"：{clean(r, 300)}" if isinstance(r, str) and r else "。"))
            if self.turn_done:
                self.turn_done.set()

    async def turn(self, text: str) -> None:
        for attempt in range(2):
            if self.proc is None and not await self._spawn():
                return
            self.turn_done = asyncio.Event()
            line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n"
            try:
                self.proc.stdin.write(line.encode())
                await self.proc.stdin.drain()
            except (ConnectionError, AttributeError):
                self.proc = None
                continue
            await self.turn_done.wait()
            if self.got_init or attempt:   # a resumed process that died before init is retried once, fresh
                return

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        self.proc = None
        if p and p.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                p.stdin.close()
                p.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            with contextlib.suppress(ProcessLookupError):
                p.kill()


class CodexAgent(Agent):
    """One `codex exec --json` per message, continuing the thread with `codex exec resume <thread id>`. Text only:
    Codex's own approval policy (from the human's config) decides what runs; nothing is relayed for approval in v1."""
    kind = "codex"

    def __init__(self, host, cfg: dict):
        super().__init__(host, cfg)
        self.proc = None
        self.failed_start = False

    def is_down(self) -> bool:
        return self.failed_start

    def argv(self, tid: str | None) -> list[str]:
        exe = _bin("AGENTJARVIS_CODEX_BIN", "codex") or "codex"
        a = [exe, "exec"] + (["resume"] if tid else []) + ["--json", "--skip-git-repo-check"]
        if self.cfg.get("model"):
            a += ["-m", self.cfg["model"]]
        return a + ([tid] if tid else []) + ["-"]

    async def turn(self, text: str) -> None:
        if not _bin("AGENTJARVIS_CODEX_BIN", "codex"):
            self.failed_start = True
            self.host.agent_notice("这台电脑上没找到 Codex（codex 命令）。装好并登录后再试。")
            return
        tid = self.host.st.agent_session(self.kind)
        argv = self.launch_argv(self.argv(tid))
        if argv is None:
            self.failed_start = True
            return
        self.failed_start = False
        self.proc = p = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=dict(os.environ),
                                                             stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                             stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT,
                                                             start_new_session=True)
        self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True))
        err = asyncio.create_task(p.stderr.read())
        p.stdin.write(text.encode())
        await p.stdin.drain()
        p.stdin.close()
        started = False
        while line := await p.stdout.readline():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if t == "thread.started" and isinstance(ev.get("thread_id"), str):
                started = True
                self.host.st.set_agent_session(self.kind, ev["thread_id"])
            elif t == "item.completed":
                it = ev.get("item") if isinstance(ev.get("item"), dict) else {}
                if it.get("type") == "agent_message" and isinstance(it.get("text"), str):
                    self.host.agent_text(it["text"])
            elif t in ("turn.failed", "error"):
                e = ev.get("error") if isinstance(ev.get("error"), dict) else ev
                m = e.get("message")
                self.host.agent_notice("Codex 这一轮没有正常完成" + (f"：{clean(m, 300)}" if isinstance(m, str) else "。"))
        code = await p.wait()
        tail = (await err).decode("utf-8", "replace").strip()
        self.proc = None
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        if code != 0:
            if tid and not started:
                self.host.st.set_agent_session(self.kind, None)
            last = clean(tail.splitlines()[-1], 200) if tail else ""
            self.host.agent_notice(f"Codex 退出了（{code}）" + (f"：{last}" if last else ""))

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        if p and p.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                p.terminate()


def make(host, cfg: dict | None):
    if not cfg:
        return None
    if cfg["kind"] == "claude":
        return ClaudeAgent(host, cfg)
    if cfg["kind"] == "codex":
        return CodexAgent(host, cfg)
    return None
