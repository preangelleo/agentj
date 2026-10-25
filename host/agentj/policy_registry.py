"""Host-owned action policy shared by all four entry layers (P512).

Identifiers come from executors, never model safety/approval claims. This is an
application boundary, not protection from code running under the host's own uid.
Run ``python -m agentj.policy_registry`` to export the authoritative JSON table.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import json
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
        (Policy("none", "none", channel=False), ("file.read",)),
        (Policy("reversible", "hold", main_confirmation="none"), ("file.write",)),
        (HOLD, ("file.trash", "settings.write", "memory.restore")),
        (UV, ("file.delete", "mail.send", "calendar.write", "secrets.read", "secrets.write",
              "payment", "admin", "device.pair", "device.revoke", "publish")),
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
    "DoomLoop": "settings.write", "task": "settings.write", "AskUserQuestion": "settings.write",
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
    **dict.fromkeys(("Bash", "exec_command", "shell_command", "Task", "Agent", "exec"), "admin"),
    "TodoWrite": "settings.write", "update_plan": "settings.write",
    "request_user_input": "settings.write",
})
# MCP server names are not authority. Recognized operation names get a floor;
# unrecognized operations fail closed, including plausible but unregistered reads.
MCP_TOOLS = MappingProxyType({
    "read_file": "file.read", "list_directory": "file.read", "search_files": "file.read",
    "write_file": "file.write", "edit_file": "file.write", "delete_file": "file.delete",
    "send_email": "mail.send", "execute_command": "admin", "bash": "admin",
    "web_search": "web.search", "generate_image": "image.generate", "edit_image": "image.edit",
})


def tool_action(tool, *, canonical=True):
    if not isinstance(tool, str):
        return None
    if tool in HOST_TOOLS:
        return HOST_TOOLS[tool]
    if tool in NATIVE_TOOLS:
        return NATIVE_TOOLS[tool]
    if tool.startswith("mcp__"):
        parts = tool.split("__")
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


def main_host_card(tool, tool_input=None, *, requirement=None):
    """Use the request's computed floor; standalone callers supply real inputs."""
    if requirement is None:
        requirement = main_requirement(tool, tool_input)
    return requirement != "allow"


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
    if operation in ("Bash", "exec_command", "shell_command", "execute_command", "bash"):
        name, inp = "Bash", {"command": inp.get("command", inp.get("cmd", ""))}
        requirement = "allow"
        # The existing lexer handles wrappers and substitutions. Elevation and
        # Agent J's credential executor are sensitive even with harmless operands.
        for command in danger.lex(str(inp["command"])):
            for simple in danger.unwrap(command):
                if "elevated" in simple.flags or simple.name in ("sudo", "su", "doas", "run0", "pkexec"):
                    return "uv"
                if simple.name == "agentj" and simple.args[:1] in (["secret"], ["secrets"], ["sudo"]):
                    return "uv"
            if command.words and danger._base(command.words[0]) in ("sudo", "su", "doas", "run0", "pkexec"):
                return "uv"
    elif action in ("file.write", "file.read"):
        name = tool if tool in danger._FILE_TOOLS else "Write" if action == "file.write" else "Read"
        inp = {**inp, "file_path": inp.get("file_path") or inp.get("path") or inp.get("notebook_path")}
    if tool == "apply_patch":
        import re
        patch = str(inp.get("command", inp.get("patch", inp.get("input", ""))))
        for kind, path in re.findall(r"^\*\*\* (Add|Update|Delete) File: (.+)$", patch, re.M):
            if kind == "Delete" or danger.cred_path(path) or danger.hook_path(path):
                return "uv"
    verdict = danger.classify(name, inp, env=env)
    shared = danger.classify_shared(name, inp, env=env)
    cats = set(verdict.cats) | set(shared.cats)
    if cats & {"credentials", "spend", "price", "delete", "send"}:
        return "uv"
    if cats:
        return "hold"  # reversible but unclear/sensitive; no blanket shell UV
    return requirement


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
