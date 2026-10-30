"""Agent J goal; developer reference: documentation/notes/P0262-HOST-REFERENCE.md."""
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
TOOL_GRANT = "agentj goal grant"          # P339: an extra grant asked while the goal runs (its own signed card)
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
MAX_ALL_GRANTS = 20                       # P339: grants of a goal including the ones added while it runs
ADD_MAX = 20                              # P339: the uses a one-tap 「批准，并加进目标授权」 grant allows
ADD_CATS = ("delete", "send")             # P339: one tap never grants money (spend / price): that is a proposal card
CAP_MAX = 500
# "agentj plaza" = plaza_perms.CARD_TOOL (P426 security review N2: the plaza permission / publisher card must reach the phone)
SOFT_EXEMPT = ("SandboxOnce", "AuthRescue", TOOL, TOOL_GRANT, "agentj quota switch", "AgentJHook",
               "agentj automation resume", "agentj plaza")  # each retains its own approval card (P426: wfrun.TOOL, plaza_perms.CARD_TOOL)
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
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,39}")


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


def _too_close(real: str) -> bool:
    """P339 (review #5): a one-tap delete grant never names home, a folder within two levels of it (~/x, ~/x/y), or a
    folder one level under / (/tmp, /srv)."""
    home = _home()
    if real == home or home.startswith(real.rstrip("/") + "/"):
        return True
    if real.startswith(home + "/"):
        return len(os.path.relpath(real, home).split("/")) <= 2
    return len([x for x in real.split("/") if x]) <= 1


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
    out = check_grants(prop.get("grants") or [], MAX_GRANTS)
    from .taskspec import secret_like
    norm = {"goal": goal, "acceptance": acc, "deadline": parse_deadline(prop.get("deadline"), now), "grants": out}
    if secret_like(json.dumps(norm, ensure_ascii=False)):
        raise GoalError("secret", "a goal must not hold a credential")
    return norm


def check_grants(grants, limit: int = MAX_GRANTS) -> list:
    """The grant list of a proposal (or, P339, of an extra grant asked mid-goal) → normalized. Raises GoalError."""
    if not isinstance(grants, list) or len(grants) > limit:
        raise GoalError("shape", f"grants: at most {limit}")
    out = []
    for i, g in enumerate(grants):
        if not isinstance(g, dict) or set(g) - {"cat", "what", "match", "dir", "paths", "max", "usd", "upload", "prefix"}:
            raise GoalError("shape", f"grants[{i}]: cat, what, match, dir, paths, upload, prefix, max, usd")
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
        # P339 (review #1): structured rules matched on the parsed command, never on its text
        if g.get("upload") is not None:
            up = g["upload"]
            if cat != "send" or item["match"] or item["paths"] or g.get("prefix") is not None or not isinstance(up, dict) \
                    or set(up) != {"host", "via"} or up.get("via") not in ("scp", "rsync") or not isinstance(up.get("host"), str) \
                    or not _HOST_RE.match(up["host"]) or len(up["host"]) < 2:
                raise GoalError("shape", f"grants[{i}].upload: {{\"host\": <ssh Host alias>, \"via\": \"scp\" | \"rsync\"}}, "
                                         "cat send, no match / paths / prefix")
            item["upload"] = {"host": up["host"], "via": up["via"]}
        if g.get("prefix") is not None:
            pre = g["prefix"]
            if item["match"] or item["paths"] or not isinstance(pre, list) or len(pre) != 2 \
                    or not all(isinstance(w, str) and _WORD_RE.fullmatch(w) for w in pre) or pre[1].startswith("-") \
                    or len(" ".join(pre)) < 3:
                raise GoalError("shape", f"grants[{i}].prefix: two plain words the command starts with (e.g. [\"git\", \"push\"]), "
                                         "no match / paths")
            item["prefix"] = list(pre)
        if not item["match"] and not item["paths"] and not item.get("upload") and not item.get("prefix"):
            raise GoalError("shape", f"grants[{i}]: give match (text the action must contain), paths (delete only under them), "
                                     "upload or prefix")
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
    return out


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
            lines.append("· " + grant_line(x, lang))
    else:
        lines.append("Nothing dangerous pre-approved." if en else "没有预先放行任何危险动作。")
    lines.append("Not listed = not approved. Outside the list: a step the Agent J host can undo asks with a 10-second countdown "
                 "(no answer = approved); anything else, and anything that looks like an attack, waits for you."
                 if en else
                 "没列进去的不算授权。范围外：Agent J 主机能撤回的动作弹 10 秒倒计时卡，不点 = 同意；不可逆的和疑似攻击的一律等你决定。")
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


# ------------------------------------------------------------------ P339: a copy to a registered machine that creates a new file
# Leo (2026-10-16): a freshly written script scp'd to the test machine `olares` was judged 「无法撤回」. A copy that creates a
# file that is not there yet is undone by deleting the copy, so it gets the 10-second countdown — but only to a machine the
# owner registered (a `Host` alias in ~/.ssh/config, never a raw user@ip / URL), only scp / rsync without --delete and the
# like, and only when the host's own read-only probe (`ssh … test -e`) says the target does not exist. Overwriting stays hold.
# P339 (review #2): whitelists — any other option (a jump host, a raw ssh option, a remote shell, --remote-option, rsync's
# --remove-* / --copy-links / --log-file …) makes the copy "not judged" (hold), never "reversible".
_SCP_FLAGS = set("rpqCv")                  # scp options without a value
_SCP_VAL = {"P": re.compile(r"\d{1,5}"), "i": re.compile(r"[^\s]{1,400}"), "l": re.compile(r"\d{1,9}")}   # -P port, -i key, -l limit
_RSYNC_FLAGS = set("arvzPhncptu")          # -a -r -v -z -P -h -n -c -t -p -u, combinable (-avz)
_RSYNC_LONG = {"--progress", "--partial", "--mkpath"}
MAX_PROBES = 4


