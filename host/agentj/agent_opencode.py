"""OpenCode adapter (PROTOCOL §8, ADR-A55 – A58): the phone talks to the customer's own OpenCode through its official
headless server, `opencode serve`, with every permission request answered on the phone.

Why `serve` and not `opencode run`: `run` answers its own permission prompts (reject, or once with `--auto`) — nothing outside
can take part. `serve` publishes `permission.asked` on its event stream (`GET /event`, SSE) and waits for
`POST /permission/{id}/reply {"reply": "once" | "reject"}`.

What serve does (measured on OpenCode 1.18.32, `reports/qa/opencode/`):
- starts `opencode serve --hostname 127.0.0.1 --port <random>` inside the fence (fence.py, like Claude Code), with a fresh
  random `OPENCODE_SERVER_PASSWORD` per start (HTTP Basic, user `opencode`) that lives only in serve's memory and the
  process's environment; models.dev fetch, LSP downloads, auto-update, share and the embedded web UI are switched off;
- keeps one conversation (session id in `agent.json`, like the other adapters) and sends each phone message with
  `POST /session/{id}/prompt_async`; the turn ends with `session.idle`; finished text parts (`message.part.updated` with
  `time.end`) go to the phone as they complete; the event stream is re-opened when it drops (and missed text / requests are
  fetched again);
- **our permission rules ride on the session, not on the config** (ADR-A56): OpenCode evaluates the LAST matching rule of
  `agent rules (defaults + every config file) + session rules`, while config files are deep-merged (a `deny` the human wrote
  could be overwritten by an `ask` of ours, and a `"*": "allow"` they wrote lands after ours). The session ruleset = ours
  (everything asks, except read-only tools and a short read-only bash list; subagents and the question tool are off) followed by
  every `deny` rule of the human's own agent ruleset again — so a human `allow` becomes a question to the phone, a human `deny`
  stays a deny, and no config file (also one the Agent writes) can loosen it;
- answers `once` or `reject` (with the reason, so the model is told), never `always` (that would add an allow rule inside
  OpenCode for the rest of the process). A reply that serve did not send (`permission.replied` once / always) means something
  else used the server's password: OpenCode is stopped at once (G-A66).

The stop switch and `/stop` call `POST /session/{id}/abort` before the process tree ends (ADR-A72); slash commands use
`POST /session/{id}/summarize`, `GET /config/providers`, `GET /session/{id}` and the messages' token counts (slash.py). A
scheduled task (`agentj tasks`) runs in its own throw-away session; `mode: research` turns every "ask" of our rules into "deny".
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import signal
import socket
import tempfile
import time

from . import slash
from .agent import LINE_LIMIT, UNCERTAIN, Agent, _bin, _signal_tree
from .slash import Result
from .envcompat import getenv
from .text import clean, clean_line

START_WAIT = 60          # s for `opencode serve` to print its address
CONNECT_WAIT = 15        # s for the event stream to connect before the first message is sent
SSE_IDLE = 90            # s without any byte (OpenCode sends heartbeats) before the stream is re-opened
REQ_TIMEOUT = 30
WATCH = float(getenv("AGENTJ_TEST_OC_WATCH", "15"))   # s between "is the turn still running?" checks
MAX_BODY = 32 * 1024 * 1024
USER = "opencode"

# ---------------------------------------------------------------- permission rules (ADR-A56)
# Read-only bash commands that run without a card (OpenCode matches each sub-command of a pipeline / list separately; a
# pattern ending in " *" also matches the bare command). Every one is low risk for danger.classify (test_opencode checks).
BASH_READ_ONLY = ("ls *", "pwd", "cat *", "git status *", "git diff *", "git log *")
# …but back to "ask" when the sub-command redirects, substitutes, chains or names an output file (OpenCode keeps
# redirections and substitutions inside the sub-command's text): `cat a > ~/.bashrc`, `ls $(rm x)`, `git diff --output=f`.
BASH_BACK_TO_ASK = ("*>*", "*<*", "*$*", "*`*", "*|*", "*;*", "*&*", "*\n*", "*--output*", "*--ext-diff*", "*--textconv*")
READ_ONLY_TOOLS = ("glob", "grep", "list", "lsp", "todowrite", "todoread", "skill", "webfetch")


def _rule(permission: str, pattern: str, action: str) -> dict:
    return {"permission": permission, "pattern": pattern, "action": action}


def our_rules() -> list[dict]:
    """The rules serve adds to the session, in order (last match wins)."""
    r = [_rule("*", "*", "ask"),
         _rule("read", "*", "allow"), _rule("read", "*.env", "ask"), _rule("read", "*.env.*", "ask"),
         _rule("read", "*.env.example", "allow")]
    r += [_rule(t, "*", "allow") for t in READ_ONLY_TOOLS]
    r += [_rule("question", "*", "allow"),  # §10.7: the model may ASK the human — question.asked → a card on the phone
          _rule("task", "*", "deny")]       # subagent sessions inherit only deny rules, not ours (G-A65)
    r += [_rule("bash", p, "allow") for p in BASH_READ_ONLY]
    r += [_rule("bash", p, "ask") for p in BASH_BACK_TO_ASK]
    return r


def _own_dir_rule(rule: dict) -> bool:
    """OpenCode's own scratch folders (truncated tool output, its temp dir): the human's agent rules allow them; keep that."""
    p = rule.get("pattern") or ""
    return (rule.get("permission") == "external_directory" and rule.get("action") == "allow"
            and (p.endswith("/opencode/tool-output/*") or p in {os.path.join(d, "opencode", "*")
                                                                 for d in ("/tmp", tempfile.gettempdir())}))


def session_rules(agent_rules, research: bool = False) -> list[dict]:
    """Ours, then OpenCode's own scratch-folder allows, then every deny of the human's agent ruleset (so it stays deny).
    research (a scheduled `mode: research` run): every "ask" of ours is a "deny" — only the read-only allows remain."""
    agent_rules = [r for r in (agent_rules or []) if isinstance(r, dict) and all(isinstance(r.get(k), str)
                                                                                for k in ("permission", "pattern", "action"))]
    own = [_rule(r["permission"], r["pattern"], r["action"]) for r in agent_rules if _own_dir_rule(r)]
    # every deny of the human's ruleset — except OpenCode's own built-in `question` deny that its defaults allow again right
    # after (PROMPT-33 §10.7: the model may ask the human); a human whose ruleset really ends in a question deny keeps it
    q_off = evaluate("question", "*", agent_rules) == "deny"
    deny = [_rule(r["permission"], r["pattern"], "deny") for r in agent_rules if r["action"] == "deny"
            and (r["permission"] != "question" or q_off)]
    ours = our_rules()
    if research:          # a read-only scheduled run asks nobody anything: every ask (and the question tool) is a deny
        ours = [dict(r, action="deny") if r["action"] == "ask" or r["permission"] == "question" else r for r in ours]
    out = ours + own + deny
    seen, dedup = set(), []
    for r in out:                       # keep the LAST copy of a duplicate (only the last one can decide)
        k = (r["permission"], r["pattern"], r["action"])
        if k in seen:
            dedup = [x for x in dedup if (x["permission"], x["pattern"], x["action"]) != k]
        seen.add(k)
        dedup.append(r)
    return dedup


def wildcard(s: str, pattern: str) -> bool:
    """OpenCode's Wildcard.match (1.18): `*` = anything, `?` = one char, a trailing " *" also matches nothing; whole string."""
    s = s.replace("\\", "/")
    a = re.sub(r"[.+^${}()|\[\]\\]", lambda m: "\\" + m.group(0), pattern.replace("\\", "/"))
    a = a.replace("*", ".*").replace("?", ".")
    if a.endswith(" .*"):
        a = a[:-3] + "( .*)?"
    return re.fullmatch(a, s, re.S) is not None


