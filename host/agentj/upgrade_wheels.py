"""Prepare an authenticated, interpreter-specific dependency directory before replacing a uv tool."""
import contextlib
import errno
import hashlib
import http.client
import json
import os
import platform
import re
import shlex
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
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
                raise integrity_failure('dependency_lock', 'size_mismatch')
            data = z.read(item)
            try:
                expected = json.loads(z.read('agentj/repair-sources.json'))['host_lock_sha256']
            except (KeyError, TypeError, ValueError):
                raise integrity_failure('dependency_lock', 'invalid_manifest') from None
            if hashlib.sha256(data).hexdigest() != expected:
                raise integrity_failure('dependency_lock', 'hash_mismatch')
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
    try:
        for package in tomllib.loads(data.decode())['package']:
            for whl in package.get('wheels', []):
                match = CDN.fullmatch(whl['url'])
                if (not match or not re.fullmatch(r'sha256:[0-9a-f]{64}', whl['hash'])
                        or not 0 < whl['size'] <= 100_000_000):
                    raise integrity_failure('dependency_lock', 'invalid_manifest')
                result[match[1]] = whl
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise integrity_failure('dependency_lock', 'invalid_manifest') from None
    return result


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith(('UV_', 'PIP_'))}


def temp_env(scratch):
    # uv --no-cache still creates a temporary cache. The host's inherited macOS
    # TMPDIR can point at a deleted folder or a regular file.
    return {key: str(scratch) for key in ('TMPDIR', 'TEMP', 'TMP')}


class PreparationFailure(ValueError):
    """Only fixed, public diagnostic words cross the preparation boundary."""
    def __init__(self, reason, phase, symptom='other', transient=False, *, detail=None):
        super().__init__(reason)
        self.fields = {'dependency_phase': phase, 'uv_failure_class': symptom}
        if detail:
            self.fields['uv_resolve_detail'] = detail
        self.transient = transient


def integrity_failure(stage, subreason, phase='resolve'):
    failure = PreparationFailure('install_artifact_changed', phase)
    failure.fields.update(artifact_stage=stage, subreason=subreason)
    return failure


def invalid_preparation(detail):
    return PreparationFailure('dependency_preparation_failed', 'resolve', detail=detail)


def local_failure(exc):
    from . import upgrade_diag
    if isinstance(exc, ValueError) and str(exc) in ('install_artifact_changed', 'install_artifact_unavailable'):
        return str(exc)
    if getattr(exc, 'errno', None) in (errno.ENOSPC, errno.EDQUOT):
        return 'disk_full'
    if isinstance(exc, OSError) and not isinstance(exc, (FileNotFoundError, PermissionError)):
        return 'read_only_filesystem' if exc.errno == errno.EROFS else 'os_error'
    if isinstance(exc, (OSError, subprocess.SubprocessError)):
        return upgrade_diag.installer_reason(exc)
    return 'dependency_preparation_failed'


def local_preparation_failure(error):
    from . import upgrade_diag
    reason = local_failure(error)
    detail = 'local_prepare_error' if reason == 'dependency_preparation_failed' else None
    failure = PreparationFailure(reason, 'resolve', detail=detail)
    failure.fields.update(upgrade_diag.artifact_exception_fields(error))
    return failure


def resolver_failure(text):
    from . import upgrade_diag
    failure = _resolver_failure(text)
    fields = upgrade_diag.dependency_evidence(text, {**clean_env(), 'UV_NO_CONFIG': '1'})
    failure.fields = {**fields, **failure.fields}
    if failure.fields['uv_failure_class'] == 'other':
        failure.fields['uv_resolve_detail'] = upgrade_diag.resolve_detail(text)
    return failure


