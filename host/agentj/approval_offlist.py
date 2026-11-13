"""0.30.7 ADR-0307-offlist: an action that is not on the explicit card list never opens a card, from any source.

Chairman 2026-11-08 / 11-10: only listed actions ask (the UV list in uv_list.py plus the host-owned explicit cards
named in CARD_LIST below); everything else is answered at once. A doubtful source can only make a listed action
stricter, or turn an off-list WRITE into an immediate refusal that says why — it never conjures a card.

Two pieces, both host-side and never read from a tool field, a turn's text or a wire value:

* `source(host)` — the typed label of the turn asking now, written from the host's own objects (the Send it queued,
  the scheduler's run scope, the paired request, the settings context). Types and trust:
    owner     owner_remote <device> · owner_terminal · owner_channel <telegram|wechat|mail>
    host      host_heartbeat · scheduled_task <name> · settings <page> · host_system <background job ordered by the owner>
    host_read host_system <compact_resume|background_done|approval_review|notice> · automation <name> · avatar <channel>
              (host-verified, but the turn carries outside data)
    external  webhook <id> · friend <id> · same_owner <id> · external_channel <kind> · external_mcp
    unknown   only when nothing host-held says who started the turn
  `host` / `host_read` turns are 「已核实 · 主机」: never the owner (they approve nothing), never 「可疑外部」.

* `answer(host, tool, inp, cwd, src)` — the off-list decision, audited, no card:
    1. a credential file / Agent J private state read          → refuse (every source; red line: no secret into a turn)
    2. an unreadable launcher                                    → refuse (every source; #488)
    3. owner / host turns                                        → allow (what the owner's own harness would run)
    4. host_read / external / unknown turns  → only the CLOSED read list (security r1 C1/C2/H2): cat/head/tail/nl/wc with
       closed flags on regular single-link files under the working directory (host_read: + bundled agentj skill docs and
       exact agentj listing shapes), ls / pwd / git status / cd within it; no glob, variable, pipe, redirect or
       substitution. Everything else is refused with the reason — still no card.
"""
from __future__ import annotations

import os
import secrets

# Host-owned explicit cards: each one is a concrete host executor's own question / grant, not a harness request.
# Together with uv_list.required() this IS the card list. A native (harness) request may only use AskUserQuestion.
CARD_LIST = frozenset({"AskUserQuestion", "SandboxOnce", "AuthRescue", "AgentJBoundedPlan", "company.write",
                       "agentj core mcp add", "agentj learn", "agentj plaza"})
NATIVE_CARDS = frozenset({"AskUserQuestion"})

TRUST = ("owner", "host", "host_read", "external", "unknown")

_SYSTEM_KINDS = {"heartbeat": ("host_heartbeat", None, "host"),
                 # security r1 H1: these two carry outside data (a job's raw output; a handoff written from a context that
                 # may have held external turns) → verified host, read-only like the explanation turn.
                 "compact_resume": ("host_system", "compact_resume", "host_read"),
                 "background_done": ("host_system", "background_done", "host_read"),
                 "approval_review": ("host_system", "approval_review", "host_read")}

_LABELS = {
    "owner_remote": ("主人消息 · 遥控器 {name}", "Owner message · remote {name}"),
    "owner_terminal": ("主人消息 · 本机终端", "Owner message · local terminal"),
    "owner_channel": ("主人消息 · {name}", "Owner message · {name}"),
    "host_heartbeat": ("已核实 · 主机心跳", "Verified · host heartbeat"),
    "host_system": ("已核实 · 主机系统提示（{name}）", "Verified · host system notice ({name})"),
    "scheduled_task": ("已核实 · 定时任务 {name}", "Verified · scheduled task {name}"),
    "automation": ("已核实 · 主机自动化 {name}", "Verified · host automation {name}"),
    "settings": ("设置页 · {name} · 你本人", "Settings · {name} · you"),
    "webhook": ("Webhook {name}", "Webhook {name}"),
    "friend": ("好友 {name}", "Friend {name}"),
    "same_owner": ("同主人设备 {name}", "Same-owner device {name}"),
    "external_channel": ("外部消息 · {name}", "External message · {name}"),
    "external_mcp": ("外部连接器 {name}", "External connector {name}"),
    "avatar": ("已核实 · 主人通道分身 {name}", "Verified · owner channel avatar {name}"),
    "unknown": ("来源未知", "Unknown source"),
}
_SYSTEM_NAMES = {"compact_resume": ("压缩恢复", "compaction recovery"), "background_done": ("后台活结束", "background job done"),
                 "approval_review": ("审批解释", "approval explanation"), "notice": ("通知", "notice")}
