"""F30 (P73, ADR-A175): where the main Agent's Codex permissions come from, and the owner's `config.toml` as Codex reads it.

The main Agent runs on the system itself, like the owner's own terminal, unless the owner explicitly restricted Codex:

- independent session: the owner's effective `sandbox_mode` (Codex `config/read`, which already merges `$CODEX_HOME/config.toml`
  and project layers) when set → that value ("config"); not set → `danger-full-access` ("agentj", Agent J's default). Codex's
  own default (workspace-write without network, or read-only in an untrusted folder) is a harness default the owner never
  chose, so Agent J does not inherit it. A `mode: research` task stays `read-only` ("research"). Friends' peer sessions
  (peer_session.py) never come here: always fenced, tool-less, read-only.
- the value is sent on `thread/start` **and** `thread/resume`: measured on Codex 0.159.2 (`reports/qa/p73/f30-resume-probe.txt`),
  a resume without `sandbox` keeps what the old thread had, even after config.toml changed and serve restarted.
- shared session (the owner's desktop thread): the thread's last recorded turn decides ("desktop"), except Codex's plain
  built-in default (workspace-write, no network, no extra roots, on-request, built-in `:workspace` profile or none): nobody
  chose that, so it follows the owner's config.toml when set, else Agent J's default (`default_desktop_record`).

`sandbox_mode` written inside a `[table]` (typically appended at the end of the file, after the last table) is not the top-level
key Codex reads: `config_info` reports it (doctor, /status), `set_mode` / `fix` always write the top level (tomlkit keeps
comments and every other key).
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import time

MODES = ("read-only", "workspace-write", "danger-full-access")
FULL = "danger-full-access"
NATIVE = {"readOnly": "read-only", "workspaceWrite": "workspace-write", "dangerFullAccess": "danger-full-access",
          "externalSandbox": "external-sandbox"}


def codex_home(env=None) -> pathlib.Path:
    e = os.environ if env is None else env
    return pathlib.Path(e.get("CODEX_HOME") or os.path.join(e.get("HOME") or os.path.expanduser("~"), ".codex"))


def config_path(env=None) -> pathlib.Path:
    return codex_home(env) / "config.toml"


def _walk(node, path, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                continue
            if k == "sandbox_mode" and path:
                out.append(".".join(path))
            elif isinstance(v, (dict, list)):
                _walk(v, path + [k], out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, path, out)


def parse(text: str) -> dict:
    """{"top": mode|None, "misplaced": [dotted table names holding a sandbox_mode key], "error": None|"invalid"}.
    `[profiles.*]` is a different (legacy) Codex feature, not a misplaced key."""
    import tomllib
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {"top": None, "misplaced": [], "error": "invalid"}
    top = data.get("sandbox_mode")
    found: list = []
    _walk({k: v for k, v in data.items() if k != "profiles"}, [], found)
    return {"top": top if isinstance(top, str) else None, "misplaced": sorted(set(found)), "error": None}


def config_info(env=None) -> dict:
    p = config_path(env)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"path": str(p), "exists": False, "top": None, "misplaced": [], "error": None}
    except (OSError, UnicodeDecodeError):
        return {"path": str(p), "exists": True, "top": None, "misplaced": [], "error": "unreadable"}
    return {"path": str(p), "exists": True, **parse(text)}


def main_sandbox(human_mode, research: bool = False) -> tuple[str, str]:
    """(the `sandbox` parameter for thread/start|resume, its source) for an independent main-Agent thread."""
    if research:
        return "read-only", "research"
    if isinstance(human_mode, str) and human_mode in MODES:
        return human_mode, "config"
    return FULL, "agentj"


def default_desktop_record(context) -> bool:
    """True when a desktop thread's last turn_context is exactly Codex's built-in default permissions — the shape the
    Codex App records for its "Default permissions" without the owner choosing anything: workspace-write, no network, no
    extra writable roots, /tmp and $TMPDIR not excluded, approval on-request, the built-in `:workspace` profile (or a
    legacy record without profiles). Anything else (read-only, network on, extra roots, untrusted / never / granular, a
    custom profile) is an explicit choice and stays the owner's."""
    if not isinstance(context, dict):
        return False
    sb = context.get("sandbox_policy")
    if not isinstance(sb, dict) or sb.get("type") != "workspace-write":
        return False
    if sb.get("network_access") not in (None, False) or sb.get("writable_roots") not in (None, []):
        return False
    if sb.get("exclude_tmpdir_env_var") or sb.get("exclude_slash_tmp"):
        return False
    if context.get("approval_policy") != "on-request":
        return False
    active = context.get("active_permission_profile")
    if isinstance(active, dict) and active.get("id") is not None:
        return active.get("id") == ":workspace"
    return context.get("permission_profile") is None and not active