def _resolver_failure(text):
    from . import upgrade_diag
    text = upgrade_diag.bound_evidence(text).lower()
    symptom = upgrade_diag.uv_failure_class(text)
    if 'hash mismatch' in text or 'hashes do not match' in text:
        return PreparationFailure('install_artifact_changed', 'resolve')
    status = re.search(
        r'(?:status(?: code)?(?: (?:client|server) error)?[ :=(]+|http[/ ](?:[0-9.]+ )?)([45]\d\d)\b'
        r'|\b([45]\d\d) (?:forbidden|not found|bad gateway|service unavailable|too many requests|gateway timeout)',
        text)
    if status:
        code = int(status.group(1) or status.group(2))
        return PreparationFailure('dependency_download_failed', 'resolve', 'http_status', code in (408, 429) or code >= 500)
    if upgrade_diag.resolve_detail(text) == 'uv_option':
        return PreparationFailure('installer_incompatible', 'resolve')
    if 'no matching distribution found' in text:
        return PreparationFailure('dependency_conflict', 'resolve')
    reason = upgrade_diag.installer_reason(stderr=text)
    if symptom != 'other' or reason == 'dependency_download_failed':
        transient = symptom in ('connect', 'timeout') or (symptom == 'dns' and 'nxdomain' not in text)
        return PreparationFailure('dependency_download_failed', 'resolve', symptom, transient)
    return PreparationFailure(reason, 'resolve')


def download_failure(error):
    if isinstance(error, urllib.error.HTTPError):
        return PreparationFailure('dependency_download_failed', 'download', 'http_status',
                                  error.code in (408, 429) or error.code >= 500)
    cause = getattr(error, 'reason', error)
    if isinstance(cause, socket.gaierror):
        return PreparationFailure('dependency_download_failed', 'download', 'dns', cause.errno == socket.EAI_AGAIN)
    if isinstance(cause, ssl.SSLError):
        return PreparationFailure('dependency_download_failed', 'download', 'tls')
    if isinstance(cause, TimeoutError):
        return PreparationFailure('dependency_download_failed', 'download', 'timeout', True)
    if (isinstance(cause, (ConnectionError, http.client.IncompleteRead))
            or getattr(cause, 'errno', None) in (errno.ENETUNREACH, errno.EHOSTUNREACH)):
        return PreparationFailure('dependency_download_failed', 'download', 'connect', True)
    return PreparationFailure('dependency_download_failed', 'download')


def download_dependency(row, timeout):
    """One HTTPS attempt; the shared preparation budget decides whether to retry."""
    from . import update
    request = urllib.request.Request(row['url'], headers={'User-Agent': 'agentj-upgrade/1'})
    try:
        with update.open_download(request, timeout=timeout) as response:
            body = response.read(row['size'] + 1)
            declared = getattr(response, 'headers', {}).get('Content-Length')
    except (OSError, http.client.HTTPException) as error:
        failure = download_failure(error)
        from . import upgrade_diag
        name = row['url'].rsplit('/', 1)[-1].split('-', 1)[0]
        failure.fields.update(upgrade_diag.dependency_fields({'dependency_package': name, 'dependency_source': 'pypi'}))
        raise failure from None
    except ValueError:  # secure redirect policy refused the destination
        raise PreparationFailure('download_failed', 'download') from None
    # HTTP permits ASCII digits and an equivalent value can have leading zeros.
    expected_length = declared and declared.lstrip('0') == str(row['size'])
    if len(body) < row['size'] and (declared is None or expected_length):
        failure = PreparationFailure('dependency_download_failed', 'download', 'connect', True)
        failure.fields.update(artifact_stage='dependency_wheel', subreason='truncated')
        raise failure
    return body


class PreparationRetry:
    """Share the install retry allowance between both wheelhouses and the final install."""
    def __init__(self, clock=time.monotonic, sleep=time.sleep, lease_end=None):
        from . import auto_update
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.deadline = auto_update.retry_limit(self.started, lease_end)
        self.count = 0
        self.used = False
        self.isolated_temp = False
        self.progress = None    # (dependency files fetched, files needed) while a wheelhouse is being filled

    def stamp(self, failure, step=None, began=None):
        """Where the time went: closed words and whole seconds only (upgrade_diag.dependency_fields re-checks them)."""
        now = self.clock()
        if step:
            failure.fields.setdefault('prepare_step', step)
            failure.fields.setdefault('step_seconds', int(now - (now if began is None else began)))
        failure.fields.setdefault('prepare_seconds', int(now - self.started))
        if self.progress:
            failure.fields.setdefault('wheels_fetched', self.progress[0])
            failure.fields.setdefault('wheels_needed', self.progress[1])
        return failure

    def call(self, operation, timeout, *, phase='resolve', step=None):
        from . import auto_update
        self.used = True
        attempts = 0
        while True:
            left = int(self.deadline - self.clock())
            if left < 1:
                raise self.stamp(PreparationFailure('installer_timeout', phase), step)
            began = self.clock()
            try:
                attempts += 1
                return operation(min(left, timeout))
            except PreparationFailure as error:
                self.stamp(error, step, began)
                if phase == 'download':
                    error.fields['download_attempts'] = attempts
                if not error.transient or self.count >= auto_update.INSTALL_RETRIES:
                    raise
                if getattr(error, 'timed_out', False):
                    # A slow link gets longer next time, never more than what is left of the one shared budget.
                    timeout *= 2
                delay = auto_update.INSTALL_BACKOFF[self.count]
                if self.clock() + delay + auto_update.INSTALL_MIN_ATTEMPT > self.deadline:
                    raise
                self.sleep(delay)
                if self.deadline - self.clock() < auto_update.RETRY_FLOOR:
                    raise
                self.count += 1


