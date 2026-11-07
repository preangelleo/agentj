"""MCP client (AGENTJ-CORE-SPEC 2.2 `core/mcp`, D7: no `mcp` SDK, standard library only; P431 C8).

Servers are the OWNER's list, `<state>/core/mcp.json` (0600; the state directory is invisible inside the fence, so an Agent
cannot add a server that would then run unvetted code — `agentj core mcp add` outside the fence, or the phone card the
fenced CLI asks serve for):

    {"schema": "agentj.core.mcp/v1", "servers": {"<name>": {"type": "stdio", "command": ["npx", "-y", "pkg"],
                                                             "env": {"NAME": "plain value"}, "allow": ["tool", …]},
                                                  "<name>": {"type": "http", "url": "https://…/mcp", "key_env": "NAME",
                                                             "allow": [...]}}}

- stdio servers run INSIDE the fence (fence.wrap, root = the session's folder) with the toolbox's allow-listed environment
  plus the plain `env` values the owner wrote; a name that looks like a secret is refused — keys never enter a tool's
  environment (spec 2.1). JSON-RPC 2.0, one message per line.
- Streamable HTTP servers are called from the host process: POST with JSON or an SSE answer, `Mcp-Session-Id` kept,
  redirects refused, ≤ 8 MiB; a key named by `key_env` is read by NAME from the service environment and only ever put in
  the Authorization header (https only, or loopback http).
- Every tool is offered as `mcp__<server>__<tool>` (Claude Code's naming, so the phone card reads the same) and goes through
  the gate: a card for every call unless the owner listed the tool in `allow`; research orders run only allowed tools;
  refusals are order-wide like any other step. A server's own annotations (readOnlyHint …) are never trusted.
- Budget (spec §11): all MCP tool definitions together ≤ SPECS_MAX tokens (descriptions cut, then tools dropped, the rest
  named in the log); small-model tier gets no MCP at all.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCHEMA = "agentj.core.mcp/v1"
PROTOCOL = "2025-06-18"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
TOOL_RE = re.compile(r"[^A-Za-z0-9_-]")
SPECS_MAX = 2000             # tokens for every MCP tool definition together
DESC_MAX = 300
SCHEMA_MAX = 4000            # characters of one input schema
TOOLS_MAX = 64               # per server
CALL_TIMEOUT = 120
START_TIMEOUT = 30
OUT_MAX = 60_000


class McpError(Exception):
    pass


def path(st_root) -> Path:
    return Path(st_root) / "core" / "mcp.json"


def load(st_root) -> dict:
    try:
        d = json.loads(path(st_root).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    s = d.get("servers") if isinstance(d, dict) else None
    return {k: v for k, v in (s or {}).items() if NAME_RE.match(str(k)) and not problems(v)} if isinstance(s, dict) else {}


def problems(spec) -> list[str]:
    """Why an owner's server entry is refused ([] = fine). Fail closed on anything we would have to guess."""
    from .toolbox import SECRETISH
    if not isinstance(spec, dict):
        return ["not an object"]
    out = []
    allow = spec.get("allow", [])
    if not isinstance(allow, list) or not all(isinstance(x, str) and x for x in allow):
        out.append("allow must be a list of tool names")
    if spec.get("type") == "stdio":
        cmd = spec.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            out.append("command must be a non-empty list of strings")
        env = spec.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            out.append("env must map names to plain strings")
        elif any(SECRETISH.search(k) for k in env):
            out.append("a stdio server never gets a key: use an http server with key_env, or no key")
        if set(spec) - {"type", "command", "env", "allow"}:
            out.append("unknown field")
    elif spec.get("type") == "http":
        u = urllib.parse.urlsplit(str(spec.get("url") or ""))
        local = u.hostname in ("127.0.0.1", "localhost", "::1")
        if not (u.scheme == "https" or (u.scheme == "http" and local)) or not u.hostname:
            out.append("url must be https (or http on loopback)")
        if u.username or u.password:
            out.append("no credentials in the url")
        k = spec.get("key_env")
        if k is not None and not (isinstance(k, str) and re.match(r"^[A-Z][A-Z0-9_]{1,63}$", k)):
            out.append("key_env must be an environment variable NAME")
        if set(spec) - {"type", "url", "key_env", "allow"}:
            out.append("unknown field")
    else:
        out.append("type must be stdio or http")
    return out


def save(st_root, servers: dict) -> None:
    p = path(st_root)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"schema": SCHEMA, "servers": servers}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def add(st_root, name: str, spec: dict) -> None:
    if not NAME_RE.match(name or ""):
        raise McpError("name: lowercase letters, digits, - and _, at most 24")
    why = problems(spec)
    if why:
        raise McpError("; ".join(why))
    cur = load(st_root)
    cur[name] = spec
    save(st_root, cur)


def remove(st_root, name: str) -> bool:
    cur = load(st_root)
    if cur.pop(name, None) is None:
        return False
    save(st_root, cur)
    return True