_CHANNEL_NAMES = {"telegram": ("Telegram", "Telegram"), "wechat": ("微信", "WeChat"), "mail": ("邮件", "Mail")}


def _short(value, limit=48):
    s = "".join(c for c in str(value or "") if c.isprintable())
    return s[:limit]


def _make(kind, name, trust):
    zh, en = _LABELS[kind]
    if isinstance(name, tuple):
        nzh, nen = name
    else:
        nzh = nen = _short(name)
    return {"type": kind, "name": nen if name is not None else "", "trust": trust,
            "label": zh.format(name=nzh).replace("  ", " ").strip(), "label_en": en.format(name=nen).replace("  ", " ").strip()}


def _device_name(host, device):
    try:
        row = host.st.devices().get(device) or {}
    except Exception:  # noqa: BLE001 — a label only
        row = {}
    return _short(row.get("name") or row.get("label") or (str(device)[:8] if device else ""), 40)


def _task_name(host, scope):
    ref = scope.get("approval_task_ref")
    record = host.__dict__.get("_approval_task_refs", {}).get(ref) or {}
    task = str(record.get("task_record") or "")
    if task.startswith("workflow:"):
        return _short(task[len("workflow:"):].split("/run:", 1)[0], 64)
    return _short(scope.get("task_name") or ref or "", 64)


def _from_normalized(host, src, send=None):
    from . import provenance
    if type(src) is provenance.SameHost:
        return _make("same_owner", ("本机另一个会话", "another session on this computer"), "external")
    kind, role = src.get("kind"), src.get("role")
    if kind == "phone" and role == "owner":
        return _make("owner_remote", _device_name(host, getattr(send, "device", None)), "owner")
    if kind == "terminal" and role == "owner":
        return _make("owner_terminal", None, "owner")
    channel = provenance.owner_channel(src)
    if channel is not None:
        return _make("owner_channel", _CHANNEL_NAMES.get(channel, (channel, channel)), "owner")
    if kind == "system":
        if send is not None and getattr(send, "hook_notice", None):
            return _make("webhook", _short(send.hook_notice, 12), "external")
        return _make("host_system", _SYSTEM_NAMES["notice"], "host_read")
    if kind == "automation":
        return _make("automation", src.get("name"), "host_read")
    if kind == "scheduled_task":
        scope = provenance.task_scope()
        return dict(_make("scheduled_task", _task_name(host, scope) if isinstance(scope, dict) else "", "host"),
                    owner_dispatch=isinstance(scope, dict) and scope.get("owner") is True)
    if kind == "friend_task":
        out = _make("friend", _short(getattr(send, "fid", None) or src.get("relay") or "", 12), "external")
        row = _handoff(host, send)
        if row is not None:     # the owner signed 「交给主 Agent 来办」 for this friend's message (friend_handoff.mark)
            out = _make("friend", row.get("name") or row["fid"][:12], "external")
            out["handoff_fid"] = row["fid"]
        return out
    if kind == "same_owner":
        return _make("same_owner", _short(getattr(send, "fid", None) or src.get("fid") or src.get("relay") or "", 12), "external")
    if kind in ("wechat_task", "official_assist", "telegram", "email"):
        names = {"wechat_task": ("微信任务", "WeChat task"), "official_assist": ("官方协助", "official assist"),
                 "telegram": ("Telegram 群/转发", "Telegram group/forward"), "email": ("访客邮件", "guest mail")}
        return _make("external_channel", names[kind], "external")
    if kind == "external_mcp":
        return _make("external_mcp", _short(src.get("connector"), 8), "external")
    return _make("unknown", None, "unknown")