def run_resolver(args, scratch, timeout, *, offline=False, isolated_temp=False, repair_index=False):
    from . import upgrade_diag
    with tempfile.TemporaryFile(dir=scratch) as errors:
        try:
            env = clean_env()
            if isolated_temp:
                env.update(temp_env(scratch))
            proc = subprocess.run(args, cwd=scratch, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=errors, timeout=timeout)
        except subprocess.TimeoutExpired:
            errors.seek(max(0, errors.tell() - upgrade_diag.EVIDENCE_MAX))
            failure = resolver_failure(errors.read())
            if str(failure) == 'uv_failed':
                # The installer was still working, silently, when its time ran out: a slow or stalling
                # link while it reads the dependency files. Nothing has been installed; it can be run again.
                source = failure.fields.get('dependency_source')
                failure = PreparationFailure('installer_timeout', 'resolve', transient=True)
                if source:
                    failure.fields['dependency_source'] = source
            failure.timed_out = True
            if offline:
                failure.transient = False
            raise failure from None
        except OSError as error:
            raise local_preparation_failure(error) from None
        if proc.returncode:
            errors.seek(max(0, errors.tell() - upgrade_diag.EVIDENCE_MAX))
            failure = resolver_failure(errors.read())
            if offline and not repair_index and str(failure) == 'dependency_conflict':
                # An expected wheel is missing from an already prepared directory.
                missing = PreparationFailure('install_artifact_unavailable', 'resolve')
                missing.fields.update(failure.fields, uv_resolve_detail='wheelhouse_incomplete')
                raise missing
            if offline:
                failure.transient = False
            raise failure


def base_python():
    return os.path.realpath(getattr(sys, '_base_executable', sys.executable))


def selected_wheels(uv, wheel, entries, scratch, *, timeout=150, python=None, isolated_temp=False):
    """uv evaluates the real wheel's markers, requires-python, native tags and transitive requirements."""
    index = scratch / 'files.html'
    index.write_text('<html><body>' + '\n'.join(
        f'<a href="{row["url"]}#sha256={row["hash"][7:]}">{name}</a>' for name, row in entries.items()
    ) + '</body></html>')
    requirement = scratch / 'requirements.in'
    requirement.write_text('agentj @ ' + wheel.as_uri() + '\n')
    result = scratch / 'pylock.toml'
    py = python or base_python()
    args = [uv, 'pip', 'compile', '--no-config', '--no-index', '--no-build', '--no-python-downloads',
            '--no-cache', '--python', py, '--find-links', str(index), '--format', 'pylock.toml', '-o', str(result), str(requirement)]
    run_resolver(args, scratch, timeout, isolated_temp=isolated_temp)
    try:
        packages = tomllib.loads(result.read_text())['packages']
        if not isinstance(packages, list):
            raise TypeError
        names = set()
        for package in packages:
            if package['name'] == 'agentj':
                continue
            for item in package['wheels']:
                match = CDN.fullmatch(item['url'])
                if not match or match[1] not in entries or item['hashes']['sha256'] != entries[match[1]]['hash'][7:]:
                    raise integrity_failure('dependency_resolver', 'selection_mismatch')
                # The platform resolver can retain multiple compatible tags.
                names.add(match[1])
            if not package['wheels']:
                raise TypeError
    except (FileNotFoundError, tomllib.TOMLDecodeError, UnicodeDecodeError, KeyError, TypeError):
        raise invalid_preparation('resolver_output_invalid') from None
    return names


