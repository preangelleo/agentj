"""Host-owned action policy shared by all four entry layers (P512).

Identifiers come from executors, never model safety/approval claims. This is an
application boundary, not protection from code running under the host's own uid.
Run ``python -m agentj.policy_registry`` to export the authoritative JSON table.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import json
import re
from types import MappingProxyType

LAYERS = ("main", "channel", "friend", "support")
# Host-only capability to request a card, never to approve or auto-execute.
COMPANY_PROPOSAL = object()


@dataclass(frozen=True)
class Policy:
    effect: str                  # none | reversible | irreversible | unknown
    confirmation: str            # none | hold | uv | deny
    friend: bool = False         # tool-less conversational work only
    channel: bool = True         # preserve the legacy channel allowlist
    support: bool = False        # conversation or registered company API only
    main_confirmation: str | None = None  # owner-window exception; other layers unchanged

    @property
    def channel_forbidden(self):
        return self.confirmation != "none" or not self.channel


FREE = Policy("none", "none", friend=True, support=True)
NATIVE = Policy("none", "none")
HOLD = Policy("reversible", "hold")
UV = Policy("irreversible", "uv")
UNKNOWN = Policy("unknown", "deny")
# Company API level is loaded and validated from the owner's tool definition.
# Never accept a level or canonical action supplied by the model.
COMPANY_READ = Policy("none", "none", channel=False, support=True)
COMPANY_WRITE = Policy("irreversible", "uv", support=True)


def _table():
    rows = {}
    for p, names in (
        (FREE, ("answer", "translate", "summarize", "rewrite")),
        (NATIVE, ("image.generate", "image.edit", "web.search")),
        # P551 (#321): sending a sub-agent off is ① — its own tool calls come back through this same table one by one
        (Policy("none", "none", channel=False), ("agent.dispatch",)),
        # P553 (#326): the Agent's own session housekeeping (stop its background job, plan mode, its task list, wake itself
        # later, a notice to the owner) — ①, nothing outside the session changes; what it runs later is checked call by call
        (Policy("none", "none", channel=False), ("agent.session",)),
        (Policy("none", "none", channel=False), ("file.read",)),
        (Policy("reversible", "hold", main_confirmation="none"), ("file.write",)),
        # P551 (#318): mail, calendar edits and same-owner changes can be taken back or corrected — reversible. The table
        # below is the Agent's floor; an owner's own tap on a paired remote is the instruction itself (confirm_tier.py).
        (HOLD, ("file.trash", "settings.write", "memory.restore", "mail.send", "calendar.write", "publish")),
        (UV, ("file.delete", "secrets.read", "secrets.write",
              "payment", "admin", "device.pair", "device.revoke",
              "computer.control")),   # P539: drive this computer's screen, mouse and keyboard (Computer Use)
        (COMPANY_READ, ("company.read",)),
        (COMPANY_WRITE, ("company.write",)),
    ):
        for name in names:
            if name in rows:
                raise ValueError("duplicate action")
            rows[name] = p
    return rows


REGISTRY = MappingProxyType(_table())


def policy(action):
    return REGISTRY.get(action, UNKNOWN) if isinstance(action, str) else UNKNOWN


def decision(layer, action):
    """Required next step; never an approval ticket or evidence of owner identity."""
    p = policy(action)
    if layer not in LAYERS or p.confirmation == "deny":
        return "deny"
    if layer == "channel":
        return "deny" if not p.channel else "handoff" if p.channel_forbidden else "allow"
    if layer == "friend" and not p.friend:
        return "handoff"
    if layer == "support" and not p.support:
        return "deny"
    confirmation = p.main_confirmation if layer == "main" and p.main_confirmation is not None else p.confirmation
    return "allow" if confirmation == "none" else confirmation


# Main-window host cards: these tools are already routed to Host.ask. Preserve
# their existing individual signed-card/UV executors; a table lookup never grants
# execution or replaces signature, nonce, target, TTL or final-executor checks.
HOST_TOOLS = MappingProxyType({
    "pair_device": "device.pair", "SandboxOnce": "admin", "AuthRescue": "secrets.read",
    "DoomLoop": "settings.write", "task": "agent.dispatch", "AskUserQuestion": "settings.write",
    "AgentJHook": "settings.write", "AgentJTerminal": "admin",
    "agentj mail": "mail.send", "agentj mail send": "mail.send",
    "agentj mail notes": "mail.send", "agentj mail reply": "mail.send",
    "agentj calendar": "calendar.write",
    "agentj data": "payment", "agentj report script": "payment",
    "agentj config": "settings.write", "agentj goal": "settings.write",
    "agentj goal grant": "settings.write", "agentj quota switch": "settings.write",
    "agentj automation resume": "settings.write", "agentj plaza": "admin",
    "AgentJSameOwnerAction": "publish",
})


# Exact native tool names only. Shell aliases retain the strict external row;
# main_requirement refines known ordinary calls using the existing danger lexer.
NATIVE_TOOLS = MappingProxyType({
    **dict.fromkeys(("Read", "Glob", "Grep", "LS", "read_file", "list_dir", "view_image"), "file.read"),
    **dict.fromkeys(("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"), "file.write"),
    "Delete": "file.delete", "WebFetch": "web.search", "WebSearch": "web.search",
    **dict.fromkeys(("Bash", "exec_command", "shell_command", "exec"), "admin"),
    **dict.fromkeys(("Task", "Agent"), "agent.dispatch"),
    # P553: session to-dos / plan / question tool ① like TaskCreate
    **dict.fromkeys(("TodoWrite", "update_plan", "request_user_input", "todowrite", "question"), "agent.session"),
    # P553 (#326) inventory: Claude Code 2.1.296 / Codex 0.162.1 / OpenCode 1.18.34 names that were unknown → deny. A loader
    # (ToolSearch, tool_search) only fetches a tool's schema: ①; the loaded tool's own call is judged by its own row.
    **dict.fromkeys(("ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool", "NotebookRead", "BashOutput", "TaskOutput",
                     "TaskGet", "TaskList", "ListAgents", "CronList", "LSP", "Skill",
                     "tool_search", "list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource", "list_agents",
                     "glob", "grep", "list", "lsp", "skill", "codesearch", "ExternalDirectory"), "file.read"),
    **dict.fromkeys(("TaskStop", "KillShell", "KillBash", "ScheduleWakeup", "EnterPlanMode", "ExitPlanMode", "TaskCreate",
                     "TaskUpdate", "CronCreate", "CronDelete", "PushNotification", "SendUserMessage", "StructuredOutput",
                     "ReportFindings", "Sleep", "TeamCreate", "TeamDelete", "wait_agent", "close_agent",
                     "plan_enter", "plan_exit"), "agent.session"),
    **dict.fromkeys(("SendMessage", "Workflow", "spawn_agent", "send_input", "resume_agent", "followup_task"),
                    "agent.dispatch"),
    **dict.fromkeys(("EnterWorktree", "ExitWorktree"), "file.write"),     # ExitWorktree discarding changes: ③ below
    "Config": "settings.write",
    "DesignSync": "publish",                                              # read methods: ① below
    # Monitor runs a shell command (judged by its content like Bash) or reads a WebSocket; write_stdin / js_repl / PowerShell
    # run code the lexer cannot read (③); RemoteTrigger starts cloud runs outside Agent J's hook (③, reads ①)
    **dict.fromkeys(("Monitor", "write_stdin", "js_repl", "PowerShell", "RemoteTrigger"), "admin"),
})
# MCP server names are not authority. Recognized operation names get a floor;
# unrecognized operations fail closed, including plausible but unregistered reads.
MCP_TOOLS = MappingProxyType({
    "read_file": "file.read", "list_directory": "file.read", "search_files": "file.read",
    "write_file": "file.write", "edit_file": "file.write", "delete_file": "file.delete",
    "send_email": "mail.send", "execute_command": "admin", "bash": "admin",
    "web_search": "web.search", "generate_image": "image.generate", "edit_image": "image.edit",
})


# P539 (#300): Computer Use. Server names are normally no authority (anyone can name a server anything), but for this one
# effect class the bundled servers are known: the Codex app's computer-use plugin and its unified variant. Every operation
# of these servers — clicks, typing, keys, and screen reads too (a screenshot can show anything on screen) — is
# computer.control. Elsewhere only the exact pointer / keyboard operation names below get that floor; a pre-authorization
# (preauth.py) covers only the allowlisted servers, never a look-alike operation name on another server.
COMPUTER_USE_SERVERS = frozenset({"computer-use", "computer_use", "unified-computer-use"})
COMPUTER_USE_OPS = frozenset({
    "left_click", "right_click", "middle_click", "double_click", "triple_click", "left_click_drag",
    "mouse_move", "left_mouse_down", "left_mouse_up", "type_text", "press_key", "hold_key",
    "perform_secondary_action", "get_app_state",
})


# M1 (P539 review): what a 「Computer Use 免问」 grant may run without a card — a closed set of known operations, and only on
# an allowlisted server. Any other operation those servers expose is still computer.control (a passkey card), never covered.
COMPUTER_USE_GRANT_OPS = COMPUTER_USE_OPS | frozenset({
    "click", "scroll", "screenshot", "get_screenshot", "zoom", "cursor_position", "key", "type", "wait",
    # Claude Code's own computer-use server (`mcp__computer-use__*`, read from the installed claude binary, P539): its
    # pointer / keyboard / screen / app operations. NOT here (cards every time): access changes (request_access,
    # request_full_control, request_teach_access, teach_step, teach_batch), read_clipboard (whatever was copied, e.g. a
    # password), and the batches (computer_batch, app_batch: many steps the quiet phone line cannot show).
    "open_application", "switch_display", "list_granted_applications", "write_clipboard",
    "release_full_control", "list_apps", "app_ax_find", "app_bring_to_current_space", "app_click", "app_drag", "app_key",
    "app_list_windows", "app_menu", "app_release", "app_screenshot", "app_scroll", "app_type",
})


# P539: the Codex app's bundled Computer Use plugin. Its MCP server is `cua_repl` (plugin manifest .mcp.json) with tools
# `js` / `js_reset` (an automation script: clicks, typing, anything) and `turn_ended` (called by the plugin's own Stop /
# Interrupt hooks: lifecycle, no effect). The server name alone is no authority (any config can name a server cua_repl);
# the authority is the `pluginId` Codex itself puts on its mcpToolCall item — "<plugin>@<marketplace>" (probed, codex
# 0.162.1, reports/qa/p539/codex-mcp-probe/plugin-*.jsonl; a config server has pluginId null, and Codex only accepts the
# `openai-bundled` marketplace from its own bundled-marketplaces directory). Exact ids only.
CODEX_CU_PLUGINS = frozenset({"unified-computer-use@openai-bundled", "computer-use@openai-bundled"})
CODEX_CU_SERVER = "cua_repl"
CODEX_CU_SCRIPT_OPS = frozenset({"js", "js_reset"})
CODEX_CU_LIFECYCLE_OPS = frozenset({"turn_ended"})


def codex_plugin_bind(plugin_id, server) -> dict | None:
    """The binding of a Codex mcpToolCall item from the bundled Computer Use plugin, or None. Only ever built from
    Codex's own item (never from a server's elicitation message / _meta)."""
    if isinstance(plugin_id, str) and plugin_id in CODEX_CU_PLUGINS and server == CODEX_CU_SERVER:
        return {"kind": "codex_plugin", "id": plugin_id, "server": server}
    return None


def _codex_cu(tool, bind, ops) -> bool:
    return (isinstance(bind, dict) and bind.get("kind") == "codex_plugin" and bind.get("id") in CODEX_CU_PLUGINS
            and bind.get("server") == CODEX_CU_SERVER and isinstance(tool, str)
            and tool in {f"mcp__{CODEX_CU_SERVER}__{op}" for op in ops})


def bound_action(tool, bind) -> str | None:
    """computer.control for the plugin's script tools under a trusted plugin binding; else None (the name decides)."""
    return "computer.control" if _codex_cu(tool, bind, CODEX_CU_SCRIPT_OPS) else None


