"""P305: a generic Agent Client Protocol (ACP v1, https://agentclientprotocol.com) adapter — one long-lived `<agent> acp`
(JSON-RPC 2.0, one JSON object per line on stdio) per Agent J host; Kimi Code CLI (agent_kimi.py) is the first harness on it,
Qwen Code (agent_qwen.py) the second, DeepSeek Harness (`dsh --profile acp`, agent_dsh.py, P329) the third.

What serve does:
- the harness runs inside the fence (fence.py) like the others; `initialize` with NO client capabilities for files or
  terminals — the agent reads, writes and runs commands itself, inside the fence, never through serve (which runs outside it);
- one conversation = one ACP session (id in `agent.json`): `session/resume` (no history replay), else `session/load`, else
  `session/new`; each phone message one `session/prompt`, whose answer (`stopReason`) ends the turn. Text arrives as
  `agent_message_chunk` updates; it is collected and sent to the phone in whole pieces — at every tool call and at the end of
  the turn (no character-by-character pushes);
- **every approval is ours**: the session is put in the harness's manual mode (`MODE_DEFAULT`) on every open, so it asks
  before anything that is not read-only; each `session/request_permission` → `Host.ask` (the danger list, the signed phone
  card, 120 s default deny, batch for low risk) → the `allow_once` option or the `reject_once` option. Never an `allow_always`
  option (it would install a session-wide rule the phone never saw). A harness's own hooks are never relied on (Kimi's fail
  open on error / timeout) — the permission request is the gate;
- a permission request with several `allow_once` options is a question (Kimi's AskUserQuestion: one option per answer and a
  `reject_once` "Skip") → the §10.7 question card; nobody answers → the reject option;
- the stop switch and `/stop` send `session/cancel`; `/model` uses the session's `model` config option
  (`session/set_config_option`); `/context` the last `usage_update`.
The identity (main_identity.prompt) rides the first prompt of each process's session as an embedded `resource` block, the
per-turn reminder as one more (ACP has no system-prompt field; the harness shows them to the model as attached context).
"""
from __future__ import annotations

from . import resource_policy

import asyncio
import contextlib
import json
import os
import signal
import time

from . import bg, danger, slash
from . import agent as _agentmod
from .agent import LINE_LIMIT, Agent, _signal_tree
from .slash import Result
from .text import clean, clean_line

START_WAIT = 60          # s for initialize / session answers
CALL_WAIT = 60
PROTOCOL = 1
AUTH_REQUIRED = -32000   # ACP `auth_required`
IDENTITY_URI = "agentj://identity"
REMINDER_URI = "agentj://turn-reminder"
FILE_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit")
KNOWN = (*FILE_TOOLS, "Bash", "WebFetch", "WebSearch", "Glob", "Grep", "Delete")
KIND_TOOL = {"execute": "Bash", "edit": "Edit", "delete": "Delete", "move": "Edit", "fetch": "WebFetch", "read": "Read"}


class RPCError(Exception):
    def __init__(self, err):
        super().__init__(json.dumps(err, ensure_ascii=False)[:4000])
        self.err = err if isinstance(err, dict) else {}

    @property
    def code(self):
        return self.err.get("code")


def err_text(err) -> str:
    """An ACP error's words (message + data), read only to classify / show one bounded line."""
    err = err if isinstance(err, dict) else {}
    data = err.get("data")
    return str(err.get("message") or "") + (("\n" + clean(json.dumps(data, ensure_ascii=False), 500)) if data else "")


def tool_of(title, kind, raw) -> tuple[str, dict]:
    """An ACP tool call → (tool, input) in the names the danger list knows (Claude Code's). Kimi already uses them (Bash,
    Write, Edit, Read …, `path` for the file); another agent's names are mapped by the ACP `kind`."""
    name = clean_line(str(title or ""), 64)
    inp = dict(raw) if isinstance(raw, dict) else ({"input": raw} if raw not in (None, "") else {})
    if name not in KNOWN:
        mapped = KIND_TOOL.get(kind) if isinstance(kind, str) else None
        if mapped and (mapped != "Bash" or any(isinstance(inp.get(k), str) for k in ("command", "cmd", "commandLine"))):
            name = mapped
    if name in FILE_TOOLS or name == "Delete":
        if not isinstance(inp.get("file_path"), str):
            for k in ("path", "filePath", "file", "abs_path"):
                if isinstance(inp.get(k), str):
                    inp["file_path"] = inp[k]
                    break
    if name == "Bash" and not isinstance(inp.get("command"), str):
        for k in ("cmd", "commandLine"):
            if isinstance(inp.get(k), str):
                inp["command"] = inp[k]
    if name == "WebFetch" and not isinstance(inp.get("url"), str) and isinstance(inp.get("uri"), str):
        inp["url"] = inp["uri"]
    return name or "?", inp