def describe(name: str, spec: dict) -> str:
    """One line for a card / `list`: what would run or be called (no key value, only its NAME)."""
    if spec.get("type") == "stdio":
        return f"{name}: stdio · {' '.join(spec['command'])[:200]}" + (f" · env {', '.join(spec.get('env') or {})}"
                                                                         if spec.get("env") else "")
    return f"{name}: http · {spec['url'][:200]}" + (f" · key {spec['key_env']}" if spec.get("key_env") else "")


# ------------------------------------------------------------------------------------------------ transports
class _Rpc:
    """JSON-RPC 2.0 framing shared by both transports: request ids, server requests answered (ping) or refused."""

    def __init__(self):
        self.n = 0

    def _msg(self, method, params=None, notify=False):
        m = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            m["params"] = params
        if not notify:
            self.n += 1
            m["id"] = self.n
        return m

    @staticmethod
    def _answer(m: dict, rid):
        """A response for `rid` → its result; raises McpError on an error object."""
        if m.get("id") != rid:
            return None
        if "error" in m:
            e = m["error"] if isinstance(m["error"], dict) else {}
            raise McpError(f"server error {e.get('code')}: {str(e.get('message'))[:200]}")
        if not isinstance(m.get("result"), dict):
            raise McpError("bad result")
        return m["result"]