def evaluate(permission: str, pattern: str, *rulesets) -> str:
    """OpenCode's Permission.evaluate: the last rule matching both permission and pattern; none → ask."""
    hit = "ask"
    for rs in rulesets:
        for r in rs:
            if wildcard(permission, r["permission"]) and wildcard(pattern, r["pattern"]):
                hit = r["action"]
    return hit


def config_content(user: str | None) -> str:
    """OPENCODE_CONFIG_CONTENT: the human's own (if any) with auto-update and share off. No permission here (ADR-A56)."""
    try:
        cfg = json.loads(user) if user else {}
    except ValueError:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update(autoupdate=False, share="disabled")
    return json.dumps(cfg, separators=(",", ":"))


def opencode_env(base: dict, password: str) -> dict:
    env = dict(base)
    env.update(OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME=USER, OPENCODE_DISABLE_MODELS_FETCH="1",
               OPENCODE_DISABLE_LSP_DOWNLOAD="1", OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_DISABLE_SHARE="1",
               OPENCODE_DISABLE_EMBEDDED_WEB_UI="1", OPENCODE_CONFIG_CONTENT=config_content(base.get("OPENCODE_CONFIG_CONTENT")))
    return env


def split_model(m: str | None) -> dict | None:
    if not m or "/" not in m:
        return None
    p, mid = m.split("/", 1)
    return {"providerID": p, "modelID": mid} if p and mid else None


# ---------------------------------------------------------------- what the phone sees for a request
def _diff_sides(diff: str) -> tuple[str, str, bool]:
    old, new, created = [], [], False
    for ln in (diff or "").splitlines():
        if ln.startswith("@@ -0,0 "):
            created = True
        if ln.startswith(("---", "+++", "Index:", "====")):
            continue
        if ln.startswith("-"):
            old.append(ln[1:])
        elif ln.startswith("+"):
            new.append(ln[1:])
    return "\n".join(old), "\n".join(new), created