def mode_of(sandbox) -> str | None:
    """A thread answer's sandbox ({"type": "workspaceWrite", …}) or a mode string → the config.toml spelling."""
    if isinstance(sandbox, dict):
        sandbox = sandbox.get("type")
    if isinstance(sandbox, str):
        return NATIVE.get(sandbox, sandbox)
    return None


def label(source: str, mode: str | None, zh: bool = True) -> str:
    """One line for /status and doctor: where the main Agent's Codex permissions come from."""
    m = mode or "?"
    if source == "agentj":
        return ("底层直跑（Agent J 默认：danger-full-access，等同在终端里直接跑）" if zh else
                "running directly on the system (Agent J default: danger-full-access, like your own terminal)")
    if source == "config":
        return (f"你的沙箱设置：{m}（config.toml）" if zh else f"your sandbox setting: {m} (config.toml)")
    if source == "research":
        return "只读（研究模式任务）" if zh else "read-only (research task)"
    if source == "pending":
        return ("还没接上桌面线程：第一条手机消息时按这条对话在 Codex 桌面 App 里的权限（没选过就按 config.toml / Agent J 默认）" if zh else
                "not attached yet: the first phone message uses this conversation's Codex App permissions (never chosen → "
                "config.toml / Agent J default)")
    if source == "desktop":
        return (f"桌面线程权限：{m}（Codex 桌面 App 里这条对话的设置）" if zh else
                f"desktop thread permissions: {m} (set for this conversation in the Codex App)")
    return (f"Codex 自己的默认权限：{m}（这条对话的权限记录 Agent J 没核对过）" if zh else
            f"Codex's own default permissions: {m} (this thread's permission record is not verified by Agent J)")


def misplaced_text(tables, zh: bool = True) -> str:
    names = "、".join(f"[{t}]" for t in tables) if zh else ", ".join(f"[{t}]" for t in tables)
    if zh:
        return (f"你在 {names} 表里写了 sandbox_mode，Codex 不认，要写在文件最前面（第一个 [ ] 之前）；"
                "`agentj codex-sandbox fix` 可以帮你挪过去")
    return (f"sandbox_mode is written inside {names}; Codex ignores it there — it must be at the top of the file (before the "
            "first [ ]); `agentj codex-sandbox fix` moves it")


# ------------------------------------------------------------------ writing config.toml (always the top level)
def edit(text: str, mode: str | None, drop_misplaced: bool = True) -> str:
    """config.toml text with `sandbox_mode` set at the top level (None = removed: Agent J's default applies). Misplaced
    `sandbox_mode` keys inside tables (not `[profiles.*]`) are removed: Codex never read them. Comments and every other key
    stay (tomlkit)."""
    import tomlkit
    doc = tomlkit.parse(text)

    def strip(node, path):
        if not hasattr(node, "items"):
            return
        for k, v in list(node.items()):
            if k == "sandbox_mode" and path:
                del node[k]
            elif hasattr(v, "items") and not (not path and k == "profiles"):
                strip(v, path + [k])
            elif isinstance(v, list):
                for x in v:
                    strip(x, path + [k])
    if drop_misplaced:
        strip(doc, [])
    if mode is None:
        if "sandbox_mode" in doc:
            del doc["sandbox_mode"]
    else:
        if mode not in MODES:
            raise ValueError(mode)
        doc["sandbox_mode"] = mode
    out = tomlkit.dumps(doc)
    if parse(out)["top"] != mode:      # never write something Codex would read differently
        raise ValueError("top-level sandbox_mode did not land at the top level")
    return out