def content_text(content, limit: int = 2000) -> str:
    """The text pieces of ACP tool-call content (diffs as old → new), for a card that has nothing better."""
    out = []
    for c in content if isinstance(content, list) else []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "content" and isinstance(c.get("content"), dict) and isinstance(c["content"].get("text"), str):
            out.append(c["content"]["text"])
        elif c.get("type") == "diff" and isinstance(c.get("path"), str):
            out.append(f"{c['path']}\n- {str(c.get('oldText') or '')[:400]}\n+ {str(c.get('newText') or '')[:400]}")
    return clean("\n".join(out), limit)


def pick_options(options) -> tuple[str | None, str | None, list[dict]]:
    """(allow_once id, reject id, the allow_once options). Never an allow_always id: a phone answer is for this one call."""
    opts = [o for o in options or [] if isinstance(o, dict) and isinstance(o.get("optionId"), str)]
    allow = [o for o in opts if o.get("kind") == "allow_once"]
    rej = next((o["optionId"] for o in opts if o.get("kind") == "reject_once"), None) or \
        next((o["optionId"] for o in opts if o.get("kind") == "reject_always"), None)
    return (allow[0]["optionId"] if len(allow) == 1 else None), rej, allow


def selected(option_id: str | None) -> dict:
    return {"outcome": {"outcome": "selected", "optionId": option_id}} if option_id else {"outcome": {"outcome": "cancelled"}}