def ssh_aliases() -> set:
    """The `Host` names declared in the owner's ~/.ssh/config (no patterns): the machines the owner registered."""
    out = set()
    try:
        with open(os.path.join(_home(), ".ssh", "config"), encoding="utf-8", errors="replace") as f:
            lines = f.read(256 * 1024).splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.strip().replace("=", " ", 1).split()
        if len(parts) >= 2 and parts[0].lower() == "host":
            out.update(h for h in parts[1:] if _HOST_RE.match(h) and not any(c in h for c in "*?!"))
    return out


def ssh_probe(alias: str, rpath: str) -> bool | None:
    """Read-only: does `rpath` exist on `alias`? True / False; None = could not tell (no answer, no key, timeout)."""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "--", alias, "test", "-e",
                            shlex.quote(rpath)], stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(r.returncode)


ALIASES = ssh_aliases                     # injectable (tests): never read the real ~/.ssh/config there
PROBE = ssh_probe                         # injectable (tests): never run ssh there


def _remote(arg: str):
    """`alias:path` → (alias, path); None for a local path, user@host, a URL or an rsync daemon `host::module`."""
    if ":" not in arg or arg.startswith(("/", ".", "~")) or "::" in arg or "//" in arg:
        return None
    host, path = arg.split(":", 1)
    return (host, path) if host else None


def _scp_opts(argv: list[str]) -> list[str] | None:
    """The operands of `scp [whitelisted options] …`; None for any other option."""
    pos, i = [], 1
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return pos + argv[i + 1:]
        if a.startswith("-") and len(a) > 1:
            for k, c in enumerate(a[1:], 1):
                if c in _SCP_FLAGS:
                    continue
                if c not in _SCP_VAL:
                    return None
                val = a[k + 1:]
                if not val:
                    i += 1
                    if i >= len(argv):
                        return None
                    val = argv[i]
                if not _SCP_VAL[c].fullmatch(val):
                    return None
                break
        else:
            pos.append(a)
        i += 1
    return pos


def _rsync_opts(argv: list[str]) -> list[str] | None:
    """The operands of `rsync [whitelisted options] …`; None for any other option."""
    pos, opts = [], True
    for a in argv[1:]:
        if opts and a == "--":
            opts = False
        elif opts and a.startswith("--"):
            if a not in _RSYNC_LONG:
                return None
        elif opts and a.startswith("-") and len(a) > 1:
            if not set(a[1:]) <= _RSYNC_FLAGS:
                return None
        else:
            pos.append(a)
    return pos


def uploads(argv: list[str]) -> list[tuple[str, str, str]] | None:
    """[(host, remote path as given, local source)] of a plain `scp` / `rsync` upload (argv[0] exactly that word, only
    whitelisted options); None for anything else (a download, options that change where or how it connects or what it
    removes, remote-to-remote)."""
    name = argv[0] if argv else ""
    if name == "scp":
        pos = _scp_opts(argv)
    elif name == "rsync":
        pos = _rsync_opts(argv)
    else:
        return None
    if pos is None or len(pos) < 2:
        return None
    srcs, dst = pos[:-1], _remote(pos[-1])
    if dst is None or any(_remote(x) is not None or "@" in x.split("/", 1)[0] or x.startswith("-") for x in srcs):
        return None
    host, path = dst
    if not _HOST_RE.match(host):
        return None
    out = []
    for src in srcs:
        if any(c in src for c in "*?[") or not src.strip("/."):
            return None
        if name == "rsync" and src.endswith("/"):
            dest = path.rstrip("/") or "."
        elif not path or path.endswith("/") or len(srcs) > 1:
            dest = (path.rstrip("/") + "/" if path.rstrip("/") else "") + os.path.basename(src.rstrip("/"))
        else:
            dest = path
        out.append((host, dest, src))
    return out


_RISKY_REMOTE = re.compile(r"cron|systemd|autostart|launchagents|launchdaemons|profile|bashrc|zshrc|authorized_keys|sudoers",
                           re.I)


def _remote_path(p: str) -> str | None:
    """The remote target as `ssh … test -e` gets it; None when it is not a plain new file the copy could create safely:
    `~`, globs, `..`, any dot-component (a dotfile / dot-folder: .bashrc, .config/autostart, .ssh …), /etc, or a name
    that starts things on its own (cron, systemd, autostart, LaunchAgents, profile …)."""
    if p.startswith("~/"):
        p = p[2:]
    if not p or p in (".", "..") or "~" in p or any(c in p for c in "*?[\n\0"):
        return None
    parts = [x for x in p.split("/") if x]
    if not parts or any(x.startswith(".") for x in parts) or _RISKY_REMOTE.search(p):
        return None
    if p.startswith("/") and parts[0] in ("etc", "usr", "bin", "sbin", "lib", "lib64", "boot", "var", "root", "proc", "sys", "dev"):
        return None
    return p