def _handoff(host, send):
    """The live host handoff record of this exact Send (in-memory book only; friend_handoff.current re-checks on use)."""
    import time
    nonce = getattr(send, "friend_handoff", None)
    row = host.__dict__.get("_friend_handoffs", {}).get(nonce) if isinstance(nonce, str) else None
    if row is None or row.get("send") is not send or time.monotonic() >= row.get("until", 0) or not isinstance(row.get("fid"), str):
        return None
    return row


def _handoff_reply(src, tool, inp):
    """In an owner-handed friend turn, exactly one plain `agentj friends tell <that friend> --reply-to <mid> …`."""
    from . import danger
    fid = src.get("handoff_fid")
    if not fid or _operation(tool) not in _SHELL_TOOLS:
        return False
    commands = danger.lex(_command(inp))
    if len(commands) != 1:
        return False
    c = commands[0]
    if c.subst or c.heredoc or c.ins or c.piped or c.redirs or getattr(c, "owner", None) is not None:
        return False
    simples = danger.unwrap(c)
    if len(simples) != 1:
        return False
    x = simples[0]
    return (x.name == "agentj" and not x.ins and not x.redirs and not x.flags and c.words[:1] == ["agentj"]
            and len(x.args) == 6 and x.args[:2] == ["friends", "tell"] and x.args[2] == fid and x.args[3] == "--reply-to"
            and not x.args[4].startswith("-") and not x.args[5].startswith("-"))   # security r2 N2: exactly one plain reply


def source(host, *, settings_context=None, background=None, order_owner=False):
    """The typed label of the turn asking now. Host-held objects only; `unknown` only when none says who.
    `owner_dispatch` (security 0310 H1): True only where the owner created / started this work — settings pages, an
    order the owner's own turn dispatched (host-held parent), a scheduled run the owner enabled / started."""
    from . import host_turns, provenance
    try:
        if settings_context is not None:
            page = getattr(settings_context, "page", None) or ("通用", "general")
            return dict(_make("settings", page, "host"), owner_dispatch=True)
        from . import approval_review
        inherited = approval_review.ORIGIN.get()
        if isinstance(inherited, dict) and isinstance(inherited.get('settings_page'), (tuple, list)):
            page = tuple(inherited['settings_page'])[:2]
            if len(page) == 2 and all(isinstance(x, str) for x in page):
                return dict(_make("settings", page, "host"), owner_dispatch=True)
        paired = provenance._paired_source()
        if paired is not provenance._UNSET:
            return _from_normalized(host, paired) if provenance.is_owner(paired) else _make("unknown", None, "unknown")
        agent = getattr(host, "agent", None)
        scope = provenance.task_scope()
        if scope is not None:
            return (dict(_make("scheduled_task", _task_name(host, scope), "host"), owner_dispatch=scope.get("owner") is True)
                    if scope.get("live") else _make("unknown", None, "unknown"))
        # A background harness (a workflow CEO, a side permission connection) never borrows the main thread's turn.
        if background == "order":
            return dict(_make("host_system", ("后台活", "background job"), "host"), owner_dispatch=order_owner is True)
        if isinstance(background, tuple) and len(background) == 2 and background[0] == "avatar":
            return _make("avatar", background[1], "host_read")     # security r1 M1: the avatar's own source
        if background is not None:
            return _make("unknown", None, "unknown")
        active = provenance._REQUEST.get()
        if active is not provenance._UNSET:
            return _from_normalized(host, active)
        child = provenance._shared_child(agent)
        if child is not provenance._UNSET:
            return _from_normalized(host, child)
        turn = host_turns.kind(host)
        fn = getattr(agent, "untrusted", None)
        if turn in _SYSTEM_KINDS and callable(fn) and fn() and getattr(agent, "untrusted_foreign", True):
            # A foreign (group / guest) envelope may still be running in this session (provenance.unattended_turn → None):
            # the host-injected turn cannot vouch for what reaches the harness now.
            return _make("unknown", None, "unknown")
        if turn in _SYSTEM_KINDS:
            kind, name, trust = _SYSTEM_KINDS[turn]
            return _make(kind, _SYSTEM_NAMES.get(name) if name else None, trust)
        send = getattr(agent, "cur_send", None)
        return _from_normalized(host, provenance.turn_source(agent), send)
    except Exception:  # noqa: BLE001 — a label never fails a request open; unknown is the strictest tier
        return _make("unknown", None, "unknown")


