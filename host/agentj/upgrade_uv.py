"""Owner-private copy of the same uv 0.12.7 used by the official repair bootstrap."""
import hashlib
import http.client
import os
from pathlib import Path
import platform
import stat
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from . import update

# Archive hashes come from installation/bootstrap.sh.in. The extracted binary
# hashes also authenticate an offline reuse, including after a worker restart.
PINS = {
    'x86_64-unknown-linux-gnu': (
        '788f18abea7c5f55d6216e4f5613fd89d4d59b631efeec117b2b07fe72f1da21',
        '8264360c1b0a6f3df3d31620942216f57bba860179a2fc0916af1e5cf87c0d52'),
    'aarch64-unknown-linux-gnu': (
        '66393193038dd7eb108abd7a218d9cec04ac70ab98242b0720fa94de19223b7c',
        'a42eb41171e452acb03eb03e4901484ca55c991cf178cb5907939dd80c2d9ac8'),
    'x86_64-apple-darwin': (
        '06b8ae1da8c2661c5434507a66f8c2b0b835933bf955b5958a9ac357a37d1959',
        '5563d51cb726dc2f17764b299ba2a665348460e61e831810b56140ebbd75e3c9'),
    'aarch64-apple-darwin': (
        '127ebdda7ad953cdf198e964b570ea5771b85467ea93eb7cb6d6f8e6f55408f3',
        '55936a60bff5de7ac04facb72f9e6fd0ffd9661063480224c3ab321c3f10caff'),
}


def target_platform():
    arch = {'arm64':'aarch64', 'AMD64':'x86_64'}.get(platform.machine(), platform.machine())
    suffix = {'Linux':'unknown-linux-gnu', 'Darwin':'apple-darwin'}.get(platform.system())
    return f'{arch}-{suffix}'


def verified_file(file, expected):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == expected


def fetch_archive(filename, archive, retry):
    from .upgrade_wheels import PreparationFailure, download_failure
    urls = (f'https://agentj.app/dl/{filename}',
            f'https://github.com/astral-sh/uv/releases/download/0.12.7/{filename}')
    attempts = 0
    def fetch(timeout):
        nonlocal attempts
        request = urllib.request.Request(urls[attempts % len(urls)], headers={'User-Agent': 'agentj-upgrade/1'})
        attempts += 1
        try:
            with update.open_download(request, timeout=timeout) as response:
                data = response.read(32_000_001)
        except (OSError, http.client.HTTPException) as exc:
            raise download_failure(exc) from None
        if len(data) > 32_000_000:
            raise PreparationFailure('install_artifact_changed', 'download')
        archive.write_bytes(data)
    retry.call(fetch, 90, phase='download', step='runtime')


def runtime(pin, retry=None):
    """Use an authenticated cached executable, or fetch the pinned archive for a live repair."""
    from . import auto_update
    from .upgrade_wheels import PreparationFailure, private_dir
    auto_update.check_artifact(pin)
    target = target_platform()
    if target not in PINS:
        raise PreparationFailure('installer_incompatible', 'resolve')
    archive_hash, binary_hash = PINS[target]
    folder = pin.path.parent / 'upgrade-runtime'
    if retry is not None:
        folder.mkdir(mode=0o700, exist_ok=True)
    private_dir(folder)
    binary = folder / ('uv-' + binary_hash)
    try:
        info = binary.lstat()
    except FileNotFoundError:
        if retry is None:
            raise
    else:
        if (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o500 and verified_file(binary, binary_hash)):
            return str(binary)
        raise PreparationFailure('install_artifact_changed', 'resolve')
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        output = Path(tmp) / 'uv'
        # The terminal bootstrap leaves its validated uv here, outside service PATH.
        # Authenticate our own copy before executing it, just as for downloaded bytes.
        seed = Path.home() / '.local/share/agentj/upgrade-bin/uv'
        try:
            fd = os.open(seed, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            pass
        else:
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 128_000_000:
                    with output.open('wb') as copy:
                        shutil.copyfileobj(stream, copy, length=1024 * 1024)
                    if verified_file(output, binary_hash):
                        output.chmod(0o500)
                        os.replace(output, binary)
                        return str(binary)
        archive = Path(tmp) / 'uv.tar.gz'
        filename = f'uv-{target}.tar.gz'
        fetch_archive(filename, archive, retry)
        if not verified_file(archive, archive_hash):
            raise PreparationFailure('install_artifact_changed', 'download')
        with tarfile.open(archive, 'r:gz') as tar:
            member = tar.getmember(f'uv-{target}/uv')
            if not member.isfile() or not 0 < member.size <= 128_000_000:
                raise PreparationFailure('install_artifact_changed', 'download')
            with tar.extractfile(member) as stream:
                output.write_bytes(stream.read(member.size))
        if not verified_file(output, binary_hash):
            raise PreparationFailure('install_artifact_changed', 'download')
        output.chmod(0o500)
        os.replace(output, binary)
    return str(binary)


# Matches the exact CPython used by support/installation/bootstrap.sh.in.
PYTHON_VERSION = '3.12.13'


def repair_python(pin, uv, retry=None):
    """Provision the official installer's CPython privately. Rollback only looks it up offline."""
    from . import auto_update
    from .upgrade_wheels import PreparationFailure, clean_env, private_dir, resolver_failure, temp_env
    auto_update.check_artifact(pin)
    folder = pin.path.parent / 'upgrade-runtime'
    private_dir(folder)
    managed = folder / 'python'
    if retry is not None:
        managed.mkdir(mode=0o700, exist_ok=True)
    private_dir(managed)
    env = {**clean_env(), **temp_env(folder), 'UV_PYTHON_INSTALL_DIR': str(managed)}
    env.pop('VIRTUAL_ENV', None)
    request = 'cpython@' + PYTHON_VERSION

    def run(args, timeout):
        # No inherited uv mirrors, project settings, default cache or global Python/tool entrypoints.
        with tempfile.TemporaryFile(dir=folder) as stderr:
            try:
                proc = subprocess.run([uv, 'python', *args, '--no-config', '--no-cache', request], cwd=managed,
                                      env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=stderr, timeout=timeout)
            except subprocess.TimeoutExpired:
                # Only the download of the interpreter can be slow; the lookup that follows is local.
                failure = PreparationFailure('installer_timeout', 'resolve', transient=args[0] == 'install')
                failure.timed_out = True
                raise failure from None
            if proc.returncode:
                stderr.seek(max(0, stderr.tell() - 65536))
                raise resolver_failure(stderr.read())
            return proc.stdout

    if retry is not None:
        # This known uv embeds the distribution URL and SHA-256 for its supported Python builds.
        retry.call(lambda t: run(['install', '--no-bin'], t), 150, step='python')
    discover = lambda t: run(['find', '--system', '--no-project', '--offline', '--managed-python'], t)
    data = retry.call(discover, 15, step='local') if retry is not None else discover(15)
    py = Path(os.fsdecode(data.rstrip(b'\r\n'))).resolve()
    if not py.is_relative_to(managed) or not py.is_file() or not os.access(py, os.X_OK):
        raise PreparationFailure('python_missing', 'resolve')
    return str(py)
