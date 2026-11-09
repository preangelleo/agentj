"""Working-root selection. Existing data is never moved or overwritten."""
from pathlib import Path
import os


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
    from . import preferences
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
    if specified:
        choice = specified
    else:
        from . import preferences
        stored = preferences.get(preferences.effective(st), "agent.working_root", "")
        old = st.config().get("agent", {}).get("dir") if st.exists() else None
        choice = stored or old or "~/coding"
        if interactive:
            print("工作根目录 / Working root (new workflows become one child folder each):")
            for p in candidates(st):
                print("  " + p)
            choice = input(f"确认或输入目录 / Confirm or enter path [{choice}]: ").strip() or choice
    root = Path(choice).expanduser()
    if not root.is_absolute():
        raise ValueError("working root must be an absolute path or ~/path")
    root = root.resolve()
    if root == Path.home().resolve():
        raise ValueError("不能使用用户主目录根；请重新选择 ~/coding 等工作目录。 / Home root is unsafe; select a work folder such as ~/coding. Existing files and configuration have not been moved or changed.")
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


class _InitialRootSetup:
    """Process-private, one-use receipt for this actual fresh State.init only.

    The caller-selected path is not wire authority. State.init mints this only
    after proving absent/empty state *before* it writes its identity and log.
    """
    def __init__(self, st, root):
        self.state, self.root, self.used = st, str(Path(root).resolve()), False
        self.identity = (st.config(), st.signing_pub())
        self.generation = self._generation(st)

    @staticmethod
    def _generation(st):
        import hashlib
        # Fresh init has four tiny files. Any added history or rewritten entry
        # (including identical pairing metadata replaced by rename) invalidates.
        return tuple((p.relative_to(st.root).as_posix(), p.lstat().st_ino,
                      p.lstat().st_mtime_ns, p.lstat().st_ctime_ns,
                      hashlib.sha256(p.read_bytes()).digest() if p.is_file() and not p.is_symlink() else None)
                     for p in (st.root, *sorted(st.root.rglob('*'))))

    def consume(self, st, candidate):
        if self.used:
            return False
        self.used = True
        return self.live(st, candidate)

    def live(self, st, candidate):
        try:
            from . import preferences
            import copy
            current = preferences.validate(preferences.read()[1])
            expected = copy.deepcopy(current)
            preferences.put(expected, 'agent.working_root', self.root)
            return bool(st is self.state and candidate == expected
                        and not preferences.get(current, 'agent.working_root', '')
                        and not st.config().get('agent')
                        and not st.devices() and not st.removed_path.exists()
                        and self.identity == (st.config(), st.signing_pub())
                        and self.generation == self._generation(st))
        except (OSError, ValueError, TypeError):
            return False


def _consume_initial_setup(st, candidate):
    setup = getattr(st, '_initial_root_setup', None)
    st._initial_root_setup = None
    return setup if type(setup) is _InitialRootSetup and setup.consume(st, candidate) else None


def record(st, root):
    from . import preferences
    root.mkdir(parents=True, exist_ok=True)
    raw, _ = preferences.read()
    raw = preferences.edit(raw, "agent.working_root", str(root))
    return preferences.transact(st, raw, _working_root_record=True)
