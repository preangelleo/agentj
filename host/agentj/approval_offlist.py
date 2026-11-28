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
CARD_LIST = frozenset({"FriendsTellOnce", "AskUserQuestion", "SandboxOnce", "AuthRescue", "AgentJBoundedPlan", "company.write",
                       "agentj core mcp add", "agentj learn", "agentj plaza", "agentj local record", "agentj local relink",
                       "agentj relay", "agentj google youtube upload"})
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
    "busy_dispatch": ("后台派单 · {name}", "Background dispatch · {name}"),     # R-0047 item 3 (busy_native.Dispatch)
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
        out = _make("same_owner", _short(getattr(send, "fid", None) or src.get("fid") or src.get("relay") or "", 12), "external")
        seat = _SameOwnerSeat.capture(host, src, send)
        if seat is not None:     # R-0056: a host-verified paired same-owner device's own forwarded task (closed_diag)
            out.update(verified_same_owner=True, same_owner_seat=seat)
        return out
    if kind in ("wechat_task", "official_assist", "telegram", "email"):
        names = {"wechat_task": ("微信任务", "WeChat task"), "official_assist": ("官方协助", "official assist"),
                 "telegram": ("Telegram 群/转发", "Telegram group/forward"), "email": ("访客邮件", "guest mail")}
        return _make("external_channel", names[kind], "external")
    if kind == "external_mcp":
        return _make("external_mcp", _short(src.get("connector"), 8), "external")
    return _make("unknown", None, "unknown")


class _SameOwnerSeat:
    """R-0056 (chairman 11-18): the live, host-held fact that this turn is the paired same-owner device's own forwarded
    task — never a friend's (`friend_task`), never 「本机另一个会话」 (provenance.SameHost), never a reply / end message,
    never a wire- or transcript-shaped dict. Only `capture` makes one; `live()` is re-read at decision time."""
    __slots__ = ("host", "send")

    def __init__(self, host, send):
        self.host, self.send = host, send

    @classmethod
    def capture(cls, host, src, send):
        from . import provenance
        if type(src) is not provenance.HostDelegated or src.get("kind") != "same_owner" or send is None:
            return None
        seat = cls(host, send)
        return seat if seat.live() else None

    def live(self):
        from . import provenance
        from .same_owner import ForwardSend
        try:
            host, send = self.host, self.send
            owner = getattr(getattr(host, "peers", None), "same_owner", None)
            data = send.data if isinstance(getattr(send, "data", None), dict) else {}
            return (type(send) is ForwardSend and owner is not None and send.owner is owner and not send.approved
                    and not data.get("irt") and not data.get("end")
                    and type(send.source) is provenance.HostDelegated and send.source.get("kind") == "same_owner"
                    and getattr(getattr(host, "agent", None), "cur_send", None) is send
                    and getattr(host, "cur_turn", None) == send.turn and not host.stopped()
                    and owner.active(send.fid) and owner.row(send.fid).get("token") == send.token)
        except Exception:  # noqa: BLE001 — no seat
            return False


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


def dispatch_label(name, trust):
    """R-0047 item 3: the typed source of a busy dispatch (busy_native.Dispatch). Never `owner_dispatch`."""
    nzh, nen = (_short(n, 48) for n in name)
    return _make("busy_dispatch", (nzh, nen), trust if trust in ("host", "host_read", "external") else "unknown")


def _dispatch_live(host, background):
    """The busy dispatch named by `background` is still live (not stopped / estopped / ended, same epoch)."""
    if isinstance(background, tuple) and len(background) == 2 and background[0] == "busy_dispatch":
        from .busy_native import Dispatch
        return type(background[1]) is Dispatch and background[1].live(host)
    return True


def source(host, *, settings_context=None, background=None, order_owner=False):
    """The typed label of the turn asking now. Host-held objects only; `unknown` only when none says who.
    `owner_dispatch` (security 0310 H1): True only where the owner created / started this work — settings pages, an
    order the owner's own turn dispatched (host-held parent), a scheduled run the owner enabled / started."""
    from . import host_turns, provenance
    try:
        if isinstance(background, tuple) and len(background) == 2 and background[0] == "busy_dispatch":
            # R-0047 item 3: a native busy dispatch's runner (busy_native._runner_host) — the label bound at dispatch,
            # only while that exact dispatch is live; never the main thread's turn, never approval_review.ORIGIN.
            from .busy_native import Dispatch
            dispatch = background[1]
            if type(dispatch) is Dispatch and dispatch.live(host):
                return dict(dispatch.label)
            return _make("unknown", None, "unknown")
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
    "write": ("这一轮来自「{label}」。这项操作超出了该来源允许的范围。主机已拦下操作，没有弹出确认卡（不是主人拒绝）。"
              "如需继续，请主人在遥控器或本机终端直接下达这项任务。",
              "This turn comes from “{label_en}”. The operation is outside the permitted scope for this source. "
              "The host blocked it without sending a confirmation card. The owner can request it directly from a remote or the local terminal."),
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