def repair_names(pin, data, entries, scratch, python, retry):
    """Use the website's lock/marker/tag selection; uv still checks the real wheel offline."""
    manifest = json.loads(Path(__file__).with_name('repair-sources.json').read_text())['packaging']
    digest = manifest['sha256']
    helper = pin.path.parent / 'upgrade-runtime' / ('tags-' + digest + '.whl')
    private_dir(helper.parent)
    if not os.path.lexists(helper):
        row = {**manifest, 'hash': 'sha256:' + digest}
        body = retry.call(lambda timeout: download_dependency(row, timeout), 60, phase='download', step='fetch')
        if len(body) != row['size'] or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError('install_artifact_changed')
        staged = scratch / 'helper.whl'
        staged.write_bytes(body)
        staged.chmod(0o400)
        os.replace(staged, helper)
    info = helper.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or info.st_size != manifest['size'] or hashlib.sha256(helper.read_bytes()).hexdigest() != digest):
        raise ValueError('install_artifact_changed')
    lock = scratch / 'host.lock'
    lock.write_bytes(data)

    def select(timeout):
        try:
            proc = subprocess.run([python, '-IS', str(Path(__file__).with_name('upgrade_select.py')), str(helper), str(lock)],
                                  cwd=scratch, env={**clean_env(), **temp_env(scratch)}, stdin=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, stdout=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise PreparationFailure('installer_timeout', 'resolve') from None     # local work: nothing to wait out
        if proc.returncode == 3:
            error = PreparationFailure('dependency_conflict', 'resolve')
            error.fields['uv_resolve_detail'] = 'wheel_tags'
            raise error
        if proc.returncode:
            raise ValueError('install_artifact_changed')
        names = json.loads(proc.stdout)
        if not isinstance(names, list) or any(name not in entries for name in names):
            raise ValueError('install_artifact_changed')
        return names
    return retry.call(select, 60, step='local')


def permission_log(root):
    from .state import State
    st = State(root)
    def note(line):
        rebuilt = re.match(r"Rebuilding dependency cache entry .* \(([a-z_]{1,40})\)$", line)
        if st.exists() and rebuilt:
            st.log('upgrade_cache_rebuilt', status=rebuilt[1])
        elif st.exists():
            match = re.match(r"Removed group write from (\d+) entries under ", line)
            st.log('upgrade_permissions_hardened', count=int(match[1]) if match else 1)
    return note


def permission_failure(error, stage):
    failure = integrity_failure(stage, 'unsafe_store')
    failure.fields.update(permission_paths=error.paths, fix=error.fix)
    return failure


def _own_file(info):
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()


def cache_failure(folder, subreason, paths, fix=None):
    """A wheelhouse is a rebuildable cache: the one repair is to remove that directory."""
    failure = integrity_failure('dependency_cache', subreason)
    failure.fields.update(permission_paths=[str(path) for path in paths],
                          fix=fix or 'rm -rf -- ' + shlex.quote(str(folder)))
    return failure


def verify_local(folder, entries, *, note=None):
    from .install_permissions import UnsafeInstallation, secure_paths
    check_cache_dir(folder)
    for wheel in folder.iterdir():
        row = entries.get(wheel.name)
        info = wheel.lstat()
        if not row or not _own_file(info):
            raise cache_failure(folder, 'cache_entry_invalid', [wheel])
        if info.st_mode & 0o022:
            try:
                secure_paths([(wheel, True)], note=note)
            except UnsafeInstallation as error:
                own = error.paths == [str(wheel)]
                failure = cache_failure(folder, 'unsafe_store', error.paths, None if own else error.fix)
                # Group write alone, without an ACL, on this account's own file: an old
                # updater's umask. Nothing else about the entry is in doubt.
                failure.stale = (own and not info.st_mode & 0o002
                                 and error.fix.startswith('chmod go-w -- '))
                raise failure from None
        if info.st_size != row['size']:
            raise cache_failure(folder, 'size_mismatch', [wheel])       # changed bytes stop the upgrade (test_r0018);
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != row['hash'][7:]:
            raise cache_failure(folder, 'hash_mismatch', [wheel])       # the owner gets the one repair line


def discard_entry(store, name):
    """Remove one generated wheelhouse so preparation rebuilds it from verified bytes.

    Only this account's regular files inside a private directory of the private store,
    reached by no-follow descriptors. Anything else is left exactly as found.
    """
    from . import auto_update
    parent = os.open(store, auto_update._NOFOLLOW_DIR)
    try:
        fd = os.open(name, auto_update._NOFOLLOW_DIR, dir_fd=parent)
        try:
            if not auto_update._private_dir(parent) or not auto_update._private_dir(fd):
                raise integrity_failure('dependency_cache', 'unsafe_store')
            names = os.listdir(fd)
            for item in names:
                if not _own_file(os.stat(item, dir_fd=fd, follow_symlinks=False)):
                    raise cache_failure(Path(store) / name, 'cache_entry_invalid', [Path(store) / name / item])
            for item in names:
                os.unlink(item, dir_fd=fd)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent)
    finally:
        os.close(parent)


