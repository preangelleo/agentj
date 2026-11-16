"""S2/S3 (0.30.25): host-owned "this turn has read external content" mark for the main thread.

One mark per running main-thread turn, held on the adapter object (never in a ContextVar: hook events, permission
connections and the turn runner are different asyncio tasks). It is set only at host-observed facts — a completed
tool call of the served session, or the host-built Send that starts the turn — never from text the model or the content
supplies. It only ever raises a later outbound action to the existing Face ID card (list_approval.ask); it cannot turn
a refusal into a card or a card into an allow. It is monotone until the next host-built turn starts.

The value is {"state": "external_read", "kind": <fixed category>} — no body, URL, sender or tool input is kept or logged.
"""
from __future__ import annotations

_ATTR = "_agentj_external_taint"
KINDS = frozenset({"web", "mcp", "mail", "friend", "webhook", "channel", "external"})

# Programs whose output is remote content (approval_material._external's list, plus other clients and remote shells).
_NET_READERS = frozenset({"curl", "wget", "http", "https", "xh", "xhs", "httpie", "lynx", "w3m", "links", "elinks", "aria2c",
                          "gh", "glab", "ssh", "mosh", "scp", "sftp", "rsync", "rclone", "nc", "ncat", "netcat", "socat",
                          "telnet", "yt-dlp", "youtube-dl"})
_GIT_READS = frozenset({"clone", "pull", "fetch", "ls-remote", "submodule", "remote"})
# `agentj <group> <verb>` whose output is written by somebody else. Sending verbs (send / tell / say) are not reads.
_AGENTJ_READS = {
    "mail": (frozenset({"check", "notes", "expect", "trash", "read", "show", "inbox", "list", "search"}), "mail"),
    "friends": (frozenset({"history", "hist", "reports", "devices", "asks", "requests", "show"}), "friend"),
    "support": (frozenset({"thread", "list"}), "channel"),
    "hook": (frozenset({"wait", "triage", "show", "log", "last"}), "webhook"),
    "plaza": (frozenset({"show", "search", "list"}), "web"),
    "wechat": (frozenset({"pending", "history"}), "channel"),
}
_AGENTJ_GROUPS = {"google": "mail", "imessage": "channel"}     # every verb: mailbox / calendar / message stores
_WEB_TOOLS = frozenset({"webfetch", "websearch", "web_fetch", "web_search", "fetch", "web_search_call",
                        "readmcpresourcetool", "listmcpresourcestool", "readmcpresource", "listmcpresources"})
# ---- outbound shapes danger.classify does not call a send (security review r3-2 H2); consulted in a marked turn only
_HTTP_CLIENTS = frozenset({"curl", "wget", "http", "https", "xh", "xhs", "httpie", "lynx", "w3m", "links", "elinks", "aria2c"})
_REMOTE_PROGRAMS = frozenset({"ssh", "mosh", "scp", "sftp", "rsync", "rclone", "nc", "ncat", "netcat", "socat", "telnet", "ftp"})
_DNS_PROGRAMS = frozenset({"dig", "nslookup", "host", "ping", "drill"})
_AGENTJ_SENDS = frozenset({"send", "tell", "say", "reply", "publish", "post", "share", "notify", "broadcast", "forward"})
_CURL_DATA = frozenset({"-K", "--config", "-T", "--upload-file", "-d", "--data", "--data-raw", "--data-binary",
                        "--data-urlencode", "-F", "--form", "--json", "--post-data", "--post-file", "--body-data",
                        "--body-file"})
_SHELL_TOOLS = frozenset({"bash", "exec_command", "shell_command", "execute_command", "shell", "monitor",
                          "local_shell", "commandexecution"})


def tainted(agent) -> dict | None:
    value = getattr(agent, _ATTR, None) if agent is not None else None
    return dict(value) if isinstance(value, dict) and value.get("state") == "external_read" else None


def mark(agent, kind: str) -> dict | None:
    """Monotone within a turn: the first category stays (audit label only)."""
    if agent is None:
        return None
    current = tainted(agent)
    if current is None:
        current = {"state": "external_read", "kind": kind if kind in KINDS else "external"}
        try:
            setattr(agent, _ATTR, dict(current))
        except Exception:  # noqa: BLE001 — an adapter that cannot hold the mark (no current one): say so once
            host = getattr(agent, "host", None)
            try:
                host.st.log("trust_taint_unheld", agent=str(getattr(agent, "kind", "?"))[:16])
            except Exception:  # noqa: BLE001
                pass
            return None
    return current


def clear(agent) -> None:
    if agent is not None and getattr(agent, _ATTR, None) is not None:
        try:
            setattr(agent, _ATTR, None)
        except Exception:  # noqa: BLE001
            pass


def restore(agent, value) -> None:
    """A queued message merged into a running turn starts no new turn: what was read stays read."""
    if isinstance(value, dict) and tainted(agent) is None:
        mark(agent, value.get("kind"))