# ------------------------------------------------------------------ what the request touches
_PATH_KEYS = ("file_path", "filePath", "path", "notebook_path", "filename", "absolute_path", "workdir", "cwd")
_SHELL_TOOLS = ("bash", "exec_command", "shell_command", "execute_command", "shell", "monitor")


def _operation(tool):
    from . import policy_registry
    return policy_registry.harness_tool(tool).rsplit("__", 1)[-1].rsplit(".", 1)[-1].lower() if isinstance(tool, str) else ""


def _command(inp):
    raw = inp.get("command", inp.get("cmd", ""))
    if isinstance(raw, list):
        import shlex
        return shlex.join(str(w) for w in raw)
    return raw if isinstance(raw, str) else ""


def _words(tool, inp):
    """Every path-like operand a reviewer could name: tool path fields, and every word of every simple command
    (including inside substitutions / wrappers) plus redirect targets. Over-inclusive on purpose."""
    from . import danger
    out = [inp.get(k) for k in _PATH_KEYS if isinstance(inp.get(k), str)]
    text = _command(inp) if _operation(tool) in _SHELL_TOOLS else ""
    if text:
        try:
            for command in danger.lex(text):
                out += list(command.words) + list(getattr(command, "redirs", []) or [])
                for simple in danger.unwrap(command):
                    out += [simple.name] + list(simple.args) + list(getattr(simple, "redirs", []) or [])
        except Exception:  # noqa: BLE001 — an unparseable command still gets the raw-text scan below
            pass
    return [w for w in out if isinstance(w, str) and w], text


def _private_roots(host):
    roots = []
    state = getattr(getattr(host, "st", None), "root", None)
    if state is not None:
        roots.append(os.path.realpath(str(state)))
    return roots


def _expand(word, cwd, home):
    for prefix in ("${HOME}/", "$HOME/"):
        if word.startswith(prefix):
            word = "~/" + word[len(prefix):]
    if word.startswith("~/") or word == "~":
        word = home + word[1:]
    if not os.path.isabs(word):
        word = os.path.join(cwd or os.sep, word)
    return word


def _host_redacted_log(host, real):
    """The closed list of host-written, redacted files a turn may read: host.log (diagnostic_log.record) only."""
    st = getattr(host, "st", None)
    try:
        path = str(st.log_path)
        if real != os.path.realpath(path):
            return False
        row = os.lstat(path)
        import stat
        return stat.S_ISREG(row.st_mode) and row.st_nlink == 1
    except Exception:  # noqa: BLE001
        return False


def sensitive(host, tool, inp, cwd):
    """'credential' | 'private_state' | None. Credentials by uv_list's explicit inventory and danger.cred_path names;
    Agent J's own state dir (browser login profiles, account logs, raw job output) except the closed redacted list."""
    from . import danger, uv_list
    try:
        if uv_list.credential_reads(tool, inp, host=host, cwd=cwd):
            return "credential"
    except Exception:  # noqa: BLE001 — fail closed
        return "credential"
    home = (uv_list.host_environment(host).get("HOME") if host is not None else None) or os.path.expanduser("~")
    words, text = _words(tool, inp)
    roots = _private_roots(host)
    import glob as _glob
    expanded = []
    for word in words:
        # security r1 C2 / H1: `f=@.env`, `--data-binary=@.env`, globbed names (`~/.s*/id_*`, `.e?v`) — check every
        # tail after `=` / `@` and every name a glob would select (owner / host turns still never read credentials).
        parts = {word} | {word.split(sep, 1)[1] for sep in ("=", "@") if sep in word}
        for part in list(parts):
            if any(c in part for c in "*?[") and "$" not in part:
                pattern = _expand(part, cwd, home)
                try:
                    parts |= set(_glob.glob(pattern, recursive=False)[:256])
                except Exception:  # noqa: BLE001
                    return "credential"
        expanded += [p for p in parts if p]
    for word in expanded:
        if danger.cred_path(word):
            return "credential"
        if any(c in word for c in "\n\x00") or word.startswith("-"):
            continue
        # A bare word (a program name, a flag value) is a path only when it names something that exists here.
        if "/" not in word and not word.startswith(("~", ".")) and not os.path.lexists(os.path.join(cwd or os.sep, word)):
            continue
        path = os.path.normpath(_expand(word, cwd, home))
        real = os.path.realpath(path)
        if danger.cred_path(real):
            return "credential"
        for root in roots:
            for candidate in {path, real}:
                if candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep):
                    if not _host_redacted_log(host, real):
                        return "private_state"
    # Raw-text scan: a state path spelled through a variable, a glob or a quote the lexer split differently.
    if text:
        flat = text.replace("${HOME}", home).replace("$HOME", home).replace("~/", home + "/")
        for root in roots:
            if root in flat:
                rest = flat.split(root, 1)[1]
                tail = rest.split()[0] if rest.split() else ""
                if not _host_redacted_log(host, os.path.realpath(root + tail.strip("'\""))):
                    return "private_state"
    return None