def codex_cu_lifecycle(tool, bind) -> bool:
    """The plugin's own `turn_ended` hook call: allowed without a card (no effect), only under its binding."""
    return _codex_cu(tool, bind, CODEX_CU_LIFECYCLE_OPS)


def computer_use_grant_op(tool) -> bool:
    """mcp__<allowlisted server>__<known Computer Use operation> — what a preauth grant can cover (P539 M1)."""
    return computer_use_server(tool) and tool.split("__")[2] in COMPUTER_USE_GRANT_OPS


def computer_use_server(tool) -> bool:
    """mcp__<allowlisted computer-use server>__<operation>: the known Computer Use effect class (P539)."""
    if not isinstance(tool, str) or not tool.startswith("mcp__"):
        return False
    parts = tool.split("__")
    return len(parts) == 3 and parts[1] in COMPUTER_USE_SERVERS and bool(parts[2])


def tool_action(tool, *, canonical=True):
    if not isinstance(tool, str):
        return None
    if tool in HOST_TOOLS:
        return HOST_TOOLS[tool]
    if tool in NATIVE_TOOLS:
        return NATIVE_TOOLS[tool]
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        if computer_use_server(tool):
            return "computer.control"
        if len(parts) == 3 and parts[1] and parts[2] in COMPUTER_USE_OPS:
            return "computer.control"
        return MCP_TOOLS.get(parts[2]) if len(parts) == 3 and parts[1] else None
    return tool if canonical and tool in REGISTRY else None