def new_remote_copy(tool: str, tool_input, classify) -> bool:
    """Every dangerous piece of the Bash line is a scp / rsync upload to a registered ssh alias whose target does not exist
    yet; every other piece is low risk on its own."""
    if tool != "Bash" or classify is None:
        return False
    ps = pieces(action_text(tool, tool_input))
    if not ps:
        return False
    targets = []
    for p in ps:
        pv = classify(tool, {"command": p})
        if not pv.cats:
            continue
        if set(pv.cats) != {"send"} or set(getattr(pv, "rules", []) or []) - {"send.upload"} or _SUBST.search(p) \
                or re.search(r"[|<>$`]", p):
            return False
        try:
            argv = shlex.split(p)
        except ValueError:
            return False
        up = uploads(argv)
        if not up:
            return False
        targets += up
    if not targets or len(targets) > MAX_PROBES:
        return False
    known = ALIASES()
    for host, dest, _src in targets:
        rp = _remote_path(dest)
        if host not in known or rp is None or PROBE(host, rp) is not False:
            return False
    return True


def reversible(tool: str, tool_input, verdict, cwd: str | None, classify=None) -> str | None:
    """A short reason when the host itself can undo the action; None = treat as irreversible."""
    cats = set(getattr(verdict, "cats", []) or [])
    if not cats or "opaque" in cats or not cats <= {"delete", "send"}:
        return None
    if cats == {"send"} and new_remote_copy(tool, tool_input, classify):
        return "new_remote_file"               # P339: undo = delete the copy
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
            if (g.get("upload") or g.get("prefix")) and not _structured_ok(g, tool, text):
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


def _structured_ok(g: dict, tool: str, text: str) -> bool:
    """P339 (review #1): an `upload` / `prefix` grant covers exactly one plain command (no chain, pipe, substitution,
    variable or redirection) — `upload`: `scp` / `rsync` (the grant's own word) with whitelisted options only, every
    destination on exactly that ssh alias, no remote source, a plain remote path; `prefix`: argv starts with those words."""
    if tool != "Bash":
        return False
    segs = _segments(text)
    if not segs or len(segs) != 1:
        return False
    argv = segs[0]
    up, pre = g.get("upload"), g.get("prefix")
    if up:
        if not isinstance(up, dict) or argv[0] != up.get("via"):
            return False
        got = uploads(argv)
        if not got or any(h != up.get("host") or _remote_path(dest) is None for h, dest, _src in got):
            return False
    if pre:
        if not isinstance(pre, list) or len(argv) < len(pre) or argv[:len(pre)] != pre:
            return False
    return True


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
    rev = reversible(tool, tool_input, verdict, cwd, classify)
    if rev:
        return "reversible", rev
    cats = set(verdict.cats)
    return "hold", ("opaque" if "opaque" in cats else "credentials" if "credentials" in cats else "irreversible")


# ------------------------------------------------------------------ words
WHY = {"zh": {"injection": "疑似注入：命令在执行它下载或解码出来的内容", "hooks": "在改钩子 / 审批设置文件",
              "settings": "在改 Agent J 的审批或安全设置", "trash": "移到回收站，可以还原", "git": "git 里有提交，可以还原",
              "git_push": "推到已有的远端，可以再提交撤回", "opaque": "看不清要做什么", "credentials": "改或查看密码、密钥",
              "irreversible": "不在授权范围内，且无法撤回",
              "new_remote_file": "复制到已登记的机器上，目标文件原来不存在，删掉副本就能撤回"},
       "en": {"injection": "possible injection: the command runs what it downloaded or decoded", "hooks": "rewrites a hook / approval file",
              "settings": "changes an Agent J approval or security setting", "trash": "moved to the trash, can be restored",
              "git": "committed in git, can be restored", "git_push": "a push to a remote it already has, can be reverted",
              "opaque": "can't tell what it does", "credentials": "changes or shows passwords or keys",
              "irreversible": "outside the grant and can't be undone",
              "new_remote_file": "a copy to a registered machine where the file did not exist yet: deleting the copy undoes it"}}


def why_text(key: str, lang: str = "zh") -> str:
    return WHY["en" if lang == "en" else "zh"].get(key, key)


def hold_message(key: str, lang: str = "zh", reason: str = "", rule: str = "") -> str:
    """What the Agent reads when a held step was not allowed (dispatch-stop-rule-scope: only this branch waits).
    P358 (#68): a card nobody answered says so (the owner may be away) and names the rule that held it (`rule`, the card's
    why — patterns and file names, never a value); a credentials hold suggests a read-only way round it."""
    en = lang == "en"
    rule = " ".join(str(rule or "").split())[:160]
    why = why_text(key, "en" if en else "zh") + ((" — " if en else "：") + rule if rule else "")
    if reason == "timeout":
        head = (f"Goal mode: the card for this step timed out unanswered (the owner may be away). It waited because: {why}. "
                if en else f"目标模式：这一步的卡片超时没人批（主人可能不在）。卡住的原因：{why}。")
    else:
        head = (f"Goal mode: this step needs the owner's decision ({why}); it was not approved. " if en else
                f"目标模式：这一步需要主人亲自决定（{why}），这次没有批准。")
    if key == "credentials":
        head += ("If this was only a read-only check, try again with a form that does not read credential files (leave out "
                 "the .env / key files, print key names only, or count). " if en else
                 "如果这只是只读检查，可以改用不碰凭据文件的写法再试（不读 .env / 密钥文件，只看键名或只数行数）。")
    if en:
        return (head + "It is listed in the goal's result card. Do not retry the same step now — carry on with the other "
                "independent parts of the goal. If more of the goal needs this kind of action, ask once for an extra grant "
                "with `agentj goal grant` (one card on the phone) instead of hitting a card per step.")
    return (head + "已记进目标结束报告「等你决定」。现在不要原样重试，先把目标里其它不依赖它的部分做完。后面还要做同类动作的话，"
            "用 `agentj goal grant` 一次性申请追加授权（手机上只出一张卡），不要一步一张卡地撞。")