def _record(host, tool, inp, decision, reason, state_subject=None):
    from . import approvals
    rid = secrets.token_hex(16)
    agent = getattr(getattr(host, "agent", None), "kind", "?")
    try:
        if state_subject is not None and not state_subject.live(host):
            return None
        record = (lambda **fields: state_subject.record(host, **fields)) if state_subject is not None else (lambda **fields: approvals.record(host.st, **fields))
        saved = record(rid=rid, agent=agent, tool=tool, input_sha256=approvals.input_digest(inp),
                         shown_sha256=approvals.shown_digest(tool, "[off list: no card]"), decision=decision,
                         reason=reason, cats=[])
        if state_subject is not None and (saved is None or not state_subject.live(host)):
            return None
    except Exception:  # noqa: BLE001 — no unconfirmed subject may become an allow
        if state_subject is not None:
            return None
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


def _cd_target(word, here):
    """security r3 F1: the real directory of `cd <word>`, only when the shell's logical move (typed path, `..` taken
    lexically) lands where the real path says — no symlink component followed by `..`. Else None."""
    from .host_turns import _path
    real = _path(word, here)
    if real is None:
        return None
    for prefix in ("$HOME/", "${HOME}/"):
        if word.startswith(prefix):
            word = "~/" + word[len(prefix):]
    typed = os.path.normpath(os.path.join(os.path.realpath(here or os.sep), os.path.expanduser(word)))
    return real if typed == real else None


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
            if len(args) != 1 or not _dir_ok(args[0], here, roots, host) or _cd_target(args[0], here) is None:
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


# ------------------------------------------------------------------ R-0056: same-owner read-only diagnostics
# Chairman 11-18: a host-verified paired same-owner device (not a friend) runs this CLOSED diagnostic list directly; UV-level
# actions keep the on-site card (uv_list.required() runs first in list_approval). Positive grammars only: anything not
# spelled out below is refused (security r0 Claude + Codex). Output still passes peer_guard.outbound before it leaves.
_DIAG_LS = frozenset("lahAt1SrdFpinsgoGcuUXv")          # read-only ls letters; never R (recurse) / L / H (follow links)
_DIAG_LS_LONG = frozenset({"--all", "--almost-all", "--human-readable", "--full-time"})
_DIAG_FIND = {"-name": "pattern", "-iname": "pattern", "-path": "pattern", "-type": "type", "-maxdepth": "depth",
              "-mtime": "number", "-size": "size", "-print": None}
_DIAG_SYSTEM = frozenset({"journalctl", "systemctl", "ls", "find", "cat", "head", "tail", "nl", "wc"})


def _diag_units():
    """The agentj service and its rescue watchdog, as service.py names them (with and without `.service`)."""
    from . import service
    try:
        names = {service.name()}
        names.add(service.rescue_name(next(iter(names))))
    except Exception:  # noqa: BLE001 — no unit name, no journal
        return frozenset()
    return frozenset(names | {n + ".service" for n in names})


