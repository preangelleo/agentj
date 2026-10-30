"""P551 (FEEDBACK #318, continuing #274): one confirmation table, decided by the program — never by a model's words.

                         the owner taps a button on a paired remote     the Agent asks
    ① none               do it                                           do it
    ② reversible         do it — the signed tap IS the instruction       long-press card (no Face ID)
    ③ irreversible       Face ID                                         Face ID

③ = deleting, spending money, unbinding a remote / cancelling a seat, elevating privileges, revealing keys, controlling the
computer. The Agent's side is policy_registry.main_requirement (the floor Host.ask applies: hold = long-press, uv = Face ID).
The owner's side is the table below: a phone control's tier by its action name, never by the Agent's current turn (#319: a
system or peer turn is not this click). A remote without its own passkey cannot pass ③ (`no_passkey`): it asks another of the
owner's devices, or sets up Face ID on itself first.

`harness` level (#320, approval_level.py): Agent J adds no approval layer of its own; the harness's own prompts still come as
long-press cards. ③ narrows to four kinds there (chairman, 10-26): spending money, deleting the owner's data (not the Agent's
own temp files / generated build output), unbinding / cancelling / moving a seat, a key going off this machine — harness_keeps().
Everything else (~/.ssh/config, sudo on the owner's own machines, reading local credentials, commands Jev cannot read, Computer
Use) is the harness's call: when it does not ask, no card at all. Standard library only.

P573 (#386 ⑤, chairman 10-29): the standard level's Face ID narrows to the same kinds (policy_registry.main_requirement
asks harness_keeps) plus trust roots and the Agent's own hook / approval files; everything else that was ③ is one button.
"""
from __future__ import annotations

NONE, HOLD, UV = "none", "hold", "uv"
EFFECTS = ("none", "reversible", "irreversible")

# Phone-initiated actions → effect. A tap needs no further confirmation unless irreversible.
OWNER_ACTIONS = {
    # ② reversible: the owner's own tap is the instruction
    "mail_fwd": "reversible", "tg_fwd": "none", "wechat_fwd": "reversible", "bot_mail": "reversible",
    "mem_rm": "reversible", "mem_undo": "reversible", "task_on": "reversible", "task_off": "reversible", "task_migrate": "reversible",
    "estop": "none",
    # ③ irreversible: Face ID even from the owner's own remote
    "dev_unbind": "irreversible",           # P573 (b): unbinding another remote keeps Face ID (a found phone could do it)
    "wechat_unbind": "reversible",          # P573: WeChat can be bound again — the owner's signed tap is the instruction
    "approval_widen": "irreversible",       # a level that asks less (full / harness), goal without cards, Jev off
}


def required(effect: str, origin: str) -> str:
    """→ none | hold | uv. origin: "owner" (a signed control from a paired remote) | anything else = the Agent."""
    if effect == "irreversible":
        return UV
    if effect == "reversible":
        return NONE if origin == "owner" else HOLD
    if effect == "none":
        return NONE
    return UV                                # unknown effect: the strictest step, never a silent allow


def owner_action(action: str) -> str:
    """The confirmation an owner's tap on `action` needs (unregistered actions keep their own handler's rules → none here)."""
    return required(OWNER_ACTIONS.get(action, "none"), "owner")


def agent_requirement(tool: str, tool_input=None, *, env=None) -> str:
    """The Agent's side: the host policy floor (allow → none)."""
    from . import policy_registry
    r = (policy_registry.main_requirement(tool, tool_input, env=env) if env is not None
         else policy_registry.main_requirement(tool, tool_input))
    return {"allow": NONE, "hold": HOLD, "uv": UV}.get(r, r)


HARNESS_KINDS = ("spend", "delete", "seat", "secret_out")
_FILE_RM = frozenset({"rm", "rmdir", "unlink", "shred", "srm", "wipe"})
_OVERWRITE = frozenset({"shred", "srm", "wipe"})     # these rewrite the file's content: a hard link would reach the owner's
# what may stand next to a scratch removal in one command: nothing that makes, moves or links files (a link planted in the
# same command, `mv ~/x /tmp/y && rm -rf /tmp/y`) — security review of phase 2D
_BESIDE = _FILE_RM | {"echo", "printf", "true", "false", ":", "test", "[", "ls", "sleep", "wc", "agentj", "ssh"}
# generated output: the first folder below the work folder (a fixed list, not .gitignore: the Agent can write that file)
GENERATED = frozenset({"build", "dist", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                       "target", ".next", ".nuxt", ".parcel-cache", ".turbo", "coverage", ".tox"})
_SEAT_CLI = (["revoke"], ["unlink"])                 # unbind a remote / take the host out of the account (its seat)
_PATCH_DELETE = r"^\s*\*\*\*\s*Delete File:\s*(.+?)\s*$"
_PATCH_MOVE = r"^\s*\*\*\*\s*Update File:\s*(.+?)\s*\n\s*\*\*\*\s*Move to:\s*(.+?)\s*$"