def _write(path: pathlib.Path, text: str) -> str | None:
    """Atomic, mode kept (0600 for a new file); the previous file is kept as config.toml.agentj-<time>.bak."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    mode = 0o600
    if path.exists():
        mode = path.stat().st_mode & 0o777
        stamp, n = time.strftime('%Y%m%d-%H%M%S'), 0
        backup = path.with_name(f"{path.name}.agentj-{stamp}.bak")
        while backup.exists():           # two edits in one second keep both originals
            n += 1
            backup = path.with_name(f"{path.name}.agentj-{stamp}-{n}.bak")
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    tmp = path.with_name(f".{path.name}.agentj-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return str(backup) if backup else None


def set_mode(mode: str | None, env=None) -> dict:
    p = config_path(env)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    new = edit(old, mode)
    backup = _write(p, new) if new != old else None
    return {"path": str(p), "changed": new != old, "backup": backup, **parse(new)}


def fix(env=None) -> dict:
    """Move a misplaced sandbox_mode to the top level. A top-level value already set wins (the misplaced ones are only
    removed); else the value from the last table in the file (the line the owner appended) moves up."""
    import tomllib
    p = config_path(env)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    info = parse(old)
    if info["error"]:
        raise ValueError("config.toml is not valid TOML")
    if not info["misplaced"]:
        return {"path": str(p), "changed": False, "backup": None, **info}
    want = info["top"]
    if want is None:
        data = tomllib.loads(old)
        vals: list = []

        def grab(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "sandbox_mode" and path and isinstance(v, str):
                        vals.append(v)
                    elif isinstance(v, (dict, list)) and not (not path and k == "profiles"):
                        grab(v, path + [k])
            elif isinstance(node, list):
                for v in node:
                    grab(v, path)
        grab(data, [])
        want = next((v for v in reversed(vals) if v in MODES), None)
    new = edit(old, want)
    backup = _write(p, new) if new != old else None
    return {"path": str(p), "changed": new != old, "backup": backup, **parse(new)}


# ------------------------------------------------------------------ doctor row and CLI
def doctor_row(st, env=None) -> dict | None:
    """`codex_perm` row (never ✗): which permissions the main Codex Agent gets, and a sandbox_mode Codex cannot see."""
    c = st.agent_config() if st.exists() else None
    if not c or c.get("kind") != "codex":
        return None
    info = config_info(env)
    shared = c.get("session_mode") == "shared"
    if info["error"]:
        return {"id": "codex_perm", "status": "warn", "summary": f"Codex 的 config.toml 读不懂（{info['error']}）/ Codex config.toml "
                f"could not be parsed", "hint": "codex 自己也会报错：先修好这个文件 / fix the file (Codex reports it too)"}
    src = "config" if info["top"] in MODES else "agentj"
    main = label(src, info["top"]) + " / " + label(src, info["top"], zh=False)
    if shared:
        main = ("共享模式：按桌面 App 里这条对话的权限；没选过（Codex 默认权限）时按 " + label(src, info["top"]) +
                " / shared: the desktop thread's own permissions; when never chosen (Codex default) → " + label(src, info["top"], zh=False))
    if info["misplaced"]:
        return {"id": "codex_perm", "status": "warn", "summary": "写了但没生效 / written but not in effect — " + misplaced_text(info["misplaced"]),
                "hint": "agentj codex-sandbox fix   (然后 / then: agentj service restart) — " + main}
    return {"id": "codex_perm", "status": "ok", "summary": main, "hint": ""}


def cmd(a) -> None:
    from .service import tilde
    try:
        if a.action == "set":
            if a.mode not in MODES:
                raise SystemExit(f"agentj codex-sandbox set {{{'|'.join(MODES)}}}")
            r = set_mode(a.mode)
        elif a.action == "default":
            r = set_mode(None)
        elif a.action == "fix":
            r = fix()
        else:
            r = config_info()
    except (OSError, ValueError) as e:
        raise SystemExit(f"Agent J：没有改 config.toml（{type(e).__name__}）/ config.toml unchanged") from e
    src = "config" if r.get("top") in MODES else "agentj"
    if a.json:
        print(json.dumps({**r, "path": tilde(r["path"]), "backup": tilde(r["backup"]) if r.get("backup") else None,
                          "source": src, "effective": r.get("top") if src == "config" else FULL}, ensure_ascii=False))
        return
    print(f"{tilde(r['path'])}: " + label(src, r.get("top")) + " / " + label(src, r.get("top"), zh=False))
    if r.get("misplaced"):
        print("! " + misplaced_text(r["misplaced"]))
        print("! " + misplaced_text(r["misplaced"], zh=False))
    if a.action != "status":
        print(("已改 / changed" if r.get("changed") else "没有变化 / nothing to change") +
              (f"（旧文件备份 / backup: {tilde(r['backup'])}）" if r.get("backup") else ""))
        if r.get("changed"):
            print("重启后生效（下一条手机消息起，同一段对话也按新设置）/ takes effect after: agentj service restart")


def add_parser(sub) -> None:
    p = sub.add_parser("codex-sandbox", help="主 Agent（Codex）的沙箱：status · set <mode> · default · fix / the main Codex Agent's sandbox",
                       description="没写 sandbox_mode = Agent J 默认底层直跑（danger-full-access）；写了就照你的。set / default / fix 只改 "
                                   "config.toml 顶层（第一个 [ ] 之前），保留注释与其它设置，先备份。/ No sandbox_mode = Agent J runs the "
                                   "main Agent directly on the system; set / default / fix only touch the top level of config.toml.")
    p.add_argument("action", nargs="?", choices=["status", "set", "default", "fix"], default="status")
    p.add_argument("mode", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd)


if __name__ == "__main__":     # pragma: no cover
    print(json.dumps(config_info(), ensure_ascii=False))
    sys.exit(0)
