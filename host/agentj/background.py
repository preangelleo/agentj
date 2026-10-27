"""P552 (FEEDBACK #325 ②): background work gets no Agent J approval card — the main Agent reviews its result instead.

Decided by the program, never by the model's words:
  * `headless(tool, tool_input)` — the main Agent's shell call whose ONLY effect is starting a headless run of a harness:
    `claude -p …` / `claude --print …`, `codex exec …` (any of their own permission flags, e.g.
    `--dangerously-skip-permissions`, `--full-auto`, `--dangerously-bypass-approvals-and-sandbox`). Around it only: `cd <dir>`,
    the wrappers nohup / setsid / timeout / time, `cat <file> |` in front, input redirections, the prompt as one argument,
    `"$(cat <file>)"` or a quoted heredoc, output redirections into a log (a temp / cache folder or a `.log` / `.out` file), a
    trailing `&`. Anything else in the same call (a variable, an assignment, another command, a pipe out, `||`, a backtick)
    → not a pure dispatch: the call is judged as usual. Like sending a sub-agent off (#321): ① — no card.
  * a run of Agent J's own: a scheduled / dispatched workflow (`Host.task_label`) or an order's CEO (core/askbridge.py,
    `background="order"`) — Host.ask's `background` branch.
What stays (Host.ask): the four ③ kinds (confirm_tier.harness_keeps: money, the owner's data deleted, a seat, a key going out)
→ Face ID; a suspected attack on Agent J itself (goal.suspect: hook / approval / settings files, download-and-run) and Agent J's
own host decisions (approval_level.HARNESS_KEEPS) keep their card. The `safe` level keeps every card. Standard library only.
"""
from __future__ import annotations

import os
import re
import shlex

HARNESSES = ("claude", "codex")
WRAPPERS = ("nohup", "setsid", "time")
_SHELL_TOOLS = ("Bash", "exec_command", "shell_command", "execute_command", "bash", "shell", "local_shell")
_HEREDOC = re.compile(r"<<-?\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)\n\2[ \t]*(?=\n|$)", re.S)
_CAT_SUBST = re.compile(r"\"?\$\(\s*cat\s+('[^'\n$`]*'|\"[^\"\n$`]*\"|[^\s$`'\"()|;&<>]+)\s*\)\"?")
_HOME = re.compile(r"\$\{HOME\}|\$HOME(?![A-Za-z0-9_])")
_REDIR = ("<", ">", ">>", "&>", "&>>")
_DUP = ">&"
_LOG_DIRS = ("/tmp/", "/var/tmp/", "~/.cache/", "$HOME/.cache/")
MAX = 64 * 1024


def _text(tool: str, tool_input) -> str | None:
    if tool not in _SHELL_TOOLS or not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("command", tool_input.get("cmd"))
    if isinstance(raw, list) and all(isinstance(w, str) for w in raw):
        if len(raw) >= 3 and os.path.basename(raw[0]) in ("bash", "sh", "zsh") and raw[1] in ("-c", "-lc"):
            return raw[2]
        return " ".join(shlex.quote(w) for w in raw)
    return raw if isinstance(raw, str) else None


def _log_target(path: str) -> bool:
    """Where a run's output may go: /dev/null, a temp / cache folder, or a new `.log` / `.out` file in the current folder."""
    if not path or ".." in path.split("/") or path.startswith("-"):
        return False
    if path == "/dev/null":
        return True
    tmp = os.environ.get("TMPDIR")
    if any(path.startswith(d) for d in _LOG_DIRS + ((tmp.rstrip("/") + "/",) if tmp else ())):
        return not _cred(path)
    return "/" not in path and not path.startswith(".") and path.endswith((".log", ".out"))


# Flags a pure dispatch may carry: flag → takes a value. Anything else (a subcommand, `--settings` / `--setting-sources`
# that could switch hooks off, `--mcp-config` / `--plugin-dir` / codex `-c` / `--profile` / `--enable` that load programs or
# configuration, `--`) → not a dispatch (security review of P552, B1).
CLAUDE_FLAGS = {"-p": False, "--print": False, "--dangerously-skip-permissions": False, "--permission-mode": True,
                "--output-format": True, "--input-format": True, "--model": True, "--fallback-model": True,
                "--max-turns": True, "--verbose": False, "--add-dir": True, "--allowedTools": True, "--allowed-tools": True,
                "--disallowedTools": True, "--disallowed-tools": True, "--append-system-prompt": True,
                "--include-partial-messages": False}
CODEX_FLAGS = {"--full-auto": False, "--dangerously-bypass-approvals-and-sandbox": False, "--yolo": False, "-C": True,
               "--cd": True, "-o": True, "--output-last-message": True, "-m": True, "--model": True,
               "--skip-git-repo-check": False, "--json": False, "-s": True, "--sandbox": True, "--color": True,
               "--add-dir": True, "--ephemeral": False}


def _flags_ok(args: list, allowed: dict) -> bool:
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-" or not a.startswith("-"):
            i += 1                                  # the prompt (or `-` = read it from stdin)
            continue
        name, eq, _ = a.partition("=")
        if name not in allowed or (eq and not allowed[name]):
            return False
        i += 1 if eq or not allowed[name] else 2
    return True