# ------------------------------------------------------------------ P339: plain words for a held step (deterministic, no model)
def _names(xs, en: bool, n: int = 3) -> str:
    xs = [os.path.basename(x.rstrip("/")) or x for x in xs if x]
    sep = ", " if en else "、"
    out = sep.join(xs[:n])
    return out + ((" and more" if en else " 等") if len(xs) > n else "")


def _url_host(text: str) -> str:
    m = re.search(r"https?://([A-Za-z0-9.-]{3,80})", text)
    return m.group(1) if m else ""


def _phrase_piece(argv: list[str], cats: set, en: bool) -> str | None:
    name = os.path.basename(argv[0]) if argv else ""
    text = " ".join(argv)
    if name in ("scp", "rsync"):
        up = uploads(argv)
        if up:
            host = up[0][0]
            return (f"copy {_names([x[2] for x in up], en)} to {host}" if en else f"把 {_names([x[2] for x in up], en)} 复制到 {host}")
        if any(a.startswith(("--del", "--remove-")) for a in argv[1:]):
            return ("copy files to another machine and delete files there or here" if en else
                    "把文件复制到别的机器，并删除那边或本机的文件")
        return ("copy files to another machine" if en else "把文件复制到别的机器") + _force_tag(argv, en)
    if name in _RM or (name == "git" and len(argv) > 1 and "rm" in argv[1:3]):
        files = [a for a in argv[1:] if not a.startswith("-") and a not in ("trash", "rm", "-C")]
        hard = name == "rm" and any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in argv[1:])
        if en:
            return ("force-delete " if hard else "delete ") + (_names(files, en) if files else "files")
        return ("强制删除 " if hard else "删除 ") + (_names(files, en) if files else "文件")
    if name == "git" and len(argv) > 1 and argv[1] == "push":
        pos = [a for a in argv[2:] if not a.startswith("-")]
        where = pos[0] if pos else "origin"
        rest = argv[2:]
        if any(a in ("--mirror",) for a in rest):
            return (f"mirror-push to {where} (overwrites every branch there)" if en else f"镜像推送到 {where}（会覆盖那边所有分支）")
        if any(a in ("-d", "--delete") or (a.startswith(":") and len(a) > 1) for a in rest):
            return f"delete a branch on {where}" if en else f"删除 {where} 上的分支"
        if any(a in ("-f",) or a.startswith("--force") or a.startswith("+") for a in rest):
            return f"force-push code to {where} (overwrites its history)" if en else f"强制推送代码到 {where}（会覆盖那边的历史）"
        return f"push code to {where}" if en else f"把代码推送到 {where}"
    if name in ("curl", "wget", "http", "https") and "send" in cats:
        h = _url_host(text)
        return (f"send data to {h}" if h else "send data out") if en else (f"把数据发送给 {h}" if h else "把数据发出去")
    if name in ("mail", "mailx", "sendmail", "mutt"):
        to = [a for a in argv[1:] if "@" in a]
        return (f"send an e-mail to {to[0]}" if to else "send an e-mail") if en else (f"发邮件给 {to[0]}" if to else "发一封邮件")
    if name in ("npm", "pnpm", "yarn", "twine", "cargo", "gem") and "publish" in argv[1:3]:
        return "publish a package" if en else "发布一个软件包"
    if name == "gh":
        return "do something on GitHub (" + " ".join(argv[1:3]) + ")" if en else "在 GitHub 上操作（" + " ".join(argv[1:3]) + "）"
    return None


_CAT_PHRASE = {"zh": {"spend": "花钱", "delete": "删除文件", "send": "把数据发出去", "price": "改价格",
                      "credentials": "改或查看密码、密钥", "opaque": "执行一条看不清用途的命令"},
               "en": {"spend": "spend money", "delete": "delete files", "send": "send data out", "price": "change prices",
                      "credentials": "change or read passwords or keys", "opaque": "run a command whose purpose is unclear"}}


def human(tool: str, tool_input, verdict, lang: str = "zh", classify=None) -> str:
    """「把 pwone.py 复制到 olares」 / "copy pwone.py to olares": what a held step does, from the verdict and the parsed
    command (never from what the Agent says about it)."""
    en = lang == "en"
    k = "en" if en else "zh"
    cats = set(getattr(verdict, "cats", []) or [])
    out = []
    if tool == "Bash":
        ps = pieces(action_text(tool, tool_input)) or []
        for p in ps:
            pv = classify(tool, {"command": p}) if classify is not None else verdict
            pc = set(getattr(pv, "cats", []) or [])
            if not pc:
                continue
            try:
                argv = shlex.split(p.split("<<", 1)[0])
            except ValueError:
                argv = []
            ph = _phrase_piece(argv, pc, en) if argv else None
            if ph is None:
                c = next((c for c in ("credentials", "spend", "price", "delete", "send", "opaque") if c in pc), "")
                ph = _CAT_PHRASE[k].get(c) or ("run a risky command" if en else "执行一条有风险的命令")
                if c == "spend":
                    from .privacy import owner_text
                    ph += (": " if en else "：") + owner_text(p)[:40]
                ph += _force_tag(argv, en)
            if ph not in out:
                out.append(ph)
    elif tool in _WRITE_TOOLS and isinstance(tool_input, dict):
        f = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        out.append((f"change the file {os.path.basename(str(f))}" if en else f"改文件 {os.path.basename(str(f))}"))
    elif tool == "WebFetch" and isinstance(tool_input, dict):
        h = _url_host(str(tool_input.get("url") or ""))
        out.append(f"open {h}" if en else f"打开网页 {h}")
    elif tool.startswith("agentj mail"):
        out.append("send an e-mail" if en else "发一封邮件")
    if not out:
        c = next((c for c in ("credentials", "spend", "price", "delete", "send", "opaque") if c in cats), "")
        out.append(_CAT_PHRASE[k].get(c) or ("run a risky command" if en else "执行一条有风险的命令"))
    more = len(out) - 2
    tail = ((f", and {more} more" if en else f"等 {more} 项") if more > 0 else "")
    return (", then " if en else "，再").join(out[:2])[:160 - len(tail)] + tail


