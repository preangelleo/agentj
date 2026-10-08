"""Codex adapter (PROTOCOL §8, ADR-A70): the phone talks to the customer's own Codex through its official headless server,
`codex app-server` (JSON-RPC 2.0, one JSON object per line on stdio), with Codex's approval requests answered on the phone.

Why app-server and not `codex exec` (L1 – PROMPT-26 item 6): `exec` cannot ask anyone — whatever the human's approval policy
says, nothing reaches the phone (G-A25), so the danger list did not apply to Codex (G-A58). app-server sends
`item/commandExecution/requestApproval` / `item/fileChange/requestApproval` as JSON-RPC requests and waits for the answer.

What serve does (measured on Codex 0.159.2, `reports/design/v1-slash/`, `reports/qa/slash/`):
- one long-lived `codex app-server` inside the fence (fence.py, like the other harnesses; on macOS Codex's own sandbox cannot
  nest inside ours, G-A54), `initialize` + `initialized`, then `config/read {cwd}` (the human's effective settings);
- one conversation = one Codex thread (id in `agent.json`, the same key `codex exec` used, so an existing conversation goes
  on): `thread/resume {threadId, excludeTurns}` or `thread/start`, each phone message one `turn/start`; finished
  `agentMessage` items go to the phone; the turn ends with `turn/completed`;
- **approvals only get stricter** (Invariant 11): serve asks Codex for `approvalPolicy: "untrusted"` (Codex asks before every
  command it does not know to be read-only, and before every file change) and `approvalsReviewer: "user"` (a human decides, not
  Codex's automatic reviewer); a human whose own policy is `granular` keeps it (overriding it could loosen it). The sandbox and
  everything else stay the human's (`sandbox: "read-only"` is added only for a `mode: research` task). Each request →
  `Host.ask` (the danger list, the signed phone card, 120 s default deny, batch for low risk) → `"accept"` or `"decline"` — never
  `acceptForSession`, an execpolicy / network amendment, or a permission grant wider than the request (`scope: "turn"`);
- the stop switch and `/stop` use `turn/interrupt`; slash commands use `thread/compact/start`, `model/list`,
  `account/rateLimits/read` and the `thread/tokenUsage/updated` figures (slash.py).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import signal
import time

from . import compactprep, danger, slash
from . import agent as _agentmod
from .agent import LINE_LIMIT, Agent, _signal_tree
from .slash import Result
from .text import clean, clean_line

START_WAIT = 60          # s for initialize / config / thread answers
CALL_WAIT = 60
_SHELLS = ("bash", "sh", "zsh", "dash")


def unwrap_shell(cmd: str) -> str:
    """Codex runs `/usr/bin/bash -lc '<command>'`: the phone (and the batch scope) see the command itself."""
    try:
        w = shlex.split(cmd)
    except ValueError:
        return cmd
    if len(w) == 3 and os.path.basename(w[0]) in _SHELLS and w[1] in ("-lc", "-c", "-l -c"):
        return w[2]
    return cmd


def _diff_sides(diff: str) -> tuple[str, str]:
    old, new = [], []
    for ln in (diff or "").splitlines():
        if ln.startswith(("---", "+++", "@@")):
            continue
        if ln.startswith("-"):
            old.append(ln[1:])
        elif ln.startswith("+"):
            new.append(ln[1:])
    return "\n".join(old), "\n".join(new)


def patch_tool(changes) -> tuple[str, dict]:
    """A Codex `fileChange` item → (tool, input) in the names the danger list knows. One file: add → Write, update → Edit,
    delete → Delete. Several files: the riskiest one decides (a delete, else a credential path), every file is shown."""
    ch = [c for c in (changes or []) if isinstance(c, dict) and isinstance(c.get("path"), str)]
    if not ch:
        return "Edit", {"file_path": "?", "old_string": "", "new_string": "（Codex 没给出改动内容）"}

    def kind(c):
        k = c.get("kind")
        return k.get("type") if isinstance(k, dict) else k
    if len(ch) == 1:
        c = ch[0]
        k, diff = kind(c), str(c.get("diff") or "")
        if k == "add":
            return "Write", {"file_path": c["path"], "content": diff}
        if k == "delete":
            return "Delete", {"file_path": c["path"]}
        old, new = _diff_sides(diff)
        mv = (c.get("kind") or {}).get("move_path") if isinstance(c.get("kind"), dict) else None
        return "Edit", {"file_path": c["path"], "old_string": old, "new_string": new + (f"\n（移动到 {mv}）" if mv else "")}
    paths = [c["path"] for c in ch]
    dele = [c["path"] for c in ch if kind(c) == "delete"]
    pick = dele[0] if dele else next((p for p in paths if danger.cred_path(p)), paths[0])
    body = "files: " + ", ".join(f"{p}（{kind(c) or '?'}）" for p, c in zip(paths, ch)) + "\n" + \
        "\n".join(str(c.get("diff") or "")[:600] for c in ch)
    if dele:
        return "Delete", {"file_path": pick, "files": paths, "detail": body[:4000]}
    return "Edit", {"file_path": pick, "old_string": "", "new_string": body[:4000]}


def perm_text(p) -> str:
    """What an `item/permissions/requestApproval` asks for (filesystem / network), for the card."""
    p = p if isinstance(p, dict) else {}
    out = []
    fs = p.get("fileSystem") if isinstance(p.get("fileSystem"), dict) else {}
    for k in ("write", "read"):
        if isinstance(fs.get(k), list) and fs[k]:
            out.append(f"{'写入' if k == 'write' else '读取'}：" + ", ".join(str(x) for x in fs[k][:10]))
    for e in fs.get("entries") or []:
        if isinstance(e, dict):
            out.append(f"{e.get('access')}：{json.dumps(e.get('path'), ensure_ascii=False)[:200]}")
    net = p.get("network") if isinstance(p.get("network"), dict) else {}
    if net.get("enabled"):
        out.append("联网")
    return "；".join(out) or json.dumps(p, ensure_ascii=False)[:400]


# ---------------------------------------------------------------- never more power than the human's own sandbox (ADR-A73)
# Measured on Codex 0.159.2 (`reports/design/v1-slash/probe-codex-sandbox.txt`): under `approvalPolicy: "untrusted"` an
# accepted command runs OUTSIDE Codex's sandbox — `touch ~/x` succeeded in a workspace-write thread after "accept", and failed
# ("Read-only file system") under the same sandbox without approval. So a phone card may only be offered for what the human's
# own sandbox already allows anyway; everything else is declined without a card.
FULL = ("dangerFullAccess", "externalSandbox")
NET_CMDS = frozenset({"curl", "wget", "http", "https", "xh", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "ftp",
                      "ping", "dig", "nslookup", "host", "whois", "aria2c"})
POLICY_NOTICE = "这一步超出了你 Codex 自己的沙箱设置，已拒绝；要放开请在电脑上改 Codex 的设置。"
# F30 (ADR-A175): ADR-A73 now only bites when the owner explicitly restricted Codex (config.toml / the desktop thread)
CONFIG_NOTICE = ("（你在 config.toml 顶层写了 sandbox_mode = {mode}。想让 Agent 像在终端里一样直接跑：电脑上运行 "
                 "`agentj codex-sandbox default`，再 `agentj service restart`；同一段对话也会按新设置。）")
DESKTOP_NOTICE = ("这一步超出了这条 Codex 对话在桌面 App 里设的权限（{mode}），已拒绝；要放开请在 Codex 桌面 App 里把这条对话的权限"
                  "改成「完全访问」（Full access），然后从手机重发。")


def _roots(sb: dict, cwd: str) -> list[str]:
    roots = [cwd] + [r for r in sb.get("writableRoots") or [] if isinstance(r, str)]
    if not sb.get("excludeSlashTmp"):
        roots.append("/tmp")
    if not sb.get("excludeTmpdirEnvVar") and os.environ.get("TMPDIR", "").startswith("/"):
        roots.append(os.environ["TMPDIR"])
    return [os.path.realpath(r) for r in roots]


def _inside(path: str, roots: list[str], cwd: str) -> bool:
    real = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    parts = real.split(os.sep)
    if ".git" in parts or ".codex" in parts or ".agents" in parts:   # Codex keeps these read-only inside writable roots
        return False
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def beyond_sandbox(sandbox, method: str, tool: str, inp: dict, params: dict, changes, cwd: str) -> str | None:
    """None when approving this request cannot let Codex do more than its own sandbox lets it do without anyone (the card
    may be shown); else why not (declined without a card). Unknown sandbox = read-only (fail closed)."""
    sb = sandbox if isinstance(sandbox, dict) else {"type": "readOnly"}
    t = sb.get("type")
    if t in FULL:
        return None                                     # the human's Codex may already do everything: an approval adds nothing
    net = bool(sb.get("networkAccess") is True or (isinstance(sb.get("networkAccess"), str) and sb["networkAccess"] == "enabled"))
    if method == "item/permissions/requestApproval":
        return "要求放宽 Codex 的沙箱"
    if method == "item/fileChange/requestApproval":
        if t != "workspaceWrite":
            return "Codex 的沙箱是只读的，不能改文件"
        roots = _roots(sb, cwd)
        paths = [c.get("path") for c in changes or [] if isinstance(c, dict)]
        if not paths or not all(isinstance(x, str) and _inside(x, roots, cwd) for x in paths):
            return "改动的文件不在 Codex 沙箱允许写的目录里"
        return None
    if method == "item/commandExecution/requestApproval":
        if params.get("kind") == "writeStdin":
            return "向正在运行的命令输入（沙箱外）"
        if isinstance(params.get("networkApprovalContext"), dict) or params.get("additionalPermissions"):
            return "要求联网或额外权限"
        cmd = inp.get("command") if isinstance(inp.get("command"), str) else ""
        ok, _ = danger.readonly("Bash", {"command": cmd})
        if not ok:      # an approved command runs outside the sandbox: only one that just reads stays within its power
            return "批准后命令会在 Codex 沙箱外运行，而它不只是读取"
        names: list = []
        danger._bash(cmd, danger._Hits(), collect=names)
        if not net and any(getattr(x, "name", "") in NET_CMDS for x in names):
            return "Codex 的沙箱不允许联网"
        return None
    return "未知请求"


def question_card(qs) -> list | None:
    """Codex `item/tool/requestUserInput` questions → the §10.7 card (single choice each), or None when the phone cannot
    answer it: a secret question, one without options, or anything over the card's limits."""
    from .approvals import norm_questions
    if not isinstance(qs, list) or not qs:
        return None
    raw, ids = [], set()
    for q in qs:
        if not isinstance(q, dict) or q.get("isSecret") or not isinstance(q.get("id"), str) or q["id"] in ids \
                or not isinstance(q.get("options"), list) or not q["options"]:
            return None
        if not all(isinstance(o, dict) for o in q["options"]):
            return None      # P33-X04: never drop an entry — the card's option k must be exactly the request's option k
        ids.add(q["id"])
        raw.append({"question": q.get("question"), "header": q.get("header") or "", "multiSelect": False,
                    "options": [{"label": o.get("label"), "description": o.get("description") or ""} for o in q["options"]]})
    card = norm_questions(raw)
    return card if card is not None and len(card) == len(qs) else None