def _scratch(path: str, workdir, remote: bool, overwrite: bool = False, recursive: bool = False) -> bool:
    """A path the Agent may remove under the harness level without a Face ID: strictly inside a system temp folder (never
    under home), or inside a generated-output folder (GENERATED) directly below the work folder (not home). No variable,
    `~user`, `..`, brace, glob before the last part, or credential-looking name; symlinks resolved; a file shred overwrites
    must have no other hard link."""
    import os
    import tempfile
    from . import danger
    if (not isinstance(path, str) or not path or any(c in path for c in "$`{}\\\n") or
            (path.startswith("~") and not path.startswith("~/")) or ".." in path.split("/") or
            any(c in (path if recursive else path.rsplit("/", 1)[0]) for c in "*?[") or danger.cred_path(path)):
        return False
    if remote:      # the other machine: links there cannot be seen — only a name directly in its temp root, no glob
        d, _, base = path.rpartition("/")
        return not overwrite and d in ("/tmp", "/var/tmp", "/private/tmp") and bool(base) and not any(c in base for c in "*?[")
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        if not workdir or not os.path.isabs(workdir):
            return False
        p = os.path.join(workdir, p)
    real = os.path.realpath(p)                       # symlinks resolved (a link out is judged where it points)
    if danger.cred_path(real):
        return False
    if overwrite and any(c in real for c in "*?["):
        return False
    try:
        if overwrite and os.path.exists(real) and (not os.path.isfile(real) or os.stat(real).st_nlink != 1):
            return False
    except OSError:
        return False
    home = os.path.realpath(os.path.expanduser("~")).rstrip("/") + "/"
    roots = {os.path.realpath(r).rstrip("/") + "/" for r in ("/tmp", "/var/tmp", tempfile.gettempdir())}
    if not real.startswith(home) and any(real.startswith(r) and not home.startswith(r) for r in roots):
        return True
    wd = os.path.realpath(workdir).rstrip("/") + "/" if workdir else None
    if not wd or not real.startswith(wd) or wd in ("/", home):
        return False
    return real[len(wd):].split("/", 1)[0] in GENERATED


def _simple(command: str) -> list:
    from . import danger
    out = []
    danger._bash(command, danger._Hits(), collect=out)
    return out


def _owner_delete(command: str, items, workdir) -> bool:
    """Does this shell command delete the owner's data? Only plain file removals whose every target is scratch are not."""
    import re
    from . import danger
    rules = {r for c, r, _w in items if c == "delete"}
    if not rules:
        return False
    if rules <= _RECOVERABLE_GIT and _only_refs(command):
        return False                                 # P573: a local branch / tag comes back from the reflog
    if rules - {"delete.rm", "delete.shred", "delete.srm", "delete.wipe"}:
        return True                                  # git / cloud / SQL / find -delete …: the owner's data
    if re.search(r"(?<![&>|])&(?![&>])", command):
        return True                                  # a background job beside it could swap a path between check and use
    simple = _simple(command)
    if any(s.name not in _BESIDE for s in simple):
        return True                                  # cd, ln, mv, cp, tar … beside it: not provable
    for s in simple:
        if s.name in _FILE_RM:
            targets = danger._positionals(s.args)
            rec = any(a == "--recursive" or a.startswith("-") and not a.startswith("--") and "r" in a.lower() for a in s.args)
            if not targets or not all(_scratch(t, workdir, "remote" in s.flags, s.name in _OVERWRITE, rec) for t in targets):
                return True
    return False


# P573 (#386 ⑤, REPORT-573 §三 「去掉」): deleting a local git branch or tag is recoverable (`git reflog`, the commit stays
# until gc) — not the owner's data lost. Everything else git deletes (reset --hard, clean, stash drop, a remote branch …) is.
_RECOVERABLE_GIT = frozenset({"delete.git_branch", "delete.git_tag"})


def _only_refs(command: str) -> bool:
    """Every simple command is a plain `git branch …` / `git tag …` (no other program, no `git -C` / push beside it)."""
    simple = _simple(command)
    return bool(simple) and all(s.name == "git" and s.args[:1] in (["branch"], ["tag"]) for s in simple)


