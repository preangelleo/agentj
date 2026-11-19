"""Prepare an authenticated, interpreter-specific dependency directory before replacing a uv tool."""
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

CDN = re.compile(r'https://files\.pythonhosted\.org/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/([A-Za-z0-9_.+-]+\.whl)')
LOCK = 'agentj/upgrade.lock'


def lock_for(path):
    """Only called on a pinned, verified wheel. Older packages use the running release's lock."""
    if not zipfile.is_zipfile(path):
        return None  # uv reports malformed distributions; they have no dependency set to prepare
    with zipfile.ZipFile(path) as z:
        if LOCK in z.namelist():
            item = z.getinfo(LOCK)
            if item.file_size > 2_000_000:
                raise ValueError('install_artifact_changed')
            data = z.read(item)
            if hashlib.sha256(data).hexdigest() != json.loads(z.read('agentj/repair-sources.json'))['host_lock_sha256']:
                raise ValueError('install_artifact_changed')
            return data
        if not any(member.endswith('.dist-info/METADATA') for member in z.namelist()):
            return None
    bundled = Path(__file__).with_name('upgrade.lock')
    if bundled.is_file():
        return bundled.read_bytes()
    return None  # older development wheels have no vendored dependency contract


def private_dir(path):
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise ValueError('install_artifact_changed')


def catalog(data):
    result = {}
    for package in tomllib.loads(data.decode())['package']:
        for whl in package.get('wheels', []):
            match = CDN.fullmatch(whl['url'])
            if not match or not re.fullmatch(r'sha256:[0-9a-f]{64}', whl['hash']) or not 0 < whl['size'] <= 100_000_000:
                raise ValueError('install_artifact_changed')
            result[match[1]] = whl
    return result


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith(('UV_', 'PIP_'))}


def selected_wheels(uv, wheel, entries, scratch):
    """uv evaluates the real wheel's markers, requires-python, native tags and transitive requirements."""
    index = scratch / 'files.html'
    index.write_text('<html><body>' + '\n'.join(
        f'<a href="{row["url"]}#sha256={row["hash"][7:]}">{name}</a>' for name, row in entries.items()
    ) + '</body></html>')
    requirement = scratch / 'requirements.in'
    requirement.write_text('agentj @ ' + wheel.as_uri() + '\n')
    result = scratch / 'pylock.toml'
    py = os.path.realpath(getattr(sys, '_base_executable', sys.executable))
    args = [uv, 'pip', 'compile', '--no-config', '--no-index', '--no-build', '--no-python-downloads',
            '--no-cache', '--python', py, '--find-links', str(index), '--format', 'pylock.toml', '-o', str(result), str(requirement)]
    proc = subprocess.run(args, cwd=scratch, env=clean_env(), stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=150)
    if proc.returncode:
        raise ValueError('dependency_download_failed')
    names = set()
    for package in tomllib.loads(result.read_text())['packages']:
        if package['name'] == 'agentj':
            continue
        wheels = package.get('wheels', [])
        if not wheels:
            raise ValueError('install_artifact_changed')
        for w in wheels:
            match = CDN.fullmatch(w.get('url', ''))
            if not match or match[1] not in entries or w['hashes'].get('sha256') != entries[match[1]]['hash'][7:]:
                raise ValueError('install_artifact_changed')
            # The platform resolver may retain multiple compatible glibc/ABI
            # tags in pylock. Leave their final ranking to the installer itself.
            names.add(match[1])
    return names


def verify_local(folder, entries):
    private_dir(folder)
    for wheel in folder.iterdir():
        row = entries.get(wheel.name)
        s = wheel.lstat()
        if (not row or not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o022
                or s.st_size != row['size'] or hashlib.sha256(wheel.read_bytes()).hexdigest() != row['hash'][7:]):
            raise ValueError('install_artifact_changed')