class Stdio(_Rpc):
    def __init__(self, spec: dict, root: str, *, st=None, fenced: bool = True, environ=None):
        super().__init__()
        from .toolbox import environment
        env = environment(environ)
        env.pop("AGENTJ_CORE_TOOLBOX", None)
        env.update(spec.get("env") or {})
        argv = list(spec["command"])
        if fenced:
            from .. import fence
            why = fence.problem(st, root)
            if why:
                raise McpError("fence:" + why)
            argv = fence.wrap(st, argv, root)
        try:
            self.proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise McpError(f"cannot start: {type(e).__name__}") from None
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True, name="mcp-stdio").start()

    def _reader(self):
        total = 0
        for line in self.proc.stdout:
            total += len(line)
            if len(line) > 8 * 1024 * 1024:
                break
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict):
                self.q.put(m)
        self.q.put(None)

    def _send(self, m):
        self.proc.stdin.write((json.dumps(m, ensure_ascii=False) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout: float = CALL_TIMEOUT) -> dict:
        with self.lock:
            m = self._msg(method, params)
            try:
                self._send(m)
            except (OSError, ValueError):
                raise McpError("server ended") from None
            import time
            end = time.monotonic() + timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    raise McpError("timeout")
                try:
                    got = self.q.get(timeout=left)
                except queue.Empty:
                    raise McpError("timeout") from None
                if got is None:
                    raise McpError("server ended")
                if "method" in got and "id" in got:          # a request from the server: only ping is answered
                    reply = {"jsonrpc": "2.0", "id": got["id"]}
                    reply.update({"result": {}} if got["method"] == "ping" else
                                 {"error": {"code": -32601, "message": "not supported by this client"}})
                    try:
                        self._send(reply)
                    except (OSError, ValueError):
                        raise McpError("server ended") from None
                    continue
                r = self._answer(got, m["id"])
                if r is not None:
                    return r

    def notify(self, method, params=None):
        with self.lock:
            try:
                self._send(self._msg(method, params, notify=True))
            except (OSError, ValueError):
                raise McpError("server ended") from None

    def close(self):
        p = self.proc
        if p.poll() is None:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                p.kill()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for f in (p.stdin, p.stdout):
            try:
                f.close()
            except OSError:
                pass


class Http(_Rpc):
    def __init__(self, spec: dict, *, environ=None, opener=None):
        super().__init__()
        self.url, self.sid = spec["url"], None
        env = os.environ if environ is None else environ
        self.key = env.get(spec["key_env"]) if spec.get("key_env") else None
        if spec.get("key_env") and not self.key:
            raise McpError(f"no key {spec['key_env']} in the service environment")
        from .llm import _NoRedirect
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.lock = threading.Lock()
        self.version = None

    def _post(self, m: dict, timeout: float):
        from .llm import MAX_REPLY, sse
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": "agentj-core/1"}
        if self.key:
            h["Authorization"] = "Bearer " + self.key
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        req = urllib.request.Request(self.url, data=json.dumps(m, ensure_ascii=False).encode(), headers=h, method="POST")
        try:
            r = self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            with e:
                raise McpError(f"http {e.code}") from None
        except (OSError, ValueError) as e:
            raise McpError(f"network: {type(e).__name__}") from None
        with r:
            if r.status not in (200, 202):
                raise McpError(f"http {r.status}")
            sid = r.headers.get("Mcp-Session-Id")
            if sid and re.match(r"^[\x21-\x7e]{1,200}$", sid):
                self.sid = sid
            body = r.read(MAX_REPLY + 1)
            if len(body) > MAX_REPLY:
                raise McpError("reply exceeds 8 MiB")
            if "id" not in m:
                return []
            if (r.headers.get("Content-Type") or "").startswith("text/event-stream"):
                out = []
                for _, data in sse(body):
                    try:
                        out.append(json.loads(data))
                    except ValueError:
                        continue
                return out
            try:
                v = json.loads(body or b"null")
            except ValueError:
                raise McpError("bad JSON") from None
            return v if isinstance(v, list) else [v]

    def request(self, method, params=None, timeout: float = CALL_TIMEOUT) -> dict:
        with self.lock:
            m = self._msg(method, params)
            for got in self._post(m, timeout):
                if isinstance(got, dict) and "method" not in got:
                    r = self._answer(got, m["id"])
                    if r is not None:
                        return r
            raise McpError("no answer")

    def notify(self, method, params=None):
        with self.lock:
            self._post(self._msg(method, params, notify=True), 30)

    def close(self):
        pass


# ------------------------------------------------------------------------------------------------ the session's hub
def tool_name(server: str, tool: str) -> str:
    return ("mcp__" + server + "__" + TOOL_RE.sub("_", tool))[:64]


def _result_text(r: dict) -> str:
    parts = []
    for c in r.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text" and isinstance(c.get("text"), str):
            parts.append(c["text"])
        elif c.get("type") == "resource" and isinstance((c.get("resource") or {}).get("text"), str):
            parts.append(c["resource"]["text"])
        else:
            parts.append(f"[{str(c.get('type'))[:20]} content omitted]")
    if not parts and r.get("structuredContent") is not None:
        parts.append(json.dumps(r["structuredContent"], ensure_ascii=False)[:OUT_MAX])
    text = "\n".join(parts)[:OUT_MAX] or "(no output)"
    return ("Error: " + text) if r.get("isError") else text


class Hub:
    """The MCP servers of one session: started on first use, their tools offered as specs, calls run after the gate."""

    def __init__(self, servers: dict, root: str, *, st=None, fenced: bool = True, environ=None, log=None,
                 make_stdio=None, make_http=None):
        self.servers, self.root, self.st, self.fenced, self.environ = servers, root, st, fenced, environ
        self.log = log or (lambda *a, **k: None)
        self.make_stdio = make_stdio or (lambda spec: Stdio(spec, root, st=st, fenced=fenced, environ=environ))
        self.make_http = make_http or (lambda spec: Http(spec, environ=environ))
        self.conns: dict = {}
        self.tools: dict[str, tuple[str, str]] = {}   # offered name → (server, the server's tool name)
        self._specs: list[dict] = []
        self.started = False
        self.failed: dict[str, str] = {}

    @classmethod
    def for_session(cls, st_root, root: str, **kw):
        servers = load(st_root)
        return cls(servers, root, **kw) if servers else None

    def start(self) -> list[dict]:
        if self.started:
            return self._specs
        self.started = True
        from .prompt import tokens
        specs, used = [], 0
        for name, spec in sorted(self.servers.items()):
            try:
                c = self.make_stdio(spec) if spec["type"] == "stdio" else self.make_http(spec)
                c.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                         "clientInfo": {"name": "agentj-core", "version": "1"}}, timeout=START_TIMEOUT)
                if isinstance(c, Http):
                    c.version = PROTOCOL
                c.notify("notifications/initialized")
                listed, cursor = [], None
                for _ in range(3):
                    r = c.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=START_TIMEOUT)
                    listed += [t for t in r.get("tools") or [] if isinstance(t, dict) and isinstance(t.get("name"), str)]
                    cursor = r.get("nextCursor")
                    if not cursor or len(listed) >= TOOLS_MAX:
                        break
                self.conns[name] = c
            except (McpError, OSError, KeyError, TypeError) as e:
                self.failed[name] = str(e)[:120]
                self.log("mcp_server_failed", server=name, error=type(e).__name__)
                continue
            for t in listed[:TOOLS_MAX]:
                schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
                if schema.get("type") != "object" or len(json.dumps(schema)) > SCHEMA_MAX:
                    schema = {"type": "object"}
                off = tool_name(name, t["name"])
                spec_ = {"type": "function", "function": {
                    "name": off, "description": f"[MCP {name}] " + " ".join(str(t.get("description") or "").split())[:DESC_MAX],
                    "parameters": schema}}
                n = tokens(json.dumps(spec_, ensure_ascii=False))
                if off in self.tools or used + n > SPECS_MAX:
                    self.log("mcp_tool_skipped", server=name, reason="budget" if off not in self.tools else "duplicate")
                    continue
                used += n
                self.tools[off] = (name, t["name"])
                specs.append(spec_)
        self._specs = specs
        return specs

    def owns(self, name: str) -> bool:
        return name in self.tools

    def allowed(self, name: str) -> bool:
        """The owner listed this tool as runnable without a card (`allow`)."""
        srv, tool = self.tools.get(name, (None, None))
        return bool(srv) and tool in (self.servers.get(srv, {}).get("allow") or [])

    def call(self, name: str, args: dict) -> str:
        srv, tool = self.tools[name]
        try:
            r = self.conns[srv].request("tools/call", {"name": tool, "arguments": args})
        except McpError as e:
            return f"Error: MCP server {srv}: {e}"
        return _result_text(r)

    def close(self):
        for c in self.conns.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        self.conns = {}


def offered(hub, tier: str) -> list[dict]:
    """The MCP tool definitions a session gets: none on the small-model tier (spec §11) or without servers."""
    if hub is None or tier == "small":
        return []
    return hub.start()
