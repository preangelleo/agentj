"""Working-root selection. Existing data is never moved or overwritten."""
from pathlib import Path
import os
from . import preferences


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
    return root


def record(st, root):
    root.mkdir(parents=True, exist_ok=True)
    raw, _ = preferences.read()
    raw = preferences.edit(raw, "agent.working_root", str(root))
    return preferences.transact(st, raw)