_FORCE = {"--force", "-f", "--force-with-lease", "--force-if-includes", "--delete", "--mirror", "--prune"}


def _force_tag(argv: list[str], en: bool) -> str:
    """P339 (review #3): a dangerous flag is said in the phrase, never hidden in the raw command."""
    fl = [a for a in argv[1:] if a in _FORCE or a.startswith(("--force", "--delete", "--del", "--remove-"))]
    if not fl:
        return ""
    return (" (with " + " ".join(sorted(set(fl)))[:40] + ")") if en else ("（带 " + " ".join(sorted(set(fl)))[:40] + "）")


def raw_text(summary: str) -> str:
    """The command as it was asked (≤ 400 characters), credentials redacted like the owner's own channels."""
    from .privacy import owner_text
    return owner_text((summary or "").strip())[:400]


def offer_grant(tool: str, tool_input, verdict, cwd: str | None, classify=None, lang: str = "zh") -> list | None:
    """P339 「批准，并加进目标授权」: the narrowest grant (in the proposal's own format, validated like one) that covers this held
    step and its kind — scp / rsync to that same host alias, a delete under the same folder, the same two-word command
    (`git push`, `npm publish`) — or None (money, credentials, opaque, nothing narrow enough)."""
    en = lang == "en"
    cats = set(getattr(verdict, "cats", []) or [])
    if not cats or not cats <= set(ADD_CATS) or tool != "Bash":
        return None
    ps = pieces(action_text(tool, tool_input))
    if not ps:
        return None
    out = []
    for p in ps:
        pv = classify(tool, {"command": p}) if classify is not None else verdict
        pc = list(getattr(pv, "cats", []) or [])
        if not pc:
            continue
        if not set(pc) <= set(ADD_CATS) or _SUBST.search(p):
            return None
        try:
            argv = shlex.split(p.split("<<", 1)[0])
        except ValueError:
            return None
        if not argv:
            return None
        name = os.path.basename(argv[0])
        for c in pc:
            if c == "delete":
                t = delete_targets(tool, {"command": p}, cwd)
                if not t or not t[0]:
                    return None
                # review #5: the grant covers exactly what this step deletes (not its folder), and never home or a
                # folder within two levels of it, nor a top-level folder
                tgts = sorted(set(t[0]))
                if len(tgts) > 6 or any(_too_wide(x) or _too_close(x) for x in tgts):
                    return None
                g = {"cat": "delete", "what": ("delete " + ", ".join(tgts) if en else "删除 " + "、".join(tgts))[:160],
                     "paths": tgts, "max": ADD_MAX}
            elif name in ("scp", "rsync") and argv[0] == name and uploads(argv):
                hosts = {x[0] for x in uploads(argv)}
                host = next(iter(hosts))
                if len(hosts) != 1 or not _HOST_RE.match(host) or len(host) < 2:
                    return None
                g = {"cat": "send", "what": f"{name}: copy files to {host}" if en else f"{name} 复制文件到 {host}",
                     "upload": {"host": host, "via": name}, "max": ADD_MAX}
            elif len(argv) >= 2 and argv[0] == name and _WORD_RE.fullmatch(name) and not argv[1].startswith("-") \
                    and _WORD_RE.fullmatch(argv[1]):
                pre = name + " " + argv[1]
                if len(pre) < 3:
                    return None
                g = {"cat": c, "what": f"commands starting with 「{pre}」" if en else f"「{pre}」开头的命令",
                     "prefix": [name, argv[1]], "max": ADD_MAX}
            else:
                return None
            if g not in out:
                out.append(g)
    if not out or len(out) > 3:
        return None
    try:
        norm = check_grants(out, 3)
    except GoalError:
        return None
    if len(_offer_line(norm, lang)) > OFFER_MAX:
        return None                            # review #5: never offer a line the card would have to cut
    return norm


def grant_line(g: dict, lang: str = "zh") -> str:
    """One grant as the phone shows it (and, for 「批准，并加进目标授权」, as the owner signs it)."""
    en = lang == "en"
    rule = []
    if g.get("match"):
        rule.append(("contains " if en else "含 ") + " + ".join(f"「{m}」" for m in g["match"]))
    if g.get("upload"):
        u = g["upload"]
        rule.append((f"only `{u['via']}` uploads to {u['host']}, one plain command" if en else
                     f"仅限 {u['via']} 上传到 {u['host']}（单条命令）"))
    if g.get("prefix"):
        pre = " ".join(g["prefix"])
        rule.append((f"the command starts with 「{pre}」" if en else f"命令以「{pre}」开头"))
    if g.get("dir"):
        rule.append(("in " if en else "在 ") + g["dir"])
    if g.get("paths"):
        rule.append(("only under " if en else "仅限 ") + ", ".join(g["paths"]))
    if g.get("usd"):
        rule.append((f"spend cap ${g['usd']:g}" if en else f"花费上限 ${g['usd']:g}"))
    rule.append((f"≤ {g['max']} times" if en else f"最多 {g['max']} 次"))
    return f"[{CAT['en' if en else 'zh'][g['cat']]}] {g['what']} — " + "; ".join(rule)