class RPCError(Exception):
    def __init__(self, err):
        super().__init__(json.dumps(err, ensure_ascii=False)[:4000])
        self.err = err if isinstance(err, dict) else {}


class CodexAgent(Agent):
    """One long-lived `codex app-server`; one thread per conversation; approvals to the phone."""
    kind = "codex"

    def __init__(self, host, cfg: dict, persist: bool = True, research: bool = False):
        if not persist:
            cfg = dict(cfg)
            cfg["_workflow_ceo"] = True
        super().__init__(host, cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self.failed_start = False
        self.persist, self.research = persist, research        # a scheduled run: a throw-away thread, read-only when research
        self.n = 0
        self.waits: dict[int, asyncio.Future] = {}
        self.pending: dict = {}                                # server request id → "gone" future (resolved elsewhere)
        self.items: dict[str, dict] = {}                       # item id → item (fileChange changes for the card)
        self.tid: str | None = None                            # the thread loaded in this app-server
        self.thread: dict = {}                                 # what thread/start|resume answered (model, sandbox, policy)
        self.turn_id: str | None = None
        self.turn_done: asyncio.Event | None = None
        self.turn_status: dict = {}
        self.usage: dict | None = None                         # last thread/tokenUsage/updated for self.tid
        self.rate: dict | None = None                          # last account/rateLimits/updated
        self.version = ""
        self.human: dict = {}                                  # config/read: approval_policy, approvals_reviewer, sandbox_mode
        self.err_tail = ""
        self.collect: list | None = None                       # run_once: texts are collected, not sent
        self.quiet = False                                     # compaction: no text to the phone
        self.tasks: set[asyncio.Task] = set()
        self.unavailable_models: set[str] = set()  # native account rejection; survives model refresh/reconnect

    def fail_notice(self, text: str) -> None:
        kind, notice, blocked = _agentmod.codex_failure(text, self.cur_model(), getattr(self.host, "lang", "zh"))
        if kind == "model" and blocked and slash.MODEL_RE.fullmatch(blocked):
            self.unavailable_models.add(blocked)
            for m in self.models_cache:
                m["disabled"] = m["id"] in self.unavailable_models
            fn = getattr(self.host, "models_changed", None)
            if fn:
                fn()
        super().fail_notice(notice)

    def is_down(self) -> bool:
        return self.failed_start

    def argv(self) -> list[str]:
        return [_agentmod._bin("AGENTJ_CODEX_BIN", "codex") or "codex", "app-server"]

    def _bg(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    # ------------------------------------------------ JSON-RPC
    def _send(self, obj: dict) -> None:
        p = self.proc
        if p is None or p.stdin is None or p.returncode is not None:
            raise ConnectionError("codex app-server is not running")
        p.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())

    async def call(self, method: str, params: dict, timeout: float = CALL_WAIT):
        self.n += 1
        i = self.n
        f = asyncio.get_running_loop().create_future()
        self.waits[i] = f
        try:
            self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
            await self.proc.stdin.drain()
            m = await asyncio.wait_for(f, timeout)
        finally:
            self.waits.pop(i, None)
        if "error" in m:
            raise RPCError(m["error"])
        return m.get("result")

    def _answer(self, rid, result: dict | None = None, error: dict | None = None) -> None:
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            self._send({"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})})

    # ------------------------------------------------ start
    def initialize_capabilities(self): return {}

    async def _spawn(self) -> bool:
        if not _agentmod._bin("AGENTJ_CODEX_BIN", "codex"):
            self.failed_start = True
            self.fail_notice("这台电脑上没找到 Codex（codex 命令）。装好并登录后再试。")
            return False
        argv = self.launch_argv(self.argv())
        if argv is None:
            self.failed_start = True
            return False
        from .proxy import environment
        self.proc = p = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=environment(os.environ, self.host.preferences),
                                                             stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                             stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT,
                                                             start_new_session=True)
        self.failed_start, self.err_tail, self.tid, self.thread = False, "", None, {}
        self.waits, self.pending = {}, {}                      # this process's calls / open cards (its reader fails them)
        from . import codex_procs                              # ADR-A178: a leftover is recognised (and ended) later
        codex_procs.record(self.host.st, p.pid)
        self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True))
        self._bg(self._read(p))
        self._bg(self._drain_err(p))
        try:
            init = await self.call("initialize", {"clientInfo": {"name": "agentj", "title": "Agent J",
                                                                "version": "1"}, "capabilities": self.initialize_capabilities()}, START_WAIT)
            self._send({"jsonrpc": "2.0", "method": "initialized"})
            ua = (init or {}).get("userAgent") if isinstance(init, dict) else None
            self.version = clean_line(ua.split("/", 1)[1].split(" ", 1)[0], 40) if isinstance(ua, str) and "/" in ua else ""
            cfg = await self.call("config/read", {"cwd": self.cfg["dir"]}, START_WAIT)
            c = (cfg or {}).get("config") if isinstance(cfg, dict) else None
            c = c if isinstance(c, dict) else {}
            from .model_limits import codex_context_metadata
            self.context_metadata = codex_context_metadata(c)
            self.human = {k: c.get(k) for k in ("approval_policy", "approvals_reviewer", "sandbox_mode", "model",
                                                "model_reasoning_effort")}
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            self.host.st.log("agent_prepare_fail", agent=self.kind, reason=type(e).__name__)
            await self._kill(p)
            self.failed_start = True
            last = self._last_err()
            self.fail_notice("Codex（app-server）没有启动" + (f"：{last}" if last else f"（{type(e).__name__}）。"))
            return False
        return self.proc is p

    def policy(self) -> dict:
        """What serve asks of a thread (start and resume alike, so a changed setting applies to the same conversation after
        a restart — Codex 0.159.2 keeps an old thread's sandbox on a resume that names none, ADR-A175).
        F14: inherit the owner's native approval policy/reviewer; do not impose untrusted.
        F30: the sandbox is the owner's explicit `sandbox_mode`, else `danger-full-access` (the main Agent runs directly on
        the system, like the owner's terminal; the danger list and phone cards still apply); research tasks read-only."""
        from . import codex_perm
        p = {}
        if self.cfg.get("high_risk_warnings", False):
            p["approvalsReviewer"] = "user"
            if not isinstance(self.human.get("approval_policy"), dict):
                p["approvalPolicy"] = "untrusted"
        elif isinstance(self.human.get("approval_policy"), (str, dict)):
            p["approvalPolicy"] = self.human["approval_policy"]   # named on resume too: a changed setting applies at once
        p["sandbox"], self.perm_source = codex_perm.main_sandbox(self.human.get("sandbox_mode"), self.research)
        if self.cfg.get("model"):
            p["model"] = self.cfg["model"]
        return p

    async def reload_identity(self) -> None:
        """A1: developerInstructions are sent on thread/start|resume: end the idle app-server; the next turn spawns it and
        resumes the same thread with the new identity text."""
        if self.persist:
            await self._end_proc()
            self.tid = None

    async def _thread(self) -> bool:
        """The conversation's thread loaded in this app-server (resume the stored one, else a new one)."""
        want = self.host.st.agent_session(self.kind) if self.persist else None
        if self.tid and (want == self.tid or not self.persist):
            return True
        base = {"cwd": self.cfg["dir"], **self.policy()}
        if self.persist and not self.cfg.get("_workflow_ceo"):
            from . import main_identity
            base["developerInstructions"] = main_identity.prompt(self.cfg)
        res = None
        if want:
            try:
                res = await self.call("thread/resume", {"threadId": want, "excludeTurns": True, **base})
            except RPCError:
                res = None
                self.host.st.set_agent_session(self.kind, None)          # gone: a fresh conversation
        if res is None:
            res = await self.call("thread/start", {**base, **({} if self.persist else {"ephemeral": True})})
        th = (res or {}).get("thread") if isinstance(res, dict) else None
        if not isinstance(th, dict) or not isinstance(th.get("id"), str):
            raise RPCError({"message": "no thread"})
        if self.persist and not self.cfg.get("_workflow_ceo"):
            main_identity.audit(self.cfg, self.kind, self.host.st, th["id"])
        self.tid = th["id"]
        self.thread = {k: res.get(k) for k in ("model", "sandbox", "approvalPolicy", "approvalsReviewer", "cwd",
                                               "reasoningEffort")}
        self.usage = None
        if self.persist:
            self.host.st.set_agent_session(self.kind, self.tid)
            m = self.cur_model()
            self.meter(model=m, model_name=self.model_name(m), effort=self.cur_effort())
            if not self.models_cache:
                self._bg(self.refresh_models())
        return True

    # ------------------------------------------------ model and effort pill (§10.11): turn/start model / effort
    def cur_model(self) -> str | None:
        m = self.cfg.get("model") or self.thread.get("model") or self.human.get("model") or next(
            (x["id"] for x in getattr(self, "models_cache", []) if x.get("default") is True), None)
        return m if isinstance(m, str) and m else None

    def model_name(self, model: str | None) -> str | None:
        return super().model_name(model) or model

    def cur_effort(self) -> str | None:
        e = self.cfg.get("effort") or self.thread.get("reasoningEffort") or self.human.get("model_reasoning_effort")
        return e if isinstance(e, str) and e else None

    async def refresh_models(self) -> None:
        if self.proc is None:
            return
        try:
            res = await self.call("model/list", {}, 20)
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError):
            return
        out = []
        for m in (res or {}).get("data") or []:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str) or m.get("hidden") or not slash.MODEL_RE.match(m["id"]):
                continue
            effs = []
            for e in m.get("supportedReasoningEfforts") or []:
                v = e.get("reasoningEffort") if isinstance(e, dict) else e
                if isinstance(v, str) and len(v) <= 16 and v not in effs:
                    effs.append(v)
            out.append({"id": clean_line(m["id"], 100), "name": clean_line(str(m.get("displayName") or m["id"]), 60),
                        "efforts": effs or None, "default": m.get("isDefault") is True, "disabled": m["id"] in self.unavailable_models})
        self.models_cache = out[:40]
        fn = getattr(self.host, "models_changed", None)
        if fn:
            fn()
        m = self.cur_model()
        self.meter(model=m, model_name=self.model_name(m))
        with contextlib.suppress(RPCError, ConnectionError, OSError, asyncio.TimeoutError):   # the quota meters, once
            res = await self.call("account/rateLimits/read", {}, 20)
            r = (res or {}).get("rateLimits") if isinstance(res, dict) else None
            if isinstance(r, dict):
                self.rate = r
                self.rate_meter(r)

    async def apply_model(self, model: str | None, effort: str | None, default: bool = False) -> str | None:
        """Codex keeps a turn's model / effort for the turns after it, so "default" sends the human's own configured values
        (config/read) once with the next turn/start."""
        target = self.human.get("model") if default else model
        if (target or self.cur_model()) in self.unavailable_models:
            return "unsupported"
        if default:
            self.revert = {"model": self.human.get("model"), "effort": self.human.get("model_reasoning_effort")}
        return await super().apply_model(model, effort, default)

    async def _ready(self) -> bool:
        if self.proc is None and not await self._spawn():
            return False
        try:
            return await self._thread()
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            self.fail_notice(f"Codex 没能打开这段对话（{clean(str(e), 4000) or type(e).__name__}）。")
            return False

    # ------------------------------------------------ the process
    def _last_err(self) -> str:
        t = self.err_tail.strip()
        return clean(t.splitlines()[-1], 200) if t else ""

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
        except Exception:  # noqa: BLE001
            pass
        code = await p.wait()
        from . import codex_procs
        codex_procs.forget(self.host.st, p.pid)
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        if self.proc is p:
            self.proc, self.tid = None, None
        quiet = p in self.ended
        self.ended.discard(p)
        for f in list(waits.values()):
            if not f.done():
                f.set_exception(ConnectionError("codex app-server exited"))
        for f in list(pending.values()):                 # every open card is withdrawn (agent_gone)
            if not f.done():
                f.set_result(True)
        if self.turn_proc is p and self.turn_done and not self.turn_done.is_set():
            if not quiet and not self.halting:
                last = self._last_err()
                self.fail_notice(f"Codex 退出了（{code}）" + (f"：{last}" if last else ""))
            self.turn_done.set()

    async def _kill(self, p) -> None:
        if self.proc is p:
            self.proc, self.tid = None, None
        self.ended.add(p)
        if p.returncode is None:
            with contextlib.suppress(Exception):
                p.stdin.close()
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)
        from . import codex_procs                      # ended on purpose (stop / turn end): no longer a possible leftover
        codex_procs.forget(self.host.st, p.pid)

    # ------------------------------------------------ notifications
    def _emit(self, text: str) -> None:
        if self.collect is not None:
            self.collect.append(text)
        elif not self.quiet:
            self.host.agent_text(text)

    def _on_note(self, method: str, p: dict) -> None:
        th = p.get("threadId")
        if method == "item/started" or method == "item/completed":
            it = p.get("item") if isinstance(p.get("item"), dict) else {}
            if isinstance(it.get("id"), str) and it.get("type") in ("fileChange", "commandExecution"):
                self.items[it["id"]] = it
                if len(self.items) > 500:
                    self.items.pop(next(iter(self.items)))
            if method == "item/completed" and it.get("type") == "agentMessage" and th == self.tid \
                    and isinstance(it.get("text"), str) and it["text"].strip():
                self._emit(it["text"])
            elif method == "item/completed" and it.get("type") == "contextCompaction" and th == self.tid:
                self._auto_compacted(p.get("turnId"))
        elif method == "thread/compacted" and th == self.tid:     # the older form of the same event (still sent by some)
            self._auto_compacted(p.get("turnId"))
        elif method == "turn/started" and th == self.tid:
            self.usage = None
            if not self.research and self.persist:
                self.meter(ctx=None)  # a new turn/model must not retain an old percentage
            t = p.get("turn") if isinstance(p.get("turn"), dict) else {}
            if isinstance(t.get("id"), str):
                self.turn_id = t["id"]
        elif method == "turn/completed" and th == self.tid:
            t = p.get("turn") if isinstance(p.get("turn"), dict) else {}
            self.turn_status = t
            if isinstance(t.get("model"), str) and slash.MODEL_RE.fullmatch(t["model"]):
                self.thread["model"] = t["model"]
                mid = self.cur_model()
                self.meter(model=mid, model_name=self.model_name(mid))
            if t.get("status") == "failed" and not self.halting:
                e = t.get("error") if isinstance(t.get("error"), dict) else {}
                m = e.get("message")
                self.fail_notice("Codex 这一轮没有正常完成" + (f"：{clean(m, 4000)}" if isinstance(m, str) and m else "。"))
            if self.turn_done:
                self.turn_done.set()
        elif method == "thread/tokenUsage/updated" and th == self.tid:
            self.usage = p["tokenUsage"] if isinstance(p.get("tokenUsage"), dict) else {}
            used = (self.usage.get("last") or {}).get("totalTokens") if isinstance(self.usage.get("last"), dict) else None
            from .model_limits import codex_context_limit
            win = codex_context_limit(self.cur_model(), self.usage.get("modelContextWindow"), getattr(self, 'context_metadata', None))
            if not self.research and self.persist:
                self.meter(ctx={"used": used, "max": win} if type(used) is int and used >= 0 else None)
        elif method == "account/rateLimits/updated":
            if isinstance(p.get("rateLimits"), dict):
                self.rate = p["rateLimits"]
                self.rate_meter(self.rate)
        elif method == "error" and th == self.tid and not p.get("willRetry") and not self.halting:
            e = p.get("error") if isinstance(p.get("error"), dict) else {}
            if isinstance(e.get("message"), str):
                self.host.st.log("agent_error", agent=self.kind)
        elif method == "account/updated":
            self.unavailable_models.clear()
            self._bg(self.refresh_models())
        elif method == "serverRequest/resolved":
            f = self.pending.get(p.get("requestId"))
            if f and not f.done():
                f.set_result(True)

    def _auto_compacted(self, turn_id) -> None:
        """P59 (ADR-A165): Codex compacted this thread by itself (its auto-compaction inside a turn: a `contextCompaction`
        item, or the older `thread/compacted`). Ours (`/compact`, quiet) is counted by compactprep.compact itself. A new
        epoch: the next user message carries the handover note when one was written before (compactprep.compacted)."""
        if self.quiet or not self.persist or self.research:
            return
        if turn_id is not None and turn_id == getattr(self, "_compacted_turn", None):
            return                                  # both forms of one compaction
        self._compacted_turn = turn_id
        self.host.st.log("codex_auto_compact", agent=self.kind)
        with contextlib.suppress(RuntimeError):
            self._bg(compactprep.compacted(self, auto=True))

    # ------------------------------------------------ approvals (server → client requests)
    def _on_request(self, m: dict) -> None:
        method, rid = m["method"], m["id"]
        p = m.get("params") if isinstance(m.get("params"), dict) else {}
        if method == "item/commandExecution/requestApproval":
            cmd = p.get("command") if isinstance(p.get("command"), str) else ""
            if not cmd:
                acts = [a.get("command") for a in p.get("commandActions") or [] if isinstance(a, dict)]
                cmd = " && ".join(a for a in acts if isinstance(a, str))
            inner = unwrap_shell(cmd)
            inp = {"command": inner}
            extra = []
            if p.get("kind") == "writeStdin":
                extra.append("（写入一个正在运行的命令）")
            if isinstance(p.get("networkApprovalContext"), dict):
                extra.append("（要联网：" + clean_line(json.dumps(p["networkApprovalContext"], ensure_ascii=False), 200) + "）")
            if isinstance(p.get("reason"), str) and p["reason"].strip():
                extra.append("Codex 说明：" + clean_line(p["reason"], 300))
            if extra:
                inp["description"] = " ".join(extra)
            batch = p.get("kind") != "writeStdin" and not isinstance(p.get("networkApprovalContext"), dict)
            why = beyond_sandbox(self.thread.get("sandbox"), method, "Bash", {"command": inner}, p, None, self.cfg["dir"])
            if why:
                return self._refuse(rid, "Bash", inp, {"decision": "decline"}, why)
            self._bg(self._decide(rid, "Bash", inp, {"decision": "accept"}, {"decision": "decline"}, batch))
        elif method == "item/fileChange/requestApproval":
            it = self.items.get(p.get("itemId")) or {}
            tool, inp = patch_tool(it.get("changes"))
            if isinstance(p.get("grantRoot"), str) and p["grantRoot"]:      # "allow writes under this root": we never grant it
                inp = {**inp, "description": "（只批准这一次改动，不放开整个目录）"}
            why = beyond_sandbox(self.thread.get("sandbox"), method, tool, inp, p, it.get("changes"), self.cfg["dir"])
            if why:
                return self._refuse(rid, tool, inp, {"decision": "decline"}, why)
            self._bg(self._decide(rid, tool, inp, {"decision": "accept"}, {"decision": "decline"}, True))
        elif method == "item/permissions/requestApproval":
            perms = p.get("permissions") if isinstance(p.get("permissions"), dict) else {}
            inp = {"permissions": perm_text(perms)}
            if isinstance(p.get("reason"), str):
                inp["reason"] = clean_line(p["reason"], 300)
            granted = {k: v for k, v in perms.items() if k in ("fileSystem", "network")}
            why = beyond_sandbox(self.thread.get("sandbox"), method, "CodexPermissions", inp, p, None, self.cfg["dir"])
            if why:
                return self._refuse(rid, "CodexPermissions", inp, {"permissions": {}, "scope": "turn"}, why)
            self._bg(self._decide(rid, "CodexPermissions", inp, {"permissions": granted, "scope": "turn"},
                                  {"permissions": {}, "scope": "turn"}, False))
        elif method in ("execCommandApproval", "applyPatchApproval"):     # legacy v1 shapes: never sent to a v2 client
            self._answer(rid, {"decision": "denied"})
        elif method == "mcpServer/elicitation/request":                   # nobody on the phone can fill a form: decline
            self._answer(rid, {"action": "decline"})
            if not self.research:
                self.local_only("Codex 要你填一张表（MCP elicitation）：手机上填不了，已拒绝；需要的话在电脑上处理。")
        elif method == "item/tool/requestUserInput":                      # §10.7: a question card on the phone
            qs = p.get("questions") if isinstance(p.get("questions"), list) else []
            card = question_card(qs)
            if card is None or self.research:
                self._answer(rid, {"answers": {}})
                if not self.research:
                    self.local_only("这个问题需要在电脑上回答（Codex 问的是要保密或要自己填写的内容）。")
                return
            self._bg(self._ask_user(rid, qs, card))
        else:
            self._answer(rid, error={"code": -32601, "message": "not supported by agentj"})

    async def _ask_user(self, rid, qs: list, card: list) -> None:
        """One question card; the answer is the picked option's label per question id, cancel / timeout = no answers."""
        gone = asyncio.get_running_loop().create_future()
        self.pending[rid] = gone
        ans = {"answers": {}}
        try:
            outcome, picks = await self.host.question(card, gone, task=getattr(self.host, "task_label", None))
            if outcome == "answer" and picks:
                # the label comes from the signed card itself (P33-X04), the id from the request at the same position
                ans = {"answers": {q["id"]: {"answers": [c["o"][p[0] - 1]["l"]]} for q, c, p in zip(qs, card, picks)}}
        except Exception:  # noqa: BLE001 — fail closed: nobody answered
            ans = {"answers": {}}
        finally:
            self.pending.pop(rid, None)
            if not gone.done():
                self._answer(rid, ans)

    def rate_meter(self, r) -> None:
        """account rate limits → the 5 h / week meters (the 300-minute window → h5, 10 080 → week); exact numbers only."""
        upd = {}
        for k in ("primary", "secondary"):
            w = r.get(k) if isinstance(r, dict) else None
            if isinstance(w, dict) and isinstance(w.get("usedPercent"), (int, float)) and not isinstance(w.get("usedPercent"), bool):
                key = {300: "h5", 10080: "week"}.get(w.get("windowDurationMins"))
                if key:
                    upd[key] = {"pct": float(w["usedPercent"]), "reset": _agentmod._unix(w.get("resetsAt"))}
        if upd and self.persist:
            self.meter(**upd)

    def policy_notice(self) -> str:
        """Why it was refused and what to change, by where the restriction comes from (F30)."""
        from . import codex_perm
        mode = codex_perm.mode_of(self.thread.get("sandbox"))
        src = getattr(self, "perm_source", None)
        if src == "desktop":
            return DESKTOP_NOTICE.format(mode=mode or "?")
        if src == "config":
            return POLICY_NOTICE + CONFIG_NOTICE.format(mode=mode or self.human.get("sandbox_mode") or "?")
        return POLICY_NOTICE

    def _refuse(self, rid, tool: str, inp: dict, no: dict, why: str) -> None:
        """Beyond the human's own sandbox: declined at once, no card; a notice and a refused-by-policy record."""
        self._answer(rid, no)
        if not self.research:                  # a read-only task's refusals are expected; it reports in its answer
            self.host.policy_refused(clean_line(tool, 64) or "?", inp, why, self.policy_notice())

    async def _decide(self, rid, tool: str, inp: dict, yes: dict, no: dict, batch: bool) -> None:
        gone = asyncio.get_running_loop().create_future()
        self.pending[rid] = gone
        ans = no
        try:
            if self.research:
                ok, _ = danger.readonly(tool, inp)
                if not ok:                         # a `mode: research` task: anything not read-only is refused outright
                    return
            r = await self.host.ask(clean_line(tool, 64) or "?", inp, gone, batch=batch)
            if r.get("behavior") == "allow":
                ans = yes
        except Exception:  # noqa: BLE001 — fail closed
            ans = no
        finally:
            self.pending.pop(rid, None)
            if not gone.done():
                self._answer(rid, ans)

    # ------------------------------------------------ turns
    async def turn(self, text: str) -> None:
        if not await self._ready():
            return
        self.turn_done, self.turn_proc = asyncio.Event(), self.proc
        self.turn_id, self.turn_status = None, {}
        params = {"threadId": self.tid, "input": [{"type": "text", "text": text, "text_elements": []}]}
        rev = getattr(self, "revert", None)
        if self.cfg.get("model"):
            params["model"] = self.cfg["model"]
        elif rev and isinstance(rev.get("model"), str):
            params["model"] = rev["model"]
        if self.cfg.get("effort"):                 # §10.11: "for this turn and subsequent turns" (app-server 0.160)
            params["effort"] = self.cfg["effort"]
        elif rev and isinstance(rev.get("effort"), str):
            params["effort"] = rev["effort"]
        try:
            r = await self.deliver(lambda: self.call("turn/start", params))    # §10.2: turn/start sent = delivered
            self.revert = None
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            if not self.halting:
                self.fail_notice(f"Codex 没有接这条消息（{clean(str(e), 4000) or type(e).__name__}）。")
            return
        t = (r or {}).get("turn") if isinstance(r, dict) else None
        if isinstance(t, dict) and isinstance(t.get("id"), str):
            self.turn_id = t["id"]
        await self.turn_done.wait()

    async def interrupt_request(self, p) -> None:
        """app-server's own `turn/interrupt`, then ≤ 2 s for `turn/completed` (halt() ends the process tree right after)."""
        td = self.turn_done
        if td is None or td.is_set() or not self.tid or not self.turn_id:
            return
        with contextlib.suppress(RPCError, ConnectionError, OSError, asyncio.TimeoutError):
            await self.call("turn/interrupt", {"threadId": self.tid, "turnId": self.turn_id}, 3)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(td.wait(), 2)

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        if p:
            await self._kill(p)
        for t in list(self.tasks):
            t.cancel()

    # ------------------------------------------------ a scheduled run (tasks.py): one throw-away thread
    async def run_once(self, prompt: str) -> tuple[str, int | str]:
        self.collect = []
        try:
            if not await self._ready():
                return "", "no_start"
            await self.turn(prompt)
            st = self.turn_status.get("status")
            return "\n\n".join(self.collect), 0 if st == "completed" else (st or "?")
        finally:
            if self.proc:
                await self._kill(self.proc)
            for t in list(self.tasks):
                t.cancel()

    # ------------------------------------------------ slash commands (slash.py)
    async def drop_conversation(self) -> None:
        self.tid, self.usage = None, None

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        if not await self._ready():
            return Result("Codex 没起来，没有压缩。", "error")
        before = (self.usage or {}).get("last", {}).get("totalTokens") if self.usage else None
        self.turn_done, self.turn_proc, self.quiet = asyncio.Event(), self.proc, True
        self.set_status("compacting")
        t0 = time.monotonic()
        try:
            await self.call("thread/compact/start", {"threadId": self.tid})
            await asyncio.wait_for(self.turn_done.wait(), 600)
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            return Result(f"没有压缩：{clean(str(e), 4000) or type(e).__name__}", "error")
        finally:
            self.quiet = False
            self.set_status("working")
        if self.turn_status.get("status") != "completed":
            return Result("没有压缩完（Codex 报告：" + str(self.turn_status.get("status") or "?") + "）。", "error")
        after = (self.usage or {}).get("last", {}).get("totalTokens") if self.usage else None
        took = slash.secs((time.monotonic() - t0) * 1000)
        if isinstance(before, int) and isinstance(after, int):
            return Result(f"已压缩：{slash.tokens(before)} → {slash.tokens(after)} tokens{took}")
        return Result(f"已压缩{took}。")

    async def cmd_context(self, arg: str) -> Result:
        u = self.usage if self.tid and self.tid == self.host.st.agent_session(self.kind) else None
        if not u:
            return Result("上下文：" + slash.NONE + "（Codex 只在跑完一轮后报告；这段对话在这次启动后还没跑过）", "info")
        last, win = (u.get("last") or {}).get("totalTokens"), u.get("modelContextWindow")
        return Result(f"上下文 {slash.tokens(last)} / {slash.tokens(win)}{slash.pct(last, win)}")

    async def cmd_cost(self, arg: str) -> Result:
        u = self.usage if self.tid and self.tid == self.host.st.agent_session(self.kind) else None
        tot = (u or {}).get("total") or {}
        if not tot:
            return Result("本会话花费：美元 " + slash.NONE + "（Codex 不按次报价）· token " + slash.NONE, "info")
        return Result(f"本会话 token：输入 {slash.tokens(tot.get('inputTokens'))}（其中缓存 "
                      f"{slash.tokens(tot.get('cachedInputTokens'))}）· 输出 {slash.tokens(tot.get('outputTokens'))}"
                      f"（含推理 {slash.tokens(tot.get('reasoningOutputTokens'))}）\n美元：{slash.NONE}（Codex 不按次报价）")

    def _rate_lines(self, r) -> list[str]:
        out = []
        for k, name in (("primary", "主额度"), ("secondary", "次额度")):
            w = r.get(k) if isinstance(r, dict) else None
            if isinstance(w, dict) and isinstance(w.get("usedPercent"), int):
                bits = [slash.window(w.get("windowDurationMins")), (slash.when(w.get("resetsAt")) + " 重置") if w.get("resetsAt") else ""]
                out.append(f"{name}已用 {w['usedPercent']}%" + (f"（{'，'.join(b for b in bits if b)}）" if any(bits) else ""))
        return out

    async def cmd_usage(self, arg: str) -> Result:
        r = None
        if self.proc is not None or await self._spawn():
            with contextlib.suppress(RPCError, ConnectionError, OSError, asyncio.TimeoutError):
                res = await self.call("account/rateLimits/read", {}, 20)
                r = (res or {}).get("rateLimits") if isinstance(res, dict) else None
        r = r if isinstance(r, dict) else self.rate
        if isinstance(r, dict):
            self.rate_meter(r)
        lines = self._rate_lines(r or {})
        return Result("套餐用量：" + ("；".join(lines) if lines else slash.NONE + "（Codex 没有报告）"), "ok" if lines else "info")

    async def cmd_status(self, arg: str) -> Result:
        if self.proc is None:
            await self._spawn()
        from . import codex_perm
        sb = self.thread.get("sandbox")
        sbt = codex_perm.mode_of(sb) or self.human.get("sandbox_mode")
        src = getattr(self, "perm_source", None) or codex_perm.main_sandbox(self.human.get("sandbox_mode"), self.research)[1]
        if not sb and src in ("agentj", "config", "research"):
            sbt = codex_perm.main_sandbox(self.human.get("sandbox_mode"), self.research)[0]
        info = codex_perm.config_info()
        if isinstance(self.human.get("approval_policy"), dict):
            pol = "你自己的 granular 策略（agentj 没改）"
        elif self.cfg.get("high_risk_warnings", False) and src != "desktop":
            pol = "每条非只读命令和每个文件改动都问手机（高危提醒开着：agentj 要求 untrusted）"
        else:
            ap = self.thread.get("approvalPolicy") or self.human.get("approval_policy") or "on-request"
            pol = f"{ap if isinstance(ap, str) else '自定义'}（{'这条对话自己的' if src == 'desktop' else '你的 Codex 设置 / Codex 默认'}）"
        lines = [f"Agent：Codex {self.version}".rstrip(),
                 f"对话：{self.host.st.agent_session(self.kind) or '（新对话）'}",
                 f"模型：{self.cfg.get('model') or self.thread.get('model') or self.human.get('model') or '默认'}",
                 f"目录：{self.cfg['dir']}",
                 f"审批：{pol}；危险清单生效",
                 "权限：" + codex_perm.label(src, sbt),
                 *(["⚠ 写了但没生效：" + codex_perm.misplaced_text(info["misplaced"])] if info.get("misplaced") else []),
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        lines += self._rate_lines(self.rate or {})
        return Result("\n".join(lines))

    async def cmd_model(self, arg: str) -> Result:
        models = []
        if self.proc is not None or await self._spawn():
            with contextlib.suppress(RPCError, ConnectionError, OSError, asyncio.TimeoutError):
                res = await self.call("model/list", {}, 20)
                for m in (res or {}).get("data") or []:
                    if isinstance(m, dict) and isinstance(m.get("id"), str) and not m.get("hidden"):
                        models.append({"id": clean_line(m["id"], 100), "name": clean_line(str(m.get("displayName") or m["id"]), 60),
                                       "desc": clean_line(str(m.get("description") or ""), 120),
                                       "disabled": m["id"] in self.unavailable_models})
        cur = self.cfg.get("model") or self.thread.get("model") or self.human.get("model") or next(
            (x["id"] for x in getattr(self, "models_cache", []) if x.get("default") is True), None) or ""
        if not arg:
            for m in models:
                m["cur"] = m["id"] == cur
            return Result(f"当前模型：{cur or '默认'}。" + ("点一个切换（之后的对话都用它）：" if models else "Codex 没有给出可选列表。"),
                          "ok", models=models[:40])
        if not slash.MODEL_RE.match(arg) or (models and arg not in {m["id"] for m in models}):
            return Result(f"没有这个模型：{clean_line(arg, 100)}", "error")
        if arg in self.unavailable_models:
            return Result(_agentmod.codex_failure(f"The '{arg}' model is not supported when using Codex with a ChatGPT account.", arg, getattr(self.host, "lang", "zh"))[1], "error")
        self.host.set_model(arg)
        self.meter(model=arg, model_name=self.model_name(arg))
        return Result(f"已切换到 {arg}：从下一条消息起使用，写进了 config.json。")