_REFUSALS = {
    "credential": ("凭据文件不能由工具直接读取（不弹卡，也不执行）。请走既有凭据入口按名称使用。",
                   "Credential files cannot be read by a tool (no card, not executed). Use the configured credential entry by name."),
    "private_state": ("Agent J 自己的状态目录（登录资料、账户日志、后台原始输出）不能直接读取（不弹卡，也不执行）；"
                      "需要诊断请用 agentj 的只读命令或 host.log。",
                      "Agent J's private state directory (login profiles, account logs, raw job output) cannot be read "
                      "directly (no card, not executed); use agentj's read-only commands or host.log."),
    "unresolved_execution": ("看不懂这条启动命令实际执行什么，未执行（不弹卡）。", "Cannot resolve what this launcher executes; not run (no card)."),
    "write": ("这一轮来自「{label}」，不是主人本人；清单外的写操作不执行，也不发卡片（不是主人拒绝）。"
              "需要的话请主人在遥控器或本机终端里直接说。",
              "This turn comes from “{label_en}”, not the owner; an off-list write is not run and no card is sent (the owner "
              "did not refuse). If it is needed, the owner can ask directly from a remote or the local terminal."),
}


# security 0310 H1: a charge / IRREVERSIBLE call in background work the owner did NOT create / start (dispatched by a
# heartbeat, a system notice, another local session, a schedule enabled outside the paired phone / account) is refused
# without a card — otherwise any turn could launder it through a dispatch. 董事长 11-11 定 covers owner-made work only.
_UNOWNED = ("这一轮来自「{label}」，这项后台工作不是主人亲自派发或启用的；花钱 / 不可逆的操作（{shape}）不弹卡，也不执行。"
            "需要的话请主人在主线程里直接说，或由主人亲自派单 / 在手机上启用这个定时任务。",
            "This turn comes from “{label_en}”, background work the owner did not dispatch or enable; a money-moving or "
            "irreversible action ({shape}) is neither carded nor run. If it is needed, the owner can ask in the main "
            "thread, or dispatch the job / enable the scheduled task from the phone.")


def log_shape(shape):
    """Fixed label for host.log: IRREVERSIBLE keys as they are, charge shapes cut to program + verb (security 0310 L1)."""
    from . import uv_list
    if shape in uv_list.IRREVERSIBLE:
        return shape
    return " ".join(str(shape or "").split()[:2])[:32]


async def refuse_unowned(host, tool, inp, src, shape, *, receipt=None):
    """Refuse a fingerprint-list call in background work the owner did not create / start: audited, never a card."""
    import asyncio
    reason = "fingerprint_unowned_" + (src.get("type") or "unknown")
    rid, agent = await asyncio.to_thread(_record, host, tool, inp, "deny", reason)
    host.st.log("ask_done", id=rid, agent=agent, tool=tool, decision="deny", reason=reason,
                source_type=src.get("type"), trust=src.get("trust"), card=False, shape=log_shape(shape))
    if isinstance(receipt, dict):
        receipt.update(id=rid, reason=reason, approval_card_shown=False, source=src.get("type"))
    label = log_shape(shape)
    message = (_UNOWNED[0].format(label=src.get("label", ""), shape=label) + " / "
               + _UNOWNED[1].format(label_en=src.get("label_en", ""), shape=label))
    return {"behavior": "deny", "reason": reason, "message": message}


