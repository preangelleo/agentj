"""Native automation control. Fixed operations only; no shell, sudo, or agent entry point.

Public snapshots contain digests/identity, never arguments/environment. Exact source
bytes are retained only in the private recovery journal.
A journal is written before the first effect, so partial failures remain recoverable.
System changes require paired-owner UV. Root-owned Linux file sources additionally
use a one-plan Elevator capability and a fresh live Host permit in a fixed helper.
"""
from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import stat
import threading

from . import systasks

ACTION = 'native_scheduler_control'
FIELDS = ('platform', 'source', 'scope', 'id', 'snapshot', 'op')
_LOCK = threading.Lock()


class Refused(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(target):
    if not isinstance(target, dict) or set(target) != set(FIELDS):
        raise Refused('shape')
    if any(not isinstance(target[k], str) for k in FIELDS):
        raise Refused('shape')
    if target['op'] not in ('disable', 'restore') or target['scope'] not in ('user', 'system', 'all-users'):
        raise Refused('shape')
    if not isinstance(target['snapshot'], str) or not re.fullmatch('[0-9a-f]{64}', target['snapshot']):
        raise Refused('shape')
    ident = target['id']
    if not isinstance(ident, str) or len(ident) > 240:
        raise Refused('shape')
    source, platform, scope = target['source'], target['platform'], target['scope']
    if source == 'systemd' and platform.startswith('linux') and scope in ('user', 'system'):
        valid = re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*\.timer', ident)
    elif source == 'launchd' and platform == 'darwin' and scope in ('user', 'system'):
        valid = re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', ident)
    elif source == 'schtasks' and platform == 'win32' and scope == 'all-users':
        valid = re.fullmatch(r'\\[A-Za-z0-9_. -]+(?:\\[A-Za-z0-9_. -]+)*', ident)
    elif source == 'cron' and (platform.startswith('linux') or platform == 'darwin') and scope == 'user':
        if not re.fullmatch(r'crontab:[1-9][0-9]{0,5}', ident):
            raise Refused('read_only')
        valid = True
    elif source in ('cron-system', 'anacron') and platform.startswith('linux') and scope == 'system':
        valid = re.fullmatch(r'(?:crontab|anacrontab|cron\.d/[A-Za-z0-9_-]+):[1-9][0-9]{0,5}', ident) if ':' in ident else re.fullmatch(r'cron\.(?:hourly|daily|weekly|monthly)/[A-Za-z0-9_-]+', ident)
        if source == 'anacron' and not ident.startswith('anacrontab:'):
            valid = False
        if source == 'cron-system' and ident.startswith('anacrontab:'):
            valid = False
    else:
        raise Refused('read_only')
    if not valid or any(p in ('.', '..') for p in ident.replace('\\', '/').split('/')):
        raise Refused('shape')
    return target


def _file_identity(path, roots):
    p = Path(path)
    if '..' in p.parts or not p.is_absolute() or not any(p.is_relative_to(Path(root)) for root in roots):
        raise Refused('read_only')
    # No symlink components and no contents (unit Environment directives are private).
    if any(part.is_symlink() for part in (p, *p.parents)):
        raise Refused('read_only')
    st = p.stat()
    if not p.is_file():
        raise Refused('read_only')
    return [str(p), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def _crontab_run(argv, stdin=None, *, native_crontab=None):
    """Fixed current-user interface. Never accepts an editor, shell, user or job argv."""
    import subprocess
    if argv not in (['crontab', '-V'], ['crontab', '-l'], ['crontab', '-']):
        raise Refused('shape')
    if (argv == ['crontab', '-']) != isinstance(stdin, bytes):
        raise Refused('shape')
    if stdin is not None and len(stdin) > 262144:
        raise Refused('crontab_limits')
    import selectors
    import time
    process = None
    streams = []
    try:
        from .native_crontab_os import NativeCrontab, UnsafeBinary
        try:
            executable = (native_crontab or NativeCrontab(sys.platform)).executable()
        except (UnsafeBinary, OSError):
            raise Refused('backend_binary_untrusted') from None
        process = subprocess.Popen([executable, argv[1]], stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, errors = bytearray(), bytearray()
        pending = memoryview(stdin or b'')
        deadline = time.monotonic() + 5
        with selectors.DefaultSelector() as selector:
            for stream, buffer, limit in ((process.stdout, output, 262144), (process.stderr, errors, 8192)):
                streams.append(stream)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (buffer, limit))
            if process.stdin is not None:
                streams.append(process.stdin)
                if pending:
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE, None)
                else:
                    process.stdin.close()
            while selector.get_map():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise Refused('backend_timeout')
                for event, _ in selector.select(left):
                    stream = event.fileobj
                    if event.data is None:
                        size = os.write(stream.fileno(), pending[:65536])
                        if not size:
                            raise Refused('install_failed')
                        pending = pending[size:]
                        if not pending:
                            selector.unregister(stream)
                            stream.close()
                    else:
                        buffer, limit = event.data
                        chunk = os.read(stream.fileno(), min(65536, limit - len(buffer) + 1))
                        if not chunk:
                            selector.unregister(stream)
                        else:
                            buffer.extend(chunk)
                            if len(buffer) > limit:
                                raise Refused('crontab_limits')
        if process.wait(timeout=max(.001, deadline - time.monotonic())):
            raise Refused('backend_unavailable')
        return bytes(output)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise Refused('backend_unavailable') from None
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        for stream in streams:
            stream.close()


class Backend:
    def __init__(self, run=systasks._run, platform=None, home=None, uid=None, launchd_system_directory=None, system_directory="/etc", crontab_run=None, transaction_directory="/tmp", native_crontab=None):
        # Explicit Python-only injection preserves legacy runner fixtures. Product
        # execution always pins a trusted OS binary; configuration supplies no seam.
        self._native_crontab = native_crontab
        if crontab_run is None:
            from .native_crontab_os import NativeCrontab, UnsafeBinary
            if self._native_crontab is None:
                try:
                    self._native_crontab = NativeCrontab(platform or sys.platform)
                except UnsafeBinary:
                    pass
            self.crontab_run = self._native_crontab.run if self._native_crontab else _crontab_run
        else:
            self.crontab_run = crontab_run
        self.transaction_directory = Path(transaction_directory)
        self.system_directory = Path(system_directory)
        self.launchd_system_directory = launchd_system_directory or "/Library/LaunchDaemons"
        self.run = run
        self.platform = platform or sys.platform
        self.home = Path(home) if home else Path.home()
        self.uid = os.getuid() if uid is None and hasattr(os, 'getuid') else uid

    def query(self, argv):
        result = self.run(argv)
        if result is None:
            raise Refused('not_visible')
        return result

    def snapshot(self, target):
        validate(target)
        if target['platform'] != self.platform:
            raise Refused('platform_changed')
        source, scope, ident = (target[k] for k in ('source', 'scope', 'id'))
        base = {k: target[k] for k in ('platform', 'source', 'scope', 'id')}
        if source == 'cron':
            return self.crontab_snapshot(target)
        if source in ('cron-system', 'anacron'):
            return self.source_snapshot(target)
        if source == 'systemd':
            prefix = ['systemctl', *(['--user'] if scope == 'user' else [])]
            props = 'Id,LoadState,UnitFileState,ActiveState,FragmentPath,DropInPaths,TimersCalendar,TimersMonotonic,Unit,NeedDaemonReload'
            text = self.query(prefix + ['show', '--no-pager', '-p', props, '--', ident])
            meta = dict(line.split('=', 1) for line in text.splitlines() if '=' in line and line.split('=',1)[0] in props.split(','))
            if meta.get('Id') != ident or meta.get('LoadState') != 'loaded' or meta.get('NeedDaemonReload') == 'yes':
                raise Refused('changed')
            enabled = meta.pop('UnitFileState', '')
            active = meta.pop('ActiveState', '')
            if enabled not in ('enabled', 'disabled') or active not in ('active', 'inactive', 'failed'):
                raise Refused('read_only')
            roots = ['/etc/systemd', '/usr/lib/systemd', '/lib/systemd', '/run/systemd', str(self.home / '.config/systemd'), str(self.home / '.local/share/systemd')]
            meta['schedule'] = systasks._timer_when({k:[meta.pop(k, '')] for k in ('TimersCalendar','TimersMonotonic')})
            files = [meta.get('FragmentPath', '')] + meta.get('DropInPaths', '').split()
            identities = [_file_identity(p, roots) for p in files]
            # Bind the activated service file too; replacing the service invalidates the authorization.
            service = meta.get('Unit')
            if not service or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*\.service', service):
                raise Refused('read_only')
            service_text = self.query(prefix + ['show', '--no-pager', '-p', 'Id,FragmentPath,DropInPaths,NeedDaemonReload', '--', service])
            service_meta = dict(line.split('=',1) for line in service_text.splitlines() if '=' in line and line.split('=',1)[0] in ('Id','FragmentPath','DropInPaths','NeedDaemonReload'))
            if service_meta.get('Id') != service or service_meta.get('NeedDaemonReload') == 'yes':
                raise Refused('changed')
            identities += [_file_identity(p, roots) for p in [service_meta.get('FragmentPath','')] + service_meta.get('DropInPaths','').split()]
            base.update(config=digest([meta, service_meta, identities]), enabled=enabled == 'enabled', loaded=active == 'active')
        elif source == 'launchd':
            # launchctl list is scoped to its caller. Never pretend a user's list is the system domain.
            if scope == 'system' and self.uid != 0:
                raise Refused('read_only')
            directory = Path(self.launchd_system_directory) if scope == 'system' else self.home / 'Library/LaunchAgents'
            path = directory / (ident + '.plist')
            identity = _file_identity(path, [str(directory)])
            import plistlib
            with path.open('rb') as fh:
                pl = plistlib.load(fh)
            if pl.get('Label') != ident:
                raise Refused('changed')
            # Never include ProgramArguments, EnvironmentVariables, or arbitrary plist values in a snapshot.
            schedule = {k:pl.get(k) for k in ('StartCalendarInterval','StartInterval','Program')}
            domain = 'system' if scope == 'system' else 'gui/' + str(self.uid)
            text = self.query(['launchctl', 'print-disabled', domain])
            matches = re.findall(r'"'+re.escape(ident)+r'"\s*=>\s*(true|false)', text)
            enabled = matches[-1] != 'true' if matches else pl.get('Disabled') is not True
            loaded = ident in systasks._launchctl(self.query(['launchctl', 'list']))
            base.update(config=digest([identity, schedule]), enabled=enabled, loaded=loaded, path=str(path), domain=domain)
        else:
            # XML is queried only for this exact object. Only execution/scheduling metadata is retained.
            import xml.etree.ElementTree as ET
            text = self.query(['schtasks', '/query', '/tn', ident, '/xml'])
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                raise Refused('not_visible') from None
            ns = {'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
            enabled = root.findtext('t:Settings/t:Enabled', namespaces=ns)
            if enabled not in ('true', 'false'):
                raise Refused('read_only')
            # Hash the task definition in host memory; raw arguments never leave this method.
            setting = root.find('t:Settings/t:Enabled', ns)
            setting.text = ''
            base.update(config=hashlib.sha256(ET.tostring(root)).hexdigest(), enabled=enabled == 'true', loaded=False)
        base['snapshot'] = digest(base)
        return base

    def crontab_bytes(self):
        if self.platform not in ('linux', 'darwin'):
            raise Refused('backend_semantics_unknown')
        # Native OS contract uses common -l / '-' semantics without a -V probe.
        # Legacy injected runners retain their explicit Cronie fixture contract.
        if self._native_crontab is None or self.crontab_run != self._native_crontab.run:
            version = self.crontab_run(['crontab', '-V'], stdin=None)
            if not isinstance(version, bytes) or not re.fullmatch(rb'(?:cronie|crontab \(cronie\)) [0-9][0-9A-Za-z.+~-]*\n?', version):
                raise Refused('backend_semantics_unknown')
        raw = self.crontab_run(['crontab', '-l'], stdin=None)
        self.crontab_validate(raw)
        return raw

    @staticmethod
    def crontab_validate(raw):
        if not isinstance(raw, bytes) or len(raw) > 262144 or len(raw.splitlines()) > 10000:
            raise Refused('crontab_limits')
        text = raw.decode('utf-8', 'strict')
        if any(c in text for c in ('\x00', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029')) or '\r' in text.replace('\r\n', ''):
            raise Refused('unknown_line_format')
        if raw and not raw.endswith(b'\n'):
            raise Refused('unknown_format')
        for line in raw.splitlines(keepends=True):
            stripped = line.strip()
            if not stripped or stripped.startswith(b'#'):
                continue
            if stripped.endswith(b'\\'):
                raise Refused('unknown_format')
            if re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*\s*=.*', stripped):
                continue
            if not systasks.eligible_source_line(line, 'cron'):
                raise Refused('unknown_format')

    def crontab_metadata(self, target, raw):
        self.crontab_validate(raw)
        lines = raw.splitlines(keepends=True)
        number = int(target['id'].split(':')[1])
        if number > len(lines):
            raise Refused('changed')
        line = lines[number - 1]
        enabled = not line.startswith(b'# agentj-disabled:')
        if enabled and not systasks.eligible_source_line(line, 'cron'):
            raise Refused('unknown_format')
        base = {k: target[k] for k in ('platform', 'source', 'scope', 'id')}
        content = hashlib.sha256(raw).hexdigest()
        base.update(config=content, content=content, enabled=enabled, loaded=False)
        base['snapshot'] = digest(base)
        return base

    def crontab_snapshot(self, target):
        return self.crontab_metadata(target, self.crontab_bytes())

    @staticmethod
    def crontab_matches(record, snap):
        allowed = [record['original']['snapshot'], record['expected']['snapshot']]
        if record.get('pending') is not None:
            allowed.append(record.get('before', {}).get('snapshot'))
        return snap['snapshot'] in allowed

    def crontab_apply(self, controller, target, snap, journal, key, guard):
        record = journal.get(key)
        op = target['op']
        if op == 'disable' and record:
            raise Refused('changed')
        if op == 'restore' and not record:
            raise Refused('no_restore')
        if op == 'disable' and any(isinstance(v, dict) and v.get('original', {}).get('source') == 'cron' for v in journal.values()):
            raise Refused('source_has_pending_control')
        if record and not self.crontab_matches(record, snap):
            raise Refused('changed')
        if not record and not snap['enabled']:
            raise Refused('marker_spoof')
        raw = self.crontab_bytes()
        if self.crontab_metadata(target, raw) != snap:
            raise Refused('changed')
        original = record['original'] if record else snap
        backup = base64.b64decode(record['bytes'], validate=True) if record else raw
        if hashlib.sha256(backup).hexdigest() != original['content']:
            raise Refused('corrupt_original')
        if op == 'disable':
            lines = raw.splitlines(keepends=True)
            number = int(target['id'].split(':')[1])
            lines[number - 1] = b'# agentj-disabled:' + hashlib.sha256(raw + target['id'].encode()).hexdigest().encode() + b' ' + lines[number - 1]
            desired = b''.join(lines)
        else:
            desired = backup
        expected = self.crontab_metadata(target, desired)
        journal[key] = record or {'original': original, 'bytes': base64.b64encode(backup).decode()}
        journal[key].update(pending=op, expected=expected, before=snap)
        encoded = json.dumps(journal).encode()
        if len(encoded) > 512 * 1024:
            raise Refused('journal_capacity')
        guard()
        controller.st.write_private(controller.path, encoded)
        guard()
        # Immediate full fingerprint check under the shared user lock. An external
        # writer ignoring this lock can still race the following atomic OS install.
        if self.crontab_bytes() != raw:
            raise Refused('changed')
        guard()
        if desired != raw:
            result = self.crontab_run(['crontab', '-'], stdin=desired)
            if result != b'':
                raise Refused('install_failed')
        guard()
        after = self.crontab_bytes()
        if after != desired:
            raise Refused('verify_failed')
        guard()
        if op == 'restore':
            del journal[key]
        else:
            journal[key]['pending'] = None
        controller.st.write_private(controller.path, json.dumps(journal).encode())
        return {'ok': True, 'snapshot': expected['snapshot']}

    def source_path(self, target):
        relative = target['id'].split(':')[0]
        return self.system_directory / relative

    def source_open(self, target, disabled=False, *, readonly=False):
        """Pin every directory with no-follow descriptors; never follow path components."""
        validate(target)
        if target["source"] not in ("cron-system", "anacron") or target["platform"] != self.platform:
            raise Refused("read_only")
        path = self.source_path(target)
        if disabled:
            path = path.with_name('.agentj-disabled-' + path.name)
        if not path.is_absolute() or '..' in path.parts:
            raise Refused('unsafe_path')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                info = os.fstat(fd)
                if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                    raise Refused('unsafe_directory')
            if not readonly and not os.access('.', os.W_OK, dir_fd=fd, effective_ids=True):
                raise Refused('os_privilege_required')
            stream = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                info = os.fstat(stream)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144:
                    raise Refused('unsafe_file')
                # Preparation may inspect readable root-owned sources. This is not
                # permission to mutate: every installation still uses the default
                # writer path below, including its current-euid/parent checks.
                if readonly:
                    if info.st_uid not in (0, self.uid):
                        raise Refused('os_privilege_required')
                elif self.uid != os.geteuid() or info.st_uid != self.uid:
                    raise Refused('os_privilege_required')
                if info.st_mode & 0o7022:
                    raise Refused('unsafe_file_mode')
                if os.listxattr(stream):
                    raise Refused('extended_metadata_not_preservable')
                data = os.read(stream, 262145)
                if len(data) != info.st_size or self.source_stat(os.fstat(stream)) != self.source_stat(info):
                    raise Refused('changed')
                return fd, path, info, data
            finally:
                os.close(stream)
        except BaseException:
            os.close(fd)
            raise

    def source_state(self, target, *, readonly=False):
        script = ':' not in target['id']
        try:
            result = self.source_open(target, readonly=readonly)
        except FileNotFoundError:
            if not script:
                raise
            result = self.source_open(target, disabled=True, readonly=readonly)
        fd, path, info, data = result
        if script:
            other = path.with_name(path.name.removeprefix('.agentj-disabled-')) if path.name.startswith('.') else path.with_name('.agentj-disabled-' + path.name)
            try:
                os.stat(other.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError:
                os.close(fd)
                raise
            else:
                os.close(fd)
                raise Refused('marker_conflict')
        return result

    @staticmethod
    def source_stat(info):
        # Reading may update atime; it is neither scheduler identity nor an external edit.
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                info.st_mode, info.st_uid, info.st_gid, info.st_nlink)

    def source_snapshot(self, target, *, readonly=False):
        fd, path, info, data = self.source_state(target, readonly=readonly)
        try:
            return self.source_metadata(target, info, data, path.name.startswith('.'))
        finally:
            os.close(fd)

    def source_metadata(self, target, info, data, script_disabled=False):
        # Physical line identities must agree with the scanner's text splitlines.
        text = data.decode('utf-8', 'strict')
        if any(c in text for c in ('\x00', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029')) or '\r' in text.replace('\r\n', ''):
            raise Refused('unknown_line_format')
        base = {k: target[k] for k in ('platform', 'source', 'scope', 'id')}
        script = ':' not in target['id']
        if not script and any(line.rstrip(b'\r\n').endswith(b'\\') for line in data.splitlines(keepends=True)):
            raise Refused('unknown_format')
        enabled = not script_disabled
        if script:
            if not info.st_mode & 0o111:
                raise Refused('run_parts_not_executable')
        else:
            lines = data.splitlines(keepends=True)
            number = int(target['id'].rsplit(':', 1)[1])
            if number > len(lines):
                raise Refused('changed')
            line = lines[number - 1]
            enabled = not line.startswith(b'# agentj-disabled:')
            if enabled and not systasks.eligible_source_line(line, target['source']):
                raise Refused('unknown_format')
        identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                    stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
        base.update(content=hashlib.sha256(data).hexdigest(), witness=digest([hashlib.sha256(data).hexdigest(), identity[:4] + identity[5:], enabled]),
                    config=digest([hashlib.sha256(data).hexdigest(), identity]), enabled=enabled,
                    loaded=False)
        base['snapshot'] = digest(base)
        return base

    @staticmethod
    def source_matches(record, snap):
        return (snap['snapshot'] == record['original']['snapshot'] or
                snap['snapshot'] == record['expected']['snapshot'] or
                (record.get('pending') is not None and
                 (snap.get('witness') == record['expected'].get('witness') or
                  snap['snapshot'] == record.get('before', {}).get('snapshot'))))

    def source_apply(self, controller, target, snap, journal, key, guard):
        """Original bytes and planned bytes are local/private; never return them to a caller."""
        record = journal.get(key)
        op = target['op']
        if op == 'disable' and record:
            raise Refused('changed')
        if op == 'disable' and any(
                k != key and value.get('original', {}).get('source') in ('cron-system', 'anacron')
                and value['original']['id'].split(':')[0] == target['id'].split(':')[0]
                for k, value in journal.items() if isinstance(value, dict)):
            raise Refused('source_has_pending_control')
        if op == 'restore' and not record:
            raise Refused('no_restore')
        if record and not self.source_matches(record, snap):
            raise Refused('changed')
        if not record and not snap['enabled']:
            raise Refused('marker_spoof')
        fd, path, info, data = self.source_state(target)
        temporary = None
        try:
            if self.source_metadata(target, info, data, path.name.startswith('.')) != snap:
                raise Refused('changed')
            original = record['original'] if record else snap
            raw = base64.b64decode(record['bytes'], validate=True) if record else data
            if hashlib.sha256(raw).hexdigest() != original['content']:
                raise Refused('corrupt_original')
            if op == 'restore' and snap['snapshot'] == original['snapshot']:
                # A journal persisted before a failed first effect: already exactly original.
                guard()
                del journal[key]
                controller.st.write_private(controller.path, json.dumps(journal).encode())
                return {'ok': True, 'snapshot': snap['snapshot']}
            script = ':' not in target['id']
            if not script:
                if op == 'disable':
                    lines = data.splitlines(keepends=True)
                    number = int(target['id'].rsplit(':', 1)[1])
                    line = lines[number - 1]
                    marker = b'# agentj-disabled:' + hashlib.sha256(data + target['id'].encode()).hexdigest().encode() + b' '
                    lines[number - 1] = marker + line
                    desired = b''.join(lines)
                else:
                    desired = raw
                guard()
                temporary = '.agentj-stage-' + secrets.token_hex(16)
                out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              stat.S_IMODE(info.st_mode), dir_fd=fd)
                try:
                    made = os.fstat(out)
                    if (stat.S_IMODE(made.st_mode), made.st_uid, made.st_gid) != (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid):
                        raise Refused('metadata_not_preservable')
                    view = memoryview(desired)
                    while view:
                        guard()
                        size = os.write(out, view)
                        if not size:
                            raise Refused('io')
                        view = view[size:]
                    guard()
                    os.fsync(out)
                    expected = self.source_metadata(target, os.fstat(out), desired)
                finally:
                    os.close(out)
            else:
                expected = self.source_metadata(target, info, data, op == 'disable')
            journal[key] = record or {'original': original, 'bytes': base64.b64encode(raw).decode()}
            journal[key].update(pending=op, expected=expected, before=snap)
            encoded = json.dumps(journal).encode()
            if len(encoded) > 512 * 1024:
                raise Refused('journal_capacity')
            guard()
            controller.st.write_private(controller.path, encoded)
            guard()
            if self.snapshot(target)['snapshot'] != snap['snapshot']:
                raise Refused('changed')
            # Verify the pinned parent still denotes the selected path after all waits/writes.
            check_fd, _, check_info, check_data = self.source_state(target)
            try:
                if os.fstat(fd) != os.fstat(check_fd) or self.source_stat(check_info) != self.source_stat(info) or check_data != data:
                    raise Refused('changed')
            finally:
                os.close(check_fd)
            guard()
            if script:
                destination = path.name.removeprefix('.agentj-disabled-') if op == 'restore' else '.agentj-disabled-' + path.name
                self.rename_noreplace(fd, path.name, destination)
            else:
                os.replace(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
                temporary = None
            # Capture only the expected moved inode; never bless an external replacement.
            after = self.snapshot(target)
            if script:
                after_fd, _, after_info, after_data = self.source_state(target)
                os.close(after_fd)
                if (after_info.st_dev, after_info.st_ino, after_data) != (info.st_dev, info.st_ino, data):
                    raise Refused('changed')
            else:
                # rename may update ctime, but the staged inode and every byte must match.
                after_fd, _, after_info, after_data = self.source_state(target)
                os.close(after_fd)
                if (after_info.st_dev, after_info.st_ino, after_data) != (made.st_dev, made.st_ino, desired):
                    raise Refused('changed')
            if after['witness'] != expected['witness'] or after['enabled'] != (op == 'restore'):
                raise Refused('verify_failed')
            journal[key]['expected'] = after
            guard()
            controller.st.write_private(controller.path, json.dumps(journal).encode())
            guard()
            os.fsync(fd)
            guard()
            if op == 'restore':
                del journal[key]
            else:
                journal[key]['pending'] = None
            controller.st.write_private(controller.path, json.dumps(journal).encode())
            return {'ok': True, 'snapshot': after['snapshot']}
        finally:
            if temporary:
                os.unlink(temporary, dir_fd=fd)
            os.close(fd)

    @staticmethod
    def rename_noreplace(fd, source, destination):
        # Linux syscall: unlike rename(), this cannot overwrite an existing job.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise Refused('no_atomic_noreplace')
        if rename(fd, os.fsencode(source), fd, os.fsencode(destination), 1):
            raise OSError(ctypes.get_errno(), 'rename_noreplace')

    def commands(self, snap, op, original):
        source, ident, scope = (snap[k] for k in ('source','id','scope'))
        if source == 'systemd':
            prefix = ['systemctl', *(['--user'] if scope == 'user' else [])]
            if op == 'disable':
                return [prefix + ['disable', '--now', '--', ident]]
            return [prefix + ['enable' if original['enabled'] else 'disable', '--', ident],
                    prefix + ['start' if original['loaded'] else 'stop', '--', ident]]
        if source == 'launchd':
            item = snap['domain'] + '/' + ident
            if op == 'disable':
                return [['launchctl', 'disable', item]] + ([['launchctl','bootout',item]] if snap['loaded'] else [])
            commands = [['launchctl', 'enable' if original['enabled'] or original['loaded'] else 'disable', item]]
            if original['loaded'] and not snap['loaded']:
                commands.append(['launchctl','bootstrap',snap['domain'],snap['path']])
            if original['loaded'] and not original['enabled']:
                commands.append(['launchctl','disable',item])
            return commands
        return [['schtasks','/change','/tn',ident,'/disable' if op == 'disable' or not original['enabled'] else '/enable']]


class Controller:
    def __init__(self, st, backend=None):
        self.st, self.backend = st, backend or Backend()
        self.path = st.root / 'native-scheduler-controls.json'

    def journal(self):
        try:
            if self.path.is_symlink():
                raise Refused('io')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            with os.fdopen(os.open(self.path, flags), 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024:
                    raise Refused('io')
                value = json.loads(stream.read(512 * 1024 + 1))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict):
            raise Refused('io')
        return value

    def key(self, target):
        return digest({k:target[k] for k in ('platform','source','scope','id')})

    def restore_rows(self, rows):
        """Disabled/unloaded objects may disappear from native listings; retain recoverable identities."""
        try:
            records = self.journal()
            recovery = []
            for record in list(records.values())[:systasks.MAX]:
                original = record['original']
                if original['platform'] != self.backend.platform:
                    continue
                recovery.append(dict(src=original['source'], scope=original['scope'], id=original['id'],
                                     name=original['id'], when=[], state='disabled', owner='unknown',
                                     last_result='unknown', execution='[masked]', recovery_pending=True))
            seen = {(r['src'],r.get('scope','user'),r['id']) for r in recovery}
            return (recovery + [r for r in rows if (r['src'],r.get('scope','user'),r['id']) not in seen])[:systasks.MAX]
        except (Refused, OSError, ValueError, KeyError, TypeError):
            return rows

    def describe(self, row):
        target = dict(platform=self.backend.platform, source=row['src'], scope=row.get('scope','user'), id=row.get('control_id', row['id']), snapshot='0'*64, op='disable')
        try:
            snap = (self.backend.source_snapshot(target, readonly=True)
                    if target['source'] in ('cron-system', 'anacron') else self.backend.snapshot(target))
            if target['source'] in ('cron-system', 'anacron') and _needs_privilege(self, target):
                from . import native_scheduler_helper
                try:
                    native_scheduler_helper.trusted_interpreter()
                except (OSError, ValueError):
                    raise Refused('trusted_interpreter_unavailable') from None
            record = self.journal().get(self.key(target))
            op = 'restore' if record else 'disable'
            if target['source'] == 'cron':
                if record and not self.backend.crontab_matches(record, snap):
                    raise Refused('changed')
                if not record and not snap['enabled']:
                    raise Refused('marker_spoof')
            elif target['source'] in ('cron-system', 'anacron'):
                if record and not self.backend.source_matches(record, snap):
                    raise Refused('changed')
                if not record and not snap['enabled']:
                    raise Refused('marker_spoof')
            elif record and record['original']['config'] != snap['config']:
                raise Refused('changed')
            target.update(snapshot=snap['snapshot'], op=op)
            return dict(supported=True, target=target, requires_uv=target['scope'] != 'user')
        except Refused as exc:
            return dict(supported=False, reason=str(exc))
        except OSError as exc:
            reason = ('os_privilege_required' if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS) else
                      'unsafe_path' if exc.errno in (errno.ELOOP, errno.ENOTDIR) else
                      'not_visible' if exc.errno == errno.ENOENT else 'io')
            return dict(supported=False, reason=reason)
        except (ValueError, KeyError):
            return dict(supported=False, reason='unknown_format')

    def apply(self, target, guard):
        validate(target)
        if not _LOCK.acquire(blocking=False):
            raise Refused('busy')
        lock_fd = None
        cron_lock_fd = None
        try:
            guard()
            if os.name == 'posix':
                import fcntl
                lock_fd = os.open(self.st.root / 'native-scheduler-controls.lock',
                                  os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                lock_info = os.fstat(lock_fd)
                if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1 or lock_info.st_uid != os.geteuid():
                    raise Refused('io')
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Refused('busy') from None
            if target['source'] == 'cron':
                if os.name != 'posix':
                    raise Refused('backend_unavailable')
                import fcntl
                cron_lock_fd = os.open(self.backend.transaction_directory / ('agentj-crontab-' + str(os.geteuid()) + '.lock'),
                                       os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                info = os.fstat(cron_lock_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise Refused('unsafe_lock')
                try:
                    fcntl.flock(cron_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Refused('busy') from None
            guard()
            snap = self.backend.snapshot(target)
            if snap['snapshot'] != target['snapshot']:
                raise Refused('changed')
            journal = self.journal()
            key, op = self.key(target), target['op']
            record = journal.get(key)
            if target['source'] == 'cron':
                return self.backend.crontab_apply(self, target, snap, journal, key, guard)
            if target['source'] in ('cron-system', 'anacron'):
                return self.backend.source_apply(self, target, snap, journal, key, guard)
            if op == 'restore' and not record:
                raise Refused('no_restore')
            if op == 'disable' and record:
                raise Refused('changed')
            original = record['original'] if record else snap
            if original['config'] != snap['config']:
                raise Refused('changed')
            journal[key] = {'original':original, 'pending':op}
            self.st.write_private(self.path, json.dumps(journal).encode())
            expected = snap
            for argv in self.backend.commands(snap, op, original):
                guard()
                current = self.backend.snapshot(target)
                if current['snapshot'] != expected['snapshot']:
                    raise Refused('changed')
                guard()
                if self.backend.run(argv) is None:
                    raise Refused('read_only')
                guard()
                expected = self.backend.snapshot(target)
                if expected['config'] != original['config']:
                    raise Refused('changed')
            desired_enabled = False if op == 'disable' else original['enabled']
            desired_loaded = False if op == 'disable' else original['loaded']
            if expected['enabled'] != desired_enabled or (snap['source'] != 'schtasks' and expected['loaded'] != desired_loaded):
                raise Refused('verify_failed')
            guard()
            if op == 'restore':
                del journal[key]
            else:
                journal[key]['pending'] = None
            self.st.write_private(self.path, json.dumps(journal).encode())
            return {'ok':True, 'snapshot':expected['snapshot']}
        finally:
            if cron_lock_fd is not None:
                os.close(cron_lock_fd)
            if lock_fd is not None:
                os.close(lock_fd)
            _LOCK.release()


def freeze_phone_boundary(host, session):
    """Capture live phone/task authority before background dispatch, never from input.

    The current-context reader deliberately ignores frozen origin_scope contextvars.
    Existing on_phone guards, signature and UV verification remain authoritative.
    """
    from .approval_special import _current_context
    state, history = host.st, getattr(host, 'hist', None)
    agent = getattr(host, 'agent', None)
    def current():
        st = host.st
        pk = st.passkey_of_device(session.device)
        pk_identity = digest({k:v for k,v in pk.items() if k not in ('count','sign_count','counter','sc')}) if pk else None
        task = _current_context(host)[1] if callable(getattr(host, '_task_id', None)) else None
        return (session.device, session.pub, getattr(session, 'sign_pub', None),
                session.cid, session.state, host.channel, st.root, st.sign_key(session.device),
                getattr(host, 'task_epoch', None), getattr(host, '_pairing_epoch', None),
                getattr(history, 'epoch', None), task, pk_identity)
    initial = current()
    def guard():
        if (host.st is not state or getattr(host, 'hist', None) is not history
                or getattr(host, 'agent', None) is not agent
                or host.sessions.get(session.cid) is not session
                or session.state != 'ready' or not initial[7]
                or not host.st.is_allowed(session.pub) or current() != initial
                or getattr(host, '_unbinding_all', False)
                or getattr(host, 'estop', {}).get('on', False)):
            raise Refused('no_device')
    return guard


async def on_phone(host, session, obj, *, _bound=None):
    from . import controls, passkey, wire
    if not wire.is_rid(obj.get('r')):
        return
    device, channel, pub = session.device, host.channel, session.pub
    session_cid = session.cid
    cancelled = threading.Event()
    initial_pk = host.st.passkey_of_device(device)
    uv_identity = digest({k:v for k,v in initial_pk.items() if k not in ('count','sign_count','counter','sc')}) if initial_pk else None
    state, state_root = host.st, host.st.root
    key = host.st.sign_key(device)
    missing = object()
    task_reader = getattr(host, '_task_id', None)
    runtime = callable(task_reader) or callable(getattr(type(host), '_task_id', None))
    agent = getattr(host, 'agent', missing)
    def live_task():
        # A native origin_scope must not hide the Host's current task in a
        # suspended password/helper coroutine (the same seam as special cards).
        from .approval_special import _current_context
        return _current_context(host)[1]
    task = live_task() if callable(task_reader) else missing
    epoch = getattr(host, 'task_epoch', None)
    pairing_epoch = getattr(host, '_pairing_epoch', None)
    history = getattr(host, 'hist', None)
    history_epoch = getattr(history, 'epoch', None)
    controller_identity = getattr(host, 'native_scheduler_controller', None)
    def guard():
        if _bound is not None:
            _bound()
        if ((runtime and (agent is missing or history is None or history_epoch is None
                          or getattr(host, 'agent', missing) is not agent
                          or not callable(getattr(host, '_task_id', None)) or live_task() != task
                          or not isinstance(getattr(host, 'estop', None), dict)))
                or getattr(host, 'estop', {}).get('on', False)
                or getattr(host, 'task_epoch', None) != epoch or getattr(host, '_pairing_epoch', None) != pairing_epoch
                or getattr(host, 'hist', None) is not history or getattr(history, 'epoch', None) != history_epoch
                or getattr(host, '_unbinding_all', False)
                or getattr(host, 'native_scheduler_controller', None) is not controller_identity
                or cancelled.is_set() or host.st is not state or host.st.root != state_root or session.state != 'ready' or session.cid != session_cid or host.sessions.get(session_cid) is not session or session.device != device
                or host.channel != channel or session.pub != pub or not host.st.is_allowed(pub)
                or not key or host.st.sign_key(device) != key):
            raise Refused('no_device')
        current_pk = host.st.passkey_of_device(device)
        current_identity = digest({k:v for k,v in current_pk.items() if k not in ('count','sign_count','counter','sc')}) if current_pk else None
        if current_identity != uv_identity:
            raise Refused('changed')
    result = {'ok':False, 'why':'error'}
    try:
        guard()
        target = validate({k:obj.get(k) for k in FIELDS})
        pk = None
        if target['scope'] != 'user':
            pk = host.st.passkey_of_device(device)
            if not pk:
                raise Refused('no_passkey')
            uv_identity = digest({k:v for k,v in pk.items() if k not in ('count','sign_count','counter','sc')})
            challenge = passkey.elevate_challenge(channel, device, obj.get('n'), ACTION, obj.get('n'), controls.object_digest(ACTION,target))
            if obj.get('fa') is None:
                result = dict(ok=False, why='passkey_required', fa=pk['id'], fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(challenge))
                return await host._ctl_res(session,obj['r'],ACTION,**result)
        why = controls.check(host.st,host.nonces,channel,device,obj,ACTION,target)
        if why:
            raise Refused(why)
        if pk:
            try:
                fields = passkey.approval_fields(obj.get('fa'))
                if fields['id'] != pk['id']:
                    raise Refused('passkey_failed')
                count = passkey.verify_assertion(pk,fields,challenge)
                host.st.passkey_count(device,pk['id'],count)
            except (ValueError, passkey.PasskeyError):
                raise Refused('passkey_failed') from None
        controller = getattr(host,'native_scheduler_controller',None) or Controller(host.st)
        # The event loop remains available for unpair/disconnect. Each worker step rechecks
        # live session/device/key and snapshot; cancellation forbids every subsequent effect.
        if target['source'] in ('cron-system', 'anacron') and _needs_privilege(controller, target):
            from . import native_scheduler_plan, approval_review
            # Install the actual controller identity before issuing any capability.
            if controller_identity is None:
                host.native_scheduler_controller = controller
                controller_identity = controller
            plan = await asyncio.to_thread(native_scheduler_plan.prepare, controller, target, guard)
            authority = _NativePlan(_NATIVE_FACTORY, host, session, controller, target, obj, guard, plan)
            try:
                with approval_review.origin_scope(authority.origin()):
                    answer = await host.elevate.request_native_scheduler(authority)
                result = answer.get('native_result') or {'ok':False, 'why':answer.get('why') or answer.get('result','refused')}
            finally:
                authority.close()
        else:
            result = await asyncio.to_thread(controller.apply, target, guard)
        guard()
    except asyncio.CancelledError:
        cancelled.set()
        raise
    except Refused as exc:
        result = {'ok':False,'why':str(exc)}
    except (OSError, ValueError, KeyError, TypeError):
        result = {'ok':False,'why':'read_only'}
    controls.log(state,action=ACTION,device=device,result='ok' if result['ok'] else 'refused:'+result['why'])
    guard_ok = True
    try:
        guard()
    except Refused:
        guard_ok = False
    if guard_ok:
        await host._ctl_res(session,obj['r'],ACTION,**result)
    else:
        # A refusal contains no object metadata and never acts through the replacement session.
        await host._ctl_res(session,obj['r'],ACTION,False,why='no_device')


_NATIVE_FACTORY = object()


class _NativePlan:
    """In-process capability. Neither a digest nor a deserialized dict is authority."""
    def __init__(self, token, host, session, controller, target, request, guard, plan):
        if token is not _NATIVE_FACTORY:
            raise Refused('native_authority')
        import time
        from . import native_scheduler_helper as helper
        self.host, self.session, self.controller = host, session, controller
        self.target, self.request = dict(target), json.loads(json.dumps(request))
        self.guard, self.plan = guard, plan
        self.digest = digest(plan)
        self.bootstrap = helper.BOOTSTRAP
        self.bootstrap_digest = helper.BOOTSTRAP_SHA256
        self.nonce, self.expires = secrets.token_hex(32), time.monotonic() + 120
        self.effect_started = False
        self._journal_expected = plan['journal_digest']
        self._journal_transition = None
        self.card = None
        self.card_binding = None
        registry = host.__dict__.setdefault('_native_scheduler_plans', {})
        registry[self.nonce] = self

    def live(self, preimage=True):
        import time
        from . import native_scheduler_helper as helper
        self.guard()
        if (self.host.__dict__.get('_native_scheduler_plans', {}).get(self.nonce) is not self
                or time.monotonic() >= self.expires or digest(self.plan) != self.digest
                or helper.BOOTSTRAP != self.bootstrap
                or hashlib.sha256(self.bootstrap.encode()).hexdigest() != self.bootstrap_digest
                or self.host.native_scheduler_controller is not self.controller):
            raise Refused('native_authority')
        if self.card is not None and self.card_binding != digest({k:self.card.get(k) for k in ('id','argv','cmd','why','effect','only','_native_scheduler_nonce','_native_scheduler_plan_digest','_native_scheduler_bootstrap_digest')}):
            raise Refused('native_authority')
        if preimage and not self.effect_started:
            allowed_journals = self._journal_transition or (self._journal_expected,)
            if (digest(self.controller.journal()) not in allowed_journals
                    or self.controller.backend.source_snapshot(self.target, readonly=True) != self.plan['before']):
                raise Refused('changed')
        return True

    def close(self):
        self.host.__dict__.get('_native_scheduler_plans', {}).pop(self.nonce, None)

    def origin(self):
        from . import approval_review, provenance
        self.live()
        text = 'Signed native scheduler control: ' + self.target['op'] + ' ' + self.target['id']
        turn = self.host.hist_add({'k':'phone', 'dev':self.session.device, 'name':'你', 'text':text}, '', 'done')
        origin = approval_review.remember(self.host, turn, provenance.phone())
        origin['owner_action'] = {'action':ACTION, 'device':self.session.device,
                                  'plan_sha256':self.digest, 'bootstrap_sha256':self.bootstrap_digest}
        return origin

    async def execute(self, elevator, card, password):
        """Only this entry builds root argv. Pseudocommands never reach run_sudo."""
        import tempfile
        import socket
        import struct
        from . import elevate, native_scheduler_helper as helper
        self.live()
        if self.card is not card or card.get('only') != self.session.device:
            raise Refused('native_authority')
        interpreter = helper.trusted_interpreter()
        cancel = card['cancel']
        result = None
        connections = set()
        completed = asyncio.get_running_loop().create_future()
        if not _LOCK.acquire(blocking=False):
            raise Refused('busy')
        lock_fd = None
        try:
            import fcntl
            lock_fd = os.open(self.controller.st.root / 'native-scheduler-controls.lock',
                              os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            lock_info = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1 or lock_info.st_uid != os.geteuid():
                raise Refused('unsafe_lock')
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # AF_UNIX addresses cannot hold long state-directory paths. The
            # private 0700 directory is ephemeral and carries no stored grant.
            with tempfile.TemporaryDirectory(prefix='aj-n369-', dir=tempfile.gettempdir()) as directory:
                endpoint = Path(directory) / 'permit'
                # This route is Linux-only: sun_path has 108 bytes including
                # its terminator. Never fall back to an unchecked state path.
                if len(os.fsencode(endpoint)) >= 108:
                    raise Refused('unix_path_too_long')
                used = False
                async def handle(reader, writer):
                    nonlocal used
                    connections.add(writer)
                    owned = False
                    def live():
                        self.live()
                        if cancel.is_set():
                            raise Refused('cancelled')
                    async def receive():
                        raw = await asyncio.wait_for(reader.readline(), 15)
                        live()
                        if not raw.endswith(b'\n') or len(raw) > 2*1024*1024:
                            raise Refused('shape')
                        return json.loads(raw)
                    async def send(value):
                        live()
                        writer.write(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()+b'\n')
                        await writer.drain()
                        live()
                    try:
                        live()
                        if not _root_helper_peer(writer):
                            raise Refused('native_authority')
                        hello = await receive()
                        if (used or set(hello) != {'nonce','digest','hello'} or hello['nonce'] != self.nonce
                                or hello['digest'] != self.digest or not re.fullmatch('[0-9a-f]{64}', hello['hello'])):
                            raise Refused('native_authority')
                        used = True
                        owned = True
                        await send({'hello':hello['hello'], 'plan':self.plan})
                        ready = await receive()
                        if set(ready) != {'ready','challenge'} or not re.fullmatch('[0-9a-f]{64}', ready['challenge']):
                            raise Refused('shape')
                        expected = ready['ready']
                        if (not isinstance(expected,dict) or expected.get('content') != hashlib.sha256(base64.b64decode(self.plan['desired_bytes'])).hexdigest()
                                or expected.get('enabled') != (self.target['op']=='restore')):
                            raise Refused('verify_failed')
                        journal = self.controller.journal()
                        key = self.plan['journal_key']
                        journal[key] = dict(original=self.plan['original'], bytes=self.plan['original_bytes'],
                                            before=self.plan['before'], expected=expected, pending=self.target['op'])
                        encoded = json.dumps(journal).encode()
                        if len(encoded)>512*1024:
                            raise Refused('journal_capacity')
                        live()
                        planned_digest = digest(journal)
                        # The disk replacement can precede this coroutine's
                        # resumption. The watcher may see exactly either state,
                        # while source/device/task guards remain unchanged.
                        self._journal_transition = (self._journal_expected, planned_digest)
                        try:
                            await asyncio.to_thread(self.controller.st.write_private, self.controller.path, encoded)
                        except BaseException:
                            self._journal_transition = None
                            raise
                        self._journal_expected = planned_digest
                        self._journal_transition = None
                        live()
                        # Journal changed intentionally; preimage and device are checked separately.
                        self.effect_started = True
                        self.live(False)
                        if self.controller.backend.source_snapshot(self.target, readonly=True) != self.plan['before']:
                            raise Refused('changed')
                        await send({'permit':ready['challenge']})
                        done = await receive()
                        after = self.controller.backend.source_snapshot(self.target, readonly=True)
                        if (set(done) != {'done'} or done['done'] != after
                                or after.get('witness') != expected.get('witness')
                                or after.get('enabled') != (self.target['op']=='restore')
                                or after.get('content') != expected.get('content')):
                            raise Refused('verify_failed')
                        live()
                        if self.target['op']=='restore':
                            del journal[key]
                        else:
                            journal[key].update(expected=after, pending=None)
                        await asyncio.to_thread(self.controller.st.write_private, self.controller.path, json.dumps(journal).encode())
                        live()
                        await send({'ack':True})
                        if not completed.done():
                            completed.set_result({'ok':True,'snapshot':after['snapshot']})
                    except (Exception, asyncio.CancelledError):
                        if owned:
                            cancel.set()
                            if not completed.done():
                                completed.set_result({'ok':False,'why':'unknown' if self.effect_started else 'native_authority'})
                    finally:
                        writer.close()
                        connections.discard(writer)
                server = await asyncio.start_unix_server(handle, path=endpoint, limit=2*1024*1024)
                os.chmod(endpoint, 0o600)
                info = endpoint.lstat()
                argv = [interpreter, '-I', '-S', '-c', self.bootstrap, str(endpoint), str(info.st_dev),
                        str(info.st_ino), str(info.st_uid), str(os.getpid()), self.nonce, self.digest]
                async def watch():
                    while True:
                        await asyncio.sleep(.05)
                        try:
                            self.live()
                            card['_approval_execution_guard']()
                        except Exception:
                            cancel.set()
                            return
                watcher = asyncio.create_task(watch())
                try:
                    result = await asyncio.to_thread(elevate.run_sudo, argv, password, '/', 120,
                                                     elevator.sudo, cancel, None)
                    self.live(False)
                    if result.get('result') == 'bad_password':
                        return result
                    if result.get('result') == 'done' and result.get('code') == 0 and not completed.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(completed), 1)
                        except asyncio.TimeoutError:
                            pass
                        self.live(False)
                    if (result.get('result') != 'done' or result.get('code') != 0
                            or not completed.done() or not completed.result().get('ok')):
                        return {'result':'failed', 'why':'unknown' if self.effect_started else 'fixed_helper', 'code':1,
                                'stdout':'','stderr':''}
                    result.update(stdout='', stderr='', native_result=completed.result())
                    return result
                finally:
                    watcher.cancel()
                    server.close()
                    await server.wait_closed()
                    for writer in list(connections):
                        writer.close()
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            _LOCK.release()


def _needs_privilege(controller, target):
    fd, path, info, data = controller.backend.source_state(target, readonly=True)
    try:
        return info.st_uid != controller.backend.uid or info.st_uid != os.geteuid() or not os.access('.', os.W_OK, dir_fd=fd, effective_ids=True)
    finally:
        os.close(fd)


def _root_helper_peer(writer):
    import socket
    import struct
    try:
        sock = writer.get_extra_info('socket')
        if sock is None or not hasattr(socket, 'SO_PEERCRED'):
            return False
        peer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
        if len(peer) != struct.calcsize('3i'):
            return False
        pid, uid, _gid = struct.unpack('3i', peer)
        return pid > 0 and uid == 0
    except (OSError, AttributeError, TypeError, ValueError, struct.error):
        return False
