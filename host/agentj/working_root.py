"""Working-root selection. Existing data is never moved or overwritten."""
from pathlib import Path
import os
from . import preferences


def protected_macos(root, *, platform=None, home=None):
    """Lexical AND resolved paths: macOS privacy applies to the folder, even if writable in a terminal."""
    import sys
    if (platform or sys.platform) != "darwin":
        return False
    h = Path(home or Path.home()).expanduser()
    paths = (h / n for n in ("Downloads", "Desktop", "Documents", "Library/Mobile Documents", "Library/CloudStorage"))
    target = Path(root).expanduser().absolute()
    return any(target == p or p in target.parents or target.resolve() == p.resolve() or p.resolve() in target.resolve().parents for p in paths)


MACOS_HINT = ("macOS 会拦截后台服务访问下载、桌面、文稿和 iCloud。把工作文件夹移到 ~/coding；例如空目标目录时运行 "
              "`mkdir -p ~/coding && mv ~/Downloads/编程 ~/coding/`，然后 `agentj agent codex --dir ~/coding`、"
              "`agentj service restart`（其它 AI 换成对应名称）。 / macOS may block the background service in Downloads, Desktop, "
              "Documents or iCloud. Move the work folder into ~/coding, select it with `agentj agent <harness> --dir ~/coding`, "
              "then run `agentj service restart`. Keep existing files; do not overwrite a destination.")


def candidates(st=None):
    found = []
    stored = preferences.get(preferences.effective(st), "agent.working_root", "")
    old = st.config().get("agent", {}).get("dir") if st and st.exists() else None
    for value in (stored, old, "~/coding", "~/Coding", "~/Projects", "~/projects"):
        if not value:
            continue
        p = Path(value).expanduser().resolve()
        if p.is_dir() and str(p) not in found:
            found.append(str(p))
    return found


def select(st, specified=None, interactive=False):
    existing = candidates(st)
    stored = preferences.get(preferences.effective(st), "agent.working_root", "")
    old = st.config().get("agent", {}).get("dir") if st.exists() else None
    choice = specified or stored or old or "~/coding"
    if interactive and not specified:
        print("工作根目录 / Working root (new workflows become one child folder each):")
        for p in existing:
            print("  " + p)
        choice = input(f"确认或输入目录 / Confirm or enter path [{choice}]: ").strip() or choice
    root = Path(choice).expanduser()
    if not root.is_absolute():
        raise ValueError("working root must be an absolute path or ~/path")
    root = root.resolve()
    from .fence import protected_paths
    for value in protected_paths(st):
        p = Path(value).resolve()
        if root == p or p in root.parents:
            raise ValueError("working root is inside a protected Agent J path")
    if root.exists() and not root.is_dir():
        raise ValueError("working root is not a directory")
    if protected_macos(root):
        print("! " + MACOS_HINT)
    return root


def record(st, root):
    root.mkdir(parents=True, exist_ok=True)
    raw, _ = preferences.read()
    raw = preferences.edit(raw, "agent.working_root", str(root))
    return preferences.transact(st, raw)
