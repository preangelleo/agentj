"""P158 `/goal` — goal mode: one signed opening grant, the goal first, a 10-second card that lets a reversible step through.

Leo (2026-10-12): 「用户可能已经去睡觉了……一旦启动这个 goal，应该特别对待，以目标达成为主。只有极其严格的，可能是被黑客攻击的，或者
真的有巨大风险的，才弹窗。弹窗 10 秒钟用户没点确认，应该有某种安全机制去执行，比如有备份、有快照、可回滚的这种就可以执行。」

Flow
- The owner sends `/goal <what, maybe a deadline>` (phone menu). The host hands it to the main Agent with a system note; the
  Agent writes a proposal (goal, acceptance, deadline, and the dangerous actions it expects, per category with limits) and runs
  `agentj goal propose --file <json>`. serve shows it as ONE approval card (tool `agentj goal`); the owner's signed allow from a
  paired phone starts the goal. Only what the card listed is granted.
- While a goal is active, every approval the main Agent asks for (owned / shared / Codex / OpenCode — they all reach
  serve.Host.ask) is sorted into a tier, decided HERE by the host, never by what the Agent says:
    low          not one of the five dangers                          → allowed, logged (no card)
    granted      matches a signed grant of the goal (within its limit) → allowed, logged, one quiet line on the phone
    reversible   outside the grant, but the host can undo it           → card with a 10 s countdown; no answer = allowed
                 (git-tracked files with nothing uncommitted, a move to the trash, a plain push to a remote already known)
    hold         outside the grant and not provably reversible         → the usual card, never automatic; when it is not
                 allowed the step is listed in the result card 「卡在哪一步，等你决定」 and the Agent goes on with other work
    attack       looks like an injection / an attack on the approval itself (`… | sh`, base64 → shell, `source <url>`,
                 a hook or Agent J approval / security setting being changed)               → red card, never automatic
- The goal ends at its deadline (default 8 h, at most 72 h), on `/goal stop` (phone) / `agentj goal stop`, on
  `agentj goal done`, on 「全部停下」, or when the granting phone is unpaired; its one-time grant ends with it. A result card
  (achieved or not, grants used, automatic approvals, what is waiting for the owner) goes to the phone.
- State: <state>/goal.json (~/.local/state/agentj/goal.json, 0600) — survives a restart and a compaction until the deadline.
Never a credential: a proposal that looks like it holds one is refused; credentials are never grantable (always a card).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time

TOOL = "agentj goal"                      # the opening card's tool name (approvals.log)
FILE = "goal.json"
DEFAULT_HOURS = 8
MAX_HOURS = 72
AUTO_S = 10                               # the reversible tier's countdown
GRANT_CATS = ("spend", "delete", "send", "price")    # credentials: never granted (always a card)
MAX_GRANTS = 12
MAX_ACCEPT = 5
MAX_BLOCKED = 30
MAX_MILESTONES = 40
DEFAULT_MAX = 50
CAP_MAX = 500
SOFT_EXEMPT = ("SandboxOnce", TOOL)       # never sorted into a goal tier: they keep their own card exactly as today
# P137 rules that mean "the text runs something it fetched / decoded" — the injection shapes Leo named (base64|sh, curl|sh)
ATTACK_RULES = frozenset({"opaque.piped_shell", "opaque.piped_interp", "opaque.source", "opaque.dynamic_code", "opaque.ansi"})
_SETTINGS = re.compile(
    r"\bagentj\s+(?:config\s+(?:set|edit|reset|apply|import)\b[^\n;&|]*\b(?:approval|security|human|agent\.(?:approvals|isolation|"
    r"session_mode|high_risk))|resume\b|passphrase\b|sudo-helper\b|pair\b|revoke\b|goal\s+propose\b)", re.I)
_AJ_DIRS = r"(?:\.config/agentj\b|\.local/state/agentj\b|agentjarvis-alpha\b|/agentperm\b)"
# writing into Agent J's own configuration / state (reading its logs stays ordinary)
_AJ_WRITE = re.compile(r"(?:>>?|\btee\b(?:\s+-a)?|\bsed\s+-i\S*(?:\s+\S+)?|\b(?:cp|mv|rm|ln|chmod|chown|truncate|install|rsync|unlink|touch)\b"
                       r"[^|;&\n]*?)\s*[\"']?\S*" + _AJ_DIRS)
_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_DEADLINE = re.compile(r"^(\d{1,3})\s*(h|m)$")
_SAFE_PART = re.compile(r"^[^\x00-\x08\x0b-\x1f\x7f]*$")


class GoalError(Exception):
    def __init__(self, why: str, detail: str = ""):
        super().__init__(f"{why}: {detail}" if detail else why)
        self.why, self.detail = why, detail

    def out(self) -> dict:
        return {"result": "refused", "why": self.why, "detail": self.detail}


# ------------------------------------------------------------------ proposal
def _text(v, n: int, name: str) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > n or not _SAFE_PART.match(v):
        raise GoalError("shape", f"{name}: 1–{n} characters of text")
    return v.strip()


def _home() -> str:
    return os.path.realpath(os.path.expanduser("~"))


def _path(p, name: str) -> str:
    if not isinstance(p, str) or not p or len(p) > 400 or "\n" in p:
        raise GoalError("shape", f"{name}: an absolute path or ~/path")
    if not (p.startswith("/") or p == "~" or p.startswith("~/")):
        raise GoalError("shape", f"{name}: an absolute path or ~/path")
    return os.path.realpath(os.path.expanduser(p))


def _too_wide(real: str) -> bool:
    """A delete grant may not cover / , the home folder itself, a folder above it, or a credential folder."""
    home = _home()
    if real in ("/", home) or home.startswith(real.rstrip("/") + "/"):
        return True
    rel = os.path.relpath(real, home) if real.startswith(home + "/") else ""
    first = rel.split("/", 1)[0] if rel else ""
    return first in (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".local", ".claude", ".codex")


def parse_deadline(v, now: float) -> float:
    if v is None or v == "":
        return now + DEFAULT_HOURS * 3600
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        at = float(v)
    elif isinstance(v, str) and (m := _DEADLINE.match(v.strip().lower())):
        at = now + int(m.group(1)) * (3600 if m.group(2) == "h" else 60)
    elif isinstance(v, str):
        import datetime as dt
        try:
            d = dt.datetime.fromisoformat(v.strip())
        except ValueError:
            raise GoalError("shape", "deadline: 8h / 90m / an ISO time with its offset") from None
        if d.tzinfo is None:
            d = d.astimezone()
        at = d.timestamp()
    else:
        raise GoalError("shape", "deadline: 8h / 90m / an ISO time with its offset")
    if at <= now + 60:
        raise GoalError("shape", "deadline: at least a minute from now")
    return min(at, now + MAX_HOURS * 3600)


def validate(prop, now: float | None = None) -> dict:
    """The Agent's proposal → the normalized goal the card shows (and the owner signs). Raises GoalError."""
    now = time.time() if now is None else now
    if not isinstance(prop, dict):
        raise GoalError("shape", "one JSON object")
    unknown = set(prop) - {"goal", "acceptance", "deadline", "grants", "lang"}
    if unknown:
        raise GoalError("shape", "unknown keys: " + ", ".join(sorted(unknown))[:200])
    goal = _text(prop.get("goal"), 500, "goal")
    acc = prop.get("acceptance") or []
    if not isinstance(acc, list) or len(acc) > MAX_ACCEPT:
        raise GoalError("shape", f"acceptance: at most {MAX_ACCEPT} lines")
    acc = [_text(a, 200, "acceptance") for a in acc]
    grants = prop.get("grants") or []
    if not isinstance(grants, list) or len(grants) > MAX_GRANTS:
        raise GoalError("shape", f"grants: at most {MAX_GRANTS}")
    out = []
    for i, g in enumerate(grants):
        if not isinstance(g, dict) or set(g) - {"cat", "what", "match", "dir", "paths", "max", "usd"}:
            raise GoalError("shape", f"grants[{i}]: cat, what, match, dir, paths, max, usd")
        cat = g.get("cat")
        if cat == "credentials":
            raise GoalError("credentials", "changing or showing credentials is never pre-approved: it always asks on the phone")
        if cat not in GRANT_CATS:
            raise GoalError("shape", f"grants[{i}].cat: one of " + " | ".join(GRANT_CATS))
        item = {"cat": cat, "what": _text(g.get("what"), 160, f"grants[{i}].what")}
        match = g.get("match") or []
        if not isinstance(match, list) or len(match) > 6:
            raise GoalError("shape", f"grants[{i}].match: at most 6 strings")
        item["match"] = []
        for m in match:
            m = _text(m, 120, f"grants[{i}].match")
            if len(m) < 3:
                raise GoalError("shape", f"grants[{i}].match: each at least 3 characters")
            item["match"].append(m)
        if g.get("dir") is not None:
            item["dir"] = _path(g["dir"], f"grants[{i}].dir")
        paths = g.get("paths") or []
        if not isinstance(paths, list) or len(paths) > 6:
            raise GoalError("shape", f"grants[{i}].paths: at most 6")
        if paths and cat != "delete":
            raise GoalError("shape", f"grants[{i}].paths: only for cat delete")
        item["paths"] = []
        for p in paths:
            real = _path(p, f"grants[{i}].paths")
            if _too_wide(real):
                raise GoalError("too_wide", f"grants[{i}].paths: {p} is too wide (/, the home folder or a credential folder)")
            item["paths"].append(real)
        if not item["match"] and not item["paths"]:
            raise GoalError("shape", f"grants[{i}]: give match (text the action must contain) or paths (delete only under them)")
        mx = g.get("max", DEFAULT_MAX)
        if not isinstance(mx, int) or isinstance(mx, bool) or not 1 <= mx <= CAP_MAX:
            raise GoalError("shape", f"grants[{i}].max: 1–{CAP_MAX}")
        item["max"] = mx
        if g.get("usd") is not None:
            usd = g["usd"]
            if not isinstance(usd, (int, float)) or isinstance(usd, bool) or not 0 < usd <= 100000:
                raise GoalError("shape", f"grants[{i}].usd: a positive number")
            item["usd"] = usd
        out.append(item)
    from .taskspec import secret_like
    norm = {"goal": goal, "acceptance": acc, "deadline": parse_deadline(prop.get("deadline"), now), "grants": out}
    if secret_like(json.dumps(norm, ensure_ascii=False)):
        raise GoalError("secret", "a goal must not hold a credential")
    return norm


