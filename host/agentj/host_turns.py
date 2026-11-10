"""0.30.6 (ADR-0306-host-turn-reads): a host-injected system turn reads without a card.

Heartbeat, background-done, compaction recovery and the approval-explanation turn are queued by the host itself
(HeartbeatSend / CompletionSend / the compact_resume Send / the approval-review Send). Their kind is read from the
host-built Send object of the turn that is running now, never from turn text, a tool field or `source` alone (a webhook
hook notice also carries the system source). In such a turn a native permission request for one of three narrow read
shapes is answered without a card: a listing `agentj` subcommand, or reading a `.md` in a bundled agentj skill folder.
Readers are only the Read tool, `cat`, `head`, `tail` (no follow), `nl`, `wc`; every reader names regular-file operands;
no pipe, redirection, substitution or wrapper. Anything else (writes, UV-list entries, credentials, network, logs and
other files, sed / grep / journalctl) keeps the existing path. Log reads are deferred to 0.30.7 (security r2 N1).
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

KINDS = ("heartbeat", "background_done", "compact_resume", "approval_review")
# Listing branches only: jcards_todo.command / jcards_cal.command / jcards list / uv list / inbox avatar-log (read-only).
AGENTJ_READ = (("card", "list"), ("card", "todo", "list"), ("card", "todo", "due"), ("card", "cal", "list"),
               ("card", "cal", "due"), ("uv", "list"), ("inbox", "avatar-log"))
# security r1 M1/M2: no reader with an exec / write / recursive / file-list sub-language (sed, grep, rg, journalctl gone).
READERS = frozenset({"cat", "head", "tail", "nl", "wc"})
_VALUE_FLAGS = {"head": {"-n", "-c"}, "tail": {"-n", "-c"}}
_FLAGS = {"cat": {"-n", "-b", "-A", "-v", "-E", "-T", "-s"}, "nl": {"-ba"}, "wc": {"-l", "-w", "-c", "-m"},
          "head": set(), "tail": set()}
_HARNESS_SKILLS = (".claude/skills", ".agents/skills", ".codex/skills", ".config/opencode/skills")


def kind(host):
    """The host-known kind of the main turn running now, or None. Any task / paired / peer / child scope → None."""
    from . import approval_review, compose, provenance
    from .background_done import CompletionSend
    from .heartbeat import HeartbeatSend
    agent = getattr(host, "agent", None)
    if (agent is None or provenance._paired_source() is not provenance._UNSET or provenance._REQUEST.get() is not provenance._UNSET
            or provenance.task_scope() is not None or approval_review.ORIGIN.get() is not None
            or provenance._shared_child(agent) is not provenance._UNSET):
        return None
    send = getattr(agent, "cur_send", None)
    if send is None or provenance.normalize(getattr(send, "source", None))["kind"] != "system" or getattr(send, "hook_notice", None):
        return None
    if type(send) is HeartbeatSend:
        return "heartbeat"
    if type(send) is CompletionSend:
        return "background_done"
    if type(send) is not compose.Send:
        return None
    if send.by == "compact_resume" and getattr(send, "compact_resume", None) is True:
        return "compact_resume"
    meta = getattr(send, "approval_review", None)
    if send.by == "approval-review" and isinstance(meta, dict):
        item = approval_review._outbox(host).get(meta.get("event_id"))
        if item is not None and item.get("target") == approval_review.thread_ref(agent) and item.get("request_id") == meta.get("id"):
            return "approval_review"
    return None


def _within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _skill_roots():
    pkg = os.path.realpath(Path(__file__).with_name("skills"))
    try:
        names = json.loads(Path(__file__).with_name("bundled_skills.json").read_text())["skills"]
    except (OSError, ValueError, KeyError, TypeError):
        names = []
    home = os.path.expanduser("~")
    roots = []
    for name in names:
        if not isinstance(name, str) or not name.startswith("agentj-") or "/" in name:
            continue
        roots.append(os.path.join(pkg, name))
        for rel in _HARNESS_SKILLS:
            base = os.path.realpath(os.path.join(home, rel))
            here = os.path.join(base, name)
            # A harness copy counts only where the skill folder itself is that folder (no symlink escape elsewhere).
            if os.path.realpath(here) == here or _within(os.path.realpath(here), pkg):
                roots.append(os.path.realpath(here))
    return roots


def _path(word, cwd):
    if not isinstance(word, str) or not word or any(c in word for c in "*?[]{}`\n\x00"):
        return None
    for prefix in ("$HOME/", "${HOME}/"):
        if word.startswith(prefix):
            word = "~/" + word[len(prefix):]
    if "$" in word:
        return None
    word = os.path.expanduser(word)
    return os.path.realpath(word if os.path.isabs(word) else os.path.join(cwd or os.sep, word))


def _target(real):
    """A regular `.md` file in a bundled agentj skill folder; nothing else (no logs in 0.30.6, security r2 N1)."""
    if real and os.path.isfile(real) and real.endswith(".md") and any(_within(real, r) for r in _skill_roots()):
        return "skill_doc"
    return None


def _operands(simple):
    """File operands of one reader, or None when an option is outside its closed list (tail -f, wc --files0-from …)."""
    values, flags, out, args = _VALUE_FLAGS.get(simple.name, set()), _FLAGS[simple.name], [], list(simple.args)
    i = 0
    while i < len(args):
        a = args[i]
        if a in values and i + 1 < len(args) and args[i + 1].lstrip("+-").isdigit():
            i += 2
            continue
        if simple.name in ("head", "tail") and len(a) > 1 and a[0] == "-" and a[1:].isdigit():
            i += 1
            continue
        if a.startswith("-"):
            if a not in flags:
                return None
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def classify(host, tool, inp, cwd, turn):
    """'agentj_read' | 'skill_doc' | 'mixed' when this exact request is one of the narrow read shapes, else None."""
    from . import danger
    from .approval_material import _external
    if not isinstance(inp, dict) or _external(tool, inp):
        return None
    if tool in ("Read", "read"):
        if set(inp) - {"file_path", "filePath", "path", "offset", "limit"}:
            return None
        return _target(_path(inp.get("file_path") or inp.get("filePath") or inp.get("path"), cwd))
    if tool not in ("Bash", "bash", "shell", "exec_command") or not isinstance(inp.get("command"), str):
        return None
    commands = danger.lex(inp["command"])
    if not commands:
        return None
    found, here = set(), cwd
    for command in commands:
        if (command.subst or command.heredoc or command.ins or command.piped or command.redirs
                or getattr(command, "owner", None) is not None):
            return None
        simples = danger.unwrap(command)
        if len(simples) != 1:
            return None
        s = simples[0]
        if s.ins or s.redirs or s.flags or command.words[:1] != [s.name]:
            return None
        if s.name == "cd" and len(s.args) == 1:
            here = _path(s.args[0], here)
            if here is None or not os.path.isdir(here):
                return None
            continue
        if s.name == "agentj":
            if not any(tuple(s.args[:len(p)]) == p for p in AGENTJ_READ):
                return None
            found.add("agentj_read")
            continue
        if s.name not in READERS:
            return None
        operands = _operands(s)
        if not operands:
            return None
        for word in operands:
            target = _target(_path(word, here))
            if target is None:
                return None
            found.add(target)
    if not found:
        return None
    return found.pop() if len(found) == 1 else "mixed"


async def admit(host, tool, inp, cwd=None):
    """None = not this shape (the caller keeps its existing path); else the allow answer, audited, no card."""
    import asyncio
    from . import approvals, danger, uv_list
    try:
        turn = kind(host)
        if turn is None or host.stopped() or host.stopping.is_set():
            return None
        cwd = cwd or (getattr(host, "agent_cfg", None) or {}).get("dir")
        if uv_list.required(tool, inp, cwd=cwd, env=uv_list.host_environment(host)):
            return None
        if danger.classify_shared(tool, inp, cwd=cwd, env=getattr(host.agent, "approval_env", None)).cats:
            return None
        what = await asyncio.to_thread(classify, host, tool, inp, cwd, turn)
    except Exception:  # noqa: BLE001 — fail closed: the existing card path
        return None
    if what is None or kind(host) != turn or host.stopped() or host.stopping.is_set():
        return None
    rid = secrets.token_hex(16)
    agent = getattr(host.agent, "kind", "?")
    await asyncio.to_thread(approvals.record, host.st, rid=rid, agent=agent, tool=tool,
                            input_sha256=approvals.input_digest(inp), shown_sha256=approvals.shown_digest(tool, "host turn read"),
                            decision="allow", reason="host_turn_read", cats=[])
    host.st.log("ask_done", id=rid, agent=agent, tool=tool, decision="allow", reason="host_turn_read", turn=turn, read=what)
    return {"behavior": "allow", "updatedInput": inp}