def _journal_ok(args):
    import re
    units, seen, i, n_units = _diag_units(), set(), 0, 0
    since = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?: [0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?")
    while i < len(args):
        a, v = args[i], (args[i + 1] if i + 1 < len(args) else None)
        if a in ("--user", "--no-pager") and a not in seen:
            seen.add(a); i += 1
        elif a == "-u" and v in units and n_units < 2:
            n_units += 1; i += 2
        elif a == "-n" and "-n" not in seen and v is not None and re.fullmatch(r"[0-9]{1,3}", v) and 1 <= int(v) <= 500:
            seen.add(a); i += 2
        elif a == "--since" and a not in seen and v is not None and since.fullmatch(v):
            seen.add(a); i += 2
        elif a == "-o" and a not in seen and v in ("short", "short-iso"):
            seen.add(a); i += 2
        else:
            return False     # -f, --file, -D, --root, -M, -m, --system, -k, -o json, --cursor-file, writers, abbreviations …
    return {"--user", "--no-pager"} <= seen and n_units >= 1


def _systemctl_ok(args):
    units = _diag_units()
    return len(args) >= 3 and args[2] in units and (args == ["--user", "is-active", args[2]]
                                                    or args == ["--user", "status", args[2], "--no-pager", "-n", "0"])


def _agentj_ok(args):
    from . import host_turns
    # `doctor --json` repairs (Codex r0 BLOCK): only the closed --read-only path. AGENTJ_READ = listing branches only.
    return (args in (["status"], ["doctor", "--read-only", "--json"])
            or any(args == list(p) or args == list(p) + ["--json"] for p in host_turns.AGENTJ_READ))


def _ls_ok(args, here, roots, host):
    for a in args:
        if a.startswith("--"):
            if a not in _DIAG_LS_LONG:
                return False
        elif a.startswith("-") and len(a) > 1:
            if set(a[1:]) - _DIAG_LS:
                return False
        elif not (_dir_ok(a, here, roots, host) or _file_ok(a, here, roots, host)):
            return False
    return not _private(host, os.path.realpath(here))


def _find_ok(args, here, roots, host):
    import re
    from .host_turns import _path, _within
    i, starts = 0, []
    while i < len(args) and not args[i].startswith("-"):
        starts.append(args[i]); i += 1
    home = os.path.realpath(os.path.expanduser("~"))
    for word in starts or ["."]:
        real = _path(word, here)
        # Every start point is a working-folder directory that does not hold the home folder or Agent J's state dir
        # (find -P never follows links, but it would list the names of a private tree it starts above).
        if (not _dir_ok(word, here, roots, host) or real is None or _within(home, real)
                or any(_within(r, real) for r in _private_roots(host))):
            return False
    checks = {"pattern": lambda v: not v.startswith("-") and not any(c in v for c in "\n\x00"),
              "type": lambda v: v in ("f", "d", "l"),
              "depth": lambda v: re.fullmatch(r"[0-3]", v) is not None,
              "number": lambda v: re.fullmatch(r"[+-]?[0-9]{1,5}", v) is not None,
              "size": lambda v: re.fullmatch(r"[+-]?[0-9]{1,10}[bcwkMG]?", v) is not None}
    while i < len(args):
        kind = _DIAG_FIND.get(args[i], False)
        if kind is False:
            return False     # -L/-H/-follow, -files0-from, -D, -newer*, -fprint*, -fls, -exec*, -ok*, -delete, ( ! -o …
        if kind is None:
            i += 1
            continue
        if i + 1 >= len(args) or not checks[kind](args[i + 1]):
            return False
        i += 2
    return True


def _log_ok(host, word, here):
    """Exactly the host-owned redacted host.log (_host_redacted_log), named without any symlink on the way."""
    from .host_turns import _path
    real = _path(word, here)
    try:
        for prefix in ("$HOME/", "${HOME}/"):
            if word.startswith(prefix):
                word = "~/" + word[len(prefix):]
        configured = os.path.abspath(str(host.st.log_path))
        # The spelled path itself (lexically) must already be the real path: no symlink in any ancestor.
        typed = os.path.normpath(os.path.join(os.path.realpath(here or os.sep), os.path.expanduser(word)))
    except Exception:  # noqa: BLE001
        return False
    return (real is not None and real == configured == os.path.realpath(configured) == typed
            and _host_redacted_log(host, real))


def _exe_ok(host, name, cwd):
    """The program the shell would run for `name` (host PATH) is a real installed binary: no relative PATH entry before
    it, not inside the working folder, not group/other-writable; system tools root-owned (not a planted `journalctl`)."""
    from . import uv_list
    from .host_turns import _within
    env = uv_list.host_environment(host) if host is not None else os.environ
    work = os.path.realpath(cwd or os.sep)
    for entry in (env.get("PATH") or "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            return False
        candidate = os.path.join(entry, name)
        if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
            continue
        real = os.path.realpath(candidate)
        try:
            row = os.stat(real)
        except OSError:
            return False
        if _within(real, work) or row.st_mode & 0o022 or _private(host, real):
            return False
        return row.st_uid == 0 if name in _DIAG_SYSTEM else row.st_uid in (0, os.getuid())
    return False


_DIAG_PLAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/=:,+@%-")
_DIAG_GLOB = frozenset("*?[]")
_DIAG_PATTERN_OPS = frozenset({"-name", "-iname", "-path"})


def _diag_tokens(text):
    r"""security r1/r2: the ONLY words a diagnostic request may be, by a positive whole-text grammar independent of
    danger.lex — one simple command on one line. Outside quotes: [A-Za-z0-9._/=:,+@%-] and single spaces between
    words; inside '…' or "…": the same plus space and * ? [ ] (a quoted glob, kept for find patterns only). Nothing
    else anywhere — no newline / CR / NUL / tab, # ; & | < > ( ) $ ` \ { } ~ !, no empty or unterminated quotes.
    Returns (words, glob flags) or None."""
    if not isinstance(text, str) or not text or len(text) > 4096:
        return None
    words, globbed, cur, glob, quote, opened = [], [], None, False, None, 0
    for c in text:
        if quote is not None:
            if c == quote:
                quote = None
                if len(cur) == opened:
                    return None          # an empty '' / "" segment: nothing a diagnostic needs
            elif c in _DIAG_PLAIN or c == " " or c in _DIAG_GLOB:
                cur, glob = cur + c, glob or c in _DIAG_GLOB
            else:
                return None
        elif c in "'\"":
            quote, cur = c, cur if cur is not None else ""
            opened = len(cur)
        elif c == " ":
            if cur is not None:
                words.append(cur); globbed.append(glob)
                cur, glob = None, False
        elif c in _DIAG_PLAIN:
            if cur is None and c == "=":
                return None              # zsh (macOS default shell) expands a word-initial `=cmd` to a path
            cur = (cur or "") + c
        else:
            return None
    if quote is not None:
        return None
    if cur is not None:
        words.append(cur); globbed.append(glob)
    if not words or any(w == "" for w in words):
        return None
    return words, globbed


def _diag_command(text, cwd):
    """(directory, command text, words) of a diagnostic request: one simple command, optionally behind EXACTLY the prefix the host
    itself writes for a harness-reported folder (approval_jev.with_cwd: "cd " + shlex.quote(dir) + " && "). Else None."""
    import shlex
    here, rest = cwd, text
    if isinstance(text, str) and text.startswith("cd ") and " && " in text:
        cut = text.rindex(" && ")
        try:
            parts = shlex.split(text[3:cut])
        except ValueError:
            return None
        if len(parts) != 1 or not os.path.isabs(parts[0]) or "cd " + shlex.quote(parts[0]) + " && " != text[:cut + 4]:
            return None
        # security r3 F1: the typed directory IS its own real path — no `.` / `..` / `//`, no symlink component — so
        # the shell's logical `cd` and this judgement land in the same place.
        if not parts[0] == os.path.normpath(parts[0]) == os.path.realpath(parts[0]):
            return None
        here, rest = parts[0], text[cut + 4:]
    parsed = _diag_tokens(rest)
    if parsed is None:
        return None
    words, globbed = parsed
    # A quoted glob only as the argument of a find -name / -iname / -path predicate.
    if any(g and not (words[0] == "find" and i > 0 and words[i - 1] in _DIAG_PATTERN_OPS) for i, g in enumerate(globbed)):
        return None
    return here, rest, words


def closed_diag(host, tool, inp, cwd):
    """R-0056: closed_read's shapes plus the closed diagnostic list, for a verified same-owner device only (decide)."""
    from . import danger, host_turns
    if not isinstance(inp, dict) or not cwd:
        return False
    roots = [os.path.realpath(cwd)]
    op = _operation(tool)
    if op in ("read", "read_file", "file_read"):
        if set(inp) - {"file_path", "filePath", "path", "offset", "limit"}:
            return False
        word = inp.get("file_path") or inp.get("filePath") or inp.get("path")
        return isinstance(word, str) and (_file_ok(word, cwd, roots, host) or _log_ok(host, word, cwd))
    if op not in _SHELL_TOOLS:
        return False
    parsed = _diag_command(_command(inp), cwd)
    if parsed is None:
        return False
    here, rest, words = parsed
    if not _dir_ok(here, cwd, roots, host):
        return False
    here = host_turns._path(here, cwd)
    # danger.lex must see exactly the same words (security r2: any comment / quote desync is a refusal, never an allow).
    commands = danger.lex(rest)
    if len(commands) != 1 or list(commands[0].words) != words:
        return False
    for command in commands:
        if (command.subst or command.heredoc or command.ins or command.piped or command.redirs
                or getattr(command, "owner", None) is not None):
            return False
        simples = danger.unwrap(command)
        if len(simples) != 1:
            return False
        s = simples[0]
        if s.ins or s.redirs or s.flags or command.words[:1] != [s.name] or [s.name] + list(s.args) != words:
            return False
        args = list(s.args)
        if not _exe_ok(host, s.name, cwd):
            return False
        if s.name != "find" and any(c in a for a in args for c in "*?["):
            return False     # a quoted glob is only ever a find -name / -iname / -path pattern
        if s.name == "ls":
            ok = _ls_ok(args, here, roots, host)
        elif s.name == "find":
            ok = _find_ok(args, here, roots, host)
        elif s.name == "journalctl":
            ok = _journal_ok(args)
        elif s.name == "systemctl":
            ok = _systemctl_ok(args)
        elif s.name == "agentj":
            ok = _agentj_ok(args)
        elif s.name in host_turns.READERS:
            operands = host_turns._operands(s)      # closed flags: no -f / -F / --follow, no --files0-from
            ok = bool(operands) and all(_file_ok(w, here, roots, host) or _log_ok(host, w, here) for w in operands)
        else:
            ok = False
        if not ok:
            return False
    return True


def _same_owner_live(src):
    seat = src.get("same_owner_seat")
    return src.get("verified_same_owner") is True and type(seat) is _SameOwnerSeat and seat.live()


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
    if trust == "external" and _same_owner_live(src) and closed_diag(host, tool, inp, cwd):
        return "allow", "off_list_diag", None      # R-0056: the relation is re-checked here, at decision time
    if _handoff_reply(src, tool, inp):
        return "allow", "off_list_handoff_reply", None
    return "deny", "off_list_write_" + trust, "write"


async def answer(host, tool, inp, cwd, *, receipt=None, settings_context=None, src=None, background=None,
                 fingerprint=None, state_subject):
    """The answer to an off-list request: audited, never a card. The source is read on the loop (paired requests and
    shared children are bound to the running task); filesystem checks and the audit row run off-loop."""
    import asyncio
    def live():     # R-0047 item 3 / R-0056 (security r1 F2): dispatch binding and same-owner seat, wherever the State is
        return (state_subject.live(host) and (src.get("type") != "busy_dispatch" or _dispatch_live(host, background))
                and (src.get("verified_same_owner") is not True or _same_owner_live(src)))

    def changed():
        if isinstance(receipt, dict):
            receipt.update(reason='state_authority_changed', approval_card_shown=False)
        return {'behavior':'deny', 'reason':'state_authority_changed',
                'message':'Controller state changed; no operation confirmed.'}
    if not state_subject.live(host):
        return changed()
    src = src or source(host, settings_context=settings_context, background=background)
    if not live():
        return changed()
    if not cwd:     # no host agent_cfg (e.g. a configured-later host): the running adapter's own working directory
        cwd = ((getattr(getattr(host, "agent", None), "cfg", None) or {}).get("dir")) or None
    from . import handoff
    own_cfg = None
    try:   # P539 (#301): Agent J's own handoff file in an owner / host-system turn (read on the loop: contextvars)
        if (src.get("type") not in ("unknown", "avatar", "busy_dispatch") and handoff.owner_side(getattr(host, "agent", None))
                and not host.stopped()):    # security r2 L4: never detached / avatar / dispatch
            own_cfg = host._switch_cfg()
    except Exception:  # noqa: BLE001 — no exemption, the ordinary decision below
        own_cfg = None
    own = own_cfg is not None and await asyncio.to_thread(
        lambda: handoff.own_read(tool, inp, own_cfg) or handoff.own_state(tool, inp, own_cfg))
    if not live():
        return changed()
    decision, reason, key = ("allow", "own_handoff", None) if own else await asyncio.to_thread(decide, host, tool, inp, cwd, src)
    if not live():
        return changed()
    if fingerprint and decision == "allow":
        # 董事长 11-11 定: a charge / irreversible call in a headless job or automation follows its harness — no card, no
        # refusal, only this log line (the shape label, never the command text).
        reason = "fingerprint_headless"
    recorded = await asyncio.to_thread(_record, host, tool, inp, decision, reason, state_subject)
    if recorded is None or not live():
        return changed()
    rid, agent = recorded
    host.st.log("ask_done", id=rid, agent=agent, tool=tool, decision=decision, reason=reason,
                source_type=src["type"], trust=src["trust"], card=False,
                **({"shape": log_shape(fingerprint)} if fingerprint else {}))
    if isinstance(receipt, dict):
        receipt.update(id=rid, reason=reason, approval_card_shown=False, source=src["type"])
    if not live():
        return changed()
    if decision == "allow":
        return {"behavior": "allow", "updatedInput": inp}
    zh, en = _REFUSALS[key]
    message = zh.format(**src) + " / " + en.format(**src)
    from . import provenance
    if key == "write" and src.get("type") == "host_system" and provenance.loop_refused(host):
        message += "\n" + provenance.LOOP[0] + " / " + provenance.LOOP[1]    # ADR-P570b: point at a scheduled task
    return {"behavior": "deny", "reason": reason, "message": message}