def channel_tool_decision(tool):
    """Preserve old native-tool denial except the owner's intentional search opening.

    Canonical executor actions retain their table row. Native spellings that used
    to be unknown may only become automatic for read-only web search/fetch.
    """
    action = tool_action(tool)
    result = decision("channel", action)
    if result == "allow" and tool != action and action != "web.search":
        return "deny"
    return result


def dispatch(tool):
    """P551 (#321): Claude Code `Agent` / `Task`, OpenCode `task` — sending a sub-agent off. ① in every level: no card."""
    return tool_action(tool) == "agent.dispatch"


def main_host_card(tool, tool_input=None, *, requirement=None):
    """Use the request's computed floor; standalone callers supply real inputs."""
    if requirement is None:
        requirement = main_requirement(tool, tool_input)
    return requirement != "allow"


IRREVERSIBLE = frozenset({"credentials", "spend", "price", "delete"})
_READ_METHODS = {"RemoteTrigger": ("action", frozenset({"list", "get", "list_runs", "get_run_log"})),
                 "DesignSync": ("method", frozenset({"list_projects", "get_project", "list_files", "get_file"}))}
_SECRET_REF = re.compile(r"\$\{?\w*(?:KEY|TOKEN|SECRET|PASS|CRED|AUTH)\w*|\bprintenv\b", re.I)