def usable_entry(folder, entries, *, note, rebuild):
    """Verify an existing wheelhouse; during preparation drop one that only an old umask spoiled and rebuild it once."""
    try:
        verify_local(folder, entries, note=note)
    except PreparationFailure as failure:
        if not rebuild or not getattr(failure, 'stale', False):
            raise
        discard_entry(folder.parent, folder.name)
        note(f"Rebuilding dependency cache entry {str(folder)!r} ({failure.fields.get('subreason')})")
        return False
    return True


def check_cache_dir(path, *, harden=False):
    from . import auto_update
    if os.getuid() != os.geteuid():
        raise integrity_failure('dependency_cache', 'unsafe_store')
    parent = None
    try:
        if harden:
            parent = os.open(path.parent, auto_update._NOFOLLOW_DIR)
        fd = os.open(path.name if parent is not None else path, auto_update._NOFOLLOW_DIR, dir_fd=parent)
        try:
            if not auto_update._private_dir(fd, harden_under=parent):
                raise integrity_failure('dependency_cache', 'unsafe_store')
        finally:
            os.close(fd)
    except OSError as error:
        if error.errno in (errno.ENOTDIR, errno.ELOOP):
            raise integrity_failure('dependency_cache', 'unsafe_store') from None
        raise
    finally:
        if parent is not None:
            os.close(parent)


def wheelhouse(pin, data, uv, *, prepare, retry=None, python=None, repair_index=False):
    from . import auto_update
    auto_update.check_artifact(pin)
    entries = catalog(data)
    # Stable across manager restarts. The private store already authenticated by
    # check_artifact is outside the normal uv cache and invisible inside the Agent fence.
    with zipfile.ZipFile(pin.path) as whl:
        meta, = [p for p in whl.namelist() if p.endswith('.dist-info/METADATA')]
        requirements = sorted(BytesParser().parsebytes(whl.read(meta)).get_all('Requires-Dist', []))
    context = [requirements, platform.platform(), sys.version, sys.implementation.cache_tag]
    ordinary_key = hashlib.sha256(json.dumps(context).encode() + data).hexdigest()
    if python:
        from . import upgrade_uv
        # The emergency interpreter is independent of the live host, including after a restart.
        context = [requirements, platform.system(), platform.machine(), upgrade_uv.PYTHON_VERSION]
    if repair_index:
        context.append("repair-index-v1")
    key = hashlib.sha256(json.dumps(context).encode() + data).hexdigest()
    store = pin.path.parent / 'dependencies'
    store.mkdir(mode=0o700, exist_ok=True)
    check_cache_dir(store, harden=prepare)
    note = permission_log(pin.root)
    ordinary = store / ordinary_key
    if repair_index and os.path.lexists(ordinary):
        usable_entry(ordinary, entries, note=note, rebuild=prepare)
    target = store / key
    exists = os.path.lexists(target)
    if exists and prepare:
        # An entry left group-writable by an old updater's umask is rebuilt, never trusted or fatal.
        exists = usable_entry(target, entries, note=note, rebuild=True)
    if not exists and not prepare:
        raise ValueError('install_artifact_unavailable')
    if not exists or (repair_index and prepare):
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=store) as tmp:
            scratch = Path(tmp)
            staged = scratch / 'wheels'
            staged.mkdir(mode=0o700)
            retry = retry or PreparationRetry()
            try:
                if repair_index:
                    names = repair_names(pin, data, entries, scratch, python or base_python(), retry) if requirements else []
                else:
                    names = retry.call(lambda timeout: selected_wheels(uv, pin.path, entries, scratch, timeout=timeout,
                                                                        python=python, isolated_temp=retry.isolated_temp), 150,
                                       step='select')
                retry.progress = (0, len(names))
                for name in sorted(names):
                    row = entries[name]
                    retry.progress = (len(list(staged.iterdir())), len(names))
                    if exists and (target / name).is_file():
                        continue
                    if repair_index and (ordinary / name).is_file():
                        body = (ordinary / name).read_bytes()
                    else:
                        body = retry.call(lambda timeout: download_dependency(row, timeout), auto_update.DOWNLOAD_TIMEOUT,
                                          phase='download', step='fetch')
                    if len(body) != row['size']:
                        raise integrity_failure('dependency_wheel', 'size_mismatch', 'download')
                    if hashlib.sha256(body).hexdigest() != row['hash'][7:]:
                        raise integrity_failure('dependency_wheel', 'hash_mismatch', 'download')
                    fd = os.open(staged / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, 'wb') as file:
                        file.write(body)
                try:
                    staged.rename(target)
                except OSError:
                    if not target.is_dir():
                        raise
                    if repair_index:
                        # Never replace bytes a concurrent install may be reading. Publish only
                        # absent, fully verified files, then revalidate the entire directory.
                        private_dir(target)
                        for item in staged.iterdir():
                            try:
                                os.link(item, target / item.name)
                            except FileExistsError:
                                pass
            finally:
                retry.progress = None
    verify_local(target, entries, note=note)
    # A missing file has no hash to recheck. Resolve again from these exact
    # local bytes before installation, including a reused rollback directory.
    with tempfile.TemporaryDirectory(prefix='.check-', dir=store) as scratch:
        py = python or base_python()
        probe = [uv, 'pip', 'install', '--dry-run', '--no-cache', '--no-index', '--offline',
                 '--no-config', '--no-build', '--no-python-downloads', '--python', py,
                 '--find-links', str(target), '--target', str(Path(scratch) / 'probe'), str(pin.path)]
        operation = lambda timeout: run_resolver(probe, scratch, timeout, offline=True,
                                                 isolated_temp=not prepare or bool(retry and retry.isolated_temp),
                                                 repair_index=repair_index)
        if retry is None:
            operation(60)  # rollback stays offline, without retries
        else:
            retry.call(operation, 60, step='probe')
    return target