def _names_cred(command: str) -> bool:
    """A credential file named anywhere in a command — an argument or a redirection (`aws s3 cp ~/.aws/credentials …`,
    `gh gist create .env`, `ssh h < key`)."""
    from . import danger
    return any(danger.cred_path(w) for s in _simple(command) for w in list(s.args) + list(s.ins or [])
               if isinstance(w, str))


def _moved_away(src, dst, workdir) -> bool:
    """Moving the owner's file into scratch is the first half of deleting it (review: `mv ~/x /tmp/y`, then `rm` later)."""
    return isinstance(dst, str) and _scratch(dst, workdir, False) and not _scratch(src if isinstance(src, str) else "",
                                                                                   workdir, False)


def harness_keeps(tool: str, tool_input=None, *, env=None, workdir=None):
    """Under the `harness` level: the ③ kind this request still needs Face ID for (HARNESS_KINDS), or None (the harness
    decides). Decided from the real arguments, like main_requirement; never from the model's words."""
    import re
    import shlex
    from . import danger, policy_registry
    inp = tool_input if isinstance(tool_input, dict) else {}
    action = policy_registry.tool_action(tool)
    if action == "payment":
        return "spend"
    if action == "device.revoke":
        return "seat"
    if tool == "agentj calendar" and inp.get("operation") == "delete":
        return "delete"
    if action == "file.delete":                      # a Codex change with several files lists them all: each must be scratch
        paths = inp.get("files") if isinstance(inp.get("files"), list) else [inp.get("file_path") or inp.get("path")]
        return None if paths and all(_scratch(p, workdir, False) for p in paths) else "delete"
    if tool == "ExitWorktree" and inp.get("action") == "remove" and inp.get("discard_changes"):
        return "delete"                              # P553: uncommitted work in the worktree is thrown away
    if _moved_away(inp.get("source"), inp.get("destination"), workdir):
        return "delete"                              # an MCP move_file into scratch
    m = re.search(r"（移动到 (.+)）$", str(inp.get("new_string", ""))) if tool == "Edit" else None
    if m and _moved_away(inp.get("file_path"), m.group(1), workdir):
        return "delete"                              # a Codex file change moving a file into scratch
    operation = tool.split("__")[-1] if tool.startswith("mcp__") else tool
    shell = operation in ("Bash", "exec_command", "shell_command", "execute_command", "bash") or (
        tool == "Monitor" and "command" in inp)          # P553: Monitor's command is a shell command
    raw = inp.get("command", inp.get("cmd", ""))
    text = " ".join(shlex.quote(str(w)) for w in raw) if isinstance(raw, list) else str(raw)
    patch = text if shell else str(inp.get("command", inp.get("patch", inp.get("input", "")))) if tool == "apply_patch" else ""
    if any(not _scratch(p, workdir, False) for p in re.findall(_PATCH_DELETE, patch, re.M)):
        return "delete"
    if any(_moved_away(a, b, workdir) for a, b in re.findall(_PATCH_MOVE, patch, re.M)):
        return "delete"
    name, inp = ("Bash", {"command": text}) if shell else (tool, inp)
    if shell:
        for command in danger.lex(text):
            for s in danger.unwrap(command):
                if s.name == "agentj" and s.args[:1] in _SEAT_CLI:
                    return "seat"
                if s.name == "agentj" and s.args[:1] in (["secret"], ["secrets"]) and not policy_registry._own_card(s.args):
                    return "secret_out"
                if s.name in ("mv", "rename") and len(pos := danger._positionals(s.args)) >= 2 and any(
                        _moved_away(src, pos[-1], workdir) or not _scratch(src, workdir, "remote" in s.flags)
                        and _scratch(pos[-1].rstrip("/") + "/x", workdir, "remote" in s.flags) for src in pos[:-1]):
                    return "delete"
    v, sh = danger.classify(name, inp, env=env), danger.classify_shared(name, inp, env=env)
    cats, rules = set(v.cats) | set(sh.cats), set(v.rules) | set(sh.rules)
    if cats & {"spend", "price"}:
        return "spend"
    net = shell and any(s.name in danger._NET for s in _simple(text))
    if "cred.exfil" in rules or (("send" in cats or net) and ("credentials" in cats or shell and (
            policy_registry._SECRET_REF.search(text) or _names_cred(text)))):
        return "secret_out"
    if "delete" in cats and (not shell or _owner_delete(text, v.items + sh.items, workdir)):
        return "delete"
    return None


def table() -> dict:
    """For docs and tests: the owner-side rows and the two-column rule."""
    return {"owner": {k: owner_action(k) for k in sorted(OWNER_ACTIONS)},
            "rule": {e: {"owner": required(e, "owner"), "agent": required(e, "agent")} for e in EFFECTS}}