# ------------------------------------------------------------------ what counts as an external read
def _command_text(inp) -> str:
    raw = inp.get("command", inp.get("cmd", "")) if isinstance(inp, dict) else ""
    if isinstance(raw, list):
        import shlex
        return shlex.join(str(w) for w in raw)
    return raw if isinstance(raw, str) else ""


def _simple_commands(text):
    """Every simple command of a shell line (danger.lex also returns the commands inside substitutions and pipes;
    danger.unwrap opens `sh -c` / env / sudo wrappers)."""
    from . import danger
    for cmd in danger.lex(text):
        yield from danger.unwrap(cmd)


def _program(simple):
    """(basename, args) of a simple command; `python -m agentj …` counts as agentj; agentj's global flags are skipped."""
    name = str(getattr(simple, "name", "") or "").rsplit("/", 1)[-1]
    args = [a for a in (getattr(simple, "args", None) or []) if isinstance(a, str)]
    if name.startswith("python") and args[:2] == ["-m", "agentj"]:
        name, args = "agentj", args[2:]
    if name == "agentj":
        args = [a for a in args if not a.startswith("-")]
    return name, args


_URL_WORD = ("http://", "https://", "ftp://", "ssh://", "git@")


def command_kind(text) -> str | None:
    """The external-read category of a shell command line, or None. Over-inclusive on purpose (the mark only ever adds
    a Face ID): a network client, a remote shell, git / gh fetching, an agentj read of somebody else's words, any
    program handed a URL, a program named by a variable — and a line that cannot be parsed but names one of them."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        for simple in _simple_commands(text):
            name, args = _program(simple)
            if name in _NET_READERS:
                return "web"
            if name == "git" and any(a in _GIT_READS for a in args[:4]):
                return "web"
            if name == "agentj" and args:
                group = args[0]
                if group in _AGENTJ_GROUPS:
                    return _AGENTJ_GROUPS[group]
                verbs, kind = _AGENTJ_READS.get(group, (frozenset(), None))
                if any(a in verbs for a in args[1:3]):
                    return kind
            raw = [w for w in (getattr(simple, "raw", None) or []) if isinstance(w, str)]
            if any(u in w for w in raw for u in _URL_WORD):
                return "web"            # python -c urllib…, node -e fetch(…), pandoc URL, npm view …
            if name.startswith(("$", "`")) and len(raw) > 1:
                return "external"       # `$c https://…`: the program is not known
    except Exception:  # noqa: BLE001 — fall through to the word check
        pass
    else:
        return None
    import re
    words = set(re.findall(r"[A-Za-z0-9_-]+", text))
    return "external" if words & (_NET_READERS | {"agentj", "git"}) or any(u in text for u in _URL_WORD) else None


def tool_kind(tool, inp=None) -> str | None:
    """The external-read category of one completed tool call (any harness's tool naming), or None."""
    if not isinstance(tool, str) or not tool:
        return None
    try:
        from . import policy_registry
        native = policy_registry.harness_tool(tool)
    except Exception:  # noqa: BLE001
        native = tool
    for name in (tool, native if isinstance(native, str) else tool):
        if name.startswith("mcp__") or name.startswith("mcp."):
            return "mcp"
        low = name.rsplit("__", 1)[-1].rsplit(".", 1)[-1].lower()
        if low in _WEB_TOOLS or low.startswith(("browser_", "playwright_")) or low in ("browser", "computer"):
            return "web"
        if low in _SHELL_TOOLS:
            return command_kind(_command_text(inp))
    return None


def observe_tool(agent, tool, inp=None) -> dict | None:
    kind = tool_kind(tool, inp)
    return mark(agent, kind) if kind else None


def observe_claude(agent, event) -> dict | None:
    """Shared Claude session: a PostToolUse / PostToolUseFailure of the served session (main thread or its subagents —
    a subagent's findings come back into the same turn). A failed fetch can still carry remote text."""
    if not isinstance(event, dict) or event.get("hook_event_name") not in ("PostToolUse", "PostToolUseFailure"):
        return None
    return observe_tool(agent, event.get("tool_name"), event.get("tool_input"))


def observe_codex(agent, item) -> dict | None:
    """Codex app-server: a completed item of the served thread, whatever its exit status."""
    if not isinstance(item, dict):
        return None
    typ = item.get("type")
    if typ in ("mcpToolCall", "dynamicToolCall"):
        return mark(agent, "mcp")
    if typ == "webSearch":
        return mark(agent, "web")
    if typ == "commandExecution":
        kind = command_kind(_command_text(item))
        return mark(agent, kind) if kind else None
    return None


def ingress_kind(send) -> str | None:
    """The category of a host-built Send whose body somebody other than the owner wrote, or None. Read from the host's
    own source record and Send fields; an owner's own phone / terminal / verified owner channel message and the host's
    own system turns (heartbeat, background-done, compaction recovery) are not external content by themselves."""
    if send is None:
        return None
    from . import provenance
    if getattr(send, "hook_notice", None):
        return "webhook"
    raw = getattr(send, "source", None)
    if raw is None:
        return "external"       # a Send nobody vouches for (the unknown tier)
    try:
        src = provenance.normalize(raw)
        if type(src) is provenance.SameHost:
            return "channel"
        kind, role = src.get("kind"), src.get("role")
        if kind in ("phone", "terminal") and role == "owner":
            return None
        if provenance.owner_channel(src) is not None:
            return None
        if kind in ("system", "scheduled_task"):
            return None
    except Exception:  # noqa: BLE001 — an unreadable source is not vouched for
        return "external"
    if kind == "friend_task":
        return "friend"
    if kind == "email":
        return "mail"
    if kind == "external_mcp":
        return "mcp"
    if kind == "automation":
        return "webhook"
    return "channel"


def turn_start(host, send=None) -> dict | None:
    """Host.agent_turn_start: a new host-built turn starts clean, then takes the mark of its own ingress. This is the
    ONLY place a mark ends. The end of a turn does not end it: in a shared session the turn's end is a Stop hook event,
    which the session's own shell can emit while the model keeps working (security review r3-3)."""
    agent = getattr(host, "agent", None)
    clear(agent)
    kind = ingress_kind(send)
    return mark(agent, kind) if kind else None


# ------------------------------------------------------------------ the approval seam (list_approval.ask)
def _dynamic(word) -> bool:
    return isinstance(word, str) and ("$" in word or "`" in word)


def command_outbound(text) -> bool:
    """Outbound shapes beyond danger.classify's `send`, for a MARKED turn only (it can only add a card): data that
    leaves inside a request line — a substitution / variable / @file / stdin / config file handed to an HTTP client, a
    request header or body, a DNS name built from a variable — any remote shell or raw socket, and agentj's own sending
    verbs. A plain fetch of a literal URL is not a send."""
    from . import danger
    for cmd in danger.lex(text):
        fed = bool(getattr(cmd, "piped", False) or getattr(cmd, "heredoc", False) or getattr(cmd, "ins", None))
        for simple in danger.unwrap(cmd):
            name, args = _program(simple)
            raw = [w for w in (getattr(simple, "raw", None) or []) if isinstance(w, str)]
            flags = getattr(simple, "flags", None) or set()
            if name in _REMOTE_PROGRAMS:
                return True
            if name in _HTTP_CLIENTS:
                if fed or getattr(simple, "ins", None) or "subst" in flags or any(_dynamic(w) for w in raw[1:]):
                    return True
                if any(a.split("=", 1)[0] in _CURL_DATA or a.startswith("@") or "=@" in a for a in raw[1:]):
                    return True
            if name in _DNS_PROGRAMS and ("subst" in flags or any(_dynamic(w) for w in raw[1:])):
                return True
            if name == "agentj" and any(a in _AGENTJ_SENDS for a in args[:4]):
                return True
            if name.startswith(("$", "`")):
                return True             # a program named by a variable: not known to be local
    return False


def outbound(tool, inp) -> bool:
    """Is this request an outbound send? danger's own data-flow categories, plus command_outbound. Unreadable → yes."""
    try:
        from . import danger, policy_registry
        native = policy_registry.harness_tool(tool) if isinstance(tool, str) else tool
        low = native.rsplit("__", 1)[-1].rsplit(".", 1)[-1].lower() if isinstance(native, str) else ""
        shell = low in _SHELL_TOOLS and not str(native).startswith("mcp__")
        if shell:
            native, inp = "Bash", {"command": _command_text(inp)}
        if "send" in danger.classify(native, inp).cats:
            return True
        return bool(shell and command_outbound(inp["command"]))
    except Exception:  # noqa: BLE001 — fail towards the card
        return True


_NEVER = frozenset({"settings", "scheduled_task"})    # the owner's settings pages; runs the owner enabled (0.30.11)


def escalates(host, tool, inp, cwd, typed) -> str | None:
    """The mark's category when this off-list request must take the Face ID card, else None. Runs off-loop.

    Three conditions, all required (security review r3): the turn is marked; the request is an outbound send; and the
    unchanged off-list decision ALLOWS it. A request the off-list rules refuse (credential / private-state access,
    unresolved execution, a write from an unknown / external / host_read source) stays refused, with no card."""
    from . import approval_offlist
    taint = tainted(getattr(host, "agent", None))
    if taint is None or not isinstance(typed, dict) or typed.get("type") in _NEVER:
        return None
    if not outbound(tool, inp):
        return None
    decision, reason, _ = approval_offlist.decide(host, tool, inp, cwd, typed)
    if decision != "allow" or reason == "off_list_handoff_reply":   # the reply the owner's own Face ID already signed
        return None
    return taint["kind"]


def effective_cwd(host, cwd):
    """The working directory approval_offlist.answer() itself falls back to (security review r3-2 M1)."""
    return cwd or ((getattr(getattr(host, "agent", None), "cfg", None) or {}).get("dir")) or None