def secure_uv_installation(cmd, pin):
    from .install_permissions import UnsafeInstallation, resolve_trusted, secure_paths, secure_tree, upgrade_paths
    env = getattr(cmd, 'env', {})
    if 'UV_TOOL_DIR' not in env:
        return
    prefix = Path(env['UV_TOOL_DIR']) / 'agentj'
    receipt = prefix / 'uv-receipt.toml'
    if not receipt.is_file():
        return
    # Check the receipt before trusting a path from it, and pin the original
    # launcher even if the service's PATH differs from the owner's shell.
    note = permission_log(pin.root)
    try:
        prefix = resolve_trusted(prefix, note=note)
        secure_paths([(prefix / receipt.name, True),
                      (resolve_trusted(pin.root, note=note), True)], note=note)
        try:
            entries = tomllib.loads((prefix / receipt.name).read_text())['tool'].get('entrypoints', [])
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, KeyError, AttributeError):
            entries = []
        home = Path.home()
        configured = env.get('UV_TOOL_BIN_DIR') or os.environ.get('UV_TOOL_BIN_DIR')
        candidates = [Path(item['install-path']) for item in entries
                      if isinstance(item, dict) and item.get('name') == 'agentj'
                      and isinstance(item.get('install-path'), str)
                      and Path(item['install-path']).is_absolute()]
        if not candidates:
            directories = [configured] if configured else [*os.get_exec_path(), home / '.local/bin']
            for directory in directories:
                entry = Path(directory) / 'agentj'
                if entry.is_absolute() and entry.resolve() == prefix / 'bin/agentj':
                    candidates.append(entry)
        python = getattr(sys, "_base_executable", sys.executable)
        if candidates:
            for entry in dict.fromkeys(candidates):
                secure_paths(upgrade_paths(home, entry, prefix, python, note=note), note=note)
        else:
            secure_paths([(prefix / 'bin/agentj', True)], note=note)
        secure_tree(prefix, python, note=note)
        env['UV_TOOL_DIR'] = str(prefix.parent)
    except UnsafeInstallation as error:
        raise permission_failure(error, 'install_store') from None