def _own_card(args):
    """P551 (#321): `agentj sudo` and `agentj secret request|result|status|log` open their own owner card on the phone (the
    admin password / the key typed there, Face ID where the remote has it) — no second Face ID card in front of them.
    Anything else under `agentj secret` (send: a value handed out) keeps the shell-layer Face ID."""
    if args[:1] == ["sudo"]:
        return True
    return args[:1] in (["secret"], ["secrets"]) and args[1:2] in (["request"], ["result"], ["status"], ["log"])


def main_requirement(tool, tool_input=None, *, env=None):
    """Owner-window floor from real arguments, never a model's safety claim.

    Known shell tools are classified by content; unknown tools still deny.
    Canonical actions and external-layer decisions keep their own fixed rows.
    """
    action = tool_action(tool)
    requirement = decision("main", action)
    if requirement == "deny":
        return requirement
    from . import danger
    inp = tool_input if isinstance(tool_input, dict) else {}
    name = tool
    operation = tool.split("__")[-1] if tool.startswith("mcp__") else tool
    if tool == "Monitor" and "command" not in inp:
        ws = inp.get("ws") if isinstance(inp.get("ws"), dict) else {}
        name, inp, requirement = "WebFetch", {"url": str(ws.get("url", ""))}, "allow"   # P553: a WebSocket read
    elif tool in _READ_METHODS and inp.get(_READ_METHODS[tool][0]) in _READ_METHODS[tool][1]:
        return "allow"                   # P553: RemoteTrigger / DesignSync read methods
    elif tool == "ExitWorktree" and inp.get("action") == "remove" and inp.get("discard_changes"):
        return "uv"                      # P553: uncommitted work and the branch are deleted
    elif operation in ("Bash", "exec_command", "shell_command", "execute_command", "bash", "Monitor"):
        name, inp = "Bash", {"command": inp.get("command", inp.get("cmd", ""))}
        requirement = "allow"
        # The existing lexer handles wrappers and substitutions. Elevation and
        # Agent J's credential executor are sensitive even with harmless operands.
        for command in danger.lex(str(inp["command"])):
            for simple in danger.unwrap(command):
                if "elevated" in simple.flags or simple.name in ("sudo", "su", "doas", "run0", "pkexec"):
                    return "uv"
                if simple.name == "agentj" and simple.args[:1] in (["revoke"], ["unlink"]):
                    return "uv"          # P550: unbinding a remote / taking the host out of the account (its seat) is ③
                if simple.name == "agentj" and simple.args[:1] in (["secret"], ["secrets"], ["sudo"]):
                    if not _own_card(simple.args):
                        return "uv"
            if command.words and danger._base(command.words[0]) in ("sudo", "su", "doas", "run0", "pkexec"):
                return "uv"
    elif action in ("file.write", "file.read"):
        name = tool if tool in danger._FILE_TOOLS else "Write" if action == "file.write" else "Read"
        inp = {**inp, "file_path": inp.get("file_path") or inp.get("path") or inp.get("notebook_path")}
    if tool == "agentj calendar" and inp.get("operation") == "delete":
        return "uv"                      # P551: a remote event deleted cannot be brought back by Agent J
    if tool == "apply_patch":
        import re
        patch = str(inp.get("command", inp.get("patch", inp.get("input", ""))))
        for kind, path in re.findall(r"^\*\*\* (Add|Update|Delete) File: (.+)$", patch, re.M):
            if kind == "Delete" or danger.cred_path(path) or danger.hook_path(path):
                return "uv"
    verdict = danger.classify(name, inp, env=env)
    shared = danger.classify_shared(name, inp, env=env)
    cats = set(verdict.cats) | set(shared.cats)
    if cats & IRREVERSIBLE:
        return "uv"                      # P551 (#318) ③ irreversible: Face ID, whoever started it
    if "send" in cats and name == "Bash" and _SECRET_REF.search(str(inp.get("command", ""))):
        return "uv"                      # P551: a secret-looking variable going out is a key revealed — ③, not ②
    if cats and requirement != "uv":     # incl. `send` — outward messages are ② (a long-press card for the Agent)
        return "hold"  # reversible but unclear/sensitive; no blanket shell UV
    return requirement                   # P550: a ③ row (computer.control …) is never lowered by its content to ②