class AcpAgent(Agent):
    """One long-lived ACP agent process; one session per conversation; approvals to the phone."""
    kind = "acp"
    LABEL = "ACP agent"
    BIN_ENV, BIN_NAME = "", ""
    MODE_DEFAULT = "default"         # the harness's "ask before anything not read-only" mode (session/set_mode)
    MODES_LOOSER = ()                # modes that approve by themselves: never kept
    TOOL_ALIAS: dict = {}            # P329: the harness's own tool titles → the danger list's names (dsh: "bash" → "Bash")
    LOGIN_ERRORS: tuple = ()         # P329: error texts that mean "no / bad model key" (dsh has no ACP auth_required)

    def __init__(self, host, cfg: dict, persist: bool = True, research: bool = False):
        if not persist:
            cfg = dict(cfg)
            cfg["_workflow_ceo"] = True
        super().__init__(host, cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self.failed_start = False
        self.persist, self.research = persist, research
        self.n = 0
        self.waits: dict[int, asyncio.Future] = {}
        self.pending: dict = {}                 # server request id → "gone" future
        self.tools: dict[str, dict] = {}        # toolCallId → {"title", "kind", "raw"} from tool_call updates
        self.sid: str | None = None             # the session loaded in this process
        self.session: dict = {}                 # configOptions / modes of the open session
        self.agent_info: dict = {}
        self.caps: dict = {}
        self.auth_methods: list = []
        self.err_tail = ""
        self.buf: list[str] = []                # agent_message_chunk text of the running turn, not yet sent
        self.collect: list | None = None
        self.quiet = False
        self.prompt_fut: asyncio.Future | None = None
        self.stop_reason: str | None = None
        self.usage: dict | None = None          # last usage_update {"used", "size"}
        self.commands: list[str] = []           # available_commands_update names
        self.identity_sent = False
        self.loading = False                    # session/load replays history as updates: none of it is news
        self.tasks: set[asyncio.Task] = set()

    # ------------------------------------------------ what a subclass provides
    def argv(self) -> list[str]:
        return [_agentmod._bin(self.BIN_ENV, self.BIN_NAME) or self.BIN_NAME, "acp"]

    def child_env(self, env: dict) -> dict:
        """The harness's own login variables on top of the fenced environment (Kimi: an API key from the key card)."""
        return env

    def login_hint(self) -> str:
        return f"{self.LABEL} 没登录。"

    def spawn_cwd(self) -> str:
        """The process's own folder (P329: dsh reads `<process cwd>/.env`, so it starts elsewhere; sessions keep cfg["dir"])."""
        return self.cfg["dir"]

    def is_down(self) -> bool:
        return self.failed_start

    def _bg(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        t.add_done_callback(lambda t: t.cancelled() or t.exception() is None or
                            bg.swallowed(bg.host_log(self.host), "bg_task_failed", t.exception(), why=self.kind, every=0))

    # ------------------------------------------------ JSON-RPC
    def _send(self, obj: dict) -> None:
        p = self.proc
        if p is None or p.stdin is None or p.returncode is not None:
            raise ConnectionError(f"{self.kind} acp is not running")
        p.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())

    async def request(self, method: str, params: dict) -> asyncio.Future:
        """Write one request; → the future of its answer (the write is the "delivered" moment for session/prompt)."""
        self.n += 1
        i = self.n
        f = asyncio.get_running_loop().create_future()
        f._agentj_id = i
        self.waits[i] = f
        try:
            self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
            await self.proc.stdin.drain()
        except BaseException:
            self.waits.pop(i, None)
            raise
        return f

    async def result(self, f: asyncio.Future, timeout: float | None):
        try:
            m = await (asyncio.wait_for(f, timeout) if timeout else f)
        finally:
            self.waits.pop(getattr(f, "_agentj_id", None), None)
        if "error" in m:
            raise RPCError(m["error"])
        return m.get("result")

    async def call(self, method: str, params: dict, timeout: float = CALL_WAIT):
        return await self.result(await self.request(method, params), timeout)

    def notify(self, method: str, params: dict) -> None:
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _answer(self, rid, result: dict | None = None, error: dict | None = None) -> None:
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            self._send({"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})})

    # ------------------------------------------------ start
    async def _spawn(self) -> bool:
        if not _agentmod._bin(self.BIN_ENV, self.BIN_NAME):
            self.failed_start = True
            self.fail_notice(f"这台Agent J 主机上没找到 {self.LABEL}（{self.BIN_NAME} 命令）。装好并登录后再试。")
            return False
        argv = await self.authorized_launch_argv(self.argv())
        if argv is None:
            self.failed_start = True
            return False
        from .proxy import environment
        from . import fence
        env = environment(os.environ, self.host.preferences)
        if self.cfg.get("_workflow_ceo"):
            env = fence.auxiliary_environment(env, self.kind, plan_keys=True)   # P348
        if self.cfg.get("fence", True):
            env = fence.launch_environment(env, self.cfg.get("docker", False), self.kind)
        env = self.child_env(env)
        self.proc = p = await asyncio.create_subprocess_exec(*resource_policy.work_argv(argv), cwd=self.spawn_cwd(), env=env,
                                                             stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                             stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT,
                                                             start_new_session=True)
        self.failed_start, self.err_tail, self.sid, self.session = False, "", None, {}
        self.waits, self.pending, self.identity_sent = {}, {}, False
        self.fresh = True
        self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True))
        self._bg(self._read(p))
        self._bg(self._drain_err(p))
        try:
            init = await self.call("initialize", {
                "protocolVersion": PROTOCOL,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                "clientInfo": {"name": "agentj", "title": "Agent J", "version": "1"}}, START_WAIT)
            init = init if isinstance(init, dict) else {}
            info = init.get("agentInfo") if isinstance(init.get("agentInfo"), dict) else {}
            self.agent_info = {"name": clean_line(str(info.get("name") or self.LABEL), 60),
                               "version": clean_line(str(info.get("version") or ""), 40)}
            self.caps = init.get("agentCapabilities") if isinstance(init.get("agentCapabilities"), dict) else {}
            self.auth_methods = [m for m in init.get("authMethods") or [] if isinstance(m, dict)]
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            self.host.st.log("agent_prepare_fail", agent=self.kind, reason=type(e).__name__)
            await self._kill(p)
            self.failed_start = True
            last = self._last_err()
            self.fail_notice(f"{self.LABEL}（acp）没有启动" + (f"：{last}" if last else f"（{type(e).__name__}）。"))
            return False
        return self.proc is p

    def _session_caps(self) -> dict:
        s = self.caps.get("sessionCapabilities")
        return s if isinstance(s, dict) else {}

    async def _open(self) -> bool:
        """The conversation's session open in this process (resume the stored one, else a new one)."""
        want = self.host.st.agent_session(self.kind) if self.persist else None
        if self.sid and (want == self.sid or not self.persist):
            return True
        cwd = self.cfg["dir"]
        res = None
        if want:
            for method in (("session/resume",) if "resume" in self._session_caps() else ()) + \
                          (("session/load",) if self.caps.get("loadSession") else ()):
                self.loading = method == "session/load"
                try:
                    res = await self.call(method, {"sessionId": want, "cwd": cwd, "mcpServers": []}, START_WAIT)
                    res = res if isinstance(res, dict) else {}
                    res.setdefault("sessionId", want)
                    break
                except RPCError as e:
                    if e.code == AUTH_REQUIRED:
                        raise
                    res = None
                finally:
                    self.loading = False
            if res is None:
                self.host.st.set_agent_session(self.kind, None)       # gone: a fresh conversation
        if res is None:
            res = await self.call("session/new", {"cwd": cwd, "mcpServers": []}, START_WAIT)
        if not isinstance(res, dict) or not isinstance(res.get("sessionId"), str):
            raise RPCError({"message": "no session"})
        self.sid = res["sessionId"]
        self.session = {"configOptions": res.get("configOptions") if isinstance(res.get("configOptions"), list) else [],
                        "modes": res.get("modes") if isinstance(res.get("modes"), dict) else {}}
        self.usage, self.identity_sent = None, False
        await self._strict_mode()
        if self.persist:
            self.host.st.set_agent_session(self.kind, self.sid)
            if not self.cfg.get("_workflow_ceo"):
                from . import main_identity
                main_identity.audit(self.cfg, self.kind, self.host.st, self.sid)
            self.refresh_models_now()
            m = self.cur_model()
            self.meter(model=m, model_name=self.model_name(m), effort=self.cur_effort())
        await self._apply_choice()
        return True

    def _mode(self) -> str | None:
        cur = (self.session.get("modes") or {}).get("currentModeId")
        if isinstance(cur, str):
            return cur
        o = self._option("mode")
        return o.get("currentValue") if o else None

    async def _strict_mode(self) -> None:
        """Approvals only ever get stricter: whatever mode the owner's settings start a session in, this one asks.
        MODE_DEFAULT None (P329, dsh): the harness has no ACP modes — its subclass makes the process itself ask."""
        if self.MODE_DEFAULT is None or self._mode() == self.MODE_DEFAULT:
            return
        try:
            await self.call("session/set_mode", {"sessionId": self.sid, "modeId": self.MODE_DEFAULT})
        except RPCError:
            await self.call("session/set_config_option", {"sessionId": self.sid, "configId": "mode", "value": self.MODE_DEFAULT})
        modes = self.session.setdefault("modes", {})
        modes["currentModeId"] = self.MODE_DEFAULT

    async def _ready(self) -> bool:
        if self.proc is None and not await self._spawn():
            return False
        try:
            return await self._open()
        except RPCError as e:
            if e.code == AUTH_REQUIRED:
                self._login_needed(err_text(e.err))
            else:
                self.fail_notice(f"{self.LABEL} 没能打开这段对话（{clean(str(e), 4000)}）。")
            return False
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            self.fail_notice(f"{self.LABEL} 没能打开这段对话（{type(e).__name__}）。")
            return False

    def quota_hint(self) -> str:
        """P423 #138: the line for a used-up quota / usage limit — never 「没登录」."""
        if getattr(self.host, "lang", "zh") == "en":
            return (f"{self.LABEL}'s quota / usage limit is used up — this is not a sign-in problem, no new login needed. Renew or "
                    "add usage with the provider, then send again; or switch to another Agent for now (/switch-harness).")
        return (f"{self.LABEL} 的额度 / 用量已用完——不是没登录，不用重新登录。到服务商处续费或加量后再发，"
                "或先切到别的 Agent（菜单 /switch-harness）。")

    def _limit_hit(self, detail: str) -> bool:
        """P423 #138: a used-up quota — even one sent as auth_required (Kimi's monthly limit) — never starts the login
        rescue: the rescue only records it (no dot), the owner gets the quota line."""
        from . import auth_rescue
        if not auth_rescue.quota_used(detail):
            return False
        fn = getattr(self.host, "auth_rescue", None)
        if fn is not None and not self.cfg.get("_workflow_ceo"):
            with contextlib.suppress(Exception):
                fn.signal(self.kind, "balance")
        self.fail_notice(self.quota_hint())
        return True

    def _login_needed(self, detail: str = "") -> None:
        if self._limit_hit(detail):
            return
        fn = getattr(self.host, "auth_rescue", None)
        if fn is not None and not self.cfg.get("_workflow_ceo"):
            with contextlib.suppress(Exception):
                fn.signal(self.kind, "login")
        self.fail_notice(self.login_hint())

    # ------------------------------------------------ the model / effort pill (config options)
    def _option(self, cid: str) -> dict | None:
        for o in self.session.get("configOptions") or []:
            if isinstance(o, dict) and (o.get("id") == cid or o.get("category") == {"thinking": "thought_level"}.get(cid, cid)):
                return o
        return None

    def _values(self, cid: str) -> list[dict]:
        o = self._option(cid) or {}
        out = []
        for v in o.get("options") or []:
            if isinstance(v, dict) and isinstance(v.get("value"), str):
                out.append(v)
            elif isinstance(v, dict) and isinstance(v.get("options"), list):        # grouped select
                out += [x for x in v["options"] if isinstance(x, dict) and isinstance(x.get("value"), str)]
        return out

    def cur_model(self) -> str | None:
        o = self._option("model") or {}
        m = self.cfg.get("model") or o.get("currentValue")
        return m if isinstance(m, str) and m else None

    def cur_effort(self) -> str | None:
        o = self._option("thinking") or {}
        e = self.cfg.get("effort") or o.get("currentValue")
        return e if isinstance(e, str) and e else None

    def refresh_models_now(self) -> None:
        efforts = [v["value"] for v in self._values("thinking") if len(v["value"]) <= 16] or None
        self.models_cache = [{"id": clean_line(v["value"], 100), "name": clean_line(str(v.get("name") or v["value"]), 60),
                              "efforts": efforts, "default": False}
                             for v in self._values("model") if slash.MODEL_RE.match(v["value"])][:40]
        fn = getattr(self.host, "models_changed", None)
        if fn:
            fn()

    async def refresh_models(self) -> None:
        if self.proc is None or not self.sid:
            return
        self.refresh_models_now()

    async def _set_option(self, cid: str, value: str) -> None:
        res = await self.call("session/set_config_option", {"sessionId": self.sid, "configId": cid, "value": value})
        if isinstance(res, dict) and isinstance(res.get("configOptions"), list):
            self.session["configOptions"] = res["configOptions"]
        else:
            o = self._option(cid)
            if o is not None:
                o["currentValue"] = value

    async def _apply_choice(self) -> None:
        """The owner's stored model / effort (config.json) on the open session; a value the harness does not offer is left."""
        for cid, want in (("model", self.cfg.get("model")), ("thinking", self.cfg.get("effort"))):
            o = self._option(cid)
            if not want or not o or o.get("currentValue") == want or want not in {v["value"] for v in self._values(cid)}:
                continue
            with contextlib.suppress(RPCError, ConnectionError, OSError, asyncio.TimeoutError):
                await self._set_option(cid, want)

    async def apply_model(self, model: str | None, effort: str | None, default: bool = False) -> str | None:
        why = await super().apply_model(model, effort, default)
        if self.proc is not None and self.sid:
            await self._apply_choice()
        return why

    # ------------------------------------------------ the process
    def _last_err(self) -> str:
        t = self.err_tail.strip()
        if not t:
            return ""
        line = t.splitlines()[-1]
        with contextlib.suppress(ValueError, TypeError, AttributeError):   # Kimi logs JSON lines on stderr
            j = json.loads(line)
            line = str(j.get("msg") or "") + (f"：{j['error']}" if isinstance(j.get("error"), str) else "")
        return clean(line, 200)

    async def _drain_err(self, p) -> None:
        with contextlib.suppress(Exception):
            while line := await p.stderr.readline():
                self.err_tail = (self.err_tail + line.decode("utf-8", "replace"))[-2000:]

    async def _read(self, p) -> None:
        waits, pending = self.waits, self.pending
        try:
            while line := await p.stdout.readline():
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                if "method" in m and "id" in m:
                    self._on_request(m)
                elif "id" in m:
                    f = self.waits.get(m.get("id"))
                    if f and not f.done():
                        f.set_result(m)
                elif isinstance(m.get("method"), str):
                    self._on_note(m["method"], m.get("params") if isinstance(m.get("params"), dict) else {})
        except Exception as e:  # noqa: BLE001 — the link ends here; say so in host.log
            bg.swallowed(bg.host_log(self.host), "agent_read_failed", e, why=self.kind)
        code = await p.wait()
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        if self.proc is p:
            self.proc, self.sid = None, None
        quiet = p in self.ended
        self.ended.discard(p)
        for f in list(waits.values()):
            if not f.done():
                f.set_exception(ConnectionError(f"{self.kind} acp exited"))
        for f in list(pending.values()):                 # every open card is withdrawn (agent_gone)
            if not f.done():
                f.set_result(True)
        if self.turn_proc is p and not quiet and not self.halting and self.prompt_fut is not None:
            last = self._last_err()
            self.fail_notice(f"{self.LABEL} 退出了（{code}）" + (f"：{last}" if last else ""))

    async def _kill(self, p) -> None:
        if self.proc is p:
            self.proc, self.sid = None, None
        self.ended.add(p)
        if p.returncode is None:
            with contextlib.suppress(Exception):
                p.stdin.close()
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    # ------------------------------------------------ session/update
    def _emit(self, text: str) -> None:
        if self.collect is not None:
            self.collect.append(text)
        elif not self.quiet:
            self.host.agent_text(text)

    def flush(self) -> None:
        text = "".join(self.buf)
        self.buf = []
        if text.strip():
            self._emit(text.strip("\n"))

    def _on_note(self, method: str, p: dict) -> None:
        if method != "session/update" or self.loading:
            return
        if p.get("sessionId") != self.sid:
            return
        u = p.get("update") if isinstance(p.get("update"), dict) else {}
        kind = u.get("sessionUpdate")
        self.touch()
        if kind == "agent_message_chunk":
            c = u.get("content") if isinstance(u.get("content"), dict) else {}
            if c.get("type") == "text" and isinstance(c.get("text"), str) and self.prompt_fut is not None:
                self.buf.append(c["text"])
        elif kind == "tool_call":
            self.flush()                                 # the words before a tool step reach the phone before its card
            tid = u.get("toolCallId")
            if isinstance(tid, str):
                self.tools[tid] = {"title": u.get("title"), "kind": u.get("kind"), "raw": u.get("rawInput")}
                if len(self.tools) > 500:
                    self.tools.pop(next(iter(self.tools)))
        elif kind == "tool_call_update":
            t = self.tools.get(u.get("toolCallId"))
            if t is not None and u.get("rawInput") is not None:
                t["raw"] = u["rawInput"]
        elif kind == "usage_update":
            used, size = u.get("used"), u.get("size")
            if type(used) is int and used >= 0:
                self.usage = {"used": used, "size": size if type(size) is int and size > 0 else None}
                if self.persist and not self.research:
                    self.meter(ctx={"used": used, "max": self.usage["size"]})
        elif kind == "available_commands_update":
            self.commands = [clean_line(str(c.get("name")), 40) for c in u.get("availableCommands") or []
                             if isinstance(c, dict) and c.get("name")][:60]
        elif kind == "config_option_update":
            if isinstance(u.get("configOptions"), list):
                self.session["configOptions"] = u["configOptions"]
                if self.persist:
                    self.refresh_models_now()
        elif kind == "current_mode_update":
            mode = u.get("currentModeId")
            self.session.setdefault("modes", {})["currentModeId"] = mode
            if isinstance(mode, str) and mode in self.MODES_LOOSER:   # never keep a mode that approves by itself
                self.host.st.log("agent_mode_reset", agent=self.kind, mode=clean_line(mode, 20))
                self._bg(self._strict_mode())

    # ------------------------------------------------ requests from the agent
    def _on_request(self, m: dict) -> None:
        method, rid = m["method"], m["id"]
        self.touch()
        p = m.get("params") if isinstance(m.get("params"), dict) else {}
        if method == "session/request_permission" and p.get("sessionId") == self.sid:
            self.flush()
            self._bg(self._permission(rid, p))
        elif method == "session/request_permission":
            self._answer(rid, selected(None))
        else:   # fs/*, terminal/*, elicitation/*: capabilities we never declared
            self._answer(rid, error={"code": -32601, "message": "not supported by agentj"})

    async def _permission(self, rid, p: dict) -> None:
        tc = p.get("toolCall") if isinstance(p.get("toolCall"), dict) else {}
        allow, reject, allows = pick_options(p.get("options"))
        if allow is None and len(allows) >= 2:
            return await self._question(rid, tc, allows, reject)
        seen = self.tools.get(tc.get("toolCallId")) or {}
        title = tc.get("title") or seen.get("title")
        title = self.TOOL_ALIAS.get(title, title) if isinstance(title, str) else title
        raw = tc.get("rawInput") if tc.get("rawInput") is not None else seen.get("raw")
        tool, inp = tool_of(title, tc.get("kind") or seen.get("kind"), raw)
        if not inp:
            inp = {"description": content_text(tc.get("content")) or f"{self.LABEL} 要用 {tool}"}
        if allow is None:                        # nothing that approves only this call: never answer yes
            self._answer(rid, selected(reject))
            return
        await self._decide(rid, tool, inp, selected(allow), selected(reject), True)

    async def _decide(self, rid, tool: str, inp: dict, yes: dict, no: dict, batch: bool) -> None:
        gone = asyncio.get_running_loop().create_future()
        self.pending[rid] = gone
        ans = no
        try:
            if self.research:
                ok, _ = danger.readonly(tool, inp)
                if not ok:                   # a `mode: research` task: anything not read-only is refused outright
                    return
            name = clean_line(tool, 64) or "?"
            from .approval_jev import low_risk_pass
            r = ({"behavior": "allow"} if not self.research and low_risk_pass(self.host, self.cfg, name, inp, batch)
                 else await self.host.ask(name, inp, gone, batch=batch))
            if r.get("behavior") == "allow":
                ans = yes
        except Exception:  # noqa: BLE001 — fail closed
            ans = no
        finally:
            self.pending.pop(rid, None)
            if not gone.done():
                self._answer(rid, ans)

    async def _question(self, rid, tc: dict, allows: list[dict], reject: str | None) -> None:
        from .approvals import norm_questions
        text = content_text(tc.get("content"), 1000) or clean_line(str(tc.get("title") or ""), 200)
        card = norm_questions([{"question": text, "header": "", "multiSelect": False,
                                "options": [{"label": clean_line(str(o.get("name") or ""), 200), "description": ""} for o in allows]}])
        if card is None or self.research:
            self._answer(rid, selected(reject))
            if not self.research:
                self.local_only(f"这个问题需要在Agent J 主机上回答（{self.LABEL} 问的内容手机卡片放不下）。")
            return
        gone = asyncio.get_running_loop().create_future()
        self.pending[rid] = gone
        ans = selected(reject)
        try:
            outcome, picks = await self.host.question(card, gone, task=getattr(self.host, "task_label", None))
            if outcome == "answer" and picks and picks[0]:
                k = picks[0][0] - 1           # the signed card's option k is exactly the request's allow option k
                if 0 <= k < len(allows):
                    ans = selected(allows[k]["optionId"])
        except Exception:  # noqa: BLE001 — fail closed: nobody answered
            ans = selected(reject)
        finally:
            self.pending.pop(rid, None)
            if not gone.done():
                self._answer(rid, ans)

    # ------------------------------------------------ turns
    def _block(self, uri: str, mime: str, text: str) -> dict:
        """An embedded resource — or, when the agent says it takes none (P329: dsh's embeddedContext false; ACP: a client
        must not send what is not advertised), the same text as a plain text block that names its source."""
        pc = self.caps.get("promptCapabilities") if isinstance(self.caps.get("promptCapabilities"), dict) else {}
        if pc.get("embeddedContext") is False:
            return {"type": "text", "text": f"<{uri}>\n{text}\n</{uri}>"}
        return {"type": "resource", "resource": {"uri": uri, "mimeType": mime, "text": text}}

    def prompt_blocks(self, text: str) -> list[dict]:
        blocks = []
        if self.persist and not self.cfg.get("_workflow_ceo"):
            from . import main_identity
            if not self.identity_sent:
                blocks.append(self._block(IDENTITY_URI, "text/markdown", main_identity.prompt(self.cfg)))
            blocks.append(self._block(REMINDER_URI, "text/plain", main_identity.turn_reminder(self.cfg, True)))
        blocks.append({"type": "text", "text": text})
        return blocks

    async def reload_identity(self) -> None:
        """The identity text changed: the next prompt carries it again (no restart needed)."""
        self.identity_sent = False

    async def turn(self, text: str) -> None:
        if not await self._ready():
            return
        self.turn_proc, self.buf, self.stop_reason = self.proc, [], None
        params = {"sessionId": self.sid, "prompt": self.prompt_blocks(text)}
        try:
            self.prompt_fut = await self.deliver(lambda: self.request("session/prompt", params))
            self.identity_sent = True
        except (ConnectionError, OSError) as e:
            self.prompt_fut = None
            if not self.halting:
                self.fail_notice(f"{self.LABEL} 没有接这条消息（{type(e).__name__}）。")
            return
        try:
            r = await self.result(self.prompt_fut, None)
            self.stop_reason = (r or {}).get("stopReason") if isinstance(r, dict) else None
            self.flush()
            if self.stop_reason in ("max_tokens", "max_turn_requests", "refusal") and not self.halting:
                self.fail_notice({"max_tokens": f"{self.LABEL} 这一轮输出到了长度上限，没有说完。",
                                  "max_turn_requests": f"{self.LABEL} 这一轮的步骤数到了上限，停下了。",
                                  "refusal": f"{self.LABEL} 拒绝了这条请求。"}[self.stop_reason])
        except RPCError as e:
            self.flush()
            if self.halting:
                return
            detail = err_text(e.err)
            if e.code == AUTH_REQUIRED:
                self._login_needed(detail)       # P423 #138: a quota sent as auth_required ends in _limit_hit there
                return
            if self._limit_hit(detail):
                return
            from . import auth_rescue
            cls = "login" if any(w in detail for w in self.LOGIN_ERRORS) else auth_rescue.classify(self.kind, detail)
            if cls == "login":
                self._login_needed(detail)
                return
            self._auth_signal(detail)
            self.fail_notice(f"{self.LABEL} 这一轮没有正常完成" + (f"：{clean(detail, 4000)}" if detail.strip() else "。"))
        except (ConnectionError, OSError):
            self.flush()                     # the reader already said why (exit notice)
        finally:
            self.prompt_fut = None

    async def interrupt_request(self, p) -> None:
        """ACP's own `session/cancel`, then ≤ 2 s for the prompt to settle (halt() ends the process tree right after)."""
        f = self.prompt_fut
        if f is None or f.done() or not self.sid:
            return
        self.notify("session/cancel", {"sessionId": self.sid})
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(asyncio.shield(f), 2)

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        if p:
            await self._kill(p)
        for t in list(self.tasks):
            t.cancel()

    # ------------------------------------------------ a scheduled run (tasks.py): one throw-away session
    async def run_once(self, prompt: str) -> tuple[str, int | str]:
        self.collect = []
        try:
            if not await self._ready():
                return "", "no_start"
            await self.turn(prompt)
            st = self.stop_reason
            return "\n\n".join(self.collect), 0 if st == "end_turn" else (st or "?")
        finally:
            if self.proc:
                await self._kill(self.proc)
            for t in list(self.tasks):
                t.cancel()

    # ------------------------------------------------ slash commands (slash.py)
    async def drop_conversation(self) -> None:
        if self.sid and self.proc is not None and "close" in self._session_caps():
            with contextlib.suppress(RPCError, ConnectionError, OSError, asyncio.TimeoutError):
                await self.call("session/close", {"sessionId": self.sid}, 10)
        self.sid, self.usage = None, None

    async def native_command(self, line: str, timeout: float = 600) -> tuple[str, str | None]:
        """One of the harness's own slash commands (`/compact`, `/usage` …) sent as a prompt; → (its text, stopReason)."""
        if not await self._ready():
            return "", "no_start"
        saved, self.collect = self.collect, []
        self.buf = []
        try:
            f = await self.request("session/prompt", {"sessionId": self.sid, "prompt": [{"type": "text", "text": line}]})
            self.prompt_fut = f
            r = await self.result(f, timeout)
            self.flush()
            return "\n".join(self.collect), (r or {}).get("stopReason") if isinstance(r, dict) else None
        finally:
            self.collect, self.prompt_fut = saved, None

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        if "compact" not in self.commands and self.commands:
            return Result(f"{self.LABEL} 没有提供压缩命令。", "info")
        before = (self.usage or {}).get("used")
        self.set_status("compacting")
        t0 = time.monotonic()
        try:
            _, st = await self.native_command("/compact")
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            return Result(f"没有压缩：{clean(str(e), 4000) or type(e).__name__}", "error")
        finally:
            self.set_status("working")
        if st not in ("end_turn", None):
            return Result(f"没有压缩完（{self.LABEL} 报告：{st}）。", "error")
        after = (self.usage or {}).get("used")
        took = slash.secs((time.monotonic() - t0) * 1000)
        if isinstance(before, int) and isinstance(after, int) and after != before:
            return Result(f"已压缩：{slash.tokens(before)} → {slash.tokens(after)} tokens{took}")
        return Result(f"已压缩{took}。")

    async def cmd_context(self, arg: str) -> Result:
        u = self.usage if self.sid and self.sid == self.host.st.agent_session(self.kind) else None
        if not u:
            return Result("上下文：" + slash.NONE + f"（{self.LABEL} 在跑完一轮后才报告）", "info")
        return Result(f"上下文 {slash.tokens(u['used'])} / {slash.tokens(u['size'])}{slash.pct(u['used'], u['size'])}")

    async def cmd_cost(self, arg: str) -> Result:
        return Result(f"本会话花费：{slash.NONE}（{self.LABEL} 不按次报价；用量看 /usage）", "info")

    async def cmd_usage(self, arg: str) -> Result:
        if self.commands and "usage" not in self.commands:
            return Result("套餐用量：" + slash.NONE + f"（{self.LABEL} 没有用量命令）", "info")
        try:
            text, _ = await self.native_command("/usage", 60)
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError):
            text = ""
        return Result("用量（" + self.LABEL + " 报告）：\n" + clean(text, 3000) if text.strip() else
                      "套餐用量：" + slash.NONE + f"（{self.LABEL} 没有报告）", "ok" if text.strip() else "info")

    async def cmd_status(self, arg: str) -> Result:
        if self.proc is None:
            await self._spawn()
        lines = [f"Agent：{self.agent_info.get('name') or self.LABEL} {self.agent_info.get('version') or ''}".rstrip(),
                 f"对话：{self.host.st.agent_session(self.kind) or '（新对话）'}",
                 f"模型：{self.cur_model() or '默认'}",
                 f"目录：{self.cfg['dir']}",
                 f"审批：{self.LABEL} 问到的都上手机（会话固定在「每次都问」模式，只回「这一次」）；危险清单生效",
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        return Result("\n".join(lines))

    async def cmd_model(self, arg: str) -> Result:
        # Listing an open session's model options is local and never reopens it mid-turn.
        if not arg and not self.sid and self.status in ("working", "compacting"):
            from .command_queries import unavailable
            return unavailable(self)
        if (arg or not self.sid) and not await self._ready():
            return Result(f"{self.LABEL} 没起来，没法换模型。", "error")
        cur = self.cur_model() or ""
        models = [{"id": clean_line(v["value"], 100), "name": clean_line(str(v.get("name") or v["value"]), 60),
                   "desc": clean_line(str(v.get("description") or ""), 120), "cur": v["value"] == cur}
                  for v in self._values("model")]
        if not arg:
            return Result(f"当前模型：{cur or '默认'}。" + ("点一个切换（之后的对话都用它）：" if models else f"{self.LABEL} 没有给出可选列表。"),
                          "ok", models=models[:40])
        if not slash.MODEL_RE.match(arg) or (models and arg not in {m["id"] for m in models}):
            return Result(f"没有这个模型：{clean_line(arg, 100)}", "error")
        try:
            await self._set_option("model", arg)
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            return Result(f"没有切换（{clean(str(e), 120) or type(e).__name__}）。", "error")
        self.host.set_model(arg)
        self.meter(model=arg, model_name=self.model_name(arg))
        return Result(f"已切换到 {arg}：立即生效，写进了 config.json（重启后也用它）。")

    async def cmd_help(self, arg: str) -> Result:
        return Result(slash.HELP + (f"\n{self.LABEL} 自己的命令：" + " ".join("/" + c for c in self.commands[:40]) if self.commands else ""))