def _record(host, tool, inp, decision, reason):
    from . import approvals
    rid = secrets.token_hex(16)
    agent = getattr(getattr(host, "agent", None), "kind", "?")
    try:
        approvals.record(host.st, rid=rid, agent=agent, tool=tool, input_sha256=approvals.input_digest(inp),
                         shown_sha256=approvals.shown_digest(tool, "[off list: no card]"), decision=decision,
                         reason=reason, cats=[])
    except Exception:  # noqa: BLE001 — the host.log line still records the decision
        pass
    return rid, agent


_LS_FLAGS = frozenset({"-l", "-a", "-A", "-h", "-1", "-la", "-al", "-lh", "-lah", "-alh", "-hl"})
_GIT_STATUS_FLAGS = frozenset({"-s", "--short", "-b", "--branch", "--porcelain", "-sb"})


def _within_any(real, roots):
    from .host_turns import _within
    return any(_within(real, r) for r in roots)


def _private(host, real):
    """security r2 N1: the RESOLVED operand (after any cd) is itself a credential store or in Agent J's state dir."""
    from . import danger
    if danger.cred_path(real):
        return True
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in _private_roots(host))


def _file_ok(word, here, roots, host=None):
    """A regular, single-link file whose real path is under one of `roots` (no glob / variable: host_turns._path)."""
    import stat
    from .host_turns import _path
    real = _path(word, here)
    if real is None or not _within_any(real, roots) or _private(host, real):
        return False
    try:
        row = os.stat(real)
    except OSError:
        return False
    return stat.S_ISREG(row.st_mode) and row.st_nlink == 1


def _dir_ok(word, here, roots, host=None):
    from .host_turns import _path
    real = _path(word, here)
    return real is not None and os.path.isdir(real) and _within_any(real, roots) and not _private(host, real)


def closed_read(host, tool, inp, cwd, trust):
    """security r1 C1/C2/H2: the only reads a host_read / external / unknown turn runs without the owner — a closed
    program list on closed flags and verified operands, never danger.readonly's open list."""
    from . import danger, host_turns
    if not isinstance(inp, dict) or not cwd:
        return False
    roots = [os.path.realpath(cwd)]
    if trust == "host_read":
        roots += host_turns._skill_roots()
    op = _operation(tool)
    if op in ("read", "read_file", "file_read"):
        if set(inp) - {"file_path", "filePath", "path", "offset", "limit"}:
            return False
        word = inp.get("file_path") or inp.get("filePath") or inp.get("path")
        return isinstance(word, str) and _file_ok(word, cwd, roots, host)
    if op not in _SHELL_TOOLS:
        return False
    text = _command(inp)
    commands = danger.lex(text) if text else []
    if not commands:
        return False
    here = cwd
    for command in commands:
        if (command.subst or command.heredoc or command.ins or command.piped or command.redirs
                or getattr(command, "owner", None) is not None):
            return False
        simples = danger.unwrap(command)
        if len(simples) != 1:
            return False
        s = simples[0]
        if s.ins or s.redirs or s.flags or command.words[:1] != [s.name]:
            return False
        args = list(s.args)
        if s.name == "cd":
            if len(args) != 1 or not _dir_ok(args[0], here, roots, host):
                return False
            here = host_turns._path(args[0], here)
        elif s.name == "pwd":
            if args:
                return False
        elif s.name == "ls":
            flags = [a for a in args if a.startswith("-")]
            if set(flags) - _LS_FLAGS or _private(host, os.path.realpath(here)) or not all(
                    _dir_ok(a, here, roots, host) or _file_ok(a, here, roots, host) for a in args if not a.startswith("-")):
                return False
        elif s.name == "git":
            # security r2 N3: `git status` runs a repository's core.fsmonitor — only for host_read (owner-side) turns.
            if trust != "host_read" or args[:1] != ["status"] or set(args[1:]) - _GIT_STATUS_FLAGS:
                return False
        elif s.name == "agentj":
            if trust != "host_read":
                return False
            shapes = host_turns.AGENTJ_READ
            if not any(args == list(p) or args == list(p) + ["--json"] for p in shapes):
                return False
        elif s.name in host_turns.READERS:
            operands = host_turns._operands(s)
            if not operands or not all(_file_ok(w, here, roots, host) for w in operands):
                return False
        else:
            return False
    return True