OFFER_MAX = 300                           # the phone drops (never cuts) a longer goal_add: it is signed byte-exact


def _offer_line(grants: list, lang: str = "zh") -> str:
    en = lang == "en"
    return "; ".join(grant_line(g, lang) for g in grants) + (" · until the goal ends" if en else " · 到目标结束为止")


def offer_text(grants: list, lang: str = "zh") -> str:
    return _offer_line(grants, lang)[:OFFER_MAX]


def grant_card_text(d: dict, grants: list, lang: str = "zh") -> str:
    """P339 `agentj goal grant`: what the owner signs to add grants to the running goal."""
    en = lang == "en"
    lines = ["🎯 Goal running — add to its grant" if en else "🎯 目标进行中 · 追加授权",
             ("Goal: " if en else "目标：") + d["goal"],
             "Also pre-approve until the goal ends (logged, no card):" if en else "追加预先放行（到目标结束为止，全部记日志，不再弹卡）："]
    lines += ["· " + grant_line(g, lang) for g in grants]
    lines.append((f"The {len(d.get('grants') or [])} grant(s) already signed stay as they are." if en else
                  f"已签的 {len(d.get('grants') or [])} 项授权不变。"))
    return "\n".join(lines)


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
    lines += blocked_lines(d, en)
    if not is_live(st, d, now):
        lines.append("(no longer valid: it ends now)" if en else "（已失效，马上结束）")
    return "\n".join(lines)


def blocked_lines(d: dict, en: bool, n: int = 10) -> list[str]:
    """P358 (#68): what each of the 「等你决定 N 件」 is — the tool and one plain-words reason (the plain phrase, or the
    rule that held it), never the command itself (it may carry a credential; the result card folds the raw text)."""
    blocked = (d.get("blocked") or [])[-n:]
    if not blocked:
        return []
    k = "en" if en else "zh"
    out = []
    for i, b in enumerate(blocked, 1):
        tool = str(b.get("tool") or "?")[:40]
        what = str(b.get("human") or "").strip()
        rule = str((b.get("rule_en") if en else None) or b.get("rule") or "").strip()
        why = why_text(str(b.get("why") or ""), k)
        if not what:
            what = rule or why
            rule = ""
        tail = ("; " if en else "；").join(x for x in (rule, why) if x and x != what)
        out.append(f"  {i}. [{tool}] {what}" + (f" ({tail})" if tail else "") if en else
                   f"  {i}.【{tool}】{what}" + (f"（{tail}）" if tail else ""))
    return out


RESULT = {"zh": {"achieved": "达成", "not_achieved": "未达成", "expired": "到期", "stopped": "已停止", "estop": "全部停下",
                 "device_gone": "授权手机已解除配对", "replaced": "被新目标替换"},
          "en": {"achieved": "achieved", "not_achieved": "not achieved", "expired": "deadline reached", "stopped": "stopped",
                 "estop": "Stop everything", "device_gone": "the granting phone was unpaired", "replaced": "replaced"}}