def verified_tool_destinations(receipt, configured=None):
    """Pin canonical destinations for this exact existing uv environment."""
    uid = os.getuid()
    try:
        if os.geteuid() != uid:
            return None
        prefix = receipt.parent.resolve(strict=True)
        if prefix.name != 'agentj':
            return None
        receipt_info = (prefix / receipt.name).lstat()
        if not stat.S_ISREG(receipt_info.st_mode) or receipt_info.st_uid != uid or receipt_info.st_mode & 0o022:
            return None
        expected = prefix / 'bin/agentj'
        meta = expected.lstat()
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != uid or meta.st_mode & 0o022
                or not os.access(expected, os.X_OK)):
            return None
        candidates = (configured,) if configured else (*os.get_exec_path(), Path.home()/'.local/bin')
        verified = set()
        for value in candidates:
            candidate = Path(value)
            if not candidate.is_absolute():
                continue
            try:
                parent = candidate.resolve(strict=True)
                entry = parent / 'agentj'
                if entry.is_symlink() and entry.lstat().st_uid == uid and entry.resolve(strict=True) == expected:
                    for path in (parent, *parent.parents, *expected.parents):
                        info = path.stat()
                        sticky = path not in (parent, expected.parent) and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
                        if info.st_uid not in (0, uid) or info.st_mode & 0o022 and not sticky:
                            break
                    else:
                        if parent.stat().st_uid == uid:
                            verified.add(parent)
            except (OSError, ValueError, RuntimeError):
                continue
        if len(verified) == 1:
            return str(prefix.parent), str(verified.pop())
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def offline_command(cmd, house, python=None):
    from .auto_update import Command
    args = list(cmd)
    if '--python' not in args:
        args.extend(['--python', python or base_python()])
    elif python:
        args[args.index('--python') + 1] = python
    private_dir(house.parent)
    env = {**cmd.env, **temp_env(house.parent)}
    # uv may keep launchers outside ~/.local/bin. Retain the running tool's
    # entrypoint and the interpreter even in a service with a different PATH.
    tool = Path(env['UV_TOOL_DIR']) / 'agentj' / 'uv-receipt.toml'
    if tool.is_file():
        try:
            receipt = tomllib.loads(tool.read_text())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            destinations = verified_tool_destinations(tool, env.get('UV_TOOL_BIN_DIR') or os.environ.get('UV_TOOL_BIN_DIR'))
            if not destinations:
                raise invalid_preparation('tool_receipt_invalid') from None
            env['UV_TOOL_DIR'], env['UV_TOOL_BIN_DIR'] = destinations
        else:
            try:
                for item in receipt['tool'].get('entrypoints', []):
                    if item.get('name') == 'agentj' and Path(item['install-path']).is_absolute():
                        env['UV_TOOL_BIN_DIR'] = str(Path(item['install-path']).parent)
                        break
            except (KeyError, TypeError, AttributeError):
                raise invalid_preparation('tool_receipt_invalid') from None
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
    cmd = cached_runtime(cmd, pin)
    # Probe only completed private wheelhouses. No network or new runtime on rollback.
    for repair_index in (False, True):
        try:
            house = wheelhouse(pin, data, cmd[0], prepare=False, repair_index=repair_index)
            return offline_command(cmd, house)
        except ValueError as error:
            if str(error) not in ('install_artifact_unavailable', 'python_incompatible', 'python_missing', 'uv_failed'):
                raise
            if repair_index and not (pin.path.parent / 'upgrade-runtime' / 'python').is_dir():
                raise
    from . import upgrade_uv
    py = upgrade_uv.repair_python(pin, upgrade_uv.runtime(pin))
    house = wheelhouse(pin, data, cmd[0], prepare=False, python=py, repair_index=True)
    return offline_command(cmd, house, python=py)


def with_runtime(cmd, binary):
    from .auto_update import Command
    return Command([binary, *cmd[1:]], cmd.env)


def cached_runtime(cmd, pin):
    from . import upgrade_uv
    if (pin.path.parent / 'upgrade-runtime').exists():
        try:
            return with_runtime(cmd, upgrade_uv.runtime(pin))
        except FileNotFoundError:
            pass  # an interrupted download did not replace the current runtime
    return cmd


_UMASK_LOCK = threading.Lock()
_umask_holders, _umask_before = 0, None