def unknown_text(tool, lang=None):
    """P553 (#326): the reason given for an unknown action — a plain denial naming the tool, never 「需要主人确认」 (no card is
    shown for it). lang None → both languages (a hook process does not know the owner's language)."""
    from .text import clean_line
    name = clean_line(tool if isinstance(tool, str) else "?", 64) or "?"
    zh = f"Agent J：未知动作，已拒绝：{name}（这是 Agent J 的缺陷，请反馈）"
    en = f"Agent J: unknown action, denied: {name} (this is an Agent J defect, please report it)"
    if lang is None:
        return zh + " / " + en
    return en if str(lang).lower().startswith("en") else zh


def log_unknown(st, tool, tool_input=None, *, agent="?"):
    """P553: an unknown-action denial leaves one approvals.log line (reason unknown_action, input hash only) and one host.log
    line naming the tool, so support can find it. Best effort: logging never turns the denial into anything else."""
    from .text import clean_line
    if st is None:
        return
    name = clean_line(tool if isinstance(tool, str) else "?", 64) or "?"
    try:
        import secrets
        from . import approvals
        approvals.record(st, rid=secrets.token_hex(16), agent=agent if isinstance(agent, str) else "?", tool=name,
                         input_sha256=approvals.input_digest(tool_input if isinstance(tool_input, dict) else {}),
                         shown_sha256=approvals.shown_digest(name, unknown_text(name, "en")),
                         decision="deny", reason="unknown_action", cats=[])
    except Exception:  # noqa: BLE001
        pass
    try:
        st.log("policy_denied", tool=name, reason="unknown_action", agent=agent if isinstance(agent, str) else "?")
    except Exception:  # noqa: BLE001
        pass


def company_action(definition):
    level = definition.get("level")
    return {"read": "company.read", "write": "company.write"}.get(level)


def export():
    return {"schema": 1, "unknown": asdict(UNKNOWN), "actions": {
        name: {**asdict(p), "channel_forbidden": p.channel_forbidden,
               "layers": {layer: decision(layer, name) for layer in LAYERS}}
        for name, p in sorted(REGISTRY.items())}}


if __name__ == "__main__":
    print(json.dumps(export(), ensure_ascii=False, sort_keys=True, indent=2))
