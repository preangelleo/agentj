"""Extra command names next to `agentj`, as symlinks in the same bin directory — never shadowing anything:

- `aj`: the short command (0.10). Installed by `agentj init`, after the 0.9 → 0.10 state move, and by `agentj alias install`.
- `jarvis`: the ≤ 0.9 command name, for ONE version cycle and only on a computer that actually migrated (migrated.json
  records a moved 0.9 state directory). The wheel ships no `jarvis` script: a fresh install never gets that name, and
  `uv tool install` can never be blocked by someone else's `~/.local/bin/jarvis`. Invoked as `jarvis`, the CLI prints the
  rename notice first (cli.main looks at argv[0]).

Rules for both: created only when PATH has no command of that name at all, only in the directory of the `agentj` on PATH,
only when that directory is on PATH and writable; a source checkout gets nothing. A taken name is reported with the exact
file that owns it. `agentj alias remove` deletes only symlinks that resolve to this agentj.
"""
from __future__ import annotations

import os
import shutil
import sys

NAME = "aj"
COMPAT = "jarvis"


def _real(p: str) -> str:
    return os.path.realpath(p)


def _on_path(d: str, path: str | None) -> bool:
    rd = _real(d)
    return any(p and os.path.isabs(p) and _real(p) == rd for p in (path or "").split(os.pathsep))


def _mine() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "agentj")


def our_agentj(path: str | None = None) -> str | None:
    """The `agentj` command on PATH when it is this very installation (same venv), else None."""
    exe = shutil.which("agentj", path=path)
    if not exe:
        return None
    if os.path.exists(_mine()) and _real(exe) != _real(_mine()):
        return None
    return exe


def _is_ours(link: str, path: str | None) -> bool:
    if not os.path.islink(link):
        return False
    want = {_real(p) for p in (our_agentj(path), _mine()) if p and os.path.exists(p)}
    return _real(link) in want


def migrated() -> bool:
    """Did this computer move a 0.9 state directory (migrated.json with migrated_from)? Only then may `jarvis` exist."""
    from . import migrate
    from .state import State, state_dir
    try:
        return bool(migrate.read_record(State(state_dir())).get("migrated_from"))
    except OSError:
        return False


def status(path: str | None = None, name: str = NAME) -> dict:
    """{"name", "state": "installed" | "taken" | "absent" | "checkout" | "no_agentj", "link": path | None, "agentj": …}"""
    path = os.environ.get("PATH", "") if path is None else path
    from .agent import source_root
    found = shutil.which(name, path=path)
    if found and _is_ours(found, path):
        return {"name": name, "state": "installed", "link": found, "agentj": our_agentj(path)}
    if found:
        return {"name": name, "state": "taken", "link": found, "agentj": our_agentj(path)}
    if source_root():
        return {"name": name, "state": "checkout", "link": None, "agentj": None}
    exe = our_agentj(path)
    if not exe:
        return {"name": name, "state": "no_agentj", "link": None, "agentj": None}
    return {"name": name, "state": "absent", "link": None, "agentj": exe}


def install(path: str | None = None, name: str = NAME) -> dict:
    """→ status dict plus "changed": bool and "why" (when not installed): taken · checkout · no_agentj · not_on_path ·
    not_writable · error."""
    path = os.environ.get("PATH", "") if path is None else path
    s = status(path, name)
    if s["state"] != "absent":
        return {**s, "changed": False, "why": None if s["state"] == "installed" else s["state"]}
    exe = s["agentj"]
    d = os.path.dirname(os.path.abspath(exe))
    if not _on_path(d, path):
        return {**s, "changed": False, "why": "not_on_path"}
    if not os.access(d, os.W_OK):
        return {**s, "changed": False, "why": "not_writable"}
    link = os.path.join(d, name)
    if os.path.lexists(link):           # a dangling link / a non-executable file: never touch it
        return {**s, "state": "taken", "link": link, "changed": False, "why": "taken"}
    try:
        os.symlink(os.path.abspath(exe), link)
    except OSError as e:
        return {**s, "changed": False, "why": "error", "error": type(e).__name__}
    return {"name": name, "state": "installed", "link": link, "agentj": exe, "changed": True, "why": None}


def remove(path: str | None = None, name: str = NAME) -> dict:
    path = os.environ.get("PATH", "") if path is None else path
    s = status(path, name)
    if s["state"] != "installed":
        return {**s, "changed": False}
    os.unlink(s["link"])
    return {**status(path, name), "changed": True}


def names(with_compat: bool | None = None) -> list[str]:
    """The names `alias install` handles here: `aj`, plus `jarvis` on a migrated computer."""
    return [NAME] + ([COMPAT] if (migrated() if with_compat is None else with_compat) else [])


def install_all(path: str | None = None, with_compat: bool | None = None) -> list[dict]:
    return [install(path, n) for n in names(with_compat)]


def remove_all(path: str | None = None) -> list[dict]:
    return [remove(path, n) for n in (NAME, COMPAT)]


def status_all(path: str | None = None) -> list[dict]:
    """`aj` always; `jarvis` when it is ours or this computer migrated (otherwise there is nothing to say about it)."""
    out = [status(path, NAME)]
    c = status(path, COMPAT)
    if c["state"] == "installed" or migrated():
        out.append(c)
    return out


def tilde(p: str) -> str:
    h = os.path.expanduser("~").rstrip("/")
    return "~" + p[len(h):] if h and (p == h or p.startswith(h + "/")) else p


def line(r: dict) -> str:
    """One human line for any result of status / install / remove."""
    n, st, why = r.get("name", NAME), r["state"], r.get("why")
    what = ("短命令 `aj`（= agentj）", "short command `aj`") if n == NAME else \
           ("旧命令 `jarvis`（= agentj，下个版本移除）", "the old command `jarvis` (= agentj, until the next version)")
    if st == "installed":
        return (f"✓ 已装{what[0]}：{tilde(r['link'])} / {what[1]} installed" if r.get("changed")
                else f"✓ {what[0]}可用：{tilde(r['link'])} / {what[1]} works")
    if st == "taken":
        return (f"`{n}` 已被 {r['link']} 占用，没有装{'短命令' if n == NAME else '旧命令'}；继续用 agentj / `{n}` is taken by "
                f"{r['link']}: not installed, keep using agentj")
    if st == "checkout":
        return (f"这是源码目录里的 agentj，不装 `{n}`；想要的话自己在 shell 里加 alias / a source checkout: no `{n}` "
                "(add a shell alias yourself)")
    if st == "no_agentj":
        return f"PATH 里找不到这个 agentj，没有装 `{n}` / agentj is not on PATH: no `{n}`"
    if r.get("changed"):
        return f"✓ 已删除 `{n}` / `{n}` removed"
    if why == "not_on_path":
        return f"agentj 所在目录 {tilde(os.path.dirname(r['agentj']))} 不在 PATH 里，没有装 `{n}` / its folder is not on PATH: no `{n}`"
    if why == "not_writable":
        return f"不能写 {tilde(os.path.dirname(r['agentj']))}，没有装 `{n}` / that folder is read-only: no `{n}`"
    if why == "error":
        return f"没能装 `{n}`（{r.get('error')}）/ could not create `{n}`"
    return (f"没有装{what[0]}（运行 agentj alias install）/ {what[1]} not installed (agentj alias install)" if n == NAME
            else f"没有 `{n}` / no `{n}`")