@contextlib.contextmanager
def private_umask():
    """umask 077 for the whole process while any preparation runs. The umask is per process, and the rescue watchdog
    prepares on worker threads (asyncio.to_thread → update.repair_install): holders are counted, so overlapping
    preparations cannot restore each other's 077 and leave the process there — the value from before the first holder
    returns when the last one leaves. Other threads create more private files meanwhile, never looser ones."""
    global _umask_holders, _umask_before
    with _UMASK_LOCK:
        if not _umask_holders:
            _umask_before = os.umask(0o077)
        _umask_holders += 1
    try:
        yield
    finally:
        with _UMASK_LOCK:
            _umask_holders -= 1
            if not _umask_holders:
                os.umask(_umask_before)


def prepare_update(cmd, pin, previous_version, *, retry=None):
    # 0.30.47-0.30.65 wrote the cache with the caller's umask (0002 on Ubuntu) and then refused its own files.
    # Every mode below is explicit; this keeps a future plain write from depending on the caller again.
    with private_umask():
        return _prepare_checked(cmd, pin, previous_version, retry=retry)


def _prepare_checked(cmd, pin, previous_version, *, retry=None):
    from . import upgrade_uv
    retry = retry or PreparationRetry()
    repairing = False
    original_detail = None
    try:
        if 'tool' not in cmd or 'install' not in cmd:
            return cmd
        secure_uv_installation(cmd, pin)
        cmd = cached_runtime(cmd, pin)
        try:
            return _prepare_update(cmd, pin, previous_version, retry=retry)
        except PreparationFailure as error:
            from . import auto_update
            detail = error.fields.get('uv_resolve_detail')
            python_issue = detail in ('interpreter_missing', 'requires_python', 'wheel_tags')
            old_uv = str(error) == 'installer_incompatible'
            repairable = (str(error) in ('installer_incompatible', 'installer_missing', 'uv_failed',
                                          'python_incompatible', 'python_missing', 'dependency_conflict')
                          or detail == 'resolver_output_invalid'
                          or (str(error) == 'install_artifact_unavailable' and detail == 'wheelhouse_incomplete')
                          or (str(error) == 'permission_denied' and detail == 'temp_directory'))
            if (error.fields['dependency_phase'] != 'resolve' or not repairable
                    or retry.count >= auto_update.INSTALL_RETRIES
                    or retry.deadline - retry.clock() < auto_update.INSTALL_MIN_ATTEMPT):
                raise
        original_detail = detail
        repairing = old_uv
        retry.isolated_temp = True
        repaired = with_runtime(cmd, upgrade_uv.runtime(pin, retry))
        py = upgrade_uv.repair_python(pin, repaired[0], retry) if python_issue else None
        repairing = False
        if (retry.count >= auto_update.INSTALL_RETRIES
                or retry.deadline - retry.clock() < auto_update.RETRY_FLOOR):
            raise retry.stamp(PreparationFailure('installer_timeout', 'resolve'))
        retry.count += 1
        result = _prepare_update(repaired, pin, previous_version, retry=retry, python=py)
        if old_uv:
            result.uv_repaired = True
        return result
    except PreparationFailure as error:
        failure = error
    except (OSError, subprocess.SubprocessError, ValueError, zipfile.BadZipFile, KeyError, TypeError,
            AttributeError, IndexError) as error:
        failure = local_preparation_failure(error)
    if original_detail and (original_detail != 'unrecognized' or not failure.fields.get('uv_resolve_detail')):
        failure.fields['uv_resolve_detail'] = original_detail
    if repairing:
        from . import doctor, update
        from .state import State
        try:
            counts = update.doctor_counts(doctor.run_upgrade(State(pin.root)))
        except Exception:  # read-only doctor is best effort; keep the actual repair failure
            counts = None
        failure.fields['uv_repair_doctor'] = list(counts) if counts else []
    raise failure from None


def _prepare_update(cmd, pin, previous_version, *, retry=None, python=None):
    """Both dependency sets must exist before the first change to the live tool."""
    if 'tool' not in cmd or 'install' not in cmd:
        return cmd
    data = lock_for(pin.path)
    if data is None:
        return cmd
    from . import auto_update
    previous = pin if previous_version == pin.version else auto_update.stored_artifact(pin.root, previous_version)
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
    retry = retry or PreparationRetry()
    options = dict(prepare=True, retry=retry, python=python, repair_index=retry.isolated_temp or bool(python))
    if previous.path != pin.path:
        wheelhouse(previous, lock_for(previous.path) or data, cmd[0], **options)
    house = wheelhouse(pin, data, cmd[0], **options)
    return offline_command(cmd, house, python) if python else offline_command(cmd, house)