def decide(host, tool, inp, cwd, src):
    """(decision, reason, message_key) for an off-list request. Pure apart from filesystem stats."""
    from . import danger
    trust = src.get("trust") if src.get("trust") in TRUST else "unknown"
    try:
        if danger.unresolved_execution(tool, inp):
            return "deny", "unresolved_execution", "unresolved_execution"
        hit = sensitive(host, tool, inp, cwd)
    except Exception:  # noqa: BLE001 — fail closed (a refusal, still no card)
        hit = "credential"
    if hit:
        return "deny", "off_list_" + hit, hit
    if trust in ("owner", "host"):
        return "allow", "off_list", None
    if closed_read(host, tool, inp, cwd, trust):
        return "allow", "off_list_read", None
    if _handoff_reply(src, tool, inp):
        return "allow", "off_list_handoff_reply", None
    return "deny", "off_list_write_" + trust, "write"


async def answer(host, tool, inp, cwd, *, receipt=None, settings_context=None, src=None, background=None,
                 fingerprint=None):
    """The answer to an off-list request: audited, never a card. The source is read on the loop (paired requests and
    shared children are bound to the running task); filesystem checks and the audit row run off-loop."""
    import asyncio
    src = src or source(host, settings_context=settings_context, background=background)
    if not cwd:     # no host agent_cfg (e.g. a configured-later host): the running adapter's own working directory
        cwd = ((getattr(getattr(host, "agent", None), "cfg", None) or {}).get("dir")) or None
    from . import handoff
    own_cfg = None
    try:   # P539 (#301): Agent J's own handoff file in an owner / host-system turn (read on the loop: contextvars)
        if (src.get("type") not in ("unknown", "avatar") and handoff.owner_side(getattr(host, "agent", None))
                and not host.stopped()):    # security r2 L4: never for a detached / avatar request
            own_cfg = host._switch_cfg()
    except Exception:  # noqa: BLE001 — no exemption, the ordinary decision below
        own_cfg = None
    own = own_cfg is not None and await asyncio.to_thread(
        lambda: handoff.own_read(tool, inp, own_cfg) or handoff.own_state(tool, inp, own_cfg))
    decision, reason, key = ("allow", "own_handoff", None) if own else await asyncio.to_thread(decide, host, tool, inp, cwd, src)
    if fingerprint and decision == "allow":
        # 董事长 11-11 定: a charge / irreversible call in a headless job or automation follows its harness — no card, no
        # refusal, only this log line (the shape label, never the command text).
        reason = "fingerprint_headless"
    rid, agent = await asyncio.to_thread(_record, host, tool, inp, decision, reason)
    host.st.log("ask_done", id=rid, agent=agent, tool=tool, decision=decision, reason=reason,
                source_type=src["type"], trust=src["trust"], card=False,
                **({"shape": log_shape(fingerprint)} if fingerprint else {}))
    if isinstance(receipt, dict):
        receipt.update(id=rid, reason=reason, approval_card_shown=False, source=src["type"])
    if decision == "allow":
        return {"behavior": "allow", "updatedInput": inp}
    zh, en = _REFUSALS[key]
    message = zh.format(**src) + " / " + en.format(**src)
    from . import provenance
    if key == "write" and src.get("type") == "host_system" and provenance.loop_refused(host):
        message += "\n" + provenance.LOOP[0] + " / " + provenance.LOOP[1]    # ADR-P570b: point at a scheduled task
    return {"behavior": "deny", "reason": reason, "message": message}