def to_tool(req: dict, root: str) -> tuple[str, dict]:
    """(tool name, input) in the shape danger.classify / summarize / batch_scope know (Claude Code's names)."""
    from .danger import cred_path
    perm = str(req.get("permission") or "?")
    pats = [p for p in (req.get("patterns") or []) if isinstance(p, str)]
    md = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}

    def absp(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(root or "/", p)
    if perm == "bash":
        cmd = md.get("command") if isinstance(md.get("command"), str) else "\n".join(pats)
        inp = {"command": cmd}
        if isinstance(md.get("description"), str):
            inp["description"] = md["description"]
        return "Bash", inp
    if perm == "edit":
        files = [absp(p) for p in pats] or ([md["filepath"]] if isinstance(md.get("filepath"), str) else [])
        fp = md.get("filepath") if isinstance(md.get("filepath"), str) else (files[0] if files else "?")
        if len(files) > 1:              # one patch over several files: judge the riskiest path, show every file
            fp = next((f for f in files if cred_path(f)), files[0])
            return "Edit", {"file_path": fp, "old_string": "", "new_string": "files: " + ", ".join(files) + "\n" +
                            str(md.get("diff") or "")}
        old, new, created = _diff_sides(str(md.get("diff") or ""))
        if created:
            return "Write", {"file_path": fp, "content": new}
        return "Edit", {"file_path": fp, "old_string": old, "new_string": new}
    if perm == "read":
        return "Read", {"file_path": absp(pats[0]) if pats else "?"}
    if perm == "webfetch":
        return "WebFetch", {"url": md.get("url") if isinstance(md.get("url"), str) else (pats[0] if pats else "")}
    if perm == "websearch":
        return "WebSearch", {"query": md.get("query") if isinstance(md.get("query"), str) else " ".join(pats)}
    if perm == "external_directory":
        return "ExternalDirectory", {"path": md.get("parentDir") or md.get("filepath") or (pats[0] if pats else "?")}
    if perm == "doom_loop":
        return "DoomLoop", {"tool": md.get("tool"), "input": md.get("input")}
    if perm in ("task", "glob", "grep", "list", "lsp", "skill", "todowrite", "question", "plan_enter", "plan_exit",
                "codesearch"):
        return perm, {"patterns": pats, **{k: v for k, v in md.items() if isinstance(k, str)}}
    # anything else is an MCP / custom tool (OpenCode names them <server>_<tool>): judged by the words of its name
    return "mcp__" + perm, {**{k: v for k, v in md.items() if isinstance(k, str)}, **({"patterns": pats} if pats != ["*"] else {})}


# ---------------------------------------------------------------- a minimal HTTP/1.1 client (loopback only, stdlib)
class HTTPError(Exception):
    pass


class Client:
    def __init__(self, port: int, password: str, user: str = USER):
        self.port = port
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    async def _open(self, method: str, path: str, body=None, accept: str = "application/json"):
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        r, w = await asyncio.open_connection("127.0.0.1", self.port, limit=LINE_LIMIT)
        head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: {self.auth}\r\n"
                f"Accept: {accept}\r\nConnection: close\r\n")
        if body is not None:
            head += f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
        w.write(head.encode() + b"\r\n" + data)
        await w.drain()
        line = await r.readline()
        m = re.match(rb"HTTP/1\.[01] (\d{3})", line)
        if not m:
            w.close()
            raise HTTPError("bad status line")
        headers = {}
        while (h := await r.readline()) not in (b"\r\n", b"\n", b""):
            k, _, v = h.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()
        return int(m.group(1)), headers, r, w

    @staticmethod
    async def _chunks(headers: dict, r: asyncio.StreamReader):
        if "chunked" in headers.get("transfer-encoding", "").lower():
            while True:
                size = int((await r.readline()).split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    return
                yield await r.readexactly(size)
                await r.readexactly(2)
        elif "content-length" in headers:
            n = int(headers["content-length"])
            if n:
                yield await r.readexactly(n)
        else:
            while chunk := await r.read(65536):
                yield chunk

    async def request(self, method: str, path: str, body=None, timeout: float = REQ_TIMEOUT):
        async def go():
            status, headers, r, w = await self._open(method, path, body)
            try:
                buf = b""
                async for c in self._chunks(headers, r):
                    buf += c
                    if len(buf) > MAX_BODY:
                        raise HTTPError("body too large")
            finally:
                w.close()
            try:
                return status, (json.loads(buf) if buf.strip() else None)
            except ValueError:
                return status, None
        try:
            return await asyncio.wait_for(go(), timeout)
        except (asyncio.IncompleteReadError, EOFError) as e:      # the server went away mid-answer
            raise HTTPError("incomplete answer") from e

    async def events(self, idle: float = SSE_IDLE):
        """Yield every SSE event (decoded JSON) until the stream ends; raises HTTPError on a non-200 answer."""
        status, headers, r, w = await asyncio.wait_for(self._open("GET", "/event", accept="text/event-stream"), REQ_TIMEOUT)
        try:
            if status != 200:
                raise HTTPError(f"event stream {status}")
            buf, data = b"", []
            it = self._chunks(headers, r).__aiter__()
            while True:
                try:
                    c = await asyncio.wait_for(it.__anext__(), idle)
                except StopAsyncIteration:
                    return
                buf += c
                while b"\n" in buf:
                    ln, buf = buf.split(b"\n", 1)
                    ln = ln.rstrip(b"\r")
                    if ln.startswith(b"data:"):
                        data.append(ln[5:].strip())
                    elif not ln and data:
                        try:
                            ev = json.loads(b"\n".join(data))
                        except ValueError:
                            ev = None
                        data = []
                        if isinstance(ev, dict):
                            yield ev
        finally:
            w.close()


def _key(rules) -> list:
    return [(r.get("permission"), r.get("pattern"), r.get("action")) for r in rules if isinstance(r, dict)] \
        if isinstance(rules, list) else []


def ends_with_ours(have, ours) -> bool:
    """Do a session's rules end with exactly ours (so nothing written before them can loosen them)?"""
    h, o = _key(have), _key(ours)
    return bool(o) and h[-len(o):] == o


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- the adapter
class OpenCodeAgent(Agent):
    kind = "opencode"

    def __init__(self, host, cfg: dict, persist: bool = True, research: bool = False):
        super().__init__(host, cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self.client: Client | None = None
        self.sid: str | None = None
        self.root = cfg.get("dir") or "/"
        self.rules: list[dict] = []
        self.failed_start = False
        self.turn_done: asyncio.Event | None = None
        self.turn_t0 = 0.0
        self.pending: dict[str, asyncio.Future] = {}   # permission id → "gone" (replied elsewhere / process gone)
        self.questions: dict[str, asyncio.Future] = {}  # question id → "gone" (§10.7)
        self.q_sent: dict[str, list] = {}               # question id → the answers serve POSTed (P33-X05 tamper check)
        self.ours: set[str] = set()                     # permission ids serve replied to
        self.emitted: set[str] = set()                  # text part ids already on the phone
        self.connected = asyncio.Event()
        self.err_tail = ""
        self.quiet_exit = False
        self.retry_noted = False
        self.saw_busy = False                           # this turn's session went busy (a stale idle cannot end it)
        self.tasks: set[asyncio.Task] = set()
        self.persist, self.research = persist, research   # a scheduled run: its own session, never stored
        self.agent_rules: list = []
        self.collect: list | None = None                # run_once: texts are collected, not sent
        self.quiet = False                              # compaction: the summary is not a reply

    def is_down(self) -> bool:
        return self.failed_start

    def _bg(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def argv(self, port: int) -> list[str]:
        exe = _bin("AGENTJ_OPENCODE_BIN", "opencode") or "opencode"
        return [exe, "serve", "--hostname", "127.0.0.1", "--port", str(port)]

    # ------------------------------------------------ start
    async def _spawn(self) -> bool:
        if not _bin("AGENTJ_OPENCODE_BIN", "opencode"):
            self.failed_start = True
            self.fail_notice("这台电脑上没找到 OpenCode（opencode 命令）。装好并配好模型后再试。")
            return False
        if self.cfg.get("model") and not split_model(self.cfg["model"]):
            self.failed_start = True
            self.fail_notice("OpenCode 的模型要写成「服务商/模型」，例如 zhipuai/glm-5.3：在电脑终端重新运行 "
                                   "`agentj agent opencode --dir … --model zhipuai/glm-5.3`。")
            return False
        port, password = free_port(), secrets.token_urlsafe(32)
        argv = self.launch_argv(self.argv(port))
        if argv is None:
            self.failed_start = True
            return False
        proc = await asyncio.create_subprocess_exec(*argv, cwd=self.cfg["dir"], env=opencode_env(os.environ, password),
                                                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT, start_new_session=True)
        self.proc, self.err_tail, self.quiet_exit, self.failed_start = proc, "", False, False
        self.connected = asyncio.Event()
        self._bg(self._drain_err(proc))
        self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True))
        listening = None
        try:
            async with asyncio.timeout(START_WAIT):
                while line := await proc.stdout.readline():
                    m = re.search(rb"listening on http://127\.0\.0\.1:(\d+)", line)
                    if m:
                        listening = int(m.group(1))
                        break
        except TimeoutError:
            pass
        if listening != port:
            await asyncio.sleep(0.2)
            await self._kill(proc)
            last = self._last_err()
            self.failed_start = True
            self.fail_notice("OpenCode 没有启动" + (f"：{last}" if last else "。"))
            return False
        self._bg(self._drain(proc.stdout))
        self._bg(self._watch(proc))
        self.client = Client(port, password)
        try:
            await self._prepare()
        except (OSError, HTTPError, ValueError, KeyError, TypeError, asyncio.TimeoutError) as e:
            self.host.st.log("agent_prepare_fail", agent=self.kind, reason=type(e).__name__)
            self.quiet_exit = True
            await self._kill(proc)
            self.failed_start = True
            self.fail_notice(f"OpenCode 起来了，但没能接上它（{type(e).__name__}）：为了安全，这条消息没有交给它。")
            return False
        self._bg(self._events(proc))
        try:
            await asyncio.wait_for(self.connected.wait(), CONNECT_WAIT)
        except asyncio.TimeoutError:
            self.quiet_exit = True
            await self._kill(proc)
            self.failed_start = True
            self.fail_notice("OpenCode 的事件流没有接上：为了安全，这条消息没有交给它（审批会收不到）。")
            return False
        return self.proc is proc

    async def _prepare(self) -> None:
        """Where the project root is, the human's own agent ruleset (for their denies), then the session with our rules."""
        st, path = await self.client.request("GET", "/path")
        if st != 200 or not isinstance(path, dict):
            raise HTTPError(f"path {st}")
        self.root = path.get("worktree") if isinstance(path.get("worktree"), str) else self.cfg["dir"]
        st, conf = await self.client.request("GET", "/config")
        default = conf.get("default_agent") if st == 200 and isinstance(conf, dict) else None
        st, ags = await self.client.request("GET", "/agent")
        if st != 200 or not isinstance(ags, list):
            raise HTTPError(f"agent {st}")
        names = [default, "build"] if isinstance(default, str) else ["build"]
        ag = next((a for n in names for a in ags if isinstance(a, dict) and a.get("name") == n), None)
        if ag is None:
            raise HTTPError("no primary agent")
        self.agent_rules = ag.get("permission") if isinstance(ag.get("permission"), list) else []
        self.rules = session_rules(self.agent_rules, self.research)
        await self._attach()

    async def _attach(self) -> None:
        """The conversation agent.json names (our rules re-applied last), or a new one; a scheduled run always a new one."""
        sid = self.host.st.agent_session(self.kind) if self.persist else None
        if sid:
            st, s = await self.client.request("GET", f"/session/{sid}")
            if st == 200 and isinstance(s, dict):
                if not ends_with_ours(s.get("permission"), self.rules):   # (re)apply ours last: what is before them is overridden
                    st, _ = await self.client.request("PATCH", f"/session/{sid}", {"permission": self.rules})
                    if st != 200:
                        raise HTTPError(f"patch {st}")
            else:
                sid = None
                self.host.st.set_agent_session(self.kind, None)
        if not sid:
            title = "Agent J" if self.persist else "Agent J 定时任务"
            st, s = await self.client.request("POST", "/session", {"title": title, "permission": self.rules})
            if st != 200 or not isinstance(s, dict) or not isinstance(s.get("id"), str):
                raise HTTPError(f"session {st}")
            sid = s["id"]
            if self.persist:
                self.host.st.set_agent_session(self.kind, sid)
        st, s = await self.client.request("GET", f"/session/{sid}")
        if st != 200 or not isinstance(s, dict) or not ends_with_ours(s.get("permission"), self.rules):
            raise HTTPError("rules not in place")        # never prompt a session whose last rules are not ours
        self.sid = sid

    # ------------------------------------------------ the process
    def _last_err(self) -> str:
        t = self.err_tail.strip()
        return clean(t.splitlines()[-1], 200) if t else ""

    async def _drain(self, stream) -> None:
        with contextlib.suppress(Exception):
            while await stream.read(65536):
                pass

    async def _drain_err(self, proc) -> None:
        with contextlib.suppress(Exception):
            while line := await proc.stderr.readline():
                self.err_tail = (self.err_tail + line.decode("utf-8", "replace"))[-2000:]

    async def _watch(self, proc) -> None:
        code = await proc.wait()
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        if self.proc is proc:
            self.proc = None
            self.client = None
        quiet = proc in self.ended
        self.ended.discard(proc)
        tp = self.turn_proc
        if tp is not None and tp is not proc and tp.returncode is None:
            return                                        # an older process: the running turn is not its business
        for f in list(self.pending.values()):
            if not f.done():
                f.set_result(True)
        if self.turn_done and not self.turn_done.is_set():
            if not quiet:
                last = self._last_err()
                self.fail_notice(f"OpenCode 退出了（{code}）" + (f"：{last}" if last else ""))
            self.turn_done.set()
        elif not quiet:
            self.host.agent_notice(f"OpenCode 退出了（{code}）：下一条消息会重新启动它。")

    async def _kill(self, p) -> None:
        if self.proc is p:
            self.quiet_exit = True
        self.ended.add(p)
        if p.returncode is None:
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    # ------------------------------------------------ the event stream
    async def _events(self, proc) -> None:
        delay = 0.5
        first = True
        while self.proc is proc and proc.returncode is None and self.client:
            client = self.client
            try:
                async for ev in client.events():
                    if not self.connected.is_set():
                        self.connected.set()
                    if not first:
                        first = True
                        self.host.st.log("agent_events", agent=self.kind, status="reconnected")
                        await self._resync()
                    delay = 0.5
                    self.on_event(ev)
            except HTTPError as e:
                self.host.st.log("agent_events", agent=self.kind, status="refused", reason=str(e)[:40])
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
                pass
            if self.proc is not proc or proc.returncode is not None:
                return
            first = False
            self.host.st.log("agent_events", agent=self.kind, status="lost")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5)

    async def _resync(self) -> None:
        """After the stream came back: requests and text that went by while it was down."""
        c = self.client
        if not c:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, reqs = await c.request("GET", "/permission")
            live = {r.get("id") for r in reqs} if st == 200 and isinstance(reqs, list) else None
            if live is not None:
                for rid, f in list(self.pending.items()):
                    if rid not in live and not f.done():
                        f.set_result(True)
                for r in reqs:
                    if isinstance(r, dict):
                        self._on_perm(r)
            await self._catch_up()
            st, busy = await c.request("GET", "/session/status")
            if st == 200 and isinstance(busy, dict) and self.turn_done and not self.turn_done.is_set():
                s = busy.get(self.sid) if self.sid else None
                if not isinstance(s, dict) or s.get("type") == "idle":
                    self._turn_end()

    async def _catch_up(self) -> None:
        if not (self.client and self.sid and self.turn_done):
            return
        st, msgs = await self.client.request("GET", f"/session/{self.sid}/message")
        if st != 200 or not isinstance(msgs, list):
            return
        for m in msgs:
            info = m.get("info") if isinstance(m, dict) else None
            if not isinstance(info, dict) or info.get("role") != "assistant":
                continue
            if ((info.get("time") or {}).get("created") or 0) < self.turn_t0 - 5000:
                continue
            for part in m.get("parts") or []:
                self._text_part(part)

    def _text_part(self, part) -> None:
        if not isinstance(part, dict) or part.get("type") != "text" or part.get("sessionID") != self.sid:
            return
        if part.get("synthetic") or part.get("ignored") or not (part.get("time") or {}).get("end"):
            return
        pid = part.get("id")
        if not isinstance(pid, str) or pid in self.emitted:
            return
        self.emitted.add(pid)
        if isinstance(part.get("text"), str) and part["text"].strip() and not self.quiet:
            if self.collect is not None:
                self.collect.append(part["text"])
            else:
                self.host.agent_text(part["text"])

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        p = ev.get("properties") if isinstance(ev.get("properties"), dict) else {}
        if t == "permission.asked":
            self._on_perm(p)
        elif t == "permission.replied":
            rid = p.get("requestID")
            f = self.pending.get(rid)
            if f and not f.done():
                f.set_result(True)
            if p.get("reply") in ("once", "always") and rid not in self.ours:
                self._tamper("foreign_reply")
        elif t == "message.part.updated":
            self._text_part(p.get("part"))
        elif t == "session.error" and p.get("sessionID") in (self.sid, None):
            err = p.get("error") if isinstance(p.get("error"), dict) else {}
            if err.get("name") != "MessageAbortedError":
                msg = (err.get("data") or {}).get("message") if isinstance(err.get("data"), dict) else None
                self.fail_notice("OpenCode 这一轮没有正常完成" + (f"：{clean(msg, 300)}" if isinstance(msg, str) and msg
                                                                       else f"（{clean(str(err.get('name') or '?'), 60)}）。"))
        elif t == "session.status" and p.get("sessionID") == self.sid:
            s = p.get("status") if isinstance(p.get("status"), dict) else {}
            if s.get("type") in ("busy", "retry"):
                self.saw_busy = True
            if s.get("type") == "retry" and not self.retry_noted:
                self.retry_noted = True
                self.host.agent_notice("模型服务出错，OpenCode 正在重试" + (f"：{clean(s['message'], 200)}"
                                                                     if isinstance(s.get("message"), str) else "。"))
            elif s.get("type") == "idle" and self.saw_busy:
                self._turn_end()
        elif t == "session.idle" and p.get("sessionID") == self.sid and self.saw_busy:
            self._turn_end()
        elif t == "session.updated" and p.get("sessionID") == self.sid:
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            if "permission" in info and self.rules and not ends_with_ours(info.get("permission"), self.rules):
                self._tamper("session_rules")
        elif t == "question.asked" and isinstance(p.get("id"), str) and self.client:   # §10.7: a card on the phone
            if p.get("sessionID") not in (self.sid, None) or p["id"] in self.questions:
                return
            self._bg(self._question(p["id"], p.get("questions")))
        elif t in ("question.replied", "question.rejected"):
            rid = p.get("requestID")
            f = self.questions.get(rid)
            if f and not f.done():
                f.set_result(True)
            if t == "question.replied":
                # P33-X05: the question endpoint takes the server password, which the Agent's own tool processes inherit.
                # An answer serve did not POST (or different from the one it POSTed, which the human signed) means
                # something else answered for the human: OpenCode is stopped at once, like a foreign permission reply.
                mine = self.q_sent.pop(rid, None)
                if mine is None or ("answers" in p and p.get("answers") != mine):
                    self._tamper("foreign_question_reply")
            else:
                self.q_sent.pop(rid, None)

    async def _question(self, qid: str, qs) -> None:
        """question.asked → one card; the answer = the picked labels per question (`reply`); cancel / timeout / anything the
        card cannot show → `reject` (the model is told nobody chose, and asks in text)."""
        from .approvals import norm_questions
        gone = asyncio.get_running_loop().create_future()
        self.questions[qid] = gone
        client = self.client
        body, path = {}, f"/question/{qid}/reject"
        try:
            raw = [{"question": q.get("question"), "header": q.get("header") or "", "multiSelect": bool(q.get("multiple")),
                    "options": q.get("options")} for q in qs] if isinstance(qs, list) else None
            card = norm_questions(raw) if raw and not self.research else None
            if card is not None:
                outcome, picks = await self.host.question(card, gone, task=getattr(self.host, "task_label", None))
                if outcome == "answer" and picks:
                    body = {"answers": [[q["o"][n - 1]["l"] for n in p] for q, p in zip(card, picks)]}
                    path = f"/question/{qid}/reply"
        except Exception:  # noqa: BLE001 — fail closed: rejected
            body, path = {}, f"/question/{qid}/reject"
        finally:
            self.questions.pop(qid, None)
        if gone.done() or not client:
            return
        if path.endswith("/reply"):
            self.q_sent[qid] = body["answers"]          # before the POST: its question.replied must match exactly this
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            await client.request("POST", path, body)

    @staticmethod
    async def _quiet(coro) -> None:
        with contextlib.suppress(Exception):
            await coro

    def _turn_end(self) -> None:
        if self.turn_done and not self.turn_done.is_set():
            for f in list(self.pending.values()):    # a request OpenCode dropped (abort) must not keep its card open
                if not f.done():
                    f.set_result(True)
            self.turn_done.set()

    def _tamper(self, why: str) -> None:
        p = self.proc
        self.host.st.log("agent_tamper", agent=self.kind, reason=why)
        self.host.agent_notice("有程序绕过手机操作了 OpenCode（直接批准请求、替你回答问题或改了会话规则）：为了安全，已停止 OpenCode。"
                               "下一条消息会重新启动它；如果不是你做的，检查这台电脑上 Agent 最近运行过的程序。")
        if p:
            self.quiet_exit = True
            self._bg(self._kill(p))

    # ------------------------------------------------ approvals
    def _on_perm(self, req: dict) -> None:
        rid = req.get("id")
        if not isinstance(rid, str) or not rid.startswith("per") or rid in self.pending or rid in self.ours:
            return
        gone = asyncio.get_running_loop().create_future()
        self.pending[rid] = gone
        self._bg(self._decide(rid, req, gone))

    async def _decide(self, rid: str, req: dict, gone: asyncio.Future) -> None:
        reply, msg = "reject", "已拒绝。"
        try:
            tool, inp = to_tool(req, self.root)
            ans = await self.host.ask(clean_line(tool, 64) or "?", inp, gone)
            if ans.get("behavior") == "allow":
                reply = "once"
            else:
                msg = ans.get("message") if isinstance(ans.get("message"), str) else msg
        except Exception:  # noqa: BLE001 — fail closed: an adapter bug is a reject, never an allow
            reply = "reject"
        finally:
            self.pending.pop(rid, None)
        if gone.done() or not self.client:
            return                                        # replied elsewhere (a reject of the same session) or gone
        self.ours.add(rid)
        body = {"reply": reply} if reply == "once" else {"reply": "reject", "message": msg}
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            await self.client.request("POST", f"/permission/{rid}/reply", body)

    # ------------------------------------------------ turns
    async def _session_ok(self) -> bool:
        """The conversation agent.json names is the one loaded (after /clear or 「撤销清空」 it is not)."""
        if self.sid is not None and (not self.persist or self.sid == self.host.st.agent_session(self.kind)):
            return True
        try:
            await self._attach()
            return True
        except (OSError, HTTPError, ValueError, KeyError, TypeError, AttributeError, asyncio.TimeoutError) as e:
            self.fail_notice(f"OpenCode 没能打开这段对话（{type(e).__name__}）：这条没有交给它。")
            return False

    async def turn(self, text: str) -> None:
        for attempt in range(2):
            if self.proc is None and not await self._spawn():
                return
            if not await self._session_ok():
                return
            self.turn_done, self.turn_proc = asyncio.Event(), self.proc
            self.turn_t0 = time.time() * 1000
            self.retry_noted = self.saw_busy = False
            body = {"parts": [{"type": "text", "text": text}]}
            m = split_model(self.cfg.get("model"))
            if m:
                body["model"] = m
            box = {}
            client, sid = self.client, self.sid

            async def post():
                box["st"], _ = await client.request("POST", f"/session/{sid}/prompt_async", body)
                if box["st"] in (200, 204):             # §10.2: prompt_async accepted = delivered
                    return True
                # 4xx: OpenCode refused it (never delivered); anything else may have been taken (P33-X08)
                return False if isinstance(box["st"], int) and 400 <= box["st"] < 500 else UNCERTAIN
            try:
                await self.deliver(post)
                st = box["st"]
            except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                if self.proc:
                    await self._kill(self.proc)
                continue
            if st == 404 and not attempt:                 # the conversation is gone: a fresh one
                if self.persist:
                    self.host.st.set_agent_session(self.kind, None)
                await self._kill(self.proc)
                continue
            if st not in (200, 204):
                self.fail_notice(f"OpenCode 没有接这条消息（HTTP {st}）。")
                return
            await self._wait_turn()
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
                await self._catch_up()                    # a text part whose end event never came
            if self.persist and self.collect is None:
                with contextlib.suppress(Exception):
                    await self.context_meter()
            return

    async def context_meter(self) -> None:
        """§10.10: the newest assistant message's tokens / the model's `limit.context`; quotas stay null (vendor quotas)."""
        info = await self._last_assistant()
        used = self._ctx(info)
        m = self._model_of(info)
        limit = None
        prov = await self._providers()
        if m:
            for p in prov.get("providers") or []:
                if isinstance(p, dict) and p.get("id") == m["providerID"]:
                    mm = (p.get("models") or {}).get(m["modelID"]) if isinstance(p.get("models"), dict) else None
                    lim = mm.get("limit") if isinstance(mm, dict) else None
                    limit = lim.get("context") if isinstance(lim, dict) else None
        if not self.models_cache:
            self._set_models(prov)
        mid = f"{m['providerID']}/{m['modelID']}" if m else self.cur_model()
        upd = {"model": mid, "model_name": self.model_name(mid)}
        if isinstance(used, int) and isinstance(limit, int) and limit > 0:
            upd["ctx"] = {"used": used, "max": limit}
        self.meter(**upd)

    def _set_models(self, prov: dict) -> None:
        out = []
        for p in prov.get("providers") or []:
            if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not isinstance(p.get("models"), dict):
                continue
            for mid, mm in p["models"].items():
                full = f"{p['id']}/{mid}"
                if isinstance(mid, str) and slash.MODEL_RE.match(full):
                    name = mm.get("name") if isinstance(mm, dict) and isinstance(mm.get("name"), str) else mid
                    out.append({"id": clean_line(full, 100), "name": clean_line(name, 60), "efforts": None})
        if out:
            self.models_cache = out[:40]
            fn = getattr(self.host, "models_changed", None)
            if fn:
                fn()

    async def refresh_models(self) -> None:
        if self.client:
            self._set_models(await self._providers())

    async def apply_model(self, model, effort, default: bool = False):
        """OpenCode: the model rides on every prompt_async ("provider/model"); no effort until a probe proves the field."""
        if model is not None and not split_model(model):
            return "unknown_model"
        return await super().apply_model(model, effort, default)

    async def _wait_turn(self) -> None:
        """Until session.idle; every WATCH s also ask OpenCode itself, so a lost idle event cannot hang the queue."""
        quiet_since = None
        while not self.turn_done.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.turn_done.wait(), WATCH)
            if self.turn_done.is_set() or not self.client or self.pending:
                quiet_since = None
                continue
            try:
                st, busy = await self.client.request("GET", "/session/status")
            except (OSError, HTTPError, asyncio.TimeoutError):
                continue
            s = busy.get(self.sid) if st == 200 and isinstance(busy, dict) else {"type": "?"}
            if isinstance(s, dict) and s.get("type") in ("busy", "retry"):
                self.saw_busy, quiet_since = True, None
            elif quiet_since is None:
                quiet_since = time.monotonic()            # idle twice in a row (≥ one WATCH apart) = done
            elif time.monotonic() - quiet_since >= WATCH:
                self._turn_end()

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        if p:
            await self._kill(p)
        self.proc = None
        for t in list(self.tasks):
            t.cancel()

    async def interrupt_request(self, p) -> None:
        """OpenCode's own `POST /session/{id}/abort` (ends the running prompt and its tool), then ≤ 2 s for the idle event;
        halt() ends the process tree right after (ADR-A72)."""
        td = self.turn_done
        if td is None or td.is_set() or not self.client or not self.sid:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            await self.client.request("POST", f"/session/{self.sid}/abort", None, timeout=3)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(td.wait(), 2)

    # ------------------------------------------------ a scheduled run (tasks.py): its own session
    async def run_once(self, prompt: str) -> tuple[str, int | str]:
        self.collect = []
        try:
            await self.turn(prompt)
            return "\n\n".join(self.collect), 0 if self.sid else "no_start"
        finally:
            if self.proc:
                await self._kill(self.proc)
            for t in list(self.tasks):
                t.cancel()

    # ------------------------------------------------ slash commands (slash.py)
    async def drop_conversation(self) -> None:
        self.sid = None

    async def _ready(self) -> bool:
        if self.proc is None and not await self._spawn():
            return False
        return await self._session_ok()

    async def _providers(self) -> dict:
        try:
            st, r = await self.client.request("GET", "/config/providers")
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return {}
        return r if st == 200 and isinstance(r, dict) else {}

    async def _last_assistant(self) -> dict:
        """info of the newest assistant message of this conversation (tokens, modelID, providerID), or {}."""
        try:
            st, msgs = await self.client.request("GET", f"/session/{self.sid}/message")
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return {}
        for m in reversed(msgs if st == 200 and isinstance(msgs, list) else []):
            info = m.get("info") if isinstance(m, dict) else None
            if isinstance(info, dict) and info.get("role") == "assistant" and isinstance(info.get("tokens"), dict):
                return info
        return {}

    @staticmethod
    def _ctx(info: dict):
        t = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
        if isinstance(t.get("total"), int):
            return t["total"]
        c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
        parts = [t.get("input"), t.get("output"), t.get("reasoning"), c.get("read"), c.get("write")]
        return sum(x for x in parts if isinstance(x, int)) if any(isinstance(x, int) for x in parts) else None

    def _model_of(self, info: dict) -> dict | None:
        m = split_model(self.cfg.get("model"))
        if m:
            return m
        if isinstance(info.get("providerID"), str) and isinstance(info.get("modelID"), str):
            return {"providerID": info["providerID"], "modelID": info["modelID"]}
        return None

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        if not await self._ready():
            return Result("OpenCode 没起来，没有压缩。", "error")
        info = await self._last_assistant()
        model = self._model_of(info)
        if model is None:
            prov = await self._providers()
            d = prov.get("default") if isinstance(prov.get("default"), dict) else {}
            if d:
                p0, m0 = next(iter(d.items()))
                model = {"providerID": p0, "modelID": m0}
        if model is None:
            return Result("没有压缩：不知道用哪个模型（先发一条消息，或用 /model 选一个）。", "error")
        before = self._ctx(info)
        self.quiet = True
        self.set_status("compacting")
        t0 = time.monotonic()
        try:
            st, _ = await self.client.request("POST", f"/session/{self.sid}/summarize", model, timeout=600)
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):   # the summary's text parts are not replies
                st2, msgs = await self.client.request("GET", f"/session/{self.sid}/message")
                for m in msgs if st2 == 200 and isinstance(msgs, list) else []:
                    for part in (m.get("parts") if isinstance(m, dict) else None) or []:
                        if isinstance(part, dict) and isinstance(part.get("id"), str):
                            self.emitted.add(part["id"])
        except (OSError, HTTPError, asyncio.TimeoutError) as e:
            return Result(f"没有压缩：{type(e).__name__}", "error")
        finally:
            await asyncio.sleep(0.2)
            self.quiet = False
            self.set_status("working")
        if st != 200:
            return Result(f"没有压缩（HTTP {st}）。", "error")
        after = self._ctx(await self._last_assistant())
        took = slash.secs((time.monotonic() - t0) * 1000)
        if isinstance(before, int) and isinstance(after, int):
            return Result(f"已压缩：{slash.tokens(before)} → {slash.tokens(after)} tokens{took}")
        return Result(f"已压缩{took}。")

    async def cmd_context(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("上下文：还没有对话。", "info")
        if not await self._ready():
            return Result("OpenCode 没起来：" + slash.NONE, "error")
        info = await self._last_assistant()
        used = self._ctx(info)
        m = self._model_of(info)
        limit = None
        if m:
            for p in (await self._providers()).get("providers") or []:
                if isinstance(p, dict) and p.get("id") == m["providerID"]:
                    mm = (p.get("models") or {}).get(m["modelID"]) if isinstance(p.get("models"), dict) else None
                    lim = mm.get("limit") if isinstance(mm, dict) else None
                    limit = lim.get("context") if isinstance(lim, dict) else None
        if used is None:
            return Result("上下文：" + slash.NONE + "（这段对话还没有回复）", "info")
        return Result(f"上下文 {slash.tokens(used)} / {slash.tokens(limit)}{slash.pct(used, limit)}"
                      + (f" · 模型 {m['providerID']}/{m['modelID']}" if m else ""))

    async def cmd_cost(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("本会话花费：$0（还没有对话）", "info")
        if not await self._ready():
            return Result("OpenCode 没起来：" + slash.NONE, "error")
        try:
            st, s = await self.client.request("GET", f"/session/{self.sid}")
        except (OSError, HTTPError, asyncio.TimeoutError):
            st, s = 0, None
        if st != 200 or not isinstance(s, dict):
            return Result("本会话花费：" + slash.NONE, "error")
        t = s.get("tokens") if isinstance(s.get("tokens"), dict) else {}
        c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
        return Result(f"本会话花费（OpenCode 报告）：{slash.money(s.get('cost'))}\ntoken：输入 {slash.tokens(t.get('input'))}"
                      f"（缓存读 {slash.tokens(c.get('read'))}）· 输出 {slash.tokens(t.get('output'))}"
                      f"（含推理 {slash.tokens(t.get('reasoning'))}）")

    async def cmd_usage(self, arg: str) -> Result:
        return Result("套餐用量：" + slash.NONE + "（OpenCode 不报告额度：在你注册的模型服务商那边看）", "info")

    async def cmd_status(self, arg: str) -> Result:
        ver = ""
        if self.proc is not None or await self._spawn():
            with contextlib.suppress(OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                st, h = await self.client.request("GET", "/global/health")
                if st == 200 and isinstance(h, dict) and isinstance(h.get("version"), str):
                    ver = clean_line(h["version"], 40)
        lines = [f"Agent：OpenCode {ver}".rstrip(), f"对话：{self.host.st.agent_session(self.kind) or '（新对话）'}",
                 f"模型：{self.cfg.get('model') or '默认'}", f"目录：{self.cfg['dir']}",
                 "审批：除只读工具外都问手机（Agent J 的会话规则）；危险清单生效",
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        return Result("\n".join(lines))

    async def cmd_model(self, arg: str) -> Result:
        if self.proc is None and not await self._spawn():
            return Result("OpenCode 没起来，没法换模型。", "error")
        prov = await self._providers()
        models = []
        for p in prov.get("providers") or []:
            if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not isinstance(p.get("models"), dict):
                continue
            for mid, mm in p["models"].items():
                if isinstance(mid, str):
                    name = mm.get("name") if isinstance(mm, dict) and isinstance(mm.get("name"), str) else mid
                    models.append({"id": clean_line(f"{p['id']}/{mid}", 100), "name": clean_line(name, 60), "desc": clean_line(p.get("name") or "", 60)})
        cur = self.cfg.get("model") or ""
        if not arg:
            for m in models:
                m["cur"] = m["id"] == cur
            return Result(f"当前模型：{cur or '默认'}。" + ("点一个切换（之后的对话都用它）：" if models else "OpenCode 没有给出可选列表。"),
                          models=models[:40])
        if not split_model(arg) or not slash.MODEL_RE.match(arg) or (models and arg not in {m["id"] for m in models}):
            return Result(f"没有这个模型：{clean_line(arg, 100)}（写成「服务商/模型」）", "error")
        self.host.set_model(arg)
        self.meter(model=arg, model_name=self.model_name(arg))
        return Result(f"已切换到 {arg}：从下一条消息起使用，写进了 config.json。")