def _harness(argv: list) -> str | None:
    """`claude -p …` / `codex exec …` with allowlisted flags only (bare names on PATH: a path could be any program)."""
    if not argv or argv[0] not in HARNESSES:
        return None
    if argv[0] == "claude":
        if len(argv) < 2 or not argv[1].startswith("-") or not any(a in ("-p", "--print") for a in argv[1:]):
            return None                             # `claude mcp … -p`, `claude update` …: a subcommand is not a run
        return "claude" if _flags_ok(argv[1:], CLAUDE_FLAGS) else None
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 1
    if i >= len(argv) or argv[i] not in ("exec", "e") or i + 1 < len(argv) and argv[i + 1] in ("resume", "review", "help"):
        return None
    return "codex" if _flags_ok(argv[1:i], CODEX_FLAGS) and _flags_ok(argv[i + 1:], CODEX_FLAGS) else None


def _cred(path: str) -> bool:
    """An input that is (or may be) a credential file: its content would go into the model's conversation."""
    try:
        from . import danger
        return bool(danger.cred_path(path))
    except Exception:  # noqa: BLE001 — cannot tell → treat it as one
        return True


def _segment(tokens: list) -> tuple[str | None, bool]:
    """One command between && ; & newline → (harness | None, allowed). Redirections are taken off first."""
    argv, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.isdigit() and i + 1 < len(tokens) and tokens[i + 1] in _REDIR + (_DUP,):
            i += 1                              # `2>` / `2>&1`: the descriptor number
            continue
        if tok == _DUP:
            if i + 1 >= len(tokens) or tokens[i + 1] not in ("1", "2"):
                return None, False
            i += 2
            continue
        if tok in _REDIR:
            if i + 1 >= len(tokens):
                return None, False
            target = tokens[i + 1]
            if tok != "<" and not _log_target(target):
                return None, False              # an output redirection somewhere other than a log
            if tok == "<" and target != "/dev/stdin" and _cred(target):
                return None, False              # a credential file fed to the run (review B2)
            i += 2
            continue
        argv.append(tok)
        i += 1
    while argv and argv[0] in WRAPPERS:
        argv = argv[1:]
    if argv[:1] == ["timeout"]:
        argv = argv[1:]
        while argv and argv[0].startswith("-"):
            argv = argv[2:] if argv[0] in ("-k", "-s", "--kill-after", "--signal") else argv[1:]
        argv = argv[1:] if argv and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[smhd]?", argv[0]) else ["?"]
        while argv and argv[0] in WRAPPERS:
            argv = argv[1:]
    if not argv:
        return None, True
    if argv[0] == "cd":
        return None, len(argv) <= 2 and "--" not in argv
    if argv[0] in ("true", ":"):
        return None, len(argv) == 1
    if any("=" in a.split("/", 1)[0] and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a) for a in argv[:1]):
        return None, False                      # an environment assignment (PATH=…, LD_PRELOAD=…) is not a dispatch
    h = _harness(argv)
    return h, h is not None


def _unsafe(text: str) -> bool:
    """Anything the shell would run, expand or read differently: outside quotes ` $ ( ) \\ # { } * ? [ ] ! and a carriage
    return (a comment, brace / glob expansion, history); inside double quotes ` $ \\ ! (single quotes are inert)."""
    q = None
    for ch in text:
        if q == "'":
            q = None if ch == "'" else q
        elif q == '"':
            if ch in "`$\\!":
                return True
            q = None if ch == '"' else q
        elif ch in "'\"":
            q = ch
        elif ch in "`$()\\#{}*?[]!\r":
            return True
    return q is not None


def headless(tool: str, tool_input) -> str | None:
    """The harness this shell call starts headless when that is ALL it does (see the module text), else None."""
    text = _text(tool, tool_input)
    if not isinstance(text, str) or not text.strip() or len(text) > MAX:
        return None
    text = _HEREDOC.sub(lambda m: "< /dev/stdin", text)          # a quoted heredoc body is data, never run by the shell
    if "<<" in text:
        return None                                             # an unquoted heredoc expands $(…) in its body
    for m in _CAT_SUBST.finditer(text):
        if _cred(m.group(1).strip("'\"")):
            return None                                         # `"$(cat ~/.ssh/id_rsa)"` as the prompt (review B2)
    text = _CAT_SUBST.sub(" PROMPT ", text)
    text = _HOME.sub("~", text)
    if _unsafe(text.replace("\\\n", " ")):
        return None
    try:
        lex = shlex.shlex(text.replace("\n", " ; "), posix=True, punctuation_chars="&;<>|")
        lex.whitespace_split = True
        lex.commenters = ""                     # the shell's `#` is rejected above; never let shlex swallow the rest
        tokens = list(lex)
    except ValueError:
        return None
    segments, cur, piped = [], [], False
    for tok in tokens:
        if tok == "|":                  # the one pipe allowed: `cat <file> | <harness>`
            if piped or len(cur) != 2 or cur[0] != "cat" or _cred(cur[1]):
                return None
            cur, piped = [], True
        elif tok in ("&&", ";", "&", ";;"):
            if piped and not cur:
                return None
            segments.append((cur, piped))
            cur, piped = [], False
        elif tok in _REDIR or tok == _DUP:
            cur.append(tok)
        elif set(tok) & set("|&;<>"):
            return None                 # ||, |&, ;&, <&, <<< …
        else:
            cur.append(tok)
    segments.append((cur, piped))
    found = None
    for seg, was_piped in segments:
        h, ok = _segment(seg)
        if not ok or (was_piped and not h):
            return None
        found = found or h
    return found