CAT = {"zh": {"spend": "花钱", "delete": "删除", "send": "对外发送", "price": "改价"},
       "en": {"spend": "Spend", "delete": "Delete", "send": "Send out", "price": "Change prices"}}


def _when(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def card_text(g: dict, lang: str = "zh") -> str:
    """Exactly what the owner signs (the approval card's summary): goal, acceptance, deadline, every grant with its limit,
    and the rules of the period."""
    en = lang == "en"
    lines = [("🎯 Goal mode — one approval for the whole goal" if en else "🎯 目标模式 · 开工授权（一次签名）"),
             ("Goal: " if en else "目标：") + g["goal"]]
    for a in g["acceptance"]:
        lines.append(("✓ Done when: " if en else "✓ 验收：") + a)
    lines.append(("Until: " if en else "截止：") + _when(g["deadline"]))
    if g["grants"]:
        lines.append("Pre-approved (no card while the goal runs):" if en else "预先放行（目标期内不再弹卡，全部记日志）：")
        for x in g["grants"]:
            rule = []
            if x["match"]:
                rule.append(("contains " if en else "含 ") + " + ".join(f"「{m}」" for m in x["match"]))
            if x.get("dir"):
                rule.append(("in " if en else "在 ") + x["dir"])
            if x["paths"]:
                rule.append(("only under " if en else "仅限 ") + ", ".join(x["paths"]))
            if x.get("usd"):
                rule.append((f"spend cap ${x['usd']:g}" if en else f"花费上限 ${x['usd']:g}"))
            rule.append((f"≤ {x['max']} times" if en else f"最多 {x['max']} 次"))
            lines.append(f"· [{CAT['en' if en else 'zh'][x['cat']]}] {x['what']} — " + "; ".join(rule))
    else:
        lines.append("Nothing dangerous pre-approved." if en else "没有预先放行任何危险动作。")
    lines.append("Not listed = not approved. Outside the list: a step the computer can undo asks with a 10-second countdown "
                 "(no answer = approved); anything else, and anything that looks like an attack, waits for you."
                 if en else
                 "没列进去的不算授权。范围外：电脑能撤回的动作弹 10 秒倒计时卡，不点 = 同意；不可逆的和疑似攻击的一律等你决定。")
    return "\n".join(lines)


def digest(g: dict) -> str:
    return hashlib.sha256(json.dumps({k: g[k] for k in ("goal", "acceptance", "deadline", "grants")}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


# ------------------------------------------------------------------ state file
def path(st):
    return st.root / FILE


def load(st) -> dict | None:
    try:
        raw = path(st).read_bytes()
        d = json.loads(raw)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("v") == 1 else None


def save(st, d: dict) -> None:
    st.write_private(path(st), json.dumps(d, ensure_ascii=False, indent=1).encode())


def sk_sha(sk) -> str | None:
    return hashlib.sha256(sk).hexdigest() if isinstance(sk, (bytes, bytearray)) and sk else None


def is_live(st, d: dict | None, now: float | None = None) -> bool:
    """Active, before its deadline, and the granting phone still paired with the same approval key."""
    if not d or d.get("state") != "active":
        return False
    if (time.time() if now is None else now) >= float(d.get("deadline") or 0):
        return False
    grant = d.get("grant") or {}
    return sk_sha(st.sign_key(str(grant.get("device")))) == grant.get("sk_sha")


# ------------------------------------------------------------------ the action's text, working folder, delete targets
def action_text(tool: str, tool_input) -> str:
    if tool == "Bash" and isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        return tool_input["command"]
    try:
        return tool + " " + json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return tool


_SEP = re.compile(r"\s*(?:&&|;|\n)\s*")


def _segments(cmd: str) -> list[list[str]] | None:
    """A plain command line split at && ; and newlines into argv lists; None when it is anything else (pipes, ||,
    substitutions, variables, redirections, quotes left open, background jobs)."""
    if not isinstance(cmd, str) or len(cmd) > 4000 or re.search(r"[|`$<>(){}]|&(?!&)|\\\n", cmd.replace("&&", "")):
        return None
    out = []
    for part in _SEP.split(cmd.strip()):
        if not part:
            continue
        try:
            argv = shlex.split(part)
        except ValueError:
            return None
        if argv:
            out.append(argv)
    return out or None


def _start_dir(segs: list[list[str]], cwd: str | None) -> tuple[str | None, list[list[str]]]:
    """A leading `cd DIR` moves the working folder; any other cd later → None (we stop guessing)."""
    d = cwd
    rest = []
    for argv in segs:
        if argv[0] == "cd":
            if rest or len(argv) != 2:
                return None, []
            d = os.path.realpath(os.path.join(d or "/", os.path.expanduser(argv[1])))
            continue
        rest.append(argv)
    return d, rest


def command_dir(tool: str, tool_input, cwd: str | None) -> str | None:
    if tool != "Bash":
        return cwd
    segs = _segments(action_text(tool, tool_input))
    if segs is None:
        return cwd
    d, _ = _start_dir(segs, cwd)
    return d


_RM = {"rm", "rmdir", "unlink", "trash", "trash-put", "gio"}


def delete_targets(tool: str, tool_input, cwd: str | None) -> tuple[list[str], bool] | None:
    """([real paths], to_trash) when every part of a Bash command is a plain delete (rm / rmdir / unlink / trash-put /
    `gio trash` / `git rm`, P198); None otherwise. A glob is reduced to the folder before it (the grant must cover that folder)."""
    if tool != "Bash":
        return None
    segs = _segments(action_text(tool, tool_input))
    if segs is None:
        return None
    d, segs = _start_dir(segs, cwd)
    if not segs or d is None:
        return None
    targets, trash = [], True
    for argv in segs:
        name = os.path.basename(argv[0])
        base = d
        if name == "git":                     # P198: git rm
            g = _git_rm(argv, d)
            if g is None:
                return None
            base, files = g
            trash = False
        elif name in _RM:
            args = argv[1:]
            if name == "gio":
                if not args or args[0] != "trash":
                    return None
                args = args[1:]
            else:
                trash = trash and name in ("trash", "trash-put")
            files = [a for a in args if not a.startswith("-")]
        else:
            return None
        if not files:
            return None
        for f in files:
            f = os.path.expanduser(f)
            if any(c in f for c in "*?["):
                f = f[:min(f.find(c) for c in "*?[" if c in f)]
                f = f.rsplit("/", 1)[0] if "/" in f else "."
            targets.append(os.path.realpath(os.path.join(base, f)))
    return targets, trash


_GIT_RM_FLAGS = {"-r", "-f", "-q", "-n", "-rf", "-fr", "--cached", "--force", "--quiet", "--dry-run", "--ignore-unmatch"}


def _git_rm(argv: list[str], d: str) -> tuple[str, list[str]] | None:
    """P198: (git's folder, paths) of `git [-C dir] rm [flags] [--] paths`; None for anything else (as before)."""
    i, base = 1, d
    while i < len(argv) and argv[i] == "-C" and i + 1 < len(argv):
        base = os.path.realpath(os.path.join(base, os.path.expanduser(argv[i + 1])))
        i += 2
    if i >= len(argv) or argv[i] != "rm":
        return None
    files, opts = [], True
    for a in argv[i + 1:]:
        if opts and a == "--":
            opts = False
        elif opts and a.startswith("-"):
            if a not in _GIT_RM_FLAGS:
                return None
        else:
            files.append(a)
    return (base, files) if files else None


def _under(p: str, roots) -> bool:
    return any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)


# ------------------------------------------------------------------ tiers
def suspect(tool: str, tool_input, verdict) -> str | None:
    """A reason when the request looks like an injection or an attack on the approval itself (never automatic)."""
    rules = set(getattr(verdict, "rules", []) or [])
    if rules & ATTACK_RULES:
        return "injection"                 # … | sh, base64 -d | bash, source <url>, python -c "$(…)", $'\x72m'
    if "cred.hooks" in rules:
        return "hooks"                     # a hook / approver file being rewritten
    if tool == "agentj config":
        return "settings"                  # an Agent J security / approval setting (only those ever reach a card)
    text = action_text(tool, tool_input)
    if _SETTINGS.search(text) or (tool == "Bash" and _AJ_WRITE.search(text)):
        return "settings"
    if tool in _WRITE_TOOLS and isinstance(tool_input, dict) and re.search(
            _AJ_DIRS, str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")):
        return "settings"
    return None


def _git(args: list[str], cwd: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _git_restorable(p: str) -> bool:
    """A path git can give back exactly: inside a work tree, tracked, nothing uncommitted or untracked under it."""
    base = p if os.path.isdir(p) else os.path.dirname(p)
    if not os.path.isdir(base):
        return False
    code, top = _git(["rev-parse", "--show-toplevel"], base)
    if code != 0 or not top.strip():
        return False
    top = os.path.realpath(top.strip())
    if p == top:
        return False                       # the whole repository (its .git too) cannot come back from itself
    rel = os.path.relpath(p, top)
    code, tracked = _git(["ls-files", "--", rel], top)
    if code != 0 or not tracked.strip():
        return False
    code, dirty = _git(["status", "--porcelain", "--ignored", "--untracked-files=all", "--", rel], top)
    return code == 0 and not dirty.strip()


_PUSH_BAD = {"-f", "--force", "--force-with-lease", "--force-if-includes", "-d", "--delete", "--mirror", "--all", "--prune",
             "--tags", "--follow-tags", "-o", "--push-option"}


def _push_reversible(argv: list[str], d: str | None) -> bool:
    """`git push [remote [branch]]` to a remote this repository already has and already fetched, no force / delete /
    mirror / tags / refspec with a colon: a later commit can take it back. A new remote (a new repository) is not."""
    if len(argv) < 2 or argv[0] != "git" or argv[1] != "push" or not d:
        return False
    rest = argv[2:]
    if any(a in _PUSH_BAD or a.startswith(("--force", "+")) for a in rest):
        return False
    pos = [a for a in rest if not a.startswith("-")]
    if len(pos) > 2 or any(":" in a or a.startswith("+") for a in pos[1:]):
        return False
    remote = pos[0] if pos else "origin"
    if "/" in remote or ":" in remote:
        return False                       # a URL / path: maybe a new repository
    code, _ = _git(["remote", "get-url", remote], d)
    if code != 0:
        return False
    code, refs = _git(["for-each-ref", "--count=1", f"refs/remotes/{remote}/"], d)
    return code == 0 and bool(refs.strip())


def reversible(tool: str, tool_input, verdict, cwd: str | None) -> str | None:
    """A short reason when the host itself can undo the action; None = treat as irreversible."""
    cats = set(getattr(verdict, "cats", []) or [])
    if not cats or "opaque" in cats or not cats <= {"delete", "send"}:
        return None
    if cats == {"delete"}:
        t = delete_targets(tool, tool_input, cwd)
        if t is None:
            return None
        targets, trash = t
        if trash:
            return "trash"
        return "git" if all(_git_restorable(p) for p in targets) else None
    if tool != "Bash":
        return None
    segs = _segments(action_text(tool, tool_input))
    if segs is None:
        return None
    d, segs = _start_dir(segs, cwd)
    if len(segs) != 1:
        return None
    return "git_push" if _push_reversible(segs[0], d) else None


def pieces(cmd: str) -> list[str] | None:
    """A command line cut at its top-level && || ; | & and newlines, quotes respected; None when the quoting does not close.
    Heredoc bodies stay with the piece that reads them (danger judges those)."""
    out, cur, q, i, n = [], [], "", 0, len(cmd)
    while i < n:
        c = cmd[i]
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(cmd[i + 1])
                i += 1
            elif c == q:
                q = ""
        elif c in "'\"":
            q = c
            cur.append(c)
        elif c == "\\" and i + 1 < n:
            cur.append(c + cmd[i + 1])
            i += 1
        elif c in ";|&\n":
            if c == "&" and i + 1 < n and cmd[i + 1] == ">":
                cur.append(c)                       # &> redirection
            elif c == "&" and cur and cur[-1].endswith(">"):
                cur.append(c)                       # 2>&1
            else:
                out.append("".join(cur))
                cur = []
                while i + 1 < n and cmd[i + 1] in ";|&":
                    i += 1
        elif c == "<" and cmd[i:i + 2] == "<<":
            # a heredoc: keep everything up to the end of its body in this piece (the body is data or a script to judge)
            end = _heredoc_end(cmd, i)
            if end is None:
                out.append("".join(cur) + cmd[i:])
                return [p.strip() for p in out if p.strip()]
            cur.append(cmd[i:end])                  # P198: what follows the body is cut as usual
            i = end
            continue
        else:
            cur.append(c)
        i += 1
    if q:
        return None
    out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


_HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([A-Za-z0-9_.-]+))")


def _heredoc_end(cmd: str, i: int) -> int | None:
    """P198: the end of the plain heredoc at cmd[i] (its closing line); None = anything unusual (the old behaviour)."""
    m = _HEREDOC.match(cmd, i)
    nl = cmd.find("\n", m.end()) if m else -1
    if nl < 0 or "<<" in cmd[m.end():nl]:
        return None
    e = re.compile("^" + ("\t*" if m.group(1) else "") + re.escape(m.group(2) or m.group(3) or m.group(4)) + "$",
                   re.M).search(cmd, nl + 1)
    return e.end() if e else None


_SUBST = re.compile(r"\$\(|`|<\(|>\(")


def match_grant(d: dict, tool: str, tool_input, verdict, cwd: str | None, classify=None) -> list[int] | None:
    """Indexes of the grants that cover this request; None = not covered. A Bash line is cut into its pieces and every
    dangerous piece must be covered on its own text (`git push && curl -d … evil` never rides on a "git push" grant); a
    piece with a command substitution is never covered. `opaque` and `credentials` are never covered."""
    cats = list(getattr(verdict, "cats", []) or [])
    if not cats or "opaque" in cats or "credentials" in cats:
        return None
    if tool == "Bash" and classify is not None:
        ps = pieces(action_text(tool, tool_input))
        if ps is None:
            return None
        lead = cwd
        picks = []
        for p in ps:
            pv = classify(tool, {"command": p})
            m = re.fullmatch(r"cd\s+(\S+)", p)
            if m and not pv.cats:
                lead = os.path.realpath(os.path.join(lead or "/", os.path.expanduser(m.group(1).strip("'\""))))
                continue
            if not pv.cats:
                continue
            if _SUBST.search(p):
                return None
            got = _match_one(d, tool, {"command": p}, pv, lead, picks)
            if got is None:
                return None
            picks += got
        return picks or None
    return _match_one(d, tool, tool_input, verdict, cwd, [])


def _match_one(d: dict, tool: str, tool_input, verdict, cwd: str | None, already: list) -> list[int] | None:
    """Indexes of the grants that cover every dangerous category of this request (each within its limit); None = not
    covered. `opaque` and `credentials` are never covered."""
    cats = [c for c in (getattr(verdict, "cats", []) or [])]
    if not cats or "opaque" in cats or "credentials" in cats:
        return None
    text = action_text(tool, tool_input)
    wd = command_dir(tool, tool_input, cwd)
    dels = None
    used = d.get("used") or {}
    picks = []
    for c in cats:
        hit = None
        for i, g in enumerate(d.get("grants") or []):
            if g.get("cat") != c or int(used.get(str(i), 0)) + already.count(i) >= int(g.get("max", DEFAULT_MAX)):
                continue
            if g.get("match") and not all(m in text for m in g["match"]):
                continue
            if g.get("dir") and not (wd and _under(wd, [g["dir"]])):
                continue
            if g.get("paths"):
                if dels is None:
                    dels = delete_targets(tool, tool_input, cwd) or ([], False)
                if not dels[0] or not all(_under(p, g["paths"]) for p in dels[0]):
                    continue
            hit = i
            break
        if hit is None:
            return None
        picks.append(hit)
    return picks


def tier(d: dict, tool: str, tool_input, verdict, cwd: str | None, classify=None) -> tuple[str, object]:
    """("attack", why) | ("low", None) | ("granted", [grant indexes]) | ("reversible", why) | ("hold", why)."""
    why = suspect(tool, tool_input, verdict)
    if why:
        return "attack", why
    if not getattr(verdict, "cats", None):
        return "low", None
    picks = match_grant(d, tool, tool_input, verdict, cwd, classify)
    if picks is not None:
        return "granted", picks
    rev = reversible(tool, tool_input, verdict, cwd)
    if rev:
        return "reversible", rev
    cats = set(verdict.cats)
    return "hold", ("opaque" if "opaque" in cats else "credentials" if "credentials" in cats else "irreversible")


# ------------------------------------------------------------------ words
WHY = {"zh": {"injection": "疑似注入：命令在执行它下载或解码出来的内容", "hooks": "在改钩子 / 审批设置文件",
              "settings": "在改 Agent J 的审批或安全设置", "trash": "移到回收站，可以还原", "git": "git 里有提交，可以还原",
              "git_push": "推到已有的远端，可以再提交撤回", "opaque": "看不清要做什么", "credentials": "改或查看密码、密钥",
              "irreversible": "不在授权范围内，且无法撤回"},
       "en": {"injection": "possible injection: the command runs what it downloaded or decoded", "hooks": "rewrites a hook / approval file",
              "settings": "changes an Agent J approval or security setting", "trash": "moved to the trash, can be restored",
              "git": "committed in git, can be restored", "git_push": "a push to a remote it already has, can be reverted",
              "opaque": "can't tell what it does", "credentials": "changes or shows passwords or keys",
              "irreversible": "outside the grant and can't be undone"}}


def why_text(key: str, lang: str = "zh") -> str:
    return WHY["en" if lang == "en" else "zh"].get(key, key)


def hold_message(key: str, lang: str = "zh") -> str:
    """What the Agent reads when a held step was not allowed (dispatch-stop-rule-scope: only this branch waits)."""
    if lang == "en":
        return (f"Goal mode: this step needs the owner's decision ({why_text(key, 'en')}); it was not approved and is listed "
                "in the goal's result card. Do not retry it now — carry on with the other independent parts of the goal.")
    return (f"目标模式：这一步需要主人亲自决定（{why_text(key)}），这次没有批准，已记进目标结束报告「等你决定」。"
            "现在不要重试，先把目标里其它不依赖它的部分做完。")


def agent_words(arg: str, lang: str = "zh") -> str:
    """P198: what the main Agent reads for the owner's `/goal <what>` — never text starting with `/goal`: Claude Code 2.1.x has
    a native `/goal` (a session-scoped Stop hook that re-asks every turn until 「/goal clear」) and runs any stream-json
    message that starts with it. The page keeps the owner's own words; only the Agent's copy is reworded."""
    return ("Goal mode — the owner's goal: " if lang == "en" else "目标模式——主人的目标：") + (arg or "").strip()


NATIVE_TAIL = 8 * 1024 * 1024             # how much of a transcript's end is searched for the native goal's last status


def native_goal_active(transcript) -> bool:
    """P198: Claude Code's own `/goal` is still set in this session (its last `goal_status` attachment says met=false).
    Read-only, bounded to the transcript's last NATIVE_TAIL bytes; any error reads as not set."""
    try:
        with open(transcript, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - NATIVE_TAIL))
            data = f.read()
    except OSError:
        return False
    for line in reversed(data.split(b"\n")):
        if b'"goal_status"' not in line:
            continue
        try:
            att = json.loads(line).get("attachment")
        except (ValueError, AttributeError):
            continue
        if isinstance(att, dict) and att.get("type") == "goal_status":
            return att.get("met") is False
    return False


def start_note(arg: str, lang: str = "zh") -> str:
    """Appended (hidden) to the owner's `/goal …` message for the main Agent."""
    if lang == "en":
        return ("\n\n[Agent J system note, not the owner's words] The owner started goal mode with the message above. Read the "
                "agentj-goal skill now: write the proposal (goal, acceptance, deadline, and every dangerous action you expect, by "
                "category with limits) and run `agentj goal propose --file <json>` — ONE card on the phone. Start the work after "
                "the owner signs it (`agentj goal status`).")
    return ("\n\n〔Agent J 系统提示，不是主人说的话〕主人用上面这条消息启动了目标模式。现在读 agentj-goal 技能：写好提案（目标、验收、截止、"
            "预计会用到的危险动作按类逐项列出并带上限），运行 `agentj goal propose --file <json>`，手机上只出一张卡。主人签名后再开工"
            "（`agentj goal status` 查看）。")


def status_text(st, d: dict | None, lang: str = "zh", now: float | None = None) -> str:
    en = lang == "en"
    if not d or d.get("state") not in ("active", "proposed"):
        return "No goal is running. Start one with /goal <what to finish, by when>." if en else \
            "现在没有进行中的目标。用 /goal <要完成什么、什么时候前> 开始一个。"
    if d.get("state") == "proposed":
        return ("Goal proposed, waiting for your signature on the phone: " if en else "目标已提出，等你在手机上签名：") + d["goal"]
    now = time.time() if now is None else now
    left = max(0, int(float(d["deadline"]) - now))
    c = d.get("counts") or {}
    lines = [("🎯 Goal running: " if en else "🎯 目标进行中：") + d["goal"],
             (f"Until {_when(d['deadline'])} ({left // 3600} h {left % 3600 // 60} min left)" if en else
              f"截止 {_when(d['deadline'])}（还剩 {left // 3600} 小时 {left % 3600 // 60} 分）"),
             (f"Allowed by the grant {c.get('granted', 0)} · quiet low-risk {c.get('low', 0)} · 10-second auto {c.get('auto', 0)}"
              f" · waiting for you {len(d.get('blocked') or [])}" if en else
              f"按授权放行 {c.get('granted', 0)} 次 · 低风险静默 {c.get('low', 0)} 次 · 10 秒自动批准 {c.get('auto', 0)} 次 · "
              f"等你决定 {len(d.get('blocked') or [])} 件")]
    if d.get("milestones"):
        lines.append(("Latest: " if en else "最新进度：") + d["milestones"][-1]["text"])
    if not is_live(st, d, now):
        lines.append("(no longer valid: it ends now)" if en else "（已失效，马上结束）")
    return "\n".join(lines)


RESULT = {"zh": {"achieved": "达成", "not_achieved": "未达成", "expired": "到期", "stopped": "已停止", "estop": "全部停下",
                 "device_gone": "授权手机已解除配对", "replaced": "被新目标替换"},
          "en": {"achieved": "achieved", "not_achieved": "not achieved", "expired": "deadline reached", "stopped": "stopped",
                 "estop": "Stop everything", "device_gone": "the granting phone was unpaired", "replaced": "replaced"}}


def result_text(d: dict, lang: str = "zh") -> str:
    en = lang == "en"
    k = "en" if en else "zh"
    how = d.get("ended") or "stopped"
    lines = [("🎯 Goal ended — " if en else "🎯 目标结束 · ") + RESULT[k].get(how, how) + ("：" if not en else ": ") + d["goal"]]
    if d.get("summary"):
        lines.append(d["summary"])
    used = d.get("used") or {}
    parts = []
    for i, g in enumerate(d.get("grants") or []):
        n = int(used.get(str(i), 0))
        if n:
            parts.append(f"[{CAT[k][g['cat']]}] {g['what']} ×{n}")
    lines.append(("Grants used: " if en else "用到的授权：") + ("; ".join(parts) if parts else ("none" if en else "无")))
    c = d.get("counts") or {}
    lines.append(f"Approved automatically: {c.get('granted', 0) + c.get('low', 0) + c.get('auto', 0)} "
                 f"(10-second countdown {c.get('auto', 0)})" if en else
                 f"自动批准 {c.get('granted', 0) + c.get('low', 0) + c.get('auto', 0)} 次（其中 10 秒倒计时 {c.get('auto', 0)} 次）")
    blocked = d.get("blocked") or []
    if blocked:
        lines.append("Waiting for your decision:" if en else "卡在这里，等你决定：")
        for b in blocked[-10:]:
            lines.append(f"· {b['summary']} — {why_text(b['why'], 'en' if en else 'zh')}")
    else:
        lines.append("Nothing is waiting for you." if en else "没有卡住等你决定的事。")
    return "\n".join(lines)


def heartbeat_line(st, lang: str = "zh") -> str:
    """For a host wake-up (P156 heartbeat): one line when a goal is still running, else ""."""
    d = load(st)
    if not is_live(st, d):
        return ""
    if lang == "en":
        return f"[goal running until {_when(d['deadline'])}] {d['goal']} — run `agentj goal status` and keep going."
    return f"〔目标进行中，截止 {_when(d['deadline'])}〕{d['goal']}——先运行 `agentj goal status`，接着推进。"


def new_id() -> str:
    return secrets.token_hex(8)


# ------------------------------------------------------------------ serve side (owned by serve.Host)
POLL_S = float(os.environ.get("AGENTJ_TEST_GOAL_POLL") or 5)


def auto_secs() -> float:
    """Tests may only shorten the countdown."""
    try:
        v = float(os.environ.get("AGENTJ_TEST_GOAL_AUTO") or AUTO_S)
    except ValueError:
        v = AUTO_S
    return max(0.05, min(AUTO_S, v))


class Goals:
    """The one goal of this host: proposal → signed card → active → ended (+ the result card)."""

    def __init__(self, host):
        self.host = host
        self.waiting: str | None = None          # id of a proposal whose card is open

    @property
    def st(self):
        return self.host.st

    def lang(self) -> str:
        return "en" if str(getattr(self.host, "lang", "zh") or "").startswith("en") else "zh"

    def current(self) -> dict | None:
        """The live goal (active, before its deadline, granting phone still paired) — else None."""
        d = load(self.st)
        return d if is_live(self.st, d) else None

    def check(self) -> None:
        """End a goal that stopped being live (deadline, phone unpaired). Cheap: one small file read."""
        d = load(self.st)
        if not d or d.get("state") != "active" or is_live(self.st, d):
            return
        gone = sk_sha(self.st.sign_key(str((d.get("grant") or {}).get("device")))) != (d.get("grant") or {}).get("sk_sha")
        self.end("device_gone" if gone and time.time() < float(d.get("deadline") or 0) else "expired", by="host")

    async def loop(self) -> None:
        import asyncio
        while not self.host.stopping.is_set():
            try:
                self.check()
            except Exception:  # noqa: BLE001 — the goal clock never takes serve down
                pass
            try:
                await asyncio.wait_for(self.host.stopping.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass

    # ---------------------------------------------------------- bookkeeping (one write per decision)
    def _update(self, fn) -> dict | None:
        d = load(self.st)
        if not d or d.get("state") != "active":
            return None
        fn(d)
        save(self.st, d)
        return d

    def count(self, kind: str, picks=None) -> None:
        def f(d):
            c = d.setdefault("counts", {})
            c[kind] = int(c.get(kind, 0)) + 1
            for i in picks or []:
                u = d.setdefault("used", {})
                u[str(i)] = int(u.get(str(i), 0)) + 1
        self._update(f)

    def block(self, summary: str, why: str) -> None:
        def f(d):
            b = d.setdefault("blocked", [])
            line = (summary or "").split("\n", 1)[0][:160]
            if not any(x.get("summary") == line for x in b):
                b.append({"at": int(time.time()), "summary": line, "why": why})
            del b[:-MAX_BLOCKED]
        self._update(f)

    def progress(self, text: str) -> dict:
        text = _text(text, 300, "progress")
        def f(d):
            m = d.setdefault("milestones", [])
            m.append({"at": int(time.time()), "text": text})
            del m[:-MAX_MILESTONES]
        d = self._update(f)
        if d is None:
            raise GoalError("no_goal", "no goal is running")
        self.host.activity("goal_progress", text=text[:120])
        self.host.agent_notice(("🎯 Goal progress: " if self.lang() == "en" else "🎯 目标进度：") + text)
        return {"result": "ok"}

    def end(self, how: str, summary: str = "", by: str = "agent") -> dict | None:
        d = load(self.st)
        if not d or d.get("state") not in ("active", "proposed"):
            return None
        was = d["state"]
        d.update(state="ended" if was == "active" else "withdrawn", ended=how, ended_at=int(time.time()), by=by)
        if summary:
            d["summary"] = summary[:500]
        save(self.st, d)
        self.st.log("goal_end", id=d.get("id"), how=how, by=by)
        self.host.activity("goal_end", how=how, by=by, text=d["goal"][:120])
        if was == "active":
            self.host.emit("goal_end", how=how)
            self.host.agent_notice(result_text(d, self.lang()))
            self.host.push_notify("reply")
        return d

    # ---------------------------------------------------------- the opening card
    async def propose(self, prop, wait: bool = True) -> dict:
        import asyncio
        h = self.host
        if h.stopped():
            raise GoalError("stopped", "Stop everything is on")
        if self.current():
            raise GoalError("active", "a goal is already running: `agentj goal stop` first, or finish it")
        if self.waiting:
            raise GoalError("pending", "a goal card is already waiting on the phone")
        g = validate(prop)
        if not h._approvers():
            raise GoalError("no_device", "no paired phone that can sign")
        lang = prop.get("lang") if isinstance(prop, dict) and prop.get("lang") in ("zh", "en") else self.lang()
        gid = new_id()
        d = {"v": 1, "id": gid, "state": "proposed", **g, "digest": digest(g), "proposed_at": int(time.time()),
             "counts": {}, "used": {}, "blocked": [], "milestones": []}
        save(self.st, d)
        self.st.log("goal_propose", id=gid, grants=len(g["grants"]))
        self.host.activity("goal_propose", text=g["goal"][:120], grants=len(g["grants"]))
        self.waiting = gid
        text = card_text(g, lang)

        async def card() -> dict:
            try:
                ans = await h.ask(TOOL, {"goal": gid, "digest": d["digest"], "card": text}, batch=False, grant_info=True,
                                  ttl=5 * h.ask_ttl)
            finally:
                self.waiting = None
            cur = load(self.st)
            if not cur or cur.get("id") != gid or cur.get("state") != "proposed":
                return {"result": "gone", "id": gid}
            info = ans.get("grant_info") or {}
            if ans.get("behavior") != "allow" or not info.get("device") or h.stopped():
                cur.update(state="declined", ended_at=int(time.time()))
                save(self.st, cur)
                self.host.activity("goal_declined", text=g["goal"][:120])
                return {"result": "declined", "id": gid}
            cur.update(state="active", started=int(time.time()),
                       grant={"rid": info.get("rid"), "device": info["device"], "sk_sha": sk_sha(info.get("sign_pub"))})
            save(self.st, cur)
            self.st.log("goal_start", id=gid, device=info["device"])
            self.host.activity("goal_start", text=g["goal"][:120], grants=len(g["grants"]))
            self.host.emit("goal_start", id=gid)
            en = self.lang() == "en"
            self.host.agent_notice((f"🎯 Goal mode on until {_when(g['deadline'])}: " if en else
                                    f"🎯 目标模式开始，截止 {_when(g['deadline'])}：") + g["goal"])
            return {"result": "active", "id": gid, "deadline": int(g["deadline"])}

        if wait:
            return await card()
        task = asyncio.ensure_future(card())

        def told(t):
            if t.cancelled() or t.exception() is not None:
                return
            res = t.result()
            if h.agent is not None and not h.stopped():
                from .capability import ReadBackSend
                en = self.lang() == "en"
                note = (f"[Agent J system note, not the owner's words] Your goal card: {res['result']}. "
                        + ("Start the work now." if res["result"] == "active" else "Nothing was granted; ask the owner.")) if en else \
                    (f"〔Agent J 系统提示，不是主人说的话〕你的目标授权卡结果：{res['result']}。"
                     + ("现在开工。" if res["result"] == "active" else "没有任何授权，先问主人。"))
                try:
                    h.agent.submit(ReadBackSend(note))
                except Exception:  # noqa: BLE001
                    pass
        task.add_done_callback(told)
        return {"result": "shown", "id": gid, "ttl": int(5 * h.ask_ttl)}

    # ---------------------------------------------------------- capability.sock requests (t: goal_*)
    async def handle(self, req: dict) -> dict:
        t = req.get("t")
        try:
            if t == "goal_propose":
                return await self.propose(req.get("goal"), bool(req.get("wait", True)))
            if t == "goal_status":
                self.check()
                d = load(self.st)
                live = d if d and d.get("state") in ("active", "proposed") else None
                return {"result": "status", "state": live["state"] if live else "none",
                        "text": status_text(self.st, live, self.lang()),
                        **({"goal": {k: live.get(k) for k in ("id", "goal", "acceptance", "deadline", "grants", "counts", "used",
                                                              "blocked", "milestones")}} if live else {})}
            if t == "goal_stop":
                d = self.end("stopped", by=str(req.get("by") or "agent")[:16])
                return {"result": "ended" if d else "none"}
            if t == "goal_done":
                how = req.get("how")
                if how not in ("achieved", "not_achieved"):
                    raise GoalError("shape", "--result achieved | not_achieved")
                summary = req.get("summary") or ""
                if not isinstance(summary, str) or len(summary) > 500:
                    raise GoalError("shape", "--summary: up to 500 characters")
                if not self.current():
                    raise GoalError("no_goal", "no goal is running")
                self.end(how, summary, by="agent")
                return {"result": "ended", "how": how}
            if t == "goal_progress":
                return self.progress(req.get("text"))
        except GoalError as e:
            return e.out()
        return {"result": "refused", "why": "shape", "detail": "t: goal_propose | goal_status | goal_stop | goal_done | goal_progress"}


# ------------------------------------------------------------------ phone `/goal` (status / stop answered by the host)
def host_cmd(arg: str) -> str | None:
    a = (arg or "").strip().lower()
    return {"": "status", "status": "status", "stop": "stop", "状态": "status", "停止": "stop", "结束": "stop"}.get(a)


# ------------------------------------------------------------------ CLI (runs as the Agent, usually inside the fence)
def _client(req: dict, timeout: float) -> dict:
    from . import capability
    res = capability.client(req, timeout=timeout)
    return res if res is not None else {"result": "refused", "why": "no_serve", "detail": "Agent J is not running"}


def cmd_goal(a) -> int:
    sub = a.goal_cmd
    if sub == "propose":
        try:
            raw = (sys.stdin.read() if a.file == "-" else open(os.path.expanduser(a.file), encoding="utf-8").read())
            prop = json.loads(raw)
        except (OSError, ValueError):
            res = {"result": "refused", "why": "shape", "detail": "--file: a JSON file (or - for stdin)"}
        else:
            try:
                validate(prop)                  # say what is wrong before anything reaches the phone
            except GoalError as e:
                res = e.out()
            else:
                res = _client({"t": "goal_propose", "goal": prop, "wait": not a.no_wait}, timeout=700)
    elif sub == "status":
        res = _client({"t": "goal_status"}, timeout=15)
    elif sub == "stop":
        res = _client({"t": "goal_stop", "by": "terminal"}, timeout=15)
    elif sub == "done":
        res = _client({"t": "goal_done", "how": a.result, "summary": a.summary or ""}, timeout=15)
    else:
        res = _client({"t": "goal_progress", "text": " ".join(a.text)}, timeout=15)
    if a.json or "text" not in res:
        print(json.dumps(res, ensure_ascii=False, indent=None if not a.json else 1))
    else:
        print(res["text"])
    return 0 if res.get("result") not in ("refused",) else 2


def add_parser(sub) -> None:
    p = sub.add_parser("goal", help="目标模式：propose 开工授权卡 · status · progress · done · stop / goal mode: one signed grant for a goal")
    ps = p.add_subparsers(dest="goal_cmd", required=True)
    x = ps.add_parser("propose", help="把目标和预计的危险动作做成一张卡发到手机（主人签名后生效）/ one card on the phone")
    x.add_argument("--file", required=True, help="提案 JSON（- = stdin）/ the proposal JSON")
    x.add_argument("--no-wait", action="store_true", help="不等主人签名就返回 / return before the owner signs")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("status", help="进行中的目标 / the running goal")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("progress", help="一句里程碑进度（推到手机）/ one milestone line")
    x.add_argument("text", nargs="+")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("done", help="结束目标并出结果卡 / end with the result card")
    x.add_argument("--result", required=True, choices=["achieved", "not_achieved"])
    x.add_argument("--summary", help="一两句结果 / one or two lines")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("stop", help="立即结束目标并撤销一次性授权 / end it now")
    x.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: sys.exit(cmd_goal(a)))
