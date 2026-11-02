"""Create only native OpenCode application directories before the Olares fence."""
from __future__ import annotations
import os
import re
import stat
from pathlib import Path

HOME = '/opt/data'
ROOTS = {'XDG_CONFIG_HOME': '.config', 'XDG_DATA_HOME': '.local/share',
         'XDG_CACHE_HOME': '.cache', 'XDG_STATE_HOME': '.local/state'}


def owner_server(cfg):
    port = cfg.get('shared_opencode_port')
    return cfg.get('session_mode') == 'shared' and type(port) is int and 1 <= port <= 65535


def _plans(st, env):
    from . import fence
    from .preferences import path
    from .instance import seats_root
    from .asr import shared_root
    protected = [str(st.root), str(path().parent), str(seats_root(env)), str(shared_root(HOME))]
    protected += [HOME + '/' + p for p in ('.local/state/agentj', '.local/state/agentjarvis-alpha',
                   '.ssh', '.gnupg', '.aws', '.claude', '.codex', '.kube',
                   '.config/systemd', '.local/share/systemd', '.agentj-olares') + fence._CTL_DIRS + fence._CONTAINER_DIRS]
    protected += fence._env_dirs(env)
    protected += [os.path.abspath(p) for p in env.get('SYSTEMD_UNIT_PATH', '').split(':') if p]
    protected = sorted({q for p in protected for q in (p, os.path.realpath(p))})
    plans = []
    for key, default in ROOTS.items():
        root = env.get(key) or HOME + '/' + default
        if not isinstance(root, str) or len(root) > 4096 or '\0' in root:
            raise ValueError('runtime_path')
        root = root.rstrip('/')
        if root != HOME and not root.startswith(HOME + '/'):
            raise ValueError('runtime_path')
        parts = root[len(HOME):].strip('/').split('/') if root != HOME else []
        if len(parts) > 64 or any(p in ('', '.', '..', 'systemd', 'agentperm') or
                re.fullmatch(r'agentj(?:-\d+|-shared)?|agentjarvis-alpha', p) for p in parts):
            raise ValueError('runtime_path')
        target = root + '/opencode'
        if any(fence._under(target, p) or fence._under(p, target) or fence._under(root, p) for p in protected):
            raise ValueError('runtime_protected')
        plans.append((*parts, 'opencode'))
    return plans


def _walk(parts, create):
    from .inbox import _open_dir
    def safe(fd):
        s = os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o022:
            raise ValueError('runtime_directory')
    fd = _open_dir(HOME, None, False)
    try:
        safe(fd)
        for part in parts:
            try:
                child = _open_dir(part, fd, create)
            except FileNotFoundError:
                if not create:
                    return
                raise
            try:
                safe(child)
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


def prepare(st, cfg):
    """Original service environment only; no caller root/env argument or permission fallback."""
    from . import fence, olares_supervisor as sup
    from .inbox import Unsafe
    if owner_server(cfg):
        return True  # an existing owner's server needs no directory preparation
    app = sup.enabled() or any(Path(p).exists() for p in (sup.ENTRY, sup.ENV_PARSER, sup.IMAGE_PYTHON))
    if not app:
        return True  # ordinary non-Olares launch is unchanged
    if fence.inside() or not sup.enabled() or not sup.trusted(__file__) or os.environ.get('HOME') != HOME:
        return False
    try:
        plans = _plans(st, os.environ)
        for parts in plans:
            _walk(parts, False)  # validate every existing ancestor before any mkdir
        for parts in plans:
            _walk(parts, True)
        return True
    except (OSError, ValueError, Unsafe):
        return False