def code_span(text: str) -> str:
    """P341 (#19): a command quoted in a system card is shown as code — "$HOME" / "$(…)" never become math on the phone.
    The fence is one backtick longer than any run inside (CommonMark), padded when the command starts or ends with one."""
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    fence = "`" * (max((len(r) for r in re.findall(r"`+", t)), default=0) + 1)
    pad = " " if t[0] == "`" or t[-1] == "`" else ""
    return f"{fence}{pad}{t}{pad}{fence}"


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
    blocked = (d.get("blocked") or [])[-10:]
    if not blocked:
        lines.append("Nothing is waiting for you." if en else "没有卡住等你决定的事。")
        return "\n".join(lines)
    # P339: plain words per item (who wants to do what, why it waited, what happened, how to allow it now); the raw
    # commands go into one `details` block the phone shows folded (md.js), older phones as a code block
    lines.append(f"Waiting for your decision ({len(blocked)}):" if en else f"卡在这里，等你决定（{len(blocked)} 件）：")
    raws = []
    for n, b in enumerate(blocked, 1):
        who = b.get("who") or ("the Agent" if en else "Agent")
        what = b.get("human") or ""
        if what:
            lines.append(f"Item {n}: {who} wanted to {what}" if en else f"第 {n} 件：{who} 想{what}")
        else:
            lines.append((f"Item {n}: " if en else f"第 {n} 件：") + code_span(str(b.get("summary") or "")[:160]))   # P341 (#19)
        lines.append(("· Why it wasn't approved automatically: " if en else "· 为什么没自动批准：") + why_text(b.get("why", ""), k))
        lines.append("· If you don't tap: skipped this step, everything else went on" if en else "· 不点会怎样：已跳过这一步，其余照做")
        lines.append((f"· To allow it now: reply \"allow item {n}\" — the Agent redoes this step and the phone shows you a card "
                      "to approve" if en else
                      f"· 现在要放行：回复「允许第 {n} 件」，Agent 会重做这一步，手机上会再弹一张卡给你批"))
        raw = (b.get("raw") or b.get("summary") or "").replace("~~~", "~ ~ ~")
        raws.append((f"Item {n}: " if en else f"第 {n} 件：") + raw)
    lines.append("~~~~details")
    lines += raws
    lines.append("~~~~")
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
        self.owner_at: float | None = None       # P336: when the owner's own `/goal <what>` arrived (monotonic)

    def owner_asked(self) -> None:
        self.owner_at = time.monotonic()

    def _preauth(self, g: dict):
        """P336: the signed switch's grant, only for the proposal that answers the owner's own /goal (once, within
        OWNER_GOAL_SECS), from the main thread of an owner turn, with no spend / price grant — else None (the card)."""
        from . import approval_level, provenance
        h = self.host
        at, self.owner_at = self.owner_at, None
        if at is None or time.monotonic() - at > approval_level.OWNER_GOAL_SECS:
            return None
        if getattr(h, "task_label", None) is not None or provenance.untrusted_turn(getattr(h, "agent", None)):
            return None
        if any(x.get("cat") in ("spend", "price") for x in g.get("grants") or []):
            return None                          # money is never granted without the owner seeing the grant
        return approval_level.goal_preauth(self.st, getattr(h, "preferences", {}))

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

    def block(self, summary: str, why: str, tool: str = "", cats=(), human_text: str = "", who: str = "", rule: str = "",
              rule_en: str = "") -> None:
        """P339: also the tool, its danger categories, the plain phrase, who asked and the raw command (≤ 400, redacted).
        P358: `rule` / `rule_en` = the card's why (which rule held it, redacted) for `agentj goal status`."""
        def f(d):
            b = d.setdefault("blocked", [])
            from .privacy import owner_text
            line = owner_text((summary or "").split("\n", 1)[0])[:160]     # review #3: redacted like raw
            raw = raw_text(summary)
            if not any(x.get("summary") == line and x.get("raw", raw) == raw for x in b):
                rec = {"at": int(time.time()), "summary": line, "why": why, "tool": str(tool)[:64],
                       "cats": [str(c) for c in cats][:6], "human": str(human_text)[:160], "who": str(who)[:60], "raw": raw}
                if rule:
                    rec["rule"] = owner_text(" ".join(str(rule).split()))[:160]
                if rule_en:
                    rec["rule_en"] = owner_text(" ".join(str(rule_en).split()))[:160]
                b.append(rec)
            del b[:-MAX_BLOCKED]
        self._update(f)

    def add_grants(self, grants: list, rid: str, device: str | None, how: str, gid: str | None = None,
                   offer_sha256: str | None = None) -> bool:
        """P339: append grants (already checked) to the running goal; they end with it. False = no goal / another goal
        than `gid` (the one the offer was made for) / too many."""
        ok = []

        def f(d):
            if gid is not None and d.get("id") != gid:
                return
            have = d.setdefault("grants", [])
            if len(have) + len(grants) > MAX_ALL_GRANTS:
                return
            for g in grants:
                have.append({**g, "added": {"rid": rid, "at": int(time.time()), "how": how}})
            ok.append(True)
        self._update(f)
        if ok:
            self.st.log("goal_grant_add", rid=rid, device=device, how=how, n=len(grants),
                        **({"offer_sha256": offer_sha256} if offer_sha256 else {}))
            self.host.activity("goal_grant_add", text="; ".join(g["what"] for g in grants)[:120], by=how)
        return bool(ok)

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
        text = card_text(g, lang)
        pre = self._preauth(g)
        if pre is not None:                     # P336: the owner's signed 「设了目标就不弹窗」 is the signature — no card
            return self._start(d, g, pre, text)
        self.waiting = gid

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

    def _start(self, d: dict, g: dict, pre: dict, text: str) -> dict:
        """P336: start a proposal at once under the signed switch: approvals.log `reason: goal_preauth` (grant = the
        switch's control line), the grant bound to the remote that signed the switch (still paired, same key)."""
        from . import approvals
        h, gid = self.host, d["id"]
        rid = new_id()
        approvals.record(self.st, rid=rid, agent=h.agent.kind if getattr(h, "agent", None) else "?", tool=TOOL,
                         input_sha256=approvals.input_digest({"goal": gid, "digest": d["digest"], "card": text}),
                         shown_sha256=approvals.shown_digest(TOOL, text), decision="allow", reason="goal_preauth",
                         device=pre["device"], grant=pre["rid"], auto="goal")
        d.update(state="active", started=int(time.time()), preauth=True,
                 grant={"rid": rid, "device": pre["device"], "sk_sha": pre["sk_sha"]})
        save(self.st, d)
        self.st.log("goal_start", id=gid, device=pre["device"], how="preauth")
        h.activity("goal_start", text=g["goal"][:120], grants=len(g["grants"]), by="goal_nocard")
        h.emit("goal_start", id=gid)
        en = self.lang() == "en"
        h.agent_notice((f"🎯 Goal mode on until {_when(g['deadline'])} (no opening card — your switch): " if en else
                        f"🎯 目标模式开始，截止 {_when(g['deadline'])}（「设了目标就不弹窗」已打开，没有弹开工卡）：") + g["goal"])
        return {"result": "active", "id": gid, "deadline": int(g["deadline"]), "preauth": True}

    # ---------------------------------------------------------- P339: an extra grant while the goal runs (one card)
    async def extra(self, grants, wait: bool = True) -> dict:
        """`agentj goal grant`: new sub-work needs actions outside the signed grant → ONE card (tool `agentj goal grant`,
        summary = grant_card_text); the owner's signed allow from a paired phone appends them to goal.json."""
        import asyncio
        h = self.host
        if h.stopped():
            raise GoalError("stopped", "Stop everything is on")
        d = self.current()
        if d is None:
            raise GoalError("no_goal", "no goal is running")
        if self.waiting:
            raise GoalError("pending", "a goal card is already waiting on the phone")
        norm = check_grants(grants, MAX_GRANTS)
        if not norm:
            raise GoalError("shape", "grants: at least one")
        if len(d.get("grants") or []) + len(norm) > MAX_ALL_GRANTS:
            raise GoalError("too_many", f"a goal holds at most {MAX_ALL_GRANTS} grants")
        from .taskspec import secret_like
        if secret_like(json.dumps(norm, ensure_ascii=False)):
            raise GoalError("secret", "a grant must not hold a credential")
        gid, lang = d["id"], self.lang()
        text = grant_card_text(d, norm, lang)
        dg = hashlib.sha256(json.dumps(norm, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.waiting = gid

        async def card() -> dict:
            try:
                ans = await h.ask(TOOL_GRANT, {"goal": gid, "digest": dg, "card": text}, batch=False, grant_info=True,
                                  ttl=5 * h.ask_ttl)
            finally:
                self.waiting = None
            info = ans.get("grant_info") or {}
            cur = self.current()
            if ans.get("behavior") != "allow" or not info.get("device") or h.stopped() or not cur or cur.get("id") != gid:
                self.host.activity("goal_grant_declined", text="; ".join(g["what"] for g in norm)[:120])
                return {"result": "declined", "id": gid}
            if not self.add_grants(norm, info.get("rid"), info["device"], "card", gid=gid):
                return {"result": "refused", "why": "too_many", "detail": f"at most {MAX_ALL_GRANTS} grants"}
            en = lang == "en"
            self.host.agent_notice(("🎯 Goal grant extended: " if en else "🎯 目标授权已追加：")
                                   + "; ".join(g["what"] for g in norm))
            return {"result": "added", "id": gid, "grants": len(cur.get("grants") or []) + len(norm)}

        if wait:
            return await card()
        task = asyncio.ensure_future(card())
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
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
            if t == "goal_grant":
                return await self.extra(req.get("grants"), bool(req.get("wait", True)))
        except GoalError as e:
            return e.out()
        return {"result": "refused", "why": "shape",
                "detail": "t: goal_propose | goal_status | goal_stop | goal_done | goal_progress | goal_grant"}


# ------------------------------------------------------------------ phone `/goal` (status / stop answered by the host)
def host_cmd(arg: str) -> str | None:
    a = (arg or "").strip().lower()
    return {"": "status", "status": "status", "stop": "stop", "状态": "status", "停止": "stop", "结束": "stop"}.get(a)


# ------------------------------------------------------------------ CLI (runs as the Agent, usually inside the fence)
def _client(req: dict, timeout: float) -> dict:
    from . import capability
    res = capability.client(req, timeout=timeout)
    return res if res is not None else {"result": "refused", "why": "no_serve", "detail": "Agent J is not running"}


def _grant_req(a) -> dict:
    """P339 `agentj goal grant --file <json> | --cat … --what … [--match …] [--path …] [--dir …] [--max N] [--usd X]`."""
    if a.file:
        try:
            raw = json.loads(sys.stdin.read() if a.file == "-" else open(os.path.expanduser(a.file), encoding="utf-8").read())
        except (OSError, ValueError):
            return {"result": "refused", "why": "shape", "detail": "--file: a JSON file (or - for stdin)"}
        grants = raw.get("grants") if isinstance(raw, dict) else raw
    elif a.cat and a.what:
        g = {"cat": a.cat, "what": a.what, "match": a.match or [], "paths": a.path or []}
        if a.dir:
            g["dir"] = a.dir
        if a.max is not None:
            g["max"] = a.max
        if a.usd is not None:
            g["usd"] = a.usd
        grants = [g]
    else:
        return {"result": "refused", "why": "shape", "detail": "--file <json>, or --cat and --what (+ --match / --path)"}
    try:
        check_grants(grants, MAX_GRANTS)        # say what is wrong before anything reaches the phone
    except GoalError as e:
        return e.out()
    return _client({"t": "goal_grant", "grants": grants, "wait": not a.no_wait}, timeout=700)


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
    elif sub == "grant":
        res = _grant_req(a)
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
    x = ps.add_parser("grant", help="目标进行中追加授权：手机上一张卡，签名后加进目标 / add a grant to the running goal (one card)")
    x.add_argument("--file", help="{\"grants\": [...]}（同 propose 的 grants 格式，- = stdin）/ grants like in propose")
    x.add_argument("--cat", choices=list(GRANT_CATS))
    x.add_argument("--what", help="一句话说明 / one line: what it is for")
    x.add_argument("--match", action="append", help="动作必须包含的文字（可重复）/ text the action must contain")
    x.add_argument("--path", action="append", help="只删这些目录下（cat delete）/ delete only under it")
    x.add_argument("--dir", help="只在这个目录里 / only in this folder")
    x.add_argument("--max", type=int, help=f"最多次数（默认 {DEFAULT_MAX}）/ at most N times")
    x.add_argument("--usd", type=float, help="花费上限 / spend cap")
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