def wheelhouse(pin, data, uv, *, prepare):
    from . import auto_update
    auto_update.check_artifact(pin)
    entries = catalog(data)
    # Stable across manager restarts. The private store already authenticated by
    # check_artifact is outside the normal uv cache and invisible inside the Agent fence.
    with zipfile.ZipFile(pin.path) as whl:
        meta, = [p for p in whl.namelist() if p.endswith('.dist-info/METADATA')]
        requirements = sorted(BytesParser().parsebytes(whl.read(meta)).get_all('Requires-Dist', []))
    context = [requirements, platform.platform(), sys.version, sys.implementation.cache_tag]
    key = hashlib.sha256(json.dumps(context).encode() + data).hexdigest()
    store = pin.path.parent / 'dependencies'
    store.mkdir(mode=0o700, exist_ok=True)
    private_dir(store)
    target = store / key
    if not target.exists():
        if not prepare:
            raise ValueError('install_artifact_unavailable')
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=store) as tmp:
            scratch = Path(tmp)
            staged = scratch / 'wheels'
            staged.mkdir(mode=0o700)
            for name in sorted(selected_wheels(uv, pin.path, entries, scratch)):
                row = entries[name]
                body = auto_update.download(row['url'], row['size'])
                if len(body) != row['size'] or hashlib.sha256(body).hexdigest() != row['hash'][7:]:
                    raise ValueError('install_artifact_changed')
                (staged / name).write_bytes(body)
            try:
                staged.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
    verify_local(target, entries)
    # A missing file has no hash to recheck. Resolve again from these exact
    # local bytes before installation, including a reused rollback directory.
    with tempfile.TemporaryDirectory(prefix='.check-', dir=store) as scratch:
        py = os.path.realpath(getattr(sys, '_base_executable', sys.executable))
        probe = [uv, 'pip', 'install', '--dry-run', '--no-cache', '--no-index', '--offline',
                 '--no-config', '--no-build', '--no-python-downloads', '--python', py,
                 '--find-links', str(target), '--target', str(Path(scratch) / 'probe'), str(pin.path)]
        if subprocess.run(probe, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          env=clean_env(), timeout=60).returncode:
            raise ValueError('install_artifact_unavailable')
    return target


def offline_command(cmd, house):
    from .auto_update import Command
    base = os.path.realpath(getattr(sys, '_base_executable', sys.executable))
    args = list(cmd)
    if '--python' not in args:
        args.extend(['--python', base])
    env = dict(cmd.env)
    # uv may keep launchers outside ~/.local/bin. Retain the running tool's
    # entrypoint and the interpreter even in a service with a different PATH.
    tool = Path(env['UV_TOOL_DIR']) / 'agentj' / 'uv-receipt.toml'
    if tool.is_file():
        receipt = tomllib.loads(tool.read_text())['tool']
        for item in receipt.get('entrypoints', []):
            if item.get('name') == 'agentj' and Path(item['install-path']).is_absolute():
                env['UV_TOOL_BIN_DIR'] = str(Path(item['install-path']).parent)
                break
    if 'UV_TOOL_BIN_DIR' not in env and os.environ.get('UV_TOOL_BIN_DIR'):
        env['UV_TOOL_BIN_DIR'] = os.environ['UV_TOOL_BIN_DIR']
    arg = Command([*args, '--offline', '--no-index', '--no-config', '--no-cache', '--no-build',
                   '--no-python-downloads', '--find-links', str(house)], env)
    arg.offline = True
    return arg


def for_rollback(cmd, pin):
    if 'tool' not in cmd or 'install' not in cmd:
        return cmd
    data = lock_for(pin.path)
    if data is None:
        return cmd
    house = wheelhouse(pin, data, cmd[0], prepare=False)
    return offline_command(cmd, house)


def prepare_update(cmd, pin, previous_version):
    """Both dependency sets must exist before the first change to the live tool."""
    if 'tool' not in cmd or 'install' not in cmd:
        return cmd
    data = lock_for(pin.path)
    if data is None:
        return cmd
    from . import auto_update
    previous = auto_update.stored_artifact(pin.root, previous_version)
    if previous is None:
        from .state import State
        # An unsigned rollback is accepted only when reconstructed from the
        # running tool's own RECORD. Never bless arbitrary loose-cache bytes.
        st = State(pin.root)
        blob = auto_update.signed_cached_bytes(st, previous_version)
        if blob is None:
            blob = auto_update.installed_wheel_bytes(previous_version)
            previous = auto_update.pin_artifact(blob, previous_version, pin.root)
        else:
            previous = auto_update.pin_artifact(blob, previous_version, pin.root, signed=True)
    wheelhouse(previous, lock_for(previous.path) or data, cmd[0], prepare=True)
    house = wheelhouse(pin, data, cmd[0], prepare=True)
    return offline_command(cmd, house)
